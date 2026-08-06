"""Tests for graph_translation.py's build_engine_graph_from_document() -
needs no fakes at all, since a GraphDocument is already a plain dict,
exactly what graph_editor.js's exportGraphDocument() produces."""
from app.graph_translation import build_engine_graph_from_document


def test_document_no_start_node_id_returns_none():
    assert build_engine_graph_from_document({'nodes': {}}) is None
    assert build_engine_graph_from_document({}) is None
    assert build_engine_graph_from_document(None) is None


def test_document_action_key_press():
    doc = {
        'start_node_id': 'n1',
        'nodes': {
            'n1': {
                'type': 'action',
                'properties': {'action_type': 'Key Press', 'key_combo': 'a'},
                'connections': {'out': []},
            },
        },
    }

    result = build_engine_graph_from_document(doc)

    assert result['start_node'] == 'n1'
    assert result['nodes']['n1'] == {
        'type': 'action', 'action_type': 'key_press', 'key_combo': 'a', 'out': None,
    }


def test_document_action_click():
    doc = {
        'start_node_id': 'n1',
        'nodes': {
            'n1': {
                'type': 'action',
                'properties': {
                    'action_type': 'Click', 'click_x': 1, 'click_y': 2, 'click_w': 3, 'click_h': 4,
                    'mouse_button': 'Right',
                },
                'connections': {'out': [{'node': 'n2', 'port': 'in'}]},
            },
            'n2': {'type': 'action', 'properties': {'action_type': 'Key Press', 'key_combo': ''},
                   'connections': {'out': []}},
        },
    }

    result = build_engine_graph_from_document(doc)

    assert result['nodes']['n1'] == {
        'type': 'action', 'action_type': 'click',
        'click_rect': [1, 2, 3, 4], 'mouse_button': 'right', 'out': 'n2',
    }


def test_document_wait_node():
    doc = {
        'start_node_id': 'n1',
        'nodes': {'n1': {'type': 'wait', 'properties': {'duration_ms': '500'}, 'connections': {'out': []}}},
    }

    result = build_engine_graph_from_document(doc)

    assert result['nodes']['n1'] == {'type': 'wait', 'duration_ms': 500, 'out': None}


def test_document_decision_branch_mode_with_images_and_false():
    doc = {
        'start_node_id': 'd1',
        'nodes': {
            'd1': {
                'type': 'decision',
                'properties': {
                    'evaluation_mode': 'Branch (True/False)',
                    'match_threshold': '0.9',
                    'images': [
                        {'reference_path': 'a.png', 'region_x': 0, 'region_y': 0, 'region_w': 10, 'region_h': 10},
                        {'reference_path': 'b.png', 'region_x': 5, 'region_y': 5, 'region_w': 8, 'region_h': 8},
                    ],
                },
                'connections': {
                    '1': [{'node': 'done', 'port': 'in'}],
                    '2': [],
                    'false': [{'node': 'done', 'port': 'in'}],
                },
            },
            'done': {'type': 'action', 'properties': {'action_type': 'Key Press', 'key_combo': ''},
                     'connections': {'out': []}},
        },
    }

    result = build_engine_graph_from_document(doc)

    assert result['nodes']['d1'] == {
        'type': 'decision',
        'images': [
            {'reference_path': 'a.png', 'region': [0, 0, 10, 10], 'out': 'done'},
            {'reference_path': 'b.png', 'region': [5, 5, 8, 8], 'out': None},
        ],
        'match_threshold': 0.9,
        'evaluation_mode': 'branch',
        'false': 'done',
    }


def test_document_decision_wait_until_true_mode_has_poll_interval_not_false():
    doc = {
        'start_node_id': 'd1',
        'nodes': {
            'd1': {
                'type': 'decision',
                'properties': {
                    'evaluation_mode': 'Wait Until True',
                    'match_threshold': '0.85',
                    'poll_interval_ms': '200',
                    'images': [{'reference_path': 'a.png', 'region_x': 0, 'region_y': 0, 'region_w': 1, 'region_h': 1}],
                },
                'connections': {'1': []},
            },
        },
    }

    result = build_engine_graph_from_document(doc)

    entry = result['nodes']['d1']
    assert entry['evaluation_mode'] == 'wait_until_true'
    assert entry['poll_interval_ms'] == 200
    assert 'false' not in entry
