"""Seed backdated demo orders for a sign-in-off browser, so "Goes with your recent demo orders" has something to show.

Run from the repo root (it writes to the Firestore your environment points at, so check .env first):
    python -m scripts.seed_demo_orders --device <the X-Device-Id of the browser> --order 30763:3 --order 28489:20

Each --order is <catalogue product id>:<days ago>. The orders are real catalogue products, marked `seeded: true`
so the app says "seeded for this demo". The shopper is `device-<id>`, the name auth.py gives a browser with sign-in off.
"""

import argparse
import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from agent import purchases
from agent.sessions import Store, now_utc
from data_prep.simulate import ONE_SIZE

ORDER_SPEC = re.compile(r"^(?P<product>[^:\s/]+):(?P<days>\d{1,3})$")


def parse_order(spec: str) -> tuple[str, int]:
    found = ORDER_SPEC.match(spec)
    if not found or int(found["days"]) > purchases.MAX_AGE_DAYS:
        raise ValueError(f"'{spec}' should look like <product id>:<days ago, 0 to {purchases.MAX_AGE_DAYS}>")
    return found["product"], int(found["days"])


def seed(store: Store, device: str, specs: Sequence[str], now: datetime | None = None) -> list[purchases.Order]:
    """Add these orders to `device-<device>`'s demo orders; returns the ones added."""
    now = now or now_utc()
    uid = f"device-{device}"
    added = []
    for spec in specs:
        product_id, days = parse_order(spec)
        product = store.product(product_id)
        if product is None:
            raise ValueError(f"no catalogue product {product_id}")
        in_stock = [size for size, count in product.sizes.items() if count > 0]
        size = None if not product.sizes or list(product.sizes) == [ONE_SIZE] else (in_stock or [None])[0]
        added.append(purchases.Order(
            id=uuid.uuid4().hex, product_id=product.id, title=product.title, article_type=product.article_type,
            gender=product.gender, base_colour=product.base_colour, usage=product.usage, size=size,
            price_inr=product.price.selling if product.price else None, ordered_at=now - timedelta(days=days),
            seeded=True,
        ))
    store.update_user(uid, {"orders": purchases.kept([*store.user(uid).orders, *added], now)})
    return added


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", required=True, help="the browser's X-Device-Id")
    parser.add_argument("--order", action="append", required=True, metavar="PRODUCT_ID:DAYS_AGO")
    args = parser.parse_args(argv)
    from agent.sessions import default_store

    added = seed(default_store(), args.device, args.order)
    for order in added:
        print(f"seeded {order.product_id} ({order.article_type}) {(datetime.now(timezone.utc) - order.ordered_at).days} days ago")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
