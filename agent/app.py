"""The agent API for the app (PLAN.md appendix E): FastAPI calling the workflow functions directly.

Run locally (AUTH_MODE=dev lets requests without a sign-in token act as a demo shopper):

    AUTH_MODE=dev uvicorn agent.app:app --port 8080

Every /api/* endpoint needs `Authorization: Bearer <Firebase ID token>`. POST /api/search answers with server-sent
events (see agent/workflow.py for the order); comment lines (": ping") keep the stream open while products are
checked. Only the origins in ALLOWED_ORIGINS may call it from a browser.

Saved chats (agent/chats.py): POST /api/brief and /api/brief/update take an optional `chat_id` and keep that chat's
product entry (its filters and session) up to date; PUT /api/chats/{id}/messages saves what was said, GET /api/chats
lists the shopper's chats and POST /api/chats/{id}/open reopens one with its saved results. Opening needs no Gemini call.

Memory mode (agent/memory.py): with `memory: true`, POST /api/brief fills what the shopper left open (gender, colour,
size, ...) from their other chats about the same product, and the brief's `remembered` names those filters. The
`set_memory` change switches it off (the remembered filters go) or on (they come back) for the current product.

GET /api/catalog lists and keyword-searches the demo catalogue for the store pages (agent/browse.py); it makes no
Gemini call. POST /api/products/{id}/looks completes the look around a catalogue product (agent/outfit.py). The AI chat sends `ask: false`, so a missing size or gender never holds the search up with a question.

A search looks in live Shopify stores (source 'live', the default) or our demo catalogue ('catalog'). Live product
ids are Shopify ids ('gid://shopify/p/...'); their product page and checkout fetch them fresh from Shopify, and
checkout sends the shopper to the store's own checkout. GET /.well-known/ucp serves our UCP agent profile (public).
"""

import asyncio
import base64
import binascii
import logging
import os
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agent import auth, browse, cards, chats, delivery, memory, outfit, purchases, verify, workflow
from agent import brief as briefs
from agent.auth import User, current_user
from agent.learn import update_taste, vectors
from agent.models import Gender, MustHave, Product, Rule, ShoppingBrief
from agent import sessions as saved
from agent.sessions import SessionDoc, Store, UserDoc, default_store
from agent.sources import shopify
from data_prep.simulate import ONE_SIZE

log = logging.getLogger(__name__)

KEEPALIVE_S = 15.0
MAX_RULES = 20
MAX_HISTORY = 50
MAX_BASE64_CHARS = 12_000_000
LANGUAGE_PATTERN = r"^[A-Za-z-]{2,16}$"
NOT_FOUND_SESSION = "We couldn't find that search. Please start a new one."
NOT_FOUND_PRODUCT = "We couldn't find that product."
NOT_FOUND_CHAT = "We couldn't find that chat."
FRESH_RESULTS = timedelta(hours=24)
WELCOME_BACK_AFTER = timedelta(hours=24)
BUSY = "Our AI is busy right now. Please try again in a moment."
STORE_BUSY = "We couldn't reach the store right now. Please try again in a moment."
LIVE_NEEDS_SEARCH = "Please open live store products from a search."
NO_LONGER_AVAILABLE = "This product isn't available at the store any more."
LOOKS_CATALOG_ONLY = "Complete the look works on our catalogue's products for now."
PROFILE_CACHE = "public, max-age=3600"


@dataclass
class Backend:
    store: Store
    services: workflow.Services
    brief_client: Any = None
    photo_client: Any = None
    read_link: Callable[[str], tuple[bytes, str]] = workflow.fetch_link
    now: Callable[[], datetime | None] = field(default=lambda: None)
    live: workflow.LiveServices | None = None
    catalog: browse.CatalogIndex | None = None
    look_cache: verify.CheckCache | None = None


@cache
def default_backend() -> Backend:
    services = workflow.default_services()
    store = default_store()
    catalog = browse.CatalogIndex(browse.firestore_loader(store.db))
    return Backend(
        store=store, services=services, live=workflow.default_live(services), catalog=catalog,
        look_cache=verify.FirestoreCache(outfit.COLLECTION, client=store.db),
    )


def backend_of(request: Request) -> Backend:
    return request.app.state.backend or default_backend()


# ---------- Request and response bodies ----------


