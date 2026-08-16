/* Graph editor built on Drawflow (vendored at vendor/drawflow/) - Rete.js/
 * Baklava.js were ruled out since both require React/Vue + a bundler,
 * which this app deliberately has none of.
 *
 * Owns everything about *editing* the graph - node/port/connection
 * rendering, property fields, the exclusive "start node" flag, and
 * packaging/unpacking the GraphDocument JSON (index.html owns save/load
 * plumbing and calls the two exported functions below).
 *
 * Nodes render minimally on the canvas - one text element, no property
 * fields inline, no per-node buttons at all - so the graph stays readable
 * at a glance even with many nodes on screen. Two size/content modes,
 * picked by the IS_MOBILE_LAYOUT constant below: desktop shows a node's
 * full display text (nodeDisplayText() - its own title if set, else the
 * type's fixed label, e.g. "Action"); mobile shows just that text's
 * first character, since there's no room for more.
 *
 * IS_MOBILE_LAYOUT is a hardcoded constant, NOT derived from the
 * window/canvas's live rendered width - it used to be (an
 * applyResponsiveMode() + ResizeObserver pair flipped it as the pywebview
 * window was resized), but that meant a wide desktop window in "mobile"
 * mode was reachable, which never actually happens in practice: this
 * exact file is the one meant to be reused as-is in an Android WebView
 * later (see android-screen-scan-macro's architecture.md) - each
 * deployment is one environment, permanently, never a runtime choice a
 * user makes by resizing something. This copy (zmk-screen-scan-macro's
 * desktop app) hardcodes it `false`; the eventual Android copy hardcodes
 * it `true` - a one-line edit at that point, not a config UI.
 *
 * Node press behavior: a short press opens the edit popup directly, on
 * both layouts - same as tapping a file opens it in every mobile file
 * browser (an earlier "tap only selects, a separate button opens" design
 * put mobile's primary action behind an extra step for no real benefit;
 * reverted). Multi-select has two *separate* entry gestures, one per
 * layout, deliberately not shared code paths - long-press isn't a native
 * desktop gesture (holding a mouse button for half a second reads as
 * "stuck," not "select"), and Ctrl/Shift-click isn't available on touch
 * at all:
 * - Mobile: a *long* press (LONG_PRESS_MS, timed by hand via
 *   initGraphEditor()'s pointerdown/pointermove/pointerup trio - not
 *   Drawflow's own node_selected/nodeSelected, which fires immediately
 *   on any press regardless of duration and so can't distinguish long
 *   from short) selects that one node (selectedNodeIds, selectNode()).
 *   Deliberately not double-tap for this: touchscreen double-tap is a
 *   documented pain point in at least one comparable node editor (n8n's
 *   mobile web UI). Once a selection is active on mobile, any further
 *   short press on *any* node (toggleNodeSelection()) adds or removes
 *   it, no modifier needed (touch has none).
 * - Desktop: Ctrl+click or Shift+click a node (checked at pointerdown,
 *   carried through nodeClickCandidate.modifierHeld to the paired
 *   pointerup) toggles it into/out of the selection directly - the
 *   standard desktop multi-select gesture (Explorer, Finder, etc.). A
 *   plain click while a desktop selection is active does NOT toggle -
 *   it opens that node directly and drops the existing selection first,
 *   matching how a plain click elsewhere collapses a multi-select back
 *   to one item in those same native file managers.
 *
 * This click/long-press/drag detection trio is built on Pointer Events
 * (pointerdown/pointermove/pointerup), not mouse events - found the hard
 * way, not designed in up front. An earlier mousedown/mousemove/mouseup
 * version worked perfectly on desktop (this app's only tested platform
 * at the time) but silently couldn't work on a real Android WebView:
 * mobile browsers don't dispatch a synthetic `mousedown` until *after*
 * `touchend` fires, i.e. only once the finger has already lifted - so a
 * long-press timer armed from `mousedown` could only ever start counting
 * after the press was already over, making LONG_PRESS_MS's whole
 * before-release detection impossible on touch (confirmed on a real
 * device: long-pressing a node produced only Android's own native
 * long-press haptic, never the CAB - see android-screen-scan-macro's
 * docs/status.md for the on-device test that found this). Pointer Events
 * fire promptly and identically for mouse, touch, and pen - switching to
 * them fixes Android without changing desktop's behavior at all (a real
 * mouse's pointerdown/pointerup fire at the same moments its
 * mousedown/mouseup would have). This is *why* IS_MOBILE_LAYOUT can stay
 * a single shared file with one flipped constant, despite that Android
 * bug: the constant was always meant to capture genuine environment
 * differences (sizing, text truncation), not gesture-detection
 * plumbing - the plumbing just needed to stop being accidentally
 * mouse-only.
 *
 * #node-action-cab, the small floating contextual action bar shown
 * whenever selectedNodeIds is non-empty - Android's own "Contextual
 * Action Bar" pattern, rehearsed here for both entry gestures above, not
 * just the mobile one it's named after - is NOT layout-gated the way
 * node sizing is: a desktop Ctrl-click selection needs it to show just
 * as much as a mobile long-press one does. Its Edit and Set as Start
 * Node are only enabled for a single-node selection (both need exactly
 * one target); multi-select has no action of its own yet beyond that
 * disabling - a planned future "group selected nodes" action is the
 * reason multi-select exists at all right now, not built today. A
 * separate visual highlight (.multi-selected,
 * applyNodeSelectionHighlight()) marks every selected node, since
 * Drawflow's own native single-node 'selected' class (this.node_selected)
 * can only ever mark one element and so can't represent a
 * multi-selection on its own.
 *
 * The edit popup itself is one shared #node-editor-modal, populated with
 * that node's full property form, including Delete Node and the
 * free-text Title field; fields there are synced into node.data manually
 * (editingNodeId + input listeners on the modal), NOT via Drawflow's own
 * df-* auto-binding, since that binding only works for fields that are
 * actual descendants of the node's own DOM element (see Drawflow's
 * updateNodeValue()) - the df-* attribute naming is kept anyway, purely
 * so each field's data key is still readable straight from its markup.
 *
 * There's no icon *picker* - a title's own first character standing in
 * for a custom icon at mobile width (a user who wants a specific glyph
 * types it as the first character of the title, e.g. an emoji) was a
 * deliberate simplification over building a real emoji-picker widget -
 * one fewer piece of UI, and the title field was going to exist
 * regardless.
 *
 * The start node is no longer chosen by a per-node star toggle - a
 * single "Set Start Node" toolbar button (index.html) arms a one-shot
 * "next node you click becomes the start node" mode (pickingStartNode
 * below), read by the same mousedown/mouseup click-detection pair that
 * opens the edit popup.
 *
 * Title data itself is expected to still exist once this editor is
 * ported to Android (it drives the mobile-layout glyph there too) - only
 * the *full-text* desktop display is a Windows-only affordance, since
 * Android will hardcode IS_MOBILE_LAYOUT true and never render it.
 *
 * Four node types: Action (click or key press), Wait (fixed delay),
 * Branch (check a reference image once, true/false), and Branch (Wait)
 * (poll until a reference image matches, single progression path - no
 * false port at all, since there's nothing to fall through to). Branch
 * and Branch (Wait) were one "Decision" node type with an evaluation_mode
 * dropdown until they were split, so a node's port shape (specifically,
 * whether a trailing false port exists) is a fixed property of its type
 * instead of something that changes with a mutable per-instance mode.
 *
 * Branch/Branch (Wait)'s `images` list and per-image output ports
 * ("1".."N", + "false" for Branch only) need real bridge round-trips:
 * image upload/masking is OpenCV work that only exists in Python, and the
 * port-rewiring algorithm on add/delete/move is branch_images.py -
 * already unit-tested in Python, so it's called through the bridge
 * rather than re-implemented untested here. The image-management UI is a
 * single shared modal (#image-editor-modal in index.html), repointed at
 * whichever node is open at a time (editingBranchNodeId) rather than one
 * dialog instance per node - this app only ever has one such dialog open
 * at once anyway. Its "Edit Images..." button now lives inside
 * #node-editor-modal instead of the node itself, so it's opened via
 * editingNodeId rather than the old event-delegation-from-node-DOM path.
 */

