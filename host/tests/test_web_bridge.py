"""Tests for webui/bridge.py's WebBridge - exercised directly against a
real ProfileManager on a tmp_path root, with no pywebview/Qt involved at
all (WebBridge itself has no such dependency; only host/main.py,
which actually launches a window, does).

Phase 5's add_branch_image() is the one method that does need `webview`
(its native file-open dialog) - faked here with a plain stand-in object
rather than mocking pywebview itself, since only `windows[0].create_file_dialog()`'s
return value matters to this method.

Phase 6's run_saved_profile()/stop_macro()/confirm_macro()/get_run_state() take a
RunController (real one is a QObject - see run_controller.py) - faked here
too, for the same reason as FakeWindow: these tests only need to verify
WebBridge wires calls through correctly (translates a GraphDocument,
passes meta fields along, reads back .is_running/.pending_status),
not RunController's own orchestration logic (already covered by
test_run_controller.py).

Phase 6b's pick_click_region()/show_click_region()/show_reference_region()
take a PickController (also a real QObject - see pick_controller.py),
faked the same way - these tests verify argument wiring and the
reference_path -> abs_path resolution, not PickController's own overlay
logic (covered by test_pick_controller.py)."""
import os

import cv2
import numpy as np
import pytest

import webui.bridge as bridge_module
from app import branch_images
from app.profiles.profile_manager import ProfileManager
from webui.bridge import WebBridge


class FakeRunController:
    def __init__(self):
        self.is_running = False
        self.pending_status = None
        self.last_error = None
        self.start_calls = []
        self.stop_calls = 0
        self.confirm_calls = 0
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1

    def start(self, engine_graph, **kwargs):
        self.start_calls.append((engine_graph, kwargs))
        self.is_running = True
        return {'ok': True}

    def stop(self):
        self.stop_calls += 1
        self.is_running = False
        return {'ok': True}

    def confirm(self):
        self.confirm_calls += 1
        return {'ok': True}


@pytest.fixture
def run_controller():
    return FakeRunController()


class FakePickController:
    def __init__(self):
        self.pick_calls = []
        self.show_click_region_calls = []
        self.show_reference_region_calls = []

    def pick_click_region(self, target_window_title):
        self.pick_calls.append(target_window_title)
        return {'ok': True, 'x': 1, 'y': 2, 'w': 3, 'h': 4}

    def show_click_region(self, target_window_title, x, y, w, h):
        self.show_click_region_calls.append((target_window_title, x, y, w, h))
        return {'ok': True}

    def show_reference_region(self, target_window_title, abs_image_path, region_x, region_y):
        self.show_reference_region_calls.append((target_window_title, abs_image_path, region_x, region_y))
        return {'ok': True}


@pytest.fixture
def pick_controller():
    return FakePickController()


@pytest.fixture
def bridge(tmp_path, run_controller, pick_controller):
    return WebBridge(ProfileManager(str(tmp_path)), run_controller, pick_controller)


class FakeWindow:
    def __init__(self, dialog_result):
        self.dialog_result = dialog_result

    def create_file_dialog(self, dialog_type, file_types=()):
        return self.dialog_result


def _write_reference_image(path):
    """A tiny opaque BGRA square with one transparent corner pixel - enough
    for process_masked_reference() to find real mask/comparison pixels
    without needing a special test-only code path."""
    image = np.full((4, 4, 4), 255, dtype=np.uint8)
    image[0, 0, 3] = 0
    cv2.imwrite(str(path), image)


def test_list_profiles_starts_empty(bridge):
    assert bridge.list_profiles() == []


def test_create_then_list(bridge):
    result = bridge.create_profile('Test')
    assert result == {'ok': True, 'name': 'Test'}
    assert bridge.list_profiles() == ['Test']


def test_create_duplicate_name_returns_error_not_raise(bridge):
    bridge.create_profile('Test')
    result = bridge.create_profile('Test')
    assert result['ok'] is False
    assert 'already exists' in result['error']


def test_create_invalid_name_returns_error(bridge):
    result = bridge.create_profile('')
    assert result['ok'] is False


def test_load_profile_returns_defaults_for_freshly_created(bridge):
    bridge.create_profile('Test')
    result = bridge.load_profile('Test')
    assert result['ok'] is True
    assert result['graph'] == {}
    assert result['meta'] == {
        'target_window_title': '', 'focus_policy': 'pause_until_focused',
        'confirmation_mode': False, 'target_executable': '',
    }


