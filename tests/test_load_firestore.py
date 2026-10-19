import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.cloud.firestore_v1.vector import Vector

from agent.models import Attribute, Product
from data_prep import load_firestore
from data_prep.embed import read_jsonl_by_id, read_selected
from data_prep.load_firestore import (
    INDEX_FILTERS,
    FirestoreApiError,
    Incomplete,
    Inputs,
    build_documents,
    build_product,
    check_documents,
    count_documents,
    create_indexes,
    firestore_document,
    index_body,
    index_key,
    indexes_url,
    plain,
    wait_until_ready,
    write_documents,
)

FIXTURES = Path(__file__).parent / "fixtures" / "firestore"
DIMENSIONS = 768
# PLAN.md appendix F, products/{productId}, plus the document id itself.
APPENDIX_F_FIELDS = {
    "id", "title", "brand", "gender", "masterCategory", "subCategory", "articleType", "baseColour", "usage", "group",
    "description", "attributes", "catalogue_details", "needs_review", "price", "sizes", "warehouse", "delivery_fee",
    "cod", "returns", "reviews", "rating_avg", "rating_count", "images", "embedding", "demo_data", "source",
}
ADDED_FIELDS = {"in_stock_sizes"}


def vector_for(product_id: str) -> list[float]:
    return [((int(product_id) + i) % 13) / 13 + 0.01 for i in range(DIMENSIONS)]


def embeddings_for(*ids: str) -> dict[str, dict]:
    return {i: {"id": i, "embedding": vector_for(i), "text_used": "t", "model": "gemini-embedding-2"} for i in ids}


@pytest.fixture
def inputs() -> Inputs:
    return Inputs(
        products=read_selected(FIXTURES / "selected.csv"),
        photos=read_jsonl_by_id(FIXTURES / "photos.jsonl"),
        real=read_jsonl_by_id(FIXTURES / "real_details.jsonl"),
        shop=read_jsonl_by_id(FIXTURES / "shop.jsonl"),
        embeddings=embeddings_for("30763", "29493", "11297"),
        enriched=read_jsonl_by_id(FIXTURES / "enriched.jsonl"),
        reviews=read_jsonl_by_id(FIXTURES / "reviews.jsonl"),
    )


# ---------- A fake Firestore ----------


class FakeRef:
    def __init__(self, collection: str, doc_id: str):
        self.collection = collection
        self.id = doc_id


class FakeBatch:
    def __init__(self, db: "FakeFirestore"):
        self.db = db
        self.writes: list[tuple[FakeRef, dict]] = []

    def set(self, ref: FakeRef, data: dict) -> None:
        self.writes.append((ref, data))

    def commit(self) -> list:
        assert len(self.writes) <= 500, "Firestore allows at most 500 writes per batch"
        for ref, data in self.writes:
            self.db.data.setdefault(ref.collection, {})[ref.id] = copy.deepcopy(data)
        self.db.commits.append(len(self.writes))
        return []


class FakeCollection:
    def __init__(self, db: "FakeFirestore", name: str):
        self.db = db
        self.name = name

    def document(self, doc_id: str) -> FakeRef:
        return FakeRef(self.name, doc_id)

    def count(self) -> SimpleNamespace:
        total = len(self.db.data.get(self.name, {}))
        return SimpleNamespace(get=lambda: [[SimpleNamespace(value=total)]])


class FakeFirestore:
    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {}
        self.commits: list[int] = []

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)

    def batch(self) -> FakeBatch:
        return FakeBatch(self)

    def get_all(self, refs: list[FakeRef]):
        for ref in refs:
            stored = self.data.get(ref.collection, {}).get(ref.id)
            yield SimpleNamespace(id=ref.id, exists=stored is not None, to_dict=lambda s=stored: copy.deepcopy(s))


# ---------- Joining the inputs ----------


