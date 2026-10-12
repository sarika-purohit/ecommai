import json
import random
from pathlib import Path

import pytest

from agent.models import Price, Product
from data_prep.schemas import ARTICLE_TYPE_TO_GROUP
from data_prep.simulate import (
    PRICE_RANGES,
    ShopData,
    main,
    read_real_details,
    read_selected,
    shop_data_for,
    simulate,
    size_names,
)

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
SELECTED = FIXTURES / "selected.csv"
REAL_DETAILS = FIXTURES / "real_details.jsonl"
SHOP_FIELDS = {"id", "price", "sizes", "warehouse", "delivery_fee", "cod", "returns", "demo_data"}
WAREHOUSES = {"Mumbai": "400072", "Delhi": "110037", "Bengaluru": "560068", "Kolkata": "700091"}


def synthetic_products(n: int) -> list[dict[str, str]]:
    types = list(ARTICLE_TYPE_TO_GROUP)
    genders = ["men", "women", "unisex"]
    products = []
    for i in range(n):
        article_type = types[i % len(types)]
        group = ARTICLE_TYPE_TO_GROUP[article_type]
        products.append({"id": str(200_000 + i), "articleType": article_type, "gender": genders[i % 3], "group": group})
    return products


@pytest.fixture(scope="module")
def products() -> list[dict[str, str]]:
    return read_selected(SELECTED)


@pytest.fixture(scope="module")
def many() -> list[tuple[dict[str, str], ShopData]]:
    """About 2,000 synthetic products with their shop data, for checking rates."""
    products = synthetic_products(2_000)
    return list(zip(products, simulate(products, {}, seed=42)))


def test_every_product_gets_every_field_and_combines_into_a_product(products):
    records = simulate(products, {}, seed=42)
    assert [r.id for r in records] == sorted(p["id"] for p in products)
    by_id = {p["id"]: p for p in products}
    for record in records:
        stored = record.model_dump()
        assert set(stored) == SHOP_FIELDS
        assert stored["demo_data"] is True
        assert ShopData.model_validate(stored) == record
        Product.model_validate(by_id[record.id] | stored)


def test_demo_prices_stay_in_range_and_end_in_9(many):
    for product, record in many:
        low, high = PRICE_RANGES[product["articleType"]]
        assert record.price.demo is True
        assert low <= record.price.selling <= high
        assert record.price.selling % 10 == 9
        assert record.price.mrp % 10 == 9


def test_mrp_is_20_to_60_percent_above_the_selling_price(many):
    for _, record in many:
        selling, mrp = record.price.selling, record.price.mrp
        assert 120 * selling <= 100 * mrp <= 160 * selling


def test_every_article_type_has_a_price_range():
    assert set(PRICE_RANGES) == set(ARTICLE_TYPE_TO_GROUP)


@pytest.mark.parametrize(
    "group, gender, expected",
    [
        ("tops", "women", ["S", "M", "L", "XL", "XXL"]),
        ("bottoms", "men", ["28", "30", "32", "34", "36", "38"]),
        ("shoes", "men", ["UK 6", "UK 7", "UK 8", "UK 9", "UK 10", "UK 11"]),
        ("shoes", "women", ["UK 3", "UK 4", "UK 5", "UK 6", "UK 7", "UK 8"]),
        ("shoes", "unisex", ["UK 5", "UK 6", "UK 7", "UK 8", "UK 9", "UK 10"]),
        ("watches", "men", ["One size"]),
        ("bags", "women", ["One size"]),
        ("sunglasses", "unisex", ["One size"]),
    ],
)
def test_sizes_follow_the_product_type(group, gender, expected):
    assert size_names(group, gender) == expected


def test_each_product_gets_the_sizes_for_its_type(many):
    for product, record in many:
        assert list(record.sizes) == size_names(product["group"], product["gender"])


