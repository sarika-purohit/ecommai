from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from agent.delivery import (
    INDIA,
    DeliveryEstimate,
    PincodeInfo,
    UnknownPincode,
    buy_link,
    estimate,
    lookup,
    total_price,
)

# 12 October 2026 is a Monday; 18 October is the Sunday of that week.
MONDAY = date(2026, 10, 12)
MONDAY_10AM = datetime(2026, 10, 12, 10, 0)


def day(n: int) -> date:
    return date(2026, 10, n)


@pytest.mark.parametrize(
    "warehouse, pincode, zone, days_min, days_max, delivery_date",
    [
        pytest.param("Mumbai", "400001", "same_city", 1, 2, day(14), id="same city Mumbai"),
        pytest.param("Delhi", "110001", "same_city", 1, 2, day(14), id="same city Delhi"),
        pytest.param("Bengaluru", "560001", "same_city", 1, 2, day(14), id="same city Bengaluru"),
        pytest.param("Kolkata", "700001", "same_city", 1, 2, day(14), id="same city Kolkata"),
        pytest.param("Mumbai", "110001", "big_city", 2, 3, day(15), id="Mumbai to Delhi"),
        pytest.param("Delhi", "600001", "big_city", 2, 3, day(15), id="Delhi to Chennai"),
        pytest.param("Bengaluru", "411001", "big_city", 2, 3, day(15), id="Bengaluru to Pune"),
        pytest.param("Mumbai", "302001", "other", 3, 5, day(17), id="Mumbai to Jaipur"),
        pytest.param("Mumbai", "421301", "other", 3, 5, day(17), id="Thane district is not Mumbai"),
        pytest.param("Kolkata", "781001", "remote", 6, 8, day(21), id="Kolkata to Guwahati"),
        pytest.param("Delhi", "194101", "remote", 6, 8, day(21), id="Delhi to Leh"),
        pytest.param("Bengaluru", "744101", "remote", 6, 8, day(21), id="Bengaluru to Port Blair"),
    ],
)
def test_each_zone_promises_the_last_day_of_its_range(warehouse, pincode, zone, days_min, days_max, delivery_date):
    assert estimate(warehouse, pincode, now=MONDAY_10AM) == DeliveryEstimate(
        zone=zone, days_min=days_min, days_max=days_max, ship_date=MONDAY, delivery_date=delivery_date
    )


@pytest.mark.parametrize(
    "ordered_at, ship_date, delivery_date",
    [
        pytest.param(datetime(2026, 10, 17, 19, 0), day(19), day(21), id="saturday evening ships monday"),
        pytest.param(datetime(2026, 10, 17, 11, 0), day(17), day(20), id="saturday morning ships saturday"),
        pytest.param(datetime(2026, 10, 18, 10, 0), day(19), day(21), id="sunday morning ships monday"),
        pytest.param(datetime(2026, 10, 18, 16, 0), day(19), day(21), id="sunday afternoon ships monday"),
        pytest.param(datetime(2026, 10, 16, 10, 0), day(16), day(19), id="transit over sunday skips it"),
    ],
)
def test_no_shipping_or_delivery_on_sundays(ordered_at, ship_date, delivery_date):
    result = estimate("Mumbai", "400001", now=ordered_at)
    assert (result.ship_date, result.delivery_date) == (ship_date, delivery_date)


def test_remote_transit_over_sunday_counts_only_working_days():
    result = estimate("Mumbai", "781001", now=datetime(2026, 10, 15, 9, 0))
    assert result.ship_date == day(15)
    assert result.delivery_date == day(24)


@pytest.mark.parametrize(
    "warehouse, pincode", [("Mumbai", "400001"), ("Mumbai", "110001"), ("Delhi", "302001"), ("Kolkata", "781001")]
)
def test_dates_never_fall_on_a_sunday_at_any_hour_of_the_week(warehouse, pincode):
    for hour in range(7 * 24):
        result = estimate(warehouse, pincode, now=MONDAY_10AM + timedelta(hours=hour))
        assert result.ship_date.weekday() != 6
        assert result.delivery_date.weekday() != 6
        working_days = sum(
            (result.ship_date + timedelta(days=n)).weekday() != 6
            for n in range(1, (result.delivery_date - result.ship_date).days + 1)
        )
        assert working_days == result.days_max