// This string value must match graph_translation.py's _ACTION_KEY_PRESS
// exactly - it's stored verbatim in properties.action_type and read back
// by build_engine_graph_from_document() when translating a GraphDocument
// to the engine schema. Kept in sync by convention, not by import (no
// Qt-importing code in this file).
const ACTION_TYPE_CLICK = 'Click';
const ACTION_TYPE_KEY_PRESS = 'Key Press';
const MODIFIER_KEYS = ['Control', 'Alt', 'Shift', 'Meta'];

const NODE_TYPE_LABELS = { action: 'Action', wait: 'Wait', branch: 'Branch', branch_wait: 'Branch (Wait)' };

// Hardcoded per deployment, not derived from window/canvas width at
// runtime - see this file's header comment for why. This copy (the
// desktop app) is always `false`; change to `true` only in the eventual
// Android WebView copy of this same file, never conditionally at
// runtime here.
const IS_MOBILE_LAYOUT = false;

// Android's own long-press threshold (ViewConfiguration.getLongPressTimeout())
// defaults to 500ms - matched here rather than picked arbitrarily, since
// this is deliberately rehearsing that platform's touch conventions (see
// this file's header comment). CLICK_MOVE_THRESHOLD_PX has only been
// confirmed workable on a real touchscreen at this value, not proven
// optimal - a looser threshold may still turn out to feel better for
// finger jitter than a mouse ever exercises, just not yet tested.
const LONG_PRESS_MS = 500;
const CLICK_MOVE_THRESHOLD_PX = 4;

let editor = null;
let startNodeId = null;
let nextSpawnOffset = 0;
let editingBranchNodeId = null;
let editingNodeId = null; // node currently open in #node-editor-modal, or null
let nodeClickCandidate = null; // {nodeId, x, y, modifierHeld} - see initGraphEditor()'s pointerdown/pointermove/pointerup trio
let longPressTimer = null; // setTimeout handle for the pending long-press CAB, or null - see initGraphEditor()
let longPressFired = false; // true once the timer above has fired for the current press, so pointerup knows not to also treat it as a short click
let pickingStartNode = false; // armed by the "Set Start Node" toolbar button - see initSetStartNodeButton()
// Node ids currently selected - entered via a mobile long press
// (selectNode(), exactly one member) or a desktop Ctrl/Shift-click
// (toggleNodeSelection() directly, see this file's header comment for
// why the two layouts use separate entry gestures), then grown/shrunk
// from there (mobile: any further short press; desktop: further
// modifier-clicks). #node-action-cab is visible whenever this is
// non-empty, on either layout; Edit/Set as Start are only enabled when
// it holds exactly one id - see updateNodeActionCab(). Multi-select
// itself has no action of its own yet beyond disabling those two
// (grouping is a planned future use, not built today).
let selectedNodeIds = new Set();
let imageThumbnailUrls = {}; // reference_path -> data: URI, display-only, never saved
let notifyDirty = function () {}; // set to initGraphEditor()'s onDirty param; module-level since #node-editor-modal's field listener isn't in that function's closure

// HTML-escapes free text (the title field) before it's dropped into an
// innerHTML string - node titles are user-authored and otherwise not
// trusted content, this is not just cosmetic.
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// A node's full display text: its own title if set, otherwise the type's
// fixed label ("Action", "Wait", ...) - shown in full at desktop width,
// reduced to just its first character at mobile width (see
// renderMiniNodeHtml()). Array.from(), not charAt/[0], so a single emoji
// made of a surrogate pair - most common ones - survives as one whole
// character at mobile width instead of a broken half-character.
//
// Note this means Branch and Branch (Wait) share the same first letter
// ("B") when neither has a title set, so they're not visually
// distinguishable from each other at mobile width alone - an accepted
// rough edge of using the type label as the fallback, not something this
// pass fixes; a real title sidesteps it.
function nodeDisplayText(data, type) {
  const title = ((data && data.title) || '').trim();
  return title.length > 0 ? title : NODE_TYPE_LABELS[type];
}

function defaultActionProperties() {
  return {
    action_type: ACTION_TYPE_CLICK,
    click_x: 0, click_y: 0, click_w: 1, click_h: 1,
    mouse_button: 'Left',
    key_combo: '',
  };
}

function defaultWaitProperties() {
  return { duration_ms: 1000 };
}

function defaultBranchProperties() {
  return { images: [], match_threshold: 0.85 };
}

function defaultBranchWaitProperties() {
  return { images: [], match_threshold: 0.85, poll_interval_ms: 200 };
}

// Minimal on-canvas body for every node type: one text element, no
// buttons. At desktop width it shows the node's full display text
// (nodeDisplayText()); at mobile width, just that text's first
// character, since there's no room for more. Property editing (title
// included, and Delete Node) happens in the popup - see
// renderEditFormHtml() and openNodeEditor(). Also used by
// updateMiniNodeDisplay() to re-render a node's body in place after its
// title changes, so this must stay a pure function of (type, data).
function renderMiniNodeHtml(type, data) {
  const text = nodeDisplayText(data, type);
  const shown = IS_MOBILE_LAYOUT ? Array.from(text)[0] : text;
  return '<div class="node-label" title="' + escapeHtml(NODE_TYPE_LABELS[type]) + ' - click to edit">' + escapeHtml(shown) + '</div>';
}

// Re-renders a node's on-canvas body in place - called after its title
// changes in the edit popup, since the title drives the displayed text.
// Only touches .drawflow_content_node (Drawflow's own wrapper for the
// html passed to addNode()), not the whole node element, so the sibling
// .branch-thumb-overlay (appended directly to the node, not inside that
// wrapper - see ensureBranchThumbOverlay()) is untouched.
function updateMiniNodeDisplay(nodeId) {
  const nodeEl = document.getElementById('node-' + nodeId);
  if (!nodeEl) return;
  const node = editor.getNodeFromId(nodeId);
  const contentEl = nodeEl.querySelector('.drawflow_content_node');
  if (contentEl) contentEl.innerHTML = renderMiniNodeHtml(node.name, node.data);
}

function renderActionEditFormHtml() {
  return (
    '<label>Type' +
      '<select df-action_type>' +
        '<option value="' + ACTION_TYPE_CLICK + '">Click</option>' +
        '<option value="' + ACTION_TYPE_KEY_PRESS + '">Key Press</option>' +
      '</select>' +
    '</label>' +
    '<div class="action-click-fields">' +
      '<label>X <input type="number" df-click_x></label>' +
      '<label>Y <input type="number" df-click_y></label>' +
      '<label>W <input type="number" df-click_w min="1"></label>' +
      '<label>H <input type="number" df-click_h min="1"></label>' +
      '<label>Button' +
        '<select df-mouse_button>' +
          '<option value="Left">Left</option>' +
          '<option value="Right">Right</option>' +
          '<option value="Middle">Middle</option>' +
        '</select>' +
      '</label>' +
      '<button type="button" class="pick-click-region-btn">Pick Click Region</button>' +
      '<button type="button" class="show-click-region-btn">Show Region in Window</button>' +
    '</div>' +
    '<div class="action-key-fields">' +
      '<label>Key / Combo' +
        '<input type="text" df-key_combo class="key-capture-input" readonly placeholder="Click, then press keys">' +
      '</label>' +
    '</div>'
  );
}

function renderWaitEditFormHtml() {
  return '<label>Duration (ms) <input type="number" df-duration_ms min="0"></label>';
}

function renderBranchEditFormHtml() {
  return (
    '<label>Match Threshold <input type="number" df-match_threshold min="0" max="1" step="0.01"></label>' +
    '<button type="button" class="edit-images-btn" style="margin-top: 6px;">Edit Images...</button>'
  );
}

