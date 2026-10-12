"""Step 1.3: read the real catalogue details from the dataset's styles/{id}.json files.

For each product in data/selected.csv this pulls the brand, description, prices and the labels
Myntra's catalogue team entered (`articleAttributes`), maps the labels to our vocabulary
(data_prep/schemas.py) and writes one line per product to data/real_details.jsonl. It then prints
how many products have each field, and the most common labels it couldn't map, so the synonym
table below can be extended.

    python -m data_prep.read_details                          # styles JSON from Kaggle
    python -m data_prep.read_details --styles-dir some/dir/   # or local {id}.json files
"""

import argparse
import csv
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv

from data_prep.get_photos import DATASET, add_source_args, open_source, with_retries
from data_prep.kaggle_zip import KaggleRateLimited
from data_prep.schemas import VOCABULARY, group_for, is_allowed

STYLES_PATH = "fashion-dataset/styles/{id}.json"
DESCRIPTION_PARTS = ("description", "materials_care_desc", "size_fit_desc")

# ---------- Synonym table: the dataset's wording -> our vocabulary ----------
#
# Each group lists (raw key, our attribute, {raw value: our value}). Matching ignores case,
# hyphens and extra spaces. When several raw keys feed one attribute, the earlier key wins.

SLEEVE_LENGTH = {
    "Sleeveless": "sleeveless",
    "No Sleeves": "sleeveless",
    "Short Sleeves": "short",
    "Half Sleeves": "short",
    "Cap Sleeves": "short",
    "Elbow Sleeves": "elbow",
    "Elbow Length Sleeves": "elbow",
    "Three-Quarter Sleeves": "three-quarter",
    "3/4th Sleeves": "three-quarter",
    "3/4 Sleeves": "three-quarter",
    "Three Fourth Sleeves": "three-quarter",
    "Long Sleeves": "full",
    "Full Sleeves": "full",
}
NECKLINE = {
    "Round Neck": "round",
    "Crew Neck": "round",
    "Scoop Neck": "round",
    "V-Neck": "v-neck",
    "Shirt Collar": "collar",
    "Spread Collar": "collar",
    "Button-Down Collar": "collar",
    "Cutaway Collar": "collar",
    "Club Collar": "collar",
    "Peter Pan Collar": "collar",
    "Mandarin Collar": "mandarin",
    "Band Collar": "mandarin",
    "Chinese Collar": "mandarin",
    "Boat Neck": "boat",
    "Square Neck": "square",
    "Keyhole Neck": "keyhole",
    "Henley Neck": "henley",
    "Henley": "henley",
    "Polo Collar": "polo",
    "Cowl Neck": "cowl",
    "Cowl": "cowl",
}
PATTERN = {
    "Solid": "solid",
    "Plain": "solid",
    "Striped": "stripes",
    "Stripes": "stripes",
    "Horizontal Stripes": "stripes",
    "Vertical Stripes": "stripes",
    "Pinstripes": "stripes",
    "Checked": "checks",
    "Checks": "checks",
    "Buffalo Checks": "checks",
    "Gingham Checks": "checks",
    "Tartan Checks": "checks",
    "Floral": "floral",
    "Floral Print": "floral",
    "Geometric": "geometric",
    "Geometric Print": "geometric",
    "Abstract": "abstract",
    "Abstract Print": "abstract",
    "Embroidered": "embroidered",
    "Printed": "printed",
    "Graphic": "printed",
    "Graphic Print Others": "printed",
    "Typography": "printed",
    "Typography or Slogan Prints": "printed",
    "Conversational": "printed",
    "Ethnic Motifs": "printed",
    "Paisley": "printed",
}
TOP_FABRIC = {
    "Cotton": "cotton",
    "Pure Cotton": "cotton",
    "100% Cotton": "cotton",
    "Organic Cotton": "cotton",
    "Linen": "linen",
    "Pure Linen": "linen",
    "Viscose Rayon": "rayon",
    "Rayon": "rayon",
    "Viscose": "rayon",
    "Modal": "rayon",
    "Polyester": "polyester",
    "Silk": "silk",
    "Pure Silk": "silk",
    "Dupion Silk": "silk",
    "Raw Silk": "silk",
    "Blended": "blend",
    "Blend": "blend",
    "Poly Cotton": "blend",
    "Polycotton": "blend",
    "Cotton Blend": "blend",
    "Cotton Linen": "blend",
    "Linen Blend": "blend",
    "Denim": "denim",
}
TOP_FIT = {
    "Regular Fit": "regular",
    "Regular": "regular",
    "Slim Fit": "slim",
    "Relaxed Fit": "relaxed",
    "Loose Fit": "relaxed",
    "Oversized Fit": "oversized",
    "Oversized": "oversized",
    "A-Line": "a-line",
    "Straight Fit": "straight",
    "Straight": "straight",
}
TOP_LENGTH = {
    "Crop": "crop",
    "Cropped": "crop",
    "Hip Length": "hip",
    "Thigh Length": "thigh",
    "Knee Length": "knee",
    "Calf Length": "calf",
    "Ankle Length": "ankle",
}
POCKETS = {
    "Patch": "patch",
    "Patch Pocket": "patch",
    "Side": "side",
    "Side Pockets": "side",
    "Chest Pocket": "chest",
    "No Pocket": "none",
    "No Pockets": "none",
}
OCCASION = {
    "Casual": "casual",
    "Daily": "casual",
    "Everyday": "casual",
    "Formal": "office",
    "Work": "office",
    "Party": "party",
}
BOTTOM_FIT = {
    "Regular Fit": "regular",
    "Slim Fit": "slim",
    "Skinny Fit": "skinny",
    "Super Skinny Fit": "skinny",
    "Straight Fit": "straight",
    "Relaxed Fit": "relaxed",
    "Loose Fit": "relaxed",
    "Bootcut Fit": "bootcut",
    "Boot Cut Fit": "bootcut",
    "Bootcut": "bootcut",
    "Wide Leg Fit": "wide-leg",
    "Wide Leg": "wide-leg",
    "Tapered Fit": "tapered",
    "Carrot Fit": "tapered",
}
BOTTOM_FABRIC = {
    "Cotton": "cotton",
    "Pure Cotton": "cotton",
    "100% Cotton": "cotton",
    "Denim": "denim",
    "Linen": "linen",
    "Polyester": "polyester",
    "Blended": "blend",
    "Poly Cotton": "blend",
    "Cotton Blend": "blend",
}
STRETCH_FIBRES = {"Elastane": "yes", "Lycra": "yes", "Spandex": "yes"}
SHOE_MATERIAL = {
    "Leather": "leather",
    "Genuine Leather": "leather",
    "Synthetic Leather": "synthetic leather",
    "PU": "synthetic leather",
    "Faux Leather": "synthetic leather",
    "Suede": "suede",
    "Synthetic Suede": "suede",
    "Faux Suede": "suede",
    "Mesh": "mesh",
    "Canvas": "canvas",
    "Knit": "knit",
    "Knitted": "knit",
    "Flyknit": "knit",
}
DIAL_COLOUR = {
    "Black": "black",
    "White": "white",
    "Silver": "silver",
    "Silver Toned": "silver",
    "Blue": "blue",
    "Navy Blue": "blue",
    "Green": "green",
    "Olive": "green",
    "Grey": "grey",
    "Gunmetal": "grey",
    "Brown": "brown",
    "Gold": "gold",
    "Gold Toned": "gold",
    "Rose Gold": "rose gold",
    "Rose Gold Toned": "rose gold",
    **dict.fromkeys(["Red", "Pink", "Purple", "Orange", "Yellow", "Maroon", "Copper", "Bronze", "Beige"], "other"),
    **dict.fromkeys(["Cream", "Off White", "Champagne", "Multi"], "other"),
}
STRAP_COLOUR = {
    "Black": "black",
    "Brown": "brown",
    "Tan": "brown",
    "Silver": "silver",
    "Silver Toned": "silver",
    "Gold": "gold",
    "Gold Toned": "gold",
    "Rose Gold": "rose gold",
    "Rose Gold Toned": "rose gold",
    "Blue": "blue",
    "Navy Blue": "blue",
    "White": "white",
    **dict.fromkeys(["Grey", "Red", "Pink", "Green", "Purple", "Orange", "Yellow", "Maroon"], "other"),
    **dict.fromkeys(["Beige", "Copper", "Bronze", "Cream", "Off White", "Multi"], "other"),
}
STRAP_MATERIAL = {
    "Leather": "leather",
    "Genuine Leather": "leather",
    "Stainless Steel": "steel",
    "Steel": "steel",
    "Metal": "steel",
    "Silicone": "silicone",
    "Fabric": "fabric",
    "Nylon": "fabric",
    "Canvas": "fabric",
    "Mesh": "mesh",
    "Ceramic": "ceramic",
}
CASE_SHAPE = {
    "Round": "round",
    "Square": "square",
    "Rectangular": "rectangular",
    "Rectangle": "rectangular",
    "Tonneau": "tonneau",
}
CASE_MATERIAL = {
    "Stainless Steel": "steel",
    "Steel": "steel",
    "Plastic": "plastic",
    "Gold Toned": "gold-tone",
    "Gold Plated": "gold-tone",
    "Rose Gold Toned": "rose-gold-tone",
}
CHRONOGRAPH = {"Chronograph": "yes"}
NOT_CHRONOGRAPH = dict.fromkeys(["Multi Function", "Multifunction", "Multi Functional"], "no")
BAG_CLOSURE = {
    "Zip": "zip",
    "Zipper": "zip",
    "Drawstring": "drawstring",
    "Flap": "flap",
    "Magnetic": "magnetic",
    "Magnetic Snap": "magnetic",
    "Magnetic Button": "magnetic",
    "Button": "button",
    "Snap Button": "button",
    "Open": "open",
    "No Closure": "open",
}
SUNGLASS_SHAPES = {
    "Aviator": "aviator",
    "Wayfarer": "wayfarer",
    "Round": "round",
    "Square": "square",
    "Rectangle": "rectangle",
    "Cat-Eye": "cat-eye",
    "Oversized": "oversized",
    "Sports": "sport",
}
POLARISED = {
    "Polarised Lens": "yes",
    "Polarized Lens": "yes",
    "Polarised and UV Protected Lens": "yes",
    "Polarized and UV Protected Lens": "yes",
    "Non-Polarised Lens": "no",
    "Non-Polarized Lens": "no",
    "Regular Lens": "no",
    "Regular Lenses": "no",
    "Normal Lens": "no",
}