def test_load_missing_profile_returns_error(bridge):
    result = bridge.load_profile('Nope')
    assert result['ok'] is False


def test_save_then_load_round_trips(bridge):
    bridge.create_profile('Test')
    graph_document = {'nodes': {'n1': {'type': 'wait'}}}
    meta = {
        'target_executable': 'notepad.exe', 'target_window_title': 'Untitled',
        'focus_policy': 'focus_and_resume', 'confirmation_mode': True,
    }

    save_result = bridge.save_profile('Test', graph_document, meta)
    assert save_result == {'ok': True}

    load_result = bridge.load_profile('Test')
    assert load_result['graph'] == graph_document
    assert load_result['meta'] == meta


def test_save_missing_profile_returns_error(bridge):
    result = bridge.save_profile('Nope', {}, {})
    assert result['ok'] is False


def test_load_profile_collects_thumbnails_for_branch_and_branch_wait_nodes(bridge):
    # _collect_image_thumbnails() filters on node type - branch and
    # branch_wait both have an `images` list (unlike action/wait), and a
    # node of a type it doesn't recognize should be skipped rather than
    # crash on missing/mismatched fields.
    bridge.create_profile('Test')
    images_dir = bridge._profile_manager.images_dir('Test')
    for filename in ('a.png', 'b.png'):
        with open(os.path.join(images_dir, filename), 'wb') as f:
            f.write(b'not a real png but just needs to exist')

    graph_document = {
        'nodes': {
            'n1': {'type': 'branch', 'properties': {'images': [{'reference_path': 'images/a.png'}]}},
            'n2': {'type': 'branch_wait', 'properties': {'images': [{'reference_path': 'images/b.png'}]}},
            'n3': {'type': 'wait', 'properties': {'duration_ms': 100}},
        },
    }
    bridge.save_profile('Test', graph_document, {})

    result = bridge.load_profile('Test')

    assert set(result['image_thumbnails'].keys()) == {'images/a.png', 'images/b.png'}
    assert all(uri.startswith('data:image/png;base64,') for uri in result['image_thumbnails'].values())


def test_rename_profile(bridge):
    bridge.create_profile('Old')
    result = bridge.rename_profile('Old', 'New')
    assert result == {'ok': True, 'name': 'New'}
    assert bridge.list_profiles() == ['New']


def test_duplicate_profile(bridge):
    bridge.create_profile('Original')
    result = bridge.duplicate_profile('Original', 'Copy')
    assert result == {'ok': True, 'name': 'Copy'}
    assert sorted(bridge.list_profiles()) == ['Copy', 'Original']


def test_delete_profile(bridge):
    bridge.create_profile('Test')
    result = bridge.delete_profile('Test')
    assert result == {'ok': True}
    assert bridge.list_profiles() == []


def test_delete_missing_profile_returns_error(bridge):
    result = bridge.delete_profile('Nope')
    assert result['ok'] is False


def test_ping_returns_a_string(bridge):
    assert 'pong' in bridge.ping()


def test_set_dirty_mirrors_the_flag_for_the_window_closing_handler(bridge):
    # main.py's window-closing handler reads this attribute directly
    # (see its docstring for why it can't just ask JS at close time) -
    # starts false, tracks whatever index.html's setDirty() last pushed.
    assert bridge.dirty is False
    bridge.set_dirty(True)
    assert bridge.dirty is True
    bridge.set_dirty(False)
    assert bridge.dirty is False


def test_set_dirty_caches_graph_document_and_meta_for_the_ssm_tog_handler(bridge):
    # main.py's &ssm_tog handler needs a save-able snapshot of JS's
    # live state without asking JS for it synchronously (same deadlock risk
    # as above) - set_dirty() is how JS keeps that snapshot fresh.
    graph_document = {'start_node_id': 'n1', 'nodes': {}}
    meta = {'target_executable': 'notepad.exe'}

    bridge.set_dirty(True, graph_document, meta)

    assert bridge._pending_graph_document == graph_document
    assert bridge._pending_meta == meta


def test_set_dirty_without_graph_document_keeps_previous_cache(bridge):
    bridge.set_dirty(True, {'start_node_id': 'n1', 'nodes': {}}, {'target_executable': 'notepad.exe'})
    bridge.set_dirty(False)  # e.g. after a save - no new snapshot passed
    assert bridge._pending_graph_document == {'start_node_id': 'n1', 'nodes': {}}
    assert bridge._pending_meta == {'target_executable': 'notepad.exe'}


