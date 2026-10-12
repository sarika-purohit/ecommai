import csv
import json
from pathlib import Path

import pytest

from data_prep import read_details
from data_prep.read_details import (
    RULES,
    case_size,
    coverage_report,
    html_to_text,
    local_styles,
    map_attributes,
    normalise,
    parse_style,
    price,
    read_selected,
    run,
)
from data_prep.schemas import is_allowed

STYLES = Path(__file__).parent / "fixtures" / "styles_json"
SELECTED_COLUMNS = [
    "id", "title", "gender", "masterCategory", "subCategory", "articleType", "baseColour", "usage", "group"
]
ROW_KEYS = {"id", "brand", "description", "mrp", "selling_price", "attributes", "raw_attributes"}

FIXTURE_PRODUCTS = [
    ("15970", "Turtle Shirt", "Men", "Apparel", "Topwear", "Shirts", "Navy Blue", "Casual", "tops"),
    ("53819", "Ayaany Kurti", "Women", "Apparel", "Topwear", "Kurtis", "Green", "Ethnic", "tops"),
    ("37290", "CASIO Watch", "Men", "Accessories", "Watches", "Watches", "Black", "Casual", "watches"),
    ("40352", "Tokyo Talkies Jeans", "Women", "Apparel", "Bottomwear", "Jeans", "Blue", "Casual", "bottoms"),
    ("34782", "Be For Bag Totes", "Women", "Accessories", "Bags", "Handbags", "Green", "Casual", "bags"),
]
MISSING_PRODUCT = ("999", *FIXTURE_PRODUCTS[0][1:])


def style(product_id: str) -> dict:
    return json.loads((STYLES / f"{product_id}.json").read_text(encoding="utf-8"))


def parsed_row(product_id: str, article_type: str) -> dict:
    return parse_style(product_id, style(product_id), article_type).row


def write_selected(path: Path, products) -> Path:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SELECTED_COLUMNS)
        writer.writerows(products)
    return path


# ---------- Synonym table ----------


