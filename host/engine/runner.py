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
from .cursor import GainEstimate, click_at_target  # noqa: E402
from .focus import (  # noqa: E402
    DEFAULT_MAX_FOCUS_WAIT_SECONDS,
    FOCUS_POLICY_FOCUS_AND_RESUME,
    FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
    FocusTimeoutError,
    focus_window,
    is_window_focused,
)
from .matcher import match  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 200
DEFAULT_FOCUS_POLL_INTERVAL_MS = 300

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
                 is_window_focused=is_window_focused, focus_window=focus_window):
        self._graph = graph
        self._capture = capture
        self._sink = sink
        self._hwnd = hwnd
        self._profile_dir = Path(profile_dir)
        self._focus_policy = focus_policy
        self._focus_poll_interval_ms = focus_poll_interval_ms
        self._max_focus_wait_seconds = max_focus_wait_seconds
        self._is_window_focused = is_window_focused
        self._focus_window = focus_window
        # Shared across every click_at_target() call for the life of this
        # run, so repeat clicks reuse the learned pointer-acceleration
        # gain instead of re-probing from scratch each time. A fresh
        # MacroRunner (a new Run) starts this neutral again.
        self._cursor_gain_estimate = GainEstimate()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None

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

    def _run_action(self, node: dict) -> None:
        if not self._ensure_focus():
            return

        action_type = node["action_type"]
        if action_type == "key_press":
            keycode = wire.keycode_for_letter(node["key_combo"])
            self._sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(keycode,)))
        elif action_type == "click":
            button = _MOUSE_BUTTONS[node.get("mouse_button", "left")]
            click_at_target(self._hwnd, tuple(node["click_rect"]), self._sink, button,
                            gain_estimate=self._cursor_gain_estimate)
        else:
            raise ValueError(f"unknown action_type: {action_type}")

    def _run_decision(self, node: dict) -> str:
        reference_bgra = cv2.imread(str(self._profile_dir / node["reference_path"]),
                                     cv2.IMREAD_UNCHANGED)
        region = tuple(node["region"])
        threshold = node["match_threshold"]
        mode = node["evaluation_mode"]

        if mode == "branch":
            frame = self._capture.get_latest_frame_bgr()
            is_match = frame is not None and match(frame, reference_bgra, region, threshold)
            return node["true"] if is_match else node["false"]

        if mode == "wait_until_true":
            poll_interval = node.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS) / 1000.0
            while not self._stop_requested.is_set():
                frame = self._capture.get_latest_frame_bgr()
                if frame is not None and match(frame, reference_bgra, region, threshold):
                    return node["true"]
                self._stop_requested.wait(timeout=poll_interval)
            return node["true"]

        raise ValueError(f"unknown evaluation_mode: {mode}")
