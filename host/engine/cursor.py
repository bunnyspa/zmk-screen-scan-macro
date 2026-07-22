"""Click-targeting: moves the real cursor toward a window-relative click
rect, then clicks.

click_rect/region coordinates are relative to the window's outer frame
(GetWindowRect() - title bar and borders included), matching how the
editor's overlays (app/ui/overlays.py's get_window_rect()) and
WindowCapture (captures the whole window surface, chrome included) both
already treat them - see get_window_screen_origin(). Using the client-area
origin here instead was a real, confirmed bug: every click landed shifted
down and right by the title bar height and border width.

Real HID mice only report relative motion - there's no "move to absolute
pixel" over Raw HID. A single computed delta isn't enough on its own,
though: Windows' own pointer-acceleration curve reinterprets one relative
move differently depending on its magnitude (confirmed against real
hardware: a 170px request came back as an 863px actual movement - a
consistent ~5x multiplier, not noise). Confirmed from ZMK's own v0.3
source that zmk_hid_mouse_movement_set is a plain field assignment with no
clamping on the firmware side, so none of this is a firmware bug to fix
there.

Rather than trying to predict or defeat that curve (which would mean
changing global Windows mouse settings), click_at_target():
  - re-measures the cursor with GetCursorPos (read-only query, not
    injection) after each move, waiting for it to actually stop changing
    rather than guessing a fixed delay (see _wait_for_settled_position) -
    a too-short guessed delay would read a stale, still-in-flight position
    and misattribute a later move's effect to the wrong attempt;
  - tracks a per-axis "observed gain" (actual movement / requested move)
    from prior attempts and divides future requests by it, so a
    consistently-amplifying (or -damping) curve gets compensated for
    instead of re-triggering the same overshoot every time;
  - treats the very first attempt as a small calibration probe rather
    than a blind full-distance guess - with zero gain data yet, a large
    first request is exactly the one most likely to get amplified hardest
    by the acceleration curve and overshoot before there's any real
    measurement to correct with;
  - caps any single request's magnitude as a backstop against a
    wrong/noisy gain estimate flinging the cursor across the screen -
    scaling both axes down by the same factor (see _scale_to_cap)
    instead of clamping each axis independently, which would distort the
    request's direction whenever |dx| and |dy| differ a lot (confirmed
    against real hardware: a mostly-horizontal remaining delta got
    clamped into a 45-degree request, and once amplified, produced a
    large, entirely unnecessary swing on the axis that barely needed
    correcting at all).

Separately, if the target is on a *different* monitor than the cursor
currently is (checked proactively via real monitor geometry -
EnumDisplayMonitors, see monitors.py - not guessed), click_at_target()
crosses to that monitor first with a dedicated, simpler mechanism: push
straight in the single axis that leads toward the target monitor,
checking the real geometry after each move to confirm when the cursor has
actually landed on a different monitor. Confirmed against real hardware
that a cursor can get stuck exactly at a seam or at the concave point
where three monitors meet, immune to gain adaptation (a response that's
flatly zero at every magnitude tried isn't a curve problem) - if that
happens mid-crossing, it steps back once with a single clean diagonal
move to a safe point inside the current monitor's interior, then resumes
pushing straight. This dedicated crossing only ever engages when the
target is confirmed to be on another monitor - it's not run
unconditionally, and the step-back only fires reactively if the straight
push actually gets stuck, not as a mandatory preamble.

If it still hasn't converged within tolerance_px after max_iterations
corrective moves, it raises instead of clicking - a misclick is worse than
aborting the macro.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol as wire  # noqa: E402

from .command import Command, CommandSink  # noqa: E402
from .monitors import find_containing_monitor, list_monitor_rects  # noqa: E402

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 10
DEFAULT_TOLERANCE_PX = 2

# Settle detection: poll the cursor position instead of guessing a fixed
# delay, and treat it as settled once it stops changing. 2 quick reads a
# couple ms apart turned out not to be strong enough proof of "truly
# done" against real hardware - a still-resolving prior move could get
# misread as finished, and its leftover motion then misattributed to the
# next attempt's measurement. More reads, spaced further apart, make a
# false "settled" declaration far less likely.
DEFAULT_SETTLE_POLL_INTERVAL_SECONDS = 0.004
DEFAULT_SETTLE_STABLE_READS = 5
DEFAULT_SETTLE_MAX_WAIT_SECONDS = 0.2

# Gain adaptation: actual-movement/requested-movement ratio observed from
# the previous attempt, applied to shrink (or grow) the next request.
DEFAULT_GAIN_MIN = 0.2
DEFAULT_GAIN_MAX = 8.0
DEFAULT_GAIN_SMOOTHING = 0.5  # weight on the newest sample vs. the running estimate
DEFAULT_MAX_STEP_PX = 500  # backstop cap on any single request, regardless of gain

# The very first attempt has zero gain data yet (gain starts at 1.0, an
# untested assumption) - confirmed against real hardware that even a
# modest few-hundred-px blind first guess can get amplified 3-8x under
# Windows' pointer-acceleration curve, overshooting badly before there's
# any real data to correct with. Capping attempt 1 much smaller than the
# steady-state max_step_px turns it into a deliberate calibration probe -
# small enough that even a wildly wrong gain can't overshoot far - so
# attempt 2 onward starts from a real measured ratio instead of a guess.
DEFAULT_INITIAL_PROBE_MAX_PX = 80

# Cross-monitor crossing: a single-axis push toward whichever monitor the
# target is confirmed to be on (via real geometry), re-checked after each
# step - not many small ticks, not a blind diagonal jump.
DEFAULT_CROSSING_STEP_PX = 100
DEFAULT_MAX_CROSSING_ATTEMPTS = 20
# How far inside the current monitor's edges a reactive step-back should
# land, if the straight crossing push gets stuck.
DEFAULT_SAFE_MARGIN_PX = 100


class CursorConvergenceError(RuntimeError):
    """Raised when click_at_target can't land the cursor within tolerance
    after max_iterations corrective moves. Surfaced rather than clicking
    anyway - a misclick is worse than stopping the macro."""


class GainEstimate:
    """Persists the learned per-axis pointer-acceleration gain across
    multiple click_at_target() calls, so repeat clicks in the same run
    don't re-probe from scratch every time. Own one per macro run (see
    MacroRunner) and pass it to every click_at_target() call; omit it to
    get the old call-scoped behavior (always starts neutral)."""

    def __init__(self):
        self.x = 1.0
        self.y = 1.0


def get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def get_window_screen_origin(hwnd) -> tuple[int, int]:
    """Screen-space coordinates of the window's top-left corner - the
    outer frame (title bar + borders included), via GetWindowRect(). This
    has to match whatever coordinate system click_rect/region numbers were
    authored against: the editor's region-pick/highlight overlays
    (app/ui/overlays.py's get_window_rect()) and WindowCapture (which
    captures the whole window surface, chrome included, standard
    Windows Graphics Capture behavior for a capture-by-hwnd) both use this
    same outer-frame origin. Using ClientToScreen's client-area origin
    here instead (as this used to) shifts every click down and right by
    the title bar height and border width - confirmed against real
    hardware as the actual cause of a consistently-offset click."""
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top


def find_window(title: str):
    return _user32.FindWindowW(None, title)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _scale_to_cap(dx: float, dy: float, cap: float) -> tuple[float, float]:
    """Scales (dx, dy) down proportionally so neither component exceeds
    cap in magnitude, preserving direction - unlike clamping each axis to
    the same cap independently, which distorts direction whenever |dx|
    and |dy| differ substantially. Confirmed against real hardware: a
    remaining delta of (1526, 162) - mostly horizontal - got clamped to
    (80, 80), a 45-degree request; once amplified by the pointer curve,
    that produced a large, entirely unnecessary vertical swing that a
    proportionally-scaled (80, 8)-ish request wouldn't have caused."""
    largest = max(abs(dx), abs(dy))
    if largest <= cap or largest == 0:
        return dx, dy
    scale = cap / largest
    return dx * scale, dy * scale


