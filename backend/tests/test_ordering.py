from kanban.ordering import FRONT_BOUNDARY_FLOOR, append_order, needs_respacing, order_between, respaced_values


def test_append_order_on_empty_list_returns_first_gap_value():
    assert append_order([]) == 1000.0


def test_append_order_adds_one_gap_past_the_last_value():
    assert append_order([1000.0, 2000.0]) == 3000.0


def test_order_between_two_values_is_the_midpoint():
    assert order_between(1000.0, 2000.0) == 1500.0


def test_order_between_none_and_a_value_is_half_that_value():
    assert order_between(None, 1000.0) == 500.0


def test_order_between_a_value_and_none_is_one_gap_past_it():
    assert order_between(1000.0, None) == 2000.0


def test_order_between_none_and_none_is_the_first_gap_value():
    assert order_between(None, None) == 1000.0


def test_needs_respacing_is_false_when_theres_room_between_neighbors():
    assert needs_respacing(1000.0, 2000.0) is False


def test_needs_respacing_is_true_when_neighbors_are_too_close_to_split():
    assert needs_respacing(1000.0, 1000.0000000001) is True


def test_needs_respacing_is_false_at_either_open_end():
    assert needs_respacing(None, 1000.0) is False
    assert needs_respacing(1000.0, None) is False


def test_needs_respacing_is_false_when_both_neighbors_are_none():
    assert needs_respacing(None, None) is False


def test_needs_respacing_is_true_when_front_insert_value_has_fallen_below_the_floor():
    # Repeated front-inserts halve `order_between(None, after)` towards 0.0
    # forever unless something resets the scale. Below FRONT_BOUNDARY_FLOOR
    # we must trigger a respace well before float precision is actually lost.
    assert needs_respacing(None, FRONT_BOUNDARY_FLOOR - 0.0001) is True
    assert needs_respacing(None, 0.5) is True


def test_needs_respacing_is_false_at_or_above_the_front_boundary_floor():
    assert needs_respacing(None, FRONT_BOUNDARY_FLOOR) is False
    assert needs_respacing(None, FRONT_BOUNDARY_FLOOR + 0.0001) is False


def test_needs_respacing_append_only_case_is_unaffected_by_the_floor():
    # The after-is-None (append, unbounded growth) case has no realistic
    # precision problem the way approaching zero does, so it must never
    # trigger respacing regardless of magnitude.
    assert needs_respacing(1000.0, None) is False
    assert needs_respacing(0.0000001, None) is False


def test_respaced_values_returns_round_multiples_of_the_gap():
    assert respaced_values(4) == [0.0, 1000.0, 2000.0, 3000.0]


def test_respaced_values_empty():
    assert respaced_values(0) == []


from kanban.store import Store


def test_store_starts_empty_and_reset_clears_it():
    store = Store()
    assert store.users == {}
    store.users["u1"] = {"id": "u1"}
    store.reset()
    assert store.users == {}


def test_member_for_returns_member_when_exists():
    store = Store()
    store.board_members["m1"] = {"id": "m1", "boardId": "b1", "userId": "u1", "role": "admin"}
    store.board_members["m2"] = {"id": "m2", "boardId": "b2", "userId": "u1", "role": "viewer"}

    member = store.member_for("b1", "u1")
    assert member == {"id": "m1", "boardId": "b1", "userId": "u1", "role": "admin"}


def test_member_for_returns_none_when_not_exists():
    store = Store()
    store.board_members["m1"] = {"id": "m1", "boardId": "b1", "userId": "u1", "role": "admin"}

    assert store.member_for("b1", "u2") is None
    assert store.member_for("b2", "u1") is None
    assert store.member_for("b2", "u2") is None


def test_members_for_board_returns_only_members_for_that_board():
    store = Store()
    store.board_members["m1"] = {"id": "m1", "boardId": "b1", "userId": "u1", "role": "admin"}
    store.board_members["m2"] = {"id": "m2", "boardId": "b1", "userId": "u2", "role": "viewer"}
    store.board_members["m3"] = {"id": "m3", "boardId": "b2", "userId": "u1", "role": "editor"}
    store.board_members["m4"] = {"id": "m4", "boardId": "b2", "userId": "u3", "role": "admin"}

    b1_members = store.members_for_board("b1")
    assert len(b1_members) == 2
    assert {"id": "m1", "boardId": "b1", "userId": "u1", "role": "admin"} in b1_members
    assert {"id": "m2", "boardId": "b1", "userId": "u2", "role": "viewer"} in b1_members

    b2_members = store.members_for_board("b2")
    assert len(b2_members) == 2
    assert {"id": "m3", "boardId": "b2", "userId": "u1", "role": "editor"} in b2_members
    assert {"id": "m4", "boardId": "b2", "userId": "u3", "role": "admin"} in b2_members

    assert store.members_for_board("b3") == []


def test_columns_for_board_returns_sorted_by_order():
    store = Store()
    # Insert out of order to test sorting
    store.columns["c2"] = {"id": "c2", "boardId": "b1", "name": "In Progress", "order": 2000.0}
    store.columns["c3"] = {"id": "c3", "boardId": "b1", "name": "Done", "order": 3000.0}
    store.columns["c1"] = {"id": "c1", "boardId": "b1", "name": "Todo", "order": 1000.0}
    store.columns["c4"] = {"id": "c4", "boardId": "b2", "name": "Backlog", "order": 500.0}

    b1_columns = store.columns_for_board("b1")
    assert len(b1_columns) == 3
    assert b1_columns[0]["id"] == "c1"
    assert b1_columns[1]["id"] == "c2"
    assert b1_columns[2]["id"] == "c3"

    b2_columns = store.columns_for_board("b2")
    assert len(b2_columns) == 1
    assert b2_columns[0]["id"] == "c4"

    assert store.columns_for_board("b3") == []


def test_tasks_for_column_returns_non_archived_sorted_by_order():
    store = Store()
    # Insert out of order to test sorting
    store.tasks["t2"] = {"id": "t2", "columnId": "c1", "order": 2000.0, "archived": False}
    store.tasks["t3"] = {"id": "t3", "columnId": "c1", "order": 3000.0, "archived": True}
    store.tasks["t1"] = {"id": "t1", "columnId": "c1", "order": 1000.0, "archived": False}
    store.tasks["t4"] = {"id": "t4", "columnId": "c2", "order": 1500.0, "archived": False}

    # Default: exclude archived
    c1_tasks = store.tasks_for_column("c1")
    assert len(c1_tasks) == 2
    assert c1_tasks[0]["id"] == "t1"
    assert c1_tasks[1]["id"] == "t2"
    assert all(not t["archived"] for t in c1_tasks)

    # With include_archived=True
    c1_all_tasks = store.tasks_for_column("c1", include_archived=True)
    assert len(c1_all_tasks) == 3
    assert c1_all_tasks[0]["id"] == "t1"
    assert c1_all_tasks[1]["id"] == "t2"
    assert c1_all_tasks[2]["id"] == "t3"

    # Other column
    c2_tasks = store.tasks_for_column("c2")
    assert len(c2_tasks) == 1
    assert c2_tasks[0]["id"] == "t4"

    assert store.tasks_for_column("c3") == []
