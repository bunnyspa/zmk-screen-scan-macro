/* Graph editor built on Drawflow (vendored at vendor/drawflow/) - Rete.js/
 * Baklava.js were ruled out since both require React/Vue + a bundler,
 * which this app deliberately has none of.
 *
 * Owns everything about *editing* the graph - node/port/connection
 * rendering, property fields (bound directly via Drawflow's df-*
 * attributes, no separate side panel), the exclusive "start node" flag,
 * and packaging/unpacking the GraphDocument JSON (index.html owns
 * save/load plumbing and calls the two exported functions below).
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
 * at once anyway.
 */

// This string value must match graph_translation.py's _ACTION_KEY_PRESS
// exactly - it's stored verbatim in properties.action_type and read back
// by build_engine_graph_from_document() when translating a GraphDocument
// to the engine schema. Kept in sync by convention, not by import (no
// Qt-importing code in this file).
const ACTION_TYPE_CLICK = 'Click';
const ACTION_TYPE_KEY_PRESS = 'Key Press';
const MODIFIER_KEYS = ['Control', 'Alt', 'Shift', 'Meta'];

let editor = null;
let startNodeId = null;
let nextSpawnOffset = 0;
let editingBranchNodeId = null;
let imageThumbnailUrls = {}; // reference_path -> data: URI, display-only, never saved

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

function renderActionNodeHtml() {
  return (
    '<div class="node-header">' +
      '<span class="node-type-label">Action</span>' +
      '<button type="button" class="icon-btn set-start-btn" title="Set as start node">☆</button>' +
      '<button type="button" class="icon-btn delete-node-btn" title="Delete node">×</button>' +
    '</div>' +
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

function renderWaitNodeHtml() {
  return (
    '<div class="node-header">' +
      '<span class="node-type-label">Wait</span>' +
      '<button type="button" class="icon-btn set-start-btn" title="Set as start node">☆</button>' +
      '<button type="button" class="icon-btn delete-node-btn" title="Delete node">×</button>' +
    '</div>' +
    '<label>Duration (ms) <input type="number" df-duration_ms min="0"></label>'
  );
}

function renderBranchNodeHtml() {
  return (
    '<div class="node-header">' +
      '<span class="node-type-label">Branch</span>' +
      '<button type="button" class="icon-btn set-start-btn" title="Set as start node">☆</button>' +
      '<button type="button" class="icon-btn delete-node-btn" title="Delete node">×</button>' +
    '</div>' +
    '<label>Match Threshold <input type="number" df-match_threshold min="0" max="1" step="0.01"></label>' +
    '<button type="button" class="edit-images-btn" style="margin-top: 6px;">Edit Images...</button>'
  );
}

function renderBranchWaitNodeHtml() {
  return (
    '<div class="node-header">' +
      '<span class="node-type-label">Branch (Wait)</span>' +
      '<button type="button" class="icon-btn set-start-btn" title="Set as start node">☆</button>' +
      '<button type="button" class="icon-btn delete-node-btn" title="Delete node">×</button>' +
    '</div>' +
    '<label>Match Threshold <input type="number" df-match_threshold min="0" max="1" step="0.01"></label>' +
    '<label>Poll Interval (ms) <input type="number" df-poll_interval_ms min="10"></label>' +
    '<button type="button" class="edit-images-btn" style="margin-top: 6px;">Edit Images...</button>'
  );
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

const BRANCH_THUMB_SIZE = 20;
const BRANCH_THUMB_GAP = 4;

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
    const left = (portRect.left - nodeRect.left) / zoom - BRANCH_THUMB_SIZE - BRANCH_THUMB_GAP;
    const thumbUrl = imageThumbnailUrls[image.reference_path] || '';
    return '<img class="branch-node-thumb" style="top: ' + top + 'px; left: ' + left + 'px;" ' +
      'src="' + thumbUrl + '" alt="" title="Image #' + (index + 1) + '">';
  }).join('');
}

function applyStartHighlight() {
  document.querySelectorAll('.drawflow-node').forEach(function (el) {
    const isStart = startNodeId != null && el.id === 'node-' + startNodeId;
    el.classList.toggle('is-start-node', isStart);
    const btn = el.querySelector('.set-start-btn');
    if (btn) btn.textContent = isStart ? '★' : '☆';
  });
}

