"""Step 5, Buy: demo delivery date, total price and the buy link.

Delivery dates are demo values worked out from the warehouse city and the shopper's pincode,
using India Post's pincode list (data_prep/pincodes.csv). A real product would ask courier partners.
"""

import csv
import os
import re
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from pydantic import BaseModel

PINCODES_CSV = Path(__file__).resolve().parent.parent / "data_prep" / "pincodes.csv"
INDIA = ZoneInfo("Asia/Kolkata")
SHIPPING_CUTOFF = time(14, 0)
SUNDAY = 6
WAREHOUSE_CITIES = ("Mumbai", "Delhi", "Bengaluru", "Kolkata")
DEFAULT_APP_BASE_URL = "http://localhost:3000"
PINCODE = re.compile(r"[1-9][0-9]{5}")

PincodeZone = Literal["big_city", "remote", "other"]
DeliveryZone = Literal["same_city", "big_city", "other", "remote"]

DELIVERY_DAYS: dict[DeliveryZone, tuple[int, int]] = {
    "same_city": (1, 2),
    "big_city": (2, 3),
    "other": (3, 5),
    "remote": (6, 8),
}


class UnknownPincode(Exception):
    def __init__(self, pincode: str):
        super().__init__(f"We couldn't find pincode '{pincode}'. Please check it.")
        self.pincode = pincode


class PincodeInfo(BaseModel):
    pincode: str
    city: str
    state: str
    zone: PincodeZone


class DeliveryEstimate(BaseModel):
    zone: DeliveryZone
    days_min: int
    days_max: int
    ship_date: date
    delivery_date: date


@cache
def _pincodes() -> dict[str, dict[str, str]]:
    with PINCODES_CSV.open(newline="", encoding="utf-8") as f:
        return {row["pincode"]: row for row in csv.DictReader(f)}


def lookup(pincode: str) -> PincodeInfo:
    code = "".join(str(pincode).split())
    row = _pincodes().get(code) if PINCODE.fullmatch(code) else None
    if row is None:
        raise UnknownPincode(pincode)
    return PincodeInfo.model_validate(row)


def delivery_zone(warehouse_city: str, place: PincodeInfo) -> DeliveryZone:
    if warehouse_city not in WAREHOUSE_CITIES:
        raise ValueError(f"unknown warehouse city '{warehouse_city}', expected one of {WAREHOUSE_CITIES}")
    if place.zone == "big_city" and place.city == warehouse_city:
        return "same_city"
    return place.zone


def india_time(now: datetime | None = None) -> datetime:
    """The current India time, or `now` in India time. A naive `now` is taken to be India time already."""
    if now is None:
        return datetime.now(INDIA)
    if now.tzinfo is None:
        return now.replace(tzinfo=INDIA)
    return now.astimezone(INDIA)


def ship_date(ordered_at: datetime) -> date:
    """Orders from 2 pm India time ship the next day, and nothing ships on a Sunday."""
    day = ordered_at.date()
    if ordered_at.time() >= SHIPPING_CUTOFF:
        day += timedelta(days=1)
    if day.weekday() == SUNDAY:
        day += timedelta(days=1)
    return day


def add_delivery_days(start: date, days: int) -> date:
    """Counts only Monday to Saturday, so the result is never a Sunday."""
    day = start
    while days > 0:
        day += timedelta(days=1)
        if day.weekday() != SUNDAY:
            days -= 1
    return day


def estimate(warehouse_city: str, pincode: str, now: datetime | None = None) -> DeliveryEstimate:
    """Promises the last day of the range, so we never promise earlier than we can deliver."""
    zone = delivery_zone(warehouse_city, lookup(pincode))
    days_min, days_max = DELIVERY_DAYS[zone]
    shipped = ship_date(india_time(now))
    return DeliveryEstimate(
        zone=zone,
        days_min=days_min,
        days_max=days_max,
        ship_date=shipped,
        delivery_date=add_delivery_days(shipped, days_max),
    )


def total_price(selling: int, delivery_fee: int, cod_fee: int, use_cod: bool) -> int:
    if selling <= 0 or delivery_fee < 0 or cod_fee < 0:
        raise ValueError("price must be positive and fees can't be negative")
    return selling + delivery_fee + (cod_fee if use_cod else 0)


def buy_link(product_id: str, size: str | None) -> str:
    base_url = (os.environ.get("APP_BASE_URL") or DEFAULT_APP_BASE_URL).rstrip("/")
    params = {"product": product_id}
    if size:
        params["size"] = size
    return f"{base_url}/checkout?{urlencode(params, quote_via=quote)}"
