# Deliberate design choices worth knowing before "fixing"

Carried over verbatim from VisionGraph (the reference sample this editor was
ported from) where still applicable, plus an addendum for what's genuinely
new in this repo.

## Dependencies

### PyQt5, not PyQt6/PySide6

The official `NodeGraphQt` package doesn't work under PyQt6 - confirmed
directly, not just by reputation: installing PyQt6 and forcing `Qt.py`
(NodeGraphQt's binding shim) onto it via `QT_PREFERRED_BINDING=PyQt6`
crashes on the very first `import NodeGraphQt`:

```
AttributeError: type object 'QGraphicsItem' has no attribute 'DeviceCoordinateCache'
```

(`NodeGraphQt/constants.py` uses old-style flat enum access -
`QGraphicsItem.DeviceCoordinateCache` - that PyQt6 requires scoped as
`QGraphicsItem.CacheMode.DeviceCoordinateCache`. `Qt.py`'s compatibility
layer doesn't backfill every such enum, so this particular one falls
through.) A PyQt6-compatible fork (`OdenGraphQt`) exists but was
intentionally not used, to stay on the actively-maintained upstream
package instead of a community fork.

## Decision node reference images & regions

### Decision nodes match a pre-processed reference image, not a live-cropped region

The user supplies a reference image with masking already baked in (a
0-alpha channel, or a single flat "ignore" color covering border and/or
interior areas). `process_masked_reference()` runs once at browse time -
detecting the mask, cropping to the bounding box of kept pixels, and
folding everything into the crop's own alpha channel - specifically so the
execution engine (`engine/matcher.py`) never has to redo that work per-frame
or scan more pixels than necessary. `reference_path` (the processed crop) is
what matching actually uses; `reference_full_path` (the untouched original)
is kept only so the node's thumbnail can pop up the full image on click.

### `DecisionNode`'s region needs no live matching to find

`region_x/y/w/h` need no separate picking step or live frame
capture/matching to find - `process_masked_reference()` already computes
that exact bounding box (position + size) when it crops the image down to
its content, since that's just where the kept pixels sit *within the
originally uploaded image*. The assumption this depends on: the user
uploads a full, unmodified screenshot of the target window (not an arbitrary
crop from elsewhere) with masking painted on top, so that position also is
where the content sits within the window right now.

## Overlays & click regions

### Click region is picked via an overlay on the live target window, not a captured preview panel

`ClickRegionOverlay` (`app/ui/overlays.py`) is a translucent,
always-on-top, frameless window positioned directly over the real target
window (found via win32 `FindWindowW`/`GetWindowRect`). The user drags a
rectangle straight on the actual target window (a plain click with no drag
reports a 1x1 region at that point); its window-relative bounds are
reported back and the overlay closes itself.

A click targets a *region*, not a single pixel - the execution engine's
`cursor.py` clicks its center, computed as a relative jog from wherever the
OS cursor currently is (real HID mice only report relative motion, there's
no absolute-position command over Raw HID).

`OverlayController` (`app/ui/overlay_controller.py`) is the only thing wired
to a node's "Pick..."/"Show..." buttons. Unlike VisionGraph, it takes a
`window_title_resolver` callable rather than a fixed string, since the
target window is per-profile here (see the addendum below).

## Graph structure & behavior

### Every node's `in` port accepts multiple incoming connections

`add_input('in', multi_input=True)` on `ActionNode`, `DecisionNode`, and
`WaitNode` - lets two different upstream branches converge into the same
next node, a normal macro shape (e.g. two different Decision outcomes both
leading to the same follow-up step).

### The graph allows cycles

`GraphWidget.__init__` calls `self.graph.set_acyclic(False)` - macros
routinely need loops (e.g. an Action retrying, then re-checking a Decision
it's downstream of). `load_session()` re-asserts `set_acyclic(False)` after
`deserialize_session()`, since that call restores whatever `acyclic` value
was serialized into the profile.

### Duplicating a node re-emits `node_created`, strips the start-node designation, and drops connections

`GraphWidget._duplicate_node()` wraps `graph.duplicate_nodes()` rather than
calling it directly, correcting for three things `duplicate_nodes()` does
on its own (doesn't emit `node_created`, would otherwise inherit the
original's start-node designation/color, and would come out wired into the
original's existing neighbors instead of as a clean standalone copy - see
the method's own docstring for the full explanation).

## App-level UX

### The last-open profile auto-loads on startup

Via `QSettings` - see `MainWindow._restore_last_profile()`.

### The canvas is disabled (`setEnabled(False)`) whenever no profile is open

There's nowhere to save changes to, so editing is blocked rather than
silently discarded.

---

## Addendum: what's genuinely new here, not in VisionGraph

VisionGraph is UI + data model only, with **no execution engine** - nothing
walks the graph or drives output. Everything below is new to this repo.

### Target window is per-profile, not a hardcoded constant

VisionGraph hardcodes `TARGET_WINDOW_TITLE`; here it's a toolbar field
(`MainWindow.target_window_edit`) bound to the current profile, stored in
`profile.json` alongside `session` (see `profile_store.py`) - a graph's
click/decision regions are meaningless against a different window's layout,
so the target window has to travel with the profile.

### `ActionNode` gained `mouse_button`

A real HID mouse click has to name a physical button (left/right/middle),
not just a screen position - VisionGraph's `ActionNode` had no concept of
this since it never drove real output.

### Run/Stop wires the editor directly to the execution engine - one process, not two

`MainWindow._start_macro()` translates the live NodeGraphQt graph into the
engine's plain-JSON schema (`_build_engine_graph()` - NodeGraphQt session
format was never meant to be the engine's input format, see the design
plan), resolves the target window's `HWND`, starts `engine.window_capture.WindowCapture`,
and starts an `engine.runner.MacroRunner` against it, using
`engine.command.HidCommandSink` wrapping the app's single open `HidLink`
Raw HID connection. `engine/` is a sibling of `app/` under `host/`, not
nested inside it or built as a separate program - deliberately, so the
whole thing runs as one process.

### `&ssm_tog` (the physical hardware toggle) drives the same Run/Stop control

`HidLink` (`app/hid_link.py`) owns the single open Raw HID device handle for
the whole app - a background thread watches for `&ssm_tog`'s stateless
trigger packets (marker `0x4E`, see `docs/wire-protocol.md`) and re-emits
them as a Qt signal on the GUI thread, wired to the exact same
`_on_run_clicked()` handler the toolbar's Run button calls. Firmware doesn't
track running/stopped at all (see the wire protocol doc) - this app is the
sole owner of that state, so pressing the physical key and clicking the
toolbar button are just two paths to the same toggle.

### A Decision node's region_x/y need a different window origin than click_x/y

`get_window_rect()` (`app/ui/overlays.py`) uses `GetWindowRect()` - the
window's outer frame, including an invisible resize-border margin DWM
adds around modern-themed windows on Windows 10/11 (confirmed ~7-8px per
side). `click_x/y` are authored and consumed against this origin
consistently end to end (`ClickRegionOverlay` picks against it,
`cursor.py`'s `get_window_screen_origin()` targets against it at runtime),
so clicks were never affected.

A Decision node's `region_x/y`, however, are measured directly within
whatever image the user uploaded (e.g. via Windows' Snipping Tool
window-capture mode) - confirmed against real hardware that this matches
DWM's extended frame bounds (`DWMWA_EXTENDED_FRAME_BOUNDS`), not
`GetWindowRect()`'s outer frame. `WindowsCapture` (this app's own live-
capture library, used for actual runtime matching) was also confirmed to
produce frames at this same extended-frame-bounds size - so runtime
matching itself was never wrong, only `overlay_controller.py`'s "Show
Region" preview for Decision nodes, which used the wrong origin. Fixed by
adding `get_window_extended_frame_bounds()` (app/ui/overlays.py) /
`get_window_extended_frame_origin()` (engine/cursor.py) alongside the
existing `GetWindowRect`-based functions, and switching only the
Decision-node preview path to the new one.

### Decision-node live overlay: two overlay classes, not one

`StaticReferenceOverlay` (pre-existing - the manual "Show Region" button)
and `LiveReferenceOverlay` (new - live feedback during Wait Until True
polling / confirmation mode, see `engine/runner.py`'s `_run_decision()`)
stayed separate rather than merging into one configurable class. Their
lifecycles differ enough that merging would mean threading a
static-snapshot-with-timer mode and a persists-until-explicitly-closed
mode through the same class: `StaticReferenceOverlay` draws once at 85%
opacity and auto-closes after `duration_ms`; `LiveReferenceOverlay`
draws at 50% opacity, is repainted many times via `update_score()` (once
per poll), never closes itself, and adds a label strip for the live match
percentage.
