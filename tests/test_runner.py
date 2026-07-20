import time

import cv2
import numpy as np

import engine.runner as runner_module
from engine.command import Command, RecordingCommandSink
from engine.runner import MacroRunner
import protocol as wire  # available via engine.runner's sys.path insert of host/


class FakeCapture:
    """Returns frames in order, repeating the last one once exhausted."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._index = 0

    def get_latest_frame_bgr(self):
        frame = self._frames[min(self._index, len(self._frames) - 1)]
        self._index += 1
        return frame


def _run_briefly(graph, capture, sink, seconds=0.3, hwnd=None, profile_dir="."):
    runner = MacroRunner(graph, capture, sink, hwnd=hwnd, profile_dir=profile_dir)
    runner.start()
    time.sleep(seconds)
    runner.stop()
    runner.join(timeout=2)
    return runner


def test_cyclic_action_wait_graph_runs_multiple_iterations():
    sink = RecordingCommandSink()
    graph = {
        "start_node": "a1",
        "nodes": {
            "a1": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": "w1"},
            "w1": {"type": "wait", "duration_ms": 10, "out": "a1"},
        },
    }
    _run_briefly(graph, FakeCapture([None]), sink, seconds=0.3)

    assert len(sink.sent) > 1  # cyclic graph looped without hanging
    assert all(cmd.action == wire.ACTION_KEY_PRESS for cmd in sink.sent)
    assert sink.sent[0].keycodes == (wire.keycode_for_letter("a"),)


def test_decision_branch_true_and_false(tmp_path):
    content = np.full((10, 10, 3), (10, 20, 30), dtype=np.uint8)
    reference_bgra = np.dstack([content, np.full((10, 10), 255, dtype=np.uint8)])
    cv2.imwrite(str(tmp_path / "ref.png"), reference_bgra)

    matching_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    matching_frame[10:20, 10:20] = content
    nonmatching_frame = np.zeros((100, 100, 3), dtype=np.uint8)

    graph = {
        "start_node": "d1",
        "nodes": {
            "d1": {
                "type": "decision",
                "reference_path": "ref.png",
                "region": [10, 10, 10, 10],
                "match_threshold": 0.99,
                "evaluation_mode": "branch",
                "true": "true_action",
                "false": "false_action",
            },
            "true_action": {"type": "action", "action_type": "key_press",
                             "key_combo": "a", "out": "true_action"},
            "false_action": {"type": "action", "action_type": "key_press",
                              "key_combo": "b", "out": "false_action"},
        },
    }

    sink_true = RecordingCommandSink()
    _run_briefly(graph, FakeCapture([matching_frame]), sink_true, seconds=0.1, profile_dir=tmp_path)
    assert sink_true.sent
    assert sink_true.sent[0].keycodes == (wire.keycode_for_letter("a"),)

    sink_false = RecordingCommandSink()
    _run_briefly(graph, FakeCapture([nonmatching_frame]), sink_false, seconds=0.1, profile_dir=tmp_path)
    assert sink_false.sent
    assert sink_false.sent[0].keycodes == (wire.keycode_for_letter("b"),)


def test_decision_wait_until_true_polls_until_match(tmp_path):
    content = np.full((10, 10, 3), 100, dtype=np.uint8)
    reference_bgra = np.dstack([content, np.full((10, 10), 255, dtype=np.uint8)])
    cv2.imwrite(str(tmp_path / "ref.png"), reference_bgra)

    nonmatching = np.zeros((100, 100, 3), dtype=np.uint8)
    matching = np.zeros((100, 100, 3), dtype=np.uint8)
    matching[10:20, 10:20] = content

    graph = {
        "start_node": "d1",
        "nodes": {
            "d1": {
                "type": "decision",
                "reference_path": "ref.png",
                "region": [10, 10, 10, 10],
                "match_threshold": 0.99,
                "evaluation_mode": "wait_until_true",
                "poll_interval_ms": 10,
                "true": "done",
            },
            "done": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": "done"},
        },
    }

    sink = RecordingCommandSink()
    capture = FakeCapture([nonmatching, nonmatching, nonmatching, matching])
    _run_briefly(graph, capture, sink, seconds=0.3, profile_dir=tmp_path)

    assert sink.sent  # eventually matched and proceeded to "done"


def test_action_click_delegates_to_cursor_click_commands(monkeypatch):
    calls = []

    def fake_click_commands(hwnd, click_rect, mouse_button):
        calls.append((hwnd, click_rect, mouse_button))
        return [Command(action=wire.ACTION_MOUSE_MOVE, dx=1, dy=2),
                Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=wire.MOUSE_BUTTON_RIGHT)]

    monkeypatch.setattr(runner_module, "click_commands", fake_click_commands)

    sink = RecordingCommandSink()
    graph = {
        "start_node": "c1",
        "nodes": {
            "c1": {"type": "action", "action_type": "click", "click_rect": [1, 2, 3, 4],
                   "mouse_button": "right", "out": "c1"},
        },
    }
    _run_briefly(graph, FakeCapture([None]), sink, seconds=0.05, hwnd=1234)

    assert calls
    assert calls[0] == (1234, (1, 2, 3, 4), wire.MOUSE_BUTTON_RIGHT)
    assert len(sink.sent) >= 2
