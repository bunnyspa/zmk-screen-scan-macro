/* Phase 4 of the PyQt5/NodeGraphQt -> web UI migration (see the approved
 * migration plan): the graph editor itself, built on Drawflow (vendored at
 * vendor/drawflow/ - see the plan's library-choice section for why Rete.js/
 * Baklava.js were ruled out: both require React/Vue + a bundler, which this
 * app deliberately has none of).
 *
 * Owns everything about *editing* the graph (see the plan's "Runtime
 * responsibilities" section) - node/port/connection rendering, property
 * fields (bound directly via Drawflow's df-* attributes, no separate side
 * panel), the exclusive "start node" flag, and packaging/unpacking the
 * GraphDocument JSON (index.html owns save/load plumbing and calls the two
 * exported functions below). Only Action and Wait node types this phase;
 * Decision arrives in Phase 5.
 *
 * Deliberately NOT wired to any bridge.* calls yet: click-region picking
 * (the "Pick Click Region"/"Show Region in Window" buttons that exist in
 * the old NodeGraphQt ActionNode) needs the native overlay bridge methods
 * from Phase 6, so for now Action's click region is four plain number
 * inputs. Key/combo capture is done in-browser (keydown listener below) -
 * no bridge round-trip needed for that one.
 *
 * Phase 5 adds the Decision node: match_threshold/evaluation_mode/
 * poll_interval_ms are plain df-* bound fields like the other node types,
 * but its `images` list and per-image output ports ("1".."N" + "false")
 * need real bridge round-trips (image upload/masking is OpenCV work that
 * only exists in Python, and the port-rewiring algorithm on add/delete/
 * move is decision_images.py - already unit-tested in Python, so it's
 * called through the bridge rather than re-implemented untested here).
 * The image-management UI is a single shared modal (#image-editor-modal
 * in index.html), repointed at whichever Decision node is open at a time
 * (editingDecisionNodeId) rather than one dialog instance per node - this
 * app only ever has one such dialog open at once anyway. "Show Region in
 * Window" (the old app's per-image preview button) is deferred to Phase 6
 * along with Action's region-picking, for the same reason - no overlay
 * bridge yet.
 */

// These string values must match graph_translation.py's _ACTION_KEY_PRESS/
// _EVAL_MODE_BRANCH exactly - they're stored verbatim in
// properties.action_type/evaluation_mode and read back by
// build_engine_graph_from_document() when translating a GraphDocument to
// the engine schema. Kept in sync by convention, not by import (no
// Qt-importing code in this file).
const ACTION_TYPE_CLICK = 'Click';
const ACTION_TYPE_KEY_PRESS = 'Key Press';
const EVAL_MODE_BRANCH = 'Branch (True/False)';
const EVAL_MODE_WAIT = 'Wait Until True';
const MODIFIER_KEYS = ['Control', 'Alt', 'Shift', 'Meta'];

let editor = null;
let startNodeId = null;
let nextSpawnOffset = 0;
let editingDecisionNodeId = null;
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

