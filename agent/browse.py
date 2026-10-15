"""The store pages: list the demo catalogue and search it by keyword, like any shopping site.

No Gemini call and no Firestore read per request: the catalogue (about 3,000 products) is read once per server,
without embeddings or reviews, and kept in memory for REFRESH_S. Live Shopify products aren't listed here, since
Shopify's terms don't allow caching their search results; the AI search covers them.

A keyword search keeps the products whose words contain every word asked for, after taking out gender words
("men", "ladies") and price limits ("under 1500"), which become filters. When no product has every word, it keeps
the products with the most of them, so "red silk kurta" still shows red kurtas. A word that no product has at all
("saree") gives no results, and `understood.unmatched` names it so the app can offer the AI search.
"""

import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.models import Price

PRODUCTS = "products"
REFRESH_S = 3600.0
PAGE_SIZE = 24
MAX_PAGE_SIZE = 60
MAX_QUERY_CHARS = 200
FIELDS = [
    "title", "brand", "gender", "masterCategory", "subCategory", "articleType", "baseColour", "usage", "price",
    "images", "rating_avg", "rating_count",
]

Sort = Literal["relevance", "price_low", "price_high", "rating"]
GenderFilter = Literal["men", "women"]

GENDER_WORDS: dict[str, GenderFilter] = {
    "men": "men", "man": "men", "mens": "men", "male": "men", "gents": "men", "boys": "men", "boy": "men",
    "women": "women", "woman": "women", "womens": "women", "female": "women", "ladies": "women", "lady": "women",
    "girls": "women", "girl": "women",
}
STOP_WORDS = {
    "a", "an", "and", "the", "for", "of", "with", "in", "on", "to", "my", "me", "i", "want", "need", "show", "some",
    "rs", "inr", "rupees", "price", "ke", "liye", "ki", "ka", "chahiye", "wala", "wali", "wale",
}
# Spellings shoppers use for the dataset's product types, mapped to a word in the product's text.
ALIASES = {
    "tee": "tshirt", "tees": "tshirt", "t": "", "sneaker": "casual shoe", "sneakers": "casual shoe",
    "trainers": "sports shoe", "running": "sports", "spectacles": "sunglass", "shades": "sunglass",
    "goggles": "sunglass", "bag": "handbag", "bags": "handbag", "purse": "handbag", "denim": "jean",
    "pant": "trouser", "pants": "trouser", "kurtaa": "kurta",
}
PRICE_LIMIT = re.compile(r"\b(?:under|below|less than|upto|up to|within|max|<)\s*(?:rs\.?|₹|inr)?\s*(\d[\d,]*)\s*(k)?\b")
WORD = re.compile(r"[a-z0-9]+")


class CatalogItem(BaseModel):
    id: str
    title: str
    brand: str | None = None
    gender: str
    article_type: str
    base_colour: str | None = None
    usage: str | None = None
    price: Price | None = None
    thumb: str | None = None
    photo: str | None = None
    rating_avg: float | None = None
    rating_count: int = 0


class CategoryCount(BaseModel):
    name: str
    count: int


class CatalogPage(BaseModel):
    items: list[CatalogItem]
    total: int
    page: int
    page_size: int
    query: str = ""
    understood: dict[str, Any] = Field(
        default_factory=dict, description="Filters read from the words: gender, max_price, and words not matched"
    )
    categories: list[CategoryCount] = Field(default_factory=list, description="Product types in these results")
    partial: bool = Field(default=False, description="True when no product had every word, so the closest are shown")