SYNONYMS = [
    ("tops", "Sleeve Length", "3/4th Sleeves", "sleeve_length", "three-quarter"),
    ("tops", "Sleeve Length", "Three-Quarter Sleeves", "sleeve_length", "three-quarter"),
    ("tops", "Sleeve Length", "Short Sleeves", "sleeve_length", "short"),
    ("tops", "Sleeve Length", "Long Sleeves", "sleeve_length", "full"),
    ("tops", "Sleeve Length", "Sleeveless", "sleeve_length", "sleeveless"),
    ("tops", "Sleeve Styling", "No Sleeves", "sleeve_length", "sleeveless"),
    ("tops", "Neck", "Round Neck", "neckline", "round"),
    ("tops", "Neck", "V-Neck", "neckline", "v-neck"),
    ("tops", "Neck", "v neck", "neckline", "v-neck"),
    ("tops", "Neck", "Mandarin Collar", "neckline", "mandarin"),
    ("tops", "Neck", "Polo Collar", "neckline", "polo"),
    ("tops", "Neck", "Boat Neck", "neckline", "boat"),
    ("tops", "Collar", "Spread Collar", "neckline", "collar"),
    ("tops", "Fit", "Regular Fit", "fit", "regular"),
    ("tops", "Fit", "Slim Fit", "fit", "slim"),
    ("tops", "Shape", "A-Line", "fit", "a-line"),
    ("tops", "Pattern", "Solid", "pattern", "solid"),
    ("tops", "Pattern", "Striped", "pattern", "stripes"),
    ("tops", "Pattern", "Checked", "pattern", "checks"),
    ("tops", "Pattern", "Printed", "pattern", "printed"),
    ("tops", "Print or Pattern Type", "Floral", "pattern", "floral"),
    ("tops", "Print or Pattern Type", "Horizontal Stripes", "pattern", "stripes"),
    ("tops", "Fabric", "Cotton", "fabric", "cotton"),
    ("tops", "Fabric", "100% cotton", "fabric", "cotton"),
    ("tops", "Fabric", "Pure Cotton", "fabric", "cotton"),
    ("tops", "Fabric", "Poly Cotton", "fabric", "blend"),
    ("tops", "Fabric", "Blended", "fabric", "blend"),
    ("tops", "Fabric", "Viscose Rayon", "fabric", "rayon"),
    ("tops", "Fabric", "Dupion Silk", "fabric", "silk"),
    ("tops", "Transparency", "Opaque", "sheerness", "opaque"),
    ("tops", "Transparency", "Sheer", "sheerness", "see-through"),
    ("tops", "Occasion", "Ethnic", "usage", "ethnic"),
    ("bottoms", "Fit", "Skinny Fit", "fit", "skinny"),
    ("bottoms", "Fit", "Super Skinny Fit", "fit", "skinny"),
    ("bottoms", "Waist Rise", "Mid-Rise", "rise", "mid"),
    ("bottoms", "Distress", "Clean Look", "distressed", "none"),
    ("bottoms", "Distress", "Low Distress", "distressed", "light"),
    ("bottoms", "Stretch", "Non Stretchable", "stretch", "no"),
    ("bottoms", "Fabric 2", "Elastane", "stretch", "yes"),
    ("bottoms", "Shade", "Dark", "wash", "dark"),
    ("bottoms", "Occasion", "Formal", "usage", "office"),
    ("shoes", "Fastening", "Lace-Ups", "closure", "laces"),
    ("shoes", "Type", "Loafers", "type", "loafers"),
    ("shoes", "Type", "Derbys", "type", "derby"),
    ("shoes", "Sport", "Running", "type", "running"),
    ("shoes", "Material", "Suede", "upper_material", "suede"),
    ("shoes", "Occasion", "Everyday", "usage", "casual"),
    ("watches", "Display", "Analogue", "display", "analog"),
    ("watches", "Display", "Digital", "display", "digital"),
    ("watches", "Dial Colour", "Black", "dial_colour", "black"),
    ("watches", "Dial Colour", "Silver Toned", "dial_colour", "silver"),
    ("watches", "Dial Colour", "Pink", "dial_colour", "other"),
    ("watches", "Strap Material", "Stainless Steel", "strap_material", "steel"),
    ("watches", "Strap Material", "Leather", "strap_material", "leather"),
    ("watches", "Strap Colour", "Silver Toned", "strap_colour", "silver"),
    ("watches", "Dial Shape", "Rectangular", "case_shape", "rectangular"),
    ("watches", "Features", "Chronograph", "chronograph", "yes"),
    ("bags", "Type", "Shoulder Bag", "type", "shoulder"),
    ("bags", "Type", "Sling Bag", "type", "sling"),
    ("bags", "Material", "Synthetic Leather", "material", "synthetic leather"),
    ("bags", "Compartment Closure", "Zip", "closure", "zip"),
    ("bags", "Number of Main Compartments", "3 and more", "compartments", "three or more"),
    ("bags", "Size", "Regular", "size", "medium"),
    ("sunglasses", "Type", "Aviator Sunglasses", "frame_shape", "aviator"),
    ("sunglasses", "Type", "Rectangle Sunglasses", "frame_shape", "rectangle"),
    ("sunglasses", "Frame Material", "Metal", "frame_material", "metal"),
    ("sunglasses", "Lens Colour", "Grey", "lens_colour", "grey"),
    ("sunglasses", "Feature", "Polarised Lens", "polarised", "yes"),
]


def test_at_least_30_synonym_examples():
    assert len(SYNONYMS) >= 30


@pytest.mark.parametrize(("group", "key", "raw", "attribute", "expected"), SYNONYMS)
def test_synonym(group, key, raw, attribute, expected):
    attributes, unmapped = map_attributes(group, {key: raw})
    assert attributes == {attribute: expected}
    assert unmapped == {}


@pytest.mark.parametrize(
    ("group", "key", "raw"),
    [
        ("tops", "Neck", "Cowl Neck"),
        ("tops", "Length", "Regular"),
        ("tops", "Weave Pattern", "Jacquard"),
        ("watches", "Strap Material", "Synthetic"),
        ("shoes", "Material", "Synthetic"),
        ("sunglasses", "Type", "Other Sunglasses"),
        ("sunglasses", "Lens Colour", "Clear"),
    ],
)
def test_values_we_cant_map_stay_raw(group, key, raw):
    attributes, unmapped = map_attributes(group, {key: raw})
    assert attributes == {}
    assert unmapped == {key: raw}


