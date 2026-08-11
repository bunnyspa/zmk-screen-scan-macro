"""Pure port/connection-rewiring algorithm for Branch/Branch (Wait)'s
OR-matched image list.

Phase 1 of the PyQt5/NodeGraphQt -> web UI migration (see the approved
migration plan): relocated from the dict-manipulation half of the old
NodeGraphQt desktop app's DecisionNode._sync_output_ports() so it can be
unit-tested for the first time, independent of any NodeGraphQt object
(the values in the connections dicts can be anything - Port objects
today, plain node-id/port-name dicts once the web UI owns graph state
instead of NodeGraphQt - this function only ever moves list entries
between dict keys, never inspects them). Decision was later split into
Branch/Branch (Wait) - this file (originally decision_images.py) is
shared unmodified by both, since the algorithm itself never depended on
which of the two a node ended up being.
"""


def rewire_ports_after_image_change(connections_before, position_mapping, num_images):
    """Rebuilds the {port_name: [connection, ...]} mapping for image ports
    "1".."num_images" plus "false", given `connections_before` (the same
    shape, keyed by the *old* port names) and `position_mapping`
    ({new_index: old_index_or_None} - a newly-added entry maps to None,
    since it has no prior port to inherit connections from).

    `false`'s connections always carry through unchanged - it isn't one of
    the reindexed image ports."""
    new_connections = {}
    for new_index in range(num_images):
        old_index = position_mapping.get(new_index)
        new_connections[str(new_index + 1)] = (
            list(connections_before.get(str(old_index + 1), [])) if old_index is not None else []
        )
    new_connections['false'] = list(connections_before.get('false', []))
    return new_connections
