"""In-memory stand-ins for Firestore, Cloud Storage and Gemini, for the workflow, API and ADK tests."""

import copy
import itertools
import json
import math
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from agent import verify
from agent.app import Backend
from agent.sessions import Store
from agent.sources import shopify
from agent.web_search import WebFindings, WebResult
from agent.workflow import LiveServices, Services
from data_prep import schemas
from data_prep.embed import Embedding

SHOPIFY_FIXTURES = Path(__file__).resolve().parent.parent / "shopify"

# 13 October 2026 is a Tuesday; before 2 pm, so orders ship the same day.
TUESDAY_10AM = datetime(2026, 10, 13, 10, 0)
BENGALURU = "560001"
DIMENSIONS = 64
_free_axes = itertools.count()


def axis(index: int, weight: float = 1.0) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[index] = weight
    return vector


TEXT_VECTOR = axis(0)
IMAGE_VECTOR = axis(1)


def near_text(weight: float = 0.6) -> list[float]:
    """Close to TEXT_VECTOR (further for a bigger weight), and never a near-duplicate of another product."""
    vector = axis(0)
    vector[2 + next(_free_axes) % (DIMENSIONS - 2)] = weight
    return vector


# ---------- Firestore ----------


class FakeSnapshot:
    def __init__(self, reference: "FakeDocument", data: dict | None):
        self.reference = reference
        self.id = reference.id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict | None:
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, db: "FakeDB", collection: str, doc_id: str):
        self.db, self.collection, self.id = db, collection, doc_id

    def get(self, field_paths: list[str] | None = None) -> FakeSnapshot:
        stored = self.db.data.get(self.collection, {}).get(self.id)
        if stored is not None and field_paths is not None:
            stored = {k: v for k, v in stored.items() if k in field_paths}
        return FakeSnapshot(self, stored)

    def set(self, data: dict, merge: bool | list[str] = False) -> None:
        with self.db.lock:
            documents = self.db.data.setdefault(self.collection, {})
            current = documents.get(self.id)
            if not merge or current is None:
                documents[self.id] = copy.deepcopy(data)
            else:
                names = list(data) if merge is True else merge
                for name in names:
                    current[name] = copy.deepcopy(data[name])

    def delete(self) -> None:
        with self.db.lock:
            self.db.data.get(self.collection, {}).pop(self.id, None)


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


def _distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return 1 - dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class FakeQuery:
    def __init__(self, db: "FakeDB", collection: str):
        self.db = db
        self.collection = collection
        self.filters: list[Any] = []
        self.fields: list[str] | None = None
        self.nearest: dict | None = None

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(self.db, self.collection, doc_id)

    def where(self, *, filter: Any) -> "FakeQuery":
        query = copy.copy(self)
        query.filters = [*self.filters, filter]
        return query

    def select(self, fields: list[str]) -> "FakeQuery":
        query = copy.copy(self)
        query.fields = list(fields)
        return query

    def find_nearest(self, **kwargs: Any) -> "FakeQuery":
        query = copy.copy(self)
        query.nearest = kwargs
        return query

    def stream(self):
        if self.db.fail_searches:
            raise self.db.fail_searches
        docs = [
            (doc_id, doc)
            for doc_id, doc in self.db.data.get(self.collection, {}).items()
            if all(_passes(doc, f) for f in self.filters)
        ]
        if self.nearest is None:
            for doc_id, _ in docs:
                yield self.document(doc_id).get()
            return
        self.db.vector_searches += 1
        vector = list(self.nearest["query_vector"])
        field = self.nearest["distance_result_field"]
        scored = sorted(((_distance(vector, d["embedding"]), i, d) for i, d in docs), key=lambda item: item[0])
        for distance, doc_id, doc in scored[: self.nearest["limit"]]:
            shown = {k: v for k, v in (doc | {field: distance}).items() if self.fields is None or k in self.fields}
            yield SimpleNamespace(id=doc_id, to_dict=lambda s=shown: copy.deepcopy(s))


