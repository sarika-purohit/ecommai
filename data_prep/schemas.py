"""The detail vocabulary: which product types we carry, and the allowed values for every detail.

This mirrors the detail vocabulary in PLAN.md (developer appendix H). Change both together.
"""

UNKNOWN = "unknown"

GROUPS: dict[str, list[str]] = {
    "tops": ["Kurtas", "Kurtis", "Tunics", "Shirts", "Tshirts", "Tops"],
    "bottoms": ["Jeans", "Trousers"],
    "shoes": ["Casual Shoes", "Sports Shoes", "Formal Shoes"],
    "watches": ["Watches"],
    "bags": ["Handbags", "Backpacks", "Clutches"],
    "sunglasses": ["Sunglasses"],
}

GROUP_NAMES: tuple[str, ...] = tuple(GROUPS)

ARTICLE_TYPE_TO_GROUP: dict[str, str] = {
    article_type: group for group, article_types in GROUPS.items() for article_type in article_types
}

VOCABULARY: dict[str, dict[str, list[str]]] = {
    "tops": {
        "sleeve_length": ["sleeveless", "short", "elbow", "three-quarter", "full"],
        "neckline": ["round", "v-neck", "collar", "mandarin", "boat", "square", "keyhole", "henley", "polo", "cowl"],
        "pockets": ["none", "side", "patch", "chest", "hidden"],
        "pattern": ["solid", "stripes", "checks", "floral", "geometric", "printed", "embroidered", "abstract"],
        "print_intensity": ["none", "subtle", "medium", "loud"],
        "fabric": ["cotton", "linen", "rayon", "polyester", "silk", "blend", "denim"],
        "sheerness": ["opaque", "slightly sheer", "see-through"],
        "fit": ["slim", "regular", "relaxed", "oversized", "a-line", "straight"],
        "length": ["crop", "hip", "thigh", "knee", "calf", "ankle"],
        "closure": ["none", "buttons", "zip", "tie"],
        "usage": ["casual", "office", "ethnic", "party", "sports"],
    },
    "bottoms": {
        "fit": ["skinny", "slim", "straight", "regular", "relaxed", "bootcut", "wide-leg", "tapered"],
        "rise": ["low", "mid", "high"],
        "wash": ["light", "medium", "dark", "black", "grey", "none"],
        "distressed": ["none", "light", "heavy"],
        "stretch": ["yes", "no"],
        "length": ["full", "ankle", "cropped"],
        "pockets": ["none", "front", "back", "front and back", "cargo"],
        "fabric": ["denim", "cotton", "linen", "polyester", "blend"],
        "usage": ["casual", "office", "party"],
    },
    "shoes": {
        "type": [
            "sneakers", "running", "training", "loafers", "slip-on", "boots", "derby", "oxford", "flats", "mary janes",
        ],
        "closure": ["laces", "velcro", "slip-on", "buckle", "zip"],
        "sole_type": ["flat", "cushioned", "chunky", "platform"],
        "sole_colour": ["white", "off-white", "black", "brown", "gum", "grey", "other"],
        "upper_material": ["canvas", "leather", "synthetic leather", "mesh", "knit", "suede"],
        "logo_visible": ["none", "subtle", "prominent"],
        "heel_height": ["flat", "low", "medium", "high"],
        "usage": ["casual", "sports", "office", "party"],
    },
    "watches": {
        "display": ["analog", "digital", "analog-digital", "smart"],
        "dial_colour": ["black", "white", "silver", "blue", "green", "grey", "brown", "gold", "rose gold", "other"],
        "dial_markings": ["numbers", "lines", "roman numerals", "mixed", "none"],
        "case_shape": ["round", "square", "rectangular", "tonneau"],
        "case_material": ["steel", "gold-tone", "rose-gold-tone", "black metal", "plastic"],
        "case_size": ["small", "medium", "large"],
        "strap_material": ["steel", "leather", "silicone", "fabric", "mesh", "ceramic"],
        "strap_colour": ["black", "brown", "silver", "gold", "rose gold", "blue", "white", "other"],
        "chronograph": ["yes", "no"],
    },
    "bags": {
        "type": ["tote", "shoulder", "sling", "satchel", "clutch", "backpack"],
        "size": ["small", "medium", "large"],
        "closure": ["zip", "magnetic", "flap", "button", "drawstring", "open"],
        "material": ["synthetic leather", "leather", "canvas", "fabric", "jute"],
        "compartments": ["one", "two", "three or more"],
        "strap": ["handles only", "shoulder strap", "cross-body strap", "handles and strap", "none"],
    },
    "sunglasses": {
        "frame_shape": ["aviator", "wayfarer", "round", "square", "rectangle", "cat-eye", "oversized", "sport"],
        "frame_material": ["metal", "plastic", "mixed"],
        "lens_colour": ["black", "grey", "brown", "green", "blue", "mirrored", "gradient"],
        "polarised": ["yes", "no"],
    },
}