def test_stock_is_0_to_15_and_every_product_has_something_in_stock(many):
    for _, record in many:
        assert all(0 <= n <= 15 for n in record.sizes.values())
        assert any(record.sizes.values())


def test_rates_are_close_to_target(many):
    records = [record for _, record in many]
    n = len(records)
    assert sum(r.cod.available for r in records) / n == pytest.approx(0.80, abs=0.03)
    assert sum(r.returns.returnable for r in records) / n == pytest.approx(0.85, abs=0.03)
    multi_size_stock = [count for r in records if len(r.sizes) > 1 for count in r.sizes.values()]
    assert multi_size_stock.count(0) / len(multi_size_stock) == pytest.approx(0.20, abs=0.02)
    for city in WAREHOUSES:
        assert sum(r.warehouse.city == city for r in records) / n == pytest.approx(0.25, abs=0.03)


def test_fees_warehouses_and_return_rules(many):
    for _, record in many:
        assert record.delivery_fee == (0 if record.price.selling >= 499 else 49)
        assert record.cod.fee == (29 if record.cod.available else 0)
        assert WAREHOUSES[record.warehouse.city] == record.warehouse.pincode
        returns = record.returns
        assert returns.window_days in ((7, 14) if returns.returnable else (0,))
        assert returns.exchange == (returns.returnable and len(record.sizes) > 1)


def test_cheap_products_pay_for_delivery(many):
    fees = {record.delivery_fee for _, record in many}
    assert fees == {0, 49}


def test_output_does_not_depend_on_row_order(products):
    shuffled = products[:]
    random.Random(3).shuffle(shuffled)
    assert simulate(shuffled, {}, seed=42) == simulate(products, {}, seed=42)


def test_a_different_seed_gives_different_data(products):
    assert simulate(products, {}, seed=1) != simulate(products, {}, seed=2)


def test_real_prices_replace_demo_prices(products):
    real = read_real_details(REAL_DETAILS)
    with_real = {r.id: r for r in simulate(products, real, seed=42)}
    demo_only = {r.id: r for r in simulate(products, {}, seed=42)}

    assert with_real["1001"].price == Price(mrp=1999, selling=1299, demo=False)

    shirt = with_real["1004"].price
    assert shirt.selling == 849 and shirt.demo is False
    assert 120 * 849 <= 100 * shirt.mrp <= 160 * 849 and shirt.mrp % 10 == 9

    tshirt = with_real["1005"].price
    assert tshirt.selling == 699 and tshirt.demo is False
    assert tshirt.mrp > 699, "a real MRP below the selling price is replaced"

    assert with_real["1013"].price == demo_only["1013"].price and with_real["1013"].price.demo is True
    assert "9999" not in with_real


def test_real_prices_leave_the_other_demo_values_alone(products):
    real = read_real_details(REAL_DETAILS)
    with_real = {r.id: r for r in simulate(products, real, seed=42)}
    demo_only = {r.id: r for r in simulate(products, {}, seed=42)}
    for product_id in ("1001", "1004", "1005"):
        other_fields = {"sizes", "warehouse", "cod", "returns"}
        assert with_real[product_id].model_dump(include=other_fields) == demo_only[product_id].model_dump(
            include=other_fields
        )


def test_unknown_article_type_is_an_error():
    with pytest.raises(ValueError, match="Socks"):
        shop_data_for({"id": "1", "articleType": "Socks", "gender": "men", "group": "tops"}, None, seed=42)


def test_cli_output_is_identical_on_two_runs(tmp_path, capsys):
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for out in (first, second):
        main(["--selected", str(SELECTED), "--real-details", str(REAL_DETAILS), "--out", str(out)])
    assert first.read_bytes() == second.read_bytes()
    lines = first.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 18
    assert all(set(json.loads(line)) == SHOP_FIELDS for line in lines)
    assert "Shop data for 18 products: 3 real prices, 15 demo prices" in capsys.readouterr().out
