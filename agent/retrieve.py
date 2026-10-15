"""Step 2, Find: products that could match the checklist.

Hard filters run inside the Firestore vector search: the category, the shopper's gender or unisex, and the
shopper's size in stock (for products that come in sizes). Soft limits run afterwards, in Python: products up to
BUDGET_SLACK_PERCENT over budget (price including delivery) or up to DEADLINE_SLACK_DAYS late are kept and marked,
because "what to give up" (agent/tradeoffs.py) counts what relaxing a limit would give. agent/rank.py never shows
them as matches.

Products come back without their embeddings, which are large. `attach_embeddings` adds them for the few products
that need them (near-duplicates and the style profile in agent/rank.py).
"""

import logging
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from functools import cache
from typing import Any

import httpx
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from agent import delivery
from agent.models import MustHave, NiceToHave, Product, ShoppingBrief
from agent.tradeoffs import BUDGET_RAISE_PERCENT, DEADLINE_EXTRA_DAYS, raised_budget
from data_prep import schemas
from data_prep.embed import Embedder, GeminiEmbedder, Settings, embed_query, genai_client, load_credentials
from data_prep.load_firestore import DEFAULT_DATABASE, VECTOR_FIELD
from data_prep.simulate import ONE_SIZE, size_names

log = logging.getLogger(__name__)

PRODUCTS = "products"
DEFAULT_LIMIT = 40
FETCH_LIMIT = 100
BUDGET_SLACK_PERCENT = BUDGET_RAISE_PERCENT
DEADLINE_SLACK_DAYS = max(DEADLINE_EXTRA_DAYS)
UNISEX = "unisex"
DISTANCE_FIELD = "distance"
PRODUCT_FIELDS = [field.alias or name for name, field in Product.model_fields.items() if name != "embedding"]
IMAGE_TIMEOUT_SECONDS = 10
COD_WANTED = {("present", True), ("absent", False), ("is", "yes"), ("is_not", "no")}
YES_NO = ["yes", "no"]

ImageFetcher = Callable[[str], tuple[bytes, str]]


class Hit(BaseModel):
    product: Product
    distance: float = Field(description="Cosine distance to the request; smaller is closer")
    total_price: int = Field(gt=0, description="Rupees, including delivery and the COD fee when COD is a must-have")
    delivery_date: date | None = None
    over_budget: bool = False
    late: bool = False
    note: str | None = Field(default=None, description="Why there's no delivery date, when there isn't one")


# ---------- Hard filters ----------


def stock_size(category: str, size: str) -> str | None:
    """The shopper's size as written in in_stock_sizes ('M', '32', 'UK 8'), or None when the category has one size."""
    group = _group(category)
    if size_names(group, UNISEX) == [ONE_SIZE]:
        return None
    text = " ".join(size.split())
    if group == "shoes" and (number := re.fullmatch(r"(?i)(?:uk\s*)?(\d+)", text)):
        return f"UK {number[1]}"
    return text.upper()


def hard_filters(brief: ShoppingBrief) -> list[FieldFilter]:
    _group(brief.category)
    filters = [FieldFilter("articleType", "==", brief.category)]
    if brief.gender:
        filters.append(FieldFilter("gender", "in", list(dict.fromkeys([brief.gender, UNISEX]))))
    if brief.size and (size := stock_size(brief.category, brief.size)):
        filters.append(FieldFilter("in_stock_sizes", "array_contains", size))
    return filters


def _group(category: str) -> str:
    group = schemas.group_for(category)
    if group is None:
        raise ValueError(f"unknown category '{category}'")
    return group


# ---------- What we search with ----------


def search_text(brief: ShoppingBrief, query_text: str) -> str:
    """The shopper's words plus the checklist, worded like the product texts ('fabric: cotton')."""
    parts = [" ".join(query_text.split()), f"{brief.category} for {brief.gender}" if brief.gender else brief.category]
    if details := [phrase for m in brief.must_haves if (phrase := must_have_phrase(m))]:
        parts.append("Details: " + "; ".join(details))
    if extras := [nice_to_have_phrase(n) for n in brief.nice_to_haves]:
        parts.append("Preferably: " + "; ".join(extras))
    return ". ".join(part for part in parts if part)


def must_have_phrase(must_have: MustHave) -> str | None:
    """None for must-haves the product embeddings can't reflect: negatives, numbers, shop and review details."""
    if _not_in_embeddings(must_have.attribute):
        return None
    words = must_have.attribute.replace("_", " ")
    if must_have.rule == "is":
        return f"{words}: {_value_text(must_have.value)}"
    if must_have.rule in ("present", "absent"):
        wanted = must_have.value == (must_have.rule == "present")
        if _is_yes_no(must_have.attribute):
            return f"{words}: {'yes' if wanted else 'no'}"
        return f"with {words}" if wanted else f"{words}: none"
    return None


