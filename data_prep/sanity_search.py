"""Step 1.7: embed a text query and run a Firestore vector search, to sanity-check the loaded products.

    python -m data_prep.sanity_search "green dial watch with steel strap"
    python -m data_prep.sanity_search "cotton kurta with pockets" --article-type Kurtas \\
        --gender women --gender unisex --size M
    python -m data_prep.sanity_search --sanity --collection products_trial

The query is embedded with the same model and size as the products, in the query format Gemini Embedding 2
expects for search ("task: search result | query: ..."). Filters are the step 2.3 hard filters: category
(articleType ==), gender (== for one, `in` for several) and size in stock (in_stock_sizes array-contains).
"""

import argparse
import os
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

from data_prep.embed import GeminiEmbedder, Settings, embed_query, genai_client, load_credentials
from data_prep.load_firestore import DEFAULT_DATABASE, VECTOR_FIELD

DISTANCE_FIELD = "distance"
RESULT_FIELDS = ["title", "articleType", "gender", "baseColour", DISTANCE_FIELD]


@dataclass(frozen=True)
class Search:
    query: str
    article_type: str | None = None
    genders: tuple[str, ...] = ()
    size: str | None = None

    def label(self) -> str:
        filters = []
        if self.article_type:
            filters.append(f"articleType == {self.article_type}")
        if len(self.genders) == 1:
            filters.append(f"gender == {self.genders[0]}")
        elif self.genders:
            filters.append(f"gender in ({', '.join(self.genders)})")
        if self.size:
            filters.append(f"size {self.size} in stock")
        return f'"{self.query}"' + (f"  [{'; '.join(filters)}]" if filters else "  [no filters]")


SANITY_SEARCHES = (
    Search("green dial watch with steel strap"),
    Search("white sneakers with no logo"),
    Search("cotton kurta with pockets"),
    Search("black leather formal shoes for office"),
    Search("gold watch for women"),
    Search("green dial watch with steel strap", article_type="Watches", genders=("women",)),
    Search("cotton kurta with pockets", article_type="Kurtas", genders=("men", "unisex"), size="L"),
)


def vector_query(collection: Any, search: Search, vector: list[float], limit: int, distance: DistanceMeasure) -> Any:
    query = collection
    if search.article_type:
        query = query.where(filter=FieldFilter("articleType", "==", search.article_type))
    if len(search.genders) == 1:
        query = query.where(filter=FieldFilter("gender", "==", search.genders[0]))
    elif search.genders:
        query = query.where(filter=FieldFilter("gender", "in", list(search.genders)))
    if search.size:
        query = query.where(filter=FieldFilter("in_stock_sizes", "array_contains", search.size))
    return query.select(RESULT_FIELDS).find_nearest(
        vector_field=VECTOR_FIELD,
        query_vector=Vector(vector),
        distance_measure=distance,
        limit=limit,
        distance_result_field=DISTANCE_FIELD,
    )


def run_search(
    embedder: GeminiEmbedder, collection: Any, search: Search, limit: int, distance: DistanceMeasure, repeat: int
) -> None:
    started = time.perf_counter()
    vector = embed_query(embedder, search.query).values
    embed_ms = (time.perf_counter() - started) * 1000
    query = vector_query(collection, search, vector, limit, distance)
    timings = []
    for _ in range(repeat):
        started = time.perf_counter()
        results = list(query.stream())
        timings.append((time.perf_counter() - started) * 1000)
    searched = f"{timings[0]:.0f} ms"
    if repeat > 1:
        searched += f" (then median {statistics.median(timings[1:]):.0f} ms over {repeat - 1} more)"
    print(search.label())
    print(f"  embed {embed_ms:.0f} ms, search {searched}, {len(results)} results")
    for rank, doc in enumerate(results, 1):
        data = doc.to_dict()
        print(
            f"  {rank:>2}  {data.get(DISTANCE_FIELD, float('nan')):.4f}  {doc.id:>6}  "
            f"{data.get('articleType', ''):<13} {data.get('gender', ''):<7} {data.get('title', '')}"
        )
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queries", nargs="*", help="text queries")
    parser.add_argument("--sanity", action="store_true", help="run the step 1.7 sanity searches")
    parser.add_argument("--article-type", help="filter: articleType, e.g. Kurtas or Watches")
    parser.add_argument("--gender", action="append", default=[], help="filter: gender (repeat for several)")
    parser.add_argument("--size", help="filter: this size is in stock, e.g. M, 32, 'UK 8' or 'One size'")
    parser.add_argument("--collection", default="products")
    parser.add_argument("--database", default=os.environ.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--distance", choices=[m.name for m in DistanceMeasure], default="COSINE")
    parser.add_argument("--repeat", type=int, default=1, help="run each Firestore query this many times, for timing")
    args = parser.parse_args(argv)
    if not args.queries and not args.sanity:
        parser.error("give a query or --sanity")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    settings = Settings.from_env(os.environ)
    credentials = load_credentials(settings)
    embedder = GeminiEmbedder(genai_client(settings, credentials), settings.model, settings.dimensions)
    collection = firestore.Client(
        project=settings.project, database=args.database, credentials=credentials
    ).collection(args.collection)
    searches = [Search(q, args.article_type, tuple(args.gender), args.size) for q in args.queries]
    if args.sanity:
        searches += SANITY_SEARCHES
    print(f"{args.collection}: {settings.model}, {settings.dimensions} numbers, {args.distance} distance\n")
    for search in searches:
        run_search(embedder, collection, search, args.limit, DistanceMeasure[args.distance], args.repeat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
