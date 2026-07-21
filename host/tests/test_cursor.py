import pytest

import engine.cursor as cursor_module
from engine.command import Command, RecordingCommandSink
from engine.cursor import CursorConvergenceError, _wait_for_settled_position, click_at_target
import protocol as wire  # available via engine.cursor's sys.path insert of host/


class FakeClock:
    """Deterministic stand-in for time.monotonic()/time.sleep() - sleep()
    advances the clock instead of actually waiting, so tests never depend
    on real wall-clock timing."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class SimulatingSink(RecordingCommandSink):
    """Records commands like RecordingCommandSink, but also tracks a
    simulated cursor position, applying each move's delta scaled by
    move_ratio - lets tests simulate the OS's pointer-acceleration curve
    under- or over-applying (even consistently amplifying) the requested
    delta, without a real display or real win32 calls. Position updates
    are instantaneous (no settle latency) - _wait_for_settled_position's
    own polling/latency behavior is tested separately, in isolation."""

    def __init__(self, start_pos, move_ratio=1.0):
        super().__init__()
        self.pos = start_pos
        self._move_ratio = move_ratio

    def send(self, command: Command) -> None:
        super().send(command)
        if command.action == wire.ACTION_MOUSE_MOVE:
            self.pos = (
                self.pos[0] + round(command.dx * self._move_ratio),
                self.pos[1] + round(command.dy * self._move_ratio),
            )

    def get_pos(self):
        return self.pos


# No settle latency to simulate in these click_at_target tests (that's
# covered separately below) - a clock that never advances means
# _wait_for_settled_position's max_wait never trips, so it just relies on
# SimulatingSink's instantaneous, stable position.
#
# get_monitor_rects: a single effectively-infinite monitor, far from any
# test's start_pos/target - without this, click_at_target would call the
# REAL EnumDisplayMonitors, and on a real multi-monitor machine (0, 0) can
# genuinely sit on a different monitor than some test's target, triggering
# the cross-monitor logic unpredictably depending on whatever's actually
# connected.
_HUGE_MONITOR_RECTS = [(-100_000, -100_000, 100_000, 100_000)]
_NO_SETTLE_DELAY = {
    "sleep": lambda seconds: None, "now": lambda: 0.0,
    "get_monitor_rects": lambda: _HUGE_MONITOR_RECTS,
}


def test_click_at_target_converges_in_one_move_when_delta_lands_exactly():
    # Distance kept within the attempt-1 calibration-probe cap
    # (DEFAULT_INITIAL_PROBE_MAX_PX) so this exercises the simple, ideal
    # single-move case rather than the probe-then-full-shot behavior
    # covered separately below.
    sink = SimulatingSink(start_pos=(200, 200), move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(50, 60, 20, 20),  # center = (60, 70) window-relative
        sink=sink,
        mouse_button=wire.MOUSE_BUTTON_RIGHT,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (100, 100),
        **_NO_SETTLE_DELAY,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    assert len(move_commands) == 1
    # target screen pos = origin(100,100) + center(60,70) = (160,170)
    assert (move_commands[0].dx, move_commands[0].dy) == (160 - 200, 170 - 200)

    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK
    assert sink.sent[-1].mouse_buttons == wire.MOUSE_BUTTON_RIGHT


def test_click_at_target_caps_the_first_attempt_as_a_calibration_probe():
    # A large remaining distance (~3000px) with a perfectly linear (1:1)
    # responder still shouldn't send the full distance on attempt 1 - it
    # should send a small probe first, then use the larger steady-state
    # cap once it has real gain data.
    sink = SimulatingSink(start_pos=(0, 0), move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(0, 0, 20, 20),  # center = (10, 10)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (3000, 3000),
        **_NO_SETTLE_DELAY,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    assert len(move_commands) >= 2
    assert abs(move_commands[0].dx) <= cursor_module.DEFAULT_INITIAL_PROBE_MAX_PX
    assert abs(move_commands[0].dy) <= cursor_module.DEFAULT_INITIAL_PROBE_MAX_PX
    assert any(abs(c.dx) > cursor_module.DEFAULT_INITIAL_PROBE_MAX_PX for c in move_commands[1:])
    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK


def test_click_at_target_corrects_when_move_undershoots():
    # move_ratio=0.5 simulates every requested move only landing halfway -
    # click_at_target should keep sending corrective moves (adapting its
    # gain estimate toward 0.5, so later requests ask for roughly double
    # the raw remaining distance) until it's within tolerance, rather than
    # requesting the same undershooting amount forever.
    sink = SimulatingSink(start_pos=(0, 0), move_ratio=0.5)

    click_at_target(
        hwnd=1,
        click_rect=(0, 0, 20, 20),  # center = (10, 10)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (0, 0),
        **_NO_SETTLE_DELAY,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    assert len(move_commands) > 1  # took more than a blind single shot to land
    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK
    assert abs(sink.pos[0] - 10) <= 2 and abs(sink.pos[1] - 10) <= 2  # within tolerance


def test_click_at_target_converges_despite_consistent_amplification():
    # move_ratio=4.0 simulates the exact failure mode confirmed against
    # real hardware: a consistently-amplifying pointer-acceleration curve.
    # Without gain adaptation this oscillates between overshoot and
    # correction forever (reproduced with real hardware logs); with it,
    # click_at_target should learn the ~4x gain and converge.
    sink = SimulatingSink(start_pos=(0, 0), move_ratio=4.0)

    click_at_target(
        hwnd=1,
        click_rect=(0, 0, 20, 20),  # center = (10, 10)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (0, 0),
        **_NO_SETTLE_DELAY,
    )

    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK
    assert abs(sink.pos[0] - 10) <= 2 and abs(sink.pos[1] - 10) <= 2


def test_click_at_target_raises_and_does_not_click_when_it_never_converges():
    # move_ratio=0.0 simulates every requested move having no effect at all.
    sink = SimulatingSink(start_pos=(0, 0), move_ratio=0.0)

    with pytest.raises(CursorConvergenceError):
        click_at_target(
            hwnd=1,
            click_rect=(0, 0, 20, 20),
            sink=sink,
            get_cursor_pos=sink.get_pos,
            get_window_screen_origin=lambda hwnd: (0, 0),
            max_iterations=3,
            **_NO_SETTLE_DELAY,
        )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    assert len(move_commands) == 3  # exhausted max_iterations
    assert not any(c.action == wire.ACTION_MOUSE_CLICK for c in sink.sent)  # never clicked


def test_click_at_target_defaults_to_left_button():
    sink = SimulatingSink(start_pos=(0, 0), move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(0, 0, 10, 10),
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (0, 0),
        **_NO_SETTLE_DELAY,
    )

    click_cmd = next(c for c in sink.sent if c.action == wire.ACTION_MOUSE_CLICK)
    assert click_cmd.mouse_buttons == wire.MOUSE_BUTTON_LEFT


# -- cross-monitor crossing (real geometry, checked before the normal approach) --

_SIDE_BY_SIDE_MONITORS = [(0, 0, 1000, 1000), (1000, 0, 2000, 1000)]


class StuckThenFreeSink(RecordingCommandSink):
    """Blocks the first blocked_move_count MOUSE_MOVE commands entirely
    (zero effect, simulating a push stuck at a seam), then applies
    move_ratio normally to every move after that."""

    def __init__(self, start_pos, blocked_move_count, move_ratio=1.0):
        super().__init__()
        self.pos = start_pos
        self._blocked_move_count = blocked_move_count
        self._move_ratio = move_ratio
        self._move_send_count = 0

    def send(self, command: Command) -> None:
        super().send(command)
        if command.action != wire.ACTION_MOUSE_MOVE:
            return
        self._move_send_count += 1
        if self._move_send_count <= self._blocked_move_count:
            return  # still blocked
        self.pos = (
            self.pos[0] + round(command.dx * self._move_ratio),
            self.pos[1] + round(command.dy * self._move_ratio),
        )

    def get_pos(self):
        return self.pos


def test_click_at_target_crosses_to_target_monitor_before_normal_approach():
    # Cursor starts on the left monitor; target is on the right one - the
    # first move should be the crossing push (a single straight,
    # full-magnitude step toward the target monitor), confirmed via real
    # monitor geometry once it lands there, before the normal gain-based
    # approach takes over to land precisely on the target.
    sink = SimulatingSink(start_pos=(950, 500), move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(1490, 490, 20, 20),  # center (1500, 500) via origin (0, 0)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (0, 0),
        get_monitor_rects=lambda: _SIDE_BY_SIDE_MONITORS,
        sleep=lambda seconds: None,
        now=lambda: 0.0,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    assert move_commands[0].dx == cursor_module.DEFAULT_CROSSING_STEP_PX
    assert move_commands[0].dy == 0
    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK
    assert abs(sink.pos[0] - 1500) <= 2 and abs(sink.pos[1] - 500) <= 2


def test_click_at_target_skips_crossing_when_already_on_target_monitor():
    # Both cursor and target are on the same (single, huge) monitor - no
    # dedicated crossing push should happen; the very first move is just
    # the normal gain-adaptive request.
    sink = SimulatingSink(start_pos=(200, 200), move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(50, 60, 20, 20),  # center = (60, 70)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (100, 100),
        **_NO_SETTLE_DELAY,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    assert len(move_commands) == 1
    assert (move_commands[0].dx, move_commands[0].dy) == (160 - 200, 170 - 200)


def test_click_at_target_steps_back_once_with_a_single_move_when_crossing_gets_stuck():
    # The first crossing push has zero effect (stuck right at the seam) -
    # click_at_target should step back exactly once with a single clean
    # move (not decomposed into many small steps), then keep pushing
    # until it actually crosses.
    sink = StuckThenFreeSink(start_pos=(950, 500), blocked_move_count=1, move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(1490, 490, 20, 20),  # center (1500, 500) via origin (0, 0)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (0, 0),
        get_monitor_rects=lambda: _SIDE_BY_SIDE_MONITORS,
        sleep=lambda seconds: None,
        now=lambda: 0.0,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    # index 0: the stuck push (blocked, zero effect); index 1: the single
    # step-back move, retreating on the crossing axis - not many small ticks.
    assert move_commands[0].dx == cursor_module.DEFAULT_CROSSING_STEP_PX
    step_back = move_commands[1]
    assert step_back.dx == -cursor_module.DEFAULT_SAFE_MARGIN_PX
    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK  # eventually still lands and clicks


def test_click_at_target_skips_crossing_when_monitors_not_simply_adjacent():
    # Monitors overlap on neither axis (diagonally placed) - the
    # dedicated crossing has no single-axis direction to push in, so it
    # should back off and let the normal gain-adaptive loop attempt the
    # move directly instead of looping pointlessly.
    diagonal_monitors = [(0, 0, 1000, 1000), (2000, 2000, 3000, 3000)]
    sink = SimulatingSink(start_pos=(500, 500), move_ratio=1.0)

    click_at_target(
        hwnd=1,
        click_rect=(2490, 2490, 20, 20),  # center (2500, 2500) via origin (0, 0)
        sink=sink,
        get_cursor_pos=sink.get_pos,
        get_window_screen_origin=lambda hwnd: (0, 0),
        get_monitor_rects=lambda: diagonal_monitors,
        sleep=lambda seconds: None,
        now=lambda: 0.0,
    )

    move_commands = [c for c in sink.sent if c.action == wire.ACTION_MOUSE_MOVE]
    # No crossing_step_px-sized straight push - goes straight to the
    # normal probe-capped gain-adaptive request.
    assert abs(move_commands[0].dx) <= cursor_module.DEFAULT_INITIAL_PROBE_MAX_PX
    assert sink.sent[-1].action == wire.ACTION_MOUSE_CLICK


# -- _wait_for_settled_position (settle-by-polling, not a guessed delay) --

def test_wait_for_settled_position_waits_past_a_stale_intransit_reading():
    # First read (right after sending a move) is still mid-flight/stale;
    # it changes on the next poll, then repeats - that's what "settled"
    # means. A remaining, unconsumed later reading proves it stopped
    # polling as soon as it saw two consecutive matching values, rather
    # than reading some fixed number of times.
    readings = iter([(0, 0), (7, 7), (7, 7), (99, 99)])
    clock = FakeClock()

    result = _wait_for_settled_position(
        get_cursor_pos=lambda: next(readings),
        sleep=clock.sleep,
        now=clock.now,
        poll_interval_seconds=0.001,
        stable_reads=2,
        max_wait_seconds=1.0,
    )

    assert result == (7, 7)


def test_wait_for_settled_position_gives_up_after_max_wait():
    # Every read differs from the last - simulates a position that never
    # actually stabilizes. Must give up rather than hang forever.
    counter = iter(range(1000))
    clock = FakeClock()

    result = _wait_for_settled_position(
        get_cursor_pos=lambda: (next(counter), 0),
        sleep=clock.sleep,
        now=clock.now,
        poll_interval_seconds=0.05,
        stable_reads=2,
        max_wait_seconds=0.2,
    )

    assert isinstance(result, tuple)  # returned something instead of hanging/raising
