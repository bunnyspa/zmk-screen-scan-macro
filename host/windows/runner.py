"""MacroRunner: walks a hand-authored graph, driving capture -> decision ->
action -> real HID output via a CommandSink.

Graph schema (plain JSON - see graph_translation.build_engine_graph_from_document()
for what actually produces this from the web UI's GraphDocument). See
tests/fixtures/example_graph.json for a worked example.

{
  "start_node": "<node id>",
  "nodes": {
    "<node id>": {
      "type": "action" | "wait" | "branch" | "branch_wait",
      ... type-specific fields below ...
      "out": "<node id>" | null         # action, wait, branch_wait; also
                                         # each of branch's own per-image entries
      "false": "<node id>" | null       # branch only
    }, ...
  }
}

An "out"/"false" of null (an unconnected port, per graph_translation.py's
_first_connected_document_node) is a dead end: the run just ends there
rather than erroring.

action:      action_type: "key_press" | "click"
             key_combo: str (single a-z letter, key_press)
             click_rect: [x, y, w, h] (window-relative, click)
             mouse_button: "left" | "right" | "middle" (click, default "left")
wait:        duration_ms: int
branch:      images: [{reference_path: str (relative to profile_dir, cropped
                        alpha-masked BGRA PNG), region: [x, y, w, h]
                        (window-relative), out: "<node id>" | null}, ...]
                       - an OR match: checked in list order, first one that
                         meets match_threshold wins and its own "out" is taken.
             match_threshold: float (shared across every image in the list)
             "false": "<node id>" | null (taken if no image matched)
branch_wait: images: [...] (same shape/OR-matching as branch)
             match_threshold: float
             poll_interval_ms: int (default 200)
                       - polls until any image matches, then that image's
                         own "out" is taken; no "false" - a branch_wait
                         node never has one to fall through to.

branch/branch_wait were one "decision" node type with an evaluation_mode
field until they were split (a node's shape - specifically, whether a
trailing false port exists at all - no longer needs to change based on a
mutable per-instance mode).

Cyclic graphs are intentional (retry-until-true idiom) - the runner does not
detect or refuse cycles. Call .stop() to end a run; without it, a cyclic
graph runs forever by design.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2

import protocol as wire

from command import Command, CommandSink
from cursor import (
    CROSSING_MODE_REACTIVE,
    GainEstimate,
    click_at_target,
    get_window_extended_frame_origin,
    get_window_screen_origin,
    move_cursor_to_target,
)
from focus import (
    DEFAULT_MAX_FOCUS_WAIT_SECONDS,
    FOCUS_POLICY_FOCUS_AND_RESUME,
    FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
    FocusTimeoutError,
    focus_window,
    is_window_focused,
)
from matcher import match_score

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 200
DEFAULT_FOCUS_POLL_INTERVAL_MS = 300
DEFAULT_CONFIRMATION_POLL_INTERVAL_MS = 100

_MOUSE_BUTTONS = {
    "left": wire.MOUSE_BUTTON_LEFT,
    "right": wire.MOUSE_BUTTON_RIGHT,
    "middle": wire.MOUSE_BUTTON_MIDDLE,
}


class MacroRunner:
    def __init__(self, graph: dict, capture, sink: CommandSink, hwnd=None,
                 profile_dir: Path | str = ".",
                 focus_policy: str = FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
                 focus_poll_interval_ms: int = DEFAULT_FOCUS_POLL_INTERVAL_MS,
                 max_focus_wait_seconds: float = DEFAULT_MAX_FOCUS_WAIT_SECONDS,
                 crossing_mode: str = CROSSING_MODE_REACTIVE,
                 is_window_focused=is_window_focused, focus_window=focus_window,
                 confirmation_mode: bool = False,
                 show_pending_click=None, show_pending_key_press=None,
                 confirmation_poll_interval_ms: int = DEFAULT_CONFIRMATION_POLL_INTERVAL_MS,
                 show_branch_overlay=None, hide_branch_overlay=None):
        self._graph = graph
        self._capture = capture
        self._sink = sink
        self._hwnd = hwnd
        self._profile_dir = Path(profile_dir)
        self._focus_policy = focus_policy
        self._focus_poll_interval_ms = focus_poll_interval_ms
        self._max_focus_wait_seconds = max_focus_wait_seconds
        self._crossing_mode = crossing_mode
        self._is_window_focused = is_window_focused
        self._focus_window = focus_window
        # Shared across every click_at_target() call for the life of this
        # run, so repeat clicks reuse the learned pointer-acceleration
        # gain instead of re-probing from scratch each time. A fresh
        # MacroRunner (a new Run) starts this neutral again.
        self._cursor_gain_estimate = GainEstimate()
        # Confirmation mode: before each click/key-press, show what's about
        # to happen (show_pending_click/show_pending_key_press - injected,
        # since showing an overlay/preview is UI-thread work) and block
        # until .confirm() is called - from the app's OK button or the
        # &ssm_confirm physical key (see hid_link.py) - or a stop is
        # requested.
        self._confirmation_mode = confirmation_mode
        self._show_pending_click = show_pending_click
        self._show_pending_key_press = show_pending_key_press
        self._confirmation_poll_interval_ms = confirmation_poll_interval_ms
        # branch/branch_wait live overlay (reference image + match
        # percentage) - shown during branch_wait polling (regardless of
        # confirmation mode) and/or right before a branch/branch_wait
        # resolves in confirmation mode (both types, per the design
        # discussion this came from) - see _make_branch_helpers().
        # Injected the same way as show_pending_click/
        # show_pending_key_press, for the same reason (UI-thread work).
        self._show_branch_overlay = show_branch_overlay
        self._hide_branch_overlay = hide_branch_overlay
        self._confirmation_event = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        # Set if _run() ends via an uncaught exception (e.g. FocusTimeoutError
        # - the target window never came to foreground within
        # max_focus_wait_seconds) rather than a dead end or .stop(). Without
        # this, such a failure was completely silent: an unhandled exception
        # on a background thread just ends the thread with nothing printed
        # anywhere a GUI app's user would ever see, confirmed via a real
        # report of a one-shot action simply never firing with no
        # indication why.
        self.error: str | None = None

    def confirm(self) -> None:
        """Call from the OK button or the &ssm_confirm physical key
        handler to resolve a pending _await_confirmation() wait. A no-op
        if nothing is currently pending."""
        self._confirmation_event.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_running(self) -> bool:
        """False once the run has actually ended, whether from .stop()
        or - just as often, since a dead-end node (no outgoing connection)
        is a normal, non-error way for a run to finish, see this module's
        docstring - the background thread simply returning on its own.
        Callers that only track "did I call .stop() yet" (e.g. a naive
        `self.macro_runner is not None` check) miss the dead-end case
        entirely and report a run as still active forever after it already
        ended by itself."""
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        logger.info("MacroRunner: run starting at node %r", self._graph.get("start_node"))
        try:
            self._run_loop()
        except Exception as exc:
            logger.exception("MacroRunner: run ended with an unhandled error")
            self.error = str(exc)
        else:
            logger.info("MacroRunner: run ended (stopped=%s)", self._stop_requested.is_set())

    def _run_loop(self) -> None:
        node_id = self._graph["start_node"]
        while not self._stop_requested.is_set():
            if node_id is None:
                # An out/true/false port with nothing wired to it translates
                # to None (see graph_translation.py's
                # _first_connected_document_node) - that's a dead end by
                # design, not an error, so the run just ends here rather
                # than KeyError-ing on nodes[None].
                logger.info("MacroRunner: reached a dead end (unconnected port) - run ending")
                return
            node = self._graph["nodes"][node_id]
            node_type = node["type"]
            logger.info("MacroRunner: visiting node %r (%s)", node_id, node_type)

            if node_type == "action":
                self._run_action(node)
                node_id = node["out"]
            elif node_type == "wait":
                self._stop_requested.wait(timeout=node["duration_ms"] / 1000.0)
                node_id = node["out"]
            elif node_type == "branch":
                node_id = self._run_branch(node)
            elif node_type == "branch_wait":
                node_id = self._run_branch_wait(node)
            else:
                raise ValueError(f"unknown node type: {node_type}")

    def _ensure_focus(self) -> bool:
        """Blocks (interruptibly) until the target window is focused, per
        focus_policy - real HID input goes wherever the OS has focus, not
        to a specific window, so an action fired while the target isn't
        foreground would land somewhere else entirely.

        Returns True once it's safe to proceed. Returns False if a stop was
        requested while waiting - callers must not proceed with the action
        in that case. Raises FocusTimeoutError if max_focus_wait_seconds
        elapses without ever confirming focus - confirmed against real
        hardware that Windows can keep refusing to hand over the
        foreground indefinitely, which an unbounded retry loop here would
        otherwise turn into what looks like the whole app freezing."""
        if self._hwnd is None:
            return True

        poll_interval = self._focus_poll_interval_ms / 1000.0
        deadline = time.monotonic() + self._max_focus_wait_seconds
        while not self._stop_requested.is_set():
            if self._is_window_focused(self._hwnd):
                return True

            if time.monotonic() >= deadline:
                raise FocusTimeoutError(
                    f"target window did not come to focus within "
                    f"{self._max_focus_wait_seconds}s (focus_policy={self._focus_policy!r})"
                )

            if self._focus_policy == FOCUS_POLICY_FOCUS_AND_RESUME:
                logger.info("MacroRunner: target window not focused - focusing and resuming")
                self._focus_window(self._hwnd)
            elif self._focus_policy == FOCUS_POLICY_PAUSE_UNTIL_FOCUSED:
                logger.info("MacroRunner: target window not focused - pausing until it regains focus")
            else:
                raise ValueError(f"unknown focus_policy: {self._focus_policy}")

            self._stop_requested.wait(timeout=poll_interval)

        return False

    def _await_confirmation(self, kind: str, details: dict) -> bool:
        """Shows the pending action via the injected UI callback (a
        no-op if none was given), then blocks (interruptibly) until
        .confirm() is called or a stop is requested. Returns False if
        stopped while waiting - callers must not proceed with the action
        in that case."""
        self._confirmation_event.clear()
        if kind == "click" and self._show_pending_click is not None:
            self._show_pending_click(details["screen_rect"])
        elif kind == "key_press" and self._show_pending_key_press is not None:
            self._show_pending_key_press(details["key_combo"], details.get("screen_pos"))

        poll_interval = self._confirmation_poll_interval_ms / 1000.0
        while not self._stop_requested.is_set():
            if self._confirmation_event.wait(timeout=poll_interval):
                return True
        return False

    def _run_action(self, node: dict) -> None:
        if not self._ensure_focus():
            logger.info("MacroRunner: stop requested while waiting for focus - action skipped")
            return

        action_type = node["action_type"]
        if action_type == "key_press":
            keycode = wire.keycode_for_letter(node["key_combo"])
            if self._confirmation_mode:
                # A key press has no on-screen region to anchor a preview to
                # (unlike a click) - the window's own top-left corner is
                # used instead, just so the pending-key overlay has
                # somewhere to float near the target window.
                screen_pos = get_window_screen_origin(self._hwnd) if self._hwnd is not None else None
                details = {"key_combo": node["key_combo"], "screen_pos": screen_pos}
                if not self._await_confirmation("key_press", details):
                    logger.info("MacroRunner: stop requested while awaiting confirmation - key press skipped")
                    return
            logger.info("MacroRunner: sending key press %r", node["key_combo"])
            self._sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(keycode,)))
        elif action_type == "click":
            button = _MOUSE_BUTTONS[node.get("mouse_button", "left")]
            click_rect = tuple(node["click_rect"])
            if self._confirmation_mode:
                move_cursor_to_target(self._hwnd, click_rect, self._sink,
                                      gain_estimate=self._cursor_gain_estimate,
                                      crossing_mode=self._crossing_mode)
                origin_x, origin_y = get_window_screen_origin(self._hwnd)
                x, y, w, h = click_rect
                screen_rect = (origin_x + x, origin_y + y, w, h)
                if not self._await_confirmation("click", {"screen_rect": screen_rect}):
                    logger.info("MacroRunner: stop requested while awaiting confirmation - click skipped")
                    return
                logger.info("MacroRunner: sending click at %r", click_rect)
                self._sink.send(Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=button))
            else:
                logger.info("MacroRunner: sending click at %r", click_rect)
                click_at_target(self._hwnd, click_rect, self._sink, button,
                                gain_estimate=self._cursor_gain_estimate,
                                crossing_mode=self._crossing_mode)
        else:
            raise ValueError(f"unknown action_type: {action_type}")

    def _make_branch_helpers(self, node: dict, threshold: float, show_overlay: bool):
        """Shared setup for _run_branch()/_run_branch_wait(): loads every
        image the node lists (fresh, not cached across polls within one
        _run_branch_wait() call, same as the old single-image behavior -
        each entry's own reference_bgra travels alongside it), and
        returns (evaluate, update_overlay, clear_overlay) closures over
        them.

        show_overlay is supplied by the caller rather than decided here:
        _run_branch wants it only in confirmation mode (a single
        instantaneous evaluation has nothing to visualize turn-by-turn
        otherwise), _run_branch_wait always wants it (there's something
        to watch update over time). With multiple images, whichever one
        is currently the best-scoring candidate is shown (the eventual
        match isn't known until it crosses threshold) - the caller
        (run_controller.py's _show_branch_overlay_on_gui_thread)
        recreates the overlay whenever the shown region/reference
        changes between polls."""
        images = [
            {**img, "reference_bgra": cv2.imread(
                str(self._profile_dir / img["reference_path"]), cv2.IMREAD_UNCHANGED,
            )}
            for img in node["images"]
        ]

        origin = None
        if show_overlay and self._hwnd is not None:
            origin = get_window_extended_frame_origin(self._hwnd)

        def evaluate(frame):
            """Scores every image in priority order. Returns (matched_img,
            display_img, display_score) - matched_img is the first (lowest
            priority index) image meeting threshold, or None if none did;
            display_img/score is the matched image if there is one,
            otherwise whichever image scored highest (most useful thing to
            show while still polling toward a match)."""
            matched_img, matched_score = None, None
            best_img, best_score = None, -1.0
            for img in images:
                score = match_score(frame, img["reference_bgra"], tuple(img["region"])) \
                    if frame is not None else 0.0
                if score > best_score:
                    best_img, best_score = img, score
                if matched_img is None and score >= threshold:
                    matched_img, matched_score = img, score
            if matched_img is not None:
                return matched_img, matched_img, matched_score
            return None, best_img, best_score

        def update_overlay(display_img, score):
            if display_img is None or origin is None or self._show_branch_overlay is None:
                return
            origin_x, origin_y = origin
            rx, ry, rw, rh = display_img["region"]
            self._show_branch_overlay({
                "screen_rect": (origin_x + rx, origin_y + ry, rw, rh),
                "reference_path": str(self._profile_dir / display_img["reference_path"]),
                "score": score,
                "threshold": threshold,
            })

        def clear_overlay():
            if show_overlay and self._hide_branch_overlay is not None:
                self._hide_branch_overlay()

        return evaluate, update_overlay, clear_overlay

    def _run_branch(self, node: dict) -> str | None:
        """Evaluates every image once; returns the first matching image's
        own "out" target, or node["false"] if none matched."""
        threshold = node["match_threshold"]
        evaluate, update_overlay, clear_overlay = self._make_branch_helpers(
            node, threshold, show_overlay=self._confirmation_mode,
        )
        frame = self._capture.get_latest_frame_bgr()
        matched_img, display_img, score = evaluate(frame)
        update_overlay(display_img, score)
        if self._confirmation_mode:
            self._await_confirmation("branch", {})
        clear_overlay()
        return matched_img["out"] if matched_img is not None else node["false"]

    def _run_branch_wait(self, node: dict) -> str | None:
        """Polls until any image matches, then returns that image's own
        "out" target - a branch_wait node has no "false" port to fall
        through to (see module docstring), so a stop request while still
        waiting is the only other way out (returns None, same dead-end
        handling _run_loop() already gives any null port)."""
        threshold = node["match_threshold"]
        evaluate, update_overlay, clear_overlay = self._make_branch_helpers(
            node, threshold, show_overlay=True,
        )
        poll_interval = node.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS) / 1000.0
        while not self._stop_requested.is_set():
            frame = self._capture.get_latest_frame_bgr()
            matched_img, display_img, score = evaluate(frame)
            update_overlay(display_img, score)
            if matched_img is not None:
                if self._confirmation_mode:
                    self._await_confirmation("branch", {})
                clear_overlay()
                return matched_img["out"]
            self._stop_requested.wait(timeout=poll_interval)
        clear_overlay()
        return None
