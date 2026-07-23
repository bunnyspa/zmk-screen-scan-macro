import os
import shutil
import time

import cv2
from NodeGraphQt.widgets.node_widgets import NodeButton
from Qt import QtWidgets

from .base import MacroBaseNode
from .reference_processing import MaskDetectionError, process_masked_reference

EVAL_MODE_BRANCH = 'Branch (True/False)'
EVAL_MODE_WAIT = 'Wait Until True'

THUMBNAIL_PROP = 'reference_thumbnail'


class DecisionNode(MacroBaseNode):
    """Condition is a match of the reference image against the live
    captured frame. The reference image is browsed in from disk, and is
    expected to carry its own mask - either a 0-alpha channel, or a single
    flat color painted over anything that should be ignored (border
    and/or interior "holes").

    That mask is only ever processed once, at browse time (not per-frame):
    process_masked_reference() crops the image down to the bounding box of
    its non-ignored pixels and folds the mask into that crop's own alpha
    channel. The resulting smaller, alpha-masked image (`reference_path`)
    is what the execution engine actually matches against (see
    engine/matcher.py); the untouched original upload (`reference_full_path`)
    is kept only so the node can show it on request. That same crop's
    bounding box within the source image is saved as `region_x/y/w/h` -
    assuming the user uploads a full, unmodified screenshot of the target
    window (not an arbitrary crop from elsewhere), this is exactly where
    the content sits in the window, with no separate live matching pass
    needed to locate it (see "Show Region in Window").

    `evaluation_mode` controls how the execution engine treats this node:
      - Branch (True/False): evaluate once, take the 'true' or 'false' output.
      - Wait Until True: keep re-evaluating until it matches, then take the
        'true' output. The 'false' output port is hidden (and disconnected)
        in this mode, since it's never used."""

    __identifier__ = 'macro'
    NODE_NAME = 'Decision'

    def __init__(self):
        super(DecisionNode, self).__init__()
        self.add_input('in', multi_input=True)
        self.add_output('true')
        self.add_output('false')

        self._images_dir_resolver = None

        self.add_image_thumbnail(THUMBNAIL_PROP)
        self.add_text_input('reference_path', 'Reference Image', text='')
        self.create_property('reference_full_path', '')  # no widget; popup-only
        # bounding box of the crop within the originally uploaded image -
        # no widgets, these are derived automatically at browse time, not
        # meant to be hand-edited
        self.create_property('region_x', '0')
        self.create_property('region_y', '0')
        self.create_property('region_w', '0')
        self.create_property('region_h', '0')
        self._add_browse_button()
        self.add_pick_button('show_decision_region', 'Show Region in Window', 'show_region')
        self.add_spinbox(
            'match_threshold', 'Match Threshold',
            value=0.85, min_value=0.0, max_value=1.0, double=True,
        )

        self.add_combo_menu(
            'evaluation_mode', 'Evaluation Mode',
            items=[EVAL_MODE_BRANCH, EVAL_MODE_WAIT],
        )
        self.add_spinbox(
            'poll_interval_ms', 'Poll Interval (ms)',
            value=200, min_value=10, max_value=60000,
        )

        self._update_false_port_visibility(self.get_property('evaluation_mode'))
        self._update_poll_interval_visibility(self.get_property('evaluation_mode'))

    def _add_browse_button(self):
        widget = NodeButton(self.view, 'browse_reference', '', 'Browse Reference Image...')
        self.add_custom_widget(widget)
        widget.get_custom_widget().clicked.connect(self._browse_reference)

    def set_images_dir_resolver(self, resolver):
        """resolver() -> absolute path to the current profile's images/
        folder, or None if no profile is open. Injected by MainWindow so
        this node doesn't need to know about ProfileManager directly."""
        self._images_dir_resolver = resolver

    def _browse_reference(self):
        parent = QtWidgets.QApplication.activeWindow()
        images_dir = self._images_dir_resolver() if self._images_dir_resolver else None
        if images_dir is None:
            QtWidgets.QMessageBox.warning(
                parent, 'No Profile',
                'Select or create a profile before choosing a reference image.',
            )
            return

        src_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent, 'Select Reference Image', '',
            'Images (*.png *.jpg *.jpeg *.bmp)',
        )
        if not src_path:
            return

        try:
            cropped_bgra, bounding_box, _kept_pixels = process_masked_reference(src_path)
        except MaskDetectionError as exc:
            QtWidgets.QMessageBox.warning(parent, 'Reference Image', str(exc))
            return

        stem = f"{self.id.replace('0x', '')}_{int(time.time() * 1000)}"
        full_filename = f"{stem}_full{os.path.splitext(src_path)[1] or '.png'}"
        cropped_filename = f"{stem}_cropped.png"
        full_dest_abs = os.path.join(images_dir, full_filename)
        cropped_dest_abs = os.path.join(images_dir, cropped_filename)

        shutil.copyfile(src_path, full_dest_abs)
        cv2.imwrite(cropped_dest_abs, cropped_bgra)

        self.set_property('reference_path', os.path.join('images', cropped_filename))
        self.set_property('reference_full_path', os.path.join('images', full_filename))
        region_x, region_y, region_w, region_h = bounding_box
        self.set_property('region_x', str(region_x))
        self.set_property('region_y', str(region_y))
        self.set_property('region_w', str(region_w))
        self.set_property('region_h', str(region_h))
        self.refresh_thumbnail(THUMBNAIL_PROP, cropped_dest_abs, full_dest_abs)

    def resolve_thumbnail(self):
        """Re-derives the thumbnail's absolute paths from the current
        reference_path/reference_full_path + images dir resolver, and
        refreshes the display. Needed after a profile load, since
        deserialize_session() restores properties directly on the model
        without going through set_property(), so it can't trigger a
        refresh on its own."""
        self.refresh_thumbnail(
            THUMBNAIL_PROP,
            self.get_reference_abs_path(),
            self._resolve_abs_path(self.get_property('reference_full_path')),
        )

    def get_reference_abs_path(self):
        """Absolute path to the processed (cropped) reference image - the
        one the execution engine actually matches against - or None if no
        reference image has been browsed in yet, or no profile is open to
        resolve the path against."""
        return self._resolve_abs_path(self.get_property('reference_path'))

    def get_region(self):
        """(x, y, w, h) of the reference image's content within the
        originally uploaded screenshot. (0, 0, 0, 0) if no reference image
        has been browsed in yet - region_x/y/w/h default to '0'."""
        return (
            int(self.get_property('region_x')),
            int(self.get_property('region_y')),
            int(self.get_property('region_w')),
            int(self.get_property('region_h')),
        )

    def _resolve_abs_path(self, relative_path):
        images_dir = self._images_dir_resolver() if self._images_dir_resolver else None
        if not images_dir or not relative_path:
            return None
        profile_dir = os.path.dirname(images_dir)
        return os.path.join(profile_dir, relative_path)

    def _update_false_port_visibility(self, evaluation_mode):
        false_port = self.get_output('false')
        if evaluation_mode == EVAL_MODE_WAIT:
            false_port.clear_connections(push_undo=False)
        false_port.set_visible(evaluation_mode != EVAL_MODE_WAIT, push_undo=False)

    def _update_poll_interval_visibility(self, evaluation_mode):
        """poll_interval_ms only means anything in Wait Until True mode -
        Branch mode evaluates once and never polls."""
        self.set_field_visible('poll_interval_ms', evaluation_mode == EVAL_MODE_WAIT)
        self.redraw()

    def set_property(self, name, value, push_undo=True):
        super(DecisionNode, self).set_property(name, value, push_undo=push_undo)
        if name == 'evaluation_mode':
            self._update_false_port_visibility(value)
            self._update_poll_interval_visibility(value)
