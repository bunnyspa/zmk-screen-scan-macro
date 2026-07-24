"""MacroRunner: walks a hand-authored graph, driving capture -> decision ->
action -> real HID output via a CommandSink.

Graph schema (plain JSON - NOT VisionGraph's NodeGraphQt session format; that
translation is Phase 3's job once the real editor exists). See
tests/fixtures/example_graph.json for a worked example.

{
  "start_node": "<node id>",
  "nodes": {
    "<node id>": {
      "type": "action" | "wait" | "decision",
      ... type-specific fields below ...
      "out": "<node id>"                # action, wait
      "true": "<node id>", "false": "<node id>"   # decision
    }, ...
  }
}

action:   action_type: "key_press" | "click"
          key_combo: str (single a-z letter, key_press)
          click_rect: [x, y, w, h] (window-relative, click)
          mouse_button: "left" | "right" | "middle" (click, default "left")
wait:     duration_ms: int
decision: reference_path: str (relative to profile_dir, cropped alpha-masked BGRA PNG)
          region: [x, y, w, h] (window-relative)
          match_threshold: float
          evaluation_mode: "branch" | "wait_until_true"
          poll_interval_ms: int (wait_until_true only, default 200)

Cyclic graphs are intentional (retry-until-true idiom) - the runner does not
detect or refuse cycles. Call .stop() to end a run; without it, a cyclic
graph runs forever by design.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol as wire  # noqa: E402

from .command import Command, CommandSink  # noqa: E402
from .cursor import (  # noqa: E402
    CROSSING_MODE_REACTIVE,
    GainEstimate,
    click_at_target,
    get_window_extended_frame_origin,
    get_window_screen_origin,
    move_cursor_to_target,
)
from .focus import (  # noqa: E402
    DEFAULT_MAX_FOCUS_WAIT_SECONDS,
    FOCUS_POLICY_FOCUS_AND_RESUME,
    FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
    FocusTimeoutError,
    focus_window,
    is_window_focused,
)
from .matcher import match_score  # noqa: E402

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
                 show_decision_overlay=None, hide_decision_overlay=None):
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
        # Decision-node live overlay (reference image + match percentage) -
        # shown during Wait Until True polling (regardless of confirmation
        # mode) and/or right before a decision resolves in confirmation
        # mode (both modes, per the design discussion this came from) -
        # see _run_decision(). Injected the same way as
        # show_pending_click/show_pending_key_press, for the same reason
        # (UI-thread work).
        self._show_decision_overlay = show_decision_overlay
        self._hide_decision_overlay = hide_decision_overlay
        self._confirmation_event = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None

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

    def _run(self) -> None:
        node_id = self._graph["start_node"]
        while not self._stop_requested.is_set():
            node = self._graph["nodes"][node_id]
            node_type = node["type"]

            if node_type == "action":
                self._run_action(node)
                node_id = node["out"]
            elif node_type == "wait":
                self._stop_requested.wait(timeout=node["duration_ms"] / 1000.0)
                node_id = node["out"]
            elif node_type == "decision":
                node_id = self._run_decision(node)
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
                    return
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
                    return
                self._sink.send(Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=button))
            else:
                click_at_target(self._hwnd, click_rect, self._sink, button,
                                gain_estimate=self._cursor_gain_estimate,
                                crossing_mode=self._crossing_mode)
        else:
            raise ValueError(f"unknown action_type: {action_type}")

    def _run_decision(self, node: dict) -> str:
        reference_path = str(self._profile_dir / node["reference_path"])
        reference_bgra = cv2.imread(reference_path, cv2.IMREAD_UNCHANGED)
        region = tuple(node["region"])
        threshold = node["match_threshold"]
        mode = node["evaluation_mode"]

        # Live overlay (reference image + match %): shown for every Wait
        # Until True poll regardless of confirmation mode (there's
        # something to watch update over time), and for Branch mode only
        # when confirmation mode is on (a single instantaneous evaluation
        # has nothing to visualize turn-by-turn otherwise) - see the
        # design discussion in the commit this came from.
        show_overlay = mode == "wait_until_true" or self._confirmation_mode
        screen_rect = None
        if show_overlay and self._hwnd is not None:
            origin_x, origin_y = get_window_extended_frame_origin(self._hwnd)
            rx, ry, rw, rh = region
            screen_rect = (origin_x + rx, origin_y + ry, rw, rh)

        def update_overlay(score):
            if screen_rect is not None and self._show_decision_overlay is not None:
                self._show_decision_overlay({
                    "screen_rect": screen_rect,
                    "reference_path": reference_path,
                    "score": score,
                    "threshold": threshold,
                })

        def clear_overlay():
            if show_overlay and self._hide_decision_overlay is not None:
                self._hide_decision_overlay()

        if mode == "branch":
            frame = self._capture.get_latest_frame_bgr()
            score = match_score(frame, reference_bgra, region) if frame is not None else 0.0
            is_match = score >= threshold
            update_overlay(score)
            if self._confirmation_mode:
                self._await_confirmation("decision", {})
            clear_overlay()
            return node["true"] if is_match else node["false"]

        if mode == "wait_until_true":
            poll_interval = node.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS) / 1000.0
            while not self._stop_requested.is_set():
                frame = self._capture.get_latest_frame_bgr()
                score = match_score(frame, reference_bgra, region) if frame is not None else 0.0
                update_overlay(score)
                if score >= threshold:
                    if self._confirmation_mode:
                        self._await_confirmation("decision", {})
                    clear_overlay()
                    return node["true"]
                self._stop_requested.wait(timeout=poll_interval)
            clear_overlay()
            return node["true"]

        raise ValueError(f"unknown evaluation_mode: {mode}")