def nice_to_have_phrase(nice: NiceToHave) -> str:
    return f"{nice.attribute.replace('_', ' ')}: {_value_text(nice.value)}"


def _not_in_embeddings(attribute: str) -> bool:
    return attribute in schemas.SHOP_ATTRIBUTES or attribute in schemas.REVIEW_ATTRIBUTES


def _is_yes_no(attribute: str) -> bool:
    return any(values.get(attribute) == YES_NO for values in schemas.VOCABULARY.values())


def _value_text(value: str | bool | float) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def fetch_image(url: str) -> tuple[bytes, str]:
    """Photo bytes and their type. gs:// links are read through the public Cloud Storage address."""
    if url.startswith("gs://"):
        url = "https://storage.googleapis.com/" + url.removeprefix("gs://")
    response = httpx.get(url, timeout=IMAGE_TIMEOUT_SECONDS, follow_redirects=True)
    response.raise_for_status()
    mime_type = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    return response.content, mime_type


def image_vector(embedder: Embedder, url: str, fetch: ImageFetcher = fetch_image) -> list[float]:
    data, mime_type = fetch(url)
    return embedder([types.Part.from_bytes(data=data, mime_type=mime_type)]).values


# ---------- Soft limits, total price and delivery ----------


def wants_cod(brief: ShoppingBrief) -> bool:
    return any(m.attribute == "cod" and (m.rule, _plain(m.value)) in COD_WANTED for m in brief.must_haves)


def order_total(brief: ShoppingBrief, product: Product) -> int:
    if product.price is None:
        raise ValueError("no price")
    cod_fee = product.cod.fee if product.cod and product.cod.available else 0
    return delivery.total_price(product.price.selling, product.delivery_fee, cod_fee, wants_cod(brief))


def delivery_date(
    brief: ShoppingBrief, product: Product, now: datetime | None = None
) -> tuple[date | None, str | None]:
    """The promised delivery date, or None with the reason when it can't be worked out."""
    if not brief.pincode:
        return None, None
    if product.warehouse is None:
        return None, "This product has no warehouse, so we can't promise a delivery date."
    try:
        return delivery.estimate(product.warehouse.city, brief.pincode, now).delivery_date, None
    except delivery.UnknownPincode as error:
        return None, str(error)
    except ValueError as error:
        log.warning("product %s: no delivery date: %s", product.id, error)
        return None, "We couldn't work out a delivery date for this product."


def make_hit(brief: ShoppingBrief, product: Product, distance: float, now: datetime | None = None) -> Hit | None:
    """The product as a hit with its total and delivery date; None beyond the soft limits or below the rating asked for."""
    total = order_total(brief, product)
    arrives, note = delivery_date(brief, product, now)
    budget, deadline = brief.budget_max_inr, brief.deadline_date
    over_budget = budget is not None and total > budget
    late = deadline is not None and arrives is not None and arrives > deadline
    if over_budget and total > raised_budget(budget):
        return None
    if late and (arrives - deadline).days > DEADLINE_SLACK_DAYS:
        return None
    if brief.min_rating is not None and (product.rating_avg or 0) < brief.min_rating:
        return None
    return Hit(
        product=product,
        distance=distance,
        total_price=total,
        delivery_date=arrives,
        over_budget=over_budget,
        late=late,
        note=note,
    )


def _plain(value: str | bool | float) -> str | bool | float:
    return value.strip().casefold() if isinstance(value, str) else value


# ---------- Searching ----------


def nearest(
    collection: Any, filters: list[FieldFilter], vector: list[float], fetch_limit: int = FETCH_LIMIT
) -> list[tuple[Product, float]]:
    """The closest products that pass the hard filters, without embeddings. Unreadable documents are skipped."""
    query = collection
    for field_filter in filters:
        query = query.where(filter=field_filter)
    query = query.select([*PRODUCT_FIELDS, DISTANCE_FIELD]).find_nearest(
        vector_field=VECTOR_FIELD,
        query_vector=Vector(vector),
        distance_measure=DistanceMeasure.COSINE,
        limit=fetch_limit,
        distance_result_field=DISTANCE_FIELD,
    )
    found = []
    for doc in query.stream():
        data = doc.to_dict()
        try:
            found.append((Product.model_validate({**data, "id": doc.id}), float(data[DISTANCE_FIELD])))
        except (ValidationError, KeyError, TypeError, ValueError) as error:
            log.warning("product %s: skipped, unreadable: %s", doc.id, error)
    return found


