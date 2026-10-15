"""The six steps in order (IMPLEMENTATION.md step 2.9), as plain functions shared by the API and the ADK agent.

A search runs: find_candidates (FETCH_LIMIT) -> quick_check -> the careful check of about CHECK_COUNT products ->
rank with the shopper's style -> what to give up. Free careful-check places go to quick-check drops that break
just one must-have (see `one_away`). `search` reports progress through `emit`, in this order:

    candidates {count, dropped}           after the quick check; count is how many go on (at most CHECK_POOL
                                          plus the one-away products), dropped how many the quick check removed
    checking {product_id, title, thumb}   once for every product sent to the careful check
    verified {product_id, status, ticks}  as each careful check finishes (saved results come first)
    done {...}                            the ranked results

`live_search` runs the same steps on live Shopify products (agent/sources/shopify.py) instead of our catalogue:
find in Shopify's Global Catalog -> live_quick_check (budget limit, size) -> careful check of the photo and listing
-> rank -> what to give up. Alongside it, Gemini with Google Search looks for listings on other sites and has Google
read their pages (agent/web_search.py). `done` goes out as soon as the main results are ready; the stream then
stays open for `web {results, search_entry_point_html, queries}`, sent when the pages are checked or WEB_WAIT_S into
the search, whichever is first. A late or failing page check sends the links Google Search found, unchecked; no
`web` comes when the search itself found nothing or failed.

One product's bad data never fails a search: it's logged and left out.

This module also turns a photo into words the checklist step understands, and applies the shopper's edits to a
checklist (the structured ones in code, the free-text ones through Gemini).
"""

import ipaddress
import logging
import os
import socket
import time
from collections import Counter
from collections.abc import Callable, Sequence, Set
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from typing import Annotated, Any, ClassVar, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError, create_model

from agent import brief as briefs
from agent import delivery, verify, web_search
from agent.cards import ResultCard, result_card
from agent.learn import TasteProfile, vectors
from agent.models import Check, Evidence, MustHave, Product, Rule, ShoppingBrief, Status, Verification
from agent.quick_check import clearly_breaks, live_quick_check, quick_check, saved_value
from agent.rank import Candidate, Tick, rank, ticks_for
from agent.retrieve import DEFAULT_LIMIT, FETCH_LIMIT, Hit, attach_embeddings, find_candidates
from agent.rules import apply_rules, label
from agent.sources import shopify
from agent.tradeoffs import Tradeoff, suggest_tradeoffs
from agent.web_search import WebFindings, WebResult
from data_prep import schemas
from data_prep.embed import Embedder, Embedding
from data_prep.simulate import size_names

log = logging.getLogger(__name__)

CHECK_POOL = DEFAULT_LIMIT
CHECK_COUNT = 12
OVER_LIMIT_SLOTS = 4
CHECK_DEADLINE_S = 8.0
MAX_PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
LINK_TIMEOUT_S = 10
SHOPPER_PHOTO = "shopper-photo"
PHOTO_MAX_OUTPUT_TOKENS = 256
PHOTO_ATTEMPTS = 3
RETRY_CODES = {408, 429, 500, 502, 503, 504}
MAX_BUDGET_INR = 10_00_000
MAX_TEXT = briefs.MAX_REQUEST_CHARS
LIVE_FETCH_LIMIT = shopify.SEARCH_LIMIT
WEB_WAIT_S = 25.0
WEB_MARGIN_S = 0.5
SIZES_WAIT_S = 1.5
SIZE_LOOKUPS = 6
LOOKUP_WORKERS = 3
LIVE_DEADLINE_NOTE = "Delivery dates for live stores are shown at their checkout"
LIVE_SHOP_NOTE = "{}: check at the store's checkout"
LIVE_RATING_NOTE = "Rated {}★ and up: live stores don't share star ratings"


# ---------- Stream events ----------


class Event(BaseModel):
    kind: ClassVar[str]


class CandidatesEvent(Event):
    kind: ClassVar[str] = "candidates"
    count: int
    dropped: int


class CheckingEvent(Event):
    kind: ClassVar[str] = "checking"
    product_id: str
    title: str
    thumb: str | None = None


class VerifiedEvent(Event):
    kind: ClassVar[str] = "verified"
    product_id: str
    status: Status
    ticks: list[Tick]


class DoneEvent(Event):
    kind: ClassVar[str] = "done"
    checked: int
    passed: int
    unclear: int
    rejected: dict[str, int]
    duplicates: int
    tradeoffs: list[Tradeoff]
    matches: list[ResultCard]
    almost: list[ResultCard]
    not_checkable: list[str]


class WebEvent(Event):
    kind: ClassVar[str] = "web"
    results: list[WebResult]
    search_entry_point_html: str | None = None
    queries: list[str] = Field(default_factory=list)


class ErrorEvent(Event):
    kind: ClassVar[str] = "error"
    message: str
    retryable: bool


Emit = Callable[[Event], None]


def tick_status(ticks: Sequence[Tick]) -> Status:
    """The same verdict rank() uses: any fail fails, every tick a pass (or no must-haves) passes."""
    if any(tick.status == "fail" for tick in ticks):
        return "fail"
    return "pass" if all(tick.status == "pass" for tick in ticks) else "unclear"


