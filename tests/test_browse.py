import pytest
from fastapi.testclient import TestClient

from agent import browse
from agent.app import create_app
from tests.fixtures.app.fakes import FakeDB, backend, brief_answer, product_doc

ENV = {"AUTH_MODE": "dev", "ALLOWED_ORIGINS": "http://localhost:3000"}
CHAT = {"pincode": "560001", "language": "en", "source": "catalog"}


def catalogue() -> list[dict]:
    return [
        product_doc("1", article_type="Kurtas", gender="women", selling=899, colour="Red", title="Biba Women Red Kurta"),
        product_doc("2", article_type="Kurtas", gender="men", selling=1499, colour="White",
                    title="Fabindia Men White Cotton Kurta"),
        product_doc("3", article_type="Kurtas", gender="women", selling=650, colour="Blue",
                    title="W Women Blue Printed Kurta", rating_avg=4.8),
        product_doc("4", article_type="Watches", gender="men", selling=2999, colour="Black",
                    title="Titan Men Black Dial Watch"),
        product_doc("5", article_type="Tshirts", gender="unisex", selling=499, colour="White",
                    title="Roadster Unisex White T-Shirt"),
        product_doc("6", article_type="Casual Shoes", gender="men", selling=1999, colour="White",
                    title="Puma Men White Sneakers"),
    ]


def index(docs: list[dict] | None = None) -> browse.CatalogIndex:
    return browse.CatalogIndex(browse.firestore_loader(FakeDB(docs if docs is not None else catalogue())))


def ids(page: browse.CatalogPage) -> list[str]:
    return [item.id for item in page.items]


def test_plurals_and_singulars_meet():
    assert [browse.stem(w) for w in ["kurtas", "watches", "shoes", "sunglasses", "dress", "jeans"]] == [
        "kurta", "watch", "shoe", "sunglass", "dress", "jean",
    ]


def test_gender_and_price_words_become_filters():
    wanted, gender, max_price, _ = browse.read_query("Cotton kurta for MEN under ₹1,500")
    assert (wanted, gender, max_price) == (["cotton", "kurta"], "men", 1500)
    assert browse.read_query("watch below 3k") == (["watch"], None, 3000, None)
    assert browse.read_query("t-shirt for ladies")[:2] == (["tshirt"], "women")


def test_a_keyword_finds_that_product_type_only():
    page = index().search("kurtas")
    assert sorted(ids(page)) == ["1", "2", "3"] and page.total == 3 and not page.partial
    assert [(c.name, c.count) for c in page.categories] == [("Kurtas", 3)]


def test_gender_word_keeps_unisex_and_drops_the_other_gender():
    assert ids(index().search("white for men")) == ["2", "5", "6"]
    assert ids(index().search("kurta", gender="women", sort="price_low")) == ["3", "1"]


@pytest.mark.parametrize(
    ("query", "rating"),
    [
        ("kurta above 4 star", 4.0), ("kurta 4 star and above", 4.0), ("watch 4+ rating", 4.0), ("shoes rated 4+", 4.0),
        ("rating above 4.5 bags", 4.5), ("4★ tops", 4.0), ("kurta 4 star se upar", 4.0), ("size 4 shoes", None),
        ("kurta 4 pockets", None),
    ],
)
def test_star_ratings_become_a_filter(query, rating):
    wanted, _, _, min_rating = browse.read_query(query)
    assert min_rating == rating
    assert not {"star", "rating", "rated", "above", "se", "upar"} & set(wanted)


def test_the_rating_filter_keeps_products_rated_that_high():
    page = index().search("kurta 4.5 star and above")
    assert ids(page) == ["3"] and page.understood == {"min_rating": 4.5}
    assert ids(index().search("kurta", min_rating=4.5)) == ["3"]


def test_every_word_must_match_else_the_closest_are_shown():
    exact = index().search("white cotton kurta")
    assert ids(exact) == ["2"] and not exact.partial
    closest = index().search("red cotton kurta")
    assert ids(closest) == ["1", "2"] and closest.partial
    missing = index().search("red silk saree")
    assert missing.total == 0 and missing.understood == {"unmatched": ["silk", "saree"]}


def test_price_limit_and_sorting():
    assert ids(index().search("kurta under 900", sort="price_high")) == ["1", "3"]
    assert ids(index().search("", sort="rating"))[0] == "3"
    assert index().search("", sort="price_low").items[0].price.selling == 499


def test_a_colour_matches_the_product_not_its_brand():
    docs = [
        *catalogue(),
        product_doc("8", article_type="Formal Shoes", gender="men", colour="Black", brand="Red Tape",
                    title="Red Tape Men Black Formal Shoes"),
        product_doc("9", article_type="Tops", gender="women", colour="Red", brand="Jealous 21",
                    title="Jealous 21 Women Red Top"),
    ]
    assert ids(index(docs).search("red")) == ["1", "9"]
    assert ids(index(docs).search("red tape")) == ["8"]
    assert ids(index(docs).search("black")) == ["4", "8"]


