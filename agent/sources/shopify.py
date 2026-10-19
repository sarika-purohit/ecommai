"""Live products from Shopify's Global Catalog MCP: the UCP catalog capability, Shopify's official channel for AI agents.

Each request is one JSON-RPC `tools/call` to ENDPOINT with our UCP agent profile in `meta.ucp-agent.profile` (no API
key). `search` finds products that ship to the shopper's pincode from Indian stores; `get_product` fetches one product
fresh for the product page, its sizes and checkout. https://shopify.dev/docs/agents/catalog/global-catalog

Shopify's catalog rules (https://shopify.dev/docs/agents/catalog, "Usage guidelines") shape this module:
- search results and images are never cached: callers keep only product ids, the product page and checkout fetch
  fresh data, and photos reach Gemini and the app as Shopify CDN links, never downloaded by us;
- product and checkout links are used exactly as Shopify returns them (they carry Shopify's attribution);
- metadata (features, specs, selling points) is inferred by Shopify's AI, so it's a signal, never proof
  (agent/verify.py); the description is the store's listing, which Shopify may have enriched;
- one product's bad data is logged and left out, never failing the search.

Shopify groups the same product from several stores under one product id, so our id for a live product names the
store too ('gid://shopify/p/X?shop=123', see `live_id`), and get_product is kept to that store.
"""

import itertools
import json
import logging
import math
import os
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from agent.models import Images, MustHave, Price, Product, ShoppingBrief
from agent.rules import label
from agent.tradeoffs import raised_budget
from data_prep import schemas
from data_prep.simulate import ONE_SIZE

log = logging.getLogger(__name__)

ENDPOINT = "https://catalog.shopify.com/api/ucp/mcp"
UCP_VERSION = "2026-08-25"
EXAMPLE_PROFILE = f"https://shopify.dev/ucp/agent-profiles/examples/{UCP_VERSION}/valid-with-capabilities.json"
DEPLOYED_API_URL = "https://ecommai-agent-v54j7fs5ma-el.a.run.app"
PROFILE_PATH = "/.well-known/ucp"
PROFILE_FILE = Path(__file__).resolve().parent.parent / "ucp_profile.json"
GID_PREFIX = "gid://shopify/"
SHOP_PREFIX = "gid://shopify/Shop/"
COUNTRY = "IN"
CURRENCY = "INR"
SEARCH_LIMIT = 40
PAGE_LIMIT = 50
TIMEOUT_S = 8.0
ATTEMPTS = 3
BASE_DELAY_S = 0.5
MAX_DELAY_S = 4.0
RETRY_CODES = {408, 429, 500, 502, 503, 504}
THUMB_WIDTH = 300
PHOTO_WIDTH = 1000
MAX_DESCRIPTION = 2000
MAX_DETAIL = 600
MAX_INTENT = 300
SHIPPING_NOTE = "Shipping and delivery shown at the store's checkout"

SIZED_GROUPS = ("tops", "bottoms", "shoes")
MASTER_CATEGORIES = {"tops": "Apparel", "bottoms": "Apparel", "shoes": "Footwear", "watches": "Accessories",
                     "bags": "Accessories", "sunglasses": "Accessories"}
SUB_CATEGORIES = {"tops": "Topwear", "bottoms": "Bottomwear", "shoes": "Shoes", "watches": "Watches", "bags": "Bags",
                  "sunglasses": "Eyewear"}
GENDER_WORDS = {"men": "men's", "women": "women's", "boys": "boys'", "girls": "girls'"}
CATEGORY_WORDS = {
    "Kurtas": "kurta", "Kurtis": "kurti", "Tunics": "tunic", "Shirts": "shirt", "Tshirts": "t-shirt", "Tops": "top",
    "Jeans": "jeans", "Trousers": "trousers", "Casual Shoes": "casual shoes", "Sports Shoes": "sports shoes",
    "Formal Shoes": "formal shoes", "Watches": "watch", "Handbags": "handbag", "Backpacks": "backpack",
    "Clutches": "clutch", "Sunglasses": "sunglasses",
}
NOT_SEARCHED = frozenset(schemas.SHOP_ATTRIBUTES) | frozenset(schemas.REVIEW_ATTRIBUTES)
SIZE_ALIASES = {"XXL": "2XL", "2XL": "XXL", "XXXL": "3XL", "3XL": "XXXL"}
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL", "6XL"]
LETTER_NAMES = {"2XL": "XXL", "3XL": "XXXL"}
SIZE_PREFIX = re.compile(r"^(UK|IND|IN|EU|US)(\d+(?:\.5)?)$")
METADATA_LABELS = {"top_features": "Top features", "tech_specs": "Specs", "unique_selling_points": "Selling points"}


