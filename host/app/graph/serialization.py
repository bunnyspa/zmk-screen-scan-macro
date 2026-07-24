"""Glue between GraphWidget (NodeGraphQt session) and ProfileManager (JSON on disk)."""

import os

REFERENCE_IMAGE_PROPERTIES = ('reference_path', 'reference_full_path')


def load_profile_into_graph(profile_manager, graph_widget, profile_name):
    data = profile_manager.load(profile_name)
    graph_widget.load_session(data.get('session') or {})
    return (data.get('target_window_title', ''), data.get('focus_policy', 'pause_until_focused'),
            data.get('confirmation_mode', False), data.get('target_executable', ''))


def save_graph_to_profile(profile_manager, graph_widget, profile_name, target_window_title='',
                          focus_policy='pause_until_focused', confirmation_mode=False,
                          target_executable=''):
    session_data = graph_widget.serialize()
    profile_manager.save(profile_name, session_data, target_window_title, focus_policy,
                         confirmation_mode, target_executable)
    graph_widget.mark_saved()
    _delete_unreferenced_images(profile_manager, graph_widget, profile_name)


def _delete_unreferenced_images(profile_manager, graph_widget, profile_name):
    """Removes image files left behind in images/ by browsing to a
    replacement reference image, or by deleting a Decision node outright.
    Runs at save time (not when the image is replaced/the node is deleted)
    so an unsaved change never deletes a file still referenced by the
    profile.json currently on disk."""
    images_dir = profile_manager.images_dir(profile_name)

    referenced_filenames = set()
    for node in graph_widget.graph.all_nodes():
        if not hasattr(node, 'get_property'):
            continue
        for prop_name in REFERENCE_IMAGE_PROPERTIES:
            if not node.has_property(prop_name):
                continue
            relative_path = node.get_property(prop_name)
            if relative_path:
                referenced_filenames.add(os.path.basename(relative_path))

    for filename in os.listdir(images_dir):
        if filename not in referenced_filenames:
            try:
                os.remove(os.path.join(images_dir, filename))
            except OSError:
                pass
