import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from agent import outfit, verify
from agent.app import create_app
from agent.models import Product
from tests.fixtures.app.fakes import FAST_CHECKS, FakeClient, FakeDB, FakeEmbedder, backend, near_text, product_doc

ENV = {"AUTH_MODE": "dev", "ALLOWED_ORIGINS": "http://localhost:3000"}
NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
ONE_SIZE = {"One Size": 5}
CLOTHES = {"M": 3, "L": 1}

OFFICE = {
    "occasion": "office", "title": "Smart office look", "summary": "Navy trousers keep the white shirt crisp.",
    "pieces": [
        {"slot": "bottom", "article_type": "Trousers", "description": "navy formal trousers"},
        {"slot": "shoes", "article_type": "Formal Shoes", "description": "black leather oxfords"},
        {"slot": "watch", "article_type": "Watches", "description": "silver round-dial watch"},
        {"slot": "hat", "article_type": "Caps", "description": "a cap"},
    ],
    "ideas": ["a slim black belt"],
}
CASUAL = {
    "occasion": "Casual", "title": "Weekend look", "summary": "Blue jeans and white sneakers keep it easy.",
    "pieces": [
        {"slot": "bottom", "article_type": "Kurtis", "description": "not offered for a bottom"},
        {"slot": "bottom", "article_type": "Jeans", "description": "slim blue jeans"},
        {"slot": "shoes", "article_type": "Casual Shoes", "description": "white sneakers"},
        {"slot": "bag", "article_type": "Backpacks", "description": "grey canvas backpack"},
    ],
    "ideas": [],
}
PLAN = {"looks": [OFFICE, dict(OFFICE, title="Another office look"), dict(CASUAL, occasion="wedding"), CASUAL]}

# Gemini's scores per product: colour, style, silhouette, and occasion fit.
SCORES = {
    "t1": (0.9, 0.9, 0.8, {"office": 0.9, "casual": 0.4}),
    "t2": (0.6, 0.7, 0.7, {"office": 0.7, "casual": 0.5}),
    "j1": (0.8, 0.8, 0.8, {"office": 0.2, "casual": 0.9}),
    "f1": (0.8, 0.9, 0.8, {"office": 0.9, "casual": 0.3}),
    "c1": (0.9, 0.8, 0.8, {"office": 0.3, "casual": 0.9}),
    "w1": (0.7, 0.8, 0.7, {"office": 0.8, "casual": 0.6}),
    "b1": (0.6, 0.6, 0.6, {"office": 0.2, "casual": 0.8}),
}
CANDIDATE_LINE = re.compile(r"^(\d+): .*?(\S+)$")


def item(product_id: str, article_type: str, *, gender: str = "men", selling: int = 1000, sizes=None, **extra) -> dict:
    return product_doc(
        product_id, article_type=article_type, gender=gender, selling=selling, title=f"{article_type} {product_id}",
        sizes=sizes if sizes is not None else CLOTHES, embedding=near_text(0.3), **extra,
    )


def catalogue() -> list[dict]:
    return [
        item("shirt1", "Shirts", selling=1500),
        item("t1", "Trousers", selling=1200),
        item("t2", "Trousers", selling=900),
        item("t0", "Trousers", sizes={"M": 0, "L": 0}),
        item("t9", "Trousers", selling=9000),
        item("tw", "Trousers", gender="women"),
        item("j1", "Jeans", selling=1800),
        item("f1", "Formal Shoes", selling=2500, sizes={"UK 9": 2}),
        item("c1", "Casual Shoes", selling=2000, sizes={"UK 9": 2}),
        item("w1", "Watches", selling=3000, sizes=ONE_SIZE),
        item("b1", "Backpacks", gender="unisex", selling=1500, sizes=ONE_SIZE),
    ]


class Stylist:
    """A Gemini stand-in: answers the plan prompt with `plan` and the judge prompt from SCORES."""

    def __init__(self, plan: dict | Exception = PLAN, judge: dict[str, Exception] | None = None):
        self.plan = plan
        self.judge = judge or {}
        self.client = FakeClient(self.respond)
        self.prompts: list[str] = []

    def respond(self, contents: list):
        prompt = contents[-1]
        self.prompts.append(prompt)
        if "=== SLOTS" in prompt:
            return self.plan if isinstance(self.plan, Exception) else json.dumps(self.plan)
        slot = re.search(r"=== CANDIDATES for the (\w+)", prompt)[1]
        if slot in self.judge:
            return self.judge[slot]
        block = prompt.split("=== CANDIDATES")[1].split("=== END CANDIDATES")[0]
        fits = []
        for line in block.splitlines():
            if found := CANDIDATE_LINE.match(line):
                colour, style, silhouette, occasions = SCORES.get(found[2], (0.5, 0.5, 0.5, {}))
                fits.append({
                    "number": int(found[1]), "colour": colour, "style": style, "silhouette": silhouette,
                    "occasions": [{"occasion": o, "score": s} for o, s in occasions.items()],
                    "reason": f"{found[2]} goes with the white shirt.",
                })
        return json.dumps({"fits": fits})

    @property
    def calls(self) -> int:
        return len(self.client.models.calls)


