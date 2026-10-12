"""Step 1.7: one Gemini Embedding 2 embedding per product, from its gemini/ photo plus a short text, in one call.

The text is the title, product type, gender, colour, the key details (from the enrichment file when it has the
product, otherwise the real catalogue labels) and the start of the description. Each output line is
{id, embedding, text_used, model, tokens}.

Re-running skips products already embedded with the same text, so a stopped run continues where it left off,
and products whose text changed (for example once enrichment lands) are embedded again. The file is
append-only: readers keep the last line for each id.

    python -m data_prep.embed
    python -m data_prep.embed --ids-file data/trial_ids.txt --enriched data/trials/enriched_trial.jsonl \\
        --out data/trials/embeddings_trial.jsonl

How Gemini Embedding 2 is used (Agent Platform docs, "Get multimodal embeddings"):
- It has no task_type setting; the task goes in the text. A photo plus text shouldn't carry a task instruction,
  so products use the plain document format "title: ... | text: ...". Text queries use
  "task: search result | query: ..." (query_text below).
- Parts in one Content give one combined embedding. Below 3,072 numbers the output is already L2-normalised;
  it is normalised again here, so cosine and dot-product distances rank the same.
"""

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Self

import google.auth
import httpx
from dotenv import load_dotenv
from google import genai
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.genai import errors, types

from data_prep.get_photos import start_on_new_line
from data_prep.schemas import UNKNOWN

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# US$ per million input tokens, online requests (Agent Platform pricing, Gemini Embedding 2). Output is free.
TEXT_PRICE_PER_MILLION = 0.20
IMAGE_PRICE_PER_MILLION = 0.45

MIN_CONFIDENCE = 0.6
DESCRIPTION_CHARS = 500
CATALOGUE_NOTE = "from the catalogue details"
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


# ---------- Settings and clients ----------


@dataclass(frozen=True)
class Settings:
    project: str
    quota_project: str
    location: str
    model: str
    dimensions: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        def get(name: str, default: str = "") -> str:
            return (env.get(name) or "").strip() or default

        project = get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise SystemExit("Set GOOGLE_CLOUD_PROJECT in .env (see .env.example).")
        dimensions = get("EMBEDDING_DIMENSIONS", "768")
        if not dimensions.isdigit():
            raise SystemExit(f"EMBEDDING_DIMENSIONS must be a whole number, not {dimensions!r}.")
        return cls(
            project=project,
            quota_project=get("GOOGLE_CLOUD_QUOTA_PROJECT", project),
            location=get("GOOGLE_CLOUD_LOCATION", "global"),
            model=get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
            dimensions=int(dimensions),
        )


def load_credentials(settings: Settings) -> Credentials:
    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE], quota_project_id=settings.quota_project)
    credentials.refresh(Request())
    return credentials


def genai_client(settings: Settings, credentials: Credentials) -> genai.Client:
    return genai.Client(vertexai=True, project=settings.project, location=settings.location, credentials=credentials)


# ---------- Reading the inputs ----------