# ---------- What a search needs ----------


@dataclass
class Services:
    db: Any
    embedder: Embedder
    check_cache: verify.CheckCache | None = None
    check_client: verify.Client | None = None
    check_settings: verify.Settings | None = None


@cache
def default_services() -> Services:
    from agent.retrieve import default_db, default_embedder

    db = default_db()
    return Services(db=db, embedder=default_embedder(), check_cache=verify.FirestoreCache(client=db),
                    check_client=verify.make_client())


class LiveCatalog(Protocol):
    def search(self, brief: ShoppingBrief, query_text: str, limit: int = ...) -> list[Product]: ...

    def get_product(self, product_id: str, brief: ShoppingBrief) -> Product: ...

    def checkout_link(self, product_id: str, pincode: str | None, size: str | None) -> str: ...


WebFinder = Callable[[ShoppingBrief, str], WebFindings | None]
PageReader = Callable[[ShoppingBrief, WebFindings, float], WebFindings]


@dataclass
class LiveServices:
    catalog: LiveCatalog
    find_on_web: WebFinder | None = None
    read_pages: PageReader | None = None


def default_live(services: Services) -> LiveServices:
    def find(brief: ShoppingBrief, text: str) -> WebFindings | None:
        return web_search.find_on_web(brief, text, client=services.check_client)

    def read(brief: ShoppingBrief, findings: WebFindings, wait_s: float) -> WebFindings:
        return web_search.read_pages(brief, findings, client=services.check_client, wait_s=wait_s)

    return LiveServices(catalog=shopify.Catalog(), find_on_web=find, read_pages=read)


class PhotoProblem(ValueError):
    """The shopper's photo can't be used; the message is safe to show."""


class PhotoSearch:
    """The shopper's photo for the search, embedded once.

    Later searches in the same session reuse the saved vector, so the uploaded photo itself can be deleted after
    the first search (IMPLEMENTATION.md 7.3).
    """

    def __init__(self, link: str | None, read: Callable[[str], tuple[bytes, str]], vector: list[float] | None = None):
        self.link = link
        self.read = read
        self.vector = vector

    @property
    def url(self) -> str | None:
        """What find_candidates sees and may log: a marker, never the upload's path (it holds the shopper's uid)."""
        return SHOPPER_PHOTO if self.link or self.vector is not None else None

    def fetch(self, url: str) -> tuple[bytes, str]:
        if self.vector is not None or not self.link:
            return b"", "image/jpeg"
        return self.read(self.link)

    def embedder(self, inner: Embedder) -> Embedder:
        def embed(parts: list[types.Part]) -> Embedding:
            if not any(part.inline_data is not None for part in parts):
                return inner(parts)
            if self.vector is None:
                self.vector = inner(parts).values
            return Embedding(self.vector)

        return embed


@dataclass
class SearchOutcome:
    done: DoneEvent
    timings: dict[str, float] = field(default_factory=dict)
    photo_vector: list[float] | None = None
    live_ids: list[str] = field(default_factory=list)


# ---------- The search ----------


def choose_for_check(pool: Sequence[Hit], count: int = CHECK_COUNT, over_limit: int = OVER_LIMIT_SLOTS) -> list[Hit]:
    """The best products within limits, plus up to `over_limit` of the best over budget or late, in search order.

    Over-limit products are never shown as matches; "what to give up" counts them. When too few products are within
    limits, more over-limit ones fill the places.
    """
    within = [hit for hit in pool if not (hit.over_budget or hit.late)]
    outside = [hit for hit in pool if hit.over_budget or hit.late]
    chosen = within[: count - min(over_limit, len(outside))]
    chosen += outside[: count - len(chosen)]
    position = {hit.product.id: index for index, hit in enumerate(pool)}
    return sorted(chosen, key=lambda hit: position[hit.product.id])


def one_away(brief: ShoppingBrief, dropped: Sequence[tuple[Hit, str]], room: int) -> list[Hit]:
    """Quick-check drops that break exactly one must-have, closest first, to fill free careful-check places.

    When the quick check leaves few products, "what to give up" would have nothing checked to count; these give
    it "drop <must-have>: N options". They keep the quick check's fail on the must-have they break.
    """
    if room <= 0:
        return []
    found = [hit for hit, _ in dropped if sum(clearly_breaks(m, hit.product) for m in brief.must_haves) == 1]
    return found[:room]


def keep_quick_verdict(brief: ShoppingBrief, product: Product, verification: Verification) -> Verification:
    """A one-away product still fails the must-have its saved detail clearly breaks, whatever the careful check says.

    It was only checked so "what to give up" can count it; it must not become a match against a clear saved detail.
    """
    broken = next((m for m in brief.must_haves if clearly_breaks(m, product)), None)
    if broken is None:
        return verification
    value = verify.value_text(saved_value(product, broken.attribute))
    evidence = Evidence(type="attribute", note=f"The saved details say {broken.attribute.replace('_', ' ')}: {value}.",
                        quote=f"{broken.attribute}: {value}")
    fail = Check(must_have_id=broken.id, status="fail", confidence=1.0, evidence=evidence)
    checks = [check for check in verification.checks if check.must_have_id != broken.id] + [fail]
    order = {m.id: index for index, m in enumerate(brief.must_haves)}
    return verification.model_copy(update={"checks": sorted(checks, key=lambda check: order.get(check.must_have_id, 0))})