class CatalogError(Exception):
    """Shopify's catalog couldn't be reached or refused the request; the message is safe to log."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BadProduct(ValueError):
    """One catalog product can't be used (no price in rupees, no photo, ...)."""


class SizeUnavailable(ValueError):
    """The shopper's size isn't available at the store right now."""


# ---------- Our agent profile ----------


def is_live_id(product_id: str) -> bool:
    return product_id.startswith(GID_PREFIX)


def live_id(product_id: str, shop_id: object) -> str:
    """Our id for one store's offer: Shopify's product id plus the store ('gid://shopify/p/X?shop=123').

    Shopify groups the same product from several stores under one id; the store keeps the product page and checkout
    on the offer we checked.
    """
    shop = str(shop_id or "").removeprefix(SHOP_PREFIX)
    return f"{product_id}?shop={shop}" if shop.isdigit() else product_id


def split_id(product_id: str) -> tuple[str, str | None]:
    """Shopify's product id and the store's shop id ('gid://shopify/Shop/123'), if our id names one."""
    base, _, query = product_id.partition("?")
    shop = dict(parse_qsl(query)).get("shop", "")
    return base, (SHOP_PREFIX + shop if shop.isdigit() else None)


def profile_url(env: Mapping[str, str] | None = None) -> str:
    """UCP_AGENT_PROFILE_URL, else our deployed profile on Cloud Run, else Shopify's hosted example (local and tests)."""
    env = os.environ if env is None else env
    if url := (env.get("UCP_AGENT_PROFILE_URL") or "").strip():
        return url
    if env.get("K_SERVICE"):
        return DEPLOYED_API_URL + PROFILE_PATH
    return EXAMPLE_PROFILE


@cache
def agent_profile() -> dict:
    """What GET /.well-known/ucp serves: catalog search and lookup, plus Shopify's Global Catalog extension."""
    return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))


# ---------- Requests ----------


def query_words(brief: ShoppingBrief) -> str:
    """The checklist as a short shopping query: "women's cotton kurta with pockets"."""
    wanted = [m for m in brief.must_haves if m.attribute not in NOT_SEARCHED and _positive(m)]
    kind = next((str(m.value) for m in wanted if m.attribute == "type" and m.rule == "is"), None)
    describing = [label(m) for m in wanted if m.rule == "is" and m.attribute != "type"]
    having = [label(m) for m in wanted if m.rule == "present"]
    words = " ".join(
        [GENDER_WORDS.get(brief.gender or "", ""), *describing, kind or CATEGORY_WORDS.get(brief.category, brief.category)]
    )
    if having:
        words += " with " + " and ".join(having)
    return " ".join(words.split())


def _positive(must_have: MustHave) -> bool:
    if must_have.rule == "present":
        return must_have.value is True
    if must_have.rule != "is":
        return False
    words = label(must_have)
    return not words.startswith(("no ", "not "))


def size_values(brief: ShoppingBrief) -> list[str]:
    """The shopper's size as Shopify's Size filter takes it: ["M"], ["XXL", "2XL"], ["9", "UK 9"]."""
    group = schemas.group_for(brief.category)
    if not brief.size or group not in SIZED_GROUPS:
        return []
    size = " ".join(brief.size.split()).upper()
    if group == "shoes":
        number = size.removeprefix("UK").strip()
        return [number, f"UK {number}"]
    return list(dict.fromkeys([size, *([SIZE_ALIASES[size]] if size in SIZE_ALIASES else [])]))


