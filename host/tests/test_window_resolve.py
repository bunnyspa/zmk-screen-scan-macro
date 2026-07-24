from engine.window_resolve import resolve_target_window


def _windows_and_exes(entries):
    """entries: [(hwnd, title, exe), ...] -> (list_windows, get_executable) fakes."""
    windows = [(hwnd, title) for hwnd, title, _exe in entries]
    exe_by_hwnd = {hwnd: exe for hwnd, _title, exe in entries}

    def list_windows():
        return windows

    def get_executable(hwnd):
        return exe_by_hwnd.get(hwnd)

    return list_windows, get_executable


def test_single_executable_match_resolves_without_confirmation():
    list_windows, get_executable = _windows_and_exes([
        (1, 'main.py - Notepad++', 'notepad++.exe'),
        (2, 'Untitled - Notepad', 'notepad.exe'),
    ])

    result = resolve_target_window(
        'notepad++.exe', 'irrelevant hint',
        list_windows=list_windows, get_executable=get_executable,
    )

    assert result.hwnd == 1
    assert result.needs_confirmation is False
    assert result.candidates == []


def test_no_executable_match_falls_back_to_title_substring():
    list_windows, get_executable = _windows_and_exes([
        (1, 'main.py - Notepad++', 'notepad++.exe'),
        (2, 'Untitled - Notepad', 'notepad.exe'),
    ])

    result = resolve_target_window(
        'wrong.exe', 'Untitled',
        list_windows=list_windows, get_executable=get_executable,
    )

    assert result.hwnd is None
    assert result.needs_confirmation is True
    assert [c.hwnd for c in result.candidates] == [2]


def test_multiple_executable_matches_still_need_confirmation_even_when_title_narrows_to_one():
    list_windows, get_executable = _windows_and_exes([
        (1, 'main.py - Notepad++', 'notepad++.exe'),
        (2, 'other.py - Notepad++', 'notepad++.exe'),
    ])

    result = resolve_target_window(
        'notepad++.exe', 'main.py',
        list_windows=list_windows, get_executable=get_executable,
    )

    # Falling back at all requires confirmation, even though the title
    # hint happens to narrow it to exactly one candidate - see the
    # module docstring for why.
    assert result.needs_confirmation is True
    assert [c.hwnd for c in result.candidates] == [1]


def test_multiple_executable_matches_with_no_narrowing_title_lists_all():
    list_windows, get_executable = _windows_and_exes([
        (1, 'main.py - Notepad++', 'notepad++.exe'),
        (2, 'other.py - Notepad++', 'notepad++.exe'),
    ])

    result = resolve_target_window(
        'notepad++.exe', '',
        list_windows=list_windows, get_executable=get_executable,
    )

    assert result.needs_confirmation is True
    assert {c.hwnd for c in result.candidates} == {1, 2}


def test_title_hint_that_matches_nothing_keeps_the_executable_matched_pool():
    list_windows, get_executable = _windows_and_exes([
        (1, 'main.py - Notepad++', 'notepad++.exe'),
        (2, 'other.py - Notepad++', 'notepad++.exe'),
    ])

    result = resolve_target_window(
        'notepad++.exe', 'nothing matches this',
        list_windows=list_windows, get_executable=get_executable,
    )

    assert result.needs_confirmation is True
    assert {c.hwnd for c in result.candidates} == {1, 2}


def test_no_executable_match_and_no_title_hint_lists_every_window():
    list_windows, get_executable = _windows_and_exes([
        (1, 'main.py - Notepad++', 'notepad++.exe'),
        (2, 'Untitled - Notepad', 'notepad.exe'),
    ])

    result = resolve_target_window(
        'wrong.exe', '',
        list_windows=list_windows, get_executable=get_executable,
    )

    assert result.needs_confirmation is True
    assert {c.hwnd for c in result.candidates} == {1, 2}
