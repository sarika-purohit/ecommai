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
        "neckline": ["round", "v-neck", "collar", "mandarin", "boat", "square", "keyhole", "henley", "polo"],
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
        "fabric": ["denim", "cotton", "linen", "polyester", "blend"],
        "usage": ["casual", "office", "party"],
    },
    "shoes": {
        "type": ["sneakers", "running", "training", "loafers", "slip-on", "boots", "derby", "oxford"],
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
        "strap_colour": ["black", "brown", "silver", "gold", "rose gold", "blue", "other"],
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
    ("tops", "sheerness"): "only judge from reviews or the real details; a photo alone rarely shows this",
}

# Shop details that apply to every group. They're checked from shop data, not from photos.
SHOP_ATTRIBUTES: dict[str, list[str]] = {
    "cod": ["yes", "no"],
    "returnable": ["yes", "no"],
    "exchange": ["yes", "no"],
}


def group_for(article_type: str) -> str | None:
    return ARTICLE_TYPE_TO_GROUP.get(article_type)


def attributes_for(group: str) -> dict[str, list[str]]:
    return {**VOCABULARY[group], **SHOP_ATTRIBUTES}


def is_allowed(group: str, attribute: str, value: str) -> bool:
    allowed = attributes_for(group).get(attribute)
    if allowed is None:
        return False
    return value == UNKNOWN or value in allowed
