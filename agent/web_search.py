"""'On other sites': listings on Amazon.in, Flipkart, Myntra, brand sites and so on, found through Gemini with
Grounding with Google Search. We never fetch those pages, and EcommAI hasn't checked them.

One Gemini call with the google_search tool lists matching products, one per line. A result's link is always the
link of the grounding chunk that supports its line (Google's redirect to the page Gemini read), never a link Gemini
wrote, so no link is invented; lines without support from a store page are dropped.

Google's display rules for grounded results
(https://cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-search-suggestions) apply to the app:
show `search_entry_point_html` (Google's Search Suggestions) exactly as given, whenever these results are shown;
show the sources (the results' links); and keep other search results, such as our checked Shopify matches, out of
this section. Each Gemini search query is billed (after the monthly free ones).
"""

import logging
import os
import re
import time
from collections.abc import Sequence
from typing import Any

from google.genai import types
from pydantic import BaseModel, Field

from agent import verify
from agent.models import ShoppingBrief
from agent.rank import rupees
from agent.rules import label
from agent.sources.shopify import CATEGORY_WORDS, GENDER_WORDS

log = logging.getLogger(__name__)

FLASH_MODEL = "gemini-3.8-flash"
MAX_RESULTS = 6
# Uncapped, Flash ran 7-12 billed searches in 8-16 s per request; asked to cap, mostly 4-6 in 5-9 s (some still 8).
MAX_SEARCHES = 4
TIMEOUT_MS = 20_000
MAX_OUTPUT_TOKENS = 1024
MAX_REQUEST = 400
NOT_STORES = (
    "youtube.com", "instagram.com", "facebook.com", "pinterest.com", "pinterest.ca", "reddit.com", "quora.com",
    "twitter.com", "x.com", "wikipedia.org", "google.com", "medium.com", "linkedin.com", "threads.net", "blogspot.com",
)
UNKNOWN_PRICES = {"", "unknown", "n/a", "na", "not shown", "none", "-"}
STORES_HINT = "Amazon.in, Flipkart, Myntra, Ajio, Nykaa Fashion, Tata CLiQ or the brand's own site"


class WebResult(BaseModel):
    title: str
    url: str = Field(description="The grounding chunk's link: Google's redirect to the page")
    site: str = Field(description="The page's domain, from the grounding chunk")
    price_text: str | None = None
    snippet: str | None = Field(default=None, description="What Gemini read on that page about the must-haves")


class WebFindings(BaseModel):
    results: list[WebResult]
    search_entry_point_html: str | None = Field(default=None, description="Google's Search Suggestions, unchanged")
    queries: list[str] = Field(default_factory=list)


def default_model() -> str:
    """Flash: lite mostly answered from memory, and lines without grounding are dropped (0-1 results of 6)."""
    return os.environ.get("WEB_SEARCH_MODEL") or os.environ.get("GEMINI_MODEL") or FLASH_MODEL


def wanted_words(brief: ShoppingBrief) -> str:
    gender = GENDER_WORDS.get(brief.gender or "", "")
    return " ".join(f"{gender} {CATEGORY_WORDS.get(brief.category, brief.category)}".split())


def web_prompt(brief: ShoppingBrief, text: str) -> str:
    lines = [
        "Search Google for products for sale in India that match this shopper's request.",
        f'Shopper\'s words: "{" ".join(text.split())[:MAX_REQUEST]}"',
        f"Product: {wanted_words(brief)}",
    ]
    if brief.must_haves:
        lines.append("Must have: " + "; ".join(label(m) for m in brief.must_haves))
    if brief.budget_max_inr:
        lines.append(f"Budget: up to {rupees(brief.budget_max_inr)}")
    if brief.size:
        lines.append(f"Size: {brief.size}")
    lines += [
        "",
        f"List up to {MAX_RESULTS} specific product listings you found on store pages ({STORES_HINT}).",
        "One per line, exactly in this form:",
        "title | store | price | which must-haves the page shows",
        'Write "unknown" for a price the page doesn\'t show. No other text, no numbering, no links.',
        f"Use at most {MAX_SEARCHES} Google searches.",
    ]
    return "\n".join(lines)


def config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        max_output_tokens=MAX_OUTPUT_TOKENS,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )


