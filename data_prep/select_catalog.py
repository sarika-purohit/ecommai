"""Pick a balanced set of products from the Kaggle Fashion Product Images dataset's styles.csv.

Run: python -m data_prep.select_catalog --styles downloads/styles.csv
"""

import argparse
import csv
import random
from collections import Counter
from itertools import zip_longest
from pathlib import Path

from data_prep.schemas import ARTICLE_TYPE_TO_GROUP, GROUPS, group_for

KEPT_GENDERS = ("Men", "Women", "Unisex")
NAME_COLUMN = "productDisplayName"
OUTPUT_COLUMNS = ["id", "title", "gender", "masterCategory", "subCategory", "articleType", "baseColour", "usage", "group"]
TYPE_ORDER = list(ARTICLE_TYPE_TO_GROUP)


def read_styles(path: Path) -> list[dict[str, str]]:
    """Read styles.csv, skipping rows with no id.

    Some product names contain unquoted commas, so those rows have extra fields; they are joined back into the name.
    """
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = [column.strip() for column in next(reader)]
        name_at = header.index(NAME_COLUMN)
        rows = []
        for fields in reader:
            row = dict(zip(header, (field.strip() for field in _repair(fields, len(header), name_at))))
            if row["id"]:
                rows.append(row)
    return rows


def _repair(fields: list[str], width: int, name_at: int) -> list[str]:
    extra = len(fields) - width
    if extra > 0:
        name = ",".join(fields[name_at : name_at + extra + 1])
        fields = fields[:name_at] + [name] + fields[name_at + extra + 1 :]
    return fields + [""] * (width - len(fields))


def fair_shares(total: int, available: dict[str, int], leftover_order: list[str] | None = None) -> dict[str, int]:
    """Split total as evenly as possible without giving any key more than it has.

    Whatever a key can't take is shared among the others. Single units that can't be split evenly go to keys in
    leftover_order (default: alphabetical).
    """
    shares = dict.fromkeys(available, 0)
    order = leftover_order or sorted(available)
    remaining = min(total, sum(available.values()))
    while remaining:
        open_keys = [key for key in order if shares[key] < available[key]]
        share = remaining // len(open_keys)
        if share == 0:
            for key in open_keys[:remaining]:
                shares[key] += 1
            break
        for key in open_keys:
            given = min(share, available[key] - shares[key])
            shares[key] += given
            remaining -= given
    return shares


def split_by_gender(quota: int, available: dict[str, int]) -> dict[str, int]:
    """Half men and half women where possible. Unisex items fill any gap first, then the other gender does."""
    half = quota // 2
    take = {"Men": min(half, available["Men"]), "Women": min(half, available["Women"])}
    take["Unisex"] = min(quota - take["Men"] - take["Women"], available["Unisex"])
    room = {gender: available[gender] - take[gender] for gender in ("Men", "Women")}
    for gender, extra in fair_shares(quota - sum(take.values()), room).items():
        take[gender] += extra
    return take


def _pools(rows: list[dict[str, str]]) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Eligible rows by article type and gender, each list sorted by id so the pick doesn't depend on file order."""
    pools: dict[str, dict[str, list[dict[str, str]]]] = {}
    seen: set[str] = set()
    for row in rows:
        if row["articleType"] not in ARTICLE_TYPE_TO_GROUP or row["gender"] not in KEPT_GENDERS:
            continue
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        by_gender = pools.setdefault(row["articleType"], {gender: [] for gender in KEPT_GENDERS})
        by_gender[row["gender"]].append(row)
    for by_gender in pools.values():
        for pool in by_gender.values():
            pool.sort(key=lambda row: row["id"])
    return {article_type: pools[article_type] for article_type in TYPE_ORDER if article_type in pools}


def _leftover_order(available: dict[str, int]) -> list[str]:
    """One article type per group in turn, fullest first, so even a tiny target reaches every group."""
    by_group: dict[str, list[str]] = {}
    for article_type in sorted(available, key=lambda t: (-available[t], t)):
        by_group.setdefault(ARTICLE_TYPE_TO_GROUP[article_type], []).append(article_type)
    return [t for turn in zip_longest(*by_group.values()) for t in turn if t is not None]


def _output_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "id": row["id"],
        "title": row[NAME_COLUMN],
        "gender": row["gender"].lower(),
        "masterCategory": row["masterCategory"],
        "subCategory": row["subCategory"],
        "articleType": row["articleType"],
        "baseColour": row["baseColour"],
        "usage": row["usage"],
        "group": group_for(row["articleType"]),
    }


def select(rows: list[dict[str, str]], target: int, cap: int, seed: int) -> list[dict[str, str]]:
    """An equal share of target per article type (at most cap each), balanced between men and women inside each type."""
    pools = _pools(rows)
    available = {t: min(cap, sum(len(pool) for pool in by_gender.values())) for t, by_gender in pools.items()}
    chosen = []
    for article_type, quota in fair_shares(target, available, _leftover_order(available)).items():
        by_gender = pools[article_type]
        counts = split_by_gender(quota, {gender: len(pool) for gender, pool in by_gender.items()})
        for gender, count in counts.items():
            rng = random.Random(f"{seed}:{article_type}:{gender}")
            chosen += rng.sample(by_gender[gender], count)
    chosen.sort(key=lambda row: (TYPE_ORDER.index(row["articleType"]), row["id"]))
    return [_output_row(row) for row in chosen]


def write_selected(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def summary(rows: list[dict[str, str]], target: int) -> str:
    by_type = Counter(row["articleType"] for row in rows)
    by_group = Counter(row["group"] for row in rows)
    by_gender = Counter(row["gender"] for row in rows)
    lines = [f"Selected {len(rows)} products (target {target})"]
    lines += [f"  {t} ({ARTICLE_TYPE_TO_GROUP[t]}): {by_type[t]}" for t in TYPE_ORDER if by_type[t]]
    lines.append("Groups: " + ", ".join(f"{group} {by_group[group]}" for group in GROUPS))
    lines.append("Gender: " + ", ".join(f"{gender} {by_gender[gender]}" for gender in ("men", "women", "unisex")))
    return "\n".join(lines)


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive whole number")
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pick a balanced set of products from styles.csv.")
    parser.add_argument("--styles", type=Path, required=True, help="styles.csv from the full Kaggle dataset")
    parser.add_argument("--out", type=Path, default=Path("data/selected.csv"), help="where to write the chosen products")
    parser.add_argument("--target", type=_positive_int, default=3000, help="how many products to pick")
    parser.add_argument("--cap", type=_positive_int, default=400, help="most products per article type")
    parser.add_argument("--seed", type=int, default=42, help="same seed, same pick")
    args = parser.parse_args(argv)

    selected = select(read_styles(args.styles), args.target, args.cap, args.seed)
    write_selected(selected, args.out)
    print(summary(selected, args.target))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