def stem(word: str) -> str:
    """Plural and singular meet: kurtas -> kurta, watches -> watch, shoes -> shoe."""
    if len(word) > 4 and word.endswith("es") and word[-3] in "sxz":
        return word[:-2]
    if len(word) > 4 and word.endswith("ches"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    return WORD.findall(text.lower().replace("t-shirt", "tshirt").replace("t shirt", "tshirt"))


class Entry:
    __slots__ = ("item", "stems", "type_stems")

    def __init__(self, item: CatalogItem):
        self.item = item
        text = " ".join(filter(None, [item.title, item.brand, item.article_type, item.base_colour, item.usage]))
        self.stems = {stem(word) for word in words(text)}
        self.type_stems = {stem(word) for word in words(item.article_type)}


def item_from(doc_id: str, data: dict) -> CatalogItem | None:
    try:
        images = data.get("images") or {}
        return CatalogItem(
            id=doc_id,
            title=data["title"],
            brand=data.get("brand"),
            gender=str(data.get("gender") or "unisex").lower(),
            article_type=data["articleType"],
            base_colour=data.get("baseColour"),
            usage=data.get("usage"),
            price=Price.model_validate(data["price"]) if data.get("price") else None,
            thumb=images.get("thumb"),
            photo=images.get("gemini"),
            rating_avg=data.get("rating_avg"),
            rating_count=int(data.get("rating_count") or 0),
        )
    except (KeyError, ValueError, TypeError):
        return None


def read_query(query: str) -> tuple[list[str], GenderFilter | None, int | None]:
    """The words to match, plus a gender and a price limit when the shopper typed them."""
    text = " ".join(query.lower().split())[:MAX_QUERY_CHARS]
    max_price = None
    match = PRICE_LIMIT.search(text)
    if match:
        max_price = int(match[1].replace(",", "")) * (1000 if match[2] else 1)
        text = text[: match.start()] + " " + text[match.end() :]
    gender = None
    wanted: list[str] = []
    for word in words(text):
        if word in GENDER_WORDS:
            gender = gender or GENDER_WORDS[word]
            continue
        for part in ALIASES.get(word, word).split():
            if part not in STOP_WORDS and not part.isdigit():
                wanted.append(stem(part))
    return list(dict.fromkeys(wanted)), gender, max_price


class CatalogIndex:
    """Every demo product's card, in memory; `load` reads them all (one Firestore query)."""

    def __init__(self, load: Callable[[], Iterable[CatalogItem]], *, refresh_s: float = REFRESH_S,
                 clock: Callable[[], float] = time.monotonic):
        self._load = load
        self._refresh_s = refresh_s
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[Entry] | None = None
        self._loaded_at = 0.0

    def entries(self) -> list[Entry]:
        with self._lock:
            if self._entries is None or self._clock() - self._loaded_at > self._refresh_s:
                self._entries = [Entry(item) for item in self._load()]
                self._entries.sort(key=lambda entry: entry.item.id)
                self._loaded_at = self._clock()
            return self._entries

    def categories(self, gender: GenderFilter | None = None) -> list[CategoryCount]:
        return _counts(entry for entry in self.entries() if _gender_ok(entry, gender))

    def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        gender: GenderFilter | None = None,
        max_price: int | None = None,
        sort: Sort = "relevance",
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> CatalogPage:
        wanted, said_gender, said_price = read_query(query)
        gender = gender or said_gender
        max_price = max_price or said_price
        pool = [
            entry for entry in self.entries()
            if _gender_ok(entry, gender)
            and (max_price is None or (entry.item.price is not None and entry.item.price.selling <= max_price))
        ]
        unmatched = [word for word in wanted if not any(word in entry.stems for entry in pool)]
        # A word no product has (say "saree") means we don't carry it; closest matches would only mislead.
        scored, partial = ([], False) if unmatched else _match(pool, wanted)
        categories = _counts(entry for entry, _ in scored)
        if category:
            scored = [(entry, score) for entry, score in scored if entry.item.article_type == category]
        ordered = _sorted(scored, sort, has_words=bool(wanted))
        page = max(page, 1)
        page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        start = (page - 1) * page_size
        understood: dict[str, Any] = {}
        if gender:
            understood["gender"] = gender
        if max_price:
            understood["max_price"] = max_price
        if unmatched:
            understood["unmatched"] = unmatched
        return CatalogPage(
            items=[entry.item for entry in ordered[start : start + page_size]],
            total=len(ordered),
            page=page,
            page_size=page_size,
            query=query.strip(),
            understood=understood,
            categories=categories,
            partial=partial,
        )


def _gender_ok(entry: Entry, gender: GenderFilter | None) -> bool:
    return gender is None or entry.item.gender in (gender, "unisex")


def _match(pool: list[Entry], wanted: list[str]) -> tuple[list[tuple[Entry, float]], bool]:
    """(entry, score) for products with every word; failing that, those with the most words. Type words count more."""
    if not wanted:
        return [(entry, 0.0) for entry in pool], False

    def score(entry: Entry) -> tuple[int, float]:
        hits = [word for word in wanted if word in entry.stems]
        return len(hits), len(hits) + sum(0.5 for word in hits if word in entry.type_stems)

    scored = [(entry, *score(entry)) for entry in pool]
    full = [(entry, value) for entry, hits, value in scored if hits == len(wanted)]
    if full:
        return full, False
    best = max((hits for _, hits, _ in scored), default=0)
    if best == 0:
        return [], False
    return [(entry, value) for entry, hits, value in scored if hits == best], True


def _sorted(scored: list[tuple[Entry, float]], sort: Sort, *, has_words: bool) -> list[Entry]:
    def price(entry: Entry) -> int:
        return entry.item.price.selling if entry.item.price else 10**9

    def key(pair: tuple[Entry, float]) -> tuple:
        entry, score = pair
        item = entry.item
        if sort == "price_low":
            return price(entry), item.id
        if sort == "price_high":
            return -price(entry), item.id
        if sort == "rating":
            return -(item.rating_avg or 0), -item.rating_count, item.id
        if has_words:
            return -score, item.id
        # The home page: mix product types instead of showing one type first.
        return _mixed_rank(entry), item.id

    return [entry for entry, _ in sorted(scored, key=key)]


def _mixed_rank(entry: Entry) -> int:
    return int(entry.item.id) % 97 if entry.item.id.isdigit() else 97


def _counts(entries: Iterable[Entry]) -> list[CategoryCount]:
    counts = Counter(entry.item.article_type for entry in entries)
    return [CategoryCount(name=name, count=count) for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def firestore_loader(db: Any, collection: str = PRODUCTS) -> Callable[[], list[CatalogItem]]:
    def load() -> list[CatalogItem]:
        snapshots = db.collection(collection).select(FIELDS).stream()
        return [item for snapshot in snapshots if (item := item_from(snapshot.id, snapshot.to_dict() or {}))]

    return load
