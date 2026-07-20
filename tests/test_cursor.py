from engine.cursor import click_commands
import protocol as wire  # available via engine.cursor's sys.path insert of host/


def test_click_commands_computes_delta_from_current_cursor_position():
    def fake_cursor_pos():
        return (500, 500)

    def fake_window_origin(hwnd):
        assert hwnd == 1
        return (100, 100)

    commands = click_commands(
        hwnd=1,
        click_rect=(50, 60, 20, 20),  # center = (60, 70) window-relative
        mouse_button=wire.MOUSE_BUTTON_RIGHT,
        get_cursor_pos=fake_cursor_pos,
        get_window_client_origin=fake_window_origin,
    )

    assert len(commands) == 2
    move, click = commands

    assert move.action == wire.ACTION_MOUSE_MOVE
    # target screen pos = origin(100,100) + center(60,70) = (160,170)
    # delta = target - cursor(500,500)
    assert (move.dx, move.dy) == (160 - 500, 170 - 500)

    assert click.action == wire.ACTION_MOUSE_CLICK
    assert click.mouse_buttons == wire.MOUSE_BUTTON_RIGHT


def test_click_commands_defaults_to_left_button():
    commands = click_commands(
        hwnd=1,
        click_rect=(0, 0, 10, 10),
        get_cursor_pos=lambda: (0, 0),
        get_window_client_origin=lambda hwnd: (0, 0),
    )
    assert commands[1].mouse_buttons == wire.MOUSE_BUTTON_LEFT