class FakeBatch:
    def __init__(self, db: "FakeDB"):
        self.db = db
        self.actions: list[Callable[[], None]] = []

    def set(self, ref: FakeDocument, data: dict) -> None:
        self.actions.append(lambda: ref.set(data))

    def delete(self, ref: FakeDocument) -> None:
        self.actions.append(ref.delete)

    def commit(self) -> None:
        for action in self.actions:
            action()


class FakeDB:
    def __init__(self, products: list[dict] = ()):
        self.data: dict[str, dict[str, dict]] = {"products": {p["id"]: copy.deepcopy(p) for p in products}}
        self.lock = threading.Lock()
        self.vector_searches = 0
        self.fail_searches: Exception | None = None

    def collection(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def get_all(self, refs: list[FakeDocument], field_paths: list[str] | None = None):
        for ref in refs:
            yield ref.get(field_paths)

    def batch(self) -> FakeBatch:
        return FakeBatch(self)

    def docs(self, collection: str) -> dict[str, dict]:
        return self.data.get(collection, {})


# ---------- Cloud Storage ----------


class FakeBlob:
    def __init__(self, bucket: "FakeBucket", name: str):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.bucket.files[self.name] = (data, content_type)

    def download_as_bytes(self) -> bytes:
        return self.bucket.files[self.name][0]

    def delete(self) -> None:
        del self.bucket.files[self.name]


class FakeBucket:
    name = "test-uploads"

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def list_blobs(self, prefix: str = "") -> list[FakeBlob]:
        return [FakeBlob(self, name) for name in list(self.files) if name.startswith(prefix)]


# ---------- Gemini ----------


class FakeEmbedder:
    """Words embed to TEXT_VECTOR and a photo to IMAGE_VECTOR."""

    def __init__(self):
        self.texts: list[str] = []
        self.images = 0

    def __call__(self, parts: list) -> Embedding:
        if any(part.inline_data is not None for part in parts):
            self.images += 1
            return Embedding(IMAGE_VECTOR)
        self.texts += [part.text for part in parts if part.text]
        return Embedding(TEXT_VECTOR)


class FakeModels:
    def __init__(self, respond: Callable[[Any], str | Exception]):
        self.respond = respond
        self.calls: list[Any] = []
        self.model_names: list[str] = []
        self.lock = threading.Lock()

    def generate_content(self, *, model: str, contents: Any, config: Any) -> SimpleNamespace:
        with self.lock:
            self.calls.append(contents)
            self.model_names.append(model)
        result = self.respond(contents)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(text=result, usage_metadata=None)


class FakeClient:
    def __init__(self, respond: Callable[[Any], str | Exception]):
        self.models = FakeModels(respond)


def brief_answer(
    category: str = "Casual Shoes",
    *,
    gender: str = "men",
    must: list[tuple[str, str]] = (("type=sneakers", "is"), ("colour=white", "is"), ("logo_visible=none", "is")),
    nice: list[tuple[str, str]] = (),
    budget: int = 3000,
    size: str = "9",
    question_about: str = "none",
    question: str = "",
) -> str:
    """A checklist answer laid out like Gemini's (agent/brief.py answer_schema)."""
    return json.dumps(
        {
            "category": category,
            "gender": gender,
            "must_haves": [{"said": detail, "detail": detail, "rule": rule} for detail, rule in must],
            "nice_to_haves": [{"said": detail, "detail": detail, "rule": rule} for detail, rule in nice],
            "not_checkable": [],
            "budget_max_inr": budget,
            "deadline": {"kind": "none", "days": 0, "weekday": "none", "next_week": False, "day": 0, "month": 0},
            "size": size,
            "language": "en",
            "question_about": question_about,
            "question": question,
        }
    )


def photo_answer(category: str = "Casual Shoes", gender: str = "men") -> str:
    return json.dumps({"category": category, "gender": gender})


PHOTO_ID = re.compile(r"/gemini/(?P<id>[^/.]+)\.jpg$")
LIVE_PHOTO = re.compile(r"cdn\.shopify\.com/s/files/[\d/]+/files/(?P<name>[^?]+)")


def asked_ids(prompt: str) -> list[str]:
    block = prompt.split("=== MUST-HAVES")[1].split("=== END MUST-HAVES")[0]
    return [line.split(":")[0].removeprefix("- ") for line in block.splitlines() if line.startswith("- ")]


class Checker:
    """A careful-check client: every must-have passes from the photo unless `verdicts` says otherwise.

    verdicts maps a product id to a status per must-have id ({"m3": "fail"}) or to an exception to raise.
    """

    def __init__(self, verdicts: dict[str, dict[str, str] | Exception] | None = None):
        self.verdicts = verdicts or {}
        self.client = FakeClient(self.respond)

    def respond(self, contents: list) -> str | Exception:
        uri = contents[0].file_data.file_uri
        found = PHOTO_ID.search(uri) or LIVE_PHOTO.search(uri)
        product_id = found["id"] if "id" in found.groupdict() else found["name"]
        verdict = self.verdicts.get(product_id, {})
        if isinstance(verdict, Exception):
            return verdict
        checks = [
            verify.AnswerCheck(
                must_have_id=must_id,
                status=verdict.get(must_id, "pass"),
                confidence=0.9,
                evidence=verify.AnswerEvidence(
                    type="image", note=f"{must_id} seen in the photo", quote="", box_2d=[100, 100, 300, 300],
                    agree_reviews=[], disagree_reviews=[],
                ),
            )
            for must_id in asked_ids(contents[1])
        ]
        return verify.Answer(checks=checks).model_dump_json()

    @property
    def calls(self) -> int:
        return len(self.client.models.calls)


# ---------- Products ----------


def product_doc(
    product_id: str,
    *,
    article_type: str = "Casual Shoes",
    gender: str = "men",
    selling: int = 1999,
    city: str = "Mumbai",
    sizes: dict[str, int] | None = None,
    colour: str = "White",
    attributes: dict[str, dict] | None = None,
    embedding: list[float] | None = None,
    images: bool = True,
    **extra: Any,
) -> dict:
    """A products/{id} document laid out like PLAN.md appendix F."""
    embedding = embedding or near_text()
    group = schemas.group_for(article_type)
    sizes = sizes if sizes is not None else {"UK 8": 2, "UK 9": 3, "UK 10": 0}
    doc = {
        "id": product_id,
        "title": f"Sneaker {product_id}",
        "brand": "Roadster",
        "gender": gender,
        "masterCategory": "Footwear",
        "subCategory": "Shoes",
        "articleType": article_type,
        "baseColour": colour,
        "usage": "Casual",
        "group": group,
        "description": "White low-top sneakers.",
        "attributes": attributes or {"type": {"value": "sneakers", "confidence": 0.95, "note": "low-top"}},
        "catalogue_details": {},
        "needs_review": [],
        "price": {"mrp": selling * 2, "selling": selling, "demo": True},
        "sizes": sizes,
        "in_stock_sizes": [size for size, count in sizes.items() if count > 0],
        "warehouse": {"city": city, "pincode": "400072"},
        "delivery_fee": 0,
        "cod": {"available": True, "fee": 29},
        "returns": {"returnable": True, "window_days": 14, "exchange": True},
        "reviews": [{"rating": 5, "text": "Comfortable and clean looking.", "demo": True, "flagged": False}],
        "rating_avg": 4.2,
        "rating_count": 12,
        "embedding": embedding,
        "demo_data": True,
    }
    if images:
        doc["images"] = {
            "original": f"https://storage.googleapis.com/bucket/original/{product_id}.jpg",
            "gemini": f"https://storage.googleapis.com/bucket/gemini/{product_id}.jpg",
            "thumb": f"https://storage.googleapis.com/bucket/thumb/{product_id}.jpg",
        }
    return doc | extra


def sneakers(count: int = 5) -> list[dict]:
    """White sneakers, closest first."""
    return [product_doc(f"s{n}", embedding=near_text(0.3 + 0.05 * n)) for n in range(1, count + 1)]


# ---------- Wiring ----------


FAST_CHECKS = verify.Settings(attempts=1, base_delay=0.0, max_delay=0.0)


def services(db: FakeDB, checker: Checker | None = None) -> Services:
    return Services(
        db=db,
        embedder=FakeEmbedder(),
        check_cache=verify.MemoryCache(),
        check_client=(checker or Checker()).client,
        check_settings=FAST_CHECKS,
    )


def shopify_fixture(name: str) -> dict:
    return json.loads((SHOPIFY_FIXTURES / name).read_text(encoding="utf-8"))


def search_products() -> list[dict]:
    """Shopify's search answer for "women's cotton kurta with pockets" (trimmed), plus one priced in dollars and
    one without a photo."""
    return shopify_fixture("search_kurtas.json")["result"]["structuredContent"]["products"]


class ShopifyServer:
    """Answers Global Catalog MCP calls from the fixtures, recording each JSON-RPC body it gets.

    get_product answers with the fixture for the G230 kurta (sized when a size is selected), the search's own copy
    for other products, and Shopify's not_found message for unknown ids. `fail` holds HTTP statuses to answer first.
    """

    def __init__(self, products: list[dict] | None = None, fail: list[int] | None = None):
        self.products = products if products is not None else search_products()
        self.fail = list(fail or [])
        self.bodies: list[dict] = []
        self.lock = threading.Lock()

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        with self.lock:
            self.bodies.append(body)
            status = self.fail.pop(0) if self.fail else 200
        if status != 200:
            return httpx.Response(status, text="busy")
        tool, catalog = body["params"]["name"], body["params"]["arguments"]["catalog"]
        if tool == "search_catalog":
            content = {"products": copy.deepcopy(self.products)}
        else:
            content = self.product(catalog)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": content}})

    def product(self, catalog: dict) -> dict:
        if catalog["id"] == "gid://shopify/p/DmptlUPpvXkzRbz0yzmrv":
            name = "get_product_g230_size_m.json" if catalog.get("selected") else "get_product_g230.json"
            return shopify_fixture(name)["result"]["structuredContent"]
        found = next((p for p in self.products if p["id"] == catalog["id"]), None)
        if found is None:
            return {"messages": [{"type": "error", "code": "not_found", "content": f"Product not found: {catalog['id']}"}]}
        return {"product": copy.deepcopy(found)}

    def calls(self, tool: str) -> list[dict]:
        return [body["params"]["arguments"] for body in self.bodies if body["params"]["name"] == tool]

    def catalog(self) -> shopify.Catalog:
        return shopify.Catalog(profile=shopify.EXAMPLE_PROFILE, transport=httpx.MockTransport(self.handle),
                               sleep=lambda seconds: None)


def web_findings() -> WebFindings:
    return WebFindings(
        results=[WebResult(title="Cotton kurta with side pockets", url="https://vertexaisearch.cloud.google.com/r/1",
                           site="myntra.com", price_text="₹899", snippet="cotton, pockets")],
        search_entry_point_html='<style>.chip{color:#1f1f1f}</style><div class="chip">cotton kurta</div>',
        queries=["women cotton kurta with pockets"],
    )


def live(
    server: ShopifyServer | None = None, find: Callable | None = None, read: Callable | None = None
) -> LiveServices:
    return LiveServices(catalog=(server or ShopifyServer()).catalog(), find_on_web=find, read_pages=read)


def backend(
    db: FakeDB,
    *,
    checker: Checker | None = None,
    brief: Callable[[Any], str | Exception] | None = None,
    photo: Callable[[Any], str | Exception] | None = None,
    links: dict[str, tuple[bytes, str]] | None = None,
    live_services: LiveServices | None = None,
) -> Backend:
    def read_link(url: str) -> tuple[bytes, str]:
        from agent.workflow import PhotoProblem

        if links and url in links:
            return links[url]
        raise PhotoProblem("We couldn't open that photo link.")

    return Backend(
        store=Store(db, FakeBucket()),
        services=services(db, checker),
        brief_client=FakeClient(brief or (lambda contents: brief_answer())),
        photo_client=FakeClient(photo or (lambda contents: photo_answer())),
        read_link=read_link,
        now=lambda: TUESDAY_10AM,
        live=live_services,
    )
