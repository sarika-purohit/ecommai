"""Saved AI chats (PLAN.md appendix F): one `chats/{chat_id}` document per chat, so a shopper can reopen it later.

The chat is a readable flat JSON. Firestore stores the same fields plus `user_id`; the API sends timestamps as
ISO-8601 UTC strings ending in Z.

    chat_id, title (the first thing said), timestamps {created_at, updated_at, last_opened_at},
    messages (id, role, text, ...; no timestamps), pending (words kept until the product type is known),
    products: one entry per product the chat talked about, by plain name ("kurta", "t-shirt"):
        product, timestamps {created_at, updated_at}, filters, remembered, session_id

`filters` is written by the server from the session's ShoppingBrief, never taken from the browser: colour,
gender, size, the other must-haves, budget_max_inr, min_rating, deadline_date, prefer and source. A must-have
`is` shows as "attr": value, `is_not` as {"not": value}, `present`/`absent` as true/false, `at_most`/`at_least`
as {"at_most": n}/{"at_least": n}; several on one attribute become a list. The shopper's saved personal rules are
left out, because the brief code applies them fresh every time. `remembered` names the `filters` keys that memory mode
filled in from past chats (agent/memory.py) rather than the shopper's own words; their values are in `filters` too.
Reopening a chat uses the product's session, so it
needs no Gemini call.

A product's `updated_at` moves only when the shopper chats about that product. The server stamps every timestamp.
"""

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, SerializerFunctionWrapHandler, model_serializer

from agent.models import MustHave, ShoppingBrief
from data_prep import schemas

CHAT_ID = re.compile(r"^c_[0-9a-f]{32}$")
MAX_MESSAGES = 200
MAX_TEXT = 4000
MAX_TITLE = 80
KEEP_DAYS = 90
KEEP_CHATS = 30

# The catalogue's articleType in the plain words shoppers use.
PRODUCT_NAMES: dict[str, str] = {
    "Kurtas": "kurta",
    "Kurtis": "kurti",
    "Tunics": "tunic",
    "Shirts": "shirt",
    "Tshirts": "t-shirt",
    "Tops": "top",
    "Jeans": "jeans",
    "Trousers": "trousers",
    "Casual Shoes": "casual shoes",
    "Sports Shoes": "sports shoes",
    "Formal Shoes": "formal shoes",
    "Watches": "watch",
    "Handbags": "handbag",
    "Backpacks": "backpack",
    "Clutches": "clutch",
    "Sunglasses": "sunglasses",
}


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


Moment = Annotated[datetime, PlainSerializer(utc_text, return_type=str, when_used="json")]


class ChatTimestamps(BaseModel):
    created_at: Moment
    updated_at: Moment
    last_opened_at: Moment


class ProductTimestamps(BaseModel):
    created_at: Moment
    updated_at: Moment


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=40)
    role: Literal["user", "ai"]
    text: str = Field(max_length=MAX_TEXT)
    error: bool | None = None
    product: str | None = Field(default=None, max_length=40, description="The plain name of the product it was about")
    tradeoffs: list[dict] | None = Field(default=None, max_length=10)
    picks: list[str] | None = Field(default=None, max_length=30)

    @model_serializer(mode="wrap")
    def _only_what_is_set(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {name: value for name, value in handler(self).items() if value is not None}


class ChatProduct(BaseModel):
    product: str
    timestamps: ProductTimestamps
    filters: dict[str, Any]
    remembered: list[str] = Field(default_factory=list)
    session_id: str


class Chat(BaseModel):
    chat_id: str
    title: str = ""
    timestamps: ChatTimestamps
    messages: list[ChatMessage] = Field(default_factory=list)
    pending: str | None = None
    products: list[ChatProduct] = Field(default_factory=list)


class ChatDoc(Chat):
    """What Firestore holds: the chat and whose it is."""

    user_id: str

    def shown(self) -> Chat:
        return Chat.model_validate(self.model_dump(exclude={"user_id"}))


class ChatSummary(BaseModel):
    chat_id: str
    title: str
    timestamps: ChatTimestamps
    products: list[str]


def product_name(category: str) -> str | None:
    return PRODUCT_NAMES.get(category)


def product_of(brief: ShoppingBrief) -> str | None:
    """The plain product name, or None while the type is only a guess (it must never be saved)."""
    return None if "product" in brief.missing else product_name(brief.category)


def title_of(text: str) -> str:
    """The first thing the shopper said, cut at a word to about MAX_TITLE characters."""
    words = " ".join(text.split())
    if len(words) <= MAX_TITLE:
        return words
    return words[:MAX_TITLE].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _form(must_have: MustHave) -> Any:
    value = must_have.value
    match must_have.rule:
        case "is":
            return value
        case "is_not":
            return {"not": value}
        case "present" | "absent":
            return must_have.rule == "present"
        case "at_most":
            return {"at_most": _number(float(value))}
        case _:
            return {"at_least": _number(float(value))}


def _joined(forms: list[Any]) -> Any:
    return forms[0] if len(forms) == 1 else forms


def chat_filters(brief: ShoppingBrief, source: str) -> dict[str, Any]:
    """The brief as a product's `filters`: shopper-readable, in a fixed order, with unset fields left out."""
    own_size = schemas.group_for(brief.category) == "bags"
    forms: dict[str, list[Any]] = {}
    for must_have in brief.must_haves:
        if not must_have.from_rule:
            forms.setdefault(must_have.attribute, []).append(_form(must_have))
    filters: dict[str, Any] = {}
    if "colour" in forms:
        filters["colour"] = _joined(forms.pop("colour"))
    if brief.gender:
        filters["gender"] = brief.gender
    if own_size:
        if "size" in forms:
            filters["size"] = _joined(forms.pop("size"))
    elif brief.size:
        filters["size"] = brief.size
    for attribute, found in forms.items():
        filters[attribute] = _joined(found)
    if brief.budget_max_inr is not None:
        filters["budget_max_inr"] = brief.budget_max_inr
    if brief.min_rating is not None:
        filters["min_rating"] = _number(brief.min_rating)
    if brief.deadline_date is not None:
        filters["deadline_date"] = brief.deadline_date.isoformat()
    prefer: dict[str, list[Any]] = {}
    for nice in brief.nice_to_haves:
        prefer.setdefault(nice.attribute, []).append(nice.value)
    if prefer:
        filters["prefer"] = {attribute: _joined(values) for attribute, values in prefer.items()}
    filters["source"] = source
    return filters