def test_an_enriched_product_joins_every_input(inputs):
    product = build_product("30763", inputs, DIMENSIONS)
    enriched = inputs.enriched["30763"]
    assert (product.title, product.article_type, product.gender, product.group) == (
        "Fabindia Men Printed White Kurta",
        "Kurtas",
        "men",
        "tops",
    )
    assert product.brand == "Fabindia"
    assert product.description == enriched["description"]
    colour = Attribute(value="white", confidence=1.0, note="Catalogue colour: White", source="catalogue")
    assert product.attributes == {**{name: Attribute(**a) for name, a in enriched["attributes"].items()}, "colour": colour}
    assert product.needs_review == ["neckline"]
    assert product.catalogue_details["Body or Garment Size"] == "Garment Measurements in"
    assert product.catalogue_details["description"].startswith("Composition\nPrinted white kurta")
    assert (product.price.selling, product.price.mrp, product.price.demo) == (830, 830, False)
    assert product.sizes == {"S": 12, "M": 13, "L": 0, "XL": 7, "XXL": 13}
    assert (product.warehouse.city, product.delivery_fee, product.cod.fee) == ("Bengaluru", 0, 29)
    assert (product.returns.returnable, product.returns.window_days) == (True, 14)
    assert [(r.rating, r.flagged, r.demo) for r in product.reviews] == [(5, False, True), (2, True, True)]
    assert (product.rating_avg, product.rating_count) == (3.5, 2)
    assert product.images.gemini.endswith("/gemini/30763.jpg")
    assert product.embedding == vector_for("30763")
    assert product.demo_data is True


def test_without_enrichment_or_reviews_the_catalogue_labels_are_the_details(inputs):
    product = build_product("29493", inputs, DIMENSIONS)
    assert set(product.attributes) == {"display", "case_shape", "strap_material", "case_size"}
    assert all(a.source == "catalogue" and a.confidence == 1.0 for a in product.attributes.values())
    assert product.attributes["strap_material"].value == "leather"
    assert product.description.startswith("Case diameter: 32 mm\nGold plated case")
    assert product.catalogue_details["Strap Material"] == "Leather"
    assert (product.reviews, product.rating_avg, product.rating_count, product.needs_review) == ([], None, 0, [])


def test_incomplete_products_are_skipped_with_the_reason(inputs):
    inputs.shop.pop("29493")
    built = build_documents(inputs.products, inputs, DIMENSIONS)
    assert list(built.documents) == ["30763"]
    assert built.skipped == {"29493": "no shop data", "36067": "no embedding", "11297": "no photo"}


def test_an_embedding_of_the_wrong_size_is_skipped(inputs):
    inputs.embeddings["29493"]["embedding"] = [0.1] * 3072
    with pytest.raises(Incomplete, match="3072 numbers, not 768"):
        build_product("29493", inputs, DIMENSIONS)


def test_a_product_that_fails_validation_is_skipped_not_fatal(inputs):
    inputs.reviews["30763"]["reviews"][0]["rating"] = 7
    built = build_documents(["30763", "29493"], inputs, DIMENSIONS)
    assert list(built.documents) == ["29493"]
    assert built.skipped["30763"].startswith("invalid: reviews.0.rating")


# ---------- The Firestore document ----------


def test_the_document_has_appendix_f_fields_a_vector_and_in_stock_sizes(inputs):
    document = firestore_document(build_product("30763", inputs, DIMENSIONS))
    assert set(document) <= APPENDIX_F_FIELDS | ADDED_FIELDS
    assert set(document) - APPENDIX_F_FIELDS == ADDED_FIELDS
    assert isinstance(document["embedding"], Vector)
    assert list(document["embedding"]) == vector_for("30763")
    assert document["in_stock_sizes"] == ["S", "M", "XL", "XXL"]
    assert (document["articleType"], document["masterCategory"], document["baseColour"]) == ("Kurtas", "Apparel", "White")
    assert "planted" not in json.dumps(plain(document)) and "tricks" not in document
    assert document["attributes"]["pockets"] == {
        "value": "chest",
        "confidence": 0.85,
        "note": "patch pocket on the left chest",
        "source": "gemini",
    }


def test_a_stored_document_reads_back_as_a_product(inputs):
    document = plain(firestore_document(build_product("29493", inputs, DIMENSIONS)))
    product = Product.model_validate(document)
    assert product.embedding == vector_for("29493")
    assert product.article_type == "Watches" and product.sizes == {"One size": 9}


# ---------- Writing and checking ----------


def test_writing_twice_gives_the_same_collection_in_batches(inputs):
    built = build_documents(inputs.products, inputs, DIMENSIONS)
    db = FakeFirestore()
    assert write_documents(db, "products_trial", built.documents, batch_size=1) == 2
    first = copy.deepcopy(db.data)
    write_documents(db, "products_trial", built.documents, batch_size=1)
    assert db.data == first
    assert db.commits == [1, 1, 1, 1]
    assert set(db.data["products_trial"]) == {"30763", "29493"}
    assert count_documents(db, "products_trial") == 2


