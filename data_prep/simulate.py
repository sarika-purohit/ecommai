"""Add demo shop data (price, sizes and stock, warehouse, delivery fee, cash on delivery, returns) to every product.

Run: python -m data_prep.simulate
"""

import argparse
import csv
import json
import math
import random
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field, NonNegativeInt

from agent.models import Cod, Price, Returns, Warehouse
from data_prep.schemas import GROUPS

# Every bound ends in 99, so steps of 50 from the lower bound give prices ending in 49 or 99.
PRICE_RANGES: dict[str, tuple[int, int]] = {
    "Tshirts": (299, 1299),
    "Tops": (399, 1499),
    "Shirts": (599, 2499),
    "Kurtas": (499, 2999),
    "Kurtis": (499, 2999),
    "Tunics": (499, 2999),
    "Jeans": (799, 2999),
    "Trousers": (799, 2999),
    **dict.fromkeys(GROUPS["shoes"], (799, 4999)),
    **dict.fromkeys(GROUPS["bags"], (699, 3999)),
    "Sunglasses": (499, 2999),
    "Watches": (999, 9999),
}
PRICE_STEP = 50
MRP_MARKUP_PERCENT = (20, 60)

TOP_SIZES = ["S", "M", "L", "XL", "XXL"]
WAIST_SIZES = ["28", "30", "32", "34", "36", "38"]
SHOE_SIZES_UK = {"men": range(6, 12), "women": range(3, 9), "unisex": range(5, 11)}
ONE_SIZE = "One size"
MAX_STOCK = 15
OUT_OF_STOCK_RATE = 0.2

# The delivery module matches on these exact city names.
WAREHOUSES = (("Mumbai", "400072"), ("Delhi", "110037"), ("Bengaluru", "560068"), ("Kolkata", "700091"))
FREE_DELIVERY_FROM = 499
DELIVERY_FEE = 49
COD_RATE = 0.8
COD_FEE = 29
RETURNABLE_RATE = 0.85
RETURN_WINDOWS_DAYS = (7, 14)


class ShopData(BaseModel):
    """One line of data/shop.jsonl. The field names match the Product model and the Firestore layout."""

    id: str
    price: Price
    sizes: dict[str, NonNegativeInt] = Field(min_length=1)
    warehouse: Warehouse
    delivery_fee: NonNegativeInt
    cod: Cod
    returns: Returns
    demo_data: bool = True


def size_names(group: str, gender: str) -> list[str]:
    if group == "tops":
        return TOP_SIZES
    if group == "bottoms":
        return WAIST_SIZES
    if group == "shoes":
        return [f"UK {n}" for n in SHOE_SIZES_UK.get(gender.lower(), SHOE_SIZES_UK["unisex"])]
    return [ONE_SIZE]


def _stock(rng: random.Random, names: list[str]) -> dict[str, int]:
    stock = {name: 0 if rng.random() < OUT_OF_STOCK_RATE else rng.randint(1, MAX_STOCK) for name in names}
    if not any(stock.values()):
        stock[rng.choice(names)] = rng.randint(1, MAX_STOCK)
    return stock


def _cod(rng: random.Random) -> Cod:
    return Cod(available=True, fee=COD_FEE) if rng.random() < COD_RATE else Cod(available=False)


def _returns(rng: random.Random, has_sizes: bool) -> Returns:
    if rng.random() >= RETURNABLE_RATE:
        return Returns(returnable=False)
    return Returns(returnable=True, window_days=rng.choice(RETURN_WINDOWS_DAYS), exchange=has_sizes)


def _mrp_for(rng: random.Random, selling: int) -> int:
    low = math.ceil(selling * (100 + MRP_MARKUP_PERCENT[0]) / 100)
    high = math.floor(selling * (100 + MRP_MARKUP_PERCENT[1]) / 100)
    first_ending_in_9 = low + (9 - low) % 10
    if first_ending_in_9 > high:
        return first_ending_in_9
    return rng.randrange(first_ending_in_9, high + 1, 10)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return round(value)


