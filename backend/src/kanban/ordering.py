"""Sortable `order` scheme shared by columns and tasks (see _docs/specs.md § Drag and Drop)."""

GAP = 1000.0
MIN_GAP = 1e-6


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
    if before is None or after is None:
        return False
    return (after - before) < MIN_GAP


def respaced_values(count: int) -> list[float]:
    return [i * GAP for i in range(count)]