def search(
    brief: ShoppingBrief,
    *,
    query_text: str,
    services: Services,
    taste: TasteProfile | None = None,
    photo: PhotoSearch | None = None,
    emit: Emit = lambda event: None,
    now: datetime | None = None,
    deadline_s: float = CHECK_DEADLINE_S,
) -> SearchOutcome:
    """Run steps 2 to 4 for a checklist and return the `done` summary. Raises only when the whole search fails."""
    started = time.perf_counter()
    timings: dict[str, float] = {}
    found: dict[str, float] = {}
    embedder = photo.embedder(services.embedder) if photo else services.embedder
    hits = find_candidates(
        brief,
        query_text=query_text,
        reference_image_url=photo.url if photo else None,
        limit=FETCH_LIMIT,
        db=services.db,
        embedder=embedder,
        now=now,
        fetch=photo.fetch if photo else _no_photo,
        timings=found,
    )
    timings.update({f"find_{name}": round(ms, 1) for name, ms in found.items()})

    clock = time.perf_counter()
    kept, dropped = quick_check(brief, hits)
    pool = kept[:CHECK_POOL]
    chosen = choose_for_check(pool)
    nearly = one_away(brief, dropped, CHECK_COUNT - len(chosen))
    chosen += nearly
    sent = {hit.product.id for hit in nearly}
    dropped = [(hit, reason) for hit, reason in dropped if hit.product.id not in sent]
    timings["quick_ms"] = _ms_since(clock)
    emit(CandidatesEvent(count=len(pool) + len(nearly), dropped=len(dropped)))

    for hit in chosen:
        images = hit.product.images
        emit(CheckingEvent(product_id=hit.product.id, title=hit.product.title, thumb=images.thumb if images else None))

    clock = time.perf_counter()
    nearly_products = {hit.product.id: hit.product for hit in nearly}

    def settled(verification: Verification) -> Verification:
        product = nearly_products.get(verification.product_id)
        return keep_quick_verdict(brief, product, verification) if product else verification

    def on_result(verification: Verification) -> None:
        timings.setdefault("first_verified_ms", _ms_since(started))
        try:
            ticks = ticks_for(brief, settled(verification))
            emit(VerifiedEvent(product_id=verification.product_id, status=tick_status(ticks), ticks=ticks))
        except Exception:
            log.exception("product %s: couldn't report its check", verification.product_id)

    verifications = verify.careful_check(
        brief,
        [hit.product for hit in chosen],
        client=services.check_client,
        cache=services.check_cache,
        settings=services.check_settings,
        on_result=on_result,
        deadline_s=deadline_s,
    )
    verifications = [settled(verification) for verification in verifications]
    timings["check_ms"] = _ms_since(clock)

    clock = time.perf_counter()
    checked = _with_embeddings(chosen, services.db)
    candidates = _candidates(checked, verifications)
    results = rank(brief, candidates, vectors(taste or {}))
    tradeoffs = suggest_tradeoffs(brief, candidates)
    products = {candidate.product.id: candidate.product for candidate in candidates}
    matches = _cards(results.matches, products)
    almost = _cards(results.almost, products)
    rejected = Counter(reason for _, reason in dropped) + Counter(results.rejected)
    done = DoneEvent(
        checked=results.checked + len(dropped),
        passed=len(matches),
        unclear=len(almost),
        rejected=dict(rejected.most_common()),
        duplicates=results.duplicates,
        tradeoffs=tradeoffs,
        matches=matches,
        almost=almost,
        not_checkable=list(brief.not_checkable),
    )
    timings["rank_ms"] = _ms_since(clock)
    timings["total_ms"] = _ms_since(started)
    log.info(
        "search %s: %d found, %d kept, %d dropped, %d checked, %d passed, %d unclear, ms %s",
        brief.category, len(hits), len(pool), len(dropped), done.checked, done.passed, done.unclear, timings,
    )
    emit(done)
    return SearchOutcome(done=done, timings=timings, photo_vector=photo.vector if photo else None)


