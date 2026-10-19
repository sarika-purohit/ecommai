"""Human spot check of Gemini's product details (IMPLEMENTATION.md step 1.4).

Build a page where people mark each detail as right, wrong or "should be unknown":
    python -m data_prep.spot_check build --sample 100

Score the results people downloaded from that page:
    python -m data_prep.spot_check score data/spot_check_results*.json
"""

import argparse
import csv
import html
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from data_prep.schemas import UNKNOWN

TARGET_ACCURACY = 0.85


def read_jsonl(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return {str(row["id"]): row for row in map(json.loads, f) if row}


def pick_sample(enriched: dict[str, dict], selected: dict[str, dict], size: int, seed: int) -> list[str]:
    by_group: dict[str, list[str]] = defaultdict(list)
    for product_id in sorted(enriched, key=int):
        by_group[selected[product_id]["group"]].append(product_id)
    rng = random.Random(seed)
    sample = []
    for group, share in sorted(group_shares(by_group, size).items()):
        sample += rng.sample(by_group[group], share)
    rng.shuffle(sample)
    return sample


def group_shares(by_group: dict[str, list[str]], size: int, minimum: int = 3) -> dict[str, int]:
    """Split the sample across groups in proportion to their size, at least `minimum` each, adding up to `size`."""
    total = sum(len(ids) for ids in by_group.values())
    size = min(size, total)
    exact = {g: size * len(ids) / total for g, ids in by_group.items()}
    shares = {g: min(len(by_group[g]), max(minimum, int(exact[g]))) for g in by_group}
    by_remainder = sorted(by_group, key=lambda g: exact[g] - int(exact[g]), reverse=True)
    while sum(shares.values()) < size:
        grew = False
        for g in by_remainder:
            if sum(shares.values()) < size and shares[g] < len(by_group[g]):
                shares[g] += 1
                grew = True
        if not grew:
            break
    while sum(shares.values()) > size:
        g = max(shares, key=lambda k: shares[k] - exact[k])
        shares[g] -= 1
    return shares


def page_data(sample: list[str], enriched: dict, selected: dict, photos: dict) -> list[dict]:
    return [
        {
            "id": product_id,
            "title": selected[product_id]["title"],
            "type": selected[product_id]["articleType"],
            "photo": photos[product_id]["gemini"],
            "original": photos[product_id]["original"],
            "details": [
                {"name": name, **{k: detail.get(k) for k in ("value", "confidence", "source", "note")}}
                for name, detail in enriched[product_id]["attributes"].items()
            ],
        }
        for product_id in sample
    ]


def build_page(products: list[dict]) -> str:
    template = (Path(__file__).parent / "spot_check_template.html").read_text(encoding="utf-8")
    payload = json.dumps(products, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("__PRODUCTS__", payload).replace("__COUNT__", html.escape(str(len(products))))


def score(results: list[dict]) -> dict:
    counts: dict[str, Counter] = defaultdict(Counter)
    for mark in results:
        if mark.get("verdict") not in ("right", "wrong", "unknown"):
            continue
        seen = "unknown" if mark.get("value") == UNKNOWN else "visible"
        counts[mark.get("group") or "no group"][(seen, mark["verdict"])] += 1
        counts["all"][(seen, mark["verdict"])] += 1
    summary = {}
    for group, c in sorted(counts.items()):
        visible_right = c[("visible", "right")]
        visible_marked = visible_right + c[("visible", "wrong")] + c[("visible", "unknown")]
        summary[group] = {
            "visible_details_marked": visible_marked,
            "visible_accuracy": round(visible_right / visible_marked, 3) if visible_marked else None,
            "guessed_when_should_be_unknown": c[("visible", "unknown")],
            "unknown_details_marked": c[("unknown", "right")] + c[("unknown", "wrong")] + c[("unknown", "unknown")],
            "missed_visible_details": c[("unknown", "wrong")],
        }
    return summary


def load_results(paths: list[Path], selected: dict[str, dict]) -> list[dict]:
    latest: dict[tuple[str, str, str], dict] = {}
    for path in paths:
        for mark in json.loads(path.read_text(encoding="utf-8"))["marks"]:
            mark["group"] = selected.get(str(mark["id"]), {}).get("group")
            latest[(mark.get("reviewer", ""), str(mark["id"]), mark["name"])] = mark
    return list(latest.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--enriched", type=Path, default=Path("data/enriched.jsonl"))
    build.add_argument("--selected", type=Path, default=Path("data/selected.csv"))
    build.add_argument("--photos", type=Path, default=Path("data/photos.jsonl"))
    build.add_argument("--sample", type=int, default=100)
    build.add_argument("--seed", type=int, default=100)
    build.add_argument("--out", type=Path, default=Path("data/spot_check.html"))
    scorer = sub.add_parser("score")
    scorer.add_argument("results", type=Path, nargs="+")
    scorer.add_argument("--selected", type=Path, default=Path("data/selected.csv"))
    args = parser.parse_args()

    with args.selected.open(encoding="utf-8") as f:
        selected = {row["id"]: row for row in csv.DictReader(f)}

    if args.command == "build":
        enriched = read_jsonl(args.enriched)
        photos = read_jsonl(args.photos)
        sample = pick_sample({k: v for k, v in enriched.items() if k in photos}, selected, args.sample, args.seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(build_page(page_data(sample, enriched, selected, photos)), encoding="utf-8")
        print(f"Wrote {args.out} with {len(sample)} products")
        return

    summary = score(load_results(args.results, selected))
    for group, s in summary.items():
        accuracy = s["visible_accuracy"]
        verdict = "" if accuracy is None else (" (meets target)" if accuracy >= TARGET_ACCURACY else " (below target)")
        print(f"{group:10} visible details right: {accuracy}{verdict}  marked {s['visible_details_marked']}, "
              f"guessed hidden {s['guessed_when_should_be_unknown']}, missed visible {s['missed_visible_details']}")


if __name__ == "__main__":
    main()