def read_selected(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return {row["id"].strip(): row for row in csv.DictReader(f) if row["id"].strip()}


def read_jsonl_by_id(path: Path) -> dict[str, dict]:
    """One record per id; a later line for the same id replaces an earlier one. Broken lines are skipped."""
    records: dict[str, dict] = {}
    broken = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                records[str(record["id"])] = record
            except (json.JSONDecodeError, KeyError, TypeError):
                broken += 1
    if broken:
        print(f"  {path}: skipped {broken} unreadable line(s)", file=sys.stderr)
    return records


def read_optional(path: Path | None, what: str) -> dict[str, dict]:
    if path is None or not path.exists():
        print(f"  no {what} file ({path}); going on without it")
        return {}
    return read_jsonl_by_id(path)


def read_id_list(path: Path) -> list[str]:
    ids = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return list(dict.fromkeys(i for i in ids if i))


# ---------- The text for each product ----------


def catalogue_attributes(real: dict | None) -> dict[str, dict]:
    """The real catalogue labels, already mapped to our vocabulary, in the same shape as enriched details."""
    mapped = (real or {}).get("attributes") or {}
    return {
        name: {"value": value, "confidence": 1.0, "note": CATALOGUE_NOTE, "source": "catalogue"}
        for name, value in mapped.items()
    }


def product_attributes(enriched: dict | None, real: dict | None) -> dict[str, dict]:
    if enriched and enriched.get("attributes"):
        return enriched["attributes"]
    return catalogue_attributes(real)


def product_description(enriched: dict | None, real: dict | None) -> str:
    text = (enriched or {}).get("description") or (real or {}).get("description") or ""
    return text.strip()


def value_text(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def detail_phrases(attributes: dict[str, dict], min_confidence: float = MIN_CONFIDENCE) -> list[str]:
    phrases = []
    for name, attribute in attributes.items():
        value = attribute.get("value")
        if value is None or value in ("", UNKNOWN) or attribute.get("confidence", 0) < min_confidence:
            continue
        phrases.append(f"{name.replace('_', ' ')}: {value_text(value)}")
    return phrases


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:") + "..."


def document_text(product: dict[str, str], real: dict | None, enriched: dict | None) -> str:
    kind = f"{product['articleType']} for {product['gender']}"
    if product.get("baseColour"):
        kind += f", colour {product['baseColour']}"
    parts = [kind]
    if details := detail_phrases(product_attributes(enriched, real)):
        parts.append("Details: " + "; ".join(details))
    if description := " ".join(product_description(enriched, real).split()):
        parts.append(shorten(description, DESCRIPTION_CHARS))
    return f"title: {product['title'].strip()} | text: " + ". ".join(parts)


def query_text(query: str) -> str:
    return f"task: search result | query: {' '.join(query.split())}"


# ---------- Calling the model ----------


@dataclass(frozen=True)
class Embedding:
    values: list[float]
    text_tokens: int = 0
    image_tokens: int = 0
    raw_norm: float = 1.0


class Embedder(Protocol):
    def __call__(self, parts: list[types.Part]) -> Embedding: ...


def normalise(values: list[float]) -> tuple[list[float], float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise ValueError("the embedding is all zeros")
    return [v / norm for v in values], norm


def token_counts(statistics: types.ContentEmbeddingStatistics | None) -> tuple[int, int]:
    text = image = 0
    for detail in (statistics.tokens_details if statistics else None) or []:
        if detail.modality == types.MediaModality.IMAGE:
            image += int(detail.token_count or 0)
        else:
            text += int(detail.token_count or 0)
    return text, image


class GeminiEmbedder:
    def __init__(self, client: genai.Client, model: str, dimensions: int):
        self.client = client
        self.model = model
        self.config = types.EmbedContentConfig(output_dimensionality=dimensions)
        self.dimensions = dimensions

    def __call__(self, parts: list[types.Part]) -> Embedding:
        response = self.client.models.embed_content(
            model=self.model, contents=[types.Content(parts=parts)], config=self.config
        )
        embeddings = response.embeddings or []
        if len(embeddings) != 1:
            raise ValueError(f"one call gave {len(embeddings)} embeddings instead of 1")
        values = list(embeddings[0].values or [])
        if len(values) != self.dimensions:
            raise ValueError(f"got {len(values)} numbers instead of {self.dimensions}")
        unit, norm = normalise(values)
        text, image = token_counts(embeddings[0].statistics)
        return Embedding(unit, text, image, norm)


def product_parts(photo: bytes, text: str) -> list[types.Part]:
    return [types.Part.from_bytes(data=photo, mime_type="image/jpeg"), types.Part.from_text(text=text)]


def embed_query(embedder: Embedder, query: str) -> Embedding:
    return embedder([types.Part.from_text(text=query_text(query))])


def is_retryable(error: Exception) -> bool:
    if isinstance(error, errors.APIError):
        return error.code in RETRYABLE_STATUS
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS
    return isinstance(error, (httpx.TransportError, ConnectionError, TimeoutError))


def retrying[T](job: Callable[[], T], attempts: int, delay: float, sleep: Callable[[float], None] = time.sleep) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return job()
        except Exception as error:
            if attempt == attempts or not is_retryable(error):
                raise
            sleep(delay * 2 ** (attempt - 1) * random.uniform(0.75, 1.25))
    raise ValueError("attempts must be at least 1")


# ---------- Running over all products ----------


@dataclass(frozen=True)
class Job:
    id: str
    photo_url: str
    text: str


@dataclass
class Summary:
    planned: int = 0
    done: int = 0
    unchanged: int = 0
    no_photo: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    text_tokens: int = 0
    image_tokens: int = 0
    seconds: float = 0.0
    call_seconds: float = 0.0
    norms: list[float] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return (self.text_tokens * TEXT_PRICE_PER_MILLION + self.image_tokens * IMAGE_PRICE_PER_MILLION) / 1e6


def plan(
    ids: Iterable[str],
    products: dict[str, dict[str, str]],
    photos: dict[str, dict],
    real: dict[str, dict],
    enriched: dict[str, dict],
    embedded_texts: dict[str, str],
) -> tuple[list[Job], Summary]:
    summary = Summary()
    jobs = []
    for product_id in ids:
        photo = photos.get(product_id)
        if not photo or not photo.get("gemini"):
            summary.no_photo.append(product_id)
            continue
        text = document_text(products[product_id], real.get(product_id), enriched.get(product_id))
        if embedded_texts.get(product_id) == text:
            summary.unchanged += 1
            continue
        jobs.append(Job(product_id, photo["gemini"], text))
    summary.planned = len(jobs)
    return jobs, summary


def embed_one(job: Job, embedder: Embedder, fetch: Callable[[str], bytes]) -> tuple[Embedding, float]:
    start = time.perf_counter()
    embedding = embedder(product_parts(fetch(job.photo_url), job.text))
    return embedding, time.perf_counter() - start


def output_row(job: Job, embedding: Embedding, model: str) -> dict:
    return {
        "id": job.id,
        "embedding": embedding.values,
        "text_used": job.text,
        "model": model,
        "tokens": {"text": embedding.text_tokens, "image": embedding.image_tokens},
    }


def run(
    jobs: list[Job],
    summary: Summary,
    embedder: Embedder,
    fetch: Callable[[str], bytes],
    model: str,
    out: Path,
    workers: int = 8,
    attempts: int = 5,
    delay: float = 2.0,
) -> Summary:
    out.parent.mkdir(parents=True, exist_ok=True)
    start_on_new_line(out)
    started = time.perf_counter()

    def task(job: Job) -> tuple[Embedding, float]:
        return retrying(lambda: embed_one(job, embedder, fetch), attempts, delay)

    with out.open("a", encoding="utf-8") as lines, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(task, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                embedding, seconds = future.result()
            except Exception as error:
                summary.failed.append(job.id)
                print(f"  failed {job.id}: {type(error).__name__}: {error}", file=sys.stderr)
                continue
            lines.write(json.dumps(output_row(job, embedding, model)) + "\n")
            lines.flush()
            summary.done += 1
            summary.text_tokens += embedding.text_tokens
            summary.image_tokens += embedding.image_tokens
            summary.call_seconds += seconds
            summary.norms.append(embedding.raw_norm)
            if summary.done % 100 == 0:
                print(f"  {summary.done:,} of {len(jobs):,} embedded")
    summary.seconds = time.perf_counter() - started
    return summary


def report(summary: Summary) -> str:
    lines = [
        f"embedded {summary.done:,} of {summary.planned:,} planned; {summary.unchanged:,} already up to date; "
        f"{len(summary.no_photo):,} without a photo; {len(summary.failed):,} failed",
    ]
    if summary.done:
        per = summary.done
        lines += [
            f"tokens: text {summary.text_tokens:,} ({summary.text_tokens / per:.0f} per product), "
            f"image {summary.image_tokens:,} ({summary.image_tokens / per:.0f} per product)",
            f"cost: US${summary.cost:.4f} (US${summary.cost / per:.6f} per product, "
            f"about US${summary.cost / per * 3000:.2f} per 3,000 products)",
            f"time: {summary.seconds:.1f} s in all, {summary.seconds / per * 1000:.0f} ms per product with "
            f"the workers in parallel, {summary.call_seconds / per * 1000:.0f} ms average per photo fetch plus call",
            f"model norms before normalising: {min(summary.norms):.4f} to {max(summary.norms):.4f}",
        ]
    if summary.no_photo:
        lines.append("without a photo: " + ", ".join(summary.no_photo))
    return "\n".join(lines)


# ---------- Command line ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selected", type=Path, default=Path("data/selected.csv"))
    parser.add_argument("--photos", type=Path, default=Path("data/photos.jsonl"))
    parser.add_argument("--real-details", type=Path, default=Path("data/real_details.jsonl"))
    parser.add_argument(
        "--enriched", type=Path, default=Path("data/enriched.jsonl"), help="used when it exists (step 1.4)"
    )
    parser.add_argument("--out", type=Path, default=Path("data/embeddings.jsonl"))
    parser.add_argument("--failed", type=Path, help="failed ids (default: next to --out, embeddings_failed.txt)")
    parser.add_argument("--ids-file", type=Path, help="only these ids, one per line (e.g. data/trial_ids.txt)")
    parser.add_argument("--limit", type=int, help="only the first N products")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=5, help="tries per product before giving up")
    args = parser.parse_args(argv)
    if args.failed is None:
        args.failed = args.out.with_name(args.out.stem + "_failed.txt")
    return args


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    settings = Settings.from_env(os.environ)
    products = read_selected(args.selected)
    ids = read_id_list(args.ids_file) if args.ids_file else list(products)
    unknown = [i for i in ids if i not in products]
    if unknown:
        print(f"  ignoring {len(unknown)} id(s) not in {args.selected}: {', '.join(unknown[:10])}", file=sys.stderr)
    ids = [i for i in ids if i in products][: args.limit]

    already = {
        product_id: row.get("text_used", "")
        for product_id, row in read_optional(args.out, "earlier embeddings").items()
        if row.get("model") == settings.model and len(row.get("embedding") or []) == settings.dimensions
    }
    jobs, summary = plan(
        ids,
        products,
        read_jsonl_by_id(args.photos),
        read_jsonl_by_id(args.real_details),
        read_optional(args.enriched, "enrichment"),
        already,
    )
    print(f"{settings.model}, {settings.dimensions} numbers, location {settings.location}: {len(jobs):,} to embed")

    client = genai_client(settings, load_credentials(settings))
    embedder = GeminiEmbedder(client, settings.model, settings.dimensions)
    with httpx.Client(timeout=30, follow_redirects=True) as http:

        def fetch(url: str) -> bytes:
            response = http.get(url)
            response.raise_for_status()
            return response.content

        run(jobs, summary, embedder, fetch, settings.model, args.out, args.workers, args.attempts)

    args.failed.parent.mkdir(parents=True, exist_ok=True)
    args.failed.write_text("".join(f"{i}\n" for i in sorted(summary.failed)), encoding="utf-8")
    print(report(summary))
    print(f"embeddings: {args.out}")
    if summary.failed:
        print(f"failed ids: {args.failed} (re-run to retry them)")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
