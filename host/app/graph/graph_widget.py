from NodeGraphQt import NodeGraph
from Qt import QtCore, QtGui, QtWidgets

from .nodes.action_node import ActionNode
from .nodes.decision_node import DecisionNode
from .nodes.wait_node import WaitNode

DELETABLE_NODE_CLASSES = (ActionNode, DecisionNode, WaitNode)

START_NODE_COLOR = (60, 140, 60)
DEFAULT_NODE_COLOR = (13, 18, 23)


class GraphWidget(QtCore.QObject):
    """Wraps a NodeGraphQt.NodeGraph, adding start-node tracking (see
    MacroBaseNode.is_start_node and docs/nodegraphqt-gotchas.md for why
    it's not tracked by node id) and a single 'dirty' signal that fires on
    any change worth prompting a save for."""

    dirty_changed = QtCore.Signal(bool)
    start_node_changed = QtCore.Signal(object)  # emits the start BaseNode, or None

    def __init__(self, parent=None):
        super(GraphWidget, self).__init__(parent)
        self.graph = NodeGraph()
        self.graph.register_node(ActionNode)
        self.graph.register_node(DecisionNode)
        self.graph.register_node(WaitNode)
        # Macros routinely loop back (e.g. an Action retrying, then
        # re-checking a Decision it's downstream of), so the graph must
        # allow cycles - NodeGraphQt defaults to acyclic=True otherwise.
        self.graph.set_acyclic(False)
        self._build_context_menu()
        self._build_delete_shortcut()

        self._start_node_id = None

        self._dirty = False

        self.graph.node_created.connect(self._mark_dirty)
        self.graph.nodes_deleted.connect(self._on_nodes_deleted)
        self.graph.property_changed.connect(self._mark_dirty)
        self.graph.port_connected.connect(self._mark_dirty)
        self.graph.port_disconnected.connect(self._mark_dirty)

    @property
    def widget(self):
        return self.graph.widget

    def _build_context_menu(self):
        """The right-click canvas menu is empty by default - see
        docs/nodegraphqt-gotchas.md."""
        graph_menu = self.graph.get_context_menu('graph')
        graph_menu.add_command(
            'Add Action Node',
            lambda graph: graph.create_node('macro.ActionNode', pos=self._cursor_scene_pos()),
        )
        graph_menu.add_command(
            'Add Decision Node',
            lambda graph: graph.create_node('macro.DecisionNode', pos=self._cursor_scene_pos()),
        )
        graph_menu.add_command(
            'Add Wait Node',
            lambda graph: graph.create_node('macro.WaitNode', pos=self._cursor_scene_pos()),
        )

        nodes_menu = self.graph.get_context_menu('nodes')
        for node_class in DELETABLE_NODE_CLASSES:
            nodes_menu.add_command(
                'Duplicate', self._duplicate_node,
                node_class=node_class,
            )
            nodes_menu.add_command(
                'Delete', lambda graph, node: graph.delete_node(node),
                node_class=node_class,
            )

    def _build_delete_shortcut(self):
        """NodeGraphQt has no default Delete/Backspace binding - see
        docs/nodegraphqt-gotchas.md."""
        for key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(key), self.graph.viewer())
            shortcut.activated.connect(self._delete_selected_nodes)

    def _delete_selected_nodes(self):
        selected = self.graph.selected_nodes()
        if selected:
            self.graph.delete_nodes(selected)

    def _duplicate_node(self, graph, node):
        """graph.duplicate_nodes() deserializes a copy internally (the same
        way deserialize_session() restores a loaded profile), so it does
        NOT emit node_created - re-emit it ourselves so MainWindow's
        node_created handler still wires up the duplicate (pick handler,
        images dir resolver, thumbnail). Also strip the start-node
        designation the copy would otherwise inherit, since there must be
        only one start node.

        Its connections are dropped too: NodeGraphQt's own connection
        rebuild step falls back to the *original* node's already-connected
        neighbors for any endpoint not part of the just-duplicated set
        (`multi_input=True` on every 'in' port lets that succeed silently),
        so left alone the duplicate would come out wired into the
        original's existing connections rather than as a clean, standalone
        copy."""
        for new_node in graph.duplicate_nodes([node]):
            if hasattr(new_node, 'is_start_node') and new_node.is_start_node():
                new_node.set_property('is_start_node', False, push_undo=False)
                new_node.set_color(*DEFAULT_NODE_COLOR)
            for port in new_node.input_ports() + new_node.output_ports():
                port.clear_connections(push_undo=False)
            graph.node_created.emit(new_node)

    def _cursor_scene_pos(self):
        pos = self.graph.viewer().scene_cursor_pos()
        return (pos.x(), pos.y())

    def _mark_dirty(self, *_args):
        if not self._dirty:
            self._dirty = True
            self.dirty_changed.emit(True)

    def _on_nodes_deleted(self, node_ids):
        self._mark_dirty()
        if self._start_node_id in node_ids:
            self._start_node_id = None
            self.start_node_changed.emit(None)

    def is_dirty(self):
        return self._dirty

    def mark_dirty(self):
        """Public entry point for UI-level changes outside the graph itself
        (e.g. editing a profile's target window title) that should still
        prompt a save."""
        self._mark_dirty()

    def start_node_id(self):
        return self._start_node_id

    def set_start_node(self, node):
        """node may be a BaseNode instance, a node id string, or None to clear."""
        if isinstance(node, str):
            node = self.graph.get_node_by_id(node)

        previous = self.graph.get_node_by_id(self._start_node_id) if self._start_node_id else None
        if previous is not None and previous is not node:
            previous.set_property('is_start_node', False, push_undo=False)
            previous.set_color(*DEFAULT_NODE_COLOR)

        if node is None:
            self._start_node_id = None
            self.start_node_changed.emit(None)
            self._mark_dirty()
            return

        node.set_property('is_start_node', True, push_undo=False)
        node.set_color(*START_NODE_COLOR)
        self._start_node_id = node.id
        self.start_node_changed.emit(node)
        self._mark_dirty()

    def _find_start_node(self):
        for node in self.graph.all_nodes():
            if hasattr(node, 'is_start_node') and node.is_start_node():
                return node
        return None

    def new_graph(self):
        self.graph.clear_session()
        self._start_node_id = None
        self._dirty = False
        self.start_node_changed.emit(None)

    def load_session(self, session_data):
        self.graph.deserialize_session(session_data)
        # deserialize_session() restores the saved 'acyclic' graph flag,
        # which for older profiles may still be the NodeGraphQt default of
        # True - reassert it so loop-back connections stay allowed.
        self.graph.set_acyclic(False)
        start_node = self._find_start_node()
        self._start_node_id = start_node.id if start_node is not None else None
        if start_node is not None:
            start_node.set_color(*START_NODE_COLOR)
        self.start_node_changed.emit(start_node)
        self._dirty = False
        self.dirty_changed.emit(False)

    def serialize(self):
        return self.graph.serialize_session()

    def mark_saved(self):
        self._dirty = False
        self.dirty_changed.emit(False)
