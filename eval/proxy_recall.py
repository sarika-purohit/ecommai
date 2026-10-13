"""Proxy recall for step 2.3 (Find) and false drops for step 2.4 (Quick check), before human labels exist.

    python -m eval.proxy_recall                    # every test brief, against the products collection
    python -m eval.proxy_recall --only watch       # briefs whose name contains 'watch'

Proxy matches stand in for eval/labels.jsonl until people mark the real answers (step 6.2). A proxy match passes
the hard filters, its saved details meet every must-have (catalogue or Gemini values, whatever the confidence) and
it's within budget and on time. Real recall needs the human labels: a product whose saved details are wrong or
'unknown' can truly match without being a proxy match.

The quick check reads the same saved details, so zero false drops against proxy matches mostly confirms the rule
logic (rules, shop data, thresholds); it isn't independent proof that the quick check never drops a true match.
"""

import argparse
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from dotenv import load_dotenv
from google.cloud.firestore_v1.base_query import FieldFilter

from agent import delivery
from agent.models import MustHave, NiceToHave, Product, ShoppingBrief
from agent.quick_check import CONFIDENT, first_broken, meets, quick_check, saved_value
from agent.retrieve import (
    DEFAULT_LIMIT,
    FETCH_LIMIT,
    PRODUCT_FIELDS,
    PRODUCTS,
    UNISEX,
    default_db,
    default_embedder,
    find_candidates,
    make_hit,
    stock_size,
)
from data_prep.embed import Embedder
from data_prep.schemas import SHOP_ATTRIBUTES

CHECKED_BY_CAREFUL_CHECK = 12
TARGET_RECALL = 0.9
PUNE = "411038"
# The step 2.3 soft limits, written out again so the evaluation checks agent/retrieve.py rather than reusing it.
MAX_OVER_BUDGET_PERCENT = 20
MAX_DAYS_LATE = 2


@dataclass(frozen=True)
class Case:
    name: str
    query: str
    brief: ShoppingBrief


def musts(*items: tuple[str, str, Any]) -> list[MustHave]:
    return [MustHave(id=f"m{n}", attribute=a, rule=r, value=v) for n, (a, r, v) in enumerate(items, start=1)]


