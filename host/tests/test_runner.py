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


def test_action_with_no_out_ends_run_instead_of_erroring():
    sink = RecordingCommandSink()
    graph = {
        "start_node": "a1",
        "nodes": {
            "a1": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": None},
        },
    }
    runner = MacroRunner(graph, FakeCapture([None]), sink)
    runner.start()
    runner.join(timeout=2)  # dead end - thread should exit on its own, no stop() needed

    assert not runner.is_running()
    assert len(sink.sent) == 1  # ran once, then stopped at the dangling "out"


def test_is_running_reflects_the_thread_actually_finishing_on_its_own():
    # Regression test: is_running() must go False once a dead-end node ends
    # the run by itself, not just after an explicit .stop() - a caller that
    # tracked "still running" some other way (e.g. just "did I ever call
    # stop() on this") would report a finished run as active forever,
    # confirmed via a real report of the web UI's Run button never
    # reverting to "Run" after reaching a dead end.
    sink = RecordingCommandSink()
    graph = {
        "start_node": "a1",
        "nodes": {"a1": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": None}},
    }
    runner = MacroRunner(graph, FakeCapture([None]), sink)

    assert runner.is_running() is False  # never started yet

    runner.start()
    runner.join(timeout=2)

    assert runner.is_running() is False  # ended itself at the dead end, no stop() called


def test_branch_true_and_false(tmp_path):
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
                "type": "branch",
                "images": [{"reference_path": "ref.png", "region": [10, 10, 10, 10], "out": "true_action"}],
                "match_threshold": 0.99,
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


def test_branch_wait_polls_until_match(tmp_path):
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
                "type": "branch_wait",
                "images": [{"reference_path": "ref.png", "region": [10, 10, 10, 10], "out": "done"}],
                "match_threshold": 0.99,
                "poll_interval_ms": 10,
            },
            "done": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": "done"},
        },
    }

    sink = RecordingCommandSink()
    capture = FakeCapture([nonmatching, nonmatching, nonmatching, matching])
    _run_briefly(graph, capture, sink, seconds=0.3, profile_dir=tmp_path)

    assert sink.sent  # eventually matched and proceeded to "done"


def _two_image_branch_graph(tmp_path, node_type, poll_interval_ms=10):
    """Two OR'd reference images at disjoint regions - image "1" (region A,
    content 100) takes 'a_action', image "2" (region B, content 200) takes
    'b_action'. Returns (graph, content_a, content_b, region_a, region_b)
    so callers can build frames with either/both/neither region matching.
    node_type: "branch" or "branch_wait" - each only gets the field it
    would actually have (branch: "false", never "poll_interval_ms";
    branch_wait: the reverse), matching build_engine_graph_from_document()'s
    real output shape rather than a generic always-both-fields fixture."""
    content_a = np.full((10, 10, 3), 100, dtype=np.uint8)
    content_b = np.full((10, 10, 3), 200, dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "a.png"), np.dstack([content_a, np.full((10, 10), 255, dtype=np.uint8)]))
    cv2.imwrite(str(tmp_path / "b.png"), np.dstack([content_b, np.full((10, 10), 255, dtype=np.uint8)]))

    region_a = [10, 10, 10, 10]
    region_b = [50, 50, 10, 10]

    node = {
        "type": node_type,
        "images": [
            {"reference_path": "a.png", "region": region_a, "out": "a_action"},
            {"reference_path": "b.png", "region": region_b, "out": "b_action"},
        ],
        "match_threshold": 0.99,
    }
    if node_type == "branch":
        node["false"] = "false_action"
    else:
        node["poll_interval_ms"] = poll_interval_ms

    graph = {
        "start_node": "d1",
        "nodes": {
            "d1": node,
            "a_action": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": "a_action"},
            "b_action": {"type": "action", "action_type": "key_press", "key_combo": "b", "out": "b_action"},
            "false_action": {"type": "action", "action_type": "key_press", "key_combo": "c", "out": "false_action"},
        },
    }
    return graph, content_a, content_b, region_a, region_b


