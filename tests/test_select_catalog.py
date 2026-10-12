import csv
import random
from collections import Counter
from pathlib import Path

import pytest

from data_prep.schemas import ARTICLE_TYPE_TO_GROUP, GROUPS
from data_prep.select_catalog import OUTPUT_COLUMNS, fair_shares, main, read_styles, select, split_by_gender

STYLES = Path(__file__).parent / "fixtures" / "catalog" / "styles.csv"


@pytest.fixture(scope="module")
def styles() -> list[dict[str, str]]:
    return read_styles(STYLES)


@pytest.fixture(scope="module")
def eligible(styles) -> Counter:
    """(articleType, gender) counts of rows we may pick, ids deduplicated."""
    unique = {r["id"]: r for r in styles if r["articleType"] in ARTICLE_TYPE_TO_GROUP}
    return Counter((r["articleType"], r["gender"]) for r in unique.values() if r["gender"] in ("Men", "Women", "Unisex"))


def test_rows_with_extra_commas_keep_the_whole_name(styles):
    by_id = {r["id"]: r for r in styles}
    assert by_id["39386"]["productDisplayName"] == "Ray Ban Men Sunglasses, Black, Aviator"
    assert by_id["39386"]["articleType"] == "Sunglasses" and by_id["39386"]["usage"] == "Casual"
    assert by_id["39387"]["productDisplayName"] == "Titan Raga Women Watch, Silver Dial"


def test_rows_without_an_id_are_skipped(styles):
    assert all(r["id"] for r in styles)
    assert not any(r["productDisplayName"] == "Orphan Men Blue Shirt" for r in styles)


def test_empty_fields_stay_empty(styles):
    kurta = next(r for r in styles if r["id"] == "39388")
    assert kurta["usage"] == "" and kurta["productDisplayName"] == "Fabindia Women Green Kurta"


def test_picks_exactly_the_target_when_enough_exist(styles):
    assert len(select(styles, target=200, cap=20, seed=42)) == 200


def test_picks_everything_allowed_when_the_target_is_too_big(styles, eligible):
    per_type = Counter()
    for (article_type, _), n in eligible.items():
        per_type[article_type] += n
    selected = select(styles, target=10_000, cap=20, seed=42)
    assert len(selected) == sum(min(20, n) for n in per_type.values())


def test_no_article_type_goes_over_the_cap(styles):
    counts = Counter(r["articleType"] for r in select(styles, target=10_000, cap=15, seed=42))
    assert max(counts.values()) == 15


def test_no_duplicate_ids_and_every_row_has_a_group(styles):
    selected = select(styles, target=10_000, cap=400, seed=42)
    ids = [r["id"] for r in selected]
    assert len(ids) == len(set(ids))
    assert all(r["group"] == ARTICLE_TYPE_TO_GROUP[r["articleType"]] for r in selected)


@pytest.mark.parametrize("target", [6, 20, 200])
def test_every_group_is_present(styles, target):
    assert {r["group"] for r in select(styles, target=target, cap=400, seed=42)} == set(GROUPS)


def test_excluded_types_and_genders_are_dropped(styles):
    assert {"Socks", "Briefs"} <= {r["articleType"] for r in styles}
    assert {"Boys", "Girls"} <= {r["gender"] for r in styles}
    selected = select(styles, target=10_000, cap=400, seed=42)
    assert {r["articleType"] for r in selected} <= set(ARTICLE_TYPE_TO_GROUP)
    assert {r["gender"] for r in selected} <= {"men", "women", "unisex"}


def test_men_and_women_are_balanced_within_each_type(styles, eligible):
    picked = Counter((r["articleType"], r["gender"]) for r in select(styles, target=200, cap=20, seed=42))
    for article_type in ARTICLE_TYPE_TO_GROUP:
        men, women = picked[(article_type, "men")], picked[(article_type, "women")]
        men_exhausted = men == eligible[(article_type, "Men")]
        women_exhausted = women == eligible[(article_type, "Women")]
        assert abs(men - women) <= 1 or men_exhausted or women_exhausted, article_type


def test_types_share_the_target_equally_where_they_can(styles):
    counts = Counter(r["articleType"] for r in select(styles, target=200, cap=20, seed=42))
    # Kurtis (10), Tunics (4), Clutches (5) and Formal Shoes (12) are all taken; the rest share what's left.
    assert (counts["Kurtis"], counts["Tunics"], counts["Clutches"], counts["Formal Shoes"]) == (10, 4, 5, 12)
    assert {n for t, n in counts.items() if t not in ("Kurtis", "Tunics", "Clutches", "Formal Shoes")} <= {14, 15}


def test_same_seed_gives_the_same_pick_whatever_the_row_order(styles):
    shuffled = styles[:]
    random.Random(1).shuffle(shuffled)
    assert select(styles, target=150, cap=20, seed=42) == select(shuffled, target=150, cap=20, seed=42)


def test_a_different_seed_gives_a_different_pick(styles):
    first = {r["id"] for r in select(styles, target=150, cap=20, seed=1)}
    second = {r["id"] for r in select(styles, target=150, cap=20, seed=2)}
    assert first != second


def test_output_rows_use_our_column_names(styles):
    selected = select(styles, target=10_000, cap=400, seed=42)
    assert all(list(r) == OUTPUT_COLUMNS for r in selected)
    sunglasses = next(r for r in selected if r["id"] == "39386")
    assert sunglasses["title"] == "Ray Ban Men Sunglasses, Black, Aviator"
    assert sunglasses["gender"] == "men" and sunglasses["group"] == "sunglasses"


def test_fair_shares_passes_on_what_a_key_cannot_take():
    assert fair_shares(10, {"a": 1, "b": 10, "c": 10}, ["a", "b", "c"]) == {"a": 1, "b": 5, "c": 4}
    assert fair_shares(100, {"a": 3, "b": 4}) == {"a": 3, "b": 4}
    assert fair_shares(0, {"a": 3}) == {"a": 0}


def test_split_by_gender_fills_gaps_with_unisex_before_the_other_gender():
    assert split_by_gender(10, {"Men": 20, "Women": 2, "Unisex": 3}) == {"Men": 5, "Women": 2, "Unisex": 3}
    assert split_by_gender(10, {"Men": 20, "Women": 2, "Unisex": 1}) == {"Men": 7, "Women": 2, "Unisex": 1}
    assert split_by_gender(10, {"Men": 20, "Women": 20, "Unisex": 20}) == {"Men": 5, "Women": 5, "Unisex": 0}


def test_cli_writes_the_same_csv_every_run(tmp_path, capsys):
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    for out in (first, second):
        main(["--styles", str(STYLES), "--out", str(out), "--target", "200", "--cap", "20"])
    assert first.read_bytes() == second.read_bytes()
    with first.open(newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == OUTPUT_COLUMNS
        assert sum(1 for _ in reader) == 200
    assert "Selected 200 products" in capsys.readouterr().out