def cases(today: date) -> list[Case]:
    """Test briefs written by hand (no Gemini), covering every group."""
    soon = today + timedelta(days=4)

    def case(
        name: str, query: str, category: str, gender: str | None, *items, rules: tuple[MustHave, ...] = (), **limits
    ) -> Case:
        must_haves = musts(*items) + list(rules)
        return Case(name, query, ShoppingBrief(category=category, gender=gender, must_haves=must_haves, **limits))

    office = [NiceToHave(attribute="usage", value="office")]
    cod_rule = MustHave(id="r1", attribute="cod", rule="present", value=True, from_rule=True)
    return [
        case(
            "kurta-cotton-pockets-3q", "women's cotton kurta with pockets and three-quarter sleeves under 1200",
            "Kurtas", "women", ("fabric", "is", "cotton"), ("pockets", "present", True),
            ("sleeve_length", "is", "three-quarter"), budget_max_inr=1200,
        ),
        case(
            "kurti-cotton-3q-M", "cotton kurti with 3/4 sleeves, size M", "Kurtis", "women",
            ("fabric", "is", "cotton"), ("sleeve_length", "is", "three-quarter"), size="M",
        ),
        case(
            "kurta-men-mandarin-cod", "men's cotton kurta with mandarin collar, delivered to Pune by the weekend",
            "Kurtas", "men", ("neckline", "is", "mandarin"), ("fabric", "is", "cotton"),
            rules=(cod_rule,), pincode=PUNE, deadline_date=soon,
        ),
        case(
            "shirt-men-checks-full-L", "men's full sleeve checked shirt for office, size L", "Shirts", "men",
            ("pattern", "is", "checks"), ("sleeve_length", "is", "full"), size="L", nice_to_haves=office,
        ),
        case(
            "shirt-women-cotton-not-loud", "women's cotton shirt, nothing too loud", "Shirts", "women",
            ("fabric", "is", "cotton"), ("print_intensity", "is_not", "loud"),
        ),
        case(
            "top-floral-sleeveless", "sleeveless floral top", "Tops", "women",
            ("pattern", "is", "floral"), ("sleeve_length", "is", "sleeveless"),
        ),
        case(
            "tshirt-men-polo-solid", "plain polo t-shirt for men under 800", "Tshirts", "men",
            ("neckline", "is", "polo"), ("pattern", "is", "solid"), budget_max_inr=800,
        ),
        case(
            "tshirt-women-stripes-round", "striped round neck t-shirt", "Tshirts", "women",
            ("pattern", "is", "stripes"), ("neckline", "is", "round"),
        ),
        case(
            "tunic-vneck-printed-L", "printed v-neck tunic, size L", "Tunics", "women",
            ("neckline", "is", "v-neck"), ("pattern", "is", "printed"), size="L",
        ),
        case(
            "jeans-men-slim-dark-32", "slim dark-wash jeans, waist 32", "Jeans", "men",
            ("fit", "is", "slim"), ("wash", "is", "dark"), size="32",
        ),
        case(
            "jeans-women-high-skinny", "high-rise skinny jeans", "Jeans", "women",
            ("rise", "is", "high"), ("fit", "is", "skinny"),
        ),
        case(
            "jeans-women-stretch-no-rips", "stretchy jeans without rips", "Jeans", "women",
            ("distressed", "absent", True), ("stretch", "is", "yes"),
        ),
        case(
            "trousers-men-cotton-slim", "men's slim cotton trousers for office", "Trousers", "men",
            ("fabric", "is", "cotton"), ("fit", "is", "slim"), nice_to_haves=office,
        ),
        case("trousers-women-ankle", "ankle length trousers", "Trousers", "women", ("length", "is", "ankle")),
        case(
            "sneakers-men-white-no-logo", "men's white sneakers with no logo", "Casual Shoes", "men",
            ("type", "is", "sneakers"), ("logo_visible", "absent", True),
        ),
        case(
            "formal-black-leather-laces-9", "black leather formal shoes with laces, UK 9", "Formal Shoes", "men",
            ("upper_material", "is", "leather"), ("closure", "is", "laces"), size="UK 9",
        ),
        case(
            "running-men-cushioned-9", "cushioned running shoes, size 9", "Sports Shoes", "men",
            ("type", "is", "running"), ("sole_type", "is", "cushioned"), size="9",
        ),
        case(
            "casual-women-slip-on", "slip-on casual shoes for women", "Casual Shoes", "women",
            ("closure", "is", "slip-on"),
        ),
        case(
            "sports-women-mesh-5", "women's mesh sports shoes, size 5, under 2500", "Sports Shoes", "women",
            ("upper_material", "is", "mesh"), size="5", budget_max_inr=2500,
        ),
        case(
            "watch-green-steel", "green dial watch with steel strap", "Watches", None,
            ("dial_colour", "is", "green"), ("strap_material", "is", "steel"),
        ),
        case(
            "watch-women-white-leather", "women's white dial watch with a leather strap", "Watches", "women",
            ("dial_colour", "is", "white"), ("strap_material", "is", "leather"),
        ),
        case(
            "watch-men-chrono-black", "black dial chronograph for men", "Watches", "men",
            ("chronograph", "is", "yes"), ("dial_colour", "is", "black"),
        ),
        case(
            "watch-women-rose-gold", "rose gold watch for women", "Watches", "women",
            ("case_material", "is", "rose-gold-tone"),
        ),
        case(
            "tote-zip", "tote bag with a zip", "Handbags", "women", ("type", "is", "tote"), ("closure", "is", "zip"),
        ),
        case(
            "backpack-3-compartments", "backpack with three or more compartments", "Backpacks", "men",
            ("compartments", "is", "three or more"),
        ),
        case(
            "sling-cod-returnable", "sling bag with cash on delivery and returns", "Handbags", "women",
            ("type", "is", "sling"), ("cod", "present", True), ("returnable", "present", True),
        ),
        case(
            "clutch-synthetic-leather", "synthetic leather clutch", "Clutches", "women",
            ("material", "is", "synthetic leather"),
        ),
        case(
            "aviator-polarised", "polarised aviator sunglasses", "Sunglasses", "men",
            ("frame_shape", "is", "aviator"), ("polarised", "is", "yes"),
        ),
        case(
            "sunglasses-women-oversized-gradient", "oversized sunglasses with gradient lenses", "Sunglasses", "women",
            ("frame_shape", "is", "oversized"), ("lens_colour", "is", "gradient"),
        ),
        case(
            "sunglasses-metal-returnable", "metal frame sunglasses I can return", "Sunglasses", "men",
            ("frame_material", "is", "metal"), ("returnable", "present", True),
        ),
    ]