def _frame_with(content, region):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    if content is not None:
        x, y, w, h = region
        frame[y:y + h, x:x + w] = content
    return frame


def test_branch_or_match_first_image_wins_when_multiple_match(tmp_path):
    graph, content_a, content_b, region_a, region_b = _two_image_branch_graph(tmp_path, "branch")
    frame = _frame_with(content_a, region_a)
    x, y, w, h = region_b
    frame[y:y + h, x:x + w] = content_b  # both regions now match their own reference

    sink = RecordingCommandSink()
    _run_briefly(graph, FakeCapture([frame]), sink, seconds=0.1, profile_dir=tmp_path)

    assert sink.sent
    assert sink.sent[0].keycodes == (wire.keycode_for_letter("a"),)  # image "1" wins the tie


def test_branch_or_match_falls_through_to_second_image(tmp_path):
    graph, _content_a, content_b, _region_a, region_b = _two_image_branch_graph(tmp_path, "branch")
    frame = _frame_with(content_b, region_b)  # only image "2"'s region matches

    sink = RecordingCommandSink()
    _run_briefly(graph, FakeCapture([frame]), sink, seconds=0.1, profile_dir=tmp_path)

    assert sink.sent
    assert sink.sent[0].keycodes == (wire.keycode_for_letter("b"),)


def test_branch_or_match_takes_false_when_no_image_matches(tmp_path):
    graph, _content_a, _content_b, _region_a, _region_b = _two_image_branch_graph(tmp_path, "branch")
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    sink = RecordingCommandSink()
    _run_briefly(graph, FakeCapture([frame]), sink, seconds=0.1, profile_dir=tmp_path)

    assert sink.sent
    assert sink.sent[0].keycodes == (wire.keycode_for_letter("c"),)


def test_branch_wait_or_match_takes_whichever_image_matches_first(tmp_path):
    graph, _content_a, content_b, _region_a, region_b = _two_image_branch_graph(tmp_path, "branch_wait")
    nonmatching = np.zeros((100, 100, 3), dtype=np.uint8)
    matching_b = _frame_with(content_b, region_b)  # image "1" never matches, only "2" does

    sink = RecordingCommandSink()
    capture = FakeCapture([nonmatching, nonmatching, matching_b])
    _run_briefly(graph, capture, sink, seconds=0.3, profile_dir=tmp_path)

    assert sink.sent
    assert sink.sent[0].keycodes == (wire.keycode_for_letter("b"),)


def test_action_click_delegates_to_cursor_click_at_target(monkeypatch):
    calls = []

    def fake_click_at_target(hwnd, click_rect, sink, mouse_button, gain_estimate=None,
                             crossing_mode=None):
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


def test_full_run_surfaces_focus_timeout_via_error_instead_of_dying_silently():
    # Regression test: a real report where a single key-press action never
    # fired with zero indication why. Root cause: .start() runs on a
    # background thread, and an uncaught exception there (FocusTimeoutError,
    # here - the target window never came to foreground) just ends the
    # thread with nothing surfaced anywhere a GUI user would see - not a
    # dead end (which is a normal, silent, expected way to finish), a real
    # failure that needs to be visible.
    sink = RecordingCommandSink()
    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=1234,
        focus_policy=FOCUS_POLICY_PAUSE_UNTIL_FOCUSED,
        is_window_focused=lambda hwnd: False, focus_window=lambda hwnd: None,
        focus_poll_interval_ms=10, max_focus_wait_seconds=0.05,
    )

    assert runner.error is None
    runner.start()
    runner.join(timeout=2)

    assert not runner.is_running()
    assert runner.error is not None
    assert 'did not come to focus' in runner.error
    assert sink.sent == []  # never actually pressed anything


def test_confirmation_mode_key_press_waits_for_confirm_before_sending():
    shown = []
    sink = RecordingCommandSink()
    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=None,
        confirmation_mode=True,
        show_pending_key_press=lambda key_combo, screen_pos: shown.append((key_combo, screen_pos)),
        confirmation_poll_interval_ms=10,
    )
    runner.start()
    time.sleep(0.05)
    assert shown == [("a", None)]  # shown the pending key (hwnd=None -> no screen_pos)
    assert not sink.sent  # still waiting for confirmation

    runner.confirm()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert sink.sent  # proceeded once confirmed