def live_search(
    brief: ShoppingBrief,
    *,
    query_text: str,
    services: Services,
    live: LiveServices,
    taste: TasteProfile | None = None,
    emit: Emit = lambda event: None,
    deadline_s: float = CHECK_DEADLINE_S,
    web_wait_s: float = WEB_WAIT_S,
) -> SearchOutcome:
    """Steps 2 to 4 on live Shopify products, with listings on other sites alongside.

    Returns after the `web` event (at most `web_wait_s` into the search), so the stream ends with it. Raises only
    when the whole search fails, e.g. shopify.CatalogError when Shopify's catalog can't be reached.
    """
    started = time.perf_counter()
    timings: dict[str, float] = {}
    brief, shop_notes = without_shop_details(brief)
    if brief.min_rating is not None:
        shop_notes.append(LIVE_RATING_NOTE.format(f"{brief.min_rating:g}"))
        brief = brief.model_copy(update={"min_rating": None})
    workers = ThreadPoolExecutor(max_workers=1 + LOOKUP_WORKERS, thread_name_prefix="live")
    try:
        web = _OtherSites(live, deadline=started + web_wait_s) if live.find_on_web else None
        if web is not None:
            web.future = workers.submit(web.run, brief, query_text)
        clock = time.perf_counter()
        products = live.catalog.search(brief, query_text, LIVE_FETCH_LIMIT)
        timings["shopify_search_ms"] = _ms_since(clock)

        clock = time.perf_counter()
        kept, dropped = live_quick_check(brief, products)
        pool = kept[:CHECK_POOL]
        chosen = choose_for_check(pool)
        timings["quick_ms"] = _ms_since(clock)
        emit(CandidatesEvent(count=len(pool), dropped=len(dropped)))
        for hit in chosen:
            images = hit.product.images
            emit(CheckingEvent(product_id=hit.product.id, title=hit.product.title, thumb=images.thumb if images else None))

        def on_result(verification: Verification) -> None:
            timings.setdefault("first_verified_ms", _ms_since(started))
            try:
                ticks = ticks_for(brief, verification)
                emit(VerifiedEvent(product_id=verification.product_id, status=tick_status(ticks), ticks=ticks))
            except Exception:
                log.exception("product %s: couldn't report its check", verification.product_id)

        clock = time.perf_counter()
        verifications = verify.careful_check(
            brief,
            [hit.product for hit in chosen],
            client=services.check_client,
            cache=services.check_cache,
            settings=services.check_settings,
            on_result=on_result,
            deadline_s=deadline_s,
        )
        timings["check_ms"] = _ms_since(clock)

        clock = time.perf_counter()
        candidates = _candidates(chosen, verifications)
        results = rank(brief, candidates, vectors(taste or {}))
        tradeoffs = suggest_tradeoffs(brief, candidates)
        timings["rank_ms"] = _ms_since(clock)

        clock = time.perf_counter()
        shown = [result.product_id for result in [*results.matches, *results.almost]][:SIZE_LOOKUPS]
        lookups = {product_id: workers.submit(live.catalog.get_product, product_id, brief) for product_id in shown}
        found = _with_live_sizes({candidate.product.id: candidate.product for candidate in candidates}, lookups,
                                 deadline=clock + SIZES_WAIT_S)
        timings["sizes_ms"] = _ms_since(clock)
        matches = _cards(results.matches, found)
        almost = _cards(results.almost, found)
        rejected = Counter(reason for _, reason in dropped) + Counter(results.rejected)
        done = DoneEvent(
            checked=results.checked + len(dropped),
            passed=len(matches),
            unclear=len(almost),
            rejected=dict(rejected.most_common()),
            duplicates=results.duplicates,
            tradeoffs=tradeoffs,
            matches=matches,
            almost=almost,
            not_checkable=[*live_not_checkable(brief), *shop_notes],
        )
        timings["total_ms"] = _ms_since(started)
        emit(done)
        if web is not None and (web_event := web.event(timings)) is not None:
            emit(web_event)
        log.info(
            "live search %s: %d found, %d kept, %d dropped, %d checked, %d passed, %d unclear, ms %s",
            brief.category, len(products), len(pool), len(dropped), done.checked, done.passed, done.unclear, timings,
        )
        return SearchOutcome(done=done, timings=timings, live_ids=[card.product_id for card in [*matches, *almost]])
    finally:
        workers.shutdown(wait=False, cancel_futures=True)


def without_shop_details(brief: ShoppingBrief) -> tuple[ShoppingBrief, list[str]]:
    """Live stores don't share cash on delivery, returns or exchange, so those can't decide a match.

    They leave the checklist for this search and come back as notes to check at the store's checkout.
    """
    shop = [m for m in brief.must_haves if m.attribute in verify.SHOP_DETAILS]
    if not shop:
        return brief, []
    kept = [m for m in brief.must_haves if m.attribute not in verify.SHOP_DETAILS]
    notes = [LIVE_SHOP_NOTE.format(label(m)[:1].upper() + label(m)[1:]) for m in shop]
    return brief.model_copy(update={"must_haves": kept}), notes


def live_not_checkable(brief: ShoppingBrief) -> list[str]:
    return [*brief.not_checkable, *([LIVE_DEADLINE_NOTE] if brief.deadline_date else [])]


def live_summary(done: DoneEvent) -> dict:
    """What a live search keeps in the session: our counts and verdicts by Shopify id, none of Shopify's data."""
    summary = done.model_dump(mode="json", exclude={"matches", "almost"})
    for name, cards in (("matches", done.matches), ("almost", done.almost)):
        summary[name] = [
            {"product_id": card.product_id, "statuses": {tick.must_have_id: tick.status for tick in card.ticks}}
            for card in cards
        ]
    return summary