def ships_to(pincode: str | None) -> dict:
    return {"country": COUNTRY, **({"postal_code": pincode} if pincode else {})}


def context(pincode: str | None, intent: str = "") -> dict:
    found = {"address_country": COUNTRY, "currency": CURRENCY}
    if pincode:
        found["postal_code"] = pincode
    if intent := " ".join(intent.split())[:MAX_INTENT]:
        found["intent"] = intent
    return found


def search_catalog(brief: ShoppingBrief, query_text: str, *, limit: int = SEARCH_LIMIT, size_filter: bool = True) -> dict:
    """The `catalog` object for search_catalog: Indian stores that ship to the pincode, up to the raised budget."""
    filters: dict[str, Any] = {"ships_to": ships_to(brief.pincode), "ships_from": [{"country": COUNTRY}]}
    if brief.budget_max_inr:
        filters["price"] = {"min": 0, "max": raised_budget(brief.budget_max_inr) * 100}
    if size_filter and (sizes := size_values(brief)):
        filters["attributes"] = [{"name": "Size", "values": sizes}]
    return {
        "query": query_words(brief),
        "context": context(brief.pincode, query_text),
        "filters": filters,
        "pagination": {"limit": max(1, min(limit, PAGE_LIMIT))},
    }


def get_product_catalog(product_id: str, pincode: str | None, selected: list[dict] | None = None) -> dict:
    """The `catalog` object for get_product, kept to the one store our id names."""
    base, shop = split_id(product_id)
    filters: dict[str, Any] = {"ships_to": ships_to(pincode)}
    if shop:
        filters["shops"] = [shop]
    catalog: dict[str, Any] = {"id": base, "context": context(pincode), "filters": filters}
    if selected:
        catalog["selected"] = selected
    return catalog


def rpc_body(tool: str, catalog: dict, profile: str, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": request_id,
        "params": {"name": tool, "arguments": {"meta": {"ucp-agent": {"profile": profile}}, "catalog": catalog}},
    }


def structured(body: Any) -> dict:
    """The tool's structuredContent, or CatalogError for a JSON-RPC or tool error."""
    if not isinstance(body, dict):
        raise CatalogError("the answer isn't a JSON object")
    if error := body.get("error"):
        raise CatalogError(f"JSON-RPC error {error.get('code')}: {_one_line(error.get('message'))}")
    result = body.get("result") or {}
    if result.get("isError"):
        texts = [part.get("text", "") for part in result.get("content") or [] if isinstance(part, dict)]
        raise CatalogError(f"tool error: {_one_line(' '.join(texts))}")
    content = result.get("structuredContent")
    if not isinstance(content, dict):
        raise CatalogError("no structured content in the answer")
    return content


def _one_line(text: object, limit: int = 200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------- From a UCP product to ours ----------


class _Text(HTMLParser):
    BLOCKS = {"p", "br", "li", "ul", "ol", "div", "tr", "td", "th", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self.skipping += 1
        elif tag in self.BLOCKS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def plain_text(markup: object) -> str:
    """HTML as one line of text: tags dropped, entities decoded, whitespace collapsed."""
    parser = _Text()
    parser.feed(str(markup or ""))
    parser.close()
    return " ".join("".join(parser.parts).split())


def with_width(url: str, width: int) -> str:
    """A smaller copy from Shopify's image CDN (it resizes on the fly); other links stay as they are."""
    parts = urlsplit(url)
    if parts.hostname != "cdn.shopify.com" and "/cdn/shop/" not in parts.path:
        return url
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "width"] + [("width", str(width))]
    return urlunsplit(parts._replace(query=urlencode(query)))


def https(url: object) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") else None


def available(variant: Mapping) -> bool:
    return bool((variant.get("availability") or {}).get("available"))


def featured_variant(variants: list[dict]) -> dict:
    variant = next((v for v in variants if available(v)), None)
    if variant is None:
        raise BadProduct("no variant is available")
    return variant


def rupees(money: Mapping | None) -> int | None:
    """Whole rupees from minor units (paise), rounded up so a price is never shown lower than it is."""
    if not money:
        return None
    if money.get("currency") != CURRENCY:
        raise BadProduct(f"price in {money.get('currency')}, not {CURRENCY}")
    amount = money.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int | float) or amount <= 0:
        return None
    return math.ceil(amount / 100)