# ---------- Proxy matches ----------


def in_hard_filters(brief: ShoppingBrief, product: Product, in_stock: list[str]) -> bool:
    """Written separately from agent.retrieve.hard_filters, so the evaluation also checks the filters."""
    if product.article_type != brief.category:
        return False
    if brief.gender and product.gender not in (brief.gender, UNISEX):
        return False
    size = stock_size(brief.category, brief.size) if brief.size else None
    return size is None or size in in_stock


def eligible(collection: Any, brief: ShoppingBrief) -> dict[str, Product]:
    """Every product that passes the hard filters, read without embeddings."""
    query = collection.where(filter=FieldFilter("articleType", "==", brief.category))
    found = {}
    for doc in query.select([*PRODUCT_FIELDS, "in_stock_sizes"]).stream():
        data = doc.to_dict()
        product = Product.model_validate({**data, "id": doc.id})
        if in_hard_filters(brief, product, data.get("in_stock_sizes") or []):
            found[product.id] = product
    return found


def is_proxy_match(brief: ShoppingBrief, product: Product) -> bool:
    if not all(meets(m, saved_value(product, m.attribute)) is True for m in brief.must_haves):
        return False
    hit = make_hit(brief, product, 0.0)
    return hit is not None and not hit.over_budget and not hit.late


def drop_basis(brief: ShoppingBrief, product: Product) -> str:
    broken = first_broken(brief.must_haves, product)
    if broken is None:
        return "not dropped"
    if broken.attribute in SHOP_ATTRIBUTES:
        return "shop data"
    saved = product.attributes[broken.attribute]
    return "catalogue" if saved.source == "catalogue" else f"gemini {saved.confidence:.2f}"


# ---------- Running the evaluation ----------


@dataclass
class Result:
    """Counts for one brief. 'top' is find_candidates' top 40 by distance; 'after quick check' is the first 40
    left when the quick check runs on everything fetched (FETCH_LIMIT) before the cut to 40."""

    name: str
    eligible: int
    proxy: int
    found: int
    found_after_quick_check: int
    found_fetched: int
    hits: int
    over_budget: int
    late: int
    dropped: int
    false_drops: list[str]
    false_drops_all_proxy: list[str]
    proxy_in_first_checked: int
    violations: list[str]
    timings: dict[str, float]
    drop_bases: Counter = field(default_factory=Counter)

    @property
    def reachable(self) -> int:
        """The most proxy matches 40 slots can hold."""
        return min(self.proxy, DEFAULT_LIMIT)


