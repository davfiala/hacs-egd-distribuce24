"""Pure, deterministic aggregation and correction of historical energy."""

from datetime import datetime, timedelta
from decimal import Decimal

from .api import VALID_STATUSES


def complete_hours(readings):
    """Only publish hours with all four acceptable quarter-hour samples."""
    groups = {}
    for reading in readings:
        hour = reading.timestamp.replace(minute=0, second=0, microsecond=0)
        groups.setdefault(hour, {})[reading.timestamp.minute] = reading
    return {
        hour.isoformat(): str(sum((r.value for r in group.values()), Decimal(0)))
        for hour, group in groups.items()
        if set(group) == {0, 15, 30, 45}
        and all(r.status in VALID_STATUSES and r.value is not None for r in group.values())
    }


def statistics(hours):
    """Rebuild absolute cumulative sums, including corrections, idempotently."""
    if not hours:
        return []
    total = Decimal(0)
    ordered = sorted(hours)
    # A zero baseline preserves the energy of the very first imported hour.
    result = [{"start": datetime.fromisoformat(ordered[0]) - timedelta(hours=1), "sum": 0.0}]
    for stamp in ordered:
        total += Decimal(hours[stamp])
        result.append({"start": datetime.fromisoformat(stamp), "sum": float(total)})
    return result