def anchor(db: FakeDB, product_id: str = "shirt1") -> Product:
    return Product.model_validate(db.docs("products")[product_id])


def run(db: FakeDB | None = None, stylist: Stylist | None = None, **options) -> tuple[outfit.Looks, Stylist]:
    db = db or FakeDB(catalogue())
    stylist = stylist or Stylist()
    looks = outfit.complete_look(
        anchor(db, options.pop("product_id", "shirt1")), client=stylist.client, db=db, embedder=FakeEmbedder(),
        settings=FAST_CHECKS, now=options.pop("now", NOW), sleep=lambda s: None, **options,
    )
    return looks, stylist


def picks(look: outfit.Look) -> dict[str, str]:
    return {piece.slot: piece.choices[0].product.id for piece in look.pieces}


# ---------- Settings ----------


def test_weights_can_be_changed_but_not_broken():
    assert outfit.weights_from(None) == outfit.WEIGHTS
    assert outfit.weights_from("colour=30, budget=0,hat=5,style=lots,silhouette=-1") == outfit.WEIGHTS | {
        "colour": 30.0, "budget": 0.0,
    }


def test_slots_follow_the_product_and_what_the_catalogue_has():
    db = FakeDB(catalogue())
    assert list(outfit.slot_types(anchor(db), None)) == ["bottom", "shoes", "bag", "watch"]
    assert outfit.slot_types(anchor(db), {"Jeans", "Casual Shoes"}) == {"bottom": ["Jeans"], "shoes": ["Casual Shoes"]}
    assert "top" in outfit.slot_types(anchor(db, "j1"), None)


# ---------- The looks ----------


def test_looks_follow_gemini_plan_within_the_slots_and_types_offered():
    looks, stylist = run()
    assert [(look.occasion, look.title) for look in looks.looks] == [("office", "Smart office look"), ("casual", "Weekend look")]
    office, casual = looks.looks
    assert picks(office) == {"bottom": "t1", "shoes": "f1", "watch": "w1"}
    assert picks(casual) == {"bottom": "j1", "shoes": "c1", "bag": "b1"}
    assert office.ideas == ["a slim black belt"] and casual.ideas == []
    assert stylist.calls == 1 + 4  # one plan, one judge per slot
    assert looks.sample_data and looks.demo_prices and looks.note is None


def test_hard_limits_come_first():
    looks, _ = run()
    shown = {c.product.id for look in looks.looks for piece in look.pieces for c in piece.choices}
    shown |= {s.product.id for row in looks.rows for s in row.items}
    assert "tw" not in shown  # women's trousers for a men's shirt
    assert "t0" not in shown  # out of stock
    assert "shirt1" not in shown


def test_match_is_the_weighted_average_of_what_we_know():
    looks, _ = run()
    t1 = looks.looks[0].pieces[0].choices[0]
    assert t1.product.id == "t1" and t1.reason == "t1 goes with the white shirt."
    parts = {part.name: part.score for part in t1.parts}
    assert parts == {"colour": 0.9, "style": 0.9, "occasion": 0.9, "silhouette": 0.8, "preferences": None, "budget": None}
    # No saved likes and no budget: those are left out, not guessed.
    assert t1.match == round(100 * (25 * 0.9 + 25 * 0.9 + 20 * 0.9 + 15 * 0.8) / 85)


def test_the_same_product_scores_differently_for_each_occasion():
    looks, _ = run()
    office, casual = looks.looks
    # Every look may swap in any of the slot's candidates, ranked for its own occasion.
    assert [c.product.id for c in office.pieces[0].choices] == ["t1", "t2", "j1", "t9"]
    assert [c.product.id for c in casual.pieces[0].choices][0] == "j1"


def test_a_look_total_needs_every_price():
    looks, _ = run()
    assert looks.looks[0].total_price == 1500 + 1200 + 2500 + 3000
    db = FakeDB(catalogue())
    del db.docs("products")["shirt1"]["price"]  # products without a price never come back from the search
    looks, _ = run(db)
    assert looks.looks[0].total_price is None


