from NodeGraphQt import BaseNode
from NodeGraphQt.widgets.node_widgets import NodeButton

from .widgets import NodeImageThumbnail, NodeKeySequenceEdit

# NodeLineEdit's default stylesheet uses a near-transparent background
# (alpha=20), which reads as a plain label rather than an editable field.
# Give our text inputs an opaque background/border to match the combo boxes.
TEXT_INPUT_STYLE = """
QLineEdit {
    background-color: rgba(40, 40, 40, 200);
    border: 1px solid rgba(100, 100, 100, 255);
    border-radius: 3px;
    color: rgba(255, 255, 255, 180);
    padding: 2px 4px;
}
QLineEdit:focus {
    border: 1px solid rgba(150, 150, 150, 255);
}
"""


class MacroBaseNode(BaseNode):
    """Common helpers shared by all macro node types."""

    def __init__(self):
        super(MacroBaseNode, self).__init__()
        # Hidden property (no widget) marking the graph's start node -
        # not tracked by node id, which doesn't survive a reload (see
        # docs/nodegraphqt-gotchas.md).
        self.create_property('is_start_node', False)
        self._pick_handler = None

    def is_start_node(self):
        return bool(self.get_property('is_start_node'))

    def add_text_input(self, name, label='', text='', placeholder_text='',
                       tooltip=None, tab=None):
        super(MacroBaseNode, self).add_text_input(
            name, label=label, text=text, placeholder_text=placeholder_text,
            tooltip=tooltip, tab=tab,
        )
        self.get_widget(name).get_custom_widget().setStyleSheet(TEXT_INPUT_STYLE)

    def set_pick_handler(self, handler):
        """handler(node, mode) is called when the user clicks one of this
        node's 'Pick ...' buttons, so the UI layer can arm the preview
        panel's region picker for this specific node/field."""
        self._pick_handler = handler

    def _request_pick(self, mode):
        if self._pick_handler is not None:
            self._pick_handler(self, mode)

    def add_pick_button(self, name, text, mode):
        """Embeds a button that arms the preview panel's region/point
        picker for this node's `mode` field. Uses add_custom_widget(), not
        add_button() - see docs/nodegraphqt-gotchas.md."""
        widget = NodeButton(self.view, name, '', text)
        self.add_custom_widget(widget)
        widget.get_custom_widget().clicked.connect(lambda: self._request_pick(mode))

    def add_image_thumbnail(self, name):
        """Embeds a read-only image preview. Call refresh_thumbnail(name, path)
        to update what it shows; its own 'value' is never read back, it's
        only present so add_custom_widget() creates a backing property
        (see docs/nodegraphqt-gotchas.md)."""
        widget = NodeImageThumbnail(self.view, name, '')
        self.add_custom_widget(widget)

    def add_key_capture(self, name, label=''):
        """Embeds a key/combo field the user sets by focusing it and
        pressing the key(s), instead of typing a key-name string."""
        widget = NodeKeySequenceEdit(self.view, name, label)
        self.add_custom_widget(widget)

    def refresh_thumbnail(self, name, cropped_abs_path, full_abs_path=None):
        widget = self.get_widget(name)
        if widget is not None:
            widget.set_value(cropped_abs_path or '')
            widget.set_full_image_path(full_abs_path)

    def get_config(self):
        """Returns all custom property values as a plain dict."""
        return dict(self.model.custom_properties)

    def set_config(self, config):
        """Writes a plain dict of custom property values back onto the node."""
        for name, value in config.items():
            if self.has_property(name):
                self.set_property(name, value, push_undo=False)

    def set_field_visible(self, name, visible):
        widget = self.get_widget(name)
        if widget is not None:
            widget.setVisible(visible)

    def redraw(self):
        """Call after a batch of set_field_visible() calls - visibility
        changes don't auto-resize the node (see docs/nodegraphqt-gotchas.md)."""
        self.view.draw_node()
