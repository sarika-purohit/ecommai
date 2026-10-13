"""Step 1.7: write every product to Firestore, read a sample back to check it, and create the vector indexes.

    python -m data_prep.load_firestore                    # join data/* and write the products collection
    python -m data_prep.load_firestore --check            # read a sample back and compare it with the inputs
    python -m data_prep.load_firestore --create-indexes   # the vector indexes step 2.3 needs; waits until READY

Each product joins data/selected.csv with photos.jsonl, real_details.jsonl, shop.jsonl and embeddings.jsonl,
plus enriched.jsonl and reviews.jsonl when they exist. Without enrichment, a product's details are its real
catalogue labels (source "catalogue", confidence 1.0). Documents are overwritten whole, so loading twice gives
the same result. Products without a photo, an embedding or shop data are skipped and listed.

Besides the fields in PLAN.md appendix F, every document has in_stock_sizes (the sizes with stock above 0), so
search can filter "size in stock" with array-contains. The real description is kept in
catalogue_details["description"], next to the real labels, because `description` is Gemini's once enrichment lands.
"""

import argparse
import os
import random
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from pydantic import ValidationError

from agent.models import Product
from data_prep.embed import (
    Settings,
    load_credentials,
    product_attributes,
    product_description,
    read_id_list,
    read_jsonl_by_id,
    read_optional,
    read_selected,
)
from data_prep.schemas import GROUPS_WITHOUT_COLOUR, colour_family
from data_prep.simulate import ShopData

CATALOGUE_COLUMNS = (
    "id",
    "title",
    "gender",
    "masterCategory",
    "subCategory",
    "articleType",
    "baseColour",
    "usage",
    "group",
)
IMAGE_SIZES = ("original", "gemini", "thumb")
DEFAULT_DATABASE = "(default)"
BATCH_SIZE = 200

FIRESTORE_API = "https://firestore.googleapis.com/v1"
VECTOR_FIELD = "embedding"
ARRAY_FIELDS = {"in_stock_sizes"}
# The pre-filters step 2.3 combines with vector search: category, gender and "size in stock".
INDEX_FILTERS: tuple[tuple[str, ...], ...] = (
    (),
    ("articleType",),
    ("gender",),
    ("articleType", "gender"),
    ("articleType", "in_stock_sizes"),
    ("articleType", "gender", "in_stock_sizes"),
)


# ---------- Joining the inputs into products ----------


@dataclass
class Inputs:
    products: dict[str, dict[str, str]]
    photos: dict[str, dict]
    real: dict[str, dict]
    shop: dict[str, dict]
    embeddings: dict[str, dict]
    enriched: dict[str, dict] = field(default_factory=dict)
    reviews: dict[str, dict] = field(default_factory=dict)


class Incomplete(Exception):
    """The product is missing something it needs; the message says what."""


@dataclass
class Built:
    documents: dict[str, dict] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def catalogue_details(real: dict | None) -> dict[str, str]:
    real = real or {}
    details = {str(name): str(value) for name, value in (real.get("raw_attributes") or {}).items()}
    if description := (real.get("description") or "").strip():
        details["description"] = description
    return details


def embedding_values(product_id: str, inputs: Inputs, dimensions: int) -> list[float]:
    values = (inputs.embeddings.get(product_id) or {}).get("embedding")
    if not values:
        raise Incomplete("no embedding")
    if len(values) != dimensions:
        raise Incomplete(f"embedding has {len(values)} numbers, not {dimensions}")
    return values