@dataclass
class _OtherSites:
    """'On other sites' in two stages, run alongside the live search: Google Search finds the links, then Google
    reads their pages. `links` keeps the first stage's links, so a late or failing second stage still sends them."""

    live: LiveServices
    deadline: float
    future: Future | None = None
    links: WebFindings | None = None
    search_ms: float | None = None
    read_ms: float | None = None

    def run(self, brief: ShoppingBrief, text: str) -> WebFindings | None:
        clock = time.perf_counter()
        self.links = self.live.find_on_web(brief, text) if self.live.find_on_web else None
        self.search_ms = _ms_since(clock)
        if self.links is None or not self.links.results or self.live.read_pages is None:
            return self.links
        clock = time.perf_counter()
        read = self.live.read_pages(brief, self.links, max(0.0, self.deadline - clock - WEB_MARGIN_S))
        self.read_ms = _ms_since(clock)
        return read

    def event(self, timings: dict[str, float]) -> WebEvent | None:
        """The `web` event once the pages are read, or at the deadline with what's there; None if nothing was found."""
        clock = time.perf_counter()
        try:
            findings = self.future.result(timeout=max(0.0, self.deadline - clock)) if self.future else self.links
        except FutureTimeout:
            log.warning("other sites not read in time; sending the links found")
            findings = self.links
        except Exception:
            log.exception("other sites failed; sending the links found")
            findings = self.links
        stages = (("web_ms", self.search_ms), ("web_read_ms", self.read_ms))
        timings.update({name: ms for name, ms in stages if ms is not None})
        timings["web_wait_ms"] = _ms_since(clock)
        if findings is None:
            return None
        return WebEvent(results=findings.results, search_entry_point_html=findings.search_entry_point_html,
                        queries=findings.queries)


def _with_live_sizes(products: dict[str, Product], lookups: dict[str, Future], *, deadline: float) -> dict[str, Product]:
    """The store's available sizes from get_product when it answered by `deadline`; the search's sizes otherwise.

    Only the products shown are looked up, a few at a time: Shopify's keyless access is rate limited.
    """
    updated = dict(products)
    for product_id, future in lookups.items():
        try:
            fresh = future.result(timeout=max(0.0, deadline - time.perf_counter()))
        except Exception as error:
            log.info("product %s: sizes from the search only: %s", product_id, type(error).__name__)
            continue
        if fresh.sizes and product_id in updated:
            updated[product_id] = updated[product_id].model_copy(update={"sizes": fresh.sizes})
    return updated


def _no_photo(url: str) -> tuple[bytes, str]:
    raise PhotoProblem("no photo for this search")


def _with_embeddings(hits: list[Hit], db: Any) -> list[Hit]:
    """Embeddings for near-duplicates and the style profile; without them ranking still works."""
    if not hits:
        return hits
    try:
        return attach_embeddings(hits, db=db)
    except Exception as error:
        log.warning("embeddings not attached, ranking without style: %s: %s", type(error).__name__, error)
        return hits


def _candidates(hits: list[Hit], verifications: list[Verification]) -> list[Candidate]:
    by_id = {verification.product_id: verification for verification in verifications}
    candidates = []
    for hit in hits:
        try:
            candidates.append(
                Candidate(
                    product=hit.product,
                    verification=by_id[hit.product.id],
                    total_price=hit.total_price,
                    delivery_date=hit.delivery_date,
                )
            )
        except (KeyError, ValidationError) as error:
            log.warning("product %s: left out, bad data: %s", hit.product.id, error)
    return candidates


def _cards(results: list, products: dict[str, Product]) -> list[ResultCard]:
    cards = []
    for result in results:
        try:
            cards.append(result_card(result, products[result.product_id]))
        except Exception:
            log.exception("product %s: left out, couldn't build its card", result.product_id)
    return cards


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def friendly_error(error: Exception) -> ErrorEvent:
    """What the shopper sees when a whole search fails."""
    if isinstance(error, errors.APIError) and error.code in RETRY_CODES:
        return ErrorEvent(message="Our AI is busy right now. Please try again in a moment.", retryable=True)
    if isinstance(error, PhotoProblem):
        return ErrorEvent(message=str(error), retryable=False)
    if isinstance(error, shopify.CatalogError):
        return ErrorEvent(message="We couldn't reach the live stores right now. Please try again, or search our "
                                  "demo catalogue.", retryable=True)
    if isinstance(error, ValueError) and "unknown category" in str(error):
        return ErrorEvent(message="We don't carry that kind of product yet.", retryable=False)
    return ErrorEvent(message="Something went wrong while searching. Please try again.", retryable=True)


# ---------- Ticks for one product, from saved checks ----------


def saved_ticks(brief: ShoppingBrief, product: Product, cache: verify.CheckCache | None) -> list[Tick]:
    """The product's tick-list for this checklist from saved careful checks; anything not checked yet is unclear."""
    checks = []
    asked = [m for m in brief.must_haves if m.attribute not in verify.SHOP_DETAILS]
    saved: dict[str, dict] = {}
    if cache is not None and asked:
        try:
            saved = cache.get_many([verify.doc_id(product.id, m) for m in asked])
        except Exception as error:
            log.warning("product %s: saved checks not read: %s: %s", product.id, type(error).__name__, error)
    for must_have in brief.must_haves:
        if must_have.attribute in verify.SHOP_DETAILS:
            checks.append(verify.shop_check(must_have, product))
        elif (doc := saved.get(verify.doc_id(product.id, must_have))) and (
            check := verify.check_from_doc(doc, must_have, product)
        ):
            checks.append(check)
    return ticks_for(brief, keep_quick_verdict(brief, product, Verification(product_id=product.id, checks=checks)))


