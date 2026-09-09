"""Sortable `order` scheme shared by columns and tasks (see _docs/specs.md § Drag and Drop)."""

GAP = 1000.0
MIN_GAP = 1e-6
# Floor for the front-boundary case in `needs_respacing`: repeatedly moving
# an item to the very front of its list keeps halving `order_between(None,
# after)`, and since `needs_respacing` never fires when a neighbor is None,
# nothing ever resets the scale — after enough repeated front-inserts the
# value underflows toward 0.0 and two items can collide on an identical
# order. FRONT_BOUNDARY_FLOOR is four orders of magnitude below GAP, so
# ordinary usage (a handful of front-inserts) never triggers a respace, but
# repeated front-inserts trip it after a small, bounded number of halvings
# instead of after ~1000+ halvings right at float-underflow territory.
FRONT_BOUNDARY_FLOOR = 1.0


def append_order(sorted_orders: list[float]) -> float:
    if not sorted_orders:
        return GAP
    return sorted_orders[-1] + GAP


def order_between(before: float | None, after: float | None) -> float:
    if before is None and after is None:
        return GAP
    if before is None:
        return after / 2
    if after is None:
        return before + GAP
    return (before + after) / 2


def needs_respacing(before: float | None, after: float | None) -> bool:
    if before is None and after is None:
        return False
    if before is None:
        return after < FRONT_BOUNDARY_FLOOR
    if after is None:
        return False
    return (after - before) < MIN_GAP


def respaced_values(count: int) -> list[float]:
    return [i * GAP for i in range(count)]