def test_aliases_find_what_shoppers_call_things():
    assert ids(index().search("sneakers")) == ["6"]
    assert ids(index().search("tee")) == ["5"]


def test_category_filter_paging_and_no_match():
    page = index().search("", category="Kurtas", page=2, page_size=2)
    assert page.total == 3 and len(page.items) == 1 and page.page == 2
    assert index().search("kurta", category="Watches").total == 0
    assert index().search("saree").total == 0


def test_home_page_mixes_product_types_and_lists_categories():
    page = index().search("")
    assert page.total == 6 and len({item.article_type for item in page.items[:4]}) > 1
    assert {c.name: c.count for c in index().categories("men")} == {"Kurtas": 1, "Watches": 1, "Tshirts": 1,
                                                                    "Casual Shoes": 1}


def test_bad_documents_are_skipped_and_the_index_reloads_after_refresh():
    docs = catalogue()
    db = FakeDB([*docs, {"id": "broken", "title": "No type"}])
    now = [0.0]
    catalog = browse.CatalogIndex(browse.firestore_loader(db), refresh_s=10, clock=lambda: now[0])
    assert catalog.search("").total == 6
    db.data["products"]["7"] = product_doc("7", article_type="Watches", title="Fastrack Watch")
    assert catalog.search("").total == 6
    now[0] = 11
    assert catalog.search("").total == 7


# ---------- API ----------


def client_for(docs: list[dict] | None = None, **options) -> TestClient:
    return TestClient(create_app(backend(FakeDB(docs if docs is not None else catalogue()), **options), env=ENV))


def test_catalog_endpoint_searches_and_pages():
    client = client_for()
    body = client.get("/api/catalog", params={"q": "kurta for women", "sort": "price_low"}).json()
    assert [item["id"] for item in body["items"]] == ["3", "1"]
    assert body["understood"] == {"gender": "women"} and body["total"] == 2
    item = body["items"][0]
    assert item["thumb"].endswith("/thumb/3.jpg") and item["price"]["selling"] == 650
    assert "embedding" not in item and "reviews" not in item
    categories = client.get("/api/catalog/categories", params={"gender": "women"}).json()
    assert categories == [{"name": "Kurtas", "count": 2}, {"name": "Tshirts", "count": 1}]


def test_catalog_endpoint_rejects_bad_parameters():
    client = client_for()
    assert client.get("/api/catalog", params={"sort": "random"}).status_code == 400
    assert client.get("/api/catalog", params={"page": 0}).status_code == 400
    assert client.get("/api/catalog", params={"gender": "aliens"}).status_code == 400


def test_catalog_endpoint_needs_sign_in():
    client = TestClient(create_app(backend(FakeDB(catalogue())), env={"ALLOWED_ORIGINS": "http://localhost:3000"}))
    assert client.get("/api/catalog").status_code == 401


def test_chat_mode_never_asks_but_keeps_what_is_missing():
    asking = brief_answer(size="", question_about="size", question="Which size?")
    client = client_for(brief=lambda contents: asking)
    asked = client.post("/api/brief", json=CHAT | {"text": "white sneakers"}).json()
    assert asked["question"]
    chat = client.post("/api/brief", json=CHAT | {"text": "white sneakers", "ask": False}).json()
    assert chat["question"] is None and chat["brief"]["question"] is None
    assert "size" in chat["brief"]["missing"]


def test_a_chat_follow_up_builds_on_the_last_checklist():
    answers = iter([
        brief_answer(gender="unknown", size=""),
        brief_answer(gender="men", size="", must=[("type=sneakers", "is"), ("colour=white", "is"),
                                                 ("logo_visible=none", "is")],
                     question_about="size", question="Which size?"),
    ])
    client = client_for(brief=lambda contents: next(answers))
    session_id = client.post("/api/brief", json=CHAT | {"text": "white sneakers no logo", "ask": False}).json()[
        "session_id"]
    change = {"session_id": session_id, "change": {"type": "text", "text": "for men"}, "ask": False}
    brief = client.post("/api/brief/update", json=change).json()["brief"]
    assert brief["gender"] == "men" and brief["question"] is None
    assert [m["attribute"] for m in brief["must_haves"]] == ["type", "colour", "logo_visible"]


def test_catalog_endpoint_filters_by_rating():
    body = client_for().get("/api/catalog", params={"q": "kurta", "min_rating": 4.5}).json()
    assert [item["id"] for item in body["items"]] == ["3"]
    assert client_for().get("/api/catalog", params={"min_rating": 6}).status_code == 400


def test_the_rating_chip_is_removed_with_a_change():
    client = client_for(brief=lambda contents: brief_answer(min_rating=4))
    made = client.post("/api/brief", json=CHAT | {"text": "white sneakers above 4 star", "ask": False}).json()
    assert made["brief"]["min_rating"] == 4.0
    change = {"session_id": made["session_id"], "change": {"type": "set_min_rating", "min_rating": None}}
    assert client.post("/api/brief/update", json=change).json()["brief"]["min_rating"] is None
