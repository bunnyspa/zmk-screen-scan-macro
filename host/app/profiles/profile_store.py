import json
import os

SCHEMA_VERSION = 1


def profile_json_path(profile_dir):
    return os.path.join(profile_dir, 'profile.json')


def load(profile_dir):
    path = profile_json_path(profile_dir)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save(profile_dir, profile_name, session_data, target_window_title='',
         focus_policy='pause_until_focused'):
    data = {
        'schema_version': SCHEMA_VERSION,
        'profile_name': profile_name,
        # Per-profile, unlike VisionGraph's single hardcoded TARGET_WINDOW_TITLE -
        # a graph's click/decision regions are meaningless against a different
        # window's layout, so the target window travels with the profile.
        'target_window_title': target_window_title,
        # What MacroRunner does when the target window isn't focused right
        # before an action fires - real HID input goes wherever the OS has
        # focus, not to a specific window. See engine/focus.py.
        'focus_policy': focus_policy,
        'session': session_data,
    }
    os.makedirs(profile_dir, exist_ok=True)
    path = profile_json_path(profile_dir)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def empty_session_data(profile_name):
    return {
        'schema_version': SCHEMA_VERSION,
        'profile_name': profile_name,
        'target_window_title': '',
        'focus_policy': 'pause_until_focused',
        'session': {},
    }