def _nonzero_round(value: float) -> int:
    """Rounds to the nearest int, but never rounds a genuinely nonzero value
    down to 0 - a request that rounds away to nothing can't ever make
    progress, which would stall an axis forever at a fixed remaining
    distance instead of nudging it by at least 1px."""
    rounded = round(value)
    if rounded == 0 and value != 0:
        return 1 if value > 0 else -1
    return rounded


def _update_gain(previous_gain: float, requested: int, actual: int) -> float:
    """Blends the ratio observed from this attempt into the running gain
    estimate. Ignores attempts whose request was too small to measure a
    meaningful ratio from, rather than letting a near-zero denominator
    send the estimate to some huge/undefined value."""
    if abs(requested) < 1:
        return previous_gain
    raw_gain = _clamp(actual / requested, DEFAULT_GAIN_MIN, DEFAULT_GAIN_MAX)
    return DEFAULT_GAIN_SMOOTHING * raw_gain + (1 - DEFAULT_GAIN_SMOOTHING) * previous_gain


def _wait_for_settled_position(
    get_cursor_pos, sleep, now,
    poll_interval_seconds: float, stable_reads: int, max_wait_seconds: float,
) -> tuple[int, int]:
    """Polls get_cursor_pos() until it reports the same value for
    stable_reads consecutive reads (the move has actually landed), instead
    of trusting a fixed guessed delay - which, if too short, reads a stale,
    still-in-flight position and misattributes a later move's effect to
    the wrong attempt. Gives up after max_wait_seconds and returns
    whatever the last reading was, so a connection that never settles
    can't hang the macro forever."""
    deadline = now() + max_wait_seconds
    last = get_cursor_pos()
    matches = 1
    while matches < stable_reads:
        if now() >= deadline:
            break
        sleep(poll_interval_seconds)
        current = get_cursor_pos()
        if current == last:
            matches += 1
        else:
            matches = 1
            last = current
    return last


