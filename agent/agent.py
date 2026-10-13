"""The same steps as ADK tools under one root_agent, for `adk web`, `adk eval`, MCP and A2A (PLAN.md appendix D).

From the repo root, `adk web .` lists this folder as the agent "agent" (ADK imports agent.agent.root_agent).
The tools call the same functions as the API; `search` is the API's search without the live stream.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from google.adk import Agent

from agent import brief as briefs
from agent import cards, delivery, workflow
from agent.cards import ResultCard
from agent.models import ShoppingBrief
from agent.sessions import Store, default_store

MODEL = os.environ.get("ADK_MODEL") or os.environ.get("GEMINI_LITE_MODEL") or "gemini-3.5-flash-lite"
MAX_MATCHES = 5
MAX_ALMOST = 3
MAX_REVIEWS = 5

INSTRUCTION = """You are EcommAI, a shopping agent for India. You find products that have everything the shopper
asked for, and you show the proof.

1. Call make_checklist with the shopper's words and their 6-digit delivery pincode. Ask for the pincode first if
   you don't have it.
2. Show the checklist briefly: must-haves, budget (it includes delivery), deadline and size. If it has a question,
   ask it, then call make_checklist again with the answer added to the shopper's words.
3. Call search with the checklist exactly as make_checklist returned it.
4. Show the matches first: title, total price in rupees, delivery date, the reason line and the ticks. Mention how
   many were checked and why others were rejected. When there are fewer than 3 matches, offer the trade-offs.
5. Call product_details when the shopper asks about one product.

Only say a product has something when its tick is a pass. Say "not sure" for unclear ticks. Prices, stock,
delivery and reviews are demo data."""


@dataclass
class Deps:
    services: Callable[[], workflow.Services] = workflow.default_services
    store: Callable[[], Store] = default_store
    brief_client: Any = None
    now: Callable[[], datetime | None] = field(default=lambda: None)


deps = Deps()


def make_checklist(text: str, pincode: str) -> dict:
    """Turn a shopping request into a checklist: must-haves, nice-to-haves, budget, deadline and size.

    Args:
        text: The shopper's request in their own words (English, Hindi or Hinglish).
        pincode: The shopper's 6-digit Indian delivery pincode.

    Returns:
        {"checklist": ..., "question": ...} where question is at most one thing to ask the shopper, or
        {"error": ...} with a message to show.
    """
    try:
        code = delivery.lookup(pincode).pincode
    except delivery.UnknownPincode as error:
        return {"error": str(error)}
    try:
        brief = workflow.with_fallback(
            lambda settings: briefs.make_brief(
                text, today=briefs.today_in_india(deps.now()), pincode=code, client=deps.brief_client, settings=settings
            )
        )
    except briefs.BriefError:
        return {"error": "Our AI is busy right now. Please try again in a moment."}
    except ValueError as error:
        return {"error": str(error)}
    return {"checklist": brief.model_dump(mode="json"), "question": brief.question}


def search(checklist: ShoppingBrief) -> dict:
    """Find products that meet every must-have of a checklist, checking each one against the photo, details and
    reviews.

    Args:
        checklist: The checklist exactly as make_checklist returned it.

    Returns:
        Counts of what was checked and rejected, the full matches with their ticks, products we couldn't fully
        confirm, and trade-offs ("what to give up") when there are few matches.
    """
    try:
        outcome = workflow.search(checklist, query_text="", services=deps.services(), now=deps.now())
    except Exception as error:
        return {"error": workflow.friendly_error(error).message}
    return summary(outcome.done)


def product_details(product_id: str) -> dict:
    """Everything about one product: details, price, delivery fee, cash on delivery, returns, sizes and reviews.

    Args:
        product_id: The product's id from a search result.
    """
    product = deps.store().product(product_id)
    if product is None:
        return {"error": "We couldn't find that product."}
    view = cards.product_view(product)
    view["reviews"] = view["reviews"][:MAX_REVIEWS]
    for name in ("images", "catalogue_details", "needs_review"):
        view.pop(name, None)
    return view


def summary(done: workflow.DoneEvent) -> dict:
    return {
        "checked": done.checked,
        "passed": done.passed,
        "unclear": done.unclear,
        "rejected": done.rejected,
        "duplicates": done.duplicates,
        "tradeoffs": [tradeoff.model_dump(mode="json") for tradeoff in done.tradeoffs],
        "not_checkable": done.not_checkable,
        "matches": [_short(card) for card in done.matches[:MAX_MATCHES]],
        "almost": [_short(card) for card in done.almost[:MAX_ALMOST]],
    }


def _short(card: ResultCard) -> dict:
    return {
        "product_id": card.product_id,
        "title": card.title,
        "brand": card.brand,
        "total_price_inr": card.total_price,
        "delivery_date": card.delivery_date.isoformat() if card.delivery_date else None,
        "reason": card.reason,
        "ticks": [
            {"label": tick.label, "status": tick.status, "proof": tick.evidence.note if tick.evidence else None}
            for tick in card.ticks
        ],
    }


root_agent = Agent(
    name="ecommai",
    model=MODEL,
    description="Finds products that meet every must-have, with proof for each one.",
    instruction=INSTRUCTION,
    tools=[make_checklist, search, product_details],
)