def test_matching_ignores_case_hyphens_and_spaces():
    attributes, _ = map_attributes("bottoms", {"waist  rise": "mid rise", "FIT": "slim-fit"})
    assert attributes == {"rise": "mid", "fit": "slim"}


def test_more_specific_key_wins_for_the_same_detail():
    attributes, unmapped = map_attributes("tops", {"Pattern": "Printed", "Print or Pattern Type": "Floral"})
    assert attributes == {"pattern": "floral"}
    assert unmapped == {}

    attributes, _ = map_attributes("tops", {"Collar": "Spread Collar", "Neck": "Round Neck"})
    assert attributes == {"neckline": "round"}


def test_explicit_stretch_label_beats_stretch_fibre():
    attributes, _ = map_attributes("bottoms", {"Fabric 2": "Elastane", "Stretch": "Non Stretchable"})
    assert attributes == {"stretch": "no"}


def test_details_outside_the_groups_vocabulary_are_not_output():
    attributes, unmapped = map_attributes("bottoms", {"Occasion": "Sports", "Pattern": "Solid"})
    assert attributes == {}
    assert unmapped == {"Occasion": "Sports", "Pattern": "Solid"}


def test_disallowed_values_are_dropped(monkeypatch):
    bad_rules = {"tops": [("neck", "neckline", {"round neck": "circle"}), ("fabric", "fabric", {"cotton": "cotton"})]}
    monkeypatch.setattr(read_details, "LOOKUP", bad_rules)
    attributes, _ = map_attributes("tops", {"Neck": "Round Neck", "Fabric": "Cotton"})
    assert attributes == {"fabric": "cotton"}


def test_unknown_group_maps_nothing():
    assert map_attributes(None, {"Fabric": "Cotton"}) == ({}, {"Fabric": "Cotton"})


def test_every_value_in_the_synonym_table_is_in_our_vocabulary():
    for group, rules in RULES.items():
        for key, attribute, values in rules:
            for raw, ours in values.items():
                assert is_allowed(group, attribute, ours), f"{group}: {key}={raw} -> {attribute}={ours}"


def test_no_raw_value_is_listed_twice_with_different_meanings():
    for group, rules in RULES.items():
        for key, attribute, values in rules:
            seen: dict[str, str] = {}
            for raw, ours in values.items():
                assert seen.setdefault(normalise(raw), ours) == ours, f"{group}: {key}={raw}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Case diameter: 25 mm\nDial shape: Round", "small"),
        ("Case diameter: 36 mm", "medium"),
        ("Case diameter: 42 mm", "medium"),
        ("Case diameter: 44.5 mm", "large"),
        ("Dial width: 45 mm", "large"),
        ("Dial diameter: 28 mm", None),
        ("Water resistant up to 50 m", None),
        (None, None),
    ],
)
def test_watch_case_size_from_description(text, expected):
    assert case_size(text) == expected


# ---------- Real styles JSON (trimmed copies of files from the dataset) ----------


def test_shirt_fixture():
    row = parsed_row("15970", "Shirts")
    assert set(row) == ROW_KEYS
    assert (row["id"], row["brand"], row["mrp"], row["selling_price"]) == ("15970", "Turtle", 1195, 1195)
    assert row["attributes"] == {
        "sleeve_length": "full",
        "neckline": "collar",
        "pattern": "checks",
        "fabric": "cotton",
        "fit": "slim",
    }
    assert row["raw_attributes"]["Collar"] == "Spread Collar"
    assert row["description"].startswith("Composition\nDark brown and indigo blue plaid check shirt")


def test_kurti_fixture_has_discount_and_clean_description():
    row = parsed_row("53819", "Kurtis")
    assert (row["mrp"], row["selling_price"]) == (499, 449)
    assert row["attributes"] == {"sleeve_length": "three-quarter", "neckline": "mandarin", "fabric": "cotton"}
    assert "100% cotton" in row["description"]
    assert "The model (height 5'8 and chest 32) is wearing a size M" in row["description"]
    for junk in ("â", "ï", "\ufffd", "euro", "dagger", "&"):
        assert junk not in row["description"]