Rules = dict[str, list[tuple[str, str, dict[str, str]]]]

RULES: Rules = {
    "tops": [
        ("Sleeve Length", "sleeve_length", SLEEVE_LENGTH),
        ("Sleeve Styling", "sleeve_length", {
            "No Sleeves": "sleeveless",
            "Shoulder Straps": "sleeveless",
            "Cap Sleeves": "short",
        }),
        ("Neck", "neckline", NECKLINE),
        ("Collar", "neckline", NECKLINE),
        ("Print or Pattern Type", "pattern", PATTERN),
        ("Main Trend", "pattern", PATTERN),
        ("Pattern", "pattern", PATTERN),
        ("Fabric", "fabric", TOP_FABRIC),
        ("Fit", "fit", TOP_FIT),
        ("Shape", "fit", {"A-Line": "a-line", "Straight": "straight"}),
        ("Type", "fit", {"A-Line": "a-line"}),
        ("Transparency", "sheerness", {"Opaque": "opaque", "Semi-Sheer": "slightly sheer", "Sheer": "see-through"}),
        ("Length", "length", TOP_LENGTH),
        ("Closure", "closure", {"Button": "buttons", "Buttons": "buttons", "Zip": "zip", "Tie-Ups": "tie"}),
        ("Placket", "closure", {"Button Placket": "buttons", "Button and Loop": "buttons"}),
        ("Pocket Type", "pockets", POCKETS),
        ("Surface Styling", "pockets", {"Chest Pocket": "chest"}),
        ("Number of Pockets", "pockets", {"0": "none"}),
        ("Occasion", "usage", OCCASION | {"Ethnic": "ethnic", "Sports": "sports"}),
    ],
    "bottoms": [
        ("Fit", "fit", BOTTOM_FIT),
        ("Waist Rise", "rise", {"Low-Rise": "low", "Mid-Rise": "mid", "High-Rise": "high"}),
        ("Shade", "wash", {"Light": "light", "Medium": "medium", "Dark": "dark"}),
        ("Distress", "distressed", {
            "Clean Look": "none",
            "No Distress": "none",
            "Low Distress": "light",
            "Mildly Distressed": "light",
            "Light Distress": "light",
            "Heavy Distress": "heavy",
            "Highly Distressed": "heavy",
        }),
        ("Stretch", "stretch", {"Stretchable": "yes", "Non Stretchable": "no", "No Stretch": "no"}),
        ("Fabric 2", "stretch", STRETCH_FIBRES),
        ("Fabric 3", "stretch", STRETCH_FIBRES),
        ("Length", "length", {"Regular": "full", "Full Length": "full", "Ankle Length": "ankle", "Cropped": "cropped"}),
        ("Fabric", "fabric", BOTTOM_FABRIC),
        ("Occasion", "usage", OCCASION),
    ],
    "shoes": [
        ("Type", "type", {
            "Sneakers": "sneakers",
            "Loafers": "loafers",
            "Driving Shoes": "loafers",
            "Slip-On Shoes": "slip-on",
            "Derbys": "derby",
            "Derby": "derby",
            "Oxfords": "oxford",
            "Oxford": "oxford",
            "Boots": "boots",
            "Ankle Boots": "boots",
            "Running Shoes": "running",
            "Training Shoes": "training",
            "Flats": "flats",
            "Flat Shoes": "flats",
            "Ballerinas": "flats",
            "Ballerina": "flats",
            "Ballerina Flats": "flats",
            "Ballet Flats": "flats",
            "Mary Janes": "mary janes",
            "Mary Jane": "mary janes",
            "Mary Jane Shoes": "mary janes",
            "Mary Jane Flats": "mary janes",
        }),
        ("Sport", "type", {"Running": "running", "Training or Gym": "training", "Training": "training"}),
        ("Fastening", "closure", {
            "Lace-Ups": "laces",
            "Laces": "laces",
            "Slip-On": "slip-on",
            "Velcro": "velcro",
            "Buckle": "buckle",
            "Buckles": "buckle",
            "Zip": "zip",
        }),
        ("Upper Material", "upper_material", SHOE_MATERIAL),
        ("Material", "upper_material", SHOE_MATERIAL),
        ("Heel Height", "heel_height", {"Flat": "flat", "Low": "low", "Medium": "medium", "High": "high"}),
        ("Occasion", "usage", OCCASION | {"Sports": "sports"}),
    ],
    "watches": [
        ("Display", "display", {
            "Analogue": "analog",
            "Analog": "analog",
            "Digital": "digital",
            "Analogue-Digital": "analog-digital",
            "Analog-Digital": "analog-digital",
            "Analogue and Digital": "analog-digital",
            "Smart": "smart",
            "Smartwatch": "smart",
        }),
        ("Dial Colour", "dial_colour", DIAL_COLOUR),
        ("Dial Color", "dial_colour", DIAL_COLOUR),
        ("Dial Shape", "case_shape", CASE_SHAPE),
        ("Case Shape", "case_shape", CASE_SHAPE),
        ("Case Material", "case_material", CASE_MATERIAL),
        ("Strap Material", "strap_material", STRAP_MATERIAL),
        ("Strap Colour", "strap_colour", STRAP_COLOUR),
        ("Strap Color", "strap_colour", STRAP_COLOUR),
        # A watch can list several features; "Chronograph" on any of them beats "Multi Function" on another.
        ("Features", "chronograph", CHRONOGRAPH),
        ("Features 2", "chronograph", CHRONOGRAPH),
        ("Features 3", "chronograph", CHRONOGRAPH),
        ("Features", "chronograph", NOT_CHRONOGRAPH),
        ("Features 2", "chronograph", NOT_CHRONOGRAPH),
        ("Features 3", "chronograph", NOT_CHRONOGRAPH),
    ],
    "bags": [
        ("Type", "type", {
            "Shoulder Bag": "shoulder",
            "Sling Bag": "sling",
            "Tote Bag": "tote",
            "Tote": "tote",
            "Satchel": "satchel",
            "Satchel Bag": "satchel",
            "Clutch": "clutch",
            "Backpack": "backpack",
        }),
        # "Regular" is left unmapped: the catalogue uses it for bags of any size (a 58 cm tote, for one).
        ("Size", "size", {"Small": "small", "Medium": "medium", "Large": "large"}),
        ("Material", "material", {
            "Synthetic Leather": "synthetic leather",
            "PU": "synthetic leather",
            "Faux Leather": "synthetic leather",
            "Leather": "leather",
            "Genuine Leather": "leather",
            "Canvas": "canvas",
            "Fabric": "fabric",
            "Nylon": "fabric",
            "Polyester": "fabric",
            "Cotton": "fabric",
            "Jute": "jute",
        }),
        ("Compartment Closure", "closure", BAG_CLOSURE),
        ("Closure", "closure", BAG_CLOSURE),
        ("Number of Main Compartments", "compartments", {
            "1": "one",
            "2": "two",
            "3": "three or more",
            "3 and more": "three or more",
        }),
    ],
    "sunglasses": [
        ("Type", "frame_shape", SUNGLASS_SHAPES | {f"{raw} Sunglasses": ours for raw, ours in SUNGLASS_SHAPES.items()}),
        ("Frame Material", "frame_material", {
            "Metal": "metal",
            "Plastic": "plastic",
            "Acetate": "plastic",
            "Metal and Plastic": "mixed",
            "Mixed": "mixed",
        }),
        ("Lens Colour", "lens_colour", {
            "Black": "black",
            "Grey": "grey",
            "Brown": "brown",
            "Green": "green",
            "Blue": "blue",
            "Mirrored": "mirrored",
            "Mirror": "mirrored",
            "Gradient": "gradient",
        }),
        ("Feature", "polarised", POLARISED),
        ("Features", "polarised", POLARISED),
    ],
}