def find_on_web(brief: ShoppingBrief, text: str, *, client: Any = None, model: str | None = None) -> WebFindings | None:
    """Grounded listings for the checklist, with Google's Search Suggestions; None when grounding gave nothing."""
    started = time.perf_counter()
    model = model or default_model()
    try:
        client = client or verify.make_client()
        response = client.models.generate_content(model=model, contents=web_prompt(brief, text), config=config())
        findings = findings_from(response)
    except Exception as error:
        log.warning("web search failed: %s: %s", type(error).__name__, " ".join(str(error).split())[:200])
        return None
    log.info(
        "web search with %s: %d grounded results, %d queries, %.0f ms",
        model, len(findings.results) if findings else 0, len(findings.queries) if findings else 0,
        (time.perf_counter() - started) * 1000,
    )
    return findings


def findings_from(response: Any) -> WebFindings | None:
    candidates = getattr(response, "candidates", None) or []
    metadata = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    if metadata is None:
        return None
    entry = getattr(metadata, "search_entry_point", None)
    html = getattr(entry, "rendered_content", None) or None
    results = grounded_results(_text(candidates[0]), metadata)
    if not results and not html:
        return None
    return WebFindings(results=results, search_entry_point_html=html, queries=list(metadata.web_search_queries or []))


def _text(candidate: Any) -> str:
    parts = getattr(getattr(candidate, "content", None), "parts", None) or []
    return "".join(part.text for part in parts if getattr(part, "text", None) and not getattr(part, "thought", False))


def grounded_results(text: str, metadata: Any) -> list[WebResult]:
    """One result per answer line that a store page supports, linked to that page's grounding chunk."""
    chunks = list(getattr(metadata, "grounding_chunks", None) or [])
    supports = list(getattr(metadata, "grounding_supports", None) or [])
    results: list[WebResult] = []
    seen: set[str] = set()
    for start, end, line in byte_lines(text):
        fields = parse_line(line)
        if fields is None:
            continue
        chunk = store_chunk(chunks, supporting(supports, start, end, line))
        if chunk is None or chunk.uri in seen:
            continue
        seen.add(chunk.uri)
        title, price, shows = fields
        results.append(WebResult(title=title, url=chunk.uri, site=chunk.domain or chunk.title, price_text=price,
                                 snippet=shows))
        if len(results) == MAX_RESULTS:
            break
    return results


def byte_lines(text: str) -> list[tuple[int, int, str]]:
    """Each line with its start and end as UTF-8 byte offsets, the unit grounding supports use."""
    lines, offset = [], 0
    for line in text.split("\n"):
        size = len(line.encode("utf-8"))
        lines.append((offset, offset + size, line))
        offset += size + 1
    return lines


def parse_line(line: str) -> tuple[str, str | None, str | None] | None:
    parts = [" ".join(part.split()) for part in re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).split("|")]
    if len(parts) < 3 or not parts[0]:
        return None
    title = parts[0].strip("*` ")
    price = parts[2] if parts[2].casefold() not in UNKNOWN_PRICES else None
    shows = parts[3] if len(parts) > 3 and parts[3] else None
    return (title, price, shows) if title else None


def supporting(supports: Sequence[Any], start: int, end: int, line: str) -> list[int]:
    """Chunk indices of the supports whose segment ends on this line (or whose text ends with it)."""
    found: list[int] = []
    wanted = line.strip()
    for support in supports:
        segment = getattr(support, "segment", None)
        if segment is None:
            continue
        stop = segment.end_index or 0
        said = (segment.text or "").strip()
        if start < stop <= end + 1 or (wanted and said.endswith(wanted)):
            found += [index for index in support.grounding_chunk_indices or [] if index not in found]
    return found


def store_chunk(chunks: Sequence[Any], indices: Sequence[int]) -> Any:
    for index in indices:
        web = getattr(chunks[index], "web", None) if 0 <= index < len(chunks) else None
        if web is None or not (web.uri or "").startswith("https://"):
            continue
        site = (getattr(web, "domain", None) or web.title or "").casefold().removeprefix("www.")
        if site and not any(site == blocked or site.endswith("." + blocked) for blocked in NOT_STORES):
            return web
    return None
