"""Small builders for the ranking, trade-off and learning tests."""

from datetime import date

from agent.models import Attribute, Check, Evidence, MustHave, NiceToHave, Product, ShoppingBrief, Verification
from agent.rank import Candidate
from data_prep import schemas

TUE, WED, THU, FRI, SAT, SUN = (date(2026, 10, day) for day in range(13, 19))

STATUS_LETTERS = {"p": "pass", "f": "fail", "u": "unclear"}

KURTA_MUST_HAVES = [
    MustHave(id="m1", attribute="fabric", rule="is", value="cotton"),
    MustHave(id="m2", attribute="pockets", rule="present", value=True),
    MustHave(id="m3", attribute="sleeve_length", rule="is", value="three-quarter"),
    MustHave(id="m4", attribute="sheerness", rule="is_not", value="see-through"),
]


def make_brief(
    must_haves: list[MustHave] = KURTA_MUST_HAVES,
    *,
    budget: int | None = None,
    deadline: date | None = None,
    nice_to_haves: list[NiceToHave] = (),
    category: str = "Kurtas",
) -> ShoppingBrief:
    return ShoppingBrief(
        category=category,
        must_haves=list(must_haves),
        nice_to_haves=list(nice_to_haves),
        budget_max_inr=budget,
        deadline_date=deadline,
    )


def make_product(
    product_id: str,
    *,
    title: str | None = None,
    brand: str = "Libas",
    colour: str = "Blue",
    article_type: str = "Kurtas",
    attributes: dict[str, str] | None = None,
    rating: float | None = None,
    reviews: int = 0,
    embedding: list[float] | None = None,
) -> Product:
    return Product(
        id=product_id,
        title=title or f"Kurta {product_id}",
        brand=brand,
        gender="women",
        masterCategory="Apparel",
        subCategory="Topwear",
        articleType=article_type,
        baseColour=colour,
        group=schemas.group_for(article_type),
        attributes={name: Attribute(value=value, confidence=0.9) for name, value in (attributes or {}).items()},
        rating_avg=rating,
        rating_count=reviews,
        embedding=embedding,
    )


def make_candidate(
    product_id: str, statuses: str, *, price: int = 1000, arrives: date | None = None, **product_fields
) -> Candidate:
    """`statuses` has one letter per must-have id m1, m2, ...: p pass, f fail, u unclear, - no check."""
    checks = [
        Check(
            must_have_id=f"m{position}",
            status=STATUS_LETTERS[letter],
            confidence=0.9,
            evidence=Evidence(type="image", note=f"m{position} {STATUS_LETTERS[letter]}"),
        )
        for position, letter in enumerate(statuses, start=1)
        if letter != "-"
    ]
    return Candidate(
        product=make_product(product_id, **product_fields),
        verification=Verification(product_id=product_id, checks=checks),
        total_price=price,
        delivery_date=arrives,
    )
