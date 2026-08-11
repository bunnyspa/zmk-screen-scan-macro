"""The pywebview js_api bridge - the only app-logic module allowed to
import `webview` (see the approved migration plan's §3/§5). main.py's
own entrypoint bootstrap (create_window()/start()) is the one necessary
exception - something has to construct the window this bridge is passed
into as js_api, so it can't itself be the one calling create_window().

Phase 3: profile CRUD, backed directly by the existing, already
framework-agnostic ProfileManager/profile_store (host/app/profiles/) -
reused as-is, no changes. Every method here is a thin wrapper: catch
ProfileError, return {'ok': False, 'error': str(exc)} instead of raising,
since there's no QMessageBox on this side of the bridge - the frontend
renders the error itself (see index.html's showError()).

Phase 5 adds Branch/Branch (Wait) image handling: add_branch_image() does
the same native-file-dialog + process_masked_reference() + copy-into-
images/ sequence as the old NodeGraphQt desktop app's Decision node
editor, and rewire_branch_ports() is a thin pass-through to
branch_images.rewire_ports_after_image_change() (Phase 1, already
unit-tested) - reused as-is rather than re-implemented in untested JS,
since graph_editor.js owns *when* to rewire (add/delete/move) but not the
rewiring algorithm itself.

Phase 6 adds Run/Stop/Confirm, backed by run_controller.RunController
(constructed in main.py, injected here). run_saved_profile() always
loads the named profile fresh from disk and translates it via
build_engine_graph_from_document() (graph_translation.py) rather than
taking a GraphDocument from the caller - the one shared "actually run"
code path for both the web Run button and the physical &ssm_tog key (see
main.py's module docstring for why they share this but not their
save-prompt popup mechanism). get_run_state() exists for polling, not
pushing: see run_controller.py's module docstring for why this bridge
deliberately never calls window.evaluate_js() to push state to the page -
JS calling into Python (this whole file) is the one direction proven safe.

Phase 6b adds the click-region picker/show-region preview, backed by
pick_controller.PickController (constructed in main.py, injected
here, same pattern as run_controller). pick_click_region() blocks (see
pick_controller.py's module docstring for why that's safe here) until the
user finishes dragging a region on the live target window or cancels.
"""
import base64
import os
import shutil
import time

import cv2
import webview

from app.graph.nodes.reference_processing import MaskDetectionError, process_masked_reference
from app.profiles.profile_manager import ProfileError
from app import branch_images, graph_translation