def build_product(product_id: str, inputs: Inputs, dimensions: int) -> Product:
    photo = inputs.photos.get(product_id) or {}
    if not all(photo.get(size) for size in IMAGE_SIZES):
        raise Incomplete("no photo")
    embedding = embedding_values(product_id, inputs, dimensions)
    if product_id not in inputs.shop:
        raise Incomplete("no shop data")
    row = inputs.products[product_id]
    real = inputs.real.get(product_id)
    enriched = inputs.enriched.get(product_id)
    reviews = inputs.reviews.get(product_id) or {}
    shop = ShopData.model_validate(inputs.shop[product_id]).model_dump(exclude={"id"})
    return Product.model_validate(
        {
            **{column: row.get(column) or None for column in CATALOGUE_COLUMNS},
            "brand": (real or {}).get("brand") or None,
            "description": product_description(enriched, real),
            "attributes": with_colour(product_attributes(enriched, real), row),
            "catalogue_details": catalogue_details(real),
            "needs_review": (enriched or {}).get("needs_review") or [],
            **shop,
            "reviews": reviews.get("reviews") or [],
            "rating_avg": reviews.get("rating_avg"),
            "rating_count": reviews.get("rating_count") or 0,
            "images": {size: photo[size] for size in IMAGE_SIZES},
            "embedding": embedding,
        }
    )


def with_colour(attributes: dict[str, dict], row: dict) -> dict[str, dict]:
    family = colour_family(row.get("baseColour"))
    if family is None or row.get("group") in GROUPS_WITHOUT_COLOUR or "colour" in attributes:
        return attributes
    note = f"Catalogue colour: {row['baseColour']}"
    return {**attributes, "colour": {"value": family, "confidence": 1.0, "note": note, "source": "catalogue"}}


def firestore_document(product: Product) -> dict:
    document = product.to_firestore()
    document[VECTOR_FIELD] = Vector(product.embedding or [])
    document["in_stock_sizes"] = [size for size, count in product.sizes.items() if count > 0]
    return document


def first_error(error: ValidationError) -> str:
    detail = error.errors()[0]
    return f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"


def build_documents(ids: Iterable[str], inputs: Inputs, dimensions: int) -> Built:
    built = Built()
    for product_id in ids:
        try:
            built.documents[product_id] = firestore_document(build_product(product_id, inputs, dimensions))
        except Incomplete as error:
            built.skipped[product_id] = str(error)
        except ValidationError as error:
            built.skipped[product_id] = f"invalid: {first_error(error)}"
    return built


def describe_built(built: Built, inputs: Inputs) -> str:
    ids = list(built.documents)
    enriched = sum(i in inputs.enriched for i in ids)
    reviewed = sum(bool(built.documents[i].get("reviews")) for i in ids)
    lines = [
        f"{len(ids):,} products ready: {enriched:,} with enrichment, {len(ids) - enriched:,} with catalogue "
        f"labels only; {reviewed:,} with reviews; {len(built.skipped):,} skipped"
    ]
    lines += [f"  skipped {product_id}: {reason}" for product_id, reason in sorted(built.skipped.items())]
    return "\n".join(lines)


# ---------- Writing and checking ----------


def write_documents(client: Any, collection: str, documents: dict[str, dict], batch_size: int = BATCH_SIZE) -> int:
    """Each document replaces whatever was stored under its id, so writing twice gives the same collection."""
    target = client.collection(collection)
    items = list(documents.items())
    written = 0
    for start in range(0, len(items), batch_size):
        batch = client.batch()
        for product_id, document in items[start : start + batch_size]:
            batch.set(target.document(product_id), document)
        batch.commit()
        written += len(items[start : start + batch_size])
        print(f"  wrote {written:,} of {len(items):,}")
    return written