function renderBranchWaitEditFormHtml() {
  return (
    '<label>Match Threshold <input type="number" df-match_threshold min="0" max="1" step="0.01"></label>' +
    '<label>Poll Interval (ms) <input type="number" df-poll_interval_ms min="10"></label>' +
    '<button type="button" class="edit-images-btn" style="margin-top: 6px;">Edit Images...</button>'
  );
}

// Title comes first and is shared by every node type - it drives the
// node's on-canvas display text at every width (see nodeDisplayText()
// and this file's header comment).
function renderTitleFieldHtml() {
  return '<label>Title <input type="text" df-title placeholder="Optional - shown in full at desktop width, first character only at mobile width"></label>';
}

function renderEditFormHtml(type) {
  const typeFields = type === 'wait' ? renderWaitEditFormHtml()
    : type === 'branch' ? renderBranchEditFormHtml()
    : type === 'branch_wait' ? renderBranchWaitEditFormHtml()
    : renderActionEditFormHtml();
  return renderTitleFieldHtml() + typeFields;
}

function nextSpawnPosition() {
  const pos = { x: 80 + (nextSpawnOffset % 5) * 60, y: 60 + (nextSpawnOffset % 5) * 50 };
  nextSpawnOffset += 1;
  return pos;
}

function updateActionFieldVisibility(nodeEl) {
  const select = nodeEl.querySelector('[df-action_type]');
  if (!select) return;
  const isClick = select.value === ACTION_TYPE_CLICK;
  const clickFields = nodeEl.querySelector('.action-click-fields');
  const keyFields = nodeEl.querySelector('.action-key-fields');
  if (clickFields) clickFields.style.display = isClick ? '' : 'none';
  if (keyFields) keyFields.style.display = isClick ? 'none' : '';
}

const BRANCH_THUMB_GAP = 4; // matches .branch-node-thumb's width in index.html's CSS

function ensureBranchThumbOverlay(nodeEl) {
  let overlay = nodeEl.querySelector(':scope > .branch-thumb-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'branch-thumb-overlay';
    nodeEl.appendChild(overlay);
  }
  return overlay;
}

// Positions one small thumbnail directly beside each image's corresponding
// output port circle ("false", on a Branch node, gets none), measured from
// the ports' actual rendered position (getBoundingClientRect()) rather than
// derived from Drawflow's internal layout math (the .outputs column is
// centered as a whole block within the node's height, not flush to its top
// - not worth reverse-engineering when the real position is one
// measurement away). Dividing by editor.zoom keeps this aligned at any
// zoom level, since rect measurements come back in already-scaled screen
// pixels but the thumbnails are positioned in the same unscaled coordinate
// space as the node itself (both live inside Drawflow's zoom/pan
// transform, so a raw scaled pixel delta would double-apply the zoom
// otherwise). Shared by both Branch and Branch (Wait).
//
// Placed just past the *right* edge of each port (not to its left, inside
// the node) - the minimized node is only wide enough for its icon, with no
// spare room for a thumbnail column inside it the way the old full-size
// node had. The overlay div itself has no overflow:hidden (neither here
// nor on Drawflow's own .drawflow-node, confirmed by reading
// vendor/drawflow/drawflow.min.css), so positioning outside the node's own
// box still renders correctly.
function renderBranchNodeImageList(nodeId) {
  const nodeEl = document.getElementById('node-' + nodeId);
  if (!nodeEl) return;
  const overlay = ensureBranchThumbOverlay(nodeEl);
  const images = branchImagesOf(nodeId);
  const outputEls = nodeEl.querySelectorAll('.outputs .output');
  const nodeRect = nodeEl.getBoundingClientRect();
  const zoom = editor.zoom || 1;

  overlay.innerHTML = images.map(function (image, index) {
    const portEl = outputEls[index];
    if (!portEl) return '';
    const portRect = portEl.getBoundingClientRect();
    const top = (portRect.top - nodeRect.top) / zoom;
    const left = (portRect.right - nodeRect.left) / zoom + BRANCH_THUMB_GAP;
    const thumbUrl = imageThumbnailUrls[image.reference_path] || '';
    return '<img class="branch-node-thumb" style="top: ' + top + 'px; left: ' + left + 'px;" ' +
      'src="' + thumbUrl + '" alt="" title="Image #' + (index + 1) + '">';
  }).join('');
}

function applyStartHighlight() {
  document.querySelectorAll('.drawflow-node').forEach(function (el) {
    el.classList.toggle('is-start-node', startNodeId != null && el.id === 'node-' + startNodeId);
  });
}

function setStartNode(nodeId) {
  startNodeId = nodeId;
  applyStartHighlight();
  notifyDirty();
}

function keydownToCombo(event) {
  const parts = [];
  if (event.ctrlKey) parts.push('Ctrl');
  if (event.altKey) parts.push('Alt');
  if (event.shiftKey) parts.push('Shift');
  if (event.metaKey) parts.push('Meta');
  if (MODIFIER_KEYS.indexOf(event.key) === -1) {
    parts.push(event.key.length === 1 ? event.key.toUpperCase() : event.key);
  }
  return parts.join('+');
}

