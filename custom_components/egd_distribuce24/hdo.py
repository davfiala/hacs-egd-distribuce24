"""Public EG.D HDO schedules, independent of measured-energy credentials."""

import math
import re
from datetime import UTC, date, datetime, time, timedelta

import aiohttp

from .api import PRAGUE

HDO_URL = "https://hdo.distribuce24.cz/casy"
REGION_URL = "https://hdo.distribuce24.cz/region"


class HdoError(Exception):
    """Schedule unavailable or ambiguous; never assume high tariff on failure."""


def normalize_settings(values):
    postcode = re.sub(r"\s", "", values.get("postcode", ""))
    code = values.get("hdo_code", "").strip()
    if not re.fullmatch(r"\d{5}", postcode) or not code or len(code) > 60:
        raise HdoError("invalid_hdo")
    prices = {}
    for key in ("price_nt", "price_vt"):
        try:
            prices[key] = float(str(values[key]).replace(",", "."))
        except (KeyError, ValueError, TypeError) as err:
            raise HdoError("invalid_price") from err
        if not math.isfinite(prices[key]) or prices[key] < 0:
            raise HdoError("invalid_price")
    return {"postcode": postcode, "hdo_code": code, **prices}


def _code(value):
    return re.sub(r"\s", "", str(value)).replace("-", "_").casefold()


def select_records(regions, records, settings):
    matching_regions = {r["Region"] for r in regions if str(r.get("PSC")) == settings["postcode"]}
    if not matching_regions:
        raise HdoError("unknown_postcode")
    wanted = _code(settings["hdo_code"])
    selected = []
    for record in records:
        regional = record.get("region") in {"ZAPAD", "VYCHOD"}
        if regional and record["region"] not in matching_regions:
            continue
        keys = (
            ("kodHdo_A", "kodHdo_B", "kodHdo_C", "kodHdo_D", "kodHdo_E")
            if regional
            else ("kodHdo_A",)
        )
        if any(wanted == _code(record.get(key, "")) for key in keys):
            selected.append(record)
    if not selected:
        raise HdoError("unknown_hdo")
    identities = {(r["region"], r["kodHdo_A"]) for r in selected}
    if len(identities) != 1:
        raise HdoError("ambiguous_hdo")
    if settings.get("schedule"):
        selected = [
            {
                **row,
                "sazby": [rate for rate in row["sazby"] if rate["sazba"] == settings["schedule"]],
            }
            for row in selected
        ]
        selected = [row for row in selected if row["sazby"]]
        if not selected:
            raise HdoError("unknown_hdo")
    return selected


def available_plans(records):
    return sorted({rate["sazba"] for record in records for rate in record["sazby"]})


def is_holiday(day):
    """Czech public holidays, including Easter (Gregorian computus)."""
    if (day.month, day.day) in {
        (1, 1),
        (5, 1),
        (5, 8),
        (7, 5),
        (7, 6),
        (9, 28),
        (10, 28),
        (11, 17),
        (12, 24),
        (12, 25),
        (12, 26),
    }:
        return True
    year = day.year
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    value = h + weekday_offset - 7 * m + 114
    easter = date(year, value // 31, value % 31 + 1)
    return day in {easter - timedelta(days=2), easter + timedelta(days=1)}


def _valid(record, day):
    first, last = record["od"], record["do"]
    begin = (int(first["mesic"]), int(first["den"]))
    end = (int(last["mesic"]), int(last["den"]))
    if int(first["rok"]) == 9999:
        current = (day.month, day.day)
        return begin <= current <= end if begin <= end else current >= begin or current <= end
    return date(int(first["rok"]), *begin) <= day <= date(int(last["rok"]), *end)


def _minute(value, *, ending=False):
    hour, minute, second = map(int, value.split(":"))
    if second or not 0 <= hour <= 24 or not 0 <= minute <= 59 or (hour == 24 and minute):
        raise HdoError("invalid_schedule")
    result = hour * 60 + minute
    # The published end-of-day sentinel is 23:59:00, covering the last minute.
    return 1440 if ending and result == 1439 else result


def day_slots(records, day):
    """Require a unique complete plan; absent data is unknown, not VT."""
    plans = []
    try:
        for record in records:
            if not _valid(record, day):
                continue
            for rate in record["sazby"]:
                # D61d is a weekend rate; a weekday holiday does not extend it.
                weekday = (
                    7
                    if is_holiday(day) and "D61d" not in rate.get("sazba", "")
                    else day.isoweekday()
                )
                for item in rate["dny"]:
                    if int(item["denVTydnu"]) == weekday:
                        plans.append(
                            tuple(
                                sorted(
                                    (_minute(s["od"]), _minute(s["do"], ending=True))
                                    for s in item["casy"]
                                )
                            )
                        )
    except (KeyError, ValueError, TypeError) as err:
        raise HdoError("invalid_schedule") from err
    if not plans:
        raise HdoError("no_schedule")
    if len(set(plans)) != 1:
        raise HdoError("ambiguous_hdo")
    return plans[0]


def tariff_state(records, now):
    """Resolve intervals in Prague, compare in UTC across daylight-saving folds."""
    now = now.astimezone(UTC)
    today = now.astimezone(PRAGUE).date()
    today_plan = day_slots(records, today)
    intervals = []
    covered = set()
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        try:
            slots = day_slots(records, day)
        except HdoError:
            if offset == 0:
                raise
            continue
        covered.add(day)
        midnight = datetime.combine(day, time.min, PRAGUE)
        for begin, end in slots:
            if begin == end:
                raise HdoError("invalid_schedule")
            if end < begin:
                end += 1440
            intervals.append(
                (
                    (midnight + timedelta(minutes=begin)).astimezone(UTC),
                    (midnight + timedelta(minutes=end)).astimezone(UTC),
                )
            )
    merged = []
    for begin, end in sorted(intervals):
        if end <= begin:
            continue
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((begin, end))
    active = any(begin <= now < end for begin, end in merged)
    # Don't claim a future switch beyond continuously known calendar days.
    horizon = today
    while horizon in covered:
        horizon += timedelta(days=1)
    limit = datetime.combine(horizon, time.min, PRAGUE).astimezone(UTC)
    changes = [boundary for interval in merged for boundary in interval if now < boundary < limit]
    return {
        "tariff": "NT" if active else "VT",
        "next_change": min(changes) if changes else None,
        "nt_today": [
            {"from": f"{a // 60:02}:{a % 60:02}", "to": f"{b // 60:02}:{b % 60:02}"}
            for a, b in today_plan
        ],
    }


class HdoClient:
    def __init__(self, session):
        self.session = session

    async def catalog(self):
        result = []
        for url in (REGION_URL, HDO_URL):
            try:
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30), allow_redirects=False
                ) as response:
                    if response.status != 200:
                        raise HdoError("cannot_connect_hdo")
                    data = await response.json()
                    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                        raise HdoError("invalid_schedule")
                    result.append(data)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise HdoError("cannot_connect_hdo") from err
        return tuple(result)