def evaluate_case(case: Case, collection: Any, db: Any, embedder: Embedder, name: str) -> Result:
    brief = case.brief
    products = eligible(collection, brief)
    proxy = {pid for pid, product in products.items() if is_proxy_match(brief, product)}
    timings: dict[str, float] = {}
    fetched = find_candidates(
        brief, query_text=case.query, limit=FETCH_LIMIT, db=db, embedder=embedder, collection=name, timings=timings
    )
    top = fetched[:DEFAULT_LIMIT]
    clock = time.perf_counter()
    _, dropped_top = quick_check(brief, top)
    timings["quick_check_ms"] = (time.perf_counter() - clock) * 1000
    kept_all, dropped_all = quick_check(brief, fetched)
    _, dropped_proxy = quick_check(brief, _as_hits(brief, products, proxy))
    return Result(
        name=case.name,
        eligible=len(products),
        proxy=len(proxy),
        found=len(proxy & _ids(top)),
        found_after_quick_check=len(proxy & _ids(kept_all[:DEFAULT_LIMIT])),
        found_fetched=len(proxy & _ids(fetched)),
        hits=len(top),
        over_budget=sum(hit.over_budget for hit in top),
        late=sum(hit.late for hit in top),
        dropped=len(dropped_top),
        false_drops=sorted(hit.product.id for hit, _ in dropped_all if hit.product.id in proxy),
        false_drops_all_proxy=sorted(hit.product.id for hit, _ in dropped_proxy),
        proxy_in_first_checked=sum(hit.product.id in proxy for hit in kept_all[:CHECKED_BY_CAREFUL_CHECK]),
        violations=_violations(brief, fetched, products),
        timings=timings,
        drop_bases=Counter(drop_basis(brief, hit.product) for hit, _ in dropped_top),
    )


def _ids(hits: list) -> set[str]:
    return {hit.product.id for hit in hits}


def _as_hits(brief: ShoppingBrief, products: dict[str, Product], ids: set[str]) -> list:
    return [hit for pid in sorted(ids) if (hit := make_hit(brief, products[pid], 0.0)) is not None]


def _violations(brief: ShoppingBrief, hits: list, products: dict[str, Product]) -> list[str]:
    problems = []
    for hit in hits:
        if hit.product.id not in products:
            problems.append(f"{hit.product.id} breaks a hard filter")
        budget, deadline = brief.budget_max_inr, brief.deadline_date
        if budget and hit.total_price * 100 > budget * (100 + MAX_OVER_BUDGET_PERCENT):
            problems.append(f"{hit.product.id} is more than {MAX_OVER_BUDGET_PERCENT}% over budget")
        if budget and (hit.total_price > budget) != hit.over_budget:
            problems.append(f"{hit.product.id} has the wrong over-budget mark")
        if deadline and hit.delivery_date and (hit.delivery_date - deadline).days > MAX_DAYS_LATE:
            problems.append(f"{hit.product.id} is more than {MAX_DAYS_LATE} days late")
        if deadline and hit.delivery_date and (hit.delivery_date > deadline) != hit.late:
            problems.append(f"{hit.product.id} has the wrong late mark")
    return problems


def evaluate(
    selected: list[Case], *, db: Any = None, embedder: Embedder | None = None, collection: str = PRODUCTS
) -> list[Result]:
    db = db or default_db()
    embedder = embedder or default_embedder()
    target = db.collection(collection)
    warm_up = selected[0]
    find_candidates(warm_up.brief, query_text=warm_up.query, db=db, embedder=embedder, collection=collection)
    return [evaluate_case(case, target, db, embedder, collection) for case in selected]


@dataclass(frozen=True)
class Summary:
    proxy: int
    reachable: int
    found: int
    found_after_quick_check: int
    found_fetched: int
    hits: int
    dropped: int
    false_drops: int
    false_drops_all_proxy: int
    violations: int

    @classmethod
    def of(cls, results: list[Result]) -> "Summary":
        return cls(
            proxy=sum(r.proxy for r in results),
            reachable=sum(r.reachable for r in results),
            found=sum(r.found for r in results),
            found_after_quick_check=sum(r.found_after_quick_check for r in results),
            found_fetched=sum(r.found_fetched for r in results),
            hits=sum(r.hits for r in results),
            dropped=sum(r.dropped for r in results),
            false_drops=sum(len(r.false_drops) for r in results),
            false_drops_all_proxy=sum(len(r.false_drops_all_proxy) for r in results),
            violations=sum(len(r.violations) for r in results),
        )


# ---------- Report ----------


def percent(part: float, whole: float) -> str:
    return f"{part / whole:.0%}" if whole else "n/a"