def _crossing_direction(current_monitor, target_monitor) -> tuple[int, int] | None:
    """Which single-axis direction moves from current_monitor toward
    target_monitor - (0, 1) down, (0, -1) up, (1, 0) right, (-1, 0) left -
    or None if they aren't simply adjacent along one axis (a case this
    doesn't try to solve; the normal gain-adaptive loop is left to
    attempt it directly instead). Requires genuine overlap on the
    perpendicular axis - two monitors placed diagonally from each other,
    with no shared row or column at all, aren't "adjacent" just because
    one happens to be below-and-right of the other."""
    cur_left, cur_top, cur_right, cur_bottom = current_monitor
    tgt_left, tgt_top, tgt_right, tgt_bottom = target_monitor

    horizontal_overlap = cur_left < tgt_right and tgt_left < cur_right
    vertical_overlap = cur_top < tgt_bottom and tgt_top < cur_bottom

    if horizontal_overlap:
        if tgt_top >= cur_bottom:
            return 0, 1
        if tgt_bottom <= cur_top:
            return 0, -1
    if vertical_overlap:
        if tgt_left >= cur_right:
            return 1, 0
        if tgt_right <= cur_left:
            return -1, 0
    return None


def _safe_axis_value(target_value: int, low_edge: int, high_edge: int, margin: int) -> int:
    """target_value clamped to stay margin px inside [low_edge, high_edge]
    - or the midpoint, if the range is narrower than 2*margin."""
    lo, hi = low_edge + margin, high_edge - margin
    if lo > hi:
        return (low_edge + high_edge) // 2
    return int(_clamp(target_value, lo, hi))