class BriefRequest(BaseModel):
    text: str | None = Field(default=None, max_length=briefs.MAX_REQUEST_CHARS)
    image_url: str | None = Field(default=None, max_length=2000)
    image_base64: str | None = Field(default=None, max_length=MAX_BASE64_CHARS)
    image_mime_type: str | None = Field(default=None, max_length=50)
    pincode: str | None = Field(default=None, max_length=12, description="None: the saved pincode, if any")
    language: str = Field(default="en", max_length=16)
    source: workflow.Source = "live"
    ask: bool = Field(default=True, description="False: never ask a question; search with what was said")
    chat_id: str | None = Field(default=None, max_length=64, description="The saved chat this is part of")
    memory: bool = Field(default=False, description="True: fill what is left open from the shopper's past chats")


class BriefResponse(BaseModel):
    session_id: str
    brief: ShoppingBrief
    question: str | None = None
    product: str | None = Field(default=None, description="The plain product name, None while the type is a guess")


class BriefUpdateRequest(BaseModel):
    session_id: str
    change: workflow.BriefChange
    ask: bool = True
    chat_id: str | None = Field(default=None, max_length=64, description="The saved chat this is part of")
    memory: bool = Field(default=False, description="True: a new product forked from this search gets its own memory")


class BriefUpdateResponse(BaseModel):
    session_id: str = Field(description="Another search when the change moved the chat to another product")
    brief: ShoppingBrief
    product: str | None = None


class SearchRequest(BaseModel):
    session_id: str


class RuleIn(BaseModel):
    id: str = ""
    attribute: str = Field(max_length=40)
    rule: Rule
    value: str | bool | float
    from_rule: bool = True


class RulesBody(BaseModel):
    rules: list[RuleIn] = Field(max_length=MAX_RULES)


class RulesResponse(BaseModel):
    rules: list[MustHave]


class FeedbackRequest(BaseModel):
    session_id: str
    product_id: str
    action: Literal["like", "save", "dislike", "buy"]


class Ok(BaseModel):
    ok: bool = True


class ChatMessagesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[chats.ChatMessage] = Field(max_length=chats.MAX_MESSAGES)
    pending: str | None = Field(default=None, max_length=chats.MAX_TEXT)


class ChatList(BaseModel):
    chats: list[chats.ChatSummary]


class OpenChatRequest(BaseModel):
    product: str | None = Field(default=None, max_length=40, description="None: the product talked about last")


class ActiveProduct(BaseModel):
    product: str
    session_id: str
    brief: ShoppingBrief
    source: workflow.Source
    query_text: str
    results: dict | None = Field(description="The saved results when still fresh and from the catalogue, else None")


class OpenChatResponse(BaseModel):
    chat: chats.Chat
    welcome_back: bool
    active: ActiveProduct | None


class Me(BaseModel):
    name: str
    email: str
    pincode: str | None
    language: str | None
    rules: list[MustHave]
    has_taste: bool


class MeUpdate(BaseModel):
    pincode: str | None = Field(default=None, max_length=12)
    language: str | None = Field(default=None, pattern=LANGUAGE_PATTERN)


class LooksRequest(BaseModel):
    occasion: outfit.Occasion | None = Field(default=None, description="None: up to 3 looks for occasions that suit it")
    style: outfit.Style | None = None
    budget_max_inr: int | None = Field(default=None, gt=0, le=1_000_000, description="For the whole look, this product included")
    gender: Gender | None = Field(default=None, description="Who it's for, when the product is unisex")


class CheckoutRequest(BaseModel):
    product_id: str = Field(max_length=200)
    size: str | None = Field(default=None, max_length=40)
    session_id: str | None = Field(default=None, description="For a live product, whose pincode to use")


class CheckoutResponse(BaseModel):
    redirect_url: str


class OrderRequest(BaseModel):
    product_id: str = Field(max_length=200)
    size: str | None = Field(default=None, max_length=40)


class OrderResponse(BaseModel):
    order: purchases.Order


class OrderList(BaseModel):
    orders: list[purchases.Order]


# ---------- Helpers ----------


def bad_request(message: str) -> HTTPException:
    return HTTPException(400, message)


def checked_pincode(pincode: str) -> str:
    try:
        return delivery.lookup(pincode).pincode
    except delivery.UnknownPincode as error:
        raise bad_request(str(error)) from error


def own_session(backend: Backend, user: User, session_id: str) -> SessionDoc:
    session = backend.store.session(user.uid, session_id)
    if session is None:
        raise HTTPException(404, NOT_FOUND_SESSION)
    return session


def checked_chat_id(chat_id: str | None) -> str | None:
    if chat_id is not None and not chats.CHAT_ID.match(chat_id):
        raise HTTPException(404, NOT_FOUND_CHAT)
    return chat_id