function nodeIdFromEventTarget(target) {
  const nodeEl = target.closest('.drawflow-node');
  if (!nodeEl) return null;
  return nodeEl.id.replace('node-', '');
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

  // Drawflow's own mousedown handler (bound to this same containerEl - see
  // its `click(e)` method) treats any mousedown not on an INPUT/TEXTAREA/
  // SELECT/contenteditable as the start of a node drag, which swallows
  // clicks on any plain <button> in a node body (set-start/delete/edit-
  // images) - confirmed via source read, not a guess. Intercepting in the
  // capture phase (which always runs before Drawflow's bubble-phase
  // listener on the same element, regardless of registration order) and
  // stopping propagation keeps the click usable while never reaching
  // Drawflow's drag/select logic.
  containerEl.addEventListener('mousedown', function (event) {
    if (event.target.tagName === 'BUTTON') {
      event.stopPropagation();
    }
  }, true);

  containerEl.addEventListener('click', function (event) {
    if (event.target.classList.contains('set-start-btn')) {
      const id = nodeIdFromEventTarget(event.target);
      if (id != null) {
        startNodeId = id;
        applyStartHighlight();
        onDirty();
      }
    } else if (event.target.classList.contains('delete-node-btn')) {
      const id = nodeIdFromEventTarget(event.target);
      if (id != null) {
        if (editingBranchNodeId === id) closeImageEditor();
        editor.removeNodeId('node-' + id);
        if (startNodeId === id) startNodeId = null;
        onDirty();
      }
    } else if (event.target.classList.contains('edit-images-btn')) {
      const id = nodeIdFromEventTarget(event.target);
      if (id != null) openImageEditor(id);
    } else if (event.target.classList.contains('pick-click-region-btn')) {
      const id = nodeIdFromEventTarget(event.target);
      if (id != null) pickClickRegionFlow(id);
    } else if (event.target.classList.contains('show-click-region-btn')) {
      const id = nodeIdFromEventTarget(event.target);
      if (id != null) showClickRegionFlow(id);
    }
  });

  containerEl.addEventListener('keydown', function (event) {
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

  // Drawflow's own container-wide 'input' listener (see updateNodeValue in
  // its source) keeps df-* fields synced into node.data; on top of that we
  // need the action-type select to toggle which fields are visible, and
  // every field to mark the profile dirty.
  containerEl.addEventListener('input', function (event) {
    onDirty();
    if (event.target.matches('[df-action_type]')) {
      updateActionFieldVisibility(event.target.closest('.drawflow-node'));
    }
  });

  editor.on('nodeCreated', function () { onDirty(); });
  editor.on('nodeRemoved', function () { onDirty(); });
  editor.on('connectionCreated', function (payload) {
    enforceSingleOutputConnection(payload);
    onDirty();
  });
  editor.on('connectionRemoved', function () { onDirty(); });

  // Drawflow's own 'contextmenu' event (see its contextmenu(e) method)
  // already preventDefault()s the native browser menu and, when the user
  // had a node/connection selected, shows its own delete-"x" box - that's
  // the existing "right-click a connection, click x to delete" behavior,
  // untouched here. Only show our own node-creation menu when the right-
  // click landed on genuinely empty canvas (not a node), so the two never
  // fight over the same gesture.
  editor.on('contextmenu', function (e) {
    if (!e.target.closest('.drawflow-node')) {
      showGraphContextMenu(e.clientX, e.clientY);
    }
  });

  initImageEditorModal();
  initGraphContextMenu();
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
    if (event.key === 'Escape') hideGraphContextMenu();
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
  const id = editor.addNode(
    'action', 1, 1, pos.x, pos.y, 'action-node',
    defaultActionProperties(), renderActionNodeHtml(),
  );
  updateActionFieldVisibility(document.getElementById('node-' + id));
  return id;
}

function addWaitNode(position) {
  const pos = position || nextSpawnPosition();
  return editor.addNode(
    'wait', 1, 1, pos.x, pos.y, 'wait-node',
    defaultWaitProperties(), renderWaitNodeHtml(),
  );
}

function addBranchNode(position) {
  const pos = position || nextSpawnPosition();
  const id = editor.addNode(
    'branch', 1, 1, pos.x, pos.y, 'branch-node', // 1 output: just 'false', 0 images
    defaultBranchProperties(), renderBranchNodeHtml(),
  );
  renderBranchNodeImageList(id);
  return id;
}

function addBranchWaitNode(position) {
  const pos = position || nextSpawnPosition();
  const id = editor.addNode(
    'branch_wait', 1, 0, pos.x, pos.y, 'branch-wait-node', // 0 outputs, 0 images - no false port ever
    defaultBranchWaitProperties(), renderBranchWaitNodeHtml(),
  );
  renderBranchNodeImageList(id);
  return id;
}

function clearGraphEditor() {
  editor.clear();
  startNodeId = null;
  nextSpawnOffset = 0;
  closeImageEditor();
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

function renderNodeHtml(type) {
  if (type === 'wait') return renderWaitNodeHtml();
  if (type === 'branch') return renderBranchNodeHtml();
  if (type === 'branch_wait') return renderBranchWaitNodeHtml();
  return renderActionNodeHtml();
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
      node.type.replace('_', '-') + '-node', Object.assign({}, node.properties), renderNodeHtml(node.type),
    );
    docIdToDrawflowId[docId] = newId;
    const nodeEl = document.getElementById('node-' + newId);
    if (node.type === 'action') updateActionFieldVisibility(nodeEl);
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
