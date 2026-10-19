"""Demo stories (IMPLEMENTATION.md step 8.1): run each story through the real pipeline, keep the proof and time it.

Each line of eval/demo_stories.jsonl is a shopper's exact words, a pincode and the day the story is set on. The
pipeline is composed here from the agent modules, in the order the app runs them: make_brief ->
find_candidates(FETCH_LIMIT) -> quick_check -> about 12 chosen (the best within limits plus up to 4 over budget or
late) -> careful_check -> rank -> suggest_tradeoffs. A story can go on with follow-ups: a tap on a "what to give up"
card, or something the shopper says next.

    python -m eval.demo_stories precheck --dry-run   # make the checklists (live), count what the batch would send
    python -m eval.demo_stories precheck             # batch-check every product the checklists could return
    python -m eval.demo_stories run                  # every story from saved checks -> data/demo/<story>.json
    python -m eval.demo_stories run --cold           # also time each story with nothing saved (live Gemini)
    python -m eval.demo_stories brief-latency        # time each story's checklist call, as the app makes it
    python -m eval.demo_stories report               # write data/trials/demo_stories.md

The checklist call is the one live Gemini step a saved story still needs, and its time depends on the exact words:
one wording of the same request took 55-68 s every time while another took 5 s. Time every stage sentence first.
"""

import argparse
import contextlib
import io
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as day_time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agent import verify
from agent.brief import BriefError, make_brief_detailed, update_brief_detailed
from agent.brief import Settings as BriefSettings
from agent.delivery import INDIA, lookup
from agent.models import Evidence, Product, ShoppingBrief
from agent.quick_check import quick_check
from agent.rank import LATE_DELIVERY, OVER_BUDGET, Candidate, RankedResult, Results, Tick, rank, rupees, ticks_for
from agent.retrieve import DEFAULT_LIMIT, FETCH_LIMIT, Hit, attach_embeddings, default_db, default_embedder, find_candidates
from agent.rules import failure_label, label
from agent.tradeoffs import Tradeoff, suggest_tradeoffs

STORIES = Path("eval/demo_stories.jsonl")
PLANTED = Path("eval/planted_problems.jsonl")
TRICKS = Path("eval/trick_reviews.jsonl")
OUT = Path("data/demo")
BRIEFS = OUT / "briefs.jsonl"
BOXES = OUT / "boxes"
EYE_CHECK = OUT / "eye_check.json"
REPORT = Path("data/trials/demo_stories.md")
DEFAULT_JOB = "precheck-demo-stories-v1"
CHECK_COUNT = 12
OVER_LIMIT_SLOTS = 4
TOP_FOR_BOXES = 2
TOP_IN_REPORT = 5
BOX_COLOURS = ((230, 25, 75), (0, 130, 200), (60, 180, 75), (245, 130, 48), (145, 30, 180))
BRIEF_LATENCY = OUT / "brief_latency.json"


# ---------- Stories ----------


class FollowUp(BaseModel):
    tap: str | None = Field(default=None, description="A card to tap: 'deadline', 'budget' or 'must_have:<attribute>'")
    say: str | None = Field(default=None, description="What the shopper says next")

    @model_validator(mode="after")
    def _one_action(self) -> "FollowUp":
        if (self.tap is None) == (self.say is None):
            raise ValueError("a follow-up either taps a card or says something")
        return self


class Story(BaseModel):
    id: str
    title: str
    shopper_text: str
    pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")
    today: date
    time: str = "10:00"
    shows: str = ""
    expected: str = ""
    stage: bool = True
    precheck: bool = True
    follow_ups: list[FollowUp] = Field(default_factory=list)

    @property
    def now(self) -> datetime:
        return datetime.combine(self.today, day_time.fromisoformat(self.time), tzinfo=INDIA)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def load_stories(path: Path = STORIES) -> list[Story]:
    stories = [Story.model_validate(row) for row in read_jsonl(path)]
    ids = [story.id for story in stories]
    if len(ids) != len(set(ids)):
        raise ValueError("story ids must be unique")
    return stories


def pick(stories: list[Story], ids: Sequence[str] | None) -> list[Story]:
    if not ids:
        return stories
    unknown = set(ids) - {story.id for story in stories}
    if unknown:
        raise ValueError(f"unknown stories: {', '.join(sorted(unknown))}")
    return [story for story in stories if story.id in ids]


def by_id(rows: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["id"]].append(row)
    return grouped


# ---------- The pipeline ----------


@dataclass
class Services:
    db: Any
    embedder: Any
    cache: verify.CheckCache
    client: verify.Client | None = None


@dataclass
class Search:
    brief: ShoppingBrief
    hits: list[Hit]
    kept: list[Hit]
    dropped: list[tuple[Hit, str]]
    chosen: list[Hit]
    candidates: list[Candidate]
    results: Results
    stats: verify.Stats
    timings: dict[str, float]


