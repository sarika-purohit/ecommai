"""Complete the look: products from our catalogue that go with one product, and why.

1. The product's group decides the slots that complete an outfit (SLOTS_FOR): a shirt needs a bottom, shoes, a bag
   and a watch; shoes need a bottom, a top and a bag. A slot only offers the product types the catalogue has for
   the shopper's gender.
2. One Gemini call looks at the product's photo and plans up to MAX_LOOKS looks for different occasions: for each
   slot, a product type and a short description to search for ("tan leather loafers"). Things we don't sell
   (belts, dupattas, jewellery) come back as styling ideas, shown as ideas and never as products.
3. Each description is a vector search in that product type (agent/retrieve.find_candidates). Hard limits come
   first: the product's gender or unisex, in stock, and within what the budget leaves after the product itself.
4. One Gemini call per slot, all at once, sees the product and that slot's candidates and scores each pairing from
   0 to 1 on colour, style, silhouette and how well it suits each look's occasion, with a short reason. Code scores
   the rest: preferences (closeness to the shopper's saved likes for that product type) and budget.
5. Match = the weighted average of the scores we have (WEIGHTS, configurable with LOOK_WEIGHTS). A score we don't
   have (no saved likes, no budget, Gemini busy for that slot) is left out, never guessed. Each look takes the best
   match per slot; when it's over budget, slots are left out from the least important up. A look's total is shown
   only when every price is known.

Gemini's scores and reasons are styling suggestions, not facts, and the app says so. Catalogue products are sample
data. Plans and Gemini's scores (never the shopper's own preference scores) are saved in the Firestore `looks`
collection for LOOK_TTL, so opening the same product again is instant.
"""

import hashlib
import logging
import os
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, get_args

import httpx
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

from agent import retrieve, verify
from agent.browse import CatalogItem
from agent.learn import cosine
from agent.models import Gender, Product, ShoppingBrief
from data_prep.load_firestore import VECTOR_FIELD

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
VERSION = "v1"
COLLECTION = "looks"
LOOK_TTL = timedelta(hours=24)
MAX_LOOKS = 3
PER_PIECE = 3
CHOICES = 4
ROW_SIZE = 8
FETCH_LIMIT = 30
MAX_IDEAS = 3
MAX_TEXT = 160
MAX_OUTPUT_TOKENS = 4096
UNISEX = "unisex"

Occasion = Literal["office", "casual", "party", "festive", "sports"]
Style = Literal["minimal", "classic", "bold", "street"]
Slot = Literal["top", "bottom", "shoes", "bag", "watch", "sunglasses"]
RowKind = Literal["pair", "shoes", "accessories"]
OCCASIONS: tuple[str, ...] = get_args(Occasion)

SLOT_TYPES: dict[str, tuple[str, ...]] = {
    "top": ("Shirts", "Tshirts", "Tops", "Kurtas", "Kurtis", "Tunics"),
    "bottom": ("Jeans", "Trousers"),
    "shoes": ("Casual Shoes", "Formal Shoes", "Sports Shoes"),
    "bag": ("Handbags", "Clutches", "Backpacks"),
    "watch": ("Watches",),
    "sunglasses": ("Sunglasses",),
}
# Most important first: an over-budget look leaves slots out from the end.
SLOTS_FOR: dict[str, tuple[str, ...]] = {
    "tops": ("bottom", "shoes", "bag", "watch"),
    "bottoms": ("top", "shoes", "bag", "watch"),
    "shoes": ("bottom", "top", "bag"),
    "watches": ("top", "bottom", "shoes"),
    "bags": ("top", "bottom", "shoes"),
    "sunglasses": ("top", "bottom", "shoes"),
}
ROWS: dict[str, tuple[str, ...]] = {"pair": ("top", "bottom"), "shoes": ("shoes",), "accessories": ("bag", "watch", "sunglasses")}
WEIGHTS: dict[str, float] = {
    "colour": 25.0, "style": 25.0, "occasion": 20.0, "silhouette": 15.0, "preferences": 10.0, "budget": 5.0,
}
GEMINI_PARTS = ("colour", "style", "occasion", "silhouette")

