"""Price history, calendar fees and conservative interval valuation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from egd_client_test.api import PRAGUE
from egd_client_test.costs import (
    capture_schedules,
    cost_statistics,
    standing_costs,
    update_journal,
    validate_cost_settings,
    variable_costs,
)


def config(at, fee="310", backfill=None):
    return validate_cost_settings(
        {"monthly_fee": fee, "backfill_prices": bool(backfill), "backfill_from": backfill}, at
    )


def quarters(at):
    return {
        (at + timedelta(minutes=15 * i)).isoformat(): {"value": "1", "status": "W"}
        for i in range(4)
    }


def journal(at, fee="310", price="5", backfill=None):
    result = {}
    update_journal(result, config(at, fee, backfill), {"price_nt": price, "price_vt": price}, None)
    return result


@pytest.mark.parametrize("year,month,next_month", [(2024, 2, 3), (2026, 3, 4), (2026, 10, 11)])
def test_full_month_fee_including_leap_year_and_dst(year, month, next_month):
    start = datetime(year, month, 1, tzinfo=PRAGUE).astimezone(UTC)
    end = datetime(year, next_month, 1, tzinfo=PRAGUE).astimezone(UTC)
    fees = standing_costs(journal(start), end)
    assert float(sum(map(Decimal, fees.values()))) == pytest.approx(310)
    assert len(fees) == (end - start).total_seconds() / 3600


def test_no_backfill_does_not_price_past_and_new_price_keeps_old_values():
    start = datetime(2026, 9, 20, tzinfo=UTC)
    ledger = journal(start)
    data = {
        **quarters(start - timedelta(hours=1)),
        **quarters(start),
        **quarters(start + timedelta(days=1)),
    }
    update_journal(
        ledger, config(start + timedelta(days=1)), {"price_nt": "8", "price_vt": "8"}, None
    )
    values = variable_costs(data, ledger)
    assert start.isoformat() in values
    assert (start - timedelta(hours=1)).isoformat() not in values
    assert Decimal(values[start.isoformat()]) == 20
    assert Decimal(values[(start + timedelta(days=1)).isoformat()]) == 32


def test_backfill_only_missing_prices_and_only_from_selected_day():
    start = datetime(2026, 9, 20, tzinfo=UTC)
    ledger = journal(start, price="5", backfill="2026-09-19")
    update_journal(
        ledger,
        config(start + timedelta(days=1), backfill="2026-09-18"),
        {"price_nt": "8", "price_vt": "8"},
        None,
    )
    stamps = [start - timedelta(days=i) for i in range(4)]
    values = variable_costs({k: v for stamp in stamps for k, v in quarters(stamp).items()}, ledger)
    assert [Decimal(values[s.isoformat()]) for s in stamps[:3]] == [20, 20, 32]
    assert stamps[3].isoformat() not in values


def test_fee_prorated_from_configuration_instant():
    start = datetime(2026, 9, 20, 0, 30, tzinfo=UTC)
    fees = standing_costs(journal(start, fee="720"), start + timedelta(minutes=30))
    assert list(map(Decimal, fees.values())) == [Decimal("0.5")]


def test_unknown_tariff_not_invented_and_fees_remain_available():
    start = datetime(2026, 9, 20, tzinfo=UTC)
    ledger = {}
    update_journal(ledger, config(start), {"price_nt": "3", "price_vt": "6"}, None)
    result = cost_statistics(quarters(start), ledger, start + timedelta(hours=1))
    assert result["energy_cost"] == []
    assert result["total_cost"] == result["standing_cost"]
    assert result["standing_cost"][-1]["sum"] > 0


def test_replay_is_idempotent_and_zero_price_is_known():
    start = datetime(2026, 9, 20, tzinfo=UTC)
    ledger = journal(start, price="0", fee="0")
    before = cost_statistics(quarters(start), ledger, start + timedelta(hours=1))
    update_journal(ledger, config(start, fee="0"), {"price_nt": "0", "price_vt": "0"}, None)
    assert cost_statistics(quarters(start), ledger, start + timedelta(hours=1)) == before
    assert before["total_cost"][-1]["sum"] == 0
    assert before["priced_hours"] == 1
    assert len(ledger["versions"]) == 1


@pytest.mark.parametrize("value", ["NaN", "inf", "-1", "text"])
def test_invalid_fee(value):
    with pytest.raises(ValueError, match="invalid_monthly_fee"):
        config(datetime.now(UTC), fee=value)


@pytest.mark.parametrize("value", ["", "2026-02-30", "2027-01-01"])
def test_invalid_backfill(value):
    with pytest.raises(ValueError, match="invalid_backfill_date"):
        validate_cost_settings(
            {"backfill_prices": True, "backfill_from": value}, datetime(2026, 9, 24, tzinfo=UTC)
        )


def test_hdo_prices_applied_per_quarter_and_ambiguous_switch_not_estimated():
    start = datetime(2026, 9, 21, 0, tzinfo=PRAGUE).astimezone(UTC)
    records = [
        {
            "od": {"rok": "1900", "mesic": "01", "den": "01"},
            "do": {"rok": "9999", "mesic": "12", "den": "31"},
            "sazby": [
                {
                    "sazba": "test",
                    "dny": [{"denVTydnu": 1, "casy": [{"od": "00:00:00", "do": "00:30:00"}]}],
                }
            ],
        }
    ]
    ledger = {}
    update_journal(ledger, config(start), {"price_nt": "3", "price_vt": "6"}, records)
    assert Decimal(variable_costs(quarters(start), ledger)[start.isoformat()]) == 18
    ledger["versions"][0]["records"][0]["sazby"][0]["dny"][0]["casy"][0]["do"] = "00:20:00"
    assert variable_costs(quarters(start), ledger) == {}


def test_daily_schedule_preserved_while_new_days_can_use_updated_schedule():
    start = datetime(2026, 9, 21, tzinfo=PRAGUE).astimezone(UTC)
    records = [
        {
            "od": {"rok": "1900", "mesic": "01", "den": "01"},
            "do": {"rok": "9999", "mesic": "12", "den": "31"},
            "sazby": [
                {
                    "sazba": "test",
                    "dny": [
                        {"denVTydnu": day, "casy": [{"od": "00:00:00", "do": "00:30:00"}]}
                        for day in range(1, 8)
                    ],
                }
            ],
        }
    ]
    ledger = {}
    settings = config(start)
    update_journal(ledger, settings, {"price_nt": "3", "price_vt": "6"}, records)
    capture_schedules(ledger, settings["effective_from"], quarters(start), records)
    for day in records[0]["sazby"][0]["dny"]:
        day["casy"][0]["do"] = "01:00:00"
    tomorrow = start + timedelta(days=1)
    data = {**quarters(start), **quarters(tomorrow)}
    capture_schedules(ledger, settings["effective_from"], data, records)
    values = variable_costs(data, ledger)
    assert Decimal(values[start.isoformat()]) == 18
    assert Decimal(values[tomorrow.isoformat()]) == 12