# ---------- Photos ----------


def decode_photo(mime_type: str | None, data: bytes) -> str:
    """The photo's type when it's one we take; raises PhotoProblem otherwise."""
    kind = (mime_type or "image/jpeg").split(";")[0].strip().lower()
    if kind == "image/jpg":
        kind = "image/jpeg"
    if kind not in PHOTO_TYPES:
        raise PhotoProblem("Please use a JPEG, PNG or WebP photo.")
    if not data:
        raise PhotoProblem("The photo is empty. Please try another one.")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoProblem("That photo is too big. Please use one under 8 MB.")
    return kind


def fetch_link(url: str) -> tuple[bytes, str]:
    """A photo from a public https link. Private and internal addresses, redirects and big files are refused."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise PhotoProblem("Photo links must start with https://.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except OSError as error:
        raise PhotoProblem("We couldn't open that photo link.") from error
    if not addresses or not all(ipaddress.ip_address(address.split("%")[0]).is_global for address in addresses):
        raise PhotoProblem("We couldn't open that photo link.")
    try:
        with httpx.stream("GET", url, timeout=LINK_TIMEOUT_S, follow_redirects=False) as response:
            if response.status_code != 200:
                raise PhotoProblem("We couldn't open that photo link.")
            mime_type = response.headers.get("content-type", "")
            data = bytearray()
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_PHOTO_BYTES:
                    raise PhotoProblem("That photo is too big. Please use one under 8 MB.")
    except httpx.HTTPError as error:
        raise PhotoProblem("We couldn't open that photo link.") from error
    return bytes(data), decode_photo(mime_type, bytes(data))


class PhotoKind(BaseModel):
    category: str
    gender: str = "unknown"

    def words(self) -> str:
        return self.category if self.gender in ("unknown", "unisex") else f"{self.category} for {self.gender}"


PHOTO_PROMPT = (
    "Which of these product types is the main item in the photo, and who is it for? "
    "Answer 'none' when it is none of them.\n" + ", ".join(schemas.ARTICLE_TYPE_TO_GROUP)
)


@cache
def photo_kind_schema() -> type[BaseModel]:
    return create_model(
        "PhotoKind",
        category=(Literal[(*schemas.ARTICLE_TYPE_TO_GROUP, "none")], ...),
        gender=(Literal[(*briefs.GENDERS, "unknown")], ...),
    )


class PhotoBusy(Exception):
    """Gemini couldn't look at the photo right now."""


def photo_kind(
    data: bytes,
    mime_type: str,
    *,
    client: Any = None,
    model: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> PhotoKind | None:
    """The product type in the photo, from one small Gemini call; None when it's nothing we carry."""
    client = client or briefs.default_client()
    model = model or os.environ.get("GEMINI_LITE_MODEL") or verify.LITE_MODEL
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=photo_kind_schema(),
        max_output_tokens=PHOTO_MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
    )
    problem = ""
    for attempt in range(1, PHOTO_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model, contents=[types.Part.from_bytes(data=data, mime_type=mime_type), PHOTO_PROMPT], config=config
            )
            kind = PhotoKind.model_validate_json(response.text or "")
            return kind if kind.category in schemas.ARTICLE_TYPE_TO_GROUP else None
        except ValidationError as error:
            problem = f"bad answer: {error.errors()[0]['msg']}"
        except errors.APIError as error:
            problem = f"HTTP {error.code}"
            if error.code not in RETRY_CODES:
                break
        except (httpx.HTTPError, OSError) as error:
            problem = type(error).__name__
        log.warning("photo kind attempt %d failed: %s", attempt, problem)
        if attempt < PHOTO_ATTEMPTS:
            sleep(attempt)
    raise PhotoBusy(problem)


def request_text(text: str | None, kind: PhotoKind | None) -> str:
    """What the checklist step reads: the shopper's words, plus what the photo shows."""
    words = " ".join((text or "").split())
    if kind is None:
        if not words:
            raise PhotoProblem("We couldn't tell what's in the photo. Add a few words, like 'white sneakers'.")
        return words
    if not words:
        return f"Something like the {kind.words()} in my photo."
    return f"{words} (My photo shows {kind.words()}.)"


# ---------- Making and editing the checklist ----------


def fallback_settings() -> briefs.Settings:
    return briefs.Settings(model=os.environ.get("GEMINI_LITE_MODEL") or verify.LITE_MODEL, attempts=2)


def with_fallback[T](call: Callable[[briefs.Settings | None], T]) -> T:
    """The checklist call on Flash, then once on Flash-Lite when Flash keeps failing.

    Under load Flash can run past brief.py's 25 s request timeout, which comes back as HTTP 499 and isn't retried.
    """
    try:
        return call(None)
    except briefs.BriefError as error:
        fallback = fallback_settings()
        log.warning("checklist failed on %s (%s); trying %s", briefs.DEFAULT_MODEL, error, fallback.model)
        return call(fallback)


class ChangeProblem(ValueError):
    """The edit can't be applied; the message is safe to show."""