@pytest.mark.parametrize(
    "ordered_at, ship_date, delivery_date",
    [
        pytest.param(datetime(2026, 10, 12, 13, 59), day(12), day(14), id="13:59 ships today"),
        pytest.param(datetime(2026, 10, 12, 14, 0), day(13), day(15), id="2 pm sharp ships tomorrow"),
        pytest.param(datetime(2026, 10, 12, 14, 1), day(13), day(15), id="14:01 ships tomorrow"),
        pytest.param(datetime(2026, 10, 12, 13, 59, tzinfo=INDIA), day(12), day(14), id="aware India time"),
        pytest.param(datetime(2026, 10, 12, 8, 29, tzinfo=timezone.utc), day(12), day(14), id="08:29 UTC is 13:59 IST"),
        pytest.param(datetime(2026, 10, 12, 8, 31, tzinfo=timezone.utc), day(13), day(15), id="08:31 UTC is 14:01 IST"),
        pytest.param(datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc), day(12), day(14), id="sunday night UTC is monday IST"),
    ],
)
def test_2pm_india_time_cutoff(ordered_at, ship_date, delivery_date):
    result = estimate("Mumbai", "400001", now=ordered_at)
    assert (result.ship_date, result.delivery_date) == (ship_date, delivery_date)


def test_now_defaults_to_current_india_time():
    today = datetime.now(INDIA).date()
    result = estimate("Mumbai", "400001")
    assert today <= result.ship_date <= today + timedelta(days=2)
    assert result.delivery_date > result.ship_date


@pytest.mark.parametrize("pincode", ["999999", "000000", "012345", "40001", "4000011", "abcdef", "40000l", ""])
def test_unknown_pincode_asks_the_shopper_to_check_it(pincode):
    with pytest.raises(UnknownPincode, match="check"):
        estimate("Mumbai", pincode, now=MONDAY_10AM)
    with pytest.raises(UnknownPincode):
        lookup(pincode)


def test_lookup_returns_city_state_and_zone():
    assert lookup("560001") == PincodeInfo(pincode="560001", city="Bengaluru", state="Karnataka", zone="big_city")


def test_lookup_ignores_spaces_the_shopper_typed():
    assert lookup(" 400 001 ").city == "Mumbai"


def test_unknown_warehouse_city_is_an_error():
    with pytest.raises(ValueError, match="warehouse"):
        estimate("Bangalore", "560001", now=MONDAY_10AM)


@pytest.mark.parametrize(
    "selling, delivery_fee, cod_fee, use_cod, total",
    [
        pytest.param(1199, 0, 29, False, 1199, id="free delivery, no COD"),
        pytest.param(1199, 0, 29, True, 1228, id="free delivery, with COD"),
        pytest.param(399, 49, 29, False, 448, id="paid delivery, no COD"),
        pytest.param(399, 49, 29, True, 477, id="paid delivery, with COD"),
        pytest.param(499, 0, 0, True, 499, id="COD with no fee"),
    ],
)
def test_total_price_is_price_plus_delivery_plus_cod_fee(selling, delivery_fee, cod_fee, use_cod, total):
    assert total_price(selling, delivery_fee, cod_fee, use_cod) == total


@pytest.mark.parametrize("selling, delivery_fee, cod_fee", [(0, 0, 0), (-1, 0, 0), (499, -49, 0), (499, 0, -29)])
def test_total_price_rejects_bad_amounts(selling, delivery_fee, cod_fee):
    with pytest.raises(ValueError):
        total_price(selling, delivery_fee, cod_fee, use_cod=True)


@pytest.mark.parametrize(
    "product_id, size, link",
    [
        ("15970", "M", "http://localhost:3000/checkout?product=15970&size=M"),
        ("15970", "One size", "http://localhost:3000/checkout?product=15970&size=One%20size"),
        ("15970", None, "http://localhost:3000/checkout?product=15970"),
        ("a&b=c", "S/M", "http://localhost:3000/checkout?product=a%26b%3Dc&size=S%2FM"),
    ],
)
def test_buy_link_encodes_product_and_size(monkeypatch, product_id, size, link):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    assert buy_link(product_id, size) == link
    query = parse_qs(urlparse(link).query)
    assert query["product"] == [product_id]
    assert query.get("size") == ([size] if size else None)


def test_buy_link_uses_app_base_url(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://ecommai.example.app/")
    assert buy_link("15970", "One size") == "https://ecommai.example.app/checkout?product=15970&size=One%20size"