def price_of(variant: Mapping, raw: Mapping) -> Price:
    selling = rupees(variant.get("price")) or rupees((raw.get("price_range") or {}).get("min"))
    if selling is None:
        raise BadProduct("no price")
    listed = rupees(variant.get("list_price"))
    return Price(mrp=max(listed or selling, selling), selling=selling, demo=False)


def first_image(raw: Mapping, variant: Mapping) -> str | None:
    for item in [*(variant.get("media") or []), *(raw.get("media") or [])]:
        if isinstance(item, dict) and item.get("type", "image") == "image" and (url := https(item.get("url"))):
            return url
    return None


def is_size_option(name: object) -> bool:
    return isinstance(name, str) and "size" in name.casefold()


def variant_size(variant: Mapping) -> str | None:
    for option in variant.get("options") or []:
        if isinstance(option, dict) and is_size_option(option.get("name")) and (value := option.get("label")):
            return " ".join(str(value).split())
    return None


def size_tokens(text: str) -> set[str]:
    tokens = {token for token in re.split(r"[^A-Z0-9.]+", text.upper()) if token}
    return tokens | {match[2] for token in tokens if (match := SIZE_PREFIX.match(token))}


def same_size(size_label: str, wanted: str) -> bool:
    """Whether a store's size label is the shopper's size: "Medium (M)" and "M-36" are M; "9 UK" is UK 9."""
    target = " ".join(wanted.upper().split()).removeprefix("UK").strip()
    targets = {target, *([SIZE_ALIASES[target]] if target in SIZE_ALIASES else [])}
    return bool(size_tokens(size_label) & targets)


def size_rank(size_label: str) -> tuple:
    """Sort key: letter sizes in chart order, then numbers, then anything else by name."""
    tokens = size_tokens(size_label)
    letters = [SIZE_ORDER.index(LETTER_NAMES.get(t, t)) for t in tokens if LETTER_NAMES.get(t, t) in SIZE_ORDER]
    if letters:
        return 0, min(letters), size_label
    numbers = [float(t) for t in tokens if re.fullmatch(r"\d+(\.\d+)?", t)]
    if numbers:
        return 1, numbers[0], size_label
    return 2, 0.0, size_label


def sizes_of(raw: Mapping, variants: list[dict], group: str) -> dict[str, int]:
    """1 for each size we know is available, 0 for each we know isn't; get_product's options say which are."""
    if group not in SIZED_GROUPS:
        return {ONE_SIZE: 1}
    sizes: dict[str, int] = {}
    for option in raw.get("options") or []:
        if not isinstance(option, dict) or not is_size_option(option.get("name")):
            continue
        for value in option.get("values") or []:
            if isinstance(value, dict) and value.get("label") and "available" in value and value.get("exists", True):
                sizes[" ".join(str(value["label"]).split())] = 1 if value["available"] else 0
    for variant in variants:
        if size := variant_size(variant):
            sizes[size] = max(sizes.get(size, 0), int(available(variant)))
    return dict(sorted(sizes.items(), key=lambda item: size_rank(item[0])))


def inferred_details(raw: Mapping) -> dict[str, str]:
    metadata = raw.get("metadata") or {}
    details = {}
    for key, name in METADATA_LABELS.items():
        items = [" ".join(str(item).split()) for item in metadata.get(key) or [] if str(item).strip()]
        if items:
            details[name] = "; ".join(items)[:MAX_DETAIL]
    return details


def description_of(raw: Mapping, variant: Mapping) -> str:
    for source in (raw.get("description"), variant.get("description")):
        if isinstance(source, dict) and (text := plain_text(source.get("plain") or source.get("html") or "")):
            return text[:MAX_DESCRIPTION]
    return ""