def ms_since(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def choose_for_check(pool: Sequence[Hit], count: int = CHECK_COUNT, over_limit: int = OVER_LIMIT_SLOTS) -> list[Hit]:
    """The best products within limits plus up to `over_limit` over budget or late, in search order.

    When too few products are within limits, more over-limit ones fill the places (as agent/workflow.py does).
    """
    within = [hit for hit in pool if not (hit.over_budget or hit.late)]
    outside = [hit for hit in pool if hit.over_budget or hit.late]
    chosen = within[: count - min(over_limit, len(outside))]
    chosen += outside[: count - len(chosen)]
    position = {hit.product.id: index for index, hit in enumerate(pool)}
    return sorted(chosen, key=lambda hit: position[hit.product.id])


def search(brief: ShoppingBrief, query_text: str, now: datetime, services: Services, cache: verify.CheckCache) -> Search:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    found: dict[str, float] = {}
    hits = find_candidates(
        brief, query_text=query_text, limit=FETCH_LIMIT, db=services.db, embedder=services.embedder, now=now,
        timings=found,
    )
    timings["find_ms"] = found.get("total_ms", ms_since(started))

    clock = time.perf_counter()
    kept, dropped = quick_check(brief, hits)
    chosen = choose_for_check(kept[:DEFAULT_LIMIT])
    timings["quick_ms"] = ms_since(clock)

    clock = time.perf_counter()
    stats = verify.Stats()
    verifications = verify.careful_check(
        brief, [hit.product for hit in chosen], client=services.client, cache=cache, stats=stats, deadline_s=None
    )
    timings["check_ms"] = ms_since(clock)

    clock = time.perf_counter()
    checked = attach_embeddings(chosen, db=services.db) if chosen else []
    candidates = [
        Candidate(product=hit.product, verification=v, total_price=hit.total_price, delivery_date=hit.delivery_date)
        for hit, v in zip(checked, verifications)
    ]
    results = rank(brief, candidates)
    results.tradeoffs = suggest_tradeoffs(brief, candidates)
    timings["rank_ms"] = ms_since(clock)
    timings["total_ms"] = ms_since(started)
    return Search(brief, hits, kept, dropped, chosen, candidates, results, stats, timings)


def apply_tap(brief: ShoppingBrief, relax: str, tradeoffs: Sequence[Tradeoff]) -> ShoppingBrief:
    """The checklist after one tap on a 'what to give up' card."""
    if relax.startswith("must_have:"):
        attribute = relax.removeprefix("must_have:")
        return brief.model_copy(update={"must_haves": [m for m in brief.must_haves if m.attribute != attribute]})
    offer = next((t for t in tradeoffs if t.relax == relax), None)
    if offer is None or offer.to is None:
        raise ValueError(f"there's no '{relax}' card to tap")
    if relax == "budget":
        return brief.model_copy(update={"budget_max_inr": int(offer.to)})
    if relax == "deadline":
        return brief.model_copy(update={"deadline_date": date.fromisoformat(str(offer.to))})
    raise ValueError(f"unknown card '{relax}'")


# ---------- What the app will show ----------


def day_text(day: date | None) -> str | None:
    return f"{day:%a} {day.day} {day:%b}" if day else None


def chips(brief: ShoppingBrief) -> list[str]:
    items = [label(m) + (" (saved rule)" if m.from_rule else "") for m in brief.must_haves]
    if brief.size:
        items.append(f"size {brief.size}")
    if brief.budget_max_inr:
        items.append(f"≤ {rupees(brief.budget_max_inr)}")
    if brief.deadline_date:
        items.append(f"by {day_text(brief.deadline_date)}")
    return items


def checklist_key(brief: ShoppingBrief) -> dict:
    return {
        "category": brief.category,
        "gender": brief.gender,
        "must_haves": sorted(m.key() for m in brief.must_haves),
        "budget": brief.budget_max_inr,
        "deadline": brief.deadline_date.isoformat() if brief.deadline_date else None,
        "size": brief.size,
    }


def evidence_line(evidence: Evidence | None) -> str:
    if evidence is None:
        return "no evidence"
    parts = [f"{evidence.type}: {evidence.note}"]
    if evidence.quote and evidence.type != "image":
        parts.append(f'"{evidence.quote}"')
    if evidence.box_2d:
        parts.append(f"box {evidence.box_2d}")
    if evidence.agree is not None or evidence.disagree is not None:
        parts.append(f"({evidence.agree or 0} agree, {evidence.disagree or 0} disagree)")
    return " ".join(parts)


def tick_view(tick: Tick) -> dict:
    view: dict[str, Any] = {"label": tick.label, "status": tick.status}
    if tick.evidence:
        view["evidence"] = tick.evidence.model_dump(exclude_none=True)
        view["proof"] = evidence_line(tick.evidence)
    return view


def product_view(product: Product, total_price: int, delivery_date: date | None) -> dict:
    return {
        "product_id": product.id,
        "title": product.title,
        "colour": product.base_colour,
        "total_price": total_price,
        "delivery_date": delivery_date.isoformat() if delivery_date else None,
        "rating": product.rating_avg,
        "photo": product.images.gemini if product.images else None,
    }


def result_view(result: RankedResult, product: Product) -> dict:
    return {
        **product_view(product, result.total_price, result.delivery_date),
        "reason": result.reason,
        "ticks": [tick_view(tick) for tick in result.ticks],
    }


def rejection(brief: ShoppingBrief, candidate: Candidate, ticks: list[Tick]) -> tuple[str, Tick | None]:
    """Why rank() left a checked product out, and the tick that proves it (as rank's own order of reasons)."""
    if brief.budget_max_inr is not None and candidate.total_price > brief.budget_max_inr:
        return OVER_BUDGET, None
    if brief.deadline_date and candidate.delivery_date and candidate.delivery_date > brief.deadline_date:
        return LATE_DELIVERY, None
    for must_have, tick in zip(brief.must_haves, ticks):
        if tick.status == "fail":
            return failure_label(must_have), tick
    return "near-duplicate of a shown product", None


def placements(found: Search) -> dict[str, str]:
    """Where each found product ended up: a match, not sure, rejected (with why), or never checked."""
    where = {hit.product.id: "not checked (beyond the products chosen for the careful check)" for hit in found.hits}
    where |= {hit.product.id: f"quick check: {reason}" for hit, reason in found.dropped}
    where |= {result.product_id: "match" for result in found.results.matches}
    where |= {result.product_id: "not sure" for result in found.results.almost}
    for candidate in found.candidates:
        if where[candidate.product.id].startswith("not checked"):
            reason, _ = rejection(found.brief, candidate, ticks_for(found.brief, candidate.verification))
            where[candidate.product.id] = f"rejected: {reason}"
    return where


def rejected_views(found: Search) -> list[dict]:
    shown = {r.product_id for r in found.results.matches + found.results.almost}
    views = []
    for candidate in found.candidates:
        if candidate.product.id in shown:
            continue
        reason, tick = rejection(found.brief, candidate, ticks_for(found.brief, candidate.verification))
        view = {**product_view(candidate.product, candidate.total_price, candidate.delivery_date), "reason": reason}
        if tick:
            view["proof"] = tick_view(tick)
        views.append(view)
    return views


def planted_in(found: Search, planted: dict[str, list[dict]]) -> list[dict]:
    where = placements(found)
    checked = {c.product.id: ticks_for(found.brief, c.verification) for c in found.candidates}
    rows = []
    for hit in found.hits:
        for problem in planted.get(hit.product.id, []):
            asked = [i for i, m in enumerate(found.brief.must_haves) if m.attribute == problem["attribute"]]
            ticks = checked.get(hit.product.id)
            rows.append({
                "product_id": hit.product.id,
                "title": hit.product.title,
                "kind": problem["kind"],
                "attribute": problem["attribute"],
                "asked": bool(asked),
                "where": where[hit.product.id],
                "check": tick_view(ticks[asked[0]]) if ticks and asked else None,
                "planted_quotes": problem["quotes"],
            })
    return rows


def tricks_in(found: Search, tricks: dict[str, list[dict]]) -> list[dict]:
    where = placements(found)
    checked = {c.product.id: ticks_for(found.brief, c.verification) for c in found.candidates}
    rows = []
    for hit in found.hits:
        for trick in tricks.get(hit.product.id, []):
            reviews = hit.product.reviews
            review = reviews[trick["review_index"]] if trick["review_index"] < len(reviews) else None
            rows.append({
                "product_id": hit.product.id,
                "title": hit.product.title,
                "kind": trick["kind"],
                "text": trick["text"],
                "model_armor_flagged": review.flagged if review else trick["flagged_by_model_armor"],
                "code_rule_flagged": verify.looks_like_instructions(trick["text"]),
                "where": where[hit.product.id],
                "ticks": [tick_view(t) for t in checked.get(hit.product.id, [])],
            })
    return rows


def screening(found: Search) -> dict:
    """What Model Armor flagged and what our code rule marked, over every review the careful check read."""
    reviews = [r for c in found.candidates for r in c.product.reviews[: verify.MAX_REVIEWS]]
    code = [verify.looks_like_instructions(r.text) for r in reviews]
    return {
        "reviews_read": len(reviews),
        "model_armor": sum(r.flagged for r in reviews),
        "code_rule": sum(code),
        "both": sum(r.flagged and c for r, c in zip(reviews, code)),
        "code_rule_only": sum(c and not r.flagged for r, c in zip(reviews, code)),
        "model_armor_only": sum(r.flagged and not c for r, c in zip(reviews, code)),
    }


def checking_view(stats: verify.Stats) -> dict:
    return {
        "saved_checks_used": stats.cached_checks,
        "shop_checks": stats.shop_checks,
        "checks_asked": stats.asked_checks,
        "gemini_calls": stats.answers,
        "tries": stats.attempts,
        "http_429": stats.rate_limited,
        "failed_products": stats.failed_products,
        "cost_usd": round(stats.spent_usd, 4),
        "problems": stats.problems[:5],
    }


def summary(found: Search, planted: dict[str, list[dict]], tricks: dict[str, list[dict]]) -> dict:
    results = found.results
    products = {c.product.id: c.product for c in found.candidates}
    quick = Counter(reason for _, reason in found.dropped)
    return {
        "brief": found.brief.model_dump(mode="json"),
        "chips": chips(found.brief),
        "counts": {
            "found": len(found.hits),
            "quick_dropped": len(found.dropped),
            "after_quick": len(found.kept),
            "checked": results.checked,
            "over_limit_checked": sum(hit.over_budget or hit.late for hit in found.chosen),
            "passed": results.passed,
            "unclear": results.unclear,
            "duplicates": results.duplicates,
            "rejected": dict((quick + Counter(results.rejected)).most_common()),
            "rejected_by_quick_check": dict(quick.most_common()),
            "rejected_after_checking": results.rejected,
        },
        "matches": [result_view(r, products[r.product_id]) for r in results.matches],
        "almost": [result_view(r, products[r.product_id]) for r in results.almost],
        "rejected_after_checking": rejected_views(found),
        "quick_dropped": [
            {"product_id": hit.product.id, "title": hit.product.title, "reason": reason} for hit, reason in found.dropped
        ],
        "tradeoffs": [t.model_dump(mode="json") for t in results.tradeoffs],
        "planted": planted_in(found, planted),
        "tricks": tricks_in(found, tricks),
        "screening": screening(found),
        "checking": checking_view(found.stats),
        "timings_ms": {name: round(ms) for name, ms in found.timings.items()},
    }


# ---------- Photo boxes ----------


def box_pixels(box: Sequence[int], width: int, height: int) -> tuple[float, float, float, float]:
    """[ymin, xmin, ymax, xmax] on 0-1000 as PIL's (left, top, right, bottom) in pixels."""
    ymin, xmin, ymax, xmax = box
    return xmin * width / 1000, ymin * height / 1000, xmax * width / 1000, ymax * height / 1000


def overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def draw_boxes(tag: str, found: Search, top: int = TOP_FOR_BOXES,
               fetch: Callable[[str], bytes] = verify.download) -> list[dict]:
    from PIL import Image, ImageDraw, ImageFont

    products = {c.product.id: c.product for c in found.candidates}
    drawn = []
    for position, result in enumerate(found.results.matches[:top], start=1):
        product = products[result.product_id]
        boxed = [tick for tick in result.ticks if tick.evidence and tick.evidence.box_2d]
        if product.images is None:
            continue
        image = Image.open(io.BytesIO(fetch(product.images.gemini))).convert("RGB")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=max(16, image.width // 28))
        placed: list[tuple[float, float, float, float]] = []
        for colour, tick in zip(BOX_COLOURS, boxed):
            left, top_y, right, bottom = box_pixels(tick.evidence.box_2d, *image.size)
            draw.rectangle((left, top_y, right, bottom), outline=colour, width=max(3, image.width // 120))
            x, y = left, max(0, top_y - font.size - 6)
            text_box = draw.textbbox((x, y), tick.label, font=font)
            while any(overlaps(text_box, other) for other in placed):
                y += text_box[3] - text_box[1] + 4
                text_box = draw.textbbox((x, y), tick.label, font=font)
            placed.append(text_box)
            draw.rectangle(text_box, fill=colour)
            draw.text((x, y), tick.label, fill=(255, 255, 255), font=font)
        BOXES.mkdir(parents=True, exist_ok=True)
        path = BOXES / f"{tag}-{position}-{product.id}.jpg"
        image.save(path, quality=88)
        drawn.append({
            "position": position,
            "product_id": product.id,
            "title": product.title,
            "file": str(path),
            "photo": product.images.gemini,
            "boxes": [{"label": t.label, "box_2d": t.evidence.box_2d, "note": t.evidence.note} for t in boxed],
        })
    return drawn


# ---------- Running a story ----------


def timed(make: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = make()
    return value, ms_since(started)


def run_follow_ups(story: Story, first: Search, services: Services, planted: dict, tricks: dict) -> list[dict]:
    steps = []
    brief, tradeoffs, query = first.brief, first.results.tradeoffs, story.shopper_text
    for number, step in enumerate(story.follow_ups, start=1):
        record: dict[str, Any] = {"step": number, **step.model_dump(exclude_none=True)}
        if step.tap:
            record["card_offered"] = next((t.model_dump(mode="json") for t in tradeoffs if t.relax == step.tap), None)
            brief, brief_ms = timed(lambda: apply_tap(brief, step.tap, tradeoffs))
        else:
            try:
                outcome, brief_ms = timed(lambda: update_brief_detailed(brief, step.say, today=story.today))
            except BriefError as error:
                steps.append(record | {"error": str(error)})
                break
            brief = outcome.brief
            query = f"{query}. {step.say}"
        found = search(brief, query, story.now, services, services.cache)
        record |= {"brief_ms": round(brief_ms), **summary(found, planted, tricks)}
        record["boxes"] = draw_boxes(f"{story.id}-step{number}", found)
        steps.append(record)
        tradeoffs = found.results.tradeoffs
    return steps


def live_brief(story: Story, prechecked: ShoppingBrief | None) -> tuple[ShoppingBrief, dict]:
    """The checklist from a live call, or the prechecked one (with the error) when Gemini can't answer."""
    started = time.perf_counter()
    try:
        outcome = make_brief_detailed(story.shopper_text, today=story.today, pincode=story.pincode)
    except BriefError as error:
        if prechecked is None:
            raise
        return prechecked, {"brief_ms": 0, "brief_failed_ms": round(ms_since(started)), "brief_error": str(error),
                            "brief_cost_usd": 0.0}
    return outcome.brief, {"brief_ms": round(ms_since(started)), "brief_cost_usd": round(outcome.usage.cost_usd(), 4)}


def run_story(story: Story, services: Services, prechecked: ShoppingBrief | None, cold: bool,
              planted: dict, tricks: dict) -> dict:
    brief, made = live_brief(story, prechecked)
    first = search(brief, story.shopper_text, story.now, services, services.cache)
    record: dict[str, Any] = {
        "story": story.model_dump(mode="json"),
        "city": lookup(story.pincode).city,
        "ran_at": datetime.now(INDIA).isoformat(timespec="seconds"),
        "question": brief.question,
        "not_checkable": brief.not_checkable,
        "nice_to_haves": [n.model_dump(mode="json") for n in brief.nice_to_haves],
        **made,
        "same_as_prechecked": checklist_key(brief) == checklist_key(prechecked) if prechecked else None,
        "prechecked_checklist": checklist_key(prechecked) if prechecked else None,
        "saved": summary(first, planted, tricks),
    }
    record["boxes"] = draw_boxes(story.id, first)
    if cold:
        cold_search = search(brief, story.shopper_text, story.now, services, verify.MemoryCache())
        record["cold"] = summary(cold_search, planted, tricks)
    record["follow_ups"] = run_follow_ups(story, first, services, planted, tricks)
    return record


def default_services() -> Services:
    db = default_db()
    return Services(db=db, embedder=default_embedder(), cache=verify.FirestoreCache(verify.DEFAULT_COLLECTION, db),
                    client=verify.make_client())


def saved_briefs() -> dict[str, dict]:
    return {row["id"]: row for row in read_jsonl(BRIEFS)} if BRIEFS.exists() else {}


def run_stories(args: argparse.Namespace) -> int:
    stories = pick(load_stories(), args.stories)
    planted, tricks = by_id(read_jsonl(PLANTED)), by_id(read_jsonl(TRICKS))
    services = default_services()
    briefs = saved_briefs()
    OUT.mkdir(parents=True, exist_ok=True)
    for story in stories:
        prechecked = briefs.get(story.id)
        brief = ShoppingBrief.model_validate(prechecked["brief"]) if prechecked else None
        record = run_story(story, services, brief, args.cold, planted, tricks)
        path = OUT / f"{story.id}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        counts = record["saved"]["counts"]
        print(f"{story.id}: found {counts['found']}, quick check dropped {counts['quick_dropped']}, checked "
              f"{counts['checked']}, {counts['passed']} matches, {counts['unclear']} not sure, trade-offs "
              f"{[(t['relax'], t['matches']) for t in record['saved']['tradeoffs']]}, "
              f"{record['saved']['timings_ms']['total_ms']} ms -> {path}", flush=True)
    return 0


# ---------- Precheck ----------


class Tee(io.TextIOBase):
    """Writes to several streams, so the batch job's progress shows while it's also kept for the report."""

    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)


def make_briefs(stories: Sequence[Story], rebrief: bool) -> list[dict]:
    """Each story's checklist, made live once and kept in data/demo/briefs.jsonl so the run can compare."""
    saved = saved_briefs()
    order = {story.id: i for i, story in enumerate(load_stories())}

    def keep() -> list[dict]:
        rows = sorted(saved.values(), key=lambda row: order.get(row["id"], len(order)))
        write_jsonl(BRIEFS, rows)
        return rows

    missing = []
    for story in stories:
        row = saved.get(story.id)
        if row and row["shopper_text"] == story.shopper_text and not rebrief:
            continue
        try:
            outcome = make_brief_detailed(story.shopper_text, today=story.today, pincode=story.pincode)
        except BriefError as error:
            missing.append(story.id)
            print(f"{story.id}: no checklist yet: {error}")
            continue
        saved[story.id] = {"id": story.id, "shopper_text": story.shopper_text, "brief": outcome.brief.model_dump(mode="json")}
        print(f"{story.id}: {', '.join(chips(outcome.brief))} ({outcome.brief.category}, {outcome.brief.gender})")
        keep()
    if missing:
        raise SystemExit(f"Run precheck again for: {', '.join(missing)}")
    return keep()


def run_precheck(args: argparse.Namespace) -> int:
    stories = [story for story in pick(load_stories(), args.stories) if story.precheck]
    rows = make_briefs(stories, args.rebrief)
    wanted = {story.id for story in stories}
    batch_file = OUT / "precheck_briefs.jsonl"
    write_jsonl(batch_file, [row for row in rows if row["id"] in wanted])
    argv = ["precheck", "--briefs", str(batch_file), "--job", args.job]
    if args.dry_run:
        argv.append("--dry-run")
    output = io.StringIO()
    with contextlib.redirect_stdout(Tee(sys.stdout, output)):
        code = verify.run_precheck(verify.parse_args(argv))
    log = OUT / f"{args.job}{'-dry-run' if args.dry_run else ''}.log"
    log.write_text(output.getvalue(), encoding="utf-8")
    return code


# ---------- Checklist latency ----------


def time_brief(story: Story) -> dict:
    """One checklist call with the app's client and timeout, and no retries."""
    started = time.perf_counter()
    try:
        outcome = make_brief_detailed(story.shopper_text, today=story.today, pincode=story.pincode,
                                      settings=BriefSettings(attempts=1))
    except BriefError as error:
        return {"seconds": round(ms_since(started) / 1000, 1), "ok": False, "problem": str(error)[:160]}
    return {"seconds": round(ms_since(started) / 1000, 1), "ok": True, "chips": chips(outcome.brief)}


def run_brief_latency(args: argparse.Namespace) -> int:
    stories = pick(load_stories(), args.stories)
    jobs = [story for story in stories for _ in range(args.repeats)]
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        tries = list(pool.map(time_brief, jobs))
    results: dict[str, list[dict]] = defaultdict(list)
    for story, outcome in zip(jobs, tries):
        results[story.id].append(outcome)
    saved = json.loads(BRIEF_LATENCY.read_text(encoding="utf-8")) if BRIEF_LATENCY.exists() else {}
    for story_id, outcomes in results.items():
        saved[story_id] = {"shopper_text": next(s.shopper_text for s in stories if s.id == story_id), "tries": outcomes}
        print(f"{story_id}: {[o['seconds'] if o['ok'] else 'failed after ' + str(o['seconds']) for o in outcomes]}")
    BRIEF_LATENCY.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


# ---------- Report ----------


def counts_line(counts: dict) -> str:
    return (f"Found {counts['found']} · quick check removed {counts['quick_dropped']} · checked {counts['checked']} "
            f"({counts['over_limit_checked']} over budget or late) · **{counts['passed']} full matches** · "
            f"{counts['unclear']} not sure")


def reasons_text(rejected: dict[str, int]) -> str:
    return ", ".join(f"{reason} {count}" for reason, count in rejected.items()) or "none"


def tradeoff_text(tradeoff: dict) -> str:
    relax, to = tradeoff["relax"], tradeoff.get("to")
    if relax == "deadline":
        words = f"Allow {day_text(date.fromisoformat(to))}"
    elif relax == "budget":
        words = f"Allow {rupees(int(to))}"
    else:
        words = f"Drop {relax.removeprefix('must_have:').replace('_', ' ')}"
    return f"{words}: {tradeoff['matches']} option{'s' if tradeoff['matches'] != 1 else ''}"


def match_lines(views: list[dict], limit: int = TOP_IN_REPORT) -> list[str]:
    lines = []
    for position, view in enumerate(views[:limit], start=1):
        arrives = day_text(date.fromisoformat(view["delivery_date"])) if view["delivery_date"] else "no date"
        lines.append(f"{position}. **{view['title']}** ({view['product_id']}), {rupees(view['total_price'])} delivered, "
                     f"arrives {arrives}. {view['reason']}")
        for tick in view["ticks"]:
            mark = {"pass": "yes", "fail": "no", "unclear": "not sure"}[tick["status"]]
            lines.append(f"   - {tick['label']} ({mark}): {tick.get('proof', 'no evidence')}")
    return lines or ["None."]


def rejected_lines(views: list[dict]) -> list[str]:
    lines = []
    for view in views:
        proof = f" Proof: {view['proof']['proof']}" if view.get("proof") and view["proof"].get("proof") else ""
        lines.append(f"- {view['title']} ({view['product_id']}, {rupees(view['total_price'])}): {view['reason']}.{proof}")
    return lines or ["- None."]


def special_lines(saved: dict) -> list[str]:
    lines = []
    for row in saved["planted"]:
        if not row["asked"]:
            continue
        check = row["check"]["proof"] if row["check"] and row["check"].get("proof") else "not checked"
        lines.append(f"- Planted '{row['kind']}' on {row['title']} ({row['product_id']}): {row['where']}. "
                     f"Check: {check}. Planted review: \"{row['planted_quotes'][0]}\"")
    for row in saved["tricks"]:
        armor = "flagged" if row["model_armor_flagged"] else "missed"
        code = "marked" if row["code_rule_flagged"] else "missed"
        lines.append(f"- Trick review ({row['kind']}) on {row['title']} ({row['product_id']}): \"{row['text']}\" "
                     f"Model Armor {armor} it; our code rule {code} it. Outcome: {row['where']}.")
        for tick in row["ticks"]:
            lines.append(f"   - {tick['label']}: {tick['status']}, {tick.get('proof', 'no evidence')}")
    return lines


def timing_rows(name: str, block: dict, brief_ms: int | None) -> str:
    t, c = block["timings_ms"], block["checking"]
    total = t["total_ms"] + (brief_ms or 0)
    return (f"| {name} | {brief_ms if brief_ms is not None else ''} | {t['find_ms']} | {t['quick_ms']} | {t['check_ms']} "
            f"| {t['rank_ms']} | {total} | {c['saved_checks_used']} | {c['gemini_calls']} | {c['cost_usd']} |")


def eye_notes() -> dict[str, str]:
    return json.loads(EYE_CHECK.read_text(encoding="utf-8")) if EYE_CHECK.exists() else {}


def box_lines(boxes: list[dict], notes: dict[str, str]) -> list[str]:
    lines = []
    for box in boxes:
        name = Path(box["file"]).name
        coords = "; ".join(f"{b['label']} {b['box_2d']}" for b in box["boxes"]) or "no photo boxes"
        note = notes.get(name, "not looked at yet")
        lines.append(f"- [{name}](../demo/boxes/{name}) {box['title']}: {coords}. Eye check: {note}")
    return lines


def cold_difference(saved: dict, cold: dict) -> str:
    """How the live check's answer differs from the saved (batch) one, product by product."""
    parts = []
    for kind, words in (("matches", "full matches"), ("almost", "not sure")):
        before = {r["product_id"] for r in saved[kind]}
        after = {r["product_id"]: r["title"] for r in cold[kind]}
        if added := [f"{title} ({pid})" for pid, title in after.items() if pid not in before]:
            parts.append(f"also {words}: {', '.join(added)}")
        if removed := sorted(before - set(after)):
            parts.append(f"no longer {words}: {', '.join(removed)}")
    return ("Differs from the saved checks: " + "; ".join(parts) + ".") if parts else "Same answer as the saved checks."


def story_section(record: dict, notes: dict[str, str]) -> list[str]:
    story, saved = record["story"], record["saved"]
    today = date.fromisoformat(story["today"])
    lines = [
        f"## {story['title']} (`{story['id']}`)", "",
        f"> {story['shopper_text']}", "",
        f"Pincode {story['pincode']} ({record['city']}), set on {today:%A} {today.day} {today:%B %Y} at "
        f"{story['time']} India time. Shows: {story['shows']}. {'' if story['stage'] else '**Not for the stage.**'}", "",
        f"Expected: {story['expected']}", "",
        f"**Checklist chips:** {' · '.join(f'`{chip}`' for chip in saved['chips'])} "
        f"({saved['brief']['category']}, {saved['brief']['gender'] or 'any gender'})",
    ]
    if record["nice_to_haves"]:
        lines.append(f"Nice-to-haves: {', '.join(f'{n['attribute']}={n['value']}' for n in record['nice_to_haves'])}.")
    if record["not_checkable"]:
        lines.append(f"Shown as not checkable: {', '.join(record['not_checkable'])}.")
    if record["question"]:
        lines.append(f"Question asked: {record['question']}")
    if record.get("brief_error"):
        lines.append(f"**The live checklist call failed after {record['brief_failed_ms']} ms** "
                     f"({record['brief_error']}); this run used the checklist saved at precheck time.")
    if record["same_as_prechecked"] is False:
        lines.append(f"**The live checklist differed from the prechecked one** ({record['prechecked_checklist']}).")
    counts = saved["counts"]
    lines += [
        "", f"**What the app shows:** {counts_line(counts)}.", "",
        f"Why others were rejected: {reasons_text(counts['rejected'])}.", "",
        f"What to give up: {' · '.join(tradeoff_text(t) for t in saved['tradeoffs']) or 'no cards'}.", "",
        "### Full matches", "", *match_lines(saved["matches"]), "",
    ]
    if record["boxes"]:
        lines += ["Photo boxes (top matches):", "", *box_lines(record["boxes"], notes), ""]
    if saved["almost"]:
        lines += ["### Not sure", "", *match_lines(saved["almost"]), ""]
    lines += ["### Checked and rejected", "", *rejected_lines(saved["rejected_after_checking"]), ""]
    if special := special_lines(saved):
        lines += ["### Planted problems and trick reviews in this search", "", *special, ""]
        s = saved["screening"]
        if s["model_armor"] or s["code_rule"]:
            lines += [f"Across the {s['reviews_read']} reviews the careful check read: Model Armor flagged "
                      f"{s['model_armor']}, our code rule marked {s['code_rule']} ({s['code_rule_only']} that Model Armor "
                      f"missed, {s['model_armor_only']} only Model Armor caught). Marked reviews are shown to Gemini as "
                      "suspicious and never count as evidence.", ""]
    lines += ["### Timings (ms)", "",
              "| Run | Checklist (live Gemini) | Find | Quick check | Careful check | Rank + trade-offs | Total "
              "| Saved checks used | Gemini calls | Cost US$ |",
              "|---|---|---|---|---|---|---|---|---|---|",
              timing_rows("Saved checks", saved, record["brief_ms"])]
    if cold := record.get("cold"):
        lines.append(timing_rows("Cold (nothing saved)", cold, record["brief_ms"]))
        lines += ["", f"Cold run (live lite): {counts_line(cold['counts'])}. {cold_difference(saved, cold)}"]
    lines.append("")
    for step in record["follow_ups"]:
        action = f"taps \"{tradeoff_text(step['card_offered'])}\"" if step.get("card_offered") else (
            f"taps {step['tap']} (no such card was offered)" if "tap" in step else f"says \"{step['say']}\"")
        if "error" in step:
            lines += [f"### Then the shopper {action}", "", f"The live checklist call failed: {step['error']}", ""]
            continue
        lines += [f"### Then the shopper {action}", "",
                  f"Chips: {' · '.join(f'`{chip}`' for chip in step['chips'])}. {counts_line(step['counts'])}.", "",
                  f"Why others were rejected: {reasons_text(step['counts']['rejected'])}.", "",
                  f"What to give up: {' · '.join(tradeoff_text(t) for t in step['tradeoffs']) or 'no cards'}.", "",
                  *match_lines(step["matches"], 3), ""]
        if step["boxes"]:
            lines += [*box_lines(step["boxes"], notes), ""]
        made = "a tap, no Gemini call" if "tap" in step else f"checklist change {step['brief_ms']} ms (live Gemini)"
        lines += [f"Timing: {made}, search {step['timings_ms']['total_ms']} ms, "
                  f"{step['checking']['saved_checks_used']} saved checks, {step['checking']['gemini_calls']} Gemini calls.", ""]
    return lines


def summary_rows(records: list[dict]) -> list[str]:
    lines = ["| Story | Stage | Found | Quick check removed | Checked | Matches | Not sure | Top rejection | Trade-offs "
             "| Checklist s | Search s (saved) | Search s (cold) |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for record in records:
        saved, counts = record["saved"], record["saved"]["counts"]
        top = next(iter(counts["rejected"].items()), ("none", 0))
        trades = "; ".join(tradeoff_text(t) for t in saved["tradeoffs"]) or "none"
        cold = record.get("cold")
        cold_s = f"{cold['timings_ms']['total_ms'] / 1000:.1f}" if cold else ""
        lines.append(f"| `{record['story']['id']}` | {'yes' if record['story']['stage'] else 'no'} | {counts['found']} "
                     f"| {counts['quick_dropped']} | {counts['checked']} | {counts['passed']} | {counts['unclear']} "
                     f"| {top[0]} ({top[1]}) | {trades} | {record['brief_ms'] / 1000:.1f} "
                     f"| {saved['timings_ms']['total_ms'] / 1000:.1f} | {cold_s} |")
    return lines


def run_report(args: argparse.Namespace) -> int:
    records = [json.loads((OUT / f"{s.id}.json").read_text(encoding="utf-8"))
               for s in load_stories() if (OUT / f"{s.id}.json").exists()]
    notes = eye_notes()
    intro = OUT / "report_intro.md"
    lines = ["# Demo stories (IMPLEMENTATION.md step 8.1)", ""]
    if intro.exists():
        lines += [intro.read_text(encoding="utf-8").strip(), ""]
    lines += ["Seconds include the live checklist call. 'Saved' runs read the batch-prechecked checks in Firestore "
              "`checks`; 'cold' runs check every product live (lite, flash for the last try) with nothing saved.", "",
              *summary_rows(records), ""]
    if BRIEF_LATENCY.exists():
        lines += ["Checklist call (live Gemini, the app's 25 s timeout, no retries), seconds per try:", "",
                  "| Story | Exact words | Tries |", "|---|---|---|"]
        for story_id, row in json.loads(BRIEF_LATENCY.read_text(encoding="utf-8")).items():
            tries = ", ".join(f"{t['seconds']}" if t["ok"] else f"failed ({t['seconds']})" for t in row["tries"])
            lines.append(f"| `{story_id}` | {row['shopper_text']} | {tries} |")
        lines.append("")
    for log in sorted(OUT.glob("precheck-*.log")):
        if "dry-run" not in log.name:
            lines += [f"Precheck job `{log.stem}`:", "", "```", log.read_text(encoding="utf-8").strip(), "```", ""]
    for record in records:
        lines += story_section(record, notes)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT} ({len(records)} stories)")
    return 0


# ---------- Command line ----------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    pre = commands.add_parser("precheck", help="make the checklists, then batch-check what they could return")
    pre.add_argument("--stories", nargs="+", help="story ids (default: all)")
    pre.add_argument("--job", default=DEFAULT_JOB, help="batch job name; reused to reattach")
    pre.add_argument("--rebrief", action="store_true", help="make the checklists again even if saved")
    pre.add_argument("--dry-run", action="store_true", help="count what the batch would send, send nothing")
    run = commands.add_parser("run", help="run the stories end to end")
    run.add_argument("--stories", nargs="+", help="story ids (default: all)")
    run.add_argument("--cold", action="store_true", help="also run each story with nothing saved (live Gemini)")
    latency = commands.add_parser("brief-latency", help="time each story's checklist call (app timeout, no retries)")
    latency.add_argument("--stories", nargs="+", help="story ids (default: all)")
    latency.add_argument("--repeats", type=int, default=3)
    latency.add_argument("--parallel", type=int, default=6)
    commands.add_parser("report", help=f"write {REPORT}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    args = parse_args(argv)
    commands = {"precheck": run_precheck, "run": run_stories, "brief-latency": run_brief_latency, "report": run_report}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