def own_chat(backend: Backend, user: User, chat_id: str | None) -> chats.ChatDoc | None:
    """The shopper's chat, or None when there is no chat yet. Someone else's chat is not found."""
    if checked_chat_id(chat_id) is None:
        return None
    found = backend.store.chat(chat_id)
    if found is not None and found.user_id != user.uid:
        raise HTTPException(404, NOT_FOUND_CHAT)
    return found


def keep_in_chat(backend: Backend, user: User, chat_id: str | None, session: SessionDoc):
    """Save this session as its product's entry in the chat. A guessed product type is never saved.

    The chat is read again here, not at the start of the request: the browser saves the shopper's message while
    Gemini is still reading it, and writing an older copy would lose that message.
    """
    if chat_id is None:
        return
    current = own_chat(backend, user, chat_id)
    name = chats.product_of(session.brief)
    if name:
        filters = chats.chat_filters(session.brief, session.source)
        backend.store.save_chat_product(user.uid, chat_id, current, name, filters, session.id, session.brief.remembered)
    elif current is None:
        backend.store.create_chat(user.uid, chat_id)


def today(backend: Backend):
    return briefs.today_in_india(backend.now())


def decode_base64(text: str) -> bytes:
    payload = text.split(",", 1)[1] if text.startswith("data:") else text
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise bad_request("We couldn't read that photo. Please try another one.") from error


def me_of(user: User, profile: UserDoc) -> Me:
    return Me(
        name=profile.name or user.name,
        email=profile.email or user.email,
        pincode=profile.pincode,
        language=profile.language,
        rules=profile.rules,
        has_taste=bool(profile.taste),
    )


def without_question(brief: ShoppingBrief, ask: bool) -> ShoppingBrief:
    return brief if ask else brief.model_copy(update={"question": None})


def catalog_of(backend: Backend) -> browse.CatalogIndex:
    if backend.catalog is None:
        backend.catalog = browse.CatalogIndex(browse.firestore_loader(backend.store.db))
    return backend.catalog


def sse(event: workflow.Event) -> str:
    return f"event: {event.kind}\ndata: {event.model_dump_json()}\n\n"


# ---------- Endpoints ----------

router = APIRouter()


@router.get("/healthz")
@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/.well-known/ucp")
def ucp_profile() -> JSONResponse:
    return JSONResponse(shopify.agent_profile(), headers={"Cache-Control": PROFILE_CACHE})


