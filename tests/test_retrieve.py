from datetime import date

import pytest
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

from agent.delivery import india_time
from agent.models import MustHave, NiceToHave
from agent.retrieve import (
    FETCH_LIMIT,
    attach_embeddings,
    find_candidates,
    hard_filters,
    make_hit,
    search_text,
    stock_size,
)
from eval.proxy_recall import TARGET_RECALL, Summary, cases, evaluate, report
from tests.fixtures.retrieve.factory import (
    IMAGE_VECTOR,
    MUMBAI_PINCODE,
    NOT_A_PINCODE,
    TEXT_VECTOR,
    TUESDAY_10AM,
    FakeEmbedder,
    FakeFirestore,
    brief,
    product,
    product_doc,
    unit,
)

TUESDAY, THURSDAY = date(2026, 10, 13), date(2026, 10, 15)
COD = MustHave(id="r1", attribute="cod", rule="present", value=True, from_rule=True)


def as_tuples(filters) -> list[tuple]:
    return [(f.field_path, f.op_string, f.value) for f in filters]


def search(db: FakeFirestore, request=None, **kwargs) -> list:
    return find_candidates(
        request or brief(), query_text="cotton kurta", db=db, embedder=FakeEmbedder(), now=TUESDAY_10AM, **kwargs
    )


def ids(hits: list) -> list[str]:
    return [hit.product.id for hit in hits]


# ---------- Hard filters ----------


def test_category_is_always_filtered_and_gender_includes_unisex():
    assert as_tuples(hard_filters(brief())) == [
        ("articleType", "==", "Kurtas"),
        ("gender", "in", ["women", "unisex"]),
    ]


def test_without_a_gender_only_the_category_is_filtered():
    assert as_tuples(hard_filters(brief(gender=None))) == [("articleType", "==", "Kurtas")]


def test_a_unisex_request_filters_unisex_once():
    assert as_tuples(hard_filters(brief(gender="unisex")))[1] == ("gender", "in", ["unisex"])


def test_size_in_stock_uses_array_contains():
    assert as_tuples(hard_filters(brief(size="M")))[-1] == ("in_stock_sizes", "array_contains", "M")


@pytest.mark.parametrize(
    "category, size, stored",
    [
        ("Kurtas", "m", "M"),
        ("Kurtas", " xl ", "XL"),
        ("Jeans", "32", "32"),
        ("Casual Shoes", "8", "UK 8"),
        ("Casual Shoes", "uk8", "UK 8"),
        ("Casual Shoes", "UK 8", "UK 8"),
        ("Watches", "M", None),
        ("Handbags", "One size", None),
        ("Sunglasses", "L", None),
    ],
)
def test_sizes_are_written_like_in_stock_sizes_and_one_size_products_skip_the_filter(category, size, stored):
    assert stock_size(category, size) == stored


def test_a_size_for_a_one_size_category_adds_no_filter():
    assert [f.field_path for f in hard_filters(brief(category="Watches", size="M"))] == ["articleType", "gender"]


def test_an_unknown_category_is_refused():
    with pytest.raises(ValueError, match="unknown category 'Sarees'"):
        hard_filters(brief(category="Sarees"))


def test_the_search_never_returns_products_outside_the_hard_filters():
    db = FakeFirestore(
        product_doc("ok", sizes={"M": 1}),
        product_doc("unisex", gender="unisex", sizes={"M": 4}),
        product_doc("no stock in M", sizes={"M": 0, "L": 5}),
        product_doc("men", gender="men", sizes={"M": 2}),
        product_doc("shirt", article_type="Shirts", sizes={"M": 2}),
    )
    assert sorted(ids(search(db, brief(size="M")))) == ["ok", "unisex"]


# ---------- The search itself ----------


def test_the_search_fetches_more_than_the_limit_and_leaves_out_embeddings():
    db = FakeFirestore(*(product_doc(f"p{n}") for n in range(5)))
    hits = search(db, limit=2)
    [query] = db.searches
    assert query.nearest["limit"] == FETCH_LIMIT
    assert query.nearest["distance_measure"] == DistanceMeasure.COSINE
    assert query.nearest["vector_field"] == "embedding"
    assert "embedding" not in query.fields and {"distance", "attributes", "reviews", "price"} <= set(query.fields)
    assert len(hits) == 2 and all(hit.product.embedding is None for hit in hits)