def test_confirmation_mode_key_press_computes_screen_pos_from_window_origin(monkeypatch):
    monkeypatch.setattr(runner_module, "get_window_screen_origin", lambda hwnd: (100, 100))

    shown = []
    sink = RecordingCommandSink()
    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=1234,
        confirmation_mode=True,
        show_pending_key_press=lambda key_combo, screen_pos: shown.append((key_combo, screen_pos)),
        confirmation_poll_interval_ms=10,
        is_window_focused=lambda hwnd: True,  # hwnd=1234 isn't a real window
    )
    runner.start()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert shown == [("a", (100, 100))]  # the pending-key overlay anchors to the window origin


def test_confirmation_mode_click_moves_cursor_and_waits_before_clicking(monkeypatch):
    move_calls = []
    monkeypatch.setattr(
        runner_module, "move_cursor_to_target",
        lambda hwnd, click_rect, sink, **kwargs: move_calls.append((hwnd, click_rect)),
    )
    monkeypatch.setattr(runner_module, "get_window_screen_origin", lambda hwnd: (100, 100))

    shown = []
    sink = RecordingCommandSink()
    graph = {
        "start_node": "c1",
        "nodes": {
            "c1": {"type": "action", "action_type": "click", "click_rect": [10, 20, 30, 40],
                   "mouse_button": "right", "out": "c1"},
        },
    }
    runner = MacroRunner(
        graph, FakeCapture([None]), sink, hwnd=1234,
        confirmation_mode=True,
        show_pending_click=lambda screen_rect: shown.append(screen_rect),
        confirmation_poll_interval_ms=10,
        is_window_focused=lambda hwnd: True,  # hwnd=1234 isn't a real window
    )
    runner.start()
    time.sleep(0.05)

    assert move_calls  # cursor positioned before waiting for confirmation
    assert shown == [(110, 120, 30, 40)]  # origin(100,100) + click_rect(10,20,30,40)
    assert not any(c.action == wire.ACTION_MOUSE_CLICK for c in sink.sent)  # not clicked yet

    runner.confirm()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert any(c.action == wire.ACTION_MOUSE_CLICK and c.mouse_buttons == wire.MOUSE_BUTTON_RIGHT
               for c in sink.sent)


def test_confirmation_mode_stop_while_waiting_sends_no_action():
    sink = RecordingCommandSink()
    runner = MacroRunner(
        _KEY_PRESS_GRAPH, FakeCapture([None]), sink, hwnd=None,
        confirmation_mode=True,
        confirmation_poll_interval_ms=10,
    )
    runner.start()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert not sink.sent


def _branch_graph(tmp_path, node_type, poll_interval_ms=10):
    """node_type: "branch" or "branch_wait" - see _two_image_branch_graph()'s
    docstring for why each only gets the field it would actually have."""
    content = np.full((10, 10, 3), 100, dtype=np.uint8)
    reference_bgra = np.dstack([content, np.full((10, 10), 255, dtype=np.uint8)])
    cv2.imwrite(str(tmp_path / "ref.png"), reference_bgra)

    nonmatching = np.zeros((100, 100, 3), dtype=np.uint8)
    matching = np.zeros((100, 100, 3), dtype=np.uint8)
    matching[10:20, 10:20] = content

    node = {
        "type": node_type,
        "images": [{"reference_path": "ref.png", "region": [10, 10, 10, 10], "out": "done"}],
        "match_threshold": 0.99,
    }
    if node_type == "branch":
        node["false"] = "done"
    else:
        node["poll_interval_ms"] = poll_interval_ms

    graph = {
        "start_node": "d1",
        "nodes": {
            "d1": node,
            "done": {"type": "action", "action_type": "key_press", "key_combo": "a", "out": "done"},
        },
    }
    return graph, nonmatching, matching