@router.post("/api/brief", response_model=BriefResponse)
def make_brief(body: BriefRequest, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    given = checked_pincode(body.pincode) if body.pincode else None
    own_chat(backend, user, body.chat_id)  # someone else's chat fails here, before Gemini is asked
    has_text = bool((body.text or "").strip())
    if not has_text and not body.image_base64 and not body.image_url:
        raise bad_request("Tell us what you're looking for, or add a photo.")
    store = backend.store
    profile = store.user(user.uid)
    store.remember_profile(user.uid, user.name, user.email, profile)
    # One prompt is enough: without a pincode the search goes on, just without delivery dates.
    pincode = given or profile.pincode

    photo_bytes: tuple[bytes, str] | None = None
    try:
        if body.image_base64:
            data = decode_base64(body.image_base64)
            photo_bytes = data, workflow.decode_photo(body.image_mime_type, data)
        elif body.image_url:
            photo_bytes = backend.read_link(body.image_url)
        kind = photo_kind(backend, photo_bytes, has_text) if photo_bytes else None
        text = workflow.request_text(body.text, kind)
    except workflow.PhotoProblem as error:
        raise bad_request(str(error)) from error

    try:
        outcome = workflow.with_fallback(
            lambda settings: briefs.make_brief_detailed(
                text, today=today(backend), pincode=pincode, language_hint=body.language, rules=profile.rules,
                client=backend.brief_client, settings=settings,
            )
        )
    except briefs.BriefError as error:
        log.warning("brief failed: %s", error)
        raise HTTPException(503, BUSY) from error
    except ValueError as error:
        raise bad_request(str(error)) from error

    photo = body.image_url if photo_bytes and not body.image_base64 else None
    if body.image_base64 and photo_bytes:
        try:
            photo = store.upload_photo(user.uid, *photo_bytes)
        except Exception as error:
            log.exception("photo upload failed")
            raise HTTPException(503, "We couldn't save your photo. Please try again.") from error
    brief = without_question(outcome.brief, body.ask).model_copy(update={"reference_image_url": photo})
    if body.memory:
        brief = remembered_for(backend, user, brief, body.chat_id)
    session = store.new_session(user.uid, brief, query_text=text, rules=profile.rules, photo=photo, source=body.source)
    if given and not profile.pincode:
        store.update_user(user.uid, {"pincode": given})
    keep_in_chat(backend, user, body.chat_id, session)
    return BriefResponse(session_id=session.id, brief=brief, question=brief.question, product=chats.product_of(brief))


def remembered_for(backend: Backend, user: User, brief: ShoppingBrief, chat_id: str | None) -> ShoppingBrief:
    """The brief with what the shopper left open filled from their other chats about this product."""
    name = chats.product_of(brief)
    if name is None:
        return brief
    past = backend.store.past_products(user.uid, name, except_chat=chat_id)
    return memory.fill(brief, memory.suggest(past, saved.now_utc()))


def photo_kind(backend: Backend, photo: tuple[bytes, str], has_text: bool) -> workflow.PhotoKind | None:
    try:
        return workflow.photo_kind(*photo, client=backend.photo_client or backend.brief_client)
    except workflow.PhotoBusy as error:
        if has_text:
            log.warning("photo kind skipped, going on with the words: %s", error)
            return None
        raise HTTPException(503, "We couldn't look at your photo right now. Please try again.") from error


@router.post("/api/brief/update", response_model=BriefUpdateResponse)
def update_brief(body: BriefUpdateRequest, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    chat = own_chat(backend, user, body.chat_id)
    session = own_session(backend, user, body.session_id)
    try:
        edited = apply_to(backend, user, session, body)
        if body.chat_id and moved_product(session, edited):
            session = move_to_product(backend, user, chat, session, edited, body)
        else:
            save_edit(backend, session, edited, body)
    except briefs.BriefError as error:
        log.warning("brief update failed: %s", error)
        raise HTTPException(503, BUSY) from error
    except ValueError as error:
        raise bad_request(str(error)) from error
    keep_in_chat(backend, user, body.chat_id, session)
    return BriefUpdateResponse(session_id=session.id, brief=session.brief, product=chats.product_of(session.brief))


def apply_to(backend: Backend, user: User, session: SessionDoc, body: BriefUpdateRequest) -> workflow.Edited:
    """The edit applied to the session's brief. What came from memory stays remembered only while it is unchanged."""
    if isinstance(body.change, workflow.SetMemory):
        brief = memory.forget(session.brief)
        if body.change.enabled:
            brief = remembered_for(backend, user, brief, body.chat_id)
        return workflow.Edited(brief=brief, rules=session.rules, disabled=set(session.disabled_rules))
    edited = workflow.apply_change(
        session.brief, body.change, rules=session.rules, disabled=set(session.disabled_rules),
        today=today(backend), client=backend.brief_client,
    )
    edited.brief = memory.carry(session.brief, edited.brief)
    return edited


def save_edit(backend: Backend, session: SessionDoc, edited: workflow.Edited, body: BriefUpdateRequest) -> None:
    session.brief = without_question(edited.brief, body.ask)
    session.rules = edited.rules
    session.disabled_rules = sorted(edited.disabled)
    if edited.words:
        session.query_text = ". ".join(part for part in (session.query_text, edited.words) if part)
    if edited.source:
        session.source = edited.source
    session.history = [*session.history, {"change": body.change.model_dump(mode="json")}][-MAX_HISTORY:]
    backend.store.update_session(session, ["brief", "rules", "disabled_rules", "query_text", "history", "source"])


def moved_product(session: SessionDoc, edited: workflow.Edited) -> bool:
    """True when a typed change made the chat talk about another product, and neither type is only a guess."""
    old, new = session.brief, edited.brief
    return old.category != new.category and chats.product_of(old) is not None and chats.product_of(new) is not None


def move_to_product(
    backend: Backend, user: User, chat: chats.ChatDoc | None, session: SessionDoc, edited: workflow.Edited,
    body: BriefUpdateRequest,
) -> SessionDoc:
    """The product's own search when the chat has one (the same change is applied and saved there), else a new
    search forked from this one, which stays as it was."""
    name = chats.product_of(edited.brief)
    entry = next((e for e in chat.products if e.product == name), None) if chat else None
    existing = backend.store.session(user.uid, entry.session_id) if entry else None
    if existing is not None:
        save_edit(backend, existing, apply_to(backend, user, existing, body), body)
        return existing
    brief = memory.forget(edited.brief)  # the old product's remembered values must not ride along
    if body.memory:
        brief = remembered_for(backend, user, brief, body.chat_id)
    return backend.store.new_session(
        user.uid, without_question(brief, body.ask), query_text=edited.words, rules=edited.rules, photo=None,
        source=edited.source or session.source, disabled_rules=sorted(edited.disabled),
        history=[{"change": body.change.model_dump(mode="json")}],
    )


@router.post("/api/search")
async def search(body: SearchRequest, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    session = await run_in_threadpool(own_session, backend, user, body.session_id)
    profile = await run_in_threadpool(backend.store.user, user.uid)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[workflow.Event | None] = asyncio.Queue()

    def put(event: workflow.Event | None) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            pass

    def work() -> None:
        try:
            run_search(backend, session, profile, put)
        except Exception as error:
            log.exception("search failed")
            put(workflow.friendly_error(error))
        finally:
            put(None)

    threading.Thread(target=work, name=f"search-{session.id[:8]}", daemon=True).start()

    async def stream() -> AsyncIterator[str]:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                return
            yield sse(event)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


def run_search(backend: Backend, session: SessionDoc, profile: UserDoc, emit: workflow.Emit) -> None:
    """Search, then save the results; the shopper's photo is deleted once its embedding is saved."""
    if session.source == "live":
        run_live_search(backend, session, profile, emit)
        return
    store = backend.store
    photo = None
    if session.photo or session.photo_vector:
        photo = workflow.PhotoSearch(session.photo, lambda link: read_photo(backend, link), session.photo_vector)
    outcome = workflow.search(
        session.brief, query_text=session.query_text, services=backend.services, taste=profile.taste, photo=photo,
        emit=emit, now=backend.now(),
    )
    session.results = outcome.done.model_dump(mode="json")
    session.timings = outcome.timings
    names = ["results", "timings"]
    if outcome.photo_vector is not None and session.photo_vector is None:
        session.photo_vector = outcome.photo_vector
        names.append("photo_vector")
        if store.is_upload(session.photo):
            try:
                store.delete_photo(session.photo)
            except Exception:
                log.exception("uploaded photo not deleted")
            session.photo = None
            names.append("photo")
    store.update_session(session, names)


def run_live_search(backend: Backend, session: SessionDoc, profile: UserDoc, emit: workflow.Emit) -> None:
    """A live search keeps only our verdicts and the Shopify ids it showed (Shopify's terms forbid caching results)."""
    outcome = workflow.live_search(
        session.brief, query_text=session.query_text, services=backend.services, live=live_of(backend),
        taste=profile.taste, emit=emit,
    )
    session.results = workflow.live_summary(outcome.done)
    session.timings = outcome.timings
    session.live_ids = outcome.live_ids
    backend.store.update_session(session, ["results", "timings", "live_ids"])


def live_of(backend: Backend) -> workflow.LiveServices:
    if backend.live is None:
        raise RuntimeError("live store search isn't set up")
    return backend.live


def read_photo(backend: Backend, link: str) -> tuple[bytes, str]:
    if backend.store.is_upload(link):
        return backend.store.read_photo(link)
    return backend.read_link(link)


def live_product(backend: Backend, session: SessionDoc, product_id: str) -> Product:
    """The live product as its store shows it now, fetched fresh from Shopify."""
    try:
        return live_of(backend).catalog.get_product(product_id, session.brief)
    except shopify.CatalogError as error:
        raise store_problem(error) from error
    except ValueError as error:
        raise HTTPException(404, NO_LONGER_AVAILABLE) from error


@router.post("/api/products/{product_id:path}/looks", response_model=outfit.Looks)
def looks(
    product_id: str, body: LooksRequest, user: User = Depends(current_user), backend: Backend = Depends(backend_of)
):
    if shopify.is_live_id(product_id):
        raise bad_request(LOOKS_CATALOG_ONLY)
    anchor = backend.store.product(product_id)
    if anchor is None:
        raise HTTPException(404, NOT_FOUND_PRODUCT)
    gender = anchor.gender if anchor.gender in ("men", "women") else body.gender
    available = {c.name for c in catalog_of(backend).categories(gender if gender in ("men", "women") else None)}
    services = backend.services
    try:
        return outfit.complete_look(
            anchor, client=services.check_client, db=services.db, embedder=services.embedder,
            settings=services.check_settings, cache=backend.look_cache, occasion=body.occasion, style=body.style,
            budget=body.budget_max_inr, gender=body.gender, taste=vectors(backend.store.user(user.uid).taste),
            available=available,
        )
    except outfit.LookFailed as error:
        log.warning("look for %s failed: %s", product_id, error)
        raise HTTPException(503, BUSY) from error


@router.get("/api/products/{product_id:path}")
def product(
    product_id: str, session_id: str | None = None, user: User = Depends(current_user),
    backend: Backend = Depends(backend_of),
):
    if shopify.is_live_id(product_id):
        if not session_id:
            raise bad_request(LIVE_NEEDS_SEARCH)
        session = own_session(backend, user, session_id)
        found = live_product(backend, session, product_id)
        ticks = workflow.saved_ticks(session.brief, found, backend.services.check_cache)
        return JSONResponse(cards.product_view(found, ticks))
    found = backend.store.product(product_id)
    if found is None:
        raise HTTPException(404, NOT_FOUND_PRODUCT)
    ticks = None
    if session_id:
        session = own_session(backend, user, session_id)
        ticks = workflow.saved_ticks(session.brief, found, backend.services.check_cache)
    return JSONResponse(cards.product_view(found, ticks))


@router.get("/api/catalog", response_model=browse.CatalogPage)
def catalog(
    q: str = Query(default="", max_length=browse.MAX_QUERY_CHARS),
    category: str | None = Query(default=None, max_length=60),
    gender: browse.GenderFilter | None = None,
    max_price: int | None = Query(default=None, gt=0),
    min_rating: float | None = Query(default=None, ge=1, le=5),
    sort: browse.Sort = "relevance",
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=browse.PAGE_SIZE, ge=1, le=browse.MAX_PAGE_SIZE),
    user: User = Depends(current_user),
    backend: Backend = Depends(backend_of),
):
    return catalog_of(backend).search(
        q, category=category, gender=gender, max_price=max_price, min_rating=min_rating, sort=sort, page=page,
        page_size=page_size,
    )


@router.get("/api/catalog/categories", response_model=list[browse.CategoryCount])
def catalog_categories(
    gender: browse.GenderFilter | None = None, user: User = Depends(current_user), backend: Backend = Depends(backend_of)
):
    return catalog_of(backend).categories(gender)


@router.get("/api/rules", response_model=RulesResponse)
def get_rules(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    return RulesResponse(rules=backend.store.user(user.uid).rules)


@router.put("/api/rules", response_model=RulesResponse)
def put_rules(body: RulesBody, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    try:
        items = [MustHave(id=item.id, attribute=item.attribute, rule=item.rule, value=item.value) for item in body.rules]
        rules = workflow.saved_rules(items)
    except ValueError as error:
        raise bad_request(str(error)) from error
    backend.store.update_user(user.uid, {"rules": rules})
    return RulesResponse(rules=rules)


@router.post("/api/feedback", response_model=Ok)
def feedback(body: FeedbackRequest, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    store = backend.store
    session = own_session(backend, user, body.session_id)
    liked = body.action != "dislike"
    live = shopify.is_live_id(body.product_id)
    found = None if live else store.product(body.product_id, with_embedding=liked)
    if found is None and not live:
        raise HTTPException(404, NOT_FOUND_PRODUCT)
    product_id = found.id if found else body.product_id
    profile = store.user(user.uid)
    likes = [p for p in profile.likes if p != product_id]
    dislikes = [p for p in profile.dislikes if p != product_id]
    (likes if liked else dislikes).append(product_id)
    fields: dict[str, Any] = {"likes": likes, "dislikes": dislikes}
    if liked and found and found.embedding:
        try:
            fields["taste"] = update_taste(profile.taste, found.article_type, found.embedding, body.action)
        except ValueError as error:
            log.warning("product %s: style not updated: %s", found.id, error)
    store.update_user(user.uid, fields)
    session.feedback = [*session.feedback, {"product_id": product_id, "action": body.action}]
    store.update_session(session, ["feedback"])
    return Ok()


@router.get("/api/me", response_model=Me)
def get_me(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    profile = backend.store.user(user.uid)
    backend.store.remember_profile(user.uid, user.name, user.email, profile)
    return me_of(user, profile)


@router.put("/api/me", response_model=Me)
def put_me(body: MeUpdate, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    fields: dict[str, Any] = {}
    if body.pincode is not None:
        fields["pincode"] = checked_pincode(body.pincode)
    if body.language is not None:
        fields["language"] = body.language.lower()
    if fields:
        backend.store.update_user(user.uid, fields)
    profile = backend.store.user(user.uid)
    backend.store.remember_profile(user.uid, user.name, user.email, profile)
    return me_of(user, profile)


@router.delete("/api/me", response_model=Ok)
def delete_me(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    backend.store.delete_user(user.uid)
    return Ok()


@router.delete("/api/me/taste", response_model=Ok)
def clear_taste(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    backend.store.update_user(user.uid, {"taste": {}})
    return Ok()


@router.post("/api/checkout", response_model=CheckoutResponse)
def checkout(body: CheckoutRequest, user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    if shopify.is_live_id(body.product_id):
        return CheckoutResponse(redirect_url=live_checkout(backend, user, body))
    found = backend.store.product(body.product_id)
    if found is None:
        raise HTTPException(404, NOT_FOUND_PRODUCT)
    size = checked_size(found, body.size)
    return CheckoutResponse(redirect_url=delivery.buy_link(found.id, size))


def checked_size(found: Product, wanted: str | None) -> str | None:
    if not found.sizes or list(found.sizes) == [ONE_SIZE]:
        return None
    size = " ".join((wanted or "").split())
    if not size:
        raise bad_request("Please pick a size.")
    if size not in found.sizes:
        raise bad_request("That size isn't available for this product.")
    if found.sizes[size] <= 0:
        raise bad_request("That size is out of stock.")
    return size


def live_checkout(backend: Backend, user: User, body: CheckoutRequest) -> str:
    """The store's own checkout (or product page) link, fresh from Shopify; we never handle payment."""
    if body.session_id:
        pincode = own_session(backend, user, body.session_id).brief.pincode
    else:
        pincode = backend.store.user(user.uid).pincode
    size = " ".join((body.size or "").split()) or None
    try:
        return live_of(backend).catalog.checkout_link(body.product_id, pincode, size)
    except shopify.SizeUnavailable as error:
        raise bad_request("That size isn't available at the store right now.") from error
    except shopify.CatalogError as error:
        raise store_problem(error) from error
    except ValueError as error:
        raise HTTPException(404, NO_LONGER_AVAILABLE) from error


# ---------- Demo orders: only "Place order" in our demo store counts as a purchase ----------


def warm_look(backend: Backend, user: User, anchor: Product) -> None:
    """Make the product's looks ready for the suggestions rail. Never raises: it runs after the answer is sent."""
    try:
        look_around(backend, user, anchor)
    except Exception as error:
        log.warning("looks for order %s not warmed: %s", anchor.id, type(error).__name__)


def look_around(backend: Backend, user: User, anchor: Product, gender: Gender | None = None) -> outfit.Looks:
    """Complete the look, as POST /api/products/{id}/looks does with no options."""
    named = anchor.gender if anchor.gender in ("men", "women") else gender
    available = {c.name for c in catalog_of(backend).categories(named if named in ("men", "women") else None)}
    services = backend.services
    return outfit.complete_look(
        anchor, client=services.check_client, db=services.db, embedder=services.embedder,
        settings=services.check_settings, cache=backend.look_cache, gender=gender,
        taste=vectors(backend.store.user(user.uid).taste), available=available,
    )


@router.post("/api/orders", response_model=OrderResponse)
def place_order(
    body: OrderRequest, background: BackgroundTasks, user: User = Depends(current_user),
    backend: Backend = Depends(backend_of),
):
    if shopify.is_live_id(body.product_id):
        raise bad_request("Demo orders work on our catalogue's products only.")
    found = backend.store.product(body.product_id)
    if found is None:
        raise HTTPException(404, NOT_FOUND_PRODUCT)
    size = checked_size(found, body.size)
    moment = saved.now_utc()
    order = purchases.Order(
        id=uuid.uuid4().hex, product_id=found.id, title=found.title, article_type=found.article_type,
        gender=found.gender, base_colour=found.base_colour, usage=found.usage, size=size,
        price_inr=found.price.selling if found.price else None, ordered_at=moment,
    )
    orders = purchases.kept([*backend.store.user(user.uid).orders, order], moment)
    backend.store.update_user(user.uid, {"orders": orders})
    background.add_task(warm_look, backend, user, found)
    return OrderResponse(order=order)


@router.get("/api/orders", response_model=OrderList)
def list_orders(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    orders = backend.store.user(user.uid).orders
    return OrderList(orders=sorted(orders, key=lambda o: purchases.as_utc(o.ordered_at), reverse=True))


@router.delete("/api/orders", response_model=Ok)
def clear_orders(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    backend.store.update_user(user.uid, {"orders": []})
    return Ok()


@router.get("/api/suggestions/from-orders", response_model=purchases.OrderSuggestions)
def suggestions_from_orders(
    gender: Gender | None = None, user: User = Depends(current_user), backend: Backend = Depends(backend_of)
):
    profile = backend.store.user(user.uid)
    now = saved.now_utc()

    def look(order: purchases.Order) -> list[outfit.Suggestion] | None:
        anchor = backend.store.product(order.product_id)
        if anchor is None:
            return None
        try:
            found = look_around(backend, user, anchor, gender)
        except outfit.LookFailed as error:
            log.warning("look for %s failed: %s", order.product_id, error)
            return None
        return [suggestion for row in found.rows for suggestion in row.items]

    return purchases.from_orders(profile.orders, profile.dislikes, now, look)


def store_problem(error: shopify.CatalogError) -> HTTPException:
    if error.status in (400, 404, 422):
        return HTTPException(404, NOT_FOUND_PRODUCT)
    log.warning("live store data not loaded: %s", error)
    return HTTPException(503, STORE_BUSY)


@router.put("/api/chats/{chat_id}/messages", response_model=Ok)
def save_chat_messages(
    chat_id: str, body: ChatMessagesBody, user: User = Depends(current_user), backend: Backend = Depends(backend_of)
):
    chat = own_chat(backend, user, chat_id)
    backend.store.save_chat_messages(user.uid, chat_id, chat, body.messages, body.pending)
    return Ok()


@router.get("/api/chats", response_model=ChatList)
def list_chats(user: User = Depends(current_user), backend: Backend = Depends(backend_of)):
    found = backend.store.chats(user.uid)
    return ChatList(
        chats=[
            chats.ChatSummary(
                chat_id=c.chat_id, title=c.title, timestamps=c.timestamps, products=[p.product for p in c.products]
            )
            for c in found
        ]
    )


@router.post("/api/chats/{chat_id}/open", response_model=OpenChatResponse)
def open_chat(
    chat_id: str, body: OpenChatRequest = OpenChatRequest(), user: User = Depends(current_user),
    backend: Backend = Depends(backend_of),
):
    chat = own_chat(backend, user, chat_id)
    if chat is None:
        raise HTTPException(404, NOT_FOUND_CHAT)
    welcome_back = saved.now_utc() - chat.timestamps.last_opened_at > WELCOME_BACK_AFTER
    active = active_product(backend, user, chat, body.product, welcome_back)
    opened = backend.store.mark_chat_opened(chat)
    return OpenChatResponse(chat=opened.shown(), welcome_back=welcome_back, active=active)


def active_product(
    backend: Backend, user: User, chat: chats.ChatDoc, asked: str | None, welcome_back: bool
) -> ActiveProduct | None:
    """The product to carry on with: the one asked for, else the one talked about last. Its saved results come
    along only for a catalogue search made in the last day; live results are never kept, so those are searched again."""
    entries = sorted(chat.products, key=lambda e: e.timestamps.updated_at, reverse=True)
    entry = next((e for e in entries if e.product == asked), entries[0] if entries else None)
    session = backend.store.session(user.uid, entry.session_id) if entry else None
    if entry is None or session is None:
        return None
    fresh = session.source == "catalog" and saved.now_utc() - session.updated_at < FRESH_RESULTS and not welcome_back
    return ActiveProduct(
        product=entry.product, session_id=session.id, brief=session.brief, source=session.source,
        query_text=session.query_text, results=session.results if fresh else None,
    )


# ---------- The app ----------


async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    first = error.errors()[0] if error.errors() else {}
    where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "invalid request")
    return JSONResponse({"detail": f"Please check the request ({where}: {message})."}, status_code=400)


def allowed_origins(env: Mapping[str, str]) -> list[str]:
    return [origin.strip().rstrip("/") for origin in (env.get("ALLOWED_ORIGINS") or "").split(",") if origin.strip()]


def create_app(
    backend: Backend | None = None,
    *,
    env: Mapping[str, str] | None = None,
    verify_token: auth.TokenVerifier | None = None,
) -> FastAPI:
    env = os.environ if env is None else env
    mode = auth.auth_mode(env)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="EcommAI agent", version="0.1.0")
    app.state.auth = auth.Authenticator(mode, verify_token)
    app.state.backend = backend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(env),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Device-Id"],
        max_age=600,
    )
    app.add_exception_handler(RequestValidationError, validation_error)
    app.include_router(router)
    if mode == auth.DEV:
        log.warning("AUTH_MODE=dev: requests without a sign-in token act as the demo shopper")
    return app


app = create_app()