def test_load_profile_sets_current_profile_name(bridge):
    bridge.create_profile('Test')
    bridge.load_profile('Test')
    assert bridge._current_profile_name == 'Test'


def test_delete_profile_clears_current_profile_name_if_it_was_open(bridge):
    bridge.create_profile('Test')
    bridge.load_profile('Test')
    bridge.delete_profile('Test')
    assert bridge._current_profile_name is None


def test_delete_profile_leaves_current_profile_name_alone_if_a_different_profile_was_open(bridge):
    bridge.create_profile('A')
    bridge.create_profile('B')
    bridge.load_profile('A')
    bridge.delete_profile('B')
    assert bridge._current_profile_name == 'A'


def test_add_branch_image_missing_profile_returns_error(bridge):
    result = bridge.add_branch_image('Nope', 'node-1')
    assert result['ok'] is False


def test_add_branch_image_cancelled_dialog_returns_cancelled(bridge, monkeypatch, tmp_path):
    bridge.create_profile('Test')
    monkeypatch.setattr(bridge_module.webview, 'windows', [FakeWindow(None)])

    result = bridge.add_branch_image('Test', 'node-1')

    assert result == {'ok': False, 'cancelled': True}


def test_add_branch_image_success_copies_files_and_returns_image(bridge, monkeypatch, tmp_path):
    bridge.create_profile('Test')
    src_path = tmp_path / 'source.png'
    _write_reference_image(src_path)
    monkeypatch.setattr(bridge_module.webview, 'windows', [FakeWindow([str(src_path)])])

    result = bridge.add_branch_image('Test', 'node-1')

    assert result['ok'] is True
    image = result['image']
    assert image['reference_path'].startswith('images' + os.sep)
    assert image['reference_full_path'].startswith('images' + os.sep)
    assert (image['region_w'], image['region_h']) == (4, 4)  # whole 4x4 square is the bounding box
    assert os.path.isfile(os.path.join(str(tmp_path), 'Test', image['reference_path']))
    assert os.path.isfile(os.path.join(str(tmp_path), 'Test', image['reference_full_path']))
    assert result['thumbnail_url'].startswith('data:image/png;base64,')


def test_add_branch_image_unmaskable_source_returns_error(bridge, monkeypatch, tmp_path):
    bridge.create_profile('Test')
    src_path = tmp_path / 'all_transparent.png'
    cv2.imwrite(str(src_path), np.zeros((4, 4, 4), dtype=np.uint8))  # alpha=0 everywhere - no comparison pixels
    monkeypatch.setattr(bridge_module.webview, 'windows', [FakeWindow([str(src_path)])])

    result = bridge.add_branch_image('Test', 'node-1')

    assert result['ok'] is False
    assert 'error' in result


def test_rewire_branch_ports_matches_the_underlying_pure_function(bridge):
    connections_before = {'1': [{'node': 'a'}], '2': [], 'false': [{'node': 'b'}]}
    position_mapping = {'0': 1, '1': 0}  # string keys, exactly as pywebview's JS->Python JSON bridge delivers them

    result = bridge.rewire_branch_ports(connections_before, position_mapping, 2)

    assert result == branch_images.rewire_ports_after_image_change(
        connections_before, {0: 1, 1: 0}, 2,  # the pure function's own contract is int-keyed
    )


def test_rewire_branch_ports_string_keys_actually_carry_connections(bridge):
    # Regression test: position_mapping arriving with string keys (as it
    # always does over the real bridge - see the method's docstring) must
    # not silently drop every connection. A swap should still swap, not wipe.
    connections_before = {'1': [{'node': 'a'}], '2': [{'node': 'b'}], 'false': []}
    position_mapping = {'0': 1, '1': 0}

    result = bridge.rewire_branch_ports(connections_before, position_mapping, 2)

    assert result == {'1': [{'node': 'b'}], '2': [{'node': 'a'}], 'false': []}


