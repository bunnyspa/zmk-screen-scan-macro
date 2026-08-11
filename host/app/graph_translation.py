"""Translates a GraphDocument (host/webui/graph_editor.js's
exportGraphDocument() shape) into the plain-JSON schema MacroRunner
consumes (see engine/runner.py's module docstring).

Originally relocated (as build_engine_graph()) from the old NodeGraphQt
desktop app's MainWindow._build_engine_graph()/_first_connected_node_id()
so it could be unit-tested for the first time; that function and the
NodeGraphQt-node-shaped input it read were removed once the web UI became
the only app.

The _ACTION_KEY_PRESS constant below is a copy of the string value
action_node.py used to define (now gone along with that file) -
graph_editor.js's own copy (ACTION_TYPE_KEY_PRESS) must be kept in sync
with it if it ever changes.

branch/branch_wait (formerly one "decision" node type with an
evaluation_mode field) were split so a node's port shape - specifically,
whether a trailing false port exists - is a fixed property of its type,
not something that changes with a mutable per-instance mode; see
engine/runner.py's module docstring for the full schema.
"""

_ACTION_KEY_PRESS = 'Key Press'


def _first_connected_document_node(node, port_name):
    connections = (node.get('connections') or {}).get(port_name) or []
    return connections[0]['node'] if connections else None


def _translate_decision_images(node, images):
    """Shared by the 'branch'/'branch_wait' translations below - both
    keep the exact same multi-image OR-matching shape (checked in list
    order, first match wins, each image keeps its own 'out' target),
    differing only in whether a 'false' port exists."""
    return [
        {
            'reference_path': img['reference_path'],
            'region': [
                int(img['region_x']), int(img['region_y']),
                int(img['region_w']), int(img['region_h']),
            ],
            'out': _first_connected_document_node(node, str(i + 1)),
        }
        for i, img in enumerate(images)
    ]


def build_engine_graph_from_document(graph_document):
    """Returns the plain-JSON engine graph dict {'start_node': ...,
    'nodes': {...}}, or None if no start node is designated
    (graph_document has no start_node_id)."""
    graph_document = graph_document or {}
    start_node_id = graph_document.get('start_node_id')
    if not start_node_id:
        return None

    engine_nodes = {}
    for node_id, node in (graph_document.get('nodes') or {}).items():
        node_type = node.get('type')
        properties = node.get('properties') or {}

        if node_type == 'action':
            entry = {'out': _first_connected_document_node(node, 'out')}
            if properties.get('action_type') == _ACTION_KEY_PRESS:
                entry['type'] = 'action'
                entry['action_type'] = 'key_press'
                entry['key_combo'] = properties.get('key_combo')
            else:
                entry['type'] = 'action'
                entry['action_type'] = 'click'
                entry['click_rect'] = [
                    int(properties.get('click_x', 0)), int(properties.get('click_y', 0)),
                    int(properties.get('click_w', 1)), int(properties.get('click_h', 1)),
                ]
                entry['mouse_button'] = str(properties.get('mouse_button', 'Left')).lower()
            engine_nodes[node_id] = entry

        elif node_type == 'wait':
            engine_nodes[node_id] = {
                'type': 'wait',
                'duration_ms': int(properties.get('duration_ms', 0)),
                'out': _first_connected_document_node(node, 'out'),
            }

        elif node_type == 'branch':
            engine_nodes[node_id] = {
                'type': 'branch',
                'images': _translate_decision_images(node, properties.get('images') or []),
                'match_threshold': float(properties.get('match_threshold', 0.85)),
                'false': _first_connected_document_node(node, 'false'),
            }

        elif node_type == 'branch_wait':
            engine_nodes[node_id] = {
                'type': 'branch_wait',
                'images': _translate_decision_images(node, properties.get('images') or []),
                'match_threshold': float(properties.get('match_threshold', 0.85)),
                # matches graph_editor.js's own default
                'poll_interval_ms': int(properties.get('poll_interval_ms', 200)),
            }

    return {'start_node': start_node_id, 'nodes': engine_nodes}