# Extra guidance shown to Gemini wherever a value needs a definition.
VALUE_HINTS: dict[tuple[str, str], str] = {
    ("watches", "case_size"): "small is under 36 mm, medium is 36-42 mm, large is over 42 mm",
    ("tops", "print_intensity"): "how loud the print looks from a distance; none for plain fabric",
    ("tops", "sheerness"): "never judge from the photo; only from reviews or the real details",
    ("tops", "pockets"): "chest means any pocket on the chest; side means pockets in the side seams",
}

# Shop details that apply to every group. They're checked from shop data, not from photos.
SHOP_ATTRIBUTES: dict[str, list[str]] = {
    "cod": ["yes", "no"],
    "returnable": ["yes", "no"],
    "exchange": ["yes", "no"],
}

# The product's own colour (the dataset's baseColour), grouped into the names shoppers use.
COLOUR_FAMILIES: dict[str, list[str]] = {
    "black": ["Black", "Charcoal"],
    "white": ["White", "Off White", "Cream"],
    "grey": ["Grey", "Grey Melange"],
    "blue": ["Blue", "Navy Blue", "Turquoise Blue", "Teal"],
    "green": ["Green", "Olive", "Sea Green"],
    "red": ["Red", "Maroon", "Rust"],
    "pink": ["Pink", "Magenta", "Rose", "Peach"],
    "purple": ["Purple", "Lavender"],
    "yellow": ["Yellow", "Mustard"],
    "orange": ["Orange"],
    "brown": ["Brown", "Coffee Brown", "Tan", "Bronze", "Copper"],
    "beige": ["Beige", "Khaki", "Nude", "Taupe"],
    "gold": ["Gold"],
    "silver": ["Silver", "Steel", "Metallic"],
    "multi": ["Multi"],
}

_BASE_COLOUR_TO_FAMILY = {c.casefold(): family for family, colours in COLOUR_FAMILIES.items() for c in colours}

# Shades shoppers name, each in its family. A colour must-have holds a family ("blue") or a shade ("navy blue").
# A shade that is also a catalogue colour sits in the same family as in COLOUR_FAMILIES.
COLOUR_SHADES: dict[str, str] = {
    "navy blue": "blue",
    "dark blue": "blue",
    "royal blue": "blue",
    "cobalt blue": "blue",
    "sky blue": "blue",
    "light blue": "blue",
    "baby blue": "blue",
    "teal": "blue",
    "turquoise": "blue",
    "mint green": "green",
    "sea green": "green",
    "pista green": "green",
    "light green": "green",
    "lime green": "green",
    "olive": "green",
    "dark green": "green",
    "emerald green": "green",
    "charcoal": "black",
    "off-white": "white",
    "cream": "white",
    "ivory": "white",
    "light grey": "grey",
    "dark grey": "grey",
    "maroon": "red",
    "wine": "red",
    "burgundy": "red",
    "rust": "red",
    "peach": "pink",
    "baby pink": "pink",
    "rani pink": "pink",
    "magenta": "pink",
    "lavender": "purple",
    "lilac": "purple",
    "mustard": "yellow",
    "saffron": "orange",
    "tan": "brown",
    "khaki": "beige",
    "nude": "beige",
}