def test_watch_fixture():
    row = parsed_row("37290", "Watches")
    assert row["attributes"] == {
        "display": "analog",
        "dial_colour": "black",
        "case_shape": "round",
        "strap_material": "steel",
        "strap_colour": "silver",
        "case_size": "large",
    }
    assert row["raw_attributes"]["Features"] == "Reset Time"


def test_jeans_fixture():
    row = parsed_row("40352", "Jeans")
    assert (row["mrp"], row["selling_price"]) == (799, 319)
    assert row["attributes"] == {
        "fit": "slim",
        "rise": "low",
        "wash": "medium",
        "distressed": "light",
        "stretch": "no",
        "length": "full",
        "fabric": "cotton",
        "usage": "casual",
    }


def test_handbag_fixture_without_labels():
    row = parsed_row("34782", "Handbags")
    assert (row["brand"], row["mrp"], row["selling_price"]) == ("Be For Bag", 599, 593)
    assert row["attributes"] == {} and row["raw_attributes"] == {}
    assert "Set of 3 tote bags" in row["description"]


def test_group_falls_back_to_the_article_type_in_the_json():
    assert parse_style("53819", style("53819")).group == "tops"


def test_na_values_are_left_out_of_raw_attributes():
    data = {"data": {"articleAttributes": {"Fabric": "Cotton", "Lining": "NA", "Closure": " ", "Fit": None}}}
    assert parse_style("1", data, "Kurtas").row["raw_attributes"] == {"Fabric": "Cotton"}


# ---------- HTML, missing keys, prices ----------


def test_html_is_stripped_to_plain_text():
    html = (
        '<p style="text-align: justify;">Soft <strong>cotton</strong> kurta<br />Hand wash&nbsp;only &amp; '
        "dry in shade</p><ul><li>Two side pockets</li><li>Straight hem</li></ul>"
    )
    assert html_to_text(html) == "Soft cotton kurta\nHand wash only & dry in shade\nTwo side pockets\nStraight hem"


@pytest.mark.parametrize("html", [None, "", "<p>NA</p>", "<p> </p><br />"])
def test_empty_html_gives_none(html):
    assert html_to_text(html) is None


@pytest.mark.parametrize(
    "style_json",
    [
        {},
        {"data": None},
        {"data": []},
        {"meta": {"code": 200}, "data": {}},
        {"data": {"price": None, "discountedPrice": "", "brandName": "", "articleAttributes": None}},
        {"data": {"productDescriptors": {"description": {}}, "articleAttributes": []}},
        {"data": {"productDescriptors": "oops", "articleType": "Shirts"}},
    ],
)
def test_missing_keys_give_empty_values(style_json):
    row = parse_style("42", style_json, "Shirts").row
    assert row == {
        "id": "42",
        "brand": None,
        "description": None,
        "mrp": None,
        "selling_price": None,
        "attributes": {},
        "raw_attributes": {},
    }


def test_description_joins_the_useful_parts_in_order():
    descriptors = {
        "style_note": {"value": "<p>Pair it with jeans.</p>"},
        "size_fit_desc": {"value": "<p>Case diameter: 40 mm</p>"},
        "materials_care_desc": {"value": "<p>NA</p>"},
        "description": {"value": "<p>Blue dial</p>"},
    }
    row = parse_style("1", {"data": {"productDescriptors": descriptors}}, "Watches").row
    assert row["description"] == "Blue dial\nCase diameter: 40 mm"
    assert row["attributes"] == {"case_size": "medium"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1195, 1195),
        (1195.6, 1196),
        ("1195", 1195),
        ("1,195", 1195),
        ("Rs. 2,499.00", 2499),
        (0, None),
        (-5, None),
        (None, None),
        ("NA", None),
        (True, None),
        ({"value": 10}, None),
    ],
)
def test_prices_are_whole_rupees(raw, expected):
    parsed = price(raw)
    assert parsed == expected
    assert parsed is None or type(parsed) is int


def test_row_prices_are_ints():
    row = parse_style("1", {"data": {"price": 1299.0, "discountedPrice": "999"}}, "Tshirts").row
    assert (row["mrp"], row["selling_price"]) == (1299, 999)
    assert type(row["mrp"]) is int and type(row["selling_price"]) is int


