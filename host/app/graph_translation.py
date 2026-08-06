"""Translates a GraphDocument (host/webui/graph_editor.js's
exportGraphDocument() shape) into the plain-JSON schema MacroRunner
consumes (see engine/runner.py's module docstring).

Originally relocated (as build_engine_graph()) from the old NodeGraphQt
desktop app's MainWindow._build_engine_graph()/_first_connected_node_id()
so it could be unit-tested for the first time; that function and the
NodeGraphQt-node-shaped input it read were removed once the web UI became
the only app.

The _ACTION_KEY_PRESS/_EVAL_MODE_BRANCH constants below are copies of the
string values action_node.py/decision_node.py used to define (now gone
along with those files) - graph_editor.js's own copies
(ACTION_TYPE_KEY_PRESS/EVAL_MODE_BRANCH) must be kept in sync with these
if either ever changes.
"""

_ACTION_KEY_PRESS = 'Key Press'
_EVAL_MODE_BRANCH = 'Branch (True/False)'


def _first_connected_document_node(node, port_name):
    connections = (node.get('connections') or {}).get(port_name) or []
    return connections[0]['node'] if connections else None


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

        elif node_type == 'decision':
            mode = properties.get('evaluation_mode')
            images = properties.get('images') or []
            entry = {
                'type': 'decision',
                'images': [
                    {
                        'reference_path': img['reference_path'],
                        'region': [
                            int(img['region_x']), int(img['region_y']),
                            int(img['region_w']), int(img['region_h']),
                        ],
                        'out': _first_connected_document_node(node, str(i + 1)),
                    }
                    for i, img in enumerate(images)
                ],
                'match_threshold': float(properties.get('match_threshold', 0.85)),
                'evaluation_mode': 'branch' if mode == _EVAL_MODE_BRANCH else 'wait_until_true',
            }
            if entry['evaluation_mode'] == 'branch':
                entry['false'] = _first_connected_document_node(node, 'false')
            else:
                entry['poll_interval_ms'] = int(properties.get('poll_interval_ms', 200))  # matches graph_editor.js's own default
            engine_nodes[node_id] = entry

    return {'start_node': start_node_id, 'nodes': engine_nodes}