def to_product(raw: Mapping, brief: ShoppingBrief) -> Product:
    """One UCP catalog product as ours (source 'shopify'). Raises BadProduct or ValidationError for unusable data."""
    product_id = str(raw.get("id") or "")
    if not is_live_id(product_id):
        raise BadProduct(f"not a Shopify product id: {product_id!r}")
    title = " ".join(str(raw.get("title") or "").split())
    if not title:
        raise BadProduct("no title")
    group = schemas.group_for(brief.category)
    if group is None:
        raise BadProduct(f"unknown category '{brief.category}'")
    variants = [variant for variant in raw.get("variants") or [] if isinstance(variant, dict)]
    variant = featured_variant(variants)
    photo = first_image(raw, variant)
    if photo is None:
        raise BadProduct("no https photo")
    seller = variant.get("seller") or {}
    return Product(
        id=live_id(product_id, seller.get("id")),
        title=title,
        gender=brief.gender or "unisex",
        masterCategory=MASTER_CATEGORIES[group],
        subCategory=SUB_CATEGORIES[group],
        articleType=brief.category,
        group=group,
        description=description_of(raw, variant),
        catalogue_details=inferred_details(raw),
        price=price_of(variant, raw),
        sizes=sizes_of(raw, variants, group),
        images=Images(original=photo, gemini=with_width(photo, PHOTO_WIDTH), thumb=with_width(photo, THUMB_WIDTH)),
        demo_data=False,
        source="shopify",
        store=" ".join(str(seller.get("name") or "").split()) or None,
        product_url=https(variant.get("url")),
        checkout_url=https(variant.get("checkout_url")),
    )


def products_from(items: Iterable[Any], brief: ShoppingBrief) -> list[Product]:
    """Usable products in Shopify's order, one per id; bad ones are logged and left out."""
    found: dict[str, Product] = {}
    for raw in items or []:
        try:
            product = to_product(raw, brief)
        except (BadProduct, ValidationError, AttributeError, KeyError, TypeError, ValueError) as error:
            log.warning("shopify product %s: left out: %s", _id_of(raw), _one_line(error))
            continue
        found.setdefault(product.id, product)
    return list(found.values())


def _id_of(raw: Any) -> str:
    return str(raw.get("id")) if isinstance(raw, dict) else "?"


def variant_for_size(raw: Mapping, size: str, prefer: set[tuple] = frozenset()) -> dict | None:
    """The available variant in this size, with the `prefer` options (such as the colour shown) when there is one."""
    variants = [v for v in raw.get("variants") or [] if isinstance(v, dict) and available(v)]
    sized = [v for v in variants if (name := variant_size(v)) and (name == size or same_size(name, size))]
    if not sized:
        return None
    return next((v for v in sized if prefer <= other_options(v)), sized[0])


def other_options(variant: Mapping) -> set[tuple]:
    """A variant's options other than its size, such as {("Color", "Black")}."""
    return {(o.get("name"), o.get("label")) for o in variant.get("options") or []
            if isinstance(o, dict) and not is_size_option(o.get("name"))}


def size_option_name(raw: Mapping) -> str | None:
    names = [o.get("name") for o in raw.get("options") or [] if isinstance(o, dict)]
    names += [o.get("name") for v in raw.get("variants") or [] for o in v.get("options") or [] if isinstance(o, dict)]
    return next((name for name in names if is_size_option(name)), None)


# ---------- The client ----------