def _price(rng: random.Random, article_type: str, real: dict) -> Price:
    if article_type not in PRICE_RANGES:
        raise ValueError(f"no demo price range for article type '{article_type}'")
    low, high = PRICE_RANGES[article_type]
    demo_selling = rng.randrange(low, high + 1, PRICE_STEP)
    real_selling = _positive_int(real.get("selling_price"))
    selling = real_selling or demo_selling
    mrp = _positive_int(real.get("mrp"))
    if mrp is None or mrp < selling:
        mrp = _mrp_for(rng, selling)
    return Price(mrp=mrp, selling=selling, demo=real_selling is None)


def shop_data_for(product: dict[str, str], real: dict | None, seed: int) -> ShopData:
    """Demo shop data for one product. The same seed and id always give the same result."""
    rng = random.Random(f"{seed}:{product['id']}")
    sizes = _stock(rng, size_names(product["group"], product["gender"]))
    city, pincode = rng.choice(WAREHOUSES)
    cod = _cod(rng)
    returns = _returns(rng, has_sizes=len(sizes) > 1)
    # Drawn last, so a real price doesn't change any of the other demo values.
    price = _price(rng, product["articleType"], real or {})
    return ShopData(
        id=product["id"],
        price=price,
        sizes=sizes,
        warehouse=Warehouse(city=city, pincode=pincode),
        delivery_fee=0 if price.selling >= FREE_DELIVERY_FROM else DELIVERY_FEE,
        cod=cod,
        returns=returns,
    )


def simulate(products: list[dict[str, str]], real_details: dict[str, dict], seed: int) -> list[ShopData]:
    """Shop data for every product, sorted by id so the output doesn't depend on row order."""
    by_id = {product["id"]: product for product in products}
    return [shop_data_for(by_id[product_id], real_details.get(product_id), seed) for product_id in sorted(by_id)]


def read_selected(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_real_details(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    details = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                details[str(record["id"])] = record
    return details


def write_shop(records: list[ShopData], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")


def _percent(count: int, out_of: int) -> str:
    return f"{count / out_of:.1%}" if out_of else "n/a"


def summary(records: list[ShopData]) -> str:
    n = len(records)
    stock = [count for r in records for count in r.sizes.values()]
    real_prices = sum(not r.price.demo for r in records)
    warehouses = Counter(r.warehouse.city for r in records)
    return "\n".join(
        [
            f"Shop data for {n} products: {real_prices} real prices, {n - real_prices} demo prices",
            f"Cash on delivery {_percent(sum(r.cod.available for r in records), n)}, "
            f"returnable {_percent(sum(r.returns.returnable for r in records), n)}, "
            f"exchange {_percent(sum(r.returns.exchange for r in records), n)}, "
            f"free delivery {_percent(sum(r.delivery_fee == 0 for r in records), n)}",
            f"Sizes out of stock: {_percent(stock.count(0), len(stock))} of {len(stock)}",
            "Warehouses: " + ", ".join(f"{city} {warehouses[city]}" for city, _ in WAREHOUSES),
        ]
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Add demo shop data to every selected product.")
    parser.add_argument("--selected", type=Path, default=Path("data/selected.csv"), help="from select_catalog.py")
    parser.add_argument(
        "--real-details",
        type=Path,
        default=Path("data/real_details.jsonl"),
        help="from read_details.py; its real prices replace demo ones (skipped if the file doesn't exist)",
    )
    parser.add_argument("--out", type=Path, default=Path("data/shop.jsonl"))
    parser.add_argument("--seed", type=int, default=42, help="same seed, same shop data")
    args = parser.parse_args(argv)

    real_details = read_real_details(args.real_details if args.real_details.exists() else None)
    records = simulate(read_selected(args.selected), real_details, args.seed)
    write_shop(records, args.out)
    print(summary(records))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
