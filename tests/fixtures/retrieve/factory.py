"""Fakes and builders for the retrieve and quick-check tests: a small in-memory Firestore and embedder."""

import math
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from agent.models import Attribute, MustHave, Product, ShoppingBrief
from agent.retrieve import Hit
from data_prep import schemas
from data_prep.embed import Embedding

# 13 October 2026 is a Tuesday. Before 2 pm, so orders ship the same day.
TUESDAY_10AM = datetime(2026, 10, 13, 10, 0)
MUMBAI_PINCODE = "400001"
NOT_A_PINCODE = "999999"

TEXT_VECTOR = [1.0, 0.0, 0.0]
IMAGE_VECTOR = [0.0, 1.0, 0.0]


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def product_doc(
    product_id: str,
    *,
    article_type: str = "Kurtas",
    gender: str = "women",
    selling: int | None = 1000,
    delivery_fee: int = 0,
    cod: tuple[bool, int] | None = (True, 29),
    returns: tuple[bool, bool] | None = (True, True),
    city: str = "Mumbai",
    sizes: dict[str, int] | None = None,
    attributes: dict[str, dict] | None = None,
    embedding: list[float] = TEXT_VECTOR,
    **extra: Any,
) -> dict:
    """A document laid out like PLAN.md appendix F, plus in_stock_sizes."""
    sizes = sizes if sizes is not None else {"S": 3, "M": 2, "L": 0}
    doc = {
        "id": product_id,
        "title": f"Product {product_id}",
        "gender": gender,
        "masterCategory": "Apparel",
        "subCategory": "Topwear",
        "articleType": article_type,
        "group": schemas.group_for(article_type),
        "sizes": sizes,
        "in_stock_sizes": [size for size, count in sizes.items() if count > 0],
        "warehouse": {"city": city, "pincode": "400001"},
        "delivery_fee": delivery_fee,
        "attributes": attributes or {},
        "embedding": embedding,
        "demo_data": True,
    }
    if selling is not None:
        doc["price"] = {"mrp": selling * 2, "selling": selling, "demo": True}
    if cod is not None:
        doc["cod"] = {"available": cod[0], "fee": cod[1]}
    if returns is not None:
        doc["returns"] = {"returnable": returns[0], "window_days": 7 if returns[0] else 0, "exchange": returns[1]}
    return doc | extra


def brief(**fields: Any) -> ShoppingBrief:
    return ShoppingBrief(**({"category": "Kurtas", "gender": "women"} | fields))


class FakeEmbedder:
    """Words embed to TEXT_VECTOR and a photo to IMAGE_VECTOR."""

    def __init__(self):
        self.texts: list[str] = []
        self.images: list[bytes] = []

    def __call__(self, parts: list) -> Embedding:
        images = [p.inline_data.data for p in parts if p.inline_data is not None]
        if images:
            self.images += images
            return Embedding(IMAGE_VECTOR)
        self.texts += [p.text for p in parts if p.text]
        return Embedding(TEXT_VECTOR)


def _passes(doc: dict, field_filter: Any) -> bool:
    value = doc.get(field_filter.field_path)
    match field_filter.op_string:
        case "==":
            return value == field_filter.value
        case "in":
            return value in field_filter.value
        case "array_contains":
            return field_filter.value in (value or [])
    raise AssertionError(f"the fake doesn't know the operator {field_filter.op_string}")


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return 1 - dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class FakeQuery:
    def __init__(self, db: "FakeFirestore", collection: str):
        self.db = db
        self.collection = collection
        self.filters: list[Any] = []
        self.fields: list[str] | None = None
        self.nearest: dict | None = None

    def where(self, *, filter: Any) -> "FakeQuery":
        self.filters.append(filter)
        return self

    def select(self, fields: list[str]) -> "FakeQuery":
        self.fields = list(fields)
        return self

    def find_nearest(self, **kwargs: Any) -> "FakeQuery":
        self.nearest = kwargs
        return self

    def document(self, doc_id: str) -> SimpleNamespace:
        return SimpleNamespace(collection=self.collection, id=doc_id)

    def stream(self):
        assert self.nearest is not None, "only vector searches are faked"
        self.db.searches.append(self)
        vector = list(self.nearest["query_vector"])
        distance_field = self.nearest["distance_result_field"]
        docs = [d for d in self.db.data.get(self.collection, {}).values() if all(_passes(d, f) for f in self.filters)]
        scored = sorted(((_cosine_distance(vector, d["embedding"]), d) for d in docs), key=lambda pair: pair[0])
        for distance, doc in scored[: self.nearest["limit"]]:
            stored = doc | {distance_field: distance}
            shown = {k: v for k, v in stored.items() if self.fields is None or k in self.fields}
            yield SimpleNamespace(id=doc["id"], to_dict=lambda s=shown: dict(s))


class FakeFirestore:
    def __init__(self, *docs: dict, collection: str = "products"):
        self.data: dict[str, dict[str, dict]] = {collection: {d["id"]: d for d in docs}}
        self.searches: list[FakeQuery] = []

    def collection(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def get_all(self, refs: list, field_paths: list[str] | None = None):
        for ref in refs:
            stored = self.data.get(ref.collection, {}).get(ref.id)
            shown = {k: v for k, v in (stored or {}).items() if field_paths is None or k in field_paths}
            yield SimpleNamespace(id=ref.id, exists=stored is not None, to_dict=lambda s=shown: dict(s))


def product(
    product_id: str = "p1",
    *,
    article_type: str = "Kurtas",
    attributes: dict[str, Attribute] | None = None,
    needs_review: list[str] | None = None,
    **doc_fields: Any,
) -> Product:
    doc = product_doc(product_id, article_type=article_type, **doc_fields)
    product = Product.model_validate(doc)
    return product.model_copy(update={"attributes": attributes or {}, "needs_review": needs_review or []})


def hit(product: Product, distance: float = 0.3) -> Hit:
    return Hit(product=product, distance=distance, total_price=product.price.selling)


def catalogue(value: str | bool | float, gemini_value: str | None = None) -> Attribute:
    return Attribute(value=value, confidence=1.0, source="catalogue", gemini_value=gemini_value)


def gemini(value: str | bool | float, confidence: float) -> Attribute:
    return Attribute(value=value, confidence=confidence, source="gemini")


def must(attribute: str, rule: str, value: str | bool | float, must_id: str = "m1") -> MustHave:
    return MustHave(id=must_id, attribute=attribute, rule=rule, value=value)