def test_branch_wait_shows_overlay_each_poll_without_blocking(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "get_window_extended_frame_origin", lambda hwnd: (100, 100))
    graph, nonmatching, matching = _branch_graph(tmp_path, "branch_wait")

    shown = []
    hidden = []
    sink = RecordingCommandSink()
    capture = FakeCapture([nonmatching, nonmatching, matching])
    runner = MacroRunner(
        graph, capture, sink, hwnd=1234, profile_dir=tmp_path,
        show_decision_overlay=shown.append, hide_decision_overlay=lambda: hidden.append(True),
        is_window_focused=lambda hwnd: True,
    )
    runner.start()
    time.sleep(0.2)
    runner.stop()
    runner.join(timeout=2)

    assert len(shown) >= 2  # updated across multiple polls
    assert shown[0]["screen_rect"] == (110, 110, 10, 10)  # origin(100,100) + region(10,10,10,10)
    assert shown[-1]["score"] >= shown[0]["score"]  # eventually converges on the matching frame
    assert hidden  # cleared once matched
    assert sink.sent  # proceeded without ever needing confirm()


def test_branch_mode_shows_no_overlay_without_confirmation_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "get_window_extended_frame_origin", lambda hwnd: (100, 100))
    graph, nonmatching, _matching = _branch_graph(tmp_path, "branch")

    shown = []
    sink = RecordingCommandSink()
    runner = MacroRunner(
        graph, FakeCapture([nonmatching]), sink, hwnd=1234, profile_dir=tmp_path,
        show_decision_overlay=shown.append,
    )
    runner.start()
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert not shown  # Branch mode + confirmation_mode off: no overlay at all


def test_branch_mode_with_confirmation_mode_shows_overlay_and_waits_for_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "get_window_extended_frame_origin", lambda hwnd: (100, 100))
    graph, nonmatching, _matching = _branch_graph(tmp_path, "branch")

    shown = []
    hidden = []
    sink = RecordingCommandSink()
    runner = MacroRunner(
        graph, FakeCapture([nonmatching]), sink, hwnd=1234, profile_dir=tmp_path,
        confirmation_mode=True, confirmation_poll_interval_ms=10,
        show_decision_overlay=shown.append, hide_decision_overlay=lambda: hidden.append(True),
        is_window_focused=lambda hwnd: True,
    )
    runner.start()
    time.sleep(0.05)

    assert shown  # single evaluation already shown
    assert not sink.sent  # blocked waiting for confirmation
    assert not hidden

    runner.confirm()  # resolves the decision's own confirmation gate
    time.sleep(0.05)

    assert hidden  # decision overlay cleared once its confirmation resolved
    assert not sink.sent  # confirmation_mode also gates the following action

    runner.confirm()  # resolves the subsequent key_press action's gate
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert sink.sent  # proceeded once both gates were confirmed


def test_branch_wait_with_confirmation_mode_waits_for_confirm_after_match(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "get_window_extended_frame_origin", lambda hwnd: (100, 100))
    graph, nonmatching, matching = _branch_graph(tmp_path, "branch_wait")

    shown = []
    hidden = []
    sink = RecordingCommandSink()
    capture = FakeCapture([nonmatching, matching, matching, matching])
    runner = MacroRunner(
        graph, capture, sink, hwnd=1234, profile_dir=tmp_path,
        confirmation_mode=True, confirmation_poll_interval_ms=10,
        show_decision_overlay=shown.append, hide_decision_overlay=lambda: hidden.append(True),
        is_window_focused=lambda hwnd: True,
    )
    runner.start()
    time.sleep(0.1)

    assert shown  # polled and updated at least once
    assert not sink.sent  # matched, but blocked waiting for confirmation
    assert not hidden

    runner.confirm()  # resolves the decision's own confirmation gate
    time.sleep(0.05)

    assert hidden  # decision overlay cleared once its confirmation resolved
    assert not sink.sent  # confirmation_mode also gates the following action

    runner.confirm()  # resolves the subsequent key_press action's gate
    time.sleep(0.05)
    runner.stop()
    runner.join(timeout=2)

    assert sink.sent  # proceeded once both gates were confirmed