def test_run_saved_profile_loads_translates_and_starts(bridge, run_controller):
    graph_document = {
        'start_node_id': 'n1',
        'nodes': {'n1': {'type': 'wait', 'properties': {'duration_ms': 10}, 'connections': {'out': []}}},
    }
    meta = {
        'target_executable': 'notepad.exe', 'target_window_title': 'Untitled',
        'focus_policy': 'focus_and_resume', 'confirmation_mode': True,
    }
    bridge.create_profile('Test')
    bridge.save_profile('Test', graph_document, meta)

    result = bridge.run_saved_profile('Test')

    assert result == {'ok': True}
    assert run_controller.is_running is True
    engine_graph, kwargs = run_controller.start_calls[0]
    assert engine_graph == {'start_node': 'n1', 'nodes': {'n1': {'type': 'wait', 'duration_ms': 10, 'out': None}}}
    assert kwargs == {
        'target_executable': 'notepad.exe', 'target_window_title': 'Untitled',
        'profile_dir': bridge._profile_manager.profile_dir('Test'),
        'focus_policy': 'focus_and_resume', 'confirmation_mode': True,
    }


def test_run_saved_profile_with_no_start_node_still_delegates_none_graph(bridge, run_controller):
    # RunController itself is what rejects a None engine_graph (see
    # test_run_controller.py) - the bridge's job is only load + translation.
    bridge.create_profile('Test')  # freshly created, never saved a graph - session is {}
    result = bridge.run_saved_profile('Test')
    assert result == {'ok': True}  # FakeRunController.start() always succeeds
    engine_graph, _kwargs = run_controller.start_calls[0]
    assert engine_graph is None


def test_run_saved_profile_missing_profile_returns_error(bridge, run_controller):
    result = bridge.run_saved_profile('Nope')
    assert result == {'ok': False, 'error': "Profile 'Nope' does not exist."}
    assert run_controller.start_calls == []


def test_stop_macro_delegates_to_run_controller(bridge, run_controller):
    assert bridge.stop_macro() == {'ok': True}
    assert run_controller.stop_calls == 1


def test_confirm_macro_delegates_to_run_controller(bridge, run_controller):
    assert bridge.confirm_macro() == {'ok': True}
    assert run_controller.confirm_calls == 1


def test_get_run_state_reflects_run_controller(bridge, run_controller):
    assert bridge.get_run_state() == {'running': False, 'pending_status': None, 'last_error': None}
    run_controller.is_running = True
    run_controller.pending_status = 'Pending: click (confirm to proceed)'
    assert bridge.get_run_state() == {
        'running': True, 'pending_status': 'Pending: click (confirm to proceed)', 'last_error': None,
    }


def test_get_run_state_polls_run_controller_first(bridge, run_controller):
    # get_run_state() is where a naturally-finished run (a dead-end node,
    # not a Stop click) actually gets noticed - see RunController.poll().
    bridge.get_run_state()
    assert run_controller.poll_calls == 1


def test_get_run_state_consumes_last_error_exactly_once(bridge, run_controller):
    # Regression test: a real failure (e.g. a focus timeout) must be
    # reported to the user once, not on every 500ms poll tick for as long
    # as the button happens to sit idle at "Run".
    run_controller.last_error = 'target window did not come to focus within 10.0s'

    first = bridge.get_run_state()
    second = bridge.get_run_state()

    assert first['last_error'] == 'target window did not come to focus within 10.0s'
    assert second['last_error'] is None
    assert run_controller.last_error is None


def test_pick_click_region_delegates_to_pick_controller(bridge, pick_controller):
    result = bridge.pick_click_region('Some Window')
    assert result == {'ok': True, 'x': 1, 'y': 2, 'w': 3, 'h': 4}
    assert pick_controller.pick_calls == ['Some Window']


def test_show_click_region_delegates_to_pick_controller(bridge, pick_controller):
    result = bridge.show_click_region('Some Window', 1, 2, 3, 4)
    assert result == {'ok': True}
    assert pick_controller.show_click_region_calls == [('Some Window', 1, 2, 3, 4)]


def test_show_reference_region_resolves_reference_path_to_an_absolute_path(bridge, pick_controller):
    bridge.create_profile('Test')

    result = bridge.show_reference_region('Test', 'Some Window', 'images/x_cropped.png', 5, 6)

    assert result == {'ok': True}
    target_window_title, abs_path, region_x, region_y = pick_controller.show_reference_region_calls[0]
    assert target_window_title == 'Some Window'
    assert abs_path == os.path.join(bridge._profile_manager.profile_dir('Test'), 'images/x_cropped.png')
    assert (region_x, region_y) == (5, 6)


def test_show_reference_region_with_no_reference_path_passes_none(bridge, pick_controller):
    bridge.create_profile('Test')
    bridge.show_reference_region('Test', 'Some Window', '', 0, 0)
    target_window_title, abs_path, _region_x, _region_y = pick_controller.show_reference_region_calls[0]
    assert abs_path is None
