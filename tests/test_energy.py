from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from egd_client_test.api import PRAGUE, ApiError, Reading, parse_readings
from egd_client_test.energy import complete_hours, statistics

EAN = "859182400000000000"
START = datetime(2026, 9, 19, tzinfo=UTC)


def samples(count=4):
    return [Reading(START + timedelta(minutes=15 * i), Decimal("0.1"), "W") for i in range(count)]


def payload(rows, units="kWh"):
    return [{"ean/eic": EAN, "profile": "DCQC", "units": units, "data": rows}]


def row(value=0.1, status="W"):
    return {"timestamp": "2026-09-19T00:00:00Z", "value": value, "status": status}


def test_decimal_aggregation_and_baseline():
    hours = complete_hours(samples())
    assert list(hours.values()) == ["0.4"]
    result = statistics(hours)
    assert result[0] == {"start": START - timedelta(hours=1), "sum": 0.0}
    assert result[1]["sum"] == 0.4


def test_production_object_and_documented_list_are_equivalent():
    series = payload([row()])
    assert parse_readings(series[0], EAN, "DCQC") == parse_readings(series, EAN, "DCQC")


def test_partial_hour_not_published():
    assert complete_hours(samples(3)) == {}


@pytest.mark.parametrize("status", ["F", "G", "V", "UNKNOWN"])
def test_unreliable_hour_not_published(status):
    readings = samples()
    readings[1] = Reading(readings[1].timestamp, Decimal("0.1"), status)
    assert complete_hours(readings) == {}


@pytest.mark.parametrize("status", ["W", "B", "E", "M", "N"])
def test_valid_and_corrected_values_accepted(status):
    readings = [Reading(r.timestamp, r.value, status) for r in samples()]
    assert list(complete_hours(readings).values()) == ["0.4"]


def test_missing_value_not_zero():
    readings = samples()
    readings[0] = Reading(START, None, "W")
    assert complete_hours(readings) == {}


def test_replay_and_correction_do_not_double_count():
    hours = complete_hours(samples(8))
    first = statistics(hours)
    hours.update(complete_hours(samples(8)))
    assert statistics(hours) == first
    hours[START.isoformat()] = "0.8"
    assert statistics(hours)[-1]["sum"] == 1.2


def test_gap_later_filled_recalculates_following_sums():
    hours = {START.isoformat(): "1", (START + timedelta(hours=2)).isoformat(): "3"}
    assert statistics(hours)[-1]["sum"] == 4
    hours[(START + timedelta(hours=1)).isoformat()] = "2"
    assert statistics(hours)[-1]["sum"] == 6


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", "bad"])
def test_invalid_energy_rejected(value):
    with pytest.raises(ApiError):
        parse_readings(payload([row(value)]), EAN, "DCQC")


def test_zero_and_missing_are_distinct():
    assert parse_readings(payload([row(0)]), EAN, "DCQC")[0].value == 0
    assert parse_readings(payload([row(None, "F")]), EAN, "DCQC")[0].value is None


def test_wrong_unit_and_identity_rejected():
    with pytest.raises(ApiError):
        parse_readings(payload([row()], "kW"), EAN, "DCQC")
    with pytest.raises(ApiError):
        parse_readings(payload([row()]), "859182400000000001", "DCQC")


def test_duplicates():
    assert len(parse_readings(payload([row(), row()]), EAN, "DCQC")) == 1
    with pytest.raises(ApiError):
        parse_readings(payload([row(), row(0.2)]), EAN, "DCQC")


@pytest.mark.parametrize(
    "day,expected", [(datetime(2026, 3, 29), 23), (datetime(2026, 10, 25), 25)]
)
def test_dst_days_preserve_all_utc_hours(day, expected):
    start = day.replace(tzinfo=PRAGUE).astimezone(UTC)
    end = (day + timedelta(days=1)).replace(tzinfo=PRAGUE).astimezone(UTC)
    count = int((end - start).total_seconds() / 900)
    readings = [
        Reading(start + timedelta(minutes=15 * i), Decimal("0.1"), "W") for i in range(count)
    ]
    assert len(complete_hours(readings)) == expected