def merge(*results: list[tuple[Product, float]]) -> dict[str, tuple[Product, float]]:
    """One entry per product, at its smallest distance across the searches."""
    best: dict[str, tuple[Product, float]] = {}
    for product, distance in (item for result in results for item in result):
        if product.id not in best or distance < best[product.id][1]:
            best[product.id] = (product, distance)
    return best


def find_candidates(
    brief: ShoppingBrief,
    *,
    query_text: str,
    reference_image_url: str | None = None,
    limit: int = DEFAULT_LIMIT,
    db: Any = None,
    embedder: Embedder | None = None,
    collection: str = PRODUCTS,
    fetch_limit: int = FETCH_LIMIT,
    now: datetime | None = None,
    fetch: ImageFetcher = fetch_image,
    timings: dict[str, float] | None = None,
) -> list[Hit]:
    """The `limit` closest products that pass the hard filters and the soft limits, closest first (ties: cheaper).

    With a reference photo, a second search uses the photo's embedding and each product keeps its best distance.
    If the photo can't be read, the search goes on with the words alone. `timings`, when given, is filled with
    milliseconds per stage.
    """
    started = time.perf_counter()
    filters = hard_filters(brief)
    target = (db or default_db()).collection(collection)
    embedder = embedder or default_embedder()
    stages = timings if timings is not None else {}

    def run(name: str, vector_of: Callable[[], list[float]]) -> list[tuple[Product, float]]:
        clock = time.perf_counter()
        vector = vector_of()
        stages[f"{name}_embed_ms"] = _ms_since(clock)
        clock = time.perf_counter()
        found = nearest(target, filters, vector, max(fetch_limit, limit))
        stages[f"{name}_search_ms"] = _ms_since(clock)
        return found

    searches: dict[str, Callable[[], list[float]]] = {
        "text": lambda: embed_query(embedder, search_text(brief, query_text)).values
    }
    if reference_image_url:
        searches["image"] = lambda: image_vector(embedder, reference_image_url, fetch)
    with ThreadPoolExecutor(max_workers=len(searches)) as pool:
        futures = {name: pool.submit(run, name, vector_of) for name, vector_of in searches.items()}
        text_results = futures["text"].result()
        image_results = _photo_results(futures.get("image"), reference_image_url)

    clock = time.perf_counter()
    hits = _within_limits(brief, merge(text_results, image_results), now)
    hits.sort(key=lambda hit: (hit.distance, hit.total_price, hit.product.id))
    stages["limits_ms"] = _ms_since(clock)
    stages["total_ms"] = _ms_since(started)
    log.info(
        "find_candidates %s: %d found, %d within limits, %d returned, %s",
        brief.category,
        len(text_results) + len(image_results),
        len(hits),
        min(limit, len(hits)),
        {name: round(ms) for name, ms in stages.items()},
    )
    return hits[:limit]


def _photo_results(future: Any, url: str | None) -> list[tuple[Product, float]]:
    if future is None:
        return []
    try:
        return future.result()
    except Exception as error:
        log.warning("photo search skipped for %s: %s: %s", url, type(error).__name__, error)
        return []


def _within_limits(brief: ShoppingBrief, found: dict[str, tuple[Product, float]], now: datetime | None) -> list[Hit]:
    hits = []
    for product, distance in found.values():
        try:
            hit = make_hit(brief, product, distance, now)
        except ValueError as error:
            log.warning("product %s: skipped, bad shop data: %s", product.id, error)
            continue
        if hit is not None:
            hits.append(hit)
    return hits


def attach_embeddings(hits: list[Hit], *, db: Any = None, collection: str = PRODUCTS) -> list[Hit]:
    """The same hits with each product's embedding filled in, read in one batch."""
    client = db or default_db()
    target = client.collection(collection)
    snapshots = client.get_all([target.document(hit.product.id) for hit in hits], field_paths=[VECTOR_FIELD])
    vectors = {s.id: list((s.to_dict() or {}).get(VECTOR_FIELD) or []) for s in snapshots if s.exists}
    return [
        hit.model_copy(update={"product": hit.product.model_copy(update={"embedding": vectors.get(hit.product.id)})})
        for hit in hits
    ]


def _ms_since(started: float) -> float:
    return (time.perf_counter() - started) * 1000


# ---------- Default clients ----------


@cache
def _settings_and_credentials() -> tuple[Settings, Any]:
    settings = Settings.from_env(os.environ)
    return settings, load_credentials(settings)


@cache
def default_db() -> firestore.Client:
    settings, credentials = _settings_and_credentials()
    database = os.environ.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE
    return firestore.Client(project=settings.project, database=database, credentials=credentials)


@cache
def default_embedder() -> GeminiEmbedder:
    settings, credentials = _settings_and_credentials()
    return GeminiEmbedder(genai_client(settings, credentials), settings.model, settings.dimensions)
