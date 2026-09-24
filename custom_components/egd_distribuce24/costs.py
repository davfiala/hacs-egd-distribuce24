"""Historical price journal and cost calculations; no Home Assistant imports."""

from calendar import monthrange
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from .api import PRAGUE, VALID_STATUSES
from .energy import statistics
from .hdo import HdoError, day_slots

COMPONENTS = ("price_nt", "price_vt", "monthly_fee")


def validate_cost_settings(values, now):
    try:
        amount = Decimal(str(values.get("monthly_fee", "0")).replace(",", "."))
    except InvalidOperation as err:
        raise ValueError("invalid_monthly_fee") from err
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid_monthly_fee")
    mode = values.get("backfill_prices", False)
    earliest = None
    if mode:
        try:
            earliest = date.fromisoformat(str(values.get("backfill_from", "")))
        except ValueError as err:
            raise ValueError("invalid_backfill_date") from err
        if earliest > now.astimezone(PRAGUE).date():
            raise ValueError("invalid_backfill_date")
    return {
        "monthly_fee": str(amount),
        "backfill_prices": bool(mode),
        "backfill_from": earliest.isoformat() if earliest else None,
        "effective_from": now.astimezone(UTC).isoformat(),
    }


def update_journal(ledger, config, hdo, records):
    """Record every configuration revision; never rewrite old prices or fills."""
    revision = config["effective_from"]
    versions = ledger.setdefault("versions", [])
    snapshot = next((v for v in versions if v["revision"] == revision), None)
    if snapshot is None:
        snapshot = {
            "revision": revision,
            "start": revision,
            "monthly_fee": config["monthly_fee"],
            "price_nt": str(hdo["price_nt"]) if hdo else None,
            "price_vt": str(hdo["price_vt"]) if hdo else None,
            "records": None,
        }
        versions.append(snapshot)
        versions.sort(key=lambda v: v["start"])
        if config["backfill_prices"]:
            start = datetime.combine(date.fromisoformat(config["backfill_from"]), time.min, PRAGUE)
            ledger.setdefault("fills", []).append(
                {
                    **deepcopy(snapshot),
                    "start": start.astimezone(UTC).isoformat(),
                    "end": revision,
                }
            )
    # An outage at configuration time must not permanently prevent valuation.
    if snapshot["records"] is None and records:
        snapshot["records"] = deepcopy(records)
        for fill in ledger.get("fills", []):
            if fill["revision"] == revision:
                fill["records"] = deepcopy(records)


def _source(ledger, stamp, component):
    selected = None
    for version in ledger.get("versions", []):
        if datetime.fromisoformat(version["start"]) <= stamp:
            selected = version
    if selected is not None and selected.get(component) is not None:
        return selected
    # First successful fill wins, including after later price changes.
    for fill in ledger.get("fills", []):
        if (
            datetime.fromisoformat(fill["start"]) <= stamp < datetime.fromisoformat(fill["end"])
            and fill.get(component) is not None
        ):
            return fill
    return None


def _boundaries(ledger, start, end):
    points = {start, end}
    for item in ledger.get("versions", []) + ledger.get("fills", []):
        for key in ("start", "end"):
            if key in item:
                point = datetime.fromisoformat(item[key])
                if start < point < end:
                    points.add(point)
    return sorted(points)


def capture_schedules(ledger, revision, quarters, records):
    """Keep known daily schedules stable, but learn schedules for newly metered days."""
    if not records:
        return
    days = {datetime.fromisoformat(stamp).astimezone(PRAGUE).date() for stamp in quarters}
    days |= {day - timedelta(days=1) for day in days}
    for item in ledger.get("versions", []) + ledger.get("fills", []):
        if item["revision"] != revision:
            continue
        schedules = item.setdefault("schedules", {})
        for day in days:
            if day.isoformat() in schedules:
                continue
            try:
                schedules[day.isoformat()] = day_slots(records, day)
            except HdoError:
                pass  # Retry unknown days on a later successful HDO refresh.