# ---------- Running over products ----------


def test_run_reads_local_files_and_records_missing_ones(tmp_path):
    products = read_selected(write_selected(tmp_path / "selected.csv", [*FIXTURE_PRODUCTS, MISSING_PRODUCT]))
    result = run(products, local_styles(STYLES), workers=3, attempts=1)

    assert [row["id"] for row in result.rows] == ["15970", "53819", "37290", "40352", "34782"]
    assert result.failed == ["999"]
    assert result.groups == {"15970": "tops", "53819": "tops", "37290": "watches", "40352": "bottoms", "34782": "bags"}
    assert result.unmapped[("bottoms", "Fade", "Light Fade")] == 1
    assert ("tops", "Body or Garment Size", "Garment Measurements in") not in result.unmapped


def test_coverage_report_counts_fields_and_details(tmp_path):
    products = read_selected(write_selected(tmp_path / "selected.csv", FIXTURE_PRODUCTS))
    report = coverage_report(run(products, local_styles(STYLES), attempts=1))

    assert "Real details for 5 of 5 products" in report
    assert "  mrp: 5 of 5" in report
    assert "  discounted (selling_price below mrp): 3 of 5" in report
    assert "  tops (2 products)" in report
    assert "    fabric: 2 of 2" in report
    assert "    case_size: 1 of 1" in report
    assert "  bottoms: Fade = Light Fade: 1" in report


def test_main_writes_one_line_per_product(tmp_path, capsys):
    selected = write_selected(tmp_path / "selected.csv", [*FIXTURE_PRODUCTS, MISSING_PRODUCT])
    out = tmp_path / "data" / "real_details.jsonl"

    read_details.main(["--selected", str(selected), "--styles-dir", str(STYLES), "--out", str(out)])

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [line["id"] for line in lines] == ["15970", "53819", "37290", "40352", "34782"]
    assert all(set(line) == ROW_KEYS and isinstance(line["id"], str) for line in lines)
    assert (tmp_path / "data" / "real_details_failed.txt").read_text() == "999\n"
    printed = capsys.readouterr().out
    assert "Real details for 5 of 6 products" in printed
    assert "1 failed" in printed


def test_main_reads_from_the_dataset_zip_by_default(monkeypatch, tmp_path):
    selected = write_selected(tmp_path / "selected.csv", FIXTURE_PRODUCTS[:1])
    sources = []

    def fake_source(*args):
        sources.append(args)
        return lambda product_id: (STYLES / f"{product_id}.json").read_bytes()

    monkeypatch.setattr(read_details, "open_source", fake_source)
    read_details.main(["--selected", str(selected), "--out", str(tmp_path / "out.jsonl")])

    assert sources == [(
        "zip", "paramaggarwal/fashion-product-images-dataset/versions/1", "fashion-dataset/styles/{id}.json",
        Path("downloads/kagglehub"), Path("downloads/kaggle_zip_index.json"),
    )]
    assert json.loads((tmp_path / "out.jsonl").read_text(encoding="utf-8"))["brand"] == "Turtle"


def test_rate_limit_stops_retries_and_is_reported_once(capsys):
    from data_prep.kaggle_zip import KaggleRateLimited

    calls = []

    def limited(product_id):
        calls.append(product_id)
        raise KaggleRateLimited("1455")

    result = run([{"id": "1"}, {"id": "2"}], limited, workers=1, attempts=4, delay=0)

    assert calls == ["1", "2"]
    assert result.failed == ["1", "2"]
    assert "1455 seconds" in result.rate_limited
    assert capsys.readouterr().out.count("stopping:") == 1


# ---------- Real services (run with: pytest -m live) ----------


@pytest.mark.live
def test_live_kaggle_styles_json(tmp_path):
    from data_prep.get_photos import DATASET, KaggleFiles
    from data_prep.read_details import STYLES_PATH, load_json

    data = KaggleFiles(DATASET, STYLES_PATH, cache_dir=tmp_path)("15970")
    row = parse_style("15970", load_json(data), "Shirts").row
    assert row["brand"] == "Turtle"
    assert row["attributes"]["pattern"] == "checks"
