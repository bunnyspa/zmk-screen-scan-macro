"""Tests for PickController's error-path logic (missing target window
title, window not found, missing reference image) - the paths that return
before ever touching a real overlay QWidget. Constructing/showing an actual
ClickRegionOverlay/RegionHighlightOverlay/StaticReferenceOverlay needs a
live display, so - same testing boundary as test_run_controller.py's
RunController tests - the actual overlay-rendering paths need manual
verification instead."""
import app.pick_controller as pick_controller_module
from app.pick_controller import PickController


def test_pick_click_region_without_target_window_title_returns_error():
    controller = PickController()
    result = controller.pick_click_region('')
    assert result == {'ok': False, 'error': 'Set a target window title for this profile first.'}


def test_pick_click_region_window_not_found_returns_error(monkeypatch):
    monkeypatch.setattr(pick_controller_module, 'get_window_rect', lambda title: None)
    controller = PickController()
    result = controller.pick_click_region('Nonexistent Window')
    assert result['ok'] is False
    assert 'Nonexistent Window' in result['error']


def test_show_click_region_without_target_window_title_returns_error():
    controller = PickController()
    result = controller.show_click_region('', 0, 0, 10, 10)
    assert result == {'ok': False, 'error': 'Set a target window title for this profile first.'}


def test_show_click_region_window_not_found_returns_error(monkeypatch):
    monkeypatch.setattr(pick_controller_module, 'get_window_rect', lambda title: None)
    controller = PickController()
    result = controller.show_click_region('Nonexistent Window', 0, 0, 10, 10)
    assert result['ok'] is False
    assert 'Nonexistent Window' in result['error']


def test_show_reference_region_missing_image_returns_error():
    controller = PickController()
    result = controller.show_reference_region('Some Window', '', 0, 0)
    assert result == {'ok': False, 'error': 'No reference image has been set for this node yet.'}


def test_show_reference_region_nonexistent_image_path_returns_error(tmp_path):
    controller = PickController()
    missing_path = str(tmp_path / 'does_not_exist.png')
    result = controller.show_reference_region('Some Window', missing_path, 0, 0)
    assert result == {'ok': False, 'error': 'No reference image has been set for this node yet.'}


def test_show_reference_region_without_target_window_title_returns_error(tmp_path):
    image_path = tmp_path / 'ref.png'
    image_path.write_bytes(b'not a real png but just needs to exist')
    controller = PickController()
    result = controller.show_reference_region('', str(image_path), 0, 0)
    assert result == {'ok': False, 'error': 'Set a target window title for this profile first.'}


def test_show_reference_region_window_not_found_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pick_controller_module, 'get_window_extended_frame_bounds', lambda title: None)
    image_path = tmp_path / 'ref.png'
    image_path.write_bytes(b'not a real png but just needs to exist')
    controller = PickController()
    result = controller.show_reference_region('Nonexistent Window', str(image_path), 0, 0)
    assert result['ok'] is False
    assert 'Nonexistent Window' in result['error']