OVER_BUDGET = "This product alone uses up the budget, so there's nothing left for the rest of the look."
UNSCORED = "Gemini couldn't score some pairings just now, so they're shown without a match score."
NOTHING = "We couldn't find products in our catalogue that go with this one."


class LookFailed(Exception):
    """Gemini couldn't plan the looks; the caller shows a busy message."""


# ---------- Gemini's answers ----------


class PlanPiece(BaseModel):
    slot: str
    article_type: str
    description: str = Field(description="What to search for, under 12 words, e.g. 'tan leather loafers'")


class PlanLook(BaseModel):
    occasion: str
    title: str = Field(description="Under 6 words, e.g. 'Smart office look'")
    summary: str = Field(description="One sentence on why the pieces work with the product")
    pieces: list[PlanPiece]
    ideas: list[str] = Field(description="Up to 3 finishing touches from outside the slots, e.g. 'a slim tan belt'")


class PlanAnswer(BaseModel):
    looks: list[PlanLook]


class OccasionFit(BaseModel):
    occasion: str
    score: float


class FitAnswer(BaseModel):
    number: int
    colour: float
    style: float
    silhouette: float
    occasions: list[OccasionFit]
    reason: str = Field(description="Under 20 words: why it pairs well or doesn't")


class JudgeAnswer(BaseModel):
    fits: list[FitAnswer]


# ---------- What we save and what we answer ----------


class Scores(BaseModel):
    colour: float
    style: float
    silhouette: float
    occasions: dict[str, float] = Field(default_factory=dict)
    reason: str = ""


class Candidate(BaseModel):
    slot: Slot
    item: CatalogItem
    distance: float
    scores: Scores | None = Field(default=None, description="None: Gemini couldn't score this pairing")


class PlannedLook(BaseModel):
    occasion: str
    title: str
    summary: str
    slots: list[Slot]
    ideas: list[str] = Field(default_factory=list)


class Pool(BaseModel):
    looks: list[PlannedLook]
    candidates: list[Candidate]


class Part(BaseModel):
    name: str
    weight: float
    score: float | None = Field(description="0-1; None when we don't know it, and then it's left out of the match")


class Suggestion(BaseModel):
    slot: Slot
    product: CatalogItem
    match: int | None = Field(description="0-100, the weighted average of the known parts; None when unscored")
    parts: list[Part]
    reason: str = Field(description="Gemini's styling note: a suggestion, not a fact")


class LookPiece(BaseModel):
    slot: Slot
    choices: list[Suggestion] = Field(description="Best first; the others are swaps")


class Look(BaseModel):
    occasion: str
    title: str
    summary: str
    pieces: list[LookPiece]
    ideas: list[str] = Field(description="Styling ideas we don't sell: never products")
    total_price: int | None = Field(description="The product plus the picks, before delivery; None if a price is missing")
    left_out: list[Slot] = Field(default_factory=list, description="Slots left out to stay within the budget")


class Row(BaseModel):
    kind: RowKind
    items: list[Suggestion]


class Looks(BaseModel):
    product_id: str
    looks: list[Look]
    rows: list[Row]
    weights: dict[str, float]
    budget_max_inr: int | None = None
    sample_data: bool = True
    demo_prices: bool = False
    note: str | None = None


# ---------- Settings ----------


def weights_from(text: str | None) -> dict[str, float]:
    """WEIGHTS with the overrides in `text` ('colour=30,budget=0'); unknown names and bad numbers are ignored."""
    weights = dict(WEIGHTS)
    for item in (text or "").split(","):
        name, _, value = item.partition("=")
        name = name.strip().lower()
        try:
            number = float(value)
        except ValueError:
            continue
        if name in weights and number >= 0:
            weights[name] = number
    return weights


def default_weights() -> dict[str, float]:
    return weights_from(os.environ.get("LOOK_WEIGHTS"))