function initGraphEditor(containerEl, onDirty) {
  editor = new Drawflow(containerEl);
  editor.reroute = true;
  editor.start();
  notifyDirty = onDirty;

  // Drawflow's own two-finger pinch handling (pointerdown_handler/
  // pointermove_handler in vendor/drawflow/drawflow.min.js, confirmed by
  // reading its source - no config option exposed for any of this) is
  // disabled entirely on mobile, in favor of the WebView's own native
  // pinch-zoom (GraphEditorActivity.kt's setBuiltInZoomControls(true) on
  // Android; this file has no equivalent to disable on desktop, which
  // doesn't have a native pinch gesture to conflict with anyway). Two
  // confirmed-real problems, not hypothetical ones:
  // 1. It applies a full zoom_in()/zoom_out() step (editor.zoom_value,
  //    0.1 by default) on essentially every pointermove event during a
  //    pinch once finger separation crosses a fixed 100px threshold, not
  //    once per gesture - a real touchscreen fires many pointermove
  //    events per second while pinching, so this compounds into a wildly
  //    oversensitive zoom (confirmed on-device: "pinch-zoom is too
  //    fast").
  // 2. Independently and more fundamentally: Drawflow's touch-based pan
  //    handler (position(), same file) unconditionally reads only
  //    e.touches[0] on 'touchmove' - it has no awareness that a second
  //    finger might be down for a pinch at all, so it keeps panning the
  //    canvas off the first finger's movement throughout a pinch gesture,
  //    fighting the zoom the whole time. Confirmed on-device ("most of
  //    the time it feels like only one finger... is registered and only
  //    panning is possible") - not a tunable, a real limitation in the
  //    vendored library's own multitouch handling that only surfaces on
  //    a real touchscreen (a mouse can't produce two simultaneous
  //    pointers to begin with, so this was invisible on desktop).
  // Only the pointer-based pinch listeners are cleared here (Drawflow
  // assigns them via container.onpointerdown = ... property assignment,
  // not addEventListener, so this can't also clear this file's own
  // pointerdown/pointermove/pointerup listeners below, which are
  // independently registered via addEventListener) - Drawflow's
  // touchstart/touchmove/touchend-based single-finger drag-to-pan is left
  // untouched and still works normally.
  if (IS_MOBILE_LAYOUT) {
    editor.container.onpointerdown = null;
    editor.container.onpointermove = null;
    editor.container.onpointerup = null;
    editor.container.onpointercancel = null;
    editor.container.onpointerout = null;
    editor.container.onpointerleave = null;
  }

  // Nodes have no buttons of their own anymore (start/delete both moved
  // out - see "Set Start Node" toolbar button and #node-editor-modal's
  // Delete Node button), so the old capture-phase button-stopPropagation
  // trick is gone too. What's left: record the pointerdown position (and,
  // for desktop's Ctrl/Shift-click multi-select entry, whether a
  // modifier was held) for any pointerdown that lands on a node's body but
  // not a port, so the paired pointerup listener below can tell a plain
  // click apart from a node drag - and, on mobile only, arm a long-press
  // timer that enters selection mode (see selectNode()). Pointer Events,
  // not mouse events - see this file's header comment for the real,
  // on-device-confirmed reason (mobile browsers only synthesize
  // `mousedown` after `touchend`, too late for a before-release long-press
  // timer to mean anything). A pointerdown on genuinely empty canvas (not
  // a node, not #node-action-cab) while a selection is active clears it
  // entirely - same "click outside closes it" pattern as
  // #graph-context-menu, but deliberately NOT triggered by a pointerdown
  // on another node, which needs to reach the pointerup handler below to
  // toggle that node into/out of the selection instead.
  containerEl.addEventListener('pointerdown', function (event) {
    const onCab = !!event.target.closest('#node-action-cab');
    const nodeEl = event.target.closest('.drawflow-node');
    const onPort = !!event.target.closest('.inputs, .outputs');

    if (selectedNodeIds.size > 0 && !onCab && !nodeEl) hideNodeActionCab(); // pressed truly empty canvas while a selection was active

    clearTimeout(longPressTimer);
    longPressTimer = null;
    longPressFired = false;

    if (!nodeEl || onPort) {
      nodeClickCandidate = null;
      return;
    }
    // Touch/pen presses on a node suppress the browser's own compatibility
    // mousedown/mouseup/click sequence for this pointer's gesture (a
    // pointerdown's preventDefault() does this per the Pointer Events
    // spec - the same mechanism FastClick and similar libraries rely on).
    // Needed because openNodeEditor() (pointerup handler below) shows
    // #node-editor-modal synchronously, before that trailing compat click
    // arrives; without this, the click still fires afterward and hit-tests
    // against whatever is now on screen at that position, not the canvas
    // node actually pressed. Confirmed on-device as a real, destructive
    // bug: pressing a Branch node - whose taller image-list form pushes
    // Delete Node further down the vertically-centered modal-box than
    // shorter forms do - could immediately delete the node it just opened.
    // Left mouse alone: real mouse pointers don't get this synthesized
    // compat-click treatment (pointerdown/mousedown/pointerup/mouseup/click
    // are all genuine, independent events for a mouse), so there's nothing
    // to suppress there, and preventDefault() on a real mousedown risks
    // taking away default behavior (e.g. text selection) for no benefit.
    if (event.pointerType !== 'mouse') event.preventDefault();
    const nodeId = nodeEl.id.replace('node-', '');
    nodeClickCandidate = { nodeId: nodeId, x: event.clientX, y: event.clientY, modifierHeld: event.ctrlKey || event.shiftKey };
    // Only arm the long-press-to-enter-selection-mode timer on mobile,
    // and only when nothing is selected yet - once a selection is
    // already active, a long press wouldn't do anything different from
    // a short one (see the pointerup handler below). Desktop never arms
    // this at all; it enters/grows a selection via modifierHeld instead.
    if (IS_MOBILE_LAYOUT && selectedNodeIds.size === 0) {
      longPressTimer = setTimeout(function () {
        longPressFired = true;
        selectNode(nodeId);
      }, LONG_PRESS_MS);
    }
  });

  // Movement past the click threshold mid-press means this is turning
  // into a drag, not a tap or a long-press - cancels the pending
  // long-press timer the same way lifting the pointer early would.
  containerEl.addEventListener('pointermove', function (event) {
    if (!nodeClickCandidate || longPressTimer == null) return;
    if (Math.hypot(event.clientX - nodeClickCandidate.x, event.clientY - nodeClickCandidate.y) > CLICK_MOVE_THRESHOLD_PX) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
  });

  // A native 'click' event fires on pointerup regardless of how far the
  // pointer moved in between, as long as it ends over the same element -
  // and since a dragged node moves together with the pointer, it's still
  // "the same element" at drag-end, so a plain click listener alone would
  // pop the edit popup open after every single node drag too. Comparing
  // pointerdown/pointerup screen positions against a small pixel threshold
  // is the standard fix. Also where pickingStartNode's one-shot pick
  // lands: any pointerup while armed consumes the mode, whether or not it
  // actually hit a node, so the button can't get stuck "on" forever.
  //
  // Three outcomes for a clean (non-drag) press on a node, in priority
  // order - see this file's header comment for the full reasoning behind
  // each:
  // 1. Ctrl/Shift was held (modifierHeld, desktop's multi-select
  //    gesture) - toggle that node's membership, regardless of whatever
  //    else is or isn't already selected.
  // 2. No modifier, but a selection is already active AND this is mobile
  //    - toggle membership too (mobile has no modifier key to require;
  //    once selectNode() has entered selection mode via long press,
  //    every further short press just toggles).
  // 3. Otherwise - open the popup directly, same as a plain tap opens a
  //    file in every mobile file browser. If a desktop selection existed
  //    (case 2 never applies there), drop it first - a plain click
  //    elsewhere collapses a multi-select back to one item in every
  //    native file manager that has both gestures.
  containerEl.addEventListener('pointerup', function (event) {
    clearTimeout(longPressTimer);
    longPressTimer = null;
    const candidate = nodeClickCandidate;
    nodeClickCandidate = null;
    const wasLongPress = longPressFired;
    longPressFired = false;
    if (wasLongPress) return; // selectNode() already ran from the timer - nothing left to do on release

    const nodeEl = candidate && event.target.closest('.drawflow-node');
    const hitCandidate = !!(nodeEl && nodeEl.id.replace('node-', '') === candidate.nodeId
      && Math.hypot(event.clientX - candidate.x, event.clientY - candidate.y) <= CLICK_MOVE_THRESHOLD_PX);

    if (pickingStartNode) {
      if (hitCandidate) setStartNode(candidate.nodeId);
      disarmPickStartNode();
      return;
    }
    if (!hitCandidate) return;

    if (candidate.modifierHeld || (IS_MOBILE_LAYOUT && selectedNodeIds.size > 0)) {
      toggleNodeSelection(candidate.nodeId);
    } else {
      if (selectedNodeIds.size > 0) hideNodeActionCab();
      openNodeEditor(candidate.nodeId);
    }
  });

  editor.on('nodeCreated', function () { onDirty(); });
  editor.on('nodeRemoved', function (id) {
    // editor.removeNodeId() (called both by #node-editor-modal's Delete
    // Node button and, in principle, anywhere else) never clears
    // node_selected or fires nodeUnselected itself even when the removed
    // node was the selected one - confirmed by reading Drawflow's own
    // source, not assumed - so without this check a deleted-while-selected
    // node would leave it in selectedNodeIds, pointing at a node id that
    // no longer exists. Only drops that one id, not the whole selection -
    // the rest of a multi-select stays intact.
    if (selectedNodeIds.delete(String(id))) {
      if (selectedNodeIds.size === 0) hideNodeActionCab();
      else updateNodeActionCab();
    }
    onDirty();
  });
  editor.on('connectionCreated', function (payload) {
    enforceSingleOutputConnection(payload);
    onDirty();
  });
  editor.on('connectionRemoved', function () { onDirty(); });

  // Drawflow's own 'contextmenu' event (see its contextmenu(e) method)
  // already preventDefault()s the native browser menu; its own
  // right-click-shows-a-delete-"x" behavior is suppressed entirely via
  // CSS (index.html's `.drawflow-delete { display: none !important; }`)
  // rather than here - right-click isn't a usual mobile gesture, and
  // #node-editor-modal's own Delete Node button already covers the same
  // need on both widths, so it was redundant even on desktop. Right-
  // click on empty canvas to add a node is untouched, still useful on
  // desktop and not the thing that was asked to go.
  editor.on('contextmenu', function (e) {
    if (!e.target.closest('.drawflow-node')) {
      showGraphContextMenu(e.clientX, e.clientY);
    }
  });

  initImageEditorModal();
  initNodeEditorModal();
  initSetStartNodeButton();
  initNodeActionCab();
  initGraphContextMenu();

  // IS_MOBILE_LAYOUT is fixed for this deployment (see this file's header
  // comment) - body's mobile-width class (index.html's CSS keys node
  // sizing/the CAB off it) is set once here and never revisited, unlike
  // the old width-measuring version of this that had to react to a
  // resizable window.
  document.body.classList.toggle('mobile-width', IS_MOBILE_LAYOUT);
}