def _cross_to_target_monitor(
    cur_x: int, cur_y: int, target_x: int, target_y: int,
    current_monitor, target_monitor, sink: CommandSink,
    get_cursor_pos, sleep, now,
    settle_poll_interval_seconds: float, settle_stable_reads: int, settle_max_wait_seconds: float,
    monitor_rects, crossing_step_px: int, max_crossing_attempts: int, safe_margin_px: int,
) -> tuple[int, int]:
    """Pushes straight, one axis at a time, from the monitor the cursor is
    currently on toward the one the target is confirmed to be on -
    re-checking the real geometry after each step rather than counting a
    fixed number of moves. If a push has zero effect (stuck, e.g. right at
    a seam or a concave multi-monitor corner), steps back once with a
    single clean diagonal move to a safe point inside the current
    monitor's interior, then resumes. Returns wherever the cursor ended up
    - not guaranteed to have actually crossed if the arrangement or the
    stuck condition defeats it; the normal gain-adaptive loop takes over
    from there regardless."""
    direction = _crossing_direction(current_monitor, target_monitor)
    if direction is None:
        logger.info("click_at_target: current and target monitors aren't simply "
                    "adjacent along one axis - skipping the dedicated crossing")
        return cur_x, cur_y

    dir_x, dir_y = direction
    stepped_back = False

    for attempt in range(1, max_crossing_attempts + 1):
        sink.send(Command(action=wire.ACTION_MOUSE_MOVE,
                          dx=dir_x * crossing_step_px, dy=dir_y * crossing_step_px))
        new_x, new_y = _wait_for_settled_position(
            get_cursor_pos, sleep, now, settle_poll_interval_seconds,
            settle_stable_reads, settle_max_wait_seconds,
        )
        moved = new_x != cur_x or new_y != cur_y
        cur_x, cur_y = new_x, new_y
        new_monitor = find_containing_monitor(cur_x, cur_y, monitor_rects)
        logger.info("click_at_target: crossing attempt %d/%d, now at (%d, %d)",
                    attempt, max_crossing_attempts, cur_x, cur_y)

        if new_monitor is not None and new_monitor != current_monitor:
            logger.info("click_at_target: crossed into a new monitor after %d attempt(s)",
                        attempt)
            return cur_x, cur_y

        if not moved and not stepped_back:
            # Retreat on the crossing axis itself (away from the seam
            # just pushed into) - a plain "stuck exactly at the seam"
            # case needs this to make any progress at all - and clamp the
            # perpendicular axis into a safe interior zone, which is what
            # additionally helps when stuck at a concave multi-monitor
            # corner rather than a plain seam.
            if dir_x != 0:
                waypoint_x = cur_x - dir_x * safe_margin_px
                waypoint_y = _safe_axis_value(
                    target_y, current_monitor[1], current_monitor[3], safe_margin_px,
                )
            else:
                waypoint_y = cur_y - dir_y * safe_margin_px
                waypoint_x = _safe_axis_value(
                    target_x, current_monitor[0], current_monitor[2], safe_margin_px,
                )
            step_back_dx, step_back_dy = waypoint_x - cur_x, waypoint_y - cur_y
            if step_back_dx != 0 or step_back_dy != 0:
                logger.info("click_at_target: crossing push got stuck at (%d, %d) - "
                            "stepping back once to a safe interior point (%d, %d) "
                            "(single diagonal move)", cur_x, cur_y, waypoint_x, waypoint_y)
                sink.send(Command(action=wire.ACTION_MOUSE_MOVE, dx=step_back_dx, dy=step_back_dy))
                cur_x, cur_y = _wait_for_settled_position(
                    get_cursor_pos, sleep, now, settle_poll_interval_seconds,
                    settle_stable_reads, settle_max_wait_seconds,
                )
                logger.info("click_at_target: after step-back, now at (%d, %d)", cur_x, cur_y)
            stepped_back = True

    logger.info("click_at_target: did not detect crossing into a new monitor after "
                "%d attempts - continuing with normal movement anyway", max_crossing_attempts)
    return cur_x, cur_y