def _quarter_price(ledger, stamp, cache):
    finish = stamp + timedelta(minutes=15)
    if len(_boundaries(ledger, stamp, finish)) > 2:
        return None  # Don't invent consumption allocation across a price change.
    nt = _source(ledger, stamp, "price_nt")
    vt = _source(ledger, stamp, "price_vt")
    if nt is None or vt is None:
        return None
    if nt["price_nt"] == vt["price_vt"]:
        return Decimal(nt["price_nt"])
    if nt["revision"] != vt["revision"] or not nt.get("records"):
        return None
    local_day = stamp.astimezone(PRAGUE).date()
    key = (nt["revision"], local_day)
    if key not in cache:
        midnight = datetime.combine(local_day, time.min, PRAGUE)
        intervals = []
        try:
            for offset in (-1, 0):
                day = local_day + timedelta(days=offset)
                try:
                    slots = nt.get("schedules", {}).get(day.isoformat())
                    if slots is None:
                        slots = day_slots(nt["records"], day)
                except HdoError:
                    if offset == 0:
                        raise
                    continue
                origin = midnight + timedelta(days=offset)
                for begin, end in slots:
                    if end < begin:
                        end += 1440
                    intervals.append(
                        (
                            (origin + timedelta(minutes=begin)).astimezone(UTC),
                            (origin + timedelta(minutes=end)).astimezone(UTC),
                        )
                    )
            cache[key] = intervals
        except HdoError:
            cache[key] = None
    intervals = cache[key]
    if intervals is None:
        return None
    cuts = {stamp, finish}
    for begin, end in intervals:
        cuts.update(p for p in (begin, end) if stamp < p < finish)
    prices = set()
    points = sorted(cuts)
    for begin, end in zip(points, points[1:]):
        midpoint = begin + (end - begin) / 2
        is_nt = any(a <= midpoint < b for a, b in intervals)
        prices.add(Decimal(nt["price_nt"] if is_nt else vt["price_vt"]))
    return prices.pop() if len(prices) == 1 else None


def variable_costs(quarters, ledger):
    groups, cache = {}, {}
    for stamp_text, row in quarters.items():
        stamp = datetime.fromisoformat(stamp_text)
        if row["status"] not in VALID_STATUSES or row["value"] is None:
            continue
        price = _quarter_price(ledger, stamp, cache)
        if price is not None:
            hour = stamp.replace(minute=0, second=0, microsecond=0).isoformat()
            groups.setdefault(hour, {})[stamp.minute] = Decimal(row["value"]) * price
    return {
        hour: str(sum(group.values(), Decimal(0)))
        for hour, group in groups.items()
        if set(group) == {0, 15, 30, 45}
    }


def standing_costs(ledger, end):
    """Distribute a monthly fee evenly across calendar days and real day seconds."""
    entries = ledger.get("versions", []) + ledger.get("fills", [])
    if not entries:
        return {}
    first = min(datetime.fromisoformat(v["start"]) for v in entries)
    hour = first.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    result = {}
    while hour + timedelta(hours=1) <= end:
        total, known = Decimal(0), False
        points = _boundaries(ledger, hour, hour + timedelta(hours=1))
        for begin, finish in zip(points, points[1:]):
            source = _source(ledger, begin, "monthly_fee")
            if source is None:
                continue
            day = begin.astimezone(PRAGUE).date()
            midnight = datetime.combine(day, time.min, PRAGUE)
            seconds = int(
                (
                    (midnight + timedelta(days=1)).astimezone(UTC) - midnight.astimezone(UTC)
                ).total_seconds()
            )
            total += (
                Decimal(source["monthly_fee"])
                / monthrange(day.year, day.month)[1]
                * Decimal(str((finish - begin).total_seconds()))
                / seconds
            )
            known = True
        if known:
            result[hour.isoformat()] = str(total)
        hour += timedelta(hours=1)
    return result


def total_statistics(variable, standing):
    """Known costs only; missing consumption costs are excluded, never estimated.

    Publish fees in their own hours even when consumption or prices are missing.
    The UI exposes coverage; these totals can be incomplete, not a supplier bill.
    """
    return statistics(
        {
            stamp: str(Decimal(variable.get(stamp, "0")) + Decimal(standing.get(stamp, "0")))
            for stamp in set(variable) | set(standing)
        }
    )


def cost_statistics(quarters, ledger, end):
    variable = variable_costs(quarters, ledger)
    standing = standing_costs(ledger, end)
    return {
        "energy_cost": statistics(variable),
        "standing_cost": statistics(standing),
        "total_cost": total_statistics(variable, standing),
        "priced_hours": len(variable),
    }