class TextChange(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class AnswerChange(BaseModel):
    type: Literal["answer"]
    question: str = Field(default="", max_length=500)
    answer: str = Field(min_length=1, max_length=MAX_TEXT)


class AddMustHave(BaseModel):
    type: Literal["add_must_have"]
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class EditMustHave(BaseModel):
    type: Literal["edit_must_have"]
    id: str
    rule: Rule
    value: str | bool | float


class RemoveMustHave(BaseModel):
    type: Literal["remove_must_have"]
    id: str


class SetRule(BaseModel):
    type: Literal["set_rule"]
    must_have: MustHave
    enabled: bool


class RemoveNiceToHave(BaseModel):
    type: Literal["remove_nice_to_have"]
    attribute: str
    value: str | bool | float


class SetBudget(BaseModel):
    type: Literal["set_budget"]
    budget_max_inr: int | None = Field(default=None, gt=0, le=MAX_BUDGET_INR)


class SetDeadline(BaseModel):
    type: Literal["set_deadline"]
    deadline_date: date | None = None


class SetPincode(BaseModel):
    type: Literal["set_pincode"]
    pincode: str = Field(max_length=12)


class SetSize(BaseModel):
    type: Literal["set_size"]
    size: str | None = Field(default=None, max_length=20)


class SetMinRating(BaseModel):
    type: Literal["set_min_rating"]
    min_rating: float | None = Field(default=None, ge=1, le=5)


Source = Literal["live", "catalog"]


class SetSource(BaseModel):
    type: Literal["set_source"]
    source: Source


BriefChange = Annotated[
    TextChange | AnswerChange | AddMustHave | EditMustHave | RemoveMustHave | SetRule | RemoveNiceToHave | SetBudget
    | SetDeadline | SetPincode | SetSize | SetMinRating | SetSource,
    Field(discriminator="type"),
]
GEMINI_CHANGES = (TextChange, AnswerChange, AddMustHave)


@dataclass
class Edited:
    brief: ShoppingBrief
    rules: list[MustHave]
    disabled: set[str]
    words: str = ""
    source: Source | None = None


def apply_change(
    brief: ShoppingBrief,
    change: BaseModel,
    *,
    rules: list[MustHave],
    disabled: Set[str],
    today: date,
    client: Any = None,
) -> Edited:
    """The checklist after one edit, with the saved rules re-applied. Free-text edits go to Gemini (BriefError).

    set_source leaves the checklist as it is; Edited.source carries where the next search looks.
    """
    rules = list(rules)
    disabled = set(disabled)
    words = ""
    source = None

    def ask(said: str) -> ShoppingBrief:
        return with_fallback(lambda settings: briefs.update_brief(brief, said, today=today, client=client,
                                                                  settings=settings))

    match change:
        case TextChange(text=text):
            brief, words = ask(text), text
        case AnswerChange(question=question, answer=answer):
            brief, words = ask(f'Answer to "{question}": {answer}' if question.strip() else answer), answer
        case AddMustHave(text=text):
            brief, words = ask(f"It must have: {text}"), text
        case EditMustHave():
            brief = _edit_must_have(brief, change)
        case RemoveMustHave(id=must_id):
            brief, disabled = _remove_must_have(brief, must_id, disabled)
        case SetRule(must_have=rule, enabled=enabled):
            rules, disabled = _set_rule(brief, rule, enabled, rules, disabled)
        case RemoveNiceToHave(attribute=attribute, value=value):
            kept = [n for n in brief.nice_to_haves if not (n.attribute == attribute and _same(n.value, value))]
            brief = brief.model_copy(update={"nice_to_haves": kept})
        case SetBudget(budget_max_inr=budget):
            brief = brief.model_copy(update={"budget_max_inr": budget})
        case SetDeadline(deadline_date=deadline):
            if deadline is not None and deadline < today:
                raise ChangeProblem("Please pick today or a later date.")
            brief = brief.model_copy(update={"deadline_date": deadline})
        case SetPincode(pincode=pincode):
            try:
                code = delivery.lookup(pincode).pincode
            except delivery.UnknownPincode as error:
                raise ChangeProblem(str(error)) from error
            brief = _resolved(brief.model_copy(update={"pincode": code}), "pincode")
        case SetSize(size=size):
            brief = _set_size(brief, size)
        case SetMinRating(min_rating=rating):
            brief = brief.model_copy(update={"min_rating": rating})
        case SetSource(source=chosen):
            source = chosen
        case _:
            raise ChangeProblem("We don't know that kind of change.")
    brief = apply_rules(brief, rules, disabled)
    return Edited(brief=brief, rules=rules, disabled=disabled, words=words, source=source)


def _group(brief: ShoppingBrief) -> str:
    group = schemas.group_for(brief.category)
    if group is None:
        raise ChangeProblem("We don't carry that kind of product yet.")
    return group


def _standard(group: str, attribute: str, rule: str, value: str | bool | float) -> tuple[Rule, str | bool]:
    if attribute not in schemas.attributes_for(group):
        raise ChangeProblem(f"'{attribute.replace('_', ' ')}' isn't something we can check for this product.")
    form = briefs.standard_form(group, attribute, rule, value)
    if form is None:
        raise ChangeProblem(f"'{value}' isn't an option for {attribute.replace('_', ' ')}.")
    return form


def _edit_must_have(brief: ShoppingBrief, change: EditMustHave) -> ShoppingBrief:
    target = next((m for m in brief.must_haves if m.id == change.id), None)
    if target is None:
        raise ChangeProblem("We couldn't find that must-have. Please refresh.")
    if target.from_rule:
        raise ChangeProblem("That's one of your saved rules. Switch it off for this search instead.")
    group = _group(brief)
    rule, value = _standard(group, target.attribute, change.rule, change.value)
    edited = target.model_copy(update={"rule": rule, "value": value})
    own = [edited if m.id == target.id else m for m in brief.must_haves if not m.from_rule]
    same = [m for m in own if m.attribute == edited.attribute]
    values = briefs.allowed_values(group, edited.attribute)
    if not set.intersection(*(briefs.satisfying(values, m.rule, m.value) for m in same)):
        raise ChangeProblem("That clashes with another must-have.")
    unique: dict[str, MustHave] = {}
    for must_have in own:
        unique.setdefault(must_have.key(), must_have)
    return brief.model_copy(update={"must_haves": list(unique.values())})


def _remove_must_have(brief: ShoppingBrief, must_id: str, disabled: set[str]) -> tuple[ShoppingBrief, set[str]]:
    target = next((m for m in brief.must_haves if m.id == must_id), None)
    if target is None:
        return brief, disabled
    if target.from_rule:
        return brief, disabled | {target.key()}
    return brief.model_copy(update={"must_haves": [m for m in brief.must_haves if m.id != must_id]}), disabled


def _set_rule(
    brief: ShoppingBrief, rule: MustHave, enabled: bool, rules: list[MustHave], disabled: set[str]
) -> tuple[list[MustHave], set[str]]:
    group = schemas.group_for(brief.category)
    if group and rule.attribute in schemas.attributes_for(group):
        form = briefs.standard_form(group, rule.attribute, rule.rule, rule.value)
        if form:
            rule = rule.model_copy(update={"rule": form[0], "value": form[1]})
    key = rule.key()
    if not enabled:
        return rules, disabled | {key}
    if all(saved.key() != key for saved in rules):
        rules = [*rules, rule.model_copy(update={"from_rule": True})]
    return rules, disabled - {key}


def _same(a: str | bool | float, b: str | bool | float) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return type(a) is type(b) and a == b


def _set_size(brief: ShoppingBrief, size: str | None) -> ShoppingBrief:
    group = _group(brief)
    if group not in briefs.SIZED_GROUPS:
        raise ChangeProblem("This product comes in one size.")
    if size is None or not size.strip():
        missing = brief.missing if "size" in brief.missing else [*brief.missing, "size"]
        return brief.model_copy(update={"size": None, "missing": missing})
    normal = briefs.normalize_size(group, size, brief.gender)
    genders = [brief.gender] if brief.gender else ["men", "women", "unisex"]
    chart = list(dict.fromkeys(name for gender in genders for name in size_names(group, gender)))
    if normal not in chart:
        raise ChangeProblem(f"Please pick one of these sizes: {', '.join(chart)}.")
    return _resolved(brief.model_copy(update={"size": normal}), "size")


def _resolved(brief: ShoppingBrief, topic: str) -> ShoppingBrief:
    """The brief without `topic` in missing; its question moves on to what's still missing, or goes."""
    if topic not in brief.missing:
        return brief
    missing = [item for item in brief.missing if item != topic]
    question = brief.question
    if question and brief.missing[0] == topic:
        question = _next_question(brief, missing)
    return brief.model_copy(update={"missing": missing, "question": question})


def saved_rules(items: Sequence[MustHave]) -> list[MustHave]:
    """The shopper's rulebook in standard form, without repeats, numbered r1..rn and locked (from_rule).

    A rule must fit the vocabulary of at least one product type; it then applies wherever that detail exists.
    """
    unique: dict[str, MustHave] = {}
    for item in items:
        rule = _rule_in_vocabulary(item)
        unique.setdefault(rule.key(), rule)
    return [rule.model_copy(update={"id": f"r{n}"}) for n, rule in enumerate(unique.values(), start=1)]


def _rule_in_vocabulary(item: MustHave) -> MustHave:
    for group in schemas.GROUP_NAMES:
        if item.attribute in schemas.attributes_for(group):
            form = briefs.standard_form(group, item.attribute, item.rule, item.value)
            if form:
                return MustHave(id=item.id, attribute=item.attribute, rule=form[0], value=form[1], from_rule=True)
    raise ChangeProblem(f"We can't use '{item.attribute.replace('_', ' ')}: {item.value}' as a rule.")


def _next_question(brief: ShoppingBrief, missing: list[str]) -> str | None:
    language = brief.language if brief.language in briefs.LANGUAGES else "en"
    group = schemas.group_for(brief.category)
    if "size" in missing and group in briefs.SIZE_QUESTIONS:
        return briefs.SIZE_QUESTIONS[group][language]
    if "pincode" in missing:
        return briefs.PINCODE_QUESTIONS[language]
    return None