def test_hits_are_closest_first_with_ties_going_to_the_cheaper_product():
    db = FakeFirestore(
        product_doc("far", embedding=unit(1, 1, 0), selling=500),
        product_doc("near dear", embedding=TEXT_VECTOR, selling=1500),
        product_doc("near cheap", embedding=TEXT_VECTOR, selling=900),
    )
    hits = search(db)
    assert ids(hits) == ["near cheap", "near dear", "far"]
    assert hits[0].distance == pytest.approx(0.0) and hits[2].distance == pytest.approx(1 - 2**-0.5)


def test_the_query_is_the_shoppers_words_plus_the_checklist():
    request = brief(
        must_haves=[
            MustHave(id="m1", attribute="fabric", rule="is", value="cotton"),
            MustHave(id="m2", attribute="pockets", rule="present", value=True),
            MustHave(id="m3", attribute="sheerness", rule="is_not", value="see-through"),
            MustHave(id="m4", attribute="size_fit", rule="is", value="true to size"),
            COD,
        ],
        nice_to_haves=[NiceToHave(attribute="usage", value="office")],
    )
    embedder = FakeEmbedder()
    find_candidates(request, query_text="  kurta with  pockets ", db=FakeFirestore(), embedder=embedder)
    assert embedder.texts == [
        (
            "task: search result | query: kurta with pockets. Kurtas for women. "
            "Details: fabric: cotton; with pockets. Preferably: usage: office"
        )
    ]


@pytest.mark.parametrize(
    "category, must_have, phrase",
    [
        ("Casual Shoes", MustHave(id="m1", attribute="logo_visible", rule="absent", value=True), "logo visible: none"),
        ("Sunglasses", MustHave(id="m1", attribute="polarised", rule="present", value=True), "polarised: yes"),
        ("Sunglasses", MustHave(id="m1", attribute="polarised", rule="is", value="yes"), "polarised: yes"),
        ("Watches", MustHave(id="m1", attribute="dial_colour", rule="is", value="green"), "dial colour: green"),
    ],
)
def test_must_haves_are_worded_like_product_details(category, must_have, phrase):
    text = search_text(brief(category=category, gender="men", must_haves=[must_have]), "words")
    assert text == f"words. {category} for men. Details: {phrase}"


def test_with_a_photo_both_searches_are_merged_at_their_best_distance():
    db = FakeFirestore(
        product_doc("words", embedding=TEXT_VECTOR),
        product_doc("photo", embedding=IMAGE_VECTOR),
        product_doc("both", embedding=unit(1, 3, 0)),
    )
    embedder = FakeEmbedder()
    hits = find_candidates(
        brief(),
        query_text="kurta like this",
        reference_image_url="https://example.com/look.jpg",
        db=db,
        embedder=embedder,
        fetch=lambda url: (b"jpeg bytes", "image/jpeg"),
    )
    by_id = {hit.product.id: hit.distance for hit in hits}
    assert ids(hits) == ["photo", "words", "both"]
    assert by_id["both"] == pytest.approx(1 - 3 / 10**0.5)
    assert embedder.images == [b"jpeg bytes"] and len(db.searches) == 2


def test_a_photo_that_cannot_be_read_falls_back_to_the_words(caplog):
    def broken(url: str) -> tuple[bytes, str]:
        raise OSError("404")

    db = FakeFirestore(product_doc("words", embedding=TEXT_VECTOR), product_doc("photo", embedding=IMAGE_VECTOR))
    hits = search(db, reference_image_url="https://example.com/gone.jpg", fetch=broken, limit=1)
    assert ids(hits) == ["words"]
    assert "photo search skipped" in caplog.text


def test_timings_are_reported_per_stage():
    timings: dict[str, float] = {}
    search(FakeFirestore(product_doc("p1")), timings=timings)
    assert set(timings) == {"text_embed_ms", "text_search_ms", "limits_ms", "total_ms"}
    assert all(ms >= 0 for ms in timings.values())


def test_embeddings_can_be_added_for_the_products_that_need_them():
    db = FakeFirestore(product_doc("p1", embedding=[0.6, 0.8, 0.0]), product_doc("p2"))
    hits = attach_embeddings(search(db), db=db)
    assert {hit.product.id: hit.product.embedding for hit in hits} == {"p1": [0.6, 0.8, 0.0], "p2": TEXT_VECTOR}