# Other words shoppers use for a listed shade, in English, Hinglish and Hindi.
SHADE_WORDS: dict[str, str] = {
    "navy": "navy blue",
    "gehra neela": "dark blue",
    "cobalt": "cobalt blue",
    "aasmani": "sky blue",
    "halka neela": "light blue",
    "powder blue": "baby blue",
    "turquoise blue": "turquoise",
    "firozi": "turquoise",
    "mint": "mint green",
    "pista": "pista green",
    "halka hara": "light green",
    "olive green": "olive",
    "bottle green": "dark green",
    "gehra hara": "dark green",
    "off white": "off-white",
    "mehroon": "maroon",
    "wine red": "wine",
    "rani": "rani pink",
    "kesari": "saffron",
}

# What a shade looks like, where it's easily mixed up with a neighbour. Shown to Gemini with the must-have.
SHADE_HINTS: dict[str, str] = {
    "navy blue": "a very dark blue, close to black; sky blue, light blue, royal blue and teal are not navy",
    "dark blue": "a deep blue such as navy; sky blue and light blue are not dark blue",
    "royal blue": "a bright, strong blue; navy and sky blue are not royal blue",
    "sky blue": "a light, clear blue; navy, royal blue, mint and sea green are not sky blue",
    "light blue": "a pale blue such as sky blue or baby blue; navy, royal blue, mint and sea green are not light blue",
    "baby blue": "a very pale, soft blue",
    "teal": "a dark blue-green",
    "turquoise": "a bright greenish blue",
    "mint green": "a pale, cool green; light blue is not mint",
    "sea green": "a medium blue-green",
    "olive": "a dull, yellowish green",
    "charcoal": "a very dark grey, not pure black",
    "off-white": "white with a slight warm tint",
    "cream": "a pale yellowish white",
    "maroon": "a dark brownish red",
    "wine": "a dark purplish red",
}

COLOUR_VALUES: list[str] = [*COLOUR_FAMILIES, *COLOUR_SHADES]

# Read from the product's catalogue fields, not from photos. Watches use dial_colour and strap_colour instead.
CATALOGUE_ATTRIBUTES: dict[str, list[str]] = {"colour": COLOUR_VALUES}
GROUPS_WITHOUT_COLOUR = frozenset({"watches"})

# Details only about how a product's colour looks. A pass needs the photo: titles and descriptions can be wrong.
COLOUR_DETAILS = frozenset({"colour", "dial_colour", "strap_colour", "sole_colour", "lens_colour", "wash"})

# Details that apply to every group but only reviews can show. Never noted from photos.
REVIEW_ATTRIBUTES: dict[str, list[str]] = {
    "size_fit": ["true to size", "runs small", "runs large"],
    "durability": ["good", "poor"],
    "colour_fastness": ["good", "fades"],
    "colour_match": ["as shown", "different"],
}


def group_for(article_type: str) -> str | None:
    return ARTICLE_TYPE_TO_GROUP.get(article_type)


def colour_family(base_colour: str | None) -> str | None:
    return _BASE_COLOUR_TO_FAMILY.get((base_colour or "").strip().casefold())


def family_of(colour: str) -> str | None:
    """The family of a colour value: itself for a family, its family for a shade, None for anything else."""
    text = colour.strip().casefold()
    return text if text in COLOUR_FAMILIES else COLOUR_SHADES.get(text)


def is_shade(colour: str) -> bool:
    return colour.strip().casefold() in COLOUR_SHADES


def colour_within(colour: str, wanted: str) -> bool | None:
    """Whether a product of `colour` has the `wanted` colour (a family or a shade); None when it can't say.

    Different families are certain. A family covers its shades. A shade can't be told apart from its family or a
    sibling shade by name: only the photo can.
    """
    colour, wanted = colour.strip().casefold(), wanted.strip().casefold()
    if colour == wanted:
        return True
    colour_group, wanted_group = family_of(colour), family_of(wanted)
    if colour_group is None or wanted_group is None:
        return False
    if colour_group != wanted_group:
        return False
    return True if wanted in COLOUR_FAMILIES else None


def attributes_for(group: str) -> dict[str, list[str]]:
    catalogue = {} if group in GROUPS_WITHOUT_COLOUR else CATALOGUE_ATTRIBUTES
    return {**VOCABULARY[group], **catalogue, **SHOP_ATTRIBUTES, **REVIEW_ATTRIBUTES}


def is_allowed(group: str, attribute: str, value: str) -> bool:
    allowed = attributes_for(group).get(attribute)
    if allowed is None:
        return False
    return value == UNKNOWN or value in allowed