def slot_types(anchor: Product, available: Collection[str] | None) -> dict[str, list[str]]:
    """The slots that complete an outfit for this product, each with the product types we can offer."""
    slots = {}
    for slot in SLOTS_FOR.get(anchor.group, ()):
        offered = [t for t in SLOT_TYPES[slot] if t != anchor.article_type and (available is None or t in available)]
        if offered:
            slots[slot] = offered
    return slots


# ---------- Asking Gemini ----------


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _config(system: str, schema: type[BaseModel], settings: verify.Settings) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(settings.thinking_level.upper())),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def photo(url: str, photo_input: str = "uri", fetch: Callable[[str], bytes] = verify.download) -> types.Part:
    if photo_input == "bytes":
        return types.Part.from_bytes(data=fetch(url), mime_type="image/jpeg")
    return types.Part.from_uri(file_uri=verify.gcs_uri(url), mime_type="image/jpeg")


def ask(
    client: verify.Client,
    contents: Callable[[str], list],
    system: str,
    schema: type[BaseModel],
    settings: verify.Settings,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """One Gemini call with retries, like verify.check_product; photos switch to bytes when a link can't be read."""
    photo_input = "uri"
    problem = "no answer"
    for attempt in range(1, settings.attempts + 1):
        model = settings.model_for(attempt)
        try:
            response = client.models.generate_content(
                model=model, contents=contents(photo_input), config=_config(system, schema, settings)
            )
            return schema.model_validate_json(response.text or "")
        except ValidationError as error:
            problem = f"bad answer: {error.error_count()} problems"
        except errors.APIError as error:
            problem = f"HTTP {error.code}: {' '.join(str(error.message).split())[:MAX_TEXT]}"
            if photo_input == "uri" and error.code in verify.PHOTO_LINK_CODES:
                photo_input = "bytes"
                continue
            if error.code not in verify.RETRY_CODES:
                break
        except (httpx.HTTPError, OSError) as error:
            problem = f"{type(error).__name__}: {error}"
        if attempt < settings.attempts:
            sleep(verify.backoff(attempt, settings))
    raise LookFailed(problem)


def _short(text: object, limit: int = MAX_TEXT) -> str:
    text = " ".join(verify.clean(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def product_block(anchor: Product) -> str:
    details = "; ".join(
        f"{name.replace('_', ' ')}: {verify.value_text(attribute.value)}" for name, attribute in sorted(anchor.attributes.items())
    )
    lines = [
        f"Type: {anchor.article_type} for {anchor.gender}",
        f"Title: {_short(anchor.title)}",
        f"Brand: {_short(anchor.brand or 'unknown')}",
        f"Colour: {anchor.base_colour or 'unknown'}",
        f"Usage: {anchor.usage or 'unknown'}",
    ]
    if details:
        lines.append(f"Details read from the photo: {_short(details, 600)}")
    return "=== PRODUCT\n" + "\n".join(lines) + "\n=== END PRODUCT"


def plan_prompt(anchor: Product, slots: Mapping[str, Sequence[str]], occasion: str | None, style: str | None) -> str:
    lines = [product_block(anchor), "", "=== SLOTS (each with the product types you may use)"]
    lines += [f"- {slot}: {', '.join(offered)}" for slot, offered in slots.items()]
    lines += ["=== END SLOTS", "", f"Occasions: {', '.join(OCCASIONS)}."]
    if occasion:
        lines.append(f"Make 1 look, for {occasion}.")
    else:
        lines.append(f"Make up to {MAX_LOOKS} looks, each for a different occasion that suits this product.")
    if style:
        lines.append(f"The shopper likes a {style} style.")
    return "\n".join(lines)


def plan_looks(
    client: verify.Client,
    anchor: Product,
    slots: Mapping[str, Sequence[str]],
    settings: verify.Settings,
    *,
    occasion: str | None = None,
    style: str | None = None,
    fetch: Callable[[str], bytes] = verify.download,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[PlannedLook, list[PlanPiece]]]:
    """Gemini's looks, kept only where they use the slots and types offered. Raises LookFailed."""
    if anchor.images is None:
        raise LookFailed("the product has no photo")
    text = plan_prompt(anchor, slots, occasion, style)
    answer = ask(
        client, lambda photo_input: [photo(anchor.images.gemini, photo_input, fetch), text], _prompt("outfit_plan.md"),
        PlanAnswer, settings, sleep,
    )
    looks: list[tuple[PlannedLook, list[PlanPiece]]] = []
    for look in answer.looks:
        wanted = occasion or look.occasion.strip().lower()
        if wanted not in OCCASIONS or any(planned.occasion == wanted for planned, _ in looks):
            continue
        pieces: dict[str, PlanPiece] = {}
        for piece in look.pieces:
            if piece.slot in slots and piece.article_type in slots[piece.slot] and piece.slot not in pieces:
                pieces[piece.slot] = piece.model_copy(update={"description": _short(piece.description, 120)})
        if not pieces:
            continue
        ordered = [slot for slot in slots if slot in pieces]
        ideas = [_short(idea, 80) for idea in look.ideas if idea.strip()][:MAX_IDEAS]
        planned = PlannedLook(occasion=wanted, title=_short(look.title, 60), summary=_short(look.summary), slots=ordered,
                              ideas=ideas)
        looks.append((planned, [pieces[slot] for slot in ordered]))
        if len(looks) == (1 if occasion else MAX_LOOKS):
            break
    if not looks:
        raise LookFailed("no usable look in the answer")
    return looks


def judge_prompt(anchor: Product, slot: str, candidates: Sequence[Candidate], occasions: Sequence[str]) -> str:
    lines = [product_block(anchor), "", f"=== CANDIDATES for the {slot}, numbered like the photos"]
    lines += [
        f"{n}: {c.item.article_type}, {c.item.base_colour or 'colour unknown'}: {_short(c.item.title, 100)}"
        for n, c in enumerate(candidates, 1)
    ]
    lines += ["=== END CANDIDATES", "", f"Occasions to score: {', '.join(occasions)}."]
    return "\n".join(lines)


def judge_slot(
    client: verify.Client,
    anchor: Product,
    slot: str,
    candidates: Sequence[Candidate],
    occasions: Sequence[str],
    settings: verify.Settings,
    *,
    fetch: Callable[[str], bytes] = verify.download,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Scores]:
    """Gemini's scores per product id; a candidate Gemini skipped stays unscored. Raises LookFailed."""
    text = judge_prompt(anchor, slot, candidates, occasions)

    def contents(photo_input: str) -> list:
        parts: list = [photo(anchor.images.gemini, photo_input, fetch)]
        for n, candidate in enumerate(candidates, 1):
            parts += [f"Photo {n}:", photo(candidate.item.photo, photo_input, fetch)]
        return [*parts, text]

    answer = ask(client, contents, _prompt("outfit_judge.md"), JudgeAnswer, settings, sleep)
    scores: dict[str, Scores] = {}
    for fit in answer.fits:
        if not 1 <= fit.number <= len(candidates):
            continue
        fits = {o.occasion.strip().lower(): _unit(o.score) for o in fit.occasions}
        scores[candidates[fit.number - 1].item.id] = Scores(
            colour=_unit(fit.colour), style=_unit(fit.style), silhouette=_unit(fit.silhouette),
            occasions={occasion: fits[occasion] for occasion in occasions if occasion in fits}, reason=_short(fit.reason),
        )
    return scores


def _unit(value: float) -> float:
    return round(min(1.0, max(0.0, float(value))), 2)


# ---------- Finding the pieces ----------


def in_stock(product: Product) -> bool:
    return not product.sizes or any(count > 0 for count in product.sizes.values())


def find_pieces(
    planned: Sequence[tuple[PlannedLook, list[PlanPiece]]],
    anchor: Product,
    *,
    gender: Gender | None,
    room: int | None,
    db: Any,
    embedder: Any,
) -> list[Candidate]:
    """Every look's searches at once; one candidate per product, at its closest distance."""
    searches = [(piece.slot, piece) for _, pieces in planned for piece in pieces]

    def search(piece: PlanPiece) -> list[retrieve.Hit]:
        brief = ShoppingBrief(category=piece.article_type, gender=gender, budget_max_inr=room)
        return retrieve.find_candidates(
            brief, query_text=piece.description, limit=PER_PIECE * 2, db=db, embedder=embedder, fetch_limit=FETCH_LIMIT
        )

    with ThreadPoolExecutor(max_workers=max(1, len(searches))) as pool:
        found = list(pool.map(lambda item: (item[0], search(item[1])), searches))
    best: dict[str, Candidate] = {}
    for slot, hits in found:
        kept = [
            hit for hit in hits
            if hit.product.id != anchor.id and not hit.over_budget and in_stock(hit.product) and hit.product.images
            and (room is None or (hit.product.price and hit.product.price.selling <= room))
        ][:PER_PIECE]
        for hit in kept:
            if hit.product.id not in best or hit.distance < best[hit.product.id].distance:
                best[hit.product.id] = Candidate(slot=slot, item=catalog_item(hit.product), distance=hit.distance)
    return sorted(best.values(), key=lambda c: (c.distance, c.item.id))


def catalog_item(product: Product) -> CatalogItem:
    images = product.images
    return CatalogItem(
        id=product.id, title=product.title, brand=product.brand, gender=product.gender, article_type=product.article_type,
        base_colour=product.base_colour, usage=product.usage, price=product.price,
        thumb=images.thumb if images else None, photo=images.gemini if images else None,
        rating_avg=product.rating_avg, rating_count=product.rating_count,
    )


def build_pool(
    anchor: Product,
    slots: Mapping[str, Sequence[str]],
    *,
    client: verify.Client,
    settings: verify.Settings,
    db: Any,
    embedder: Any,
    gender: Gender | None,
    room: int | None,
    occasion: str | None,
    style: str | None,
    fetch: Callable[[str], bytes] = verify.download,
    sleep: Callable[[float], None] = time.sleep,
) -> Pool:
    planned = plan_looks(client, anchor, slots, settings, occasion=occasion, style=style, fetch=fetch, sleep=sleep)
    candidates = find_pieces(planned, anchor, gender=gender, room=room, db=db, embedder=embedder)
    occasions = [look.occasion for look, _ in planned]
    by_slot = {slot: [c for c in candidates if c.slot == slot] for slot in slots}

    def judge(slot: str) -> dict[str, Scores]:
        try:
            return judge_slot(client, anchor, slot, by_slot[slot], occasions, settings, fetch=fetch, sleep=sleep)
        except LookFailed as error:
            log.warning("look for %s: %s left unscored: %s", anchor.id, slot, error)
            return {}

    asked = [slot for slot, found in by_slot.items() if found]
    with ThreadPoolExecutor(max_workers=max(1, len(asked))) as pool:
        scores = {slot: result for slot, result in zip(asked, pool.map(judge, asked))}
    scored = [c.model_copy(update={"scores": scores.get(c.slot, {}).get(c.item.id)}) for c in candidates]
    return Pool(looks=[look for look, _ in planned], candidates=scored)


# ---------- Scoring and putting the looks together ----------


def preference(candidate: Candidate, taste: Mapping[str, list[float]], embeddings: Mapping[str, list[float]]) -> float | None:
    """How close the product is to the shopper's saved likes for its type; None without any."""
    liked, vector = taste.get(candidate.item.article_type), embeddings.get(candidate.item.id)
    if not liked or not vector or len(liked) != len(vector):
        return None
    return _unit(cosine(liked, vector))


def budget_score(price: int | None, room: int | None, slots: int) -> float | None:
    """1 within a fair share of what the budget leaves (split across the slots), falling to 0 at all of it."""
    if room is None or price is None or slots <= 0:
        return None
    fair = room / slots
    if price <= fair:
        return 1.0
    return _unit((room - price) / (room - fair)) if room > fair else 0.0


def suggestion(
    candidate: Candidate, occasion: str, weights: Mapping[str, float], *, preference_score: float | None,
    budget: float | None,
) -> Suggestion:
    scores = candidate.scores
    known = {
        "colour": scores.colour if scores else None,
        "style": scores.style if scores else None,
        "occasion": scores.occasions.get(occasion) if scores else None,
        "silhouette": scores.silhouette if scores else None,
        "preferences": preference_score,
        "budget": budget,
    }
    parts = [Part(name=name, weight=weights[name], score=known[name]) for name in WEIGHTS]
    used = [part for part in parts if part.score is not None and part.weight > 0]
    match = None
    if any(part.name in GEMINI_PARTS for part in used):
        match = round(100 * sum(part.weight * part.score for part in used) / sum(part.weight for part in used))
    return Suggestion(slot=candidate.slot, product=candidate.item, match=match, parts=parts,
                      reason=scores.reason if scores else "")


def _rank(suggestions: list[Suggestion], distance: Mapping[str, float]) -> list[Suggestion]:
    return sorted(suggestions, key=lambda s: (s.match is None, -(s.match or 0), distance[s.product.id], s.product.id))


def _gender_ok(item: CatalogItem, gender: str | None) -> bool:
    return gender is None or item.gender in (gender, UNISEX)


def _price(item: CatalogItem) -> int | None:
    return item.price.selling if item.price else None


def look_total(anchor: Product, pieces: Sequence[LookPiece]) -> int | None:
    prices = [anchor.price.selling if anchor.price else None, *(_price(piece.choices[0].product) for piece in pieces)]
    return None if any(price is None for price in prices) else sum(prices)


def assemble(
    anchor: Product,
    pool: Pool,
    *,
    gender: str | None,
    budget: int | None,
    weights: Mapping[str, float],
    taste: Mapping[str, list[float]],
    embeddings: Mapping[str, list[float]],
) -> tuple[list[Look], list[Row]]:
    anchor_price = anchor.price.selling if anchor.price else None
    room = budget - anchor_price if budget is not None and anchor_price is not None else None
    distance = {c.item.id: c.distance for c in pool.candidates}
    liked = {c.item.id: preference(c, taste, embeddings) for c in pool.candidates}

    def scored(candidates: list[Candidate], occasion: str, slots: int) -> list[Suggestion]:
        return _rank([
            suggestion(c, occasion, weights, preference_score=liked[c.item.id], budget=budget_score(_price(c.item), room, slots))
            for c in candidates
        ], distance)

    looks = []
    for planned in pool.looks:
        look_gender = gender
        pieces: list[LookPiece] = []
        for slot in planned.slots:
            options = [c for c in pool.candidates if c.slot == slot and _gender_ok(c.item, look_gender)]
            choices = scored(options, planned.occasion, len(planned.slots))[:CHOICES]
            if look_gender is None and choices and choices[0].product.gender != UNISEX:
                # A unisex product with no gender asked: the first pick decides it, so the look stays together.
                look_gender = choices[0].product.gender
                choices = [c for c in choices if _gender_ok(c.product, look_gender)]
            if choices:
                pieces.append(LookPiece(slot=slot, choices=choices))
        left_out: list[Slot] = []
        while budget is not None and len(pieces) > 1 and (total := look_total(anchor, pieces)) is not None and total > budget:
            left_out.insert(0, pieces.pop().slot)
        if pieces:
            looks.append(Look(
                occasion=planned.occasion, title=planned.title, summary=planned.summary, pieces=pieces, ideas=planned.ideas,
                total_price=look_total(anchor, pieces), left_out=left_out,
            ))

    rows = []
    for kind, row_slots in ROWS.items():
        best: dict[str, Suggestion] = {}
        for planned in pool.looks:
            options = [c for c in pool.candidates if c.slot in row_slots and _gender_ok(c.item, gender)]
            for item in scored(options, planned.occasion, len(planned.slots)):
                current = best.get(item.product.id)
                if current is None or (item.match or -1) > (current.match or -1):
                    best[item.product.id] = item
        if best:
            rows.append(Row(kind=kind, items=_rank(list(best.values()), distance)[:ROW_SIZE]))
    return looks, rows


# ---------- Saving and the whole step ----------


def cache_id(anchor: Product, **choices: object) -> str:
    asked = "|".join(f"{name}={choices[name]}" for name in sorted(choices))
    return f"{anchor.id}__{hashlib.sha256(f'{VERSION}|{asked}'.encode()).hexdigest()[:16]}"


def read_saved(cache: verify.CheckCache | None, doc_id: str, now: datetime) -> Pool | None:
    if cache is None:
        return None
    try:
        doc = cache.get_many([doc_id]).get(doc_id)
        if not doc or doc.get("version") != VERSION:
            return None
        if datetime.fromisoformat(doc["saved_at"]) + LOOK_TTL < now:
            return None
        return Pool.model_validate(doc["pool"])
    except Exception as error:
        log.warning("saved look %s not read: %s", doc_id, error)
        return None


def save(cache: verify.CheckCache | None, doc_id: str, pool: Pool, now: datetime) -> None:
    if cache is None:
        return
    try:
        cache.put_many({doc_id: {"version": VERSION, "saved_at": now.isoformat(), "pool": pool.model_dump(mode="json")}})
    except Exception as error:
        log.warning("look %s not saved: %s", doc_id, error)


def read_embeddings(db: Any, ids: Sequence[str], collection: str = retrieve.PRODUCTS) -> dict[str, list[float]]:
    if not ids:
        return {}
    target = db.collection(collection)
    snapshots = db.get_all([target.document(doc_id) for doc_id in ids], field_paths=[VECTOR_FIELD])
    return {s.id: list((s.to_dict() or {}).get(VECTOR_FIELD) or []) for s in snapshots if s.exists}


def complete_look(
    anchor: Product,
    *,
    client: verify.Client,
    db: Any,
    embedder: Any,
    settings: verify.Settings | None = None,
    cache: verify.CheckCache | None = None,
    occasion: Occasion | None = None,
    style: Style | None = None,
    budget: int | None = None,
    gender: Gender | None = None,
    taste: Mapping[str, list[float]] | None = None,
    available: Collection[str] | None = None,
    weights: Mapping[str, float] | None = None,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = verify.download,
    sleep: Callable[[float], None] = time.sleep,
) -> Looks:
    """Up to MAX_LOOKS looks around `anchor`, plus rows of alternatives. Raises LookFailed when Gemini can't plan."""
    weights = dict(weights or default_weights())
    gender = anchor.gender if anchor.gender != UNISEX else gender
    anchor_price = anchor.price.selling if anchor.price else None
    room = budget - anchor_price if budget is not None and anchor_price is not None else None
    answer = Looks(product_id=anchor.id, looks=[], rows=[], weights=weights, budget_max_inr=budget,
                   demo_prices=bool(anchor.price and anchor.price.demo))
    if room is not None and room <= 0:
        return answer.model_copy(update={"note": OVER_BUDGET})
    slots = slot_types(anchor, available)
    if not slots:
        return answer.model_copy(update={"note": NOTHING})

    now = now or datetime.now(timezone.utc)
    doc_id = cache_id(anchor, occasion=occasion, style=style, room=room, gender=gender, slots=sorted(slots.items()))
    pool = read_saved(cache, doc_id, now)
    if pool is None:
        pool = build_pool(anchor, slots, client=client, settings=settings or verify.default_settings(), db=db,
                          embedder=embedder, gender=gender, room=room, occasion=occasion, style=style, fetch=fetch,
                          sleep=sleep)
        save(cache, doc_id, pool, now)

    taste = taste or {}
    wanted = [c.item.id for c in pool.candidates if c.item.article_type in taste]
    embeddings = read_embeddings(db, wanted) if wanted and weights.get("preferences", 0) > 0 else {}
    looks, rows = assemble(anchor, pool, gender=gender, budget=budget, weights=weights, taste=taste, embeddings=embeddings)
    shown = [c.product for look in looks for piece in look.pieces for c in piece.choices]
    note = None
    if not looks:
        note = NOTHING
    elif any(c.scores is None for c in pool.candidates):
        note = UNSCORED
    return answer.model_copy(update={
        "looks": looks, "rows": rows, "note": note,
        "demo_prices": answer.demo_prices or any(item.price and item.price.demo for item in shown),
    })