def plain(value: Any) -> Any:
    if isinstance(value, Vector):
        return list(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    return value


def differences(expected: dict, actual: dict) -> list[str]:
    expected, actual = plain(expected), plain(actual)
    return [name for name in sorted(set(expected) | set(actual)) if expected.get(name) != actual.get(name)]


def check_documents(client: Any, collection: str, expected: dict[str, dict], sample: list[str]) -> list[str]:
    target = client.collection(collection)
    snapshots = client.get_all([target.document(product_id) for product_id in sample])
    found = {snapshot.id: snapshot.to_dict() for snapshot in snapshots if snapshot.exists}
    problems = []
    for product_id in sample:
        if product_id not in found:
            problems.append(f"{product_id}: not in {collection}")
        elif fields := differences(expected[product_id], found[product_id]):
            problems.append(f"{product_id}: differs in {', '.join(fields)}")
    return problems


def count_documents(client: Any, collection: str) -> int:
    return int(client.collection(collection).count().get()[0][0].value)


def pick_sample(ids: Iterable[str], size: int, seed: int = 17) -> list[str]:
    ids = sorted(ids)
    return random.Random(seed).sample(ids, min(size, len(ids)))


# ---------- Vector indexes (Firestore Admin REST API) ----------


class FirestoreApiError(Exception):
    pass


class Response(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class Http(Protocol):
    def get(self, url: str, **kwargs: Any) -> Response: ...

    def post(self, url: str, **kwargs: Any) -> Response: ...


def indexes_url(project: str, database: str, collection: str) -> str:
    return f"{FIRESTORE_API}/projects/{project}/databases/{database}/collectionGroups/{collection}/indexes"


def index_body(filters: tuple[str, ...], dimensions: int) -> dict:
    fields: list[dict] = [
        {"fieldPath": name, "arrayConfig": "CONTAINS"}
        if name in ARRAY_FIELDS
        else {"fieldPath": name, "order": "ASCENDING"}
        for name in filters
    ]
    fields.append({"fieldPath": VECTOR_FIELD, "vectorConfig": {"dimension": dimensions, "flat": {}}})
    return {"queryScope": "COLLECTION", "fields": fields}


def field_key(index_field: dict) -> tuple:
    path = index_field["fieldPath"]
    if "vectorConfig" in index_field:
        return (path, "vector", int(index_field["vectorConfig"]["dimension"]))
    if "arrayConfig" in index_field:
        return (path, "array", index_field["arrayConfig"])
    return (path, "order", index_field.get("order"))


def index_key(index: dict) -> tuple:
    """What makes two indexes the same; Firestore adds __name__ to the fields it lists, which is ignored."""
    fields = tuple(field_key(f) for f in index.get("fields", []) if f["fieldPath"] != "__name__")
    return (index.get("queryScope"), fields)


def describe_index(index: dict) -> str:
    parts = []
    for path, kind, setting in index_key(index)[1]:
        parts.append(f"{path} ({setting} numbers)" if kind == "vector" else f"{path} ({setting.lower()})")
    return ", ".join(parts)


def raise_for(response: Response) -> None:
    if response.status_code >= 400:
        raise FirestoreApiError(f"HTTP {response.status_code}: {response.text[:1000]}")


def list_indexes(http: Http, url: str) -> list[dict]:
    """The indexes of the url's collection group. The API answers with every collection group's indexes."""
    prefix = url.removeprefix(FIRESTORE_API + "/") + "/"
    indexes: list[dict] = []
    token = ""
    while True:
        response = http.get(url, params={"pageToken": token} if token else None, timeout=60)
        raise_for(response)
        data = response.json()
        indexes += data.get("indexes", [])
        token = data.get("nextPageToken", "")
        if not token:
            return [index for index in indexes if index.get("name", "").startswith(prefix)]


def create_indexes(http: Http, url: str, wanted: list[dict]) -> list[dict]:
    """Creates the wanted indexes that don't exist yet and returns them. Safe to run again."""
    existing = {index_key(index) for index in list_indexes(http, url)}
    created = []
    for body in wanted:
        if index_key(body) in existing:
            continue
        response = http.post(url, json=body, timeout=60)
        if response.status_code == 409:
            continue
        raise_for(response)
        created.append(body)
    return created


def wait_until_ready(
    http: Http,
    url: str,
    wanted: list[dict],
    poll_seconds: float = 15,
    timeout_seconds: float = 1800,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    deadline = clock() + timeout_seconds
    while True:
        states = {index_key(index): index.get("state", "") for index in list_indexes(http, url)}
        current = {describe_index(body): states.get(index_key(body), "MISSING") for body in wanted}
        if all(state == "READY" for state in current.values()):
            return current
        if "NEEDS_REPAIR" in current.values():
            raise FirestoreApiError(f"an index needs repair: {current}")
        if clock() > deadline:
            raise TimeoutError(f"indexes still not READY after {timeout_seconds:.0f} s: {current}")
        print(f"  waiting: {sum(s == 'READY' for s in current.values())} of {len(current)} READY")
        sleep(poll_seconds)


def ensure_indexes(http: Http, project: str, database: str, collection: str, dimensions: int) -> dict[str, str]:
    url = indexes_url(project, database, collection)
    wanted = [index_body(filters, dimensions) for filters in INDEX_FILTERS]
    for body in create_indexes(http, url, wanted):
        print(f"  creating {collection}: {describe_index(body)}")
    return wait_until_ready(http, url, wanted)


# ---------- Command line ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read a sample back and compare it with the inputs")
    mode.add_argument("--create-indexes", action="store_true", help="create the vector indexes and wait for them")
    mode.add_argument("--dry-run", action="store_true", help="join and validate everything, but write nothing")
    parser.add_argument("--collection", default="products", help="default: products (the trial uses products_trial)")
    parser.add_argument("--database", default=os.environ.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--selected", type=Path, default=Path("data/selected.csv"))
    parser.add_argument("--photos", type=Path, default=Path("data/photos.jsonl"))
    parser.add_argument("--real-details", type=Path, default=Path("data/real_details.jsonl"))
    parser.add_argument("--shop", type=Path, default=Path("data/shop.jsonl"))
    parser.add_argument("--embeddings", type=Path, default=Path("data/embeddings.jsonl"))
    parser.add_argument("--enriched", type=Path, default=Path("data/enriched.jsonl"), help="used when it exists")
    parser.add_argument("--reviews", type=Path, default=Path("data/reviews.jsonl"), help="used when it exists")
    parser.add_argument("--ids-file", type=Path, help="only these ids, one per line (e.g. data/trial_ids.txt)")
    parser.add_argument("--limit", type=int, help="only the first N products")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="writes per commit (at most 500)")
    parser.add_argument("--sample", type=int, default=25, help="documents --check reads back")
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    return args


def read_inputs(args: argparse.Namespace) -> Inputs:
    return Inputs(
        products=read_selected(args.selected),
        photos=read_jsonl_by_id(args.photos),
        real=read_jsonl_by_id(args.real_details),
        shop=read_jsonl_by_id(args.shop),
        embeddings=read_jsonl_by_id(args.embeddings),
        enriched=read_optional(args.enriched, "enrichment"),
        reviews=read_optional(args.reviews, "reviews"),
    )


def run_indexes(args: argparse.Namespace, settings: Settings, http: Http) -> int:
    started = time.perf_counter()
    states = ensure_indexes(http, settings.project, args.database, args.collection, settings.dimensions)
    print(f"vector indexes on {args.collection} ({time.perf_counter() - started:.0f} s):")
    for name, state in states.items():
        print(f"  {state:<6} {name}")
    return 0


def run_check(args: argparse.Namespace, client: Any, built: Built) -> int:
    sample = read_id_list(args.ids_file) if args.ids_file else pick_sample(built.documents, args.sample)
    sample = [i for i in sample if i in built.documents]
    problems = check_documents(client, args.collection, built.documents, sample)
    stored = count_documents(client, args.collection)
    print(f"{args.collection} holds {stored:,} documents; {len(built.documents):,} expected from the inputs")
    print(f"read back {len(sample):,}: {len(sample) - len(problems):,} match the inputs, {len(problems):,} don't")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    settings = Settings.from_env(os.environ)
    if args.create_indexes:
        return run_indexes(args, settings, AuthorizedSession(load_credentials(settings)))

    inputs = read_inputs(args)
    ids = read_id_list(args.ids_file) if args.ids_file else list(inputs.products)
    ids = [i for i in ids if i in inputs.products][: args.limit]
    built = build_documents(ids, inputs, settings.dimensions)
    print(describe_built(built, inputs))
    if args.dry_run:
        return 0

    credentials = load_credentials(settings)
    client = firestore.Client(project=settings.project, database=args.database, credentials=credentials)
    if args.check:
        return run_check(args, client, built)
    started = time.perf_counter()
    written = write_documents(client, args.collection, built.documents, args.batch_size)
    print(f"wrote {written:,} documents to {args.collection} in {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
