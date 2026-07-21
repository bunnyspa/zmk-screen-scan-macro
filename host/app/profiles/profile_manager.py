import os
import re
import shutil

from . import profile_store

_VALID_NAME_RE = re.compile(r'^[A-Za-z0-9 _\-]+$')


class ProfileError(Exception):
    pass


def _validate_name(name):
    name = name.strip()
    if not name or not _VALID_NAME_RE.match(name):
        raise ProfileError(
            "Profile name must be non-empty and contain only letters, "
            "numbers, spaces, hyphens, or underscores."
        )
    return name


class ProfileManager:
    """CRUD over profile folders under `profiles_root`. Each profile is a
    subfolder containing profile.json and an images/ folder for Decision
    node reference images."""

    def __init__(self, profiles_root):
        self.profiles_root = profiles_root
        os.makedirs(self.profiles_root, exist_ok=True)

    def _dir_for(self, name):
        return os.path.join(self.profiles_root, name)

    def _images_dir_for(self, name):
        return os.path.join(self._dir_for(name), 'images')

    def list_profiles(self):
        if not os.path.isdir(self.profiles_root):
            return []
        return sorted(
            entry for entry in os.listdir(self.profiles_root)
            if os.path.isdir(self._dir_for(entry))
        )

    def exists(self, name):
        return os.path.isdir(self._dir_for(name))

    def create(self, name):
        name = _validate_name(name)
        if self.exists(name):
            raise ProfileError(f"Profile '{name}' already exists.")
        profile_dir = self._dir_for(name)
        os.makedirs(profile_dir)
        os.makedirs(self._images_dir_for(name))
        profile_store.save(profile_dir, name, {})
        return name

    def load(self, name):
        if not self.exists(name):
            raise ProfileError(f"Profile '{name}' does not exist.")
        return profile_store.load(self._dir_for(name))

    def save(self, name, session_data, target_window_title='',
             focus_policy='pause_until_focused'):
        if not self.exists(name):
            raise ProfileError(f"Profile '{name}' does not exist.")
        profile_store.save(self._dir_for(name), name, session_data, target_window_title,
                           focus_policy)

    def rename(self, old_name, new_name):
        new_name = _validate_name(new_name)
        if not self.exists(old_name):
            raise ProfileError(f"Profile '{old_name}' does not exist.")
        if self.exists(new_name):
            raise ProfileError(f"Profile '{new_name}' already exists.")
        os.rename(self._dir_for(old_name), self._dir_for(new_name))
        data = profile_store.load(self._dir_for(new_name))
        data['profile_name'] = new_name
        profile_store.save(self._dir_for(new_name), new_name, data.get('session', {}),
                           data.get('target_window_title', ''),
                           data.get('focus_policy', 'pause_until_focused'))
        return new_name

    def delete(self, name):
        if not self.exists(name):
            raise ProfileError(f"Profile '{name}' does not exist.")
        shutil.rmtree(self._dir_for(name))

    def duplicate(self, name, new_name):
        new_name = _validate_name(new_name)
        if not self.exists(name):
            raise ProfileError(f"Profile '{name}' does not exist.")
        if self.exists(new_name):
            raise ProfileError(f"Profile '{new_name}' already exists.")
        shutil.copytree(self._dir_for(name), self._dir_for(new_name))
        data = profile_store.load(self._dir_for(new_name))
        data['profile_name'] = new_name
        profile_store.save(self._dir_for(new_name), new_name, data.get('session', {}),
                           data.get('target_window_title', ''),
                           data.get('focus_policy', 'pause_until_focused'))
        return new_name

    def images_dir(self, name):
        images_dir = self._images_dir_for(name)
        os.makedirs(images_dir, exist_ok=True)
        return images_dir

    def profile_dir(self, name):
        """Absolute path to the profile's own folder - needed by the engine
        to resolve a Decision node's reference_path (stored relative, e.g.
        'images/xxx_cropped.png')."""
        return self._dir_for(name)
