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

import sys
import threading
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
import protocol as wire  # noqa: E402

from .command import Command, CommandSink  # noqa: E402
from .cursor import click_commands  # noqa: E402
from .matcher import match  # noqa: E402

DEFAULT_POLL_INTERVAL_MS = 200

_MOUSE_BUTTONS = {
    "left": wire.MOUSE_BUTTON_LEFT,
    "right": wire.MOUSE_BUTTON_RIGHT,
    "middle": wire.MOUSE_BUTTON_MIDDLE,
}


class MacroRunner:
    def __init__(self, graph: dict, capture, sink: CommandSink, hwnd=None,
                 profile_dir: Path | str = "."):
        self._graph = graph
        self._capture = capture
        self._sink = sink
        self._hwnd = hwnd
        self._profile_dir = Path(profile_dir)
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

    def _run_action(self, node: dict) -> None:
        action_type = node["action_type"]
        if action_type == "key_press":
            keycode = wire.keycode_for_letter(node["key_combo"])
            self._sink.send(Command(action=wire.ACTION_KEY_PRESS, keycodes=(keycode,)))
        elif action_type == "click":
            button = _MOUSE_BUTTONS[node.get("mouse_button", "left")]
            for command in click_commands(self._hwnd, tuple(node["click_rect"]), button):
                self._sink.send(command)
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