function defaultDecisionProperties() {
  return { images: [], match_threshold: 0.85, evaluation_mode: EVAL_MODE_BRANCH, poll_interval_ms: 200 };
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

function renderDecisionNodeHtml() {
  return (
    '<div class="node-header">' +
      '<span class="node-type-label">Decision</span>' +
      '<button type="button" class="icon-btn set-start-btn" title="Set as start node">☆</button>' +
      '<button type="button" class="icon-btn delete-node-btn" title="Delete node">×</button>' +
    '</div>' +
    '<label>Match Threshold <input type="number" df-match_threshold min="0" max="1" step="0.01"></label>' +
    '<label>Evaluation Mode' +
      '<select df-evaluation_mode>' +
        '<option value="' + EVAL_MODE_BRANCH + '">' + EVAL_MODE_BRANCH + '</option>' +
        '<option value="' + EVAL_MODE_WAIT + '">' + EVAL_MODE_WAIT + '</option>' +
      '</select>' +
    '</label>' +
    '<div class="decision-poll-field">' +
      '<label>Poll Interval (ms) <input type="number" df-poll_interval_ms min="10"></label>' +
    '</div>' +
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

function updateDecisionFieldVisibility(nodeEl) {
  const select = nodeEl.querySelector('[df-evaluation_mode]');
  if (!select) return;
  const pollField = nodeEl.querySelector('.decision-poll-field');
  if (pollField) pollField.style.display = select.value === EVAL_MODE_WAIT ? '' : 'none';
}

const DECISION_THUMB_SIZE = 20;
const DECISION_THUMB_GAP = 4;

function ensureDecisionThumbOverlay(nodeEl) {
  let overlay = nodeEl.querySelector(':scope > .decision-thumb-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'decision-thumb-overlay';
    nodeEl.appendChild(overlay);
  }
  return overlay;
}

// Positions one small thumbnail directly beside each image's corresponding
// output port circle ("false" gets none), measured from the ports' actual
// rendered position (getBoundingClientRect()) rather than derived from
// Drawflow's internal layout math (the .outputs column is centered as a
// whole block within the node's height, not flush to its top - not worth
// reverse-engineering when the real position is one measurement away).
// Dividing by editor.zoom keeps this aligned at any zoom level, since rect
// measurements come back in already-scaled screen pixels but the
// thumbnails are positioned in the same unscaled coordinate space as the
// node itself (both live inside Drawflow's zoom/pan transform, so a raw
// scaled pixel delta would double-apply the zoom otherwise).
function renderDecisionNodeImageList(nodeId) {
  const nodeEl = document.getElementById('node-' + nodeId);
  if (!nodeEl) return;
  const overlay = ensureDecisionThumbOverlay(nodeEl);
  const images = decisionImagesOf(nodeId);
  const outputEls = nodeEl.querySelectorAll('.outputs .output');
  const nodeRect = nodeEl.getBoundingClientRect();
  const zoom = editor.zoom || 1;

  overlay.innerHTML = images.map(function (image, index) {
    const portEl = outputEls[index];
    if (!portEl) return '';
    const portRect = portEl.getBoundingClientRect();
    const top = (portRect.top - nodeRect.top) / zoom;
    const left = (portRect.left - nodeRect.left) / zoom - DECISION_THUMB_SIZE - DECISION_THUMB_GAP;
    const thumbUrl = imageThumbnailUrls[image.reference_path] || '';
    return '<img class="decision-node-thumb" style="top: ' + top + 'px; left: ' + left + 'px;" ' +
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
        if (editingDecisionNodeId === id) closeImageEditor();
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
    } else if (event.target.matches('[df-evaluation_mode]')) {
      const nodeEl = event.target.closest('.drawflow-node');
      updateDecisionFieldVisibility(nodeEl);
      // Showing/hiding the Poll Interval field changes the node's height,
      // which shifts where Drawflow vertically centers the .outputs column
      // - the thumbnail positions computed against the old height are now
      // stale (confirmed via a real screenshot: switching to "Wait Until
      // True" left the thumbnails pinned to where the ports used to be).
      renderDecisionNodeImageList(nodeIdFromEventTarget(nodeEl));
    }
  });

  editor.on('nodeCreated', function () { onDirty(); });
  editor.on('nodeRemoved', function () { onDirty(); });
  editor.on('connectionCreated', function (payload) {
    enforceSingleOutputConnection(payload);
    onDirty();
  });
  editor.on('connectionRemoved', function () { onDirty(); });

  initImageEditorModal();
}

// Every output port here means "go to exactly one next node" (matches
// build_engine_graph()'s _first_connected_node_id(), which only ever reads
// the first connection - see graph_translation.py). Drawflow itself places
// no limit on connections per output, so a new connection from a port that
// already had one silently replaces it rather than fanning out, to avoid a
// second wire that the engine would just ignore.
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

function addActionNode() {
  const pos = nextSpawnPosition();
  const id = editor.addNode(
    'action', 1, 1, pos.x, pos.y, 'action-node',
    defaultActionProperties(), renderActionNodeHtml(),
  );
  updateActionFieldVisibility(document.getElementById('node-' + id));
  return id;
}

function addWaitNode() {
  const pos = nextSpawnPosition();
  return editor.addNode(
    'wait', 1, 1, pos.x, pos.y, 'wait-node',
    defaultWaitProperties(), renderWaitNodeHtml(),
  );
}

function addDecisionNode() {
  const pos = nextSpawnPosition();
  const id = editor.addNode(
    'decision', 1, 1, pos.x, pos.y, 'decision-node', // 1 output: just 'false', 0 images
    defaultDecisionProperties(), renderDecisionNodeHtml(),
  );
  updateDecisionFieldVisibility(document.getElementById('node-' + id));
  renderDecisionNodeImageList(id);
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
  if (type === 'decision') return renderDecisionNodeHtml();
  return renderActionNodeHtml();
}

/* GraphDocument (see the plan's schema) <-> Drawflow's own export() shape.
 * Action/Wait nodes always have one input ("in") and one output ("out");
 * Decision has one input and N+1 outputs named "1".."N" (image priority
 * order) + "false", which map onto Drawflow's output_1..output_(N+1) in
 * that same order - an invariant this module always maintains (see
 * rebuildDecisionOutputPorts()), so no separate name-mapping needs storing. */
function loadGraphDocument(doc, imageThumbnails) {
  clearGraphEditor();
  mergeImageThumbnails(imageThumbnails);
  const nodes = (doc && doc.nodes) || {};
  const docIds = Object.keys(nodes);
  const docIdToDrawflowId = {};

  docIds.forEach(function (docId) {
    const node = nodes[docId];
    const numImages = node.type === 'decision' ? (node.properties.images || []).length : 0;
    const numOutputs = node.type === 'decision' ? numImages + 1 : 1;
    const newId = editor.addNode(
      node.type, 1, numOutputs, node.position[0], node.position[1],
      node.type + '-node', Object.assign({}, node.properties), renderNodeHtml(node.type),
    );
    docIdToDrawflowId[docId] = newId;
    const nodeEl = document.getElementById('node-' + newId);
    if (node.type === 'action') updateActionFieldVisibility(nodeEl);
    if (node.type === 'decision') {
      updateDecisionFieldVisibility(nodeEl);
      renderDecisionNodeImageList(newId);
    }
  });

  Object.keys(nodes).forEach(function (docId) {
    const node = nodes[docId];
    const sourceId = docIdToDrawflowId[docId];
    const connectionsByPort = node.connections || {};
    const portNames = node.type === 'decision'
      ? Array.from({ length: (node.properties.images || []).length }, function (_, i) { return String(i + 1); }).concat(['false'])
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
    const outputNames = Object.keys(raw.outputs); // already in output_1..output_N order (see rebuildDecisionOutputPorts)
    const portNames = raw.name === 'decision'
      ? outputNames.slice(0, -1).map(function (_, i) { return String(i + 1); }).concat(['false'])
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

/* --- Decision node: images list + per-image output ports --- */

function mergeImageThumbnails(map) {
  Object.assign(imageThumbnailUrls, map || {});
}

function decisionImagesOf(nodeId) {
  return editor.getNodeFromId(nodeId).data.images || [];
}

// Reads the node's *current* output ports back into the same
// {portName: [{node, port}]} shape GraphDocument.connections uses, relying
// on the output_k <-> port-name invariant described above loadGraphDocument().
function currentDecisionConnections(nodeId) {
  const node = editor.getNodeFromId(nodeId);
  const numOutputs = Object.keys(node.outputs).length;
  const numImages = numOutputs - 1;
  const result = {};
  for (let i = 0; i < numImages; i++) {
    const conns = (node.outputs['output_' + (i + 1)] || {}).connections || [];
    result[String(i + 1)] = conns.map(function (c) { return { node: String(c.node), port: 'in' }; });
  }
  const falseConns = (node.outputs['output_' + numOutputs] || {}).connections || [];
  result.false = falseConns.map(function (c) { return { node: String(c.node), port: 'in' }; });
  return result;
}

// Rebuilds every output port from scratch (mirrors the old
// DecisionNode._sync_output_ports()'s "recompute from scratch is simpler
// than incremental rename/reconnect" approach) from newConnections (as
// returned by decision_images.rewire_ports_after_image_change() via the
// bridge). Removing "output_1" numImages+1 times is deliberate, not a typo -
// Drawflow's removeNodeOutput() renumbers remaining ports down after each
// removal (confirmed via source read), so the first remaining port is
// always named output_1 regardless of how many removals have happened.
function rebuildDecisionOutputPorts(nodeId, newConnections, numImages) {
  const node = editor.getNodeFromId(nodeId);
  const existingCount = Object.keys(node.outputs).length;
  for (let i = 0; i < existingCount; i++) {
    editor.removeNodeOutput(nodeId, 'output_1');
  }
  for (let i = 0; i < numImages + 1; i++) {
    editor.addNodeOutput(nodeId);
  }
  for (let i = 0; i < numImages; i++) {
    (newConnections[String(i + 1)] || []).forEach(function (conn) {
      editor.addConnection(nodeId, conn.node, 'output_' + (i + 1), 'input_1');
    });
  }
  (newConnections.false || []).forEach(function (conn) {
    editor.addConnection(nodeId, conn.node, 'output_' + (numImages + 1), 'input_1');
  });
}

// Shared by add/delete/move: apply a new images[] + the position_mapping
// that describes it (same {new_index: old_index_or_null} shape the old
// NodeGraphQt desktop app's add/delete/move-image handlers built), via
// decision_images.rewire_ports_after_image_change() (Python, tested)
// through the bridge, then rebuild ports/UI to match.
function applyDecisionImagesChange(nodeId, newImages, positionMapping) {
  const oldConnections = currentDecisionConnections(nodeId);
  return pywebview.api.rewire_decision_ports(oldConnections, positionMapping, newImages.length).then(function (newConnections) {
    const data = Object.assign({}, editor.getNodeFromId(nodeId).data, { images: newImages });
    editor.updateNodeDataFromId(nodeId, data);
    rebuildDecisionOutputPorts(nodeId, newConnections, newImages.length);
    renderDecisionNodeImageList(nodeId);
    renderImageEditorList();
    setDirty(true);
  });
}

function addDecisionImageFlow() {
  const nodeId = editingDecisionNodeId;
  pywebview.api.add_decision_image(currentProfile, nodeId).then(function (result) {
    if (!result.ok) {
      if (!result.cancelled) showError(result.error);
      return;
    }
    imageThumbnailUrls[result.image.reference_path] = result.thumbnail_url;
    const images = decisionImagesOf(nodeId);
    const mapping = {};
    for (let i = 0; i < images.length; i++) mapping[i] = i;
    mapping[images.length] = null;
    applyDecisionImagesChange(nodeId, images.concat([result.image]), mapping);
  });
}

function deleteDecisionImage(index) {
  const nodeId = editingDecisionNodeId;
  const images = decisionImagesOf(nodeId);
  const newImages = images.slice(0, index).concat(images.slice(index + 1));
  const mapping = {};
  for (let newIndex = 0; newIndex < newImages.length; newIndex++) {
    mapping[newIndex] = newIndex < index ? newIndex : newIndex + 1;
  }
  applyDecisionImagesChange(nodeId, newImages, mapping);
}

function moveDecisionImage(index, delta) {
  const nodeId = editingDecisionNodeId;
  const images = decisionImagesOf(nodeId);
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
  applyDecisionImagesChange(nodeId, newImages, mapping);
}

function openImageEditor(nodeId) {
  editingDecisionNodeId = nodeId;
  renderImageEditorList();
  document.getElementById('image-editor-modal').style.display = 'flex';
}

function closeImageEditor() {
  editingDecisionNodeId = null;
  const modal = document.getElementById('image-editor-modal');
  if (modal) modal.style.display = 'none';
}

function renderImageEditorList() {
  if (editingDecisionNodeId == null) return;
  const images = decisionImagesOf(editingDecisionNodeId);
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
    row.querySelector('.move-left-btn').addEventListener('click', function () { moveDecisionImage(index, -1); });
    row.querySelector('.move-right-btn').addEventListener('click', function () { moveDecisionImage(index, 1); });
    row.querySelector('.show-image-region-btn').addEventListener('click', function () { showImageRegionFlow(index); });
    row.querySelector('.delete-image-btn').addEventListener('click', function () { deleteDecisionImage(index); });
    list.appendChild(row);
  });
}

function initImageEditorModal() {
  document.getElementById('image-editor-close-btn').addEventListener('click', closeImageEditor);
  document.getElementById('image-editor-add-btn').addEventListener('click', addDecisionImageFlow);
}

/* --- Click-region picker / show-region preview (Phase 6b) ---
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
  const image = decisionImagesOf(editingDecisionNodeId)[index];
  const result = await pywebview.api.show_reference_region(
    currentProfile, currentTargetWindowTitle(), image.reference_path, image.region_x, image.region_y,
  );
  if (!result.ok) showError(result.error);
}
