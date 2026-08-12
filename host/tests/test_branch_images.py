from app.branch_images import rewire_ports_after_image_change


def test_add_new_entry_has_no_connections():
    # Adding a 3rd image: images 0,1 keep their old connections at the same
    # positions, image 2 is brand new (position_mapping[2] = None).
    connections_before = {"1": ["a"], "2": ["b"], "false": ["f"]}
    mapping = {0: 0, 1: 1, 2: None}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=3)

    assert result == {"1": ["a"], "2": ["b"], "3": [], "false": ["f"]}


def test_delete_first_shifts_remaining_down():
    # Images were [X, Y, Z] (ports "1","2","3"); deleting X leaves [Y, Z] -
    # port "1" should now carry what was "2"'s connection, "2" carries "3"'s.
    connections_before = {"1": ["x"], "2": ["y"], "3": ["z"], "false": ["f"]}
    mapping = {0: 1, 1: 2}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=2)

    assert result == {"1": ["y"], "2": ["z"], "false": ["f"]}


def test_delete_middle_leaves_first_and_shifts_last():
    # [X, Y, Z] -> delete Y -> [X, Z]
    connections_before = {"1": ["x"], "2": ["y"], "3": ["z"], "false": ["f"]}
    mapping = {0: 0, 1: 2}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=2)

    assert result == {"1": ["x"], "2": ["z"], "false": ["f"]}


def test_delete_last_leaves_earlier_ports_untouched():
    # [X, Y, Z] -> delete Z -> [X, Y]
    connections_before = {"1": ["x"], "2": ["y"], "3": ["z"], "false": ["f"]}
    mapping = {0: 0, 1: 1}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=2)

    assert result == {"1": ["x"], "2": ["y"], "false": ["f"]}


def test_reorder_swap_carries_connections_with_the_entry_not_the_port():
    # [X, Y] -> swap -> [Y, X]: port "1" (now Y) should get what was "2"'s
    # (X's... no, Y's) connection - i.e. the connection follows the image,
    # not the port position.
    connections_before = {"1": ["x_target"], "2": ["y_target"], "false": []}
    mapping = {0: 1, 1: 0}  # new position 0 <- old index 1 (Y), new position 1 <- old index 0 (X)

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=2)

    assert result == {"1": ["y_target"], "2": ["x_target"], "false": []}


def test_false_port_connections_always_pass_through_unchanged():
    connections_before = {"1": ["a"], "false": ["stays"]}
    mapping = {0: 0}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=1)

    assert result["false"] == ["stays"]


def test_deleting_all_images_leaves_only_false():
    connections_before = {"1": ["a"], "false": ["f"]}
    mapping = {}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=0)

    assert result == {"false": ["f"]}


def test_multi_connection_port_carries_every_connection():
    connections_before = {"1": ["a", "b", "c"], "false": []}
    mapping = {0: 0}

    result = rewire_ports_after_image_change(connections_before, mapping, num_images=1)

    assert result["1"] == ["a", "b", "c"]
