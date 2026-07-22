import time

import cv2
import numpy as np
import pytest

import engine.runner as runner_module
from engine.command import Command, RecordingCommandSink
from engine.focus import FOCUS_POLICY_FOCUS_AND_RESUME, FOCUS_POLICY_PAUSE_UNTIL_FOCUSED, FocusTimeoutError
from engine.runner import MacroRunner
import protocol as wire  # available via engine.runner's sys.path insert of host/


class FocusState:
    """Simulates a target window's foreground state for MacroRunner's
    focus-policy tests, without touching real win32 calls.

    focus_after_call=True simulates a successful SetForegroundWindow() call
    (focus_and_resume policy); left False, focus() is a no-op recorded call,
    simulating a window that only regains focus some other way (e.g. the
    user alt-tabs back), matching the pause_until_focused policy."""

    def __init__(self, focused=False, focus_after_call=False):
        self.focused = focused
        self._focus_after_call = focus_after_call
        self.focus_calls = 0

    def is_focused(self, hwnd):
        return self.focused

    def focus(self, hwnd):
        self.focus_calls += 1
        if self._focus_after_call:
            self.focused = True


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


def test_action_click_delegates_to_cursor_click_at_target(monkeypatch):
    calls = []

    def fake_click_at_target(hwnd, click_rect, sink, mouse_button, gain_estimate=None):
        calls.append((hwnd, click_rect, mouse_button))
        sink.send(Command(action=wire.ACTION_MOUSE_MOVE, dx=1, dy=2))
        sink.send(Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=wire.MOUSE_BUTTON_RIGHT))

    monkeypatch.setattr(runner_module, "click_at_target", fake_click_at_target)

    sink = RecordingCommandSink()
    graph = {
        "start_node": "c1",
        "nodes": {
            "c1": {"type": "action", "action_type": "click", "click_rect": [1, 2, 3, 4],
                   "mouse_button": "right", "out": "c1"},
        },
    }
    # hwnd=1234 isn't a real window handle - fake is_window_focused so the
    # new focus-gating in _run_action doesn't block on it (unrelated to
    # what this test actually verifies: click delegation).
    runner = MacroRunner(graph, FakeCapture([None]), sink, hwnd=1234,
                          is_window_focused=lambda hwnd: True)
    runner.start()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert calls
    assert calls[0] == (1234, (1, 2, 3, 4), wire.MOUSE_BUTTON_RIGHT)
    assert len(sink.sent) >= 2


_KEY_PRESS_GRAPH = {
    "start_node": "a1",
    "nodes": {
        "a1": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": "a1"},
    },
}


def test_action_proceeds_immediately_when_target_already_focused():
    state = FocusState(focused=True)
    sink = RecordingCommandSink()

    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=1234,
        is_window_focused=state.is_focused, focus_window=state.focus,
        focus_poll_interval_ms=10,
    )
    runner.start()
    time.sleep(0.1)
    runner.stop()
    runner.join(timeout=2)

    assert sink.sent
    assert state.focus_calls == 0  # already focused, never needed to request focus


def test_pause_policy_waits_for_focus_before_acting():
    state = FocusState(focused=False)
    sink = RecordingCommandSink()

    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=1234,
        focus_policy=FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
        is_window_focused=state.is_focused, focus_window=state.focus,
        focus_poll_interval_ms=10,
    )
    runner.start()
    time.sleep(0.1)
    assert not sink.sent  # never focused - still paused, no action sent
    assert state.focus_calls == 0  # pause policy never tries to steal focus

    state.focused = True
    time.sleep(0.1)
    runner.stop()
    runner.join(timeout=2)

    assert sink.sent  # proceeded once focus was regained


def test_focus_and_resume_policy_calls_focus_window_then_proceeds():
    state = FocusState(focused=False, focus_after_call=True)
    sink = RecordingCommandSink()

    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=1234,
        focus_policy=FOCUS_POLICY_FOCUS_AND_RESUME,
        is_window_focused=state.is_focused, focus_window=state.focus,
        focus_poll_interval_ms=10,
    )
    runner.start()
    time.sleep(0.1)
    runner.stop()
    runner.join(timeout=2)

    assert state.focus_calls >= 1
    assert sink.sent


def test_stop_while_waiting_for_focus_sends_no_action():
    state = FocusState(focused=False)
    sink = RecordingCommandSink()

    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=1234,
        focus_policy=FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
        is_window_focused=state.is_focused, focus_window=state.focus,
        focus_poll_interval_ms=50,
    )
    runner.start()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert not sink.sent


def test_unknown_focus_policy_raises():
    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), RecordingCommandSink(), hwnd=1234,
        focus_policy="bogus",
        is_window_focused=lambda hwnd: False, focus_window=lambda hwnd: None,
    )
    with pytest.raises(ValueError):
        runner._ensure_focus()


def test_ensure_focus_raises_focus_timeout_instead_of_looping_forever():
    # Windows can refuse to ever hand over the foreground - confirmed
    # against real hardware that this otherwise looks like the whole app
    # freezing. A short max_focus_wait_seconds here keeps the test fast.
    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), RecordingCommandSink(), hwnd=1234,
        focus_policy=FOCUS_POLICY_FOCUS_AND_RESUME,
        is_window_focused=lambda hwnd: False, focus_window=lambda hwnd: None,
        focus_poll_interval_ms=10, max_focus_wait_seconds=0.05,
    )
    with pytest.raises(FocusTimeoutError):
        runner._ensure_focus()