# Raw keys with nothing to map to in our vocabulary. They stay in raw_attributes but are left
# out of the "most common unmapped" report so it shows what's worth adding.
NO_VOCABULARY_KEYS = {
    "Body or Garment Size", "Wash Care", "Multipack Set", "Number of Components", "Warranty", "Make",
    "Power Source", "Movement", "Water Resistance", "Business Unit", "Segment", "Brick", "Family",
    "Class", "Processing Time", "Weave Type", "Weave Pattern", "Brand Fit Name", "Brand",
}

# Mapped values that mean something else for one article type. The catalogue labels jeans "Fabric: Cotton",
# and catalogue values beat Gemini's, so left as cotton they would fail a "denim" must-have. Trousers keep cotton.
ARTICLE_TYPE_VALUES: dict[str, dict[str, dict[str, str]]] = {
    "Jeans": {"fabric": {"cotton": "denim"}},
}

CASE_DIAMETER = re.compile(r"\b(?:case\s+(?:diameter|width|size)|dial\s+width)\W{0,5}(\d+(?:\.\d+)?)\s*mm", re.I)


def normalise(text: str) -> str:
    return " ".join(str(text).lower().replace("-", " ").replace("_", " ").split())


def _compile(rules: Rules) -> Rules:
    return {
        group: [
            (normalise(key), attribute, {normalise(raw): ours for raw, ours in values.items()})
            for key, attribute, values in group_rules
        ]
        for group, group_rules in rules.items()
    }


