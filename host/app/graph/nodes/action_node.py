from .base import MacroBaseNode

ACTION_CLICK = 'Click'
ACTION_KEY_PRESS = 'Key Press'

MOUSE_BUTTON_LEFT = 'Left'
MOUSE_BUTTON_RIGHT = 'Right'
MOUSE_BUTTON_MIDDLE = 'Middle'


class ActionNode(MacroBaseNode):
    """Performs a click or a key press.

    A click targets a rectangular region (`click_x/y/w/h`, window-relative),
    not a single pixel - the execution engine's cursor.py clicks its center
    (computed as a relative jog from wherever the OS cursor currently is,
    since real HID mice only report relative motion) rather than one exact
    coordinate. `mouse_button` selects which physical button the click
    reports (needed since a real HID mouse click has to name a button, not
    just a screen position)."""

    __identifier__ = 'macro'
    NODE_NAME = 'Action'

    def __init__(self):
        super(ActionNode, self).__init__()
        self.add_input('in', multi_input=True)
        self.add_output('out')

        self.add_combo_menu(
            'action_type', 'Action Type',
            items=[ACTION_CLICK, ACTION_KEY_PRESS],
        )
        self.add_text_input('click_x', 'X (window-relative)', text='0')
        self.add_text_input('click_y', 'Y (window-relative)', text='0')
        self.add_text_input('click_w', 'Width', text='1')
        self.add_text_input('click_h', 'Height', text='1')
        self.add_pick_button('pick_click_region', 'Pick Click Region', 'click_region')
        self.add_pick_button('show_click_region', 'Show Region in Window', 'show_region')
        self.add_combo_menu(
            'mouse_button', 'Mouse Button',
            items=[MOUSE_BUTTON_LEFT, MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE],
        )
        self.add_key_capture('key_combo', 'Key / Combo')

        self._update_field_visibility(self.get_property('action_type'))

    def get_region(self):
        """(x, y, w, h) of the click region, window-relative."""
        return (
            int(self.get_property('click_x')),
            int(self.get_property('click_y')),
            int(self.get_property('click_w')),
            int(self.get_property('click_h')),
        )

    def _update_field_visibility(self, action_type):
        is_click = action_type == ACTION_CLICK
        self.set_field_visible('click_x', is_click)
        self.set_field_visible('click_y', is_click)
        self.set_field_visible('click_w', is_click)
        self.set_field_visible('click_h', is_click)
        self.set_field_visible('pick_click_region', is_click)
        self.set_field_visible('show_click_region', is_click)
        self.set_field_visible('mouse_button', is_click)
        self.set_field_visible('key_combo', not is_click)
        self.redraw()

    def set_property(self, name, value, push_undo=True):
        super(ActionNode, self).set_property(name, value, push_undo=push_undo)
        if name == 'action_type':
            self._update_field_visibility(value)