// "Set Start Node": clicking the toolbar button arms pickingStartNode
// (see the mouseup listener above for where the actual pick happens);
// clicking it again while armed, or pressing Escape (see
// initGraphContextMenu()), disarms it without picking anything.
function armPickStartNode() {
  pickingStartNode = true;
  const btn = document.getElementById('set-start-node-btn');
  if (btn) {
    btn.classList.add('armed');
    btn.textContent = 'Click a node...';
  }
}

function disarmPickStartNode() {
  pickingStartNode = false;
  const btn = document.getElementById('set-start-node-btn');
  if (btn) {
    btn.classList.remove('armed');
    btn.textContent = 'Set Start Node';
  }
}

// #set-start-node-btn is optional, not guaranteed to exist in every host
// page - android-screen-scan-macro's copy of index.html drops it entirely
// (its arm-then-tap-any-node flow is redundant there with
// #node-action-cab's own "Set as Start" button, reachable via long-press
// on mobile), relying only on setStartNode() being called from the CAB
// instead. Guarded here so that omission doesn't throw and abort the rest
// of initGraphEditor() - pickingStartNode/armPickStartNode()/
// disarmPickStartNode() stay as dead-but-harmless code on a page with no
// button to trigger them, kept only so the toolbar button could come back
// on either platform without further JS changes.
function initSetStartNodeButton() {
  const btn = document.getElementById('set-start-node-btn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    if (pickingStartNode) disarmPickStartNode();
    else armPickStartNode();
  });
}

// Toggles each node's own visual selection highlight (.multi-selected,
// index.html) to match selectedNodeIds - needed because Drawflow's own
// native single-node 'selected' class (this.node_selected) can only ever
// mark one element at a time, which doesn't reflect a multi-selection at
// all; this is a separate highlight this app owns, not a wrapper around
// Drawflow's. Called from updateNodeActionCab()/hideNodeActionCab() -
// every selectedNodeIds mutation path (selectNode(),
// toggleNodeSelection(), the mousedown "clicked empty canvas" case, and
// the 'nodeRemoved' handler) ends in one of those two, so this stays in
// sync without repeating the call at every individual site.
function applyNodeSelectionHighlight() {
  document.querySelectorAll('.drawflow-node').forEach(function (el) {
    el.classList.toggle('multi-selected', selectedNodeIds.has(el.id.replace('node-', '')));
  });
}

// #node-action-cab: the contextual action bar shown whenever
// selectedNodeIds is non-empty, on either layout - not gated by
// body.mobile-width the way node sizing is, since desktop's own
// Ctrl/Shift-click entry (see this file's header comment) needs it just
// as much as mobile's long-press one does. Edit and Set as Start are
// only enabled for a single-node selection - multi-select has no action
// of its own yet (grouping, planned later, not built today). The
// optional selection-count label only shows for 2+, so the common
// single-select case looks exactly like before multi-select existed.
function updateNodeActionCab() {
  applyNodeSelectionHighlight();
  const cab = document.getElementById('node-action-cab');
  if (!cab) return;
  cab.classList.add('visible');
  const multi = selectedNodeIds.size > 1;
  document.getElementById('cab-edit-btn').disabled = multi;
  document.getElementById('cab-start-btn').disabled = multi;
  const countEl = document.getElementById('cab-count');
  if (countEl) countEl.textContent = multi ? selectedNodeIds.size + ' selected' : '';
}

// selectNode() (long press - always starts a fresh one-node selection)
// and toggleNodeSelection() (short press once a selection is already
// active - adds/removes one node) are the only two ways into a
// selection; the empty-selection paths inside toggleNodeSelection() and
// the 'nodeRemoved' handler, plus a mousedown on empty canvas, are the
// only ways out, all funneling through hideNodeActionCab() below.
function selectNode(nodeId) {
  selectedNodeIds = new Set([nodeId]);
  updateNodeActionCab();
}

function toggleNodeSelection(nodeId) {
  if (!selectedNodeIds.delete(nodeId)) selectedNodeIds.add(nodeId);
  if (selectedNodeIds.size === 0) hideNodeActionCab();
  else updateNodeActionCab();
}

function hideNodeActionCab() {
  selectedNodeIds = new Set();
  applyNodeSelectionHighlight();
  const cab = document.getElementById('node-action-cab');
  if (cab) cab.classList.remove('visible');
}

function initNodeActionCab() {
  document.getElementById('cab-edit-btn').addEventListener('click', function () {
    if (selectedNodeIds.size === 1) openNodeEditor(Array.from(selectedNodeIds)[0]);
    hideNodeActionCab();
  });
  document.getElementById('cab-start-btn').addEventListener('click', function () {
    if (selectedNodeIds.size === 1) setStartNode(Array.from(selectedNodeIds)[0]);
    hideNodeActionCab();
  });
}

/* --- Node edit popup: one shared #node-editor-modal, repointed at
 * whichever node is open (editingNodeId) - same pattern as the branch
 * image editor's editingBranchNodeId. Fields are plain inputs/selects
 * named via df-<key> attributes (kept only as a readable naming
 * convention here, not for Drawflow's own auto-binding - see this file's
 * header comment for why that binding doesn't reach into a modal). */

// Reads a field's data key off whichever of its attributes starts with
// "df-" - the attribute name itself varies per field (df-action_type,
// df-duration_ms, ...), so this can't be a single known attribute-name
// CSS selector; iterating the element's own attribute list is the direct
// way to recover it.
function dfFieldName(el) {
  for (let i = 0; i < el.attributes.length; i++) {
    const attr = el.attributes[i];
    if (attr.name.indexOf('df-') === 0) return attr.name.slice(3);
  }
  return null;
}

function populateNodeEditorFields(data) {
  document.querySelectorAll('#node-editor-fields input, #node-editor-fields select').forEach(function (el) {
    const key = dfFieldName(el);
    if (key == null) return;
    const value = data[key];
    if (value != null) el.value = value;
  });
}

function openNodeEditor(nodeId) {
  const node = editor.getNodeFromId(nodeId);
  editingNodeId = nodeId;
  document.getElementById('node-editor-title').textContent = NODE_TYPE_LABELS[node.name] || 'Edit Node';
  const fieldsEl = document.getElementById('node-editor-fields');
  fieldsEl.innerHTML = renderEditFormHtml(node.name);
  populateNodeEditorFields(node.data);
  if (node.name === 'action') updateActionFieldVisibility(fieldsEl);
  document.getElementById('node-editor-modal').style.display = 'flex';
}

function closeNodeEditor() {
  editingNodeId = null;
  const modal = document.getElementById('node-editor-modal');
  if (modal) modal.style.display = 'none';
}