class WebBridge:
    def __init__(self, profile_manager, run_controller, pick_controller):
        self._profile_manager = profile_manager
        self._run_controller = run_controller
        self._pick_controller = pick_controller
        # Mirrors index.html's own `dirty` flag/live graph+meta, pushed
        # here on every setDirty() call. Exists so the &ssm_tog physical-key
        # handler (main.py) can check for unsaved changes and, if the
        # user chooses to save, do so without needing to ask JS for its
        # current state synchronously - window.evaluate_js() called from a
        # GUI-thread Qt slot (which &ssm_tog's handler is) risks the same
        # deadlock a real app freeze already confirmed for window.events.closing
        # (see main.py's docstring) - so Python keeps its own copy
        # instead of ever pulling from JS on that path.
        self.dirty = False
        self._pending_graph_document = None
        self._pending_meta = None
        # Set on a successful load_profile() - the &ssm_tog handler needs
        # to know which profile to (maybe save, then) run; nothing else in
        # this bridge needs "current profile" state, since every other
        # method already takes the profile name explicitly per call.
        self._current_profile_name = None

    def ping(self):
        return f'pong from Python at {time.strftime("%H:%M:%S")}'

    def set_dirty(self, value, graph_document=None, meta=None):
        self.dirty = bool(value)
        if graph_document is not None:
            self._pending_graph_document = graph_document
        if meta is not None:
            self._pending_meta = meta

    def list_profiles(self):
        return self._profile_manager.list_profiles()

    def create_profile(self, name):
        try:
            created = self._profile_manager.create(name)
            return {'ok': True, 'name': created}
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}

    def rename_profile(self, old_name, new_name):
        try:
            renamed = self._profile_manager.rename(old_name, new_name)
            return {'ok': True, 'name': renamed}
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}

    def duplicate_profile(self, name, new_name):
        try:
            duplicated = self._profile_manager.duplicate(name, new_name)
            return {'ok': True, 'name': duplicated}
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}

    def delete_profile(self, name):
        try:
            self._profile_manager.delete(name)
            if name == self._current_profile_name:
                self._current_profile_name = None
            return {'ok': True}
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}

    def load_profile(self, name):
        try:
            data = self._profile_manager.load(name)
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}
        self._current_profile_name = name
        graph = data.get('session') or {}
        return {
            'ok': True,
            'graph': graph,
            'meta': {
                'target_window_title': data.get('target_window_title', ''),
                'focus_policy': data.get('focus_policy', 'pause_until_focused'),
                'confirmation_mode': data.get('confirmation_mode', False),
                'target_executable': data.get('target_executable', ''),
            },
            'image_thumbnails': self._collect_image_thumbnails(name, graph),
        }

    def _collect_image_thumbnails(self, name, graph):
        """{reference_path: data: URI} for every Branch/Branch (Wait) node's
        image in `graph` - display-only, computed fresh on every load
        rather than stored in the GraphDocument. Silently skips anything
        not shaped like one of those two node types (e.g. an
        incompatible/legacy graph) - graph_editor.js's own compatibility
        check is what actually surfaces that to the user; this just must
        not crash on it."""
        thumbnails = {}
        for node in (graph.get('nodes') or {}).values():
            if not isinstance(node, dict) or node.get('type') not in ('branch', 'branch_wait'):
                continue
            for image in (node.get('properties') or {}).get('images') or []:
                relative_path = image.get('reference_path')
                if relative_path and relative_path not in thumbnails:
                    thumbnails[relative_path] = self._thumbnail_data_uri(self._abs_image_path(name, relative_path))
        return thumbnails

    def _abs_image_path(self, profile_name, relative_path):
        return os.path.join(self._profile_manager.profile_dir(profile_name), relative_path)

    @staticmethod
    def _thumbnail_data_uri(abs_path):
        """A data: URI (not a file:// URL) - QtWebEngine refuses to load a
        file:// resource whose path falls outside the directory the page
        itself was served from (confirmed via a real "Not allowed to load
        local resource" error - index.html lives under host/webui/, but
        profile images live under profiles/<name>/images/, a completely
        different tree), so the actual bytes have to cross the bridge
        instead of just a path. Every cropped reference image is written as
        a PNG by add_branch_image() (cv2.imwrite(..., '..._cropped.png')),
        so the mime type is always image/png."""
        with open(abs_path, 'rb') as image_file:
            encoded = base64.b64encode(image_file.read()).decode('ascii')
        return 'data:image/png;base64,' + encoded

    def save_profile(self, name, graph_document, meta):
        meta = meta or {}
        try:
            self._profile_manager.save(
                name, graph_document or {},
                target_window_title=meta.get('target_window_title', ''),
                focus_policy=meta.get('focus_policy', 'pause_until_focused'),
                confirmation_mode=meta.get('confirmation_mode', False),
                target_executable=meta.get('target_executable', ''),
            )
            return {'ok': True}
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}

    def add_branch_image(self, profile_name, node_id):
        """Native file-pick + mask-process + copy-into-images/, mirroring
        the old NodeGraphQt desktop app's Decision node add-image handler
        exactly except for the filename stem: the old code used the node's
        own id plus its current image count for unused-yet-informative
        uniqueness; here
        node_id is just whatever small integer Drawflow assigned this
        session (not globally stable), so it's combined with a millisecond
        timestamp instead - collision-proof for a single user clicking one
        button at a time, without needing the image count passed in."""
        if not self._profile_manager.exists(profile_name):
            return {'ok': False, 'error': f"Profile '{profile_name}' does not exist."}

        chosen = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=('Images (*.png;*.jpg;*.jpeg;*.bmp)',),
        )
        if not chosen:
            return {'ok': False, 'cancelled': True}
        src_path = chosen[0]

        try:
            cropped_bgra, bounding_box, _kept_pixels = process_masked_reference(src_path)
        except MaskDetectionError as exc:
            return {'ok': False, 'error': str(exc)}

        images_dir = self._profile_manager.images_dir(profile_name)
        stem = f'{node_id}_{int(time.time() * 1000)}'
        full_filename = f"{stem}_full{os.path.splitext(src_path)[1] or '.png'}"
        cropped_filename = f'{stem}_cropped.png'
        shutil.copyfile(src_path, os.path.join(images_dir, full_filename))
        cv2.imwrite(os.path.join(images_dir, cropped_filename), cropped_bgra)

        region_x, region_y, region_w, region_h = bounding_box
        reference_path = os.path.join('images', cropped_filename)
        image = {
            'reference_path': reference_path,
            'reference_full_path': os.path.join('images', full_filename),
            'region_x': region_x, 'region_y': region_y,
            'region_w': region_w, 'region_h': region_h,
        }
        return {
            'ok': True,
            'image': image,
            'thumbnail_url': self._thumbnail_data_uri(self._abs_image_path(profile_name, reference_path)),
        }

    def rewire_branch_ports(self, connections_before, position_mapping, num_images):
        """Thin pass-through to branch_images.rewire_ports_after_image_change()
        (Phase 1, already unit-tested) - graph_editor.js computes the
        position_mapping for whichever operation (add/delete/move) just
        happened, the same way the old NodeGraphQt desktop app's Decision
        node add/delete/move-image handlers did, and calls this after
        every one of them.

        position_mapping crosses the JS -> Python bridge as JSON, whose
        object keys are always strings - so JS's {0: 1, 1: 0} arrives here
        as {"0": 1, "1": 0}. rewire_ports_after_image_change() looks keys up
        with plain ints (position_mapping.get(new_index) for an int
        new_index from range(num_images) - see its own already-int-keyed
        test suite), so passing the dict through as received made every
        lookup silently miss and wipe every port's connections - confirmed
        as a real bug via a report that reordering images cut a connection
        instead of carrying it to the new position. Normalizing keys back
        to int here, at the bridge boundary where the JSON round-trip
        actually introduced the mismatch, keeps the already-tested pure
        function's int-keyed contract untouched."""
        int_keyed_mapping = {int(key): value for key, value in position_mapping.items()}
        return branch_images.rewire_ports_after_image_change(
            connections_before, int_keyed_mapping, num_images,
        )

    def run_saved_profile(self, profile_name):
        """Loads `profile_name` fresh from disk, translates it, and starts
        it - the one shared "actually run" code path for both trigger
        points (the web Run button and the physical &ssm_tog key, via
        main.py's _on_ssm_tog()), so they behave identically: always
        run what's saved, never whatever might be live and unsaved in the
        browser. Each trigger handles its own "save first?" prompt
        beforehand using whatever confirmation mechanism is safe on its own
        calling thread (index.html's window.confirm() here - a normal JS-
        side call, no cross-thread issue; a native QMessageBox in
        _on_ssm_tog(), which runs on the GUI thread) - see this bridge's
        module docstring and run_controller.py's for why those two threads
        can't share the exact same popup mechanism, even though the
        resulting behavior is now identical either way."""
        try:
            data = self._profile_manager.load(profile_name)
        except ProfileError as exc:
            return {'ok': False, 'error': str(exc)}
        engine_graph = graph_translation.build_engine_graph_from_document(data.get('session') or {})
        return self._run_controller.start(
            engine_graph,
            target_executable=data.get('target_executable', ''),
            target_window_title=data.get('target_window_title', ''),
            profile_dir=self._profile_manager.profile_dir(profile_name),
            focus_policy=data.get('focus_policy', 'pause_until_focused'),
            confirmation_mode=bool(data.get('confirmation_mode', False)),
        )

    def stop_macro(self):
        return self._run_controller.stop()

    def confirm_macro(self):
        return self._run_controller.confirm()

    def get_run_state(self):
        """Polled from index.html on a timer rather than pushed - see
        run_controller.py's module docstring for why Python never calls
        window.evaluate_js() to push this instead. Also where a naturally-
        finished run (a dead-end node, not a Stop click) actually gets
        noticed - see RunController.poll(). last_error is consumed here
        (reset to None immediately after reading it) so a real failure
        (e.g. a focus timeout - see run_controller.py's poll()) is
        reported to the user exactly once, not on every 500ms poll tick
        for as long as the button happens to sit at "Run"."""
        self._run_controller.poll()
        last_error = self._run_controller.last_error
        self._run_controller.last_error = None
        return {
            'running': self._run_controller.is_running,
            'pending_status': self._run_controller.pending_status,
            'last_error': last_error,
        }

    def pick_click_region(self, target_window_title):
        """Blocks (see pick_controller.py's module docstring) until the
        user finishes dragging a region on the live target window, or
        cancels."""
        return self._pick_controller.pick_click_region(target_window_title)

    def show_click_region(self, target_window_title, x, y, w, h):
        return self._pick_controller.show_click_region(target_window_title, x, y, w, h)

    def show_reference_region(self, profile_name, target_window_title, reference_path, region_x, region_y):
        abs_path = self._abs_image_path(profile_name, reference_path) if reference_path else None
        return self._pick_controller.show_reference_region(target_window_title, abs_path, region_x, region_y)
