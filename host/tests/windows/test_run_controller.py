"""Tests for RunController's start()/stop()/confirm() orchestration logic -
resolve_target_window/window_capture_factory/macro_runner_factory/
command_sink_factory are all swapped for fakes (the same dependency-
injection seams runner.py's own MacroRunner tests use for
is_window_focused/focus_window), so nothing here touches a real HID
device, target window, screen capture, or overlay widget. RunController is
a QObject (needed for its cross-thread signals - see its module
docstring), but these tests never trigger the paths that actually
construct a QWidget (that only happens inside a MacroRunner callback,
which FakeMacroRunner never calls), so no QApplication/display is needed
either."""
import pytest

from run_controller import RunController
from window_resolve import ResolveResult


class FakeHidLink:
    pass


class FakeCapture:
    def __init__(self, window_hwnd=None, window_title=None):
        self.window_hwnd = window_hwnd
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeSink:
    def __init__(self, dev):
        self.dev = dev


class FakeMacroRunner:
    """Records its constructor args instead of actually running anything -
    orchestration tests only need to see that RunController wired things up
    correctly, not exercise MacroRunner itself (already covered by
    test_runner.py)."""
    instances = []

    def __init__(self, graph, capture, sink, **kwargs):
        self.graph = graph
        self.capture = capture
        self.sink = sink
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.confirmed = False
        self._running = False  # flips True on start() - a test can flip it back to simulate a dead-end finish
        self.error = None  # a test can set this to simulate a real failure (e.g. FocusTimeoutError), not a dead end
        FakeMacroRunner.instances.append(self)

    def start(self):
        self.started = True
        self._running = True

    def stop(self):
        self.stopped = True
        self._running = False

    def confirm(self):
        self.confirmed = True

    def is_running(self):
        return self._running


@pytest.fixture(autouse=True)
def _reset_fake_macro_runner_instances():
    FakeMacroRunner.instances = []
    yield
    FakeMacroRunner.instances = []


def _resolved(hwnd=1):
    return lambda target_executable, target_window_title='': ResolveResult(hwnd=hwnd, needs_confirmation=False)


def _needs_confirmation(candidates=()):
    return lambda target_executable, target_window_title='': ResolveResult(needs_confirmation=True, candidates=list(candidates))


_UNSET = object()


def make_controller(hid_link=_UNSET, resolve_target_window=None):
    return RunController(
        hid_link=FakeHidLink() if hid_link is _UNSET else hid_link,
        resolve_target_window=resolve_target_window or _resolved(),
        window_capture_factory=FakeCapture,
        macro_runner_factory=FakeMacroRunner,
        command_sink_factory=FakeSink,
    )


def test_start_fails_without_hid_link():
    controller = make_controller(hid_link=None)
    result = controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    assert result == {'ok': False, 'error': 'No Raw HID device connected.'}
    assert not controller.is_running


def test_start_fails_without_engine_graph():
    controller = make_controller()
    result = controller.start(None, 'app.exe', '', '.', 'pause_until_focused', False)
    assert result == {'ok': False, 'error': 'Set a start node first.'}


def test_start_fails_without_target_executable():
    controller = make_controller()
    result = controller.start({'start_node': 'n1', 'nodes': {}}, '', '', '.', 'pause_until_focused', False)
    assert result['ok'] is False
    assert 'target executable' in result['error']


def test_start_fails_when_already_running():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    result = controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    assert result == {'ok': False, 'error': 'Already running.'}


def test_start_with_ambiguous_target_and_candidates_returns_disambiguation_error():
    controller = make_controller(resolve_target_window=_needs_confirmation(candidates=['a', 'b']))
    result = controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    assert result['ok'] is False
    assert 'multiple windows' in result['error']


def test_start_with_no_matching_window_returns_not_found_error():
    controller = make_controller(resolve_target_window=_needs_confirmation(candidates=[]))
    result = controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    assert result['ok'] is False
    assert 'No window found' in result['error']


def test_start_success_constructs_capture_and_runner_and_starts_both():
    controller = make_controller(resolve_target_window=_resolved(hwnd=42))
    graph = {'start_node': 'n1', 'nodes': {}}

    result = controller.start(graph, 'app.exe', 'Some Window', '/profiles/x', 'focus_and_resume', True)

    assert result == {'ok': True}
    assert controller.is_running
    runner = FakeMacroRunner.instances[0]
    assert runner.started is True
    assert runner.graph is graph
    assert runner.capture.window_hwnd == 42
    assert runner.capture.started is True
    assert runner.kwargs['hwnd'] == 42
    assert runner.kwargs['profile_dir'] == '/profiles/x'
    assert runner.kwargs['focus_policy'] == 'focus_and_resume'
    assert runner.kwargs['confirmation_mode'] is True