@dataclass
class Catalog:
    """Shopify's Global Catalog over MCP, with timeouts and retries on rate limits and server errors."""

    endpoint: str = ENDPOINT
    profile: str = field(default_factory=profile_url)
    timeout_s: float = TIMEOUT_S
    attempts: int = ATTEMPTS
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._http = httpx.Client(timeout=self.timeout_s, transport=self.transport,
                                  headers={"Accept": "application/json"})
        self._ids = itertools.count(1)

    def call(self, tool: str, catalog: dict) -> dict:
        body = rpc_body(tool, catalog, self.profile, next(self._ids))
        problem, status = "no answer", None
        for attempt in range(1, self.attempts + 1):
            delay = None
            try:
                response = self._http.post(self.endpoint, json=body)
            except httpx.HTTPError as error:
                problem, status = f"{type(error).__name__}: {_one_line(error)}", None
            else:
                status = response.status_code
                if status == 200:
                    try:
                        return structured(response.json())
                    except ValueError:
                        problem = "the answer isn't JSON"
                else:
                    problem = f"HTTP {status}: {_one_line(response.text)}"
                    if status not in RETRY_CODES:
                        raise CatalogError(f"{tool}: {problem}", status)
                    delay = _retry_after(response)
            log.warning("shopify %s attempt %d failed: %s", tool, attempt, problem)
            if attempt < self.attempts:
                self.sleep(delay if delay is not None else _backoff(attempt))
        raise CatalogError(f"{tool}: {problem}", status)

    def search(self, brief: ShoppingBrief, query_text: str, limit: int = SEARCH_LIMIT) -> list[Product]:
        """Live products for the checklist, Shopify's best first. Raises CatalogError when the catalog fails."""
        try:
            found = self.call("search_catalog", search_catalog(brief, query_text, limit=limit))
        except CatalogError as error:
            if not size_values(brief) or error.status is None or error.status in RETRY_CODES:
                raise
            log.warning("shopify search with the size filter failed (%s); searching without it", error)
            found = self.call("search_catalog", search_catalog(brief, query_text, limit=limit, size_filter=False))
        return products_from(found.get("products") or [], brief)

    def product_data(self, product_id: str, pincode: str | None, selected: list[dict] | None = None) -> dict:
        found = self.call("get_product", get_product_catalog(product_id, pincode, selected))
        raw = found.get("product")
        if not isinstance(raw, dict):
            raise CatalogError(f"get_product: no product for {product_id}", 404)
        return raw

    def get_product(self, product_id: str, brief: ShoppingBrief) -> Product:
        """The product as the store shows it now. Raises CatalogError, or BadProduct when it can't be used."""
        return to_product(self.product_data(product_id, brief.pincode), brief)

    def checkout_link(self, product_id: str, pincode: str | None, size: str | None) -> str:
        """The store's own checkout for this product in the shopper's size, as Shopify gives it.

        Without a size, a product that comes in several sizes goes to its page on the store, to pick one there.
        Raises SizeUnavailable, CatalogError, or BadProduct when the store gives no link.
        """
        raw = self.product_data(product_id, pincode)
        variant = featured_variant([v for v in raw.get("variants") or [] if isinstance(v, dict)])
        size = None if size == ONE_SIZE else size
        if size:
            shown = other_options(variant)
            variant = variant_for_size(raw, size, shown)
            name = size_option_name(raw)
            if variant is None and name:
                raw = self.product_data(product_id, pincode, [{"name": name, "label": size}])
                variant = variant_for_size(raw, size, shown)
            if variant is None:
                raise SizeUnavailable(size)
        elif len(size_labels(raw)) > 1:
            return _link(variant.get("url"), variant.get("checkout_url"))
        return _link(variant.get("checkout_url"), variant.get("url"))


def size_labels(raw: Mapping) -> set[str]:
    listed = {
        str(value["label"])
        for option in raw.get("options") or []
        if isinstance(option, dict) and is_size_option(option.get("name"))
        for value in option.get("values") or []
        if isinstance(value, dict) and value.get("label")
    }
    worn = {variant_size(variant) for variant in raw.get("variants") or [] if isinstance(variant, dict)}
    return listed | {size for size in worn if size}


def _link(*candidates: object) -> str:
    link = next((url for url in map(https, candidates) if url), None)
    if link is None:
        raise BadProduct("the store gave no checkout or product link")
    return link


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds to wait from the Retry-After header, or from the JSON-RPC error's data.retry_after Shopify sends."""
    wait: Any = response.headers.get("retry-after")
    if wait is None:
        try:
            wait = ((response.json().get("error") or {}).get("data") or {}).get("retry_after")
        except (ValueError, AttributeError):
            wait = None
    try:
        return min(MAX_DELAY_S, max(0.0, float(wait)))
    except (TypeError, ValueError):
        return None


def _backoff(attempt: int) -> float:
    return min(MAX_DELAY_S, BASE_DELAY_S * 2 ** (attempt - 1)) + random.uniform(0, BASE_DELAY_S)
