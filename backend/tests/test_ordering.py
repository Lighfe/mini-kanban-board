from kanban.ordering import append_order, needs_respacing, order_between, respaced_values


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