LOOKUP = _compile(RULES)


def map_attributes(group: str | None, raw: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (our attributes, raw pairs we couldn't map)."""
    if group not in LOOKUP:
        return {}, dict(raw)
    by_key = {normalise(key): (key, value) for key, value in raw.items()}
    attributes: dict[str, str] = {}
    understood: set[str] = set()
    for key, attribute, values in LOOKUP[group]:
        if key not in by_key:
            continue
        ours = values.get(normalise(by_key[key][1]))
        if ours is None:
            continue
        understood.add(key)
        if attribute not in attributes and is_allowed(group, attribute, ours):
            attributes[attribute] = ours
    unmapped = {key: value for key, value in raw.items() if normalise(key) not in understood}
    return attributes, unmapped


def case_size(text: str | None) -> str | None:
    match = CASE_DIAMETER.search(text or "")
    if not match:
        return None
    mm = float(match.group(1))
    if mm < 36:
        return "small"
    return "medium" if mm <= 42 else "large"


# ---------- Reading one styles JSON ----------


class _Text(HTMLParser):
    BREAKS = {"br", "p", "div", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


# Some descriptions were double-encoded before they reached the dataset ("5'8â€Ã?Â??", "ï»¿"),
# and some entities were escaped twice ("&amp;dagger"). These pieces never carry meaning.
MOJIBAKE = re.compile(r"[ÃÂâ€ï†�¿»\ufffd]|&\s*(?:euro|dagger|acirc|iuml|raquo|iquest)\b;?|\?{2,}")


def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    parser = _Text()
    parser.feed(str(html))
    parser.close()
    text = MOJIBAKE.sub("", "".join(parser.parts).replace("\xa0", " "))
    lines = (re.sub(r"\s+([),.;:])", r"\1", " ".join(line.split())) for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    return text if text and text.upper() != "NA" else None


def description_text(descriptors: object) -> str | None:
    if not isinstance(descriptors, dict):
        return None
    parts = []
    for name in DESCRIPTION_PARTS:
        part = descriptors.get(name)
        text = html_to_text(part.get("value") if isinstance(part, dict) else None)
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts) or None


def price(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if not match:
            return None
        value = float(match.group().replace(",", ""))
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return round(value)


def text_or_none(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split())
    return text if text and text.upper() not in ("NA", "N/A") else None


def clean_raw_attributes(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for key, raw in value.items():
        text = text_or_none(raw)
        if text:
            cleaned[" ".join(str(key).split())] = text
    return cleaned


def article_type_of(data: dict) -> str | None:
    article_type = data.get("articleType")
    if isinstance(article_type, dict):
        return text_or_none(article_type.get("typeName"))
    return text_or_none(article_type)


@dataclass
class Parsed:
    row: dict
    group: str | None
    unmapped: dict[str, str]


def parse_style(product_id: str, style: dict, article_type: str | None = None) -> Parsed:
    data = style.get("data") if isinstance(style, dict) else None
    data = data if isinstance(data, dict) else {}
    article_type = article_type or article_type_of(data) or ""
    group = group_for(article_type)
    raw = clean_raw_attributes(data.get("articleAttributes"))
    attributes, unmapped = map_attributes(group, raw)
    for attribute, swaps in ARTICLE_TYPE_VALUES.get(article_type, {}).items():
        if attributes.get(attribute) in swaps:
            attributes[attribute] = swaps[attributes[attribute]]
    description = description_text(data.get("productDescriptors"))
    if group == "watches" and "case_size" not in attributes and (size := case_size(description)):
        attributes["case_size"] = size
    row = {
        "id": product_id,
        "brand": text_or_none(data.get("brandName")),
        "description": description,
        "mrp": price(data.get("price")),
        "selling_price": price(data.get("discountedPrice")),
        "attributes": attributes,
        "raw_attributes": raw,
    }
    return Parsed(row, group, unmapped)


# ---------- Running over all products ----------


@dataclass
class Result:
    total: int
    rows: list[dict] = field(default_factory=list)
    groups: dict[str, str | None] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    unmapped: Counter = field(default_factory=Counter)
    rate_limited: str = ""


def read_selected(selected: Path) -> list[dict[str, str]]:
    with selected.open(newline="", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if (row.get("id") or "").strip()]
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        unique.setdefault(row["id"].strip(), row)
    return [{**row, "id": product_id} for product_id, row in unique.items()]


def local_styles(root: Path) -> Callable[[str], bytes]:
    return lambda product_id: (root / f"{product_id}.json").read_bytes()


def load_json(data: bytes) -> dict:
    return json.loads(data.decode("utf-8-sig", errors="replace"))


def run(
    products: Iterable[dict[str, str]],
    fetch: Callable[[str], bytes],
    workers: int = 8,
    attempts: int = 4,
    delay: float = 2.0,
) -> Result:
    products = list(products)
    result = Result(total=len(products))

    def fetch_one(product_id: str) -> dict | Exception:
        try:
            return with_retries(lambda: load_json(fetch(product_id)), attempts, delay)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=workers) as pool:
        styles = pool.map(fetch_one, [p["id"] for p in products])
        for product, style in zip(products, styles):
            if isinstance(style, KaggleRateLimited):
                if not result.rate_limited:
                    print(f"  stopping: {style}")
                result.failed.append(product["id"])
                result.rate_limited = str(style)
                continue
            if isinstance(style, Exception):
                result.failed.append(product["id"])
                print(f"  failed {product['id']}: {type(style).__name__}: {style}")
                continue
            parsed = parse_style(product["id"], style, product.get("articleType"))
            result.rows.append(parsed.row)
            result.groups[product["id"]] = parsed.group
            result.unmapped.update(
                (parsed.group, k, v) for k, v in parsed.unmapped.items() if k not in NO_VOCABULARY_KEYS
            )
    return result


def write_jsonl(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(out)


def coverage_report(result: Result, top: int = 30) -> str:
    rows, total = result.rows, result.total
    lines = [f"Real details for {len(rows):,} of {total:,} products", "", "Fields:"]
    for name in ("brand", "description", "mrp", "selling_price"):
        lines.append(f"  {name}: {sum(1 for r in rows if r[name] is not None):,} of {total:,}")
    discounted = sum(1 for r in rows if r["mrp"] and r["selling_price"] and r["selling_price"] < r["mrp"])
    lines.append(f"  discounted (selling_price below mrp): {discounted:,} of {total:,}")

    lines += ["", "Mapped details, by group:"]
    for group, attributes in VOCABULARY.items():
        in_group = [r for r in rows if result.groups.get(r["id"]) == group]
        if not in_group:
            continue
        lines.append(f"  {group} ({len(in_group):,} products)")
        for attribute in attributes:
            count = sum(1 for r in in_group if attribute in r["attributes"])
            if count:
                lines.append(f"    {attribute}: {count:,} of {len(in_group):,}")

    lines += ["", f"Most common raw details we couldn't map (top {top}):"]
    for (group, key, value), count in result.unmapped.most_common(top):
        lines.append(f"  {group}: {key} = {value}: {count:,}")
    return "\n".join(lines)


# ---------- Command line ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selected", type=Path, default=Path("data/selected.csv"))
    parser.add_argument("--out", type=Path, default=Path("data/real_details.jsonl"))
    parser.add_argument("--failed", type=Path, help="failed ids (default: next to --out, real_details_failed.txt)")
    parser.add_argument("--styles-dir", type=Path, help="read {id}.json from this folder instead of Kaggle")
    parser.add_argument("--dataset", default=DATASET, help="Kaggle dataset handle")
    parser.add_argument(
        "--styles-path", default=STYLES_PATH, help="styles JSON path inside the dataset; {id} is replaced"
    )
    add_source_args(parser)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=4, help="tries per product before giving up")
    args = parser.parse_args(argv)
    if args.failed is None:
        args.failed = args.out.with_name("real_details_failed.txt")
    return args


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    products = read_selected(args.selected)
    if args.styles_dir:
        fetch, attempts = local_styles(args.styles_dir), 1
    else:
        try:
            fetch = open_source(args.source, args.dataset, args.styles_path, args.kaggle_cache, args.zip_index)
        except KaggleRateLimited as error:
            raise SystemExit(str(error))
        attempts = args.attempts

    result = run(products, fetch, workers=args.workers, attempts=attempts)
    write_jsonl(result.rows, args.out)
    args.failed.parent.mkdir(parents=True, exist_ok=True)
    args.failed.write_text("".join(f"{i}\n" for i in result.failed), encoding="utf-8")

    print(coverage_report(result))
    print(f"\nWrote {len(result.rows):,} rows to {args.out}")
    if result.failed:
        print(f"{len(result.failed):,} failed, ids in {args.failed} (re-run to retry them)")
    if result.rate_limited:
        print(result.rate_limited)


if __name__ == "__main__":
    main()
