# Non-obvious NodeGraphQt gotchas (installed version: 0.6.44)

Carried over verbatim from VisionGraph (the reference sample this editor was
ported from) - these cost real debugging time there, don't rediscover them.

These cost real debugging time during development - don't rediscover them.

## `node.add_button()` crashes `serialize_session()`

It embeds a widget without a backing property, but `update_model()`
blindly calls `model.set_property()` for every embedded widget at
serialize time. Use `add_custom_widget()` directly instead (it creates the
property itself) - see `MacroBaseNode.add_pick_button()` for the pattern.

## Node ids are regenerated on every `deserialize_session()` call

Never persist a node id and expect to look it up after a reload. The
start-node designation is tracked via a hidden `is_start_node` custom
property on the node itself instead (survives reload naturally, since
custom properties round-trip through the session JSON) - see
`GraphWidget._find_start_node()`.

## The right-click canvas menu starts completely empty

`register_node()` only feeds the Tab-key search popup, not the context
menu - and if the menu has zero actions, NodeGraphQt shows nothing at all
instead of an empty menu. Commands must be added explicitly via
`graph.get_context_menu('graph').add_command(...)` (done in
`GraphWidget._build_context_menu()`).

## There is no default keybinding to delete a node

`NodeViewer.keyPressEvent` only tracks modifier-key state (for the
Shift/Ctrl/Alt overlay hints); it never handles `Delete`/`Backspace`.
Without wiring one up yourself, there's no way to remove a node from the
UI at all - not even via a right-click menu, since (per the gotcha above)
that starts empty too. `GraphWidget._build_delete_shortcut()` binds both
`Delete` and `Backspace` to deleting the current selection via
`QShortcut`, and `_build_context_menu()` additionally registers a
per-node-type `'Delete'` command on `graph.get_context_menu('nodes')`
(this menu requires a separate registration per node class - see
`NodesMenu.add_command`'s `node_class` parameter).

## Toggling a widget's visibility doesn't resize the node

NodeGraphQt only recalculates node size/layout when
`node.view.draw_node()` is explicitly called. `MacroBaseNode.set_field_visible()`
+ `.redraw()` is the pattern (see `ActionNode._update_field_visibility()`).
Without the `.redraw()` call, the node keeps its old size with dead blank
space where a hidden field used to be.

## `QKeySequenceEdit` has no `setMaximumSequenceLength()` in this Qt version

So it happily accumulates up to 4 chords (it's built for multi-shortcut
editing, like `Ctrl+K, Ctrl+S`). `_SingleChordKeySequenceEdit` in
`widgets.py` overrides `keyPressEvent` to clear the field before each new
keypress instead, so it only ever holds one combo.

## Simulated keyboard focus doesn't reliably reach widgets embedded via `QGraphicsProxyWidget`

That's anything added with `add_custom_widget()`, under `QTest`. Real user
clicks focus them fine in the actual app; for automated tests, exercise
the widget class standalone instead.

## `python -m pytest` used to segfault/hang in VisionGraph - root-caused to pytest's own `unraisableexception` plugin, now disabled

**Root cause found** (in VisionGraph; applies here too if a PyQt5 test suite
is ever added). It was never the project's code, NodeGraphQt, or even really
PyQt5/SIP reference counting - it's pytest's built-in `unraisableexception`
plugin. At session end it registers a cleanup that forces several rounds of
`gc.collect()` (`_pytest/unraisableexception.py`, `gc_collect_harder`), and
that GC pass can walk a SIP-wrapped Qt object whose underlying C++ object
has already been destroyed, crashing with a Windows access violation
(segfault). This is a known, actively-discussed upstream pytest issue when
PyQt/PySide objects are alive at session teardown - see
[pytest#7634](https://github.com/pytest-dev/pytest/issues/7634),
[pytest#14500](https://github.com/pytest-dev/pytest/issues/14500), and
[pytest#13333](https://github.com/pytest-dev/pytest/discussions/13333) -
not something specific to this codebase.

**Fix, if/when a PyQt5 test suite is added here:** set
`addopts = -p no:unraisableexception` in `pytest.ini`, so a plain
`python -m pytest` just works.

## `Qt` here means the `Qt.py` compatibility shim

Bundled with NodeGraphQt, and it resolves to PyQt5 in this environment.
`from Qt import QtCore` and `from PyQt5 import QtCore` are the same
classes here - both are used across the codebase interchangeably, that's
expected, not a mistake to "fix."

---

## Deviations from VisionGraph worth knowing

- **Target window is per-profile**, not a hardcoded constant. VisionGraph
  hardcodes `TARGET_WINDOW_TITLE`; here it's a toolbar field bound to the
  current profile, stored in `profile.json` (see `profile_store.py`) -
  since a graph's click/decision regions are meaningless against a
  different window's layout.
- **`ActionNode` gained `mouse_button`** (Left/Right/Middle), needed because
  a real HID mouse click has to name a physical button, not just a screen
  position.
- **No ported test suite yet.** VisionGraph has `tests/test_action_node.py`,
  `test_decision_node.py`, etc. - none of that has been ported here. This is
  a real, acknowledged gap, not an oversight to gloss over.
