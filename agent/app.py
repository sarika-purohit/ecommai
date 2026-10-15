"""The agent API for the app (PLAN.md appendix E): FastAPI calling the workflow functions directly.

Run locally (AUTH_MODE=dev lets requests without a sign-in token act as a demo shopper):

    AUTH_MODE=dev uvicorn agent.app:app --port 8080

Every /api/* endpoint needs `Authorization: Bearer <Firebase ID token>`. POST /api/search answers with server-sent
events (see agent/workflow.py for the order); comment lines (": ping") keep the stream open while products are
checked. Only the origins in ALLOWED_ORIGINS may call it from a browser.

GET /api/catalog lists and keyword-searches the demo catalogue for the store pages (agent/browse.py); it makes no
Gemini call. The AI chat sends `ask: false`, so a missing size or gender never holds the search up with a question.

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
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent import auth, browse, cards, delivery, workflow
from agent import brief as briefs
from agent.auth import User, current_user
from agent.learn import update_taste
from agent.models import MustHave, Product, Rule, ShoppingBrief
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
BUSY = "Our AI is busy right now. Please try again in a moment."
STORE_BUSY = "We couldn't reach the store right now. Please try again in a moment."
LIVE_NEEDS_SEARCH = "Please open live store products from a search."
NO_LONGER_AVAILABLE = "This product isn't available at the store any more."
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


@cache
def default_backend() -> Backend:
    services = workflow.default_services()
    store = default_store()
    catalog = browse.CatalogIndex(browse.firestore_loader(store.db))
    return Backend(store=store, services=services, live=workflow.default_live(services), catalog=catalog)


def backend_of(request: Request) -> Backend:
    return request.app.state.backend or default_backend()


# ---------- Request and response bodies ----------


class BriefRequest(BaseModel):
    text: str | None = Field(default=None, max_length=briefs.MAX_REQUEST_CHARS)
    image_url: str | None = Field(default=None, max_length=2000)
    image_base64: str | None = Field(default=None, max_length=MAX_BASE64_CHARS)
    image_mime_type: str | None = Field(default=None, max_length=50)
    pincode: str = Field(max_length=12)
    language: str = Field(default="en", max_length=16)
    source: workflow.Source = "live"
    ask: bool = Field(default=True, description="False: never ask a question; search with what was said")


class BriefResponse(BaseModel):
    session_id: str
    brief: ShoppingBrief
    question: str | None = None


class BriefUpdateRequest(BaseModel):
    session_id: str
    change: workflow.BriefChange
    ask: bool = True


class BriefUpdateResponse(BaseModel):
    brief: ShoppingBrief


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


class CheckoutRequest(BaseModel):
    product_id: str = Field(max_length=200)
    size: str | None = Field(default=None, max_length=40)
    session_id: str | None = Field(default=None, description="For a live product, whose pincode to use")


class CheckoutResponse(BaseModel):
    redirect_url: str


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
    pincode = checked_pincode(body.pincode)
    has_text = bool((body.text or "").strip())
    if not has_text and not body.image_base64 and not body.image_url:
        raise bad_request("Tell us what you're looking for, or add a photo.")
    store = backend.store
    profile = store.user(user.uid)
    store.remember_profile(user.uid, user.name, user.email, profile)

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
    session = store.new_session(user.uid, brief, query_text=text, rules=profile.rules, photo=photo, source=body.source)
    if not profile.pincode:
        store.update_user(user.uid, {"pincode": pincode})
    return BriefResponse(session_id=session.id, brief=brief, question=brief.question)


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
    session = own_session(backend, user, body.session_id)
    try:
        edited = workflow.apply_change(
            session.brief, body.change, rules=session.rules, disabled=set(session.disabled_rules),
            today=today(backend), client=backend.brief_client,
        )
    except briefs.BriefError as error:
        log.warning("brief update failed: %s", error)
        raise HTTPException(503, BUSY) from error
    except ValueError as error:
        raise bad_request(str(error)) from error
    session.brief = without_question(edited.brief, body.ask)
    session.rules = edited.rules
    session.disabled_rules = sorted(edited.disabled)
    if edited.words:
        session.query_text = ". ".join(part for part in (session.query_text, edited.words) if part)
    if edited.source:
        session.source = edited.source
    session.history = [*session.history, {"change": body.change.model_dump(mode="json")}][-MAX_HISTORY:]
    backend.store.update_session(session, ["brief", "rules", "disabled_rules", "query_text", "history", "source"])
    return BriefUpdateResponse(brief=session.brief)


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
    sort: browse.Sort = "relevance",
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=browse.PAGE_SIZE, ge=1, le=browse.MAX_PAGE_SIZE),
    user: User = Depends(current_user),
    backend: Backend = Depends(backend_of),
):
    return catalog_of(backend).search(
        q, category=category, gender=gender, max_price=max_price, sort=sort, page=page, page_size=page_size
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
    size = None
    if found.sizes and list(found.sizes) != [ONE_SIZE]:
        size = " ".join((body.size or "").split())
        if not size:
            raise bad_request("Please pick a size.")
        if size not in found.sizes:
            raise bad_request("That size isn't available for this product.")
        if found.sizes[size] <= 0:
            raise bad_request("That size is out of stock.")
    return CheckoutResponse(redirect_url=delivery.buy_link(found.id, size))


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


def store_problem(error: shopify.CatalogError) -> HTTPException:
    if error.status in (400, 404, 422):
        return HTTPException(404, NOT_FOUND_PRODUCT)
    log.warning("live store data not loaded: %s", error)
    return HTTPException(503, STORE_BUSY)


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