def test_stop_stops_runner_and_capture_and_clears_running_state():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    runner = FakeMacroRunner.instances[0]
    capture = runner.capture

    result = controller.stop()

    assert result == {'ok': True}
    assert not controller.is_running
    assert runner.stopped is True
    assert capture.stopped is True


def test_stop_clears_pending_state_via_signal_not_a_direct_call():
    # Regression test: stop() is callable from pywebview's background API
    # thread (bridge.py's stop_macro()), not just the GUI thread
    # (&ssm_tog's handler) - touching _pending_confirmation_overlay (a
    # QWidget) directly from stop() froze the app for real whenever it
    # happened to run off the GUI thread. Emitting _clear_pending_signal
    # (Qt auto-marshals it onto the receiver's own thread) is the only
    # thread-safe way to reach that cleanup - if stop() ever regresses to
    # calling _clear_pending_indicator()/overlay methods directly instead,
    # this spy-connected slot won't fire.
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    emitted = []
    controller._clear_pending_signal.connect(lambda: emitted.append(True))

    controller.stop()

    assert emitted == [True]


def test_stop_emits_branch_overlay_hide_via_signal_not_a_direct_call():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    emitted = []
    controller._branch_overlay_hide_signal.connect(lambda: emitted.append(True))

    controller.stop()

    assert emitted == [True]


def test_stop_when_not_running_is_a_safe_no_op():
    controller = make_controller()
    assert controller.stop() == {'ok': True}


def test_confirm_delegates_to_running_macro_runner():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    runner = FakeMacroRunner.instances[0]

    controller.confirm()

    assert runner.confirmed is True


def test_confirm_when_not_running_is_a_safe_no_op():
    controller = make_controller()
    controller.confirm()  # must not raise


def test_confirm_clears_pending_state_via_signal_not_a_direct_call():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    emitted = []
    controller._clear_pending_signal.connect(lambda: emitted.append(True))

    controller.confirm()

    assert emitted == [True]


def test_after_stop_a_new_run_can_start_again():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    controller.stop()

    result = controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)

    assert result == {'ok': True}
    assert len(FakeMacroRunner.instances) == 2


def test_poll_reaps_a_run_that_finished_on_its_own():
    # Regression test: a dead-end node (no outgoing connection) ends a run
    # by itself, without .stop() ever being called - is_running must still
    # go False once that's noticed (via poll(), called from
    # bridge.py's get_run_state() on every JS polling tick), or the
    # web UI's Run button stays stuck on "Stop" forever (a real report).
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    runner = FakeMacroRunner.instances[0]
    capture = runner.capture
    runner._running = False  # simulate the background thread finishing on its own

    controller.poll()

    assert controller.is_running is False
    assert capture.stopped is True


def test_poll_is_a_safe_no_op_while_still_actually_running():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)

    controller.poll()

    assert controller.is_running is True


def test_poll_is_a_safe_no_op_when_nothing_is_running():
    controller = make_controller()
    controller.poll()  # must not raise
    assert controller.is_running is False


def test_poll_clears_pending_state_via_signal_when_reaping():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    runner = FakeMacroRunner.instances[0]
    runner._running = False
    emitted = []
    controller._clear_pending_signal.connect(lambda: emitted.append(True))

    controller.poll()

    assert emitted == [True]


def test_poll_surfaces_a_real_failure_via_last_error():
    # Regression test: a real report of an action that silently never
    # fired - runner.py's MacroRunner.error is set when the
    # background thread ends via an uncaught exception (e.g.
    # FocusTimeoutError), distinct from a dead end (which leaves .error
    # None). poll() must carry that message into last_error so
    # bridge.py's get_run_state() can surface it to the user.
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    runner = FakeMacroRunner.instances[0]
    runner._running = False
    runner.error = 'target window did not come to focus within 10.0s'

    controller.poll()

    assert controller.last_error == 'target window did not come to focus within 10.0s'


def test_poll_after_a_dead_end_leaves_last_error_none():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    FakeMacroRunner.instances[0]._running = False  # a dead end - .error stays None

    controller.poll()

    assert controller.last_error is None


def test_start_clears_a_stale_last_error_from_the_previous_run():
    controller = make_controller()
    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)
    FakeMacroRunner.instances[0]._running = False
    FakeMacroRunner.instances[0].error = 'boom'
    controller.poll()
    assert controller.last_error == 'boom'

    controller.start({'start_node': 'n1', 'nodes': {}}, 'app.exe', '', '.', 'pause_until_focused', False)

    assert controller.last_error is None