def test_rows_group_the_alternatives():
    looks, _ = run()
    rows = {row.kind: [s.product.id for s in row.items] for row in looks.rows}
    assert rows["pair"][:2] == ["t1", "j1"] and set(rows["pair"]) == {"t1", "t2", "t9", "j1"}
    assert set(rows["shoes"]) == {"f1", "c1"} and set(rows["accessories"]) == {"w1", "b1"}


# ---------- Budget ----------


def test_a_budget_limits_each_piece_and_leaves_out_the_least_important():
    looks, _ = run(budget=6000)
    office = looks.looks[0]
    assert all(c.product.price.selling <= 4500 for piece in office.pieces for c in piece.choices)
    assert "t9" not in {c.product.id for piece in office.pieces for c in piece.choices}
    assert office.total_price <= 6000 and office.left_out
    assert office.left_out[-1] == "watch"
    budget = {part.name: part.score for part in office.pieces[0].choices[0].parts}["budget"]
    assert budget is not None and 0 <= budget <= 1


def test_a_product_over_the_budget_leaves_nothing_for_the_rest():
    looks, stylist = run(budget=1200)
    assert looks.looks == [] and looks.note == outfit.OVER_BUDGET and stylist.calls == 0


# ---------- Preferences ----------


def test_saved_likes_count_as_preferences():
    db = FakeDB(catalogue())
    liked = db.docs("products")["t2"]["embedding"]
    looks, _ = run(db, taste={"Trousers": liked})
    office = looks.looks[0]
    t2 = next(c for c in office.pieces[0].choices if c.product.id == "t2")
    t1 = next(c for c in office.pieces[0].choices if c.product.id == "t1")
    assert {p.name: p.score for p in t2.parts}["preferences"] == 1.0
    assert {p.name: p.score for p in t1.parts}["preferences"] is not None
    shoes = office.pieces[1].choices[0]
    assert {p.name: p.score for p in shoes.parts}["preferences"] is None


# ---------- When Gemini struggles ----------


def test_a_slot_gemini_couldnt_score_is_shown_without_a_match():
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    looks, _ = run(stylist=Stylist(judge={"watch": busy}))
    watch = next(piece for piece in looks.looks[0].pieces if piece.slot == "watch")
    assert watch.choices[0].match is None and watch.choices[0].reason == ""
    assert looks.note == outfit.UNSCORED
    assert looks.looks[0].pieces[0].choices[0].match is not None


def test_no_plan_is_a_failure():
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    with pytest.raises(outfit.LookFailed):
        run(stylist=Stylist(plan=busy))
    with pytest.raises(outfit.LookFailed):
        run(stylist=Stylist(plan={"looks": [dict(OFFICE, pieces=[OFFICE["pieces"][-1]])]}))


def test_one_occasion_asked_gives_one_look():
    looks, stylist = run(occasion="casual", style="minimal")
    assert [look.occasion for look in looks.looks] == ["casual"]
    assert "Make 1 look, for casual." in stylist.prompts[0] and "minimal style" in stylist.prompts[0]


# ---------- Saving ----------


def test_a_saved_look_is_reused_until_it_expires():
    db, cache = FakeDB(catalogue()), verify.MemoryCache()
    first, stylist = run(db, cache=cache)
    again, _ = run(db, stylist, cache=cache)
    assert stylist.calls == 5 and again == first
    later, _ = run(db, stylist, cache=cache, now=NOW + outfit.LOOK_TTL + timedelta(minutes=1))
    assert stylist.calls == 10 and later == first


# ---------- The API ----------


def client_for(stylist: Stylist) -> TestClient:
    wired = backend(FakeDB(catalogue()), checker=SimpleNamespace(client=stylist.client))
    return TestClient(create_app(wired, env=ENV))


def test_the_api_completes_the_look():
    client = client_for(Stylist())
    response = client.post("/api/products/shirt1/looks", json={"budget_max_inr": 20000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["product_id"] == "shirt1" and body["budget_max_inr"] == 20000 and body["sample_data"] is True
    assert [look["occasion"] for look in body["looks"]] == ["office", "casual"]
    assert set(body["looks"][0]["pieces"][0]["choices"][0]["product"]) == set(
        ["id", "title", "brand", "gender", "article_type", "base_colour", "usage", "price", "thumb", "photo",
         "rating_avg", "rating_count"]
    )


def test_the_api_refuses_what_it_cant_style():
    client = client_for(Stylist())
    assert client.post("/api/products/nope/looks", json={}).status_code == 404
    assert client.post("/api/products/gid://shopify/p/1/looks", json={}).status_code == 400
    assert client.post("/api/products/shirt1/looks", json={"occasion": "wedding"}).status_code == 400
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    assert client_for(Stylist(plan=busy)).post("/api/products/shirt1/looks", json={}).status_code == 503