def report(results: list[Result]) -> str:
    header = (
        f"{'brief':<36} {'eligible':>8} {'proxy':>5} {'@40':>4} {'of max':>6} {'qc@40':>5} {'of max':>6} "
        f"{'@100':>4} {'over':>4} {'late':>4} {'drop':>4} {'false':>5} {'p@12':>4} "
        f"{'embed':>5} {'search':>6} {'total':>5}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.name:<36} {r.eligible:>8} {r.proxy:>5} {r.found:>4} {percent(r.found, r.reachable):>6} "
            f"{r.found_after_quick_check:>5} {percent(r.found_after_quick_check, r.reachable):>6} "
            f"{r.found_fetched:>4} {r.over_budget:>4} {r.late:>4} {r.dropped:>4} {len(r.false_drops):>5} "
            f"{r.proxy_in_first_checked:>4} {r.timings['text_embed_ms']:>5.0f} {r.timings['text_search_ms']:>6.0f} "
            f"{r.timings['total_ms']:>5.0f}"
        )
    s = Summary.of(results)
    bases = sum((r.drop_bases for r in results), Counter())
    mean_drop_share = statistics.mean(r.dropped / r.hits for r in results if r.hits)
    lines += [
        "",
        "Columns: @40 = proxy matches in find_candidates' top 40 (by distance); of max = that share of the most",
        "40 slots can hold, min(proxy, 40); qc@40 = proxy matches in the first 40 left when the quick check runs on",
        f"all {FETCH_LIMIT} fetched before the cut; @100 = proxy matches fetched; drop = quick-check drops in the top",
        f"40; false = proxy matches the quick check dropped; p@12 = first {CHECKED_BY_CAREFUL_CHECK} kept.",
        "",
        (
            f"briefs: {len(results)}, with proxy matches: {sum(1 for r in results if r.proxy)}; "
            f"proxy matches: {s.proxy} (40 slots per brief can hold {s.reachable})"
        ),
        (
            f"proxy recall@40, find alone: {percent(s.found, s.proxy)} of all proxy matches, "
            f"{percent(s.found, s.reachable)} of the most 40 slots can hold (target {TARGET_RECALL:.0%})"
        ),
        (
            f"proxy recall@40, quick check before the cut: {percent(s.found_after_quick_check, s.proxy)} of all, "
            f"{percent(s.found_after_quick_check, s.reachable)} of the most 40 slots can hold"
        ),
        f"proxy matches fetched ({FETCH_LIMIT} per search): {percent(s.found_fetched, s.proxy)}",
        (
            f"quick check: dropped {s.dropped} of {s.hits} top-40 candidates ({percent(s.dropped, s.hits)}), "
            f"mean per brief {mean_drop_share:.0%}"
        ),
        (
            f"quick-check false drops against proxy matches: {s.false_drops} among those fetched, "
            f"{s.false_drops_all_proxy} across all {s.proxy}"
        ),
        f"what the drops rested on (gemini threshold {CONFIDENT}): {dict(bases.most_common())}",
        f"filter or limit violations: {s.violations}",
    ]
    lines += [f"  {r.name}: {problem}" for r in results for problem in r.violations]
    lines.append("latency in ms (median / p90 / max): " + latency(results))
    return "\n".join(lines)


def latency(results: list[Result]) -> str:
    parts = []
    for stage in ("text_embed_ms", "text_search_ms", "limits_ms", "quick_check_ms", "total_ms"):
        values = sorted(r.timings[stage] for r in results)
        p90 = values[min(len(values) - 1, round(0.9 * (len(values) - 1)))]
        parts.append(f"{stage.removesuffix('_ms')} {statistics.median(values):.0f} / {p90:.0f} / {values[-1]:.0f}")
    return "; ".join(parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="only briefs whose name contains this text")
    parser.add_argument("--collection", default=PRODUCTS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    selected = [c for c in cases(delivery.india_time().date()) if not args.only or args.only in c.name]
    if not selected:
        print(f"no brief name contains {args.only!r}")
        return 1
    print(f"{len(selected)} briefs against {args.collection} ({os.environ.get('GOOGLE_CLOUD_PROJECT', '?')})\n")
    print(report(evaluate(selected, collection=args.collection)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