def click_at_target(
    hwnd,
    click_rect: tuple[int, int, int, int],
    sink: CommandSink,
    mouse_button: int = wire.MOUSE_BUTTON_LEFT,
    get_cursor_pos=get_cursor_pos,
    get_window_screen_origin=get_window_screen_origin,
    sleep=time.sleep,
    now=time.monotonic,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    tolerance_px: int = DEFAULT_TOLERANCE_PX,
    settle_poll_interval_seconds: float = DEFAULT_SETTLE_POLL_INTERVAL_SECONDS,
    settle_stable_reads: int = DEFAULT_SETTLE_STABLE_READS,
    settle_max_wait_seconds: float = DEFAULT_SETTLE_MAX_WAIT_SECONDS,
    max_step_px: int = DEFAULT_MAX_STEP_PX,
    initial_probe_max_px: int = DEFAULT_INITIAL_PROBE_MAX_PX,
    get_monitor_rects=list_monitor_rects,
    crossing_step_px: int = DEFAULT_CROSSING_STEP_PX,
    max_crossing_attempts: int = DEFAULT_MAX_CROSSING_ATTEMPTS,
    safe_margin_px: int = DEFAULT_SAFE_MARGIN_PX,
    gain_estimate: GainEstimate | None = None,
) -> None:
    """Moves the cursor toward click_rect's center (window-relative),
    re-measuring after each move and adapting to whatever the actual
    movement/requested-movement ratio turns out to be, then clicks.
    get_cursor_pos/get_window_screen_origin/sleep/now are injectable for
    testing without touching real win32 calls or a real clock.

    Pass a GainEstimate owned by the caller (see MacroRunner) to carry
    the learned gain across multiple clicks in the same run, so repeat
    clicks don't re-probe from scratch every time - omit it to always
    start neutral (a fresh GainEstimate, discarded after this call).

    If the target is confirmed (via real monitor geometry) to be on a
    different monitor than the cursor currently is, crosses to it first -
    see _cross_to_target_monitor().

    Raises CursorConvergenceError instead of clicking if the cursor still
    isn't within tolerance_px after max_iterations corrective moves."""
    x, y, w, h = click_rect
    target_client_x = x + w // 2
    target_client_y = y + h // 2

    origin_x, origin_y = get_window_screen_origin(hwnd)
    target_x = origin_x + target_client_x
    target_y = origin_y + target_client_y

    cur_x, cur_y = get_cursor_pos()

    monitor_rects = get_monitor_rects()
    target_monitor = find_containing_monitor(target_x, target_y, monitor_rects)

    if gain_estimate is None:
        gain_estimate = GainEstimate()
        has_prior_gain_data = False
    else:
        has_prior_gain_data = gain_estimate.x != 1.0 or gain_estimate.y != 1.0
    gain_x = gain_estimate.x
    gain_y = gain_estimate.y
    just_crossed = False

    for attempt in range(1, max_iterations + 1):
        # Checked every attempt, not just once up front: gain adaptation's
        # own amplification overshoot can wander the cursor off the target
        # monitor entirely mid-approach (confirmed against real hardware -
        # a crossing that succeeded initially, then a later gain-adaptive
        # move overshot back onto a third monitor and got stuck there,
        # with no recovery, since the one-time upfront check never got a
        # chance to run again). Re-cross whenever this happens, wherever
        # it happens in the loop.
        if target_monitor is not None:
            current_monitor = find_containing_monitor(cur_x, cur_y, monitor_rects)
            if current_monitor is not None and current_monitor != target_monitor:
                logger.info("click_at_target: at (%d, %d), not on the target's monitor - "
                            "crossing before continuing", cur_x, cur_y)
                cur_x, cur_y = _cross_to_target_monitor(
                    cur_x, cur_y, target_x, target_y, current_monitor, target_monitor, sink,
                    get_cursor_pos, sleep, now, settle_poll_interval_seconds,
                    settle_stable_reads, settle_max_wait_seconds, monitor_rects,
                    crossing_step_px, max_crossing_attempts, safe_margin_px,
                )
                # New territory - the gain estimate from wherever we were
                # before doesn't necessarily apply here, and neither does
                # a full-magnitude request (same reasoning as attempt 1).
                gain_x = 1.0
                gain_y = 1.0
                gain_estimate.x, gain_estimate.y = gain_x, gain_y
                just_crossed = True

        dx = target_x - cur_x
        dy = target_y - cur_y
        if abs(dx) <= tolerance_px and abs(dy) <= tolerance_px:
            logger.info("click_at_target: converged after %d move(s), at (%d, %d), "
                        "remaining delta (%d, %d)", attempt - 1, cur_x, cur_y, dx, dy)
            sink.send(Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=mouse_button))
            return

        # Attempt 1 has no gain data yet - treat it as a small calibration
        # probe rather than a blind full-distance guess (see
        # DEFAULT_INITIAL_PROBE_MAX_PX above) - unless a caller-owned
        # GainEstimate already has real data from an earlier click in this
        # run, in which case trust it instead of re-probing. Same
        # reasoning applies right after a (re-)crossing, regardless of
        # prior data, since it's new territory either way.
        if just_crossed or (attempt == 1 and not has_prior_gain_data):
            step_cap = initial_probe_max_px
            just_crossed = False
        else:
            step_cap = max_step_px
        need_x = abs(dx) > tolerance_px
        need_y = abs(dy) > tolerance_px
        scaled_dx, scaled_dy = _scale_to_cap(dx / gain_x, dy / gain_y, step_cap)
        request_dx = _nonzero_round(scaled_dx) if need_x else 0
        request_dy = _nonzero_round(scaled_dy) if need_y else 0

        logger.info("click_at_target: attempt %d/%d, at (%d, %d), remaining (%d, %d), "
                    "requesting move (%d, %d) [gain estimate (%.2f, %.2f)] toward (%d, %d)",
                    attempt, max_iterations, cur_x, cur_y, dx, dy, request_dx, request_dy,
                    gain_x, gain_y, target_x, target_y)
        sink.send(Command(action=wire.ACTION_MOUSE_MOVE, dx=request_dx, dy=request_dy))

        new_x, new_y = _wait_for_settled_position(
            get_cursor_pos, sleep, now, settle_poll_interval_seconds,
            settle_stable_reads, settle_max_wait_seconds,
        )
        actual_dx = new_x - cur_x
        actual_dy = new_y - cur_y
        logger.info("click_at_target: attempt %d/%d, requested (%d, %d), actual movement "
                    "(%d, %d), now at (%d, %d)", attempt, max_iterations, request_dx,
                    request_dy, actual_dx, actual_dy, new_x, new_y)

        gain_x = _update_gain(gain_x, request_dx, actual_dx)
        gain_y = _update_gain(gain_y, request_dy, actual_dy)
        gain_estimate.x, gain_estimate.y = gain_x, gain_y
        cur_x, cur_y = new_x, new_y

        # Checked again immediately, not just at the top of the next
        # iteration - a move can land exactly on target on the very last
        # allowed attempt, with no further iteration left to notice it.
        if abs(target_x - cur_x) <= tolerance_px and abs(target_y - cur_y) <= tolerance_px:
            logger.info("click_at_target: converged after %d move(s), at (%d, %d)",
                        attempt, cur_x, cur_y)
            sink.send(Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=mouse_button))
            return

    dx = target_x - cur_x
    dy = target_y - cur_y
    logger.error("click_at_target: failed to converge after %d attempts, final position "
                 "(%d, %d), remaining delta (%d, %d) - not clicking", max_iterations,
                 cur_x, cur_y, dx, dy)
    raise CursorConvergenceError(
        f"cursor did not converge on target ({target_x}, {target_y}) after "
        f"{max_iterations} attempts; landed at ({cur_x}, {cur_y}), remaining delta ({dx}, {dy})"
    )
