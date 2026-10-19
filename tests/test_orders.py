"""Demo orders: storing them, the three endpoints, and the suggestions that go with them."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent import purchases, sessions
from agent.app import create_app
from agent.auth import DEMO_USER_ID
from tests.fixtures.app.fakes import FakeDB, backend
from tests.test_outfit import ENV, Stylist, catalogue

CLOCK = datetime(2026, 10, 18, 9, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def server_time(monkeypatch):
    monkeypatch.setattr(sessions, "now_utc", lambda: CLOCK)


def wired(stylist: Stylist | None = None):
    stylist = stylist or Stylist()
    made = backend(FakeDB(catalogue()), checker=SimpleNamespace(client=stylist.client))
    return TestClient(create_app(made, env=ENV)), made, stylist


def old_order(n: int, days: int) -> dict:
    return purchases.Order(
        id=f"o{n}", product_id="t1", title="Trousers t1", article_type="Trousers", gender="men",
        ordered_at=CLOCK - timedelta(days=days),
    ).model_dump(mode="python")


def stored(made) -> list[dict]:
    return made.store.db.docs("users").get(DEMO_USER_ID, {}).get("orders", [])


# ---------- Placing, listing, clearing ----------


def test_an_order_is_saved_with_server_time_and_the_products_own_facts():
    client, made, _ = wired()
    response = client.post("/api/orders", json={"product_id": "shirt1", "size": "M", "ordered_at": "2001-01-01"})
    assert response.status_code == 200, response.text
    order = response.json()["order"]
    assert order["product_id"] == "shirt1" and order["size"] == "M" and order["price_inr"] == 1500
    assert order["source"] == "demo_catalog" and order["seeded"] is False
    assert order["article_type"] == "Shirts" and order["usage"] == "Casual" and order["base_colour"] == "White"
    assert datetime.fromisoformat(order["ordered_at"]) == CLOCK
    assert [o["id"] for o in stored(made)] == [order["id"]]


def test_live_unknown_and_badly_sized_orders_are_refused():
    client, made, _ = wired()
    assert client.post("/api/orders", json={"product_id": "gid://shopify/p/1", "size": "M"}).status_code == 400
    assert client.post("/api/orders", json={"product_id": "nope", "size": "M"}).status_code == 404
    assert client.post("/api/orders", json={"product_id": "shirt1"}).status_code == 400
    assert client.post("/api/orders", json={"product_id": "shirt1", "size": "XXL"}).status_code == 400
    assert client.post("/api/orders", json={"product_id": "t0", "size": "M"}).status_code == 400
    assert client.post("/api/orders", json={"product_id": "w1", "size": "One Size"}).status_code == 200
    assert len(stored(made)) == 1


def test_orders_list_newest_first_and_clear():
    client, made, _ = wired()
    made.store.update_user(DEMO_USER_ID, {"orders": [purchases.Order(**old_order(1, 20)), purchases.Order(**old_order(2, 5))]})
    client.post("/api/orders", json={"product_id": "w1", "size": "One Size"})
    listed = client.get("/api/orders").json()["orders"]
    assert [o["id"] for o in listed][1:] == ["o2", "o1"] and listed[0]["product_id"] == "w1"
    assert client.delete("/api/orders").json() == {"ok": True}
    assert client.get("/api/orders").json() == {"orders": []}


def test_old_orders_drop_and_only_fifty_are_kept():
    client, made, _ = wired()
    made.store.update_user(
        DEMO_USER_ID, {"orders": [purchases.Order(**old_order(n, 400 if n < 5 else 10 + n % 30)) for n in range(60)]}
    )
    client.post("/api/orders", json={"product_id": "w1", "size": "One Size"})
    kept = stored(made)
    assert len(kept) == 50
    assert all(purchases.age_days(o["ordered_at"], CLOCK) <= 365 for o in kept)
    assert kept[-1]["product_id"] == "w1"


def test_deleting_my_data_removes_the_orders():
    client, made, _ = wired()
    client.post("/api/orders", json={"product_id": "w1", "size": "One Size"})
    assert stored(made)
    client.delete("/api/me")
    assert DEMO_USER_ID not in made.store.db.docs("users")
    assert client.get("/api/orders").json() == {"orders": []}


# ---------- Suggestions ----------


def test_no_orders_means_no_suggestions_and_no_gemini_call():
    client, _, stylist = wired()
    body = client.get("/api/suggestions/from-orders").json()
    assert body["items"] == [] and body["ideas"] == []
    assert body["sample_data"] is True and body["demo_orders"] is True
    assert stylist.calls == 0


def test_suggestions_go_with_the_order_and_skip_disliked_products():
    client, made, stylist = wired()
    client.post("/api/orders", json={"product_id": "shirt1", "size": "M"})
    body = client.get("/api/suggestions/from-orders").json()
    assert body["items"] and len(body["items"]) <= purchases.RAIL_SIZE
    first = body["items"][0]
    assert set(first) == {"product", "slot", "match", "reason", "goes_with"}
    assert set(first["product"]) >= {"id", "title", "thumb", "price", "article_type"}
    assert first["goes_with"][0]["product_id"] == "shirt1" and first["goes_with"][0]["seeded"] is False
    assert all(i["product"]["id"] != "shirt1" for i in body["items"]) and all(i["match"] >= 50 for i in body["items"])
    made.store.update_user(DEMO_USER_ID, {"dislikes": [first["product"]["id"]]})
    again = client.get("/api/suggestions/from-orders").json()
    assert first["product"]["id"] not in {i["product"]["id"] for i in again["items"]}


# ---------- The seeding script ----------


def test_seeding_writes_backdated_seeded_orders_for_the_device():
    from scripts import seed_demo_orders

    made = backend(FakeDB(catalogue()))
    added = seed_demo_orders.seed(made.store, "abcd1234abcd1234", ["shirt1:3", "t1:20"], CLOCK)
    saved = made.store.user("device-abcd1234abcd1234").orders
    assert [o.product_id for o in saved] == ["t1", "shirt1"] and all(o.seeded for o in saved) and len(added) == 2
    assert saved[1].ordered_at == CLOCK - timedelta(days=3) and saved[1].size == "M"
    with pytest.raises(ValueError):
        seed_demo_orders.seed(made.store, "abcd1234abcd1234", ["nope:3"], CLOCK)
    with pytest.raises(ValueError):
        seed_demo_orders.parse_order("shirt1:400")
