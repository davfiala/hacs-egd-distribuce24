from copy import deepcopy
from datetime import date, datetime

import pytest
from egd_client_test.hdo import (
    PRAGUE,
    HdoError,
    day_slots,
    is_holiday,
    normalize_settings,
    select_records,
    tariff_state,
)


def record(slots=None, *, plan="D57d-relé1", region="SM", code="Example_1"):
    return {
        "region": region,
        "kodHdo_A": code,
        "od": {"rok": "1900", "mesic": "01", "den": "01"},
        "do": {"rok": "9999", "mesic": "12", "den": "31"},
        "sazby": [
            {
                "sazba": plan,
                "dny": [
                    {
                        "denVTydnu": d,
                        "casy": slots
                        if slots is not None
                        else [
                            {"od": "00:00:00", "do": "07:30:00"},
                            {"od": "22:30:00", "do": "23:59:00"},
                        ],
                    }
                    for d in range(1, 8)
                ],
            }
        ],
    }


def test_postcode_code_and_zero_price():
    settings = normalize_settings(
        {"postcode": "123 45", "hdo_code": "example-1", "price_nt": "0", "price_vt": "6,25"}
    )
    assert settings["price_nt"] == 0
    assert settings["price_vt"] == 6.25
    assert select_records([{"PSC": "12345", "Region": "ZAPAD"}], [record()], settings)


@pytest.mark.parametrize("price", ["", "NaN", "inf", "-1", "hello"])
def test_invalid_prices(price):
    with pytest.raises(HdoError, match="invalid_price"):
        normalize_settings(
            {"postcode": "12345", "hdo_code": "a", "price_nt": price, "price_vt": "1"}
        )


def test_regional_code_does_not_match_wrong_region():
    with pytest.raises(HdoError, match="unknown_hdo"):
        select_records(
            [{"PSC": "12345", "Region": "ZAPAD"}],
            [record(region="VYCHOD")],
            {"postcode": "12345", "hdo_code": "Example_1"},
        )


def test_multiple_relays_must_not_be_merged():
    records = [record(), record([{"od": "01:00:00", "do": "05:00:00"}], plan="D57d-relé2")]
    with pytest.raises(HdoError, match="ambiguous_hdo"):
        day_slots(records, date(2026, 9, 24))
    chosen = select_records(
        [{"PSC": "12345", "Region": "ZAPAD"}],
        records,
        {"postcode": "12345", "hdo_code": "Example_1", "schedule": "D57d-relé1"},
    )
    assert len(chosen) == 1


@pytest.mark.parametrize(
    "hour,minute,expected", [(7, 29, "NT"), (7, 30, "VT"), (22, 30, "NT"), (23, 59, "NT")]
)
def test_switch_and_end_of_day(hour, minute, expected):
    state = tariff_state([record()], datetime(2026, 9, 24, hour, minute, tzinfo=PRAGUE))
    assert state["tariff"] == expected


def test_midnight_is_not_a_switch_for_continuous_nt():
    state = tariff_state([record()], datetime(2026, 9, 24, 23, 59, tzinfo=PRAGUE))
    assert state["next_change"].astimezone(PRAGUE) == datetime(2026, 9, 25, 7, 30, tzinfo=PRAGUE)


def test_season_changes_tomorrow_using_its_own_validity():
    summer = record([{"od": "01:00:00", "do": "02:00:00"}])
    winter = record([{"od": "03:00:00", "do": "04:00:00"}])
    summer["od"] = {"rok": "9999", "mesic": "04", "den": "01"}
    summer["do"] = {"rok": "9999", "mesic": "09", "den": "30"}
    winter["od"] = {"rok": "9999", "mesic": "10", "den": "01"}
    winter["do"] = {"rok": "9999", "mesic": "03", "den": "31"}
    state = tariff_state([summer, winter], datetime(2026, 9, 30, 23, 0, tzinfo=PRAGUE))
    assert state["next_change"].astimezone(PRAGUE).hour == 3


def test_expired_schedule_is_unknown_not_vt():
    old = record()
    old["do"]["rok"] = "2025"
    with pytest.raises(HdoError, match="no_schedule"):
        tariff_state([old], datetime(2026, 9, 24, tzinfo=PRAGUE))


@pytest.mark.parametrize("day", [date(2026, 4, 3), date(2026, 4, 6), date(2026, 9, 28)])
def test_czech_holidays(day):
    assert is_holiday(day)


def test_holiday_sunday_plan_and_weekend_rate_exception():
    normal = record()
    normal["sazby"][0]["dny"][6]["casy"] = []
    weekend = deepcopy(normal)
    weekend["sazby"][0]["sazba"] = "D61d"
    assert day_slots([normal], date(2026, 9, 28)) == ()
    assert day_slots([weekend], date(2026, 9, 28))


def test_dst_fold_preserves_second_occurrence():
    rows = [record([{"od": "01:00:00", "do": "03:00:00"}])]
    for fold in (0, 1):
        assert (
            tariff_state(rows, datetime(2026, 10, 25, 2, 30, tzinfo=PRAGUE, fold=fold))["tariff"]
            == "NT"
        )