def test_a_reload_replaces_old_fields(inputs):
    built = build_documents(["30763"], inputs, DIMENSIONS)
    db = FakeFirestore()
    db.data["products"] = {"30763": {"title": "old", "stale_field": True}}
    write_documents(db, "products", built.documents)
    assert "stale_field" not in db.data["products"]["30763"]


def test_check_finds_matching_changed_and_missing_documents(inputs):
    built = build_documents(inputs.products, inputs, DIMENSIONS)
    db = FakeFirestore()
    write_documents(db, "products_trial", built.documents)
    assert check_documents(db, "products_trial", built.documents, ["30763", "29493"]) == []

    db.data["products_trial"]["29493"]["price"]["selling"] = 1
    db.data["products_trial"]["29493"]["embedding"] = Vector([0.0] * DIMENSIONS)
    del db.data["products_trial"]["30763"]
    assert check_documents(db, "products_trial", built.documents, ["30763", "29493"]) == [
        "30763: not in products_trial",
        "29493: differs in embedding, price",
    ]


def test_main_loads_the_trial_and_checks_it(tmp_path, monkeypatch):
    embeddings = tmp_path / "embeddings_trial.jsonl"
    embeddings.write_text("".join(json.dumps(r) + "\n" for r in embeddings_for("30763", "29493").values()))
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("30763\n29493\n36067\n11297\n")
    db = FakeFirestore()
    monkeypatch.setattr(load_firestore, "load_dotenv", lambda: None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.setattr(load_firestore, "load_credentials", lambda settings: None)
    monkeypatch.setattr(load_firestore.firestore, "Client", lambda **kwargs: db)

    args = ["--collection", "products_trial", "--ids-file", str(ids_file), "--embeddings", str(embeddings)]
    for flag, name in [("--selected", "selected.csv"), ("--photos", "photos.jsonl"), ("--shop", "shop.jsonl")]:
        args += [flag, str(FIXTURES / name)]
    args += ["--real-details", str(FIXTURES / "real_details.jsonl"), "--enriched", str(tmp_path / "none.jsonl")]
    args += ["--reviews", str(tmp_path / "none.jsonl")]

    assert load_firestore.main(args + ["--dry-run"]) == 0
    assert db.data == {}
    assert load_firestore.main(args) == 0
    assert set(db.data["products_trial"]) == {"30763", "29493"}
    assert set(db.data["products_trial"]["30763"]["attributes"]) == {"colour"}, "only the catalogue colour"
    assert load_firestore.main(args + ["--check"]) == 0
    db.data["products_trial"]["29493"]["title"] = "changed"
    assert load_firestore.main(args + ["--check"]) == 1


# ---------- Vector indexes ----------


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self.body = body or {}
        self.text = json.dumps(self.body)

    def json(self) -> dict:
        return self.body


URL = indexes_url("p", "(default)", "products")
TRIAL_URL = indexes_url("p", "(default)", "products_trial")


class FakeAdminApi:
    """Like the Firestore Admin API: lists every collection group's indexes with __name__ added, in pages,
    and keeps new indexes CREATING for a few polls."""

    def __init__(self, existing: dict[str, list[dict]] | None = None, polls_until_ready: int = 1, page_size: int = 2):
        self.indexes = [
            dict(index, state="READY", name=self.index_name(url, n))
            for url, indexes in (existing or {}).items()
            for n, index in enumerate(indexes)
        ]
        self.posted: list[dict] = []
        self.polls_until_ready = polls_until_ready
        self.page_size = page_size

    @staticmethod
    def index_name(url: str, number: int) -> str:
        return url.removeprefix("https://firestore.googleapis.com/v1/") + f"/X{number}"

    def get(self, url: str, params: dict | None = None, timeout: float = 0) -> FakeResponse:
        start = int((params or {}).get("pageToken") or 0)
        page = [
            dict(index, fields=index["fields"] + [{"fieldPath": "__name__", "order": "ASCENDING"}])
            for index in self.indexes[start : start + self.page_size]
        ]
        end = start + self.page_size
        body = {"indexes": page} | ({"nextPageToken": str(end)} if end < len(self.indexes) else {})
        return FakeResponse(200, body)

    def post(self, url: str, json: dict, timeout: float = 0) -> FakeResponse:
        self.posted.append(json)
        self.indexes.append(dict(json, state="CREATING", name=self.index_name(url, 100 + len(self.posted))))
        return FakeResponse(200, {"name": "operations/1"})

    def tick(self) -> None:
        self.polls_until_ready -= 1
        if self.polls_until_ready <= 0:
            for index in self.indexes:
                index["state"] = "READY"


def test_index_request_body_matches_the_firestore_vector_index_format():
    assert index_body(("articleType", "gender", "in_stock_sizes"), 768) == {
        "queryScope": "COLLECTION",
        "fields": [
            {"fieldPath": "articleType", "order": "ASCENDING"},
            {"fieldPath": "gender", "order": "ASCENDING"},
            {"fieldPath": "in_stock_sizes", "arrayConfig": "CONTAINS"},
            {"fieldPath": "embedding", "vectorConfig": {"dimension": 768, "flat": {}}},
        ],
    }
    assert index_body((), 768)["fields"] == [{"fieldPath": "embedding", "vectorConfig": {"dimension": 768, "flat": {}}}]


def test_the_indexes_cover_no_filter_and_the_step_2_3_filters():
    assert () in INDEX_FILTERS and ("articleType", "gender") in INDEX_FILTERS
    assert ("articleType", "gender", "in_stock_sizes") in INDEX_FILTERS
    assert len(set(INDEX_FILTERS)) == len(INDEX_FILTERS)


def test_indexes_url_is_per_collection_group():
    assert indexes_url("p", "(default)", "products_trial") == (
        "https://firestore.googleapis.com/v1/projects/p/databases/(default)/collectionGroups/products_trial/indexes"
    )


def test_creating_indexes_is_idempotent_and_ignores_the_listed_name_field():
    wanted = [index_body(filters, DIMENSIONS) for filters in INDEX_FILTERS]
    unrelated = {"queryScope": "COLLECTION", "fields": [{"fieldPath": "title", "order": "ASCENDING"}]}
    api = FakeAdminApi(existing={URL: [wanted[0], unrelated]})
    created = create_indexes(api, URL, wanted)
    assert created == wanted[1:] == api.posted
    assert create_indexes(api, URL, wanted) == []
    assert len(api.posted) == len(wanted) - 1


def test_the_same_index_on_another_collection_group_does_not_count():
    wanted = [index_body(filters, DIMENSIONS) for filters in INDEX_FILTERS]
    api = FakeAdminApi(existing={TRIAL_URL: wanted})
    assert create_indexes(api, URL, wanted) == wanted
    assert create_indexes(api, TRIAL_URL, wanted) == []


def test_an_index_with_another_dimension_is_a_different_index():
    assert index_key(index_body(("gender",), 768)) != index_key(index_body(("gender",), 1536))


def test_a_conflict_counts_as_already_there_and_other_errors_stop():
    class Conflict(FakeAdminApi):
        def post(self, url, json, timeout=0):
            return FakeResponse(409, {"error": {"status": "ALREADY_EXISTS"}})

    assert create_indexes(Conflict(), URL, [index_body((), DIMENSIONS)]) == []

    class Refused(FakeAdminApi):
        def post(self, url, json, timeout=0):
            return FakeResponse(400, {"error": {"message": "bad index"}})

    with pytest.raises(FirestoreApiError, match="HTTP 400.*bad index"):
        create_indexes(Refused(), URL, [index_body((), DIMENSIONS)])


def test_waiting_polls_until_every_index_is_ready():
    wanted = [index_body(filters, DIMENSIONS) for filters in INDEX_FILTERS]
    api = FakeAdminApi(polls_until_ready=3)
    create_indexes(api, URL, wanted)
    states = wait_until_ready(api, URL, wanted, poll_seconds=5, sleep=lambda _: api.tick(), clock=lambda: 0)
    assert set(states.values()) == {"READY"} and len(states) == len(wanted)
    assert "embedding (768 numbers)" in states
    assert "articleType (ascending), gender (ascending), in_stock_sizes (contains), embedding (768 numbers)" in states


def test_waiting_gives_up_after_the_timeout_and_on_needs_repair():
    wanted = [index_body((), DIMENSIONS)]
    api = FakeAdminApi(polls_until_ready=100)
    create_indexes(api, URL, wanted)
    times = iter([0, 10, 20, 9999])
    with pytest.raises(TimeoutError):
        wait_until_ready(api, URL, wanted, timeout_seconds=60, sleep=lambda _: None, clock=lambda: next(times))

    api.indexes[0]["state"] = "NEEDS_REPAIR"
    with pytest.raises(FirestoreApiError, match="needs repair"):
        wait_until_ready(api, URL, wanted, sleep=lambda _: None, clock=lambda: 0)