# ---------- Soft limits, total price and delivery ----------


@pytest.mark.parametrize(
    "selling, kept, over_budget",
    [
        (1000, True, False),
        (1190, True, True),
        (1200, True, True),
        (1210, False, None),
    ],
    ids=["at budget", "19% over", "20% over", "21% over"],
)
def test_products_up_to_20_percent_over_budget_are_kept_and_marked(selling, kept, over_budget):
    hits = search(FakeFirestore(product_doc("p", selling=selling)), brief(budget_max_inr=1000))
    assert [hit.over_budget for hit in hits] == ([over_budget] if kept else [])


def test_products_rated_below_the_minimum_are_left_out():
    rated = brief(min_rating=4.0)
    assert make_hit(rated, product().model_copy(update={"rating_avg": 4.0}), 0.1) is not None
    assert make_hit(rated, product().model_copy(update={"rating_avg": 3.9}), 0.1) is None
    assert make_hit(rated, product().model_copy(update={"rating_avg": None}), 0.1) is None
    assert make_hit(brief(), product().model_copy(update={"rating_avg": 1.0}), 0.1) is not None


def test_the_budget_counts_the_delivery_fee():
    hit = make_hit(brief(budget_max_inr=450), product(selling=450, delivery_fee=49), 0.1)
    assert (hit.total_price, hit.over_budget) == (499, True)


def test_the_cod_fee_counts_only_when_cod_is_a_must_have():
    cheap = product(selling=450, delivery_fee=49, cod=(True, 29))
    assert make_hit(brief(), cheap, 0.1).total_price == 499
    assert make_hit(brief(must_haves=[COD]), cheap, 0.1).total_price == 528
    cod_by_value = MustHave(id="m1", attribute="cod", rule="is", value="yes")
    assert make_hit(brief(must_haves=[cod_by_value]), cheap, 0.1).total_price == 528
    no_cod = product(selling=450, delivery_fee=49, cod=(False, 0))
    assert make_hit(brief(must_haves=[COD]), no_cod, 0.1).total_price == 499


def test_products_up_to_2_days_late_are_kept_and_marked():
    db = FakeFirestore(
        product_doc("2 days late", city="Mumbai"),
        product_doc("3 days late", city="Delhi"),
    )
    hits = search(db, brief(pincode=MUMBAI_PINCODE, deadline_date=TUESDAY))
    assert ids(hits) == ["2 days late"]
    assert (hits[0].delivery_date, hits[0].late) == (THURSDAY, True)


def test_on_time_products_are_not_marked():
    request = brief(pincode=MUMBAI_PINCODE, deadline_date=THURSDAY, budget_max_inr=1000)
    [hit] = search(FakeFirestore(product_doc("p")), request)
    assert (hit.delivery_date, hit.late, hit.over_budget, hit.note) == (THURSDAY, False, False, None)


def test_an_unknown_pincode_keeps_the_products_with_a_note():
    [hit] = search(FakeFirestore(product_doc("p")), brief(pincode=NOT_A_PINCODE, deadline_date=TUESDAY))
    assert hit.delivery_date is None and not hit.late
    assert hit.note == f"We couldn't find pincode '{NOT_A_PINCODE}'. Please check it."


def test_one_products_bad_data_never_fails_the_search(caplog):
    db = FakeFirestore(
        product_doc("good"),
        product_doc("odd warehouse", city="Pune"),
        product_doc("no price", selling=None),
        product_doc("bad group", group="sarees"),
        product_doc("no warehouse", warehouse=None),
    )
    hits = search(db, brief(pincode=MUMBAI_PINCODE))
    notes = {hit.product.id: hit.note for hit in hits}
    assert notes == {
        "good": None,
        "odd warehouse": "We couldn't work out a delivery date for this product.",
        "no warehouse": "This product has no warehouse, so we can't promise a delivery date.",
    }
    assert "no price" in caplog.text and "bad group" in caplog.text


# ---------- Live: the proxy evaluation against the products collection ----------


@pytest.mark.live
def test_live_proxy_recall_hard_filters_and_quick_check_false_drops():
    results = evaluate(cases(india_time().date()))
    print(report(results))
    summary = Summary.of(results)
    assert summary.violations == 0
    assert summary.false_drops == summary.false_drops_all_proxy == 0
    assert summary.found_after_quick_check >= TARGET_RECALL * summary.reachable