function initNodeEditorModal() {
  document.getElementById('node-editor-close-btn').addEventListener('click', closeNodeEditor);

  // Delete Node replaces the old per-node "×" button - it lives outside
  // fieldsEl (a fixed part of the modal, not the per-type dynamic form),
  // same reasoning as #image-editor-modal's "+ Add Image..." button.
  document.getElementById('node-editor-delete-btn').addEventListener('click', function () {
    if (editingNodeId == null) return;
    const id = editingNodeId;
    if (editingBranchNodeId === id) closeImageEditor();
    closeNodeEditor();
    editor.removeNodeId('node-' + id);
    if (startNodeId === id) startNodeId = null;
    applyStartHighlight();
    notifyDirty();
  });

  const fieldsEl = document.getElementById('node-editor-fields');

  // Manual equivalent of Drawflow's own df-* auto-sync (see this file's
  // header comment) - reads the field's df-<key> name, writes it into the
  // node's data via the public updateNodeDataFromId() API (same one
  // pickClickRegionFlow()/showClickRegionFlow() already use elsewhere in
  // this file), and toggles Action's click-vs-key field visibility the
  // same way the old inline binding did.
  fieldsEl.addEventListener('input', function (event) {
    if (editingNodeId == null) return;
    const key = dfFieldName(event.target);
    if (key == null) return;
    const data = Object.assign({}, editor.getNodeFromId(editingNodeId).data);
    data[key] = event.target.value;
    editor.updateNodeDataFromId(editingNodeId, data);
    if (event.target.matches('[df-action_type]')) updateActionFieldVisibility(fieldsEl);
    if (key === 'title') updateMiniNodeDisplay(editingNodeId); // title drives the on-canvas display text too, see nodeDisplayText()
    notifyDirty();
  });

  fieldsEl.addEventListener('click', function (event) {
    if (editingNodeId == null) return;
    if (event.target.classList.contains('edit-images-btn')) {
      openImageEditor(editingNodeId);
    } else if (event.target.classList.contains('pick-click-region-btn')) {
      pickClickRegionFlow(editingNodeId);
    } else if (event.target.classList.contains('show-click-region-btn')) {
      showClickRegionFlow(editingNodeId);
    }
  });

  // Key-combo capture for Action's Key Press field - moved here verbatim
  // from the old containerEl-scoped listener, since the key-capture-input
  // field now lives in this modal instead of inside the node's own DOM.
  fieldsEl.addEventListener('keydown', function (event) {
    if (!event.target.classList.contains('key-capture-input')) return;
    event.preventDefault();
    if (event.key === 'Escape') {
      event.target.value = '';
    } else if (MODIFIER_KEYS.indexOf(event.key) === -1) {
      event.target.value = keydownToCombo(event);
    } else {
      return; // a lone modifier keydown - keep waiting for the real key
    }
    event.target.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

// Converts a viewport point (e.g. a contextmenu event's clientX/clientY)
// into Drawflow's local/unscaled coordinate space - the same unprojection
// Drawflow's own source uses internally (e.g. its drawConnection()):
// subtract the (already zoom/pan-transformed) canvas's own screen origin,
// then divide by the current zoom.
function screenToLocalPosition(clientX, clientY) {
  const rect = editor.precanvas.getBoundingClientRect();
  return {
    x: (clientX - rect.left) / editor.zoom,
    y: (clientY - rect.top) / editor.zoom,
  };
}

let contextMenuPosition = null;

function showGraphContextMenu(clientX, clientY) {
  contextMenuPosition = screenToLocalPosition(clientX, clientY);
  const menu = document.getElementById('graph-context-menu');
  menu.style.left = clientX + 'px';
  menu.style.top = clientY + 'px';
  menu.style.display = 'block';
}

function hideGraphContextMenu() {
  document.getElementById('graph-context-menu').style.display = 'none';
  contextMenuPosition = null;
}

function initGraphContextMenu() {
  const menu = document.getElementById('graph-context-menu');
  menu.querySelectorAll('button[data-node-type]').forEach(function (button) {
    button.addEventListener('click', function () {
      const position = contextMenuPosition;
      if (button.dataset.nodeType === 'action') addActionNode(position);
      else if (button.dataset.nodeType === 'wait') addWaitNode(position);
      else if (button.dataset.nodeType === 'branch') addBranchNode(position);
      else if (button.dataset.nodeType === 'branch_wait') addBranchWaitNode(position);
      hideGraphContextMenu();
    });
  });
  document.addEventListener('click', function (event) {
    if (menu.style.display === 'block' && !menu.contains(event.target)) {
      hideGraphContextMenu();
    }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    hideGraphContextMenu();
    if (pickingStartNode) disarmPickStartNode();
  });
}

// Frames every node in the current graph within #drawflow-canvas's visible
// area - no built-in Drawflow equivalent (it only has zoom_in/zoom_out/
// zoom_reset, all relative to the current view, not content-aware).
//
// Resets to an identity transform (zoom=1, canvas at 0,0) immediately
// before measuring, rather than inverting whatever transform is currently
// applied - a version that did the latter worked on the second press but
// not the first (and again after every window resize, first-press-fails-
// second-succeeds), pointing at editor.canvas_x/canvas_y/zoom being
// stale/inconsistent with what's actually painted at the moment it read
// them. With the transform forced to zero right before measuring,
// getBoundingClientRect() already reports local coordinates directly - no
// inversion, no dependency on that state being trustworthy. This never
// paints a visible flash at the reset zoom level: the reset and the final
// fitted transform both happen synchronously in this one function, and
// the browser only paints once the whole task finishes.
function fitViewToNodes() {
  const nodeEls = Array.from(document.querySelectorAll('#drawflow-canvas .drawflow-node'));
  if (nodeEls.length === 0) return;

  const container = document.getElementById('drawflow-canvas');
  editor.zoom = 1;
  editor.zoom_last_value = 1;
  editor.canvas_x = 0;
  editor.canvas_y = 0;
  editor.precanvas.style.transform = 'translate(0px, 0px) scale(1)';

  const containerRect = container.getBoundingClientRect();
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  nodeEls.forEach(function (el) {
    const rect = el.getBoundingClientRect();
    minX = Math.min(minX, rect.left - containerRect.left);
    minY = Math.min(minY, rect.top - containerRect.top);
    maxX = Math.max(maxX, rect.right - containerRect.left);
    maxY = Math.max(maxY, rect.bottom - containerRect.top);
  });

  const viewWidth = container.clientWidth;
  const viewHeight = container.clientHeight;
  const padding = 40; // local-space margin around the content, each side

  const contentWidth = (maxX - minX) + padding * 2;
  const contentHeight = (maxY - minY) + padding * 2;
  let zoom = Math.min(viewWidth / contentWidth, viewHeight / contentHeight);
  zoom = Math.min(Math.max(zoom, editor.zoom_min), editor.zoom_max);

  // precanvas has no transform-origin override in Drawflow's vendor CSS, so
  // it uses the CSS default (50% 50%, its own center) - NOT the top-left
  // corner. translate(x,y) scale(s) around a center origin O maps a local
  // point P to O + s*(P - O) + (x,y), not simply x + P*s - so hitting an
  // exact target screen position for the content's center requires this
  // extra origin term (viewWidth/2 and viewHeight/2 here, since precanvas
  // is stretched to exactly fill #drawflow-canvas, so its own center
  // equals the container's center) rather than the naive top-left-origin
  // formula this used before.
  const contentCenterX = minX + (maxX - minX) / 2;
  const contentCenterY = minY + (maxY - minY) / 2;
  const canvasX = zoom * (viewWidth / 2 - contentCenterX);
  const canvasY = zoom * (viewHeight / 2 - contentCenterY);

  editor.zoom = zoom;
  editor.zoom_last_value = zoom;
  editor.canvas_x = canvasX;
  editor.canvas_y = canvasY;
  editor.precanvas.style.transform = 'translate(' + canvasX + 'px, ' + canvasY + 'px) scale(' + zoom + ')';
  editor.dispatch('zoom', zoom);
}

// Every output port here means "go to exactly one next node" (matches
// build_engine_graph_from_document()'s _first_connected_document_node(),
// which only ever reads the first connection - see graph_translation.py).
// Drawflow itself places no limit on connections per output, so a new
// connection from a port that already had one silently replaces it rather
// than fanning out, to avoid a second wire that the engine would just
// ignore.
function enforceSingleOutputConnection(payload) {
  const node = editor.getNodeFromId(payload.output_id);
  const connections = (node.outputs[payload.output_class] || {}).connections || [];
  connections.forEach(function (conn) {
    const isTheNewOne = String(conn.node) === String(payload.input_id) && conn.output === payload.input_class;
    if (!isTheNewOne) {
      editor.removeSingleConnection(payload.output_id, conn.node, payload.output_class, conn.output);
    }
  });
}

// position is optional ({x, y} in Drawflow's local/unscaled coordinate
// space) - the graph-canvas context menu passes the actual right-clicked
// spot (see screenToLocalPosition()); callers with no particular spot in
// mind (none currently) would fall back to the staggered default.
function addActionNode(position) {
  const pos = position || nextSpawnPosition();
  const data = defaultActionProperties();
  return editor.addNode(
    'action', 1, 1, pos.x, pos.y, 'action-node',
    data, renderMiniNodeHtml('action', data),
  );
}

function addWaitNode(position) {
  const pos = position || nextSpawnPosition();
  const data = defaultWaitProperties();
  return editor.addNode(
    'wait', 1, 1, pos.x, pos.y, 'wait-node',
    data, renderMiniNodeHtml('wait', data),
  );
}

function addBranchNode(position) {
  const pos = position || nextSpawnPosition();
  const data = defaultBranchProperties();
  const id = editor.addNode(
    'branch', 1, 1, pos.x, pos.y, 'branch-node', // 1 output: just 'false', 0 images
    data, renderMiniNodeHtml('branch', data),
  );
  renderBranchNodeImageList(id);
  return id;
}

function addBranchWaitNode(position) {
  const pos = position || nextSpawnPosition();
  const data = defaultBranchWaitProperties();
  const id = editor.addNode(
    'branch_wait', 1, 0, pos.x, pos.y, 'branch-wait-node', // 0 outputs, 0 images - no false port ever
    data, renderMiniNodeHtml('branch_wait', data),
  );
  renderBranchNodeImageList(id);
  return id;
}

function clearGraphEditor() {
  editor.clear();
  startNodeId = null;
  nextSpawnOffset = 0;
  closeImageEditor();
  closeNodeEditor();
  hideNodeActionCab(); // editor.clear() wipes Drawflow's own DOM/data but not our own CAB state - a stale one would survive a profile switch otherwise
}

// A profile saved by the old NodeGraphQt editor stores its graph as a
// NodeGraphQt session blob (node ids like "0x23bc0739160", fields named
// `type_`/`pos`/`custom` - confirmed by reading a real profile.json), not
// this GraphDocument schema (`type`/`position`/`properties`). Per the
// decision to not write a migration script, such a profile can't be loaded
// here - it must be recreated in this editor. Detect that case up front
// instead of letting loadGraphDocument() throw partway through rendering
// (which left the canvas in a half-built state).
function graphDocumentIsCompatible(graph) {
  if (!graph || typeof graph !== 'object' || !graph.nodes) return true; // empty/missing graph is fine
  const ids = Object.keys(graph.nodes);
  if (ids.length === 0) return true;
  const first = graph.nodes[ids[0]];
  return !!(first && Array.isArray(first.position) && typeof first.type === 'string');
}

/* GraphDocument (see engine/runner.py's module docstring for the engine-
 * side schema this ultimately becomes) <-> Drawflow's own export() shape.
 * Action/Wait nodes always have one input ("in") and one output ("out");
 * Branch has one input and N+1 outputs named "1".."N" (image priority
 * order) + "false"; Branch (Wait) has one input and N outputs named
 * "1".."N" only - no "false" port at all. Both map onto Drawflow's
 * output_1..output_N(+1) in that same order - an invariant this module
 * always maintains (see rebuildBranchOutputPorts()), so no separate
 * name-mapping needs storing. */
function loadGraphDocument(doc, imageThumbnails) {
  clearGraphEditor();
  mergeImageThumbnails(imageThumbnails);
  const nodes = (doc && doc.nodes) || {};
  const docIds = Object.keys(nodes);
  const docIdToDrawflowId = {};

  docIds.forEach(function (docId) {
    const node = nodes[docId];
    const isBranchFamily = node.type === 'branch' || node.type === 'branch_wait';
    const numImages = isBranchFamily ? (node.properties.images || []).length : 0;
    const numOutputs = node.type === 'branch' ? numImages + 1 : node.type === 'branch_wait' ? numImages : 1;
    const newId = editor.addNode(
      node.type, 1, numOutputs, node.position[0], node.position[1],
      node.type.replace('_', '-') + '-node', Object.assign({}, node.properties), renderMiniNodeHtml(node.type, node.properties),
    );
    docIdToDrawflowId[docId] = newId;
    if (isBranchFamily) renderBranchNodeImageList(newId);
  });

  Object.keys(nodes).forEach(function (docId) {
    const node = nodes[docId];
    const sourceId = docIdToDrawflowId[docId];
    const connectionsByPort = node.connections || {};
    const numImages = (node.type === 'branch' || node.type === 'branch_wait')
      ? (node.properties.images || []).length : 0;
    const portNames = node.type === 'branch'
      ? Array.from({ length: numImages }, function (_, i) { return String(i + 1); }).concat(['false'])
      : node.type === 'branch_wait'
      ? Array.from({ length: numImages }, function (_, i) { return String(i + 1); })
      : ['out'];
    portNames.forEach(function (portName, outputIndex) {
      (connectionsByPort[portName] || []).forEach(function (conn) {
        const targetId = docIdToDrawflowId[conn.node];
        if (targetId != null) {
          editor.addConnection(sourceId, targetId, 'output_' + (outputIndex + 1), 'input_1');
        }
      });
    });
  });

  startNodeId = doc && doc.start_node_id != null ? String(docIdToDrawflowId[doc.start_node_id]) : null;
  applyStartHighlight();
}

function exportGraphDocument() {
  const exported = editor.export();
  const rawNodes = (exported.drawflow.Home && exported.drawflow.Home.data) || {};
  const nodes = {};
  Object.keys(rawNodes).forEach(function (id) {
    const raw = rawNodes[id];
    const outputNames = Object.keys(raw.outputs); // already in output_1..output_N order (see rebuildBranchOutputPorts)
    const portNames = raw.name === 'branch'
      ? outputNames.slice(0, -1).map(function (_, i) { return String(i + 1); }).concat(['false'])
      : raw.name === 'branch_wait'
      ? outputNames.map(function (_, i) { return String(i + 1); })
      : ['out'];
    const connections = {};
    outputNames.forEach(function (outputClass, i) {
      const conns = (raw.outputs[outputClass] || {}).connections || [];
      connections[portNames[i]] = conns.map(function (c) { return { node: String(c.node), port: 'in' }; });
    });
    nodes[id] = {
      type: raw.name,
      position: [raw.pos_x, raw.pos_y],
      properties: raw.data,
      connections: connections,
    };
  });
  return {
    schema_version: 1,
    start_node_id: startNodeId != null ? String(startNodeId) : null,
    nodes: nodes,
  };
}

/* --- Branch/Branch (Wait): images list + per-image output ports --- */

function mergeImageThumbnails(map) {
  Object.assign(imageThumbnailUrls, map || {});
}

function branchImagesOf(nodeId) {
  return editor.getNodeFromId(nodeId).data.images || [];
}

// Reads the node's *current* output ports back into the same
// {portName: [{node, port}]} shape GraphDocument.connections uses, relying
// on the output_k <-> port-name invariant described above loadGraphDocument().
// Only a 'branch'-type node has a trailing false port; 'branch_wait' never
// does (see module docstring).
function currentBranchConnections(nodeId) {
  const node = editor.getNodeFromId(nodeId);
  const hasFalsePort = node.name === 'branch';
  const numOutputs = Object.keys(node.outputs).length;
  const numImages = hasFalsePort ? numOutputs - 1 : numOutputs;
  const result = {};
  for (let i = 0; i < numImages; i++) {
    const conns = (node.outputs['output_' + (i + 1)] || {}).connections || [];
    result[String(i + 1)] = conns.map(function (c) { return { node: String(c.node), port: 'in' }; });
  }
  if (hasFalsePort) {
    const falseConns = (node.outputs['output_' + numOutputs] || {}).connections || [];
    result.false = falseConns.map(function (c) { return { node: String(c.node), port: 'in' }; });
  }
  return result;
}

// Rebuilds every output port from scratch (mirrors the old
// DecisionNode._sync_output_ports()'s "recompute from scratch is simpler
// than incremental rename/reconnect" approach) from newConnections (as
// returned by branch_images.rewire_ports_after_image_change() via the
// bridge). includeFalsePort is a property of the node's *type* (true for
// 'branch', false for 'branch_wait'), not a per-instance toggle - see
// module docstring. Removing "output_1" existingCount times is deliberate,
// not a typo - Drawflow's removeNodeOutput() renumbers remaining ports
// down after each removal (confirmed via source read), so the first
// remaining port is always named output_1 regardless of how many
// removals have happened.
function rebuildBranchOutputPorts(nodeId, newConnections, numImages, includeFalsePort) {
  const node = editor.getNodeFromId(nodeId);
  const existingCount = Object.keys(node.outputs).length;
  for (let i = 0; i < existingCount; i++) {
    editor.removeNodeOutput(nodeId, 'output_1');
  }
  const totalPorts = numImages + (includeFalsePort ? 1 : 0);
  for (let i = 0; i < totalPorts; i++) {
    editor.addNodeOutput(nodeId);
  }
  for (let i = 0; i < numImages; i++) {
    (newConnections[String(i + 1)] || []).forEach(function (conn) {
      editor.addConnection(nodeId, conn.node, 'output_' + (i + 1), 'input_1');
    });
  }
  if (includeFalsePort) {
    (newConnections.false || []).forEach(function (conn) {
      editor.addConnection(nodeId, conn.node, 'output_' + (numImages + 1), 'input_1');
    });
  }
}

// Shared by add/delete/move: apply a new images[] + the position_mapping
// that describes it (same {new_index: old_index_or_null} shape the old
// NodeGraphQt desktop app's add/delete/move-image handlers built), via
// branch_images.rewire_ports_after_image_change() (Python, tested)
// through the bridge, then rebuild ports/UI to match.
function applyBranchImagesChange(nodeId, newImages, positionMapping) {
  const includeFalsePort = editor.getNodeFromId(nodeId).name === 'branch';
  const oldConnections = currentBranchConnections(nodeId);
  return pywebview.api.rewire_branch_ports(oldConnections, positionMapping, newImages.length).then(function (newConnections) {
    const data = Object.assign({}, editor.getNodeFromId(nodeId).data, { images: newImages });
    editor.updateNodeDataFromId(nodeId, data);
    rebuildBranchOutputPorts(nodeId, newConnections, newImages.length, includeFalsePort);
    renderBranchNodeImageList(nodeId);
    renderImageEditorList();
    setDirty(true);
  });
}

function addBranchImageFlow() {
  const nodeId = editingBranchNodeId;
  pywebview.api.add_branch_image(currentProfile, nodeId).then(function (result) {
    if (!result.ok) {
      if (!result.cancelled) showError(result.error);
      return;
    }
    imageThumbnailUrls[result.image.reference_path] = result.thumbnail_url;
    const images = branchImagesOf(nodeId);
    const mapping = {};
    for (let i = 0; i < images.length; i++) mapping[i] = i;
    mapping[images.length] = null;
    applyBranchImagesChange(nodeId, images.concat([result.image]), mapping);
  });
}

function deleteBranchImage(index) {
  const nodeId = editingBranchNodeId;
  const images = branchImagesOf(nodeId);
  const newImages = images.slice(0, index).concat(images.slice(index + 1));
  const mapping = {};
  for (let newIndex = 0; newIndex < newImages.length; newIndex++) {
    mapping[newIndex] = newIndex < index ? newIndex : newIndex + 1;
  }
  applyBranchImagesChange(nodeId, newImages, mapping);
}

function moveBranchImage(index, delta) {
  const nodeId = editingBranchNodeId;
  const images = branchImagesOf(nodeId);
  const target = index + delta;
  if (target < 0 || target >= images.length) return;
  const newImages = images.slice();
  const tmp = newImages[index];
  newImages[index] = newImages[target];
  newImages[target] = tmp;
  const mapping = {};
  for (let i = 0; i < images.length; i++) mapping[i] = i;
  mapping[index] = target;
  mapping[target] = index;
  applyBranchImagesChange(nodeId, newImages, mapping);
}

function openImageEditor(nodeId) {
  editingBranchNodeId = nodeId;
  renderImageEditorList();
  document.getElementById('image-editor-modal').style.display = 'flex';
}

function closeImageEditor() {
  editingBranchNodeId = null;
  const modal = document.getElementById('image-editor-modal');
  if (modal) modal.style.display = 'none';
}

function renderImageEditorList() {
  if (editingBranchNodeId == null) return;
  const images = branchImagesOf(editingBranchNodeId);
  const list = document.getElementById('image-editor-list');
  list.innerHTML = '';
  images.forEach(function (image, index) {
    const row = document.createElement('div');
    row.className = 'image-editor-row';
    const thumbUrl = imageThumbnailUrls[image.reference_path] || '';
    row.innerHTML =
      '<img class="image-editor-thumb" src="' + thumbUrl + '" alt="">' +
      '<span class="image-editor-index">#' + (index + 1) + '</span>' +
      '<button type="button" class="move-left-btn"' + (index === 0 ? ' disabled' : '') + '>←</button>' +
      '<button type="button" class="move-right-btn"' + (index === images.length - 1 ? ' disabled' : '') + '>→</button>' +
      '<button type="button" class="show-image-region-btn">Show</button>' +
      '<button type="button" class="delete-image-btn">Delete</button>';
    row.querySelector('.move-left-btn').addEventListener('click', function () { moveBranchImage(index, -1); });
    row.querySelector('.move-right-btn').addEventListener('click', function () { moveBranchImage(index, 1); });
    row.querySelector('.show-image-region-btn').addEventListener('click', function () { showImageRegionFlow(index); });
    row.querySelector('.delete-image-btn').addEventListener('click', function () { deleteBranchImage(index); });
    list.appendChild(row);
  });
}

function initImageEditorModal() {
  document.getElementById('image-editor-close-btn').addEventListener('click', closeImageEditor);
  document.getElementById('image-editor-add-btn').addEventListener('click', addBranchImageFlow);
}

/* --- Click-region picker / show-region preview ---
 * Native overlays over the live target window, via pick_controller.py -
 * the old NodeGraphQt desktop app wired this directly to a node's Qt
 * widgets; here it's bridge-callable, returning a plain dict instead.
 * pick_click_region() blocks Python-side until the user finishes
 * dragging or cancels (see pick_controller.py's module docstring for why
 * that's safe) - from here it's just an ordinary awaited bridge call. */

function currentTargetWindowTitle() {
  return document.getElementById('target-window-title').value.trim();
}

async function pickClickRegionFlow(nodeId) {
  const result = await pywebview.api.pick_click_region(currentTargetWindowTitle());
  if (!result.ok) {
    if (!result.cancelled) showError(result.error);
    return;
  }
  const data = Object.assign({}, editor.getNodeFromId(nodeId).data, {
    click_x: result.x, click_y: result.y, click_w: result.w, click_h: result.h,
  });
  editor.updateNodeDataFromId(nodeId, data);
  setDirty(true);
}

async function showClickRegionFlow(nodeId) {
  const data = editor.getNodeFromId(nodeId).data;
  const result = await pywebview.api.show_click_region(
    currentTargetWindowTitle(), data.click_x, data.click_y, data.click_w, data.click_h,
  );
  if (!result.ok) showError(result.error);
}

async function showImageRegionFlow(index) {
  const image = branchImagesOf(editingBranchNodeId)[index];
  const result = await pywebview.api.show_reference_region(
    currentProfile, currentTargetWindowTitle(), image.reference_path, image.region_x, image.region_y,
  );
  if (!result.ok) showError(result.error);
}
