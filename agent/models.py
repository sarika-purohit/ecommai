"""Shared data formats used by the agent, the API and the tests.

These mirror the developer appendix in PLAN.md (checklist format, check result, Firestore layout).
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from data_prep.schemas import GROUP_NAMES

Rule = Literal["is", "is_not", "present", "absent", "at_most", "at_least"]
Status = Literal["pass", "fail", "unclear"]
EvidenceType = Literal["image", "details", "reviews", "attribute"]
Gender = Literal["men", "women", "boys", "girls", "unisex"]


# ---------- The shopper's checklist ----------


class MustHave(BaseModel):
    id: str
    attribute: str = Field(description="A detail name from the vocabulary, e.g. 'pockets' or 'dial_colour'")
    rule: Rule
    value: str | bool | float
    from_rule: bool = Field(default=False, description="True when it comes from the shopper's saved personal rules")

    @model_validator(mode="after")
    def _value_matches_rule(self) -> "MustHave":
        if self.rule in ("present", "absent") and not isinstance(self.value, bool):
            raise ValueError(f"rule '{self.rule}' needs a true/false value")
        if self.rule in ("at_most", "at_least") and (isinstance(self.value, bool) or not isinstance(self.value, float)):
            raise ValueError(f"rule '{self.rule}' needs a number")
        if self.rule in ("is", "is_not") and not isinstance(self.value, str):
            raise ValueError(f"rule '{self.rule}' needs a text value")
        return self

    def key(self) -> str:
        """Stable across searches, so saved check results can be reused."""
        value = str(self.value).lower().replace(" ", "-")
        return f"{self.attribute}__{self.rule}__{value}"


class NiceToHave(BaseModel):
    attribute: str
    value: str | bool | float


class ShoppingBrief(BaseModel):
    category: str = Field(description="The dataset's product type (articleType), e.g. 'Kurtas' or 'Watches'")
    gender: Gender | None = None
    must_haves: list[MustHave] = Field(default_factory=list)
    nice_to_haves: list[NiceToHave] = Field(default_factory=list)
    rules_applied: list[str] = Field(default_factory=list)
    budget_max_inr: int | None = Field(default=None, gt=0, description="Includes delivery")
    deadline_date: date | None = None
    pincode: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    size: str | None = None
    reference_image_url: str | None = None
    language: str = "en"
    missing: list[str] = Field(default_factory=list)
    not_checkable: list[str] = Field(
        default_factory=list, description="Wishes we can't check yet, such as a brand; shown to the shopper"
    )
    question: str | None = Field(default=None, description="At most one question, only when the answer changes the results")

    @field_validator("must_haves")
    @classmethod
    def _unique_ids(cls, must_haves: list[MustHave]) -> list[MustHave]:
        ids = [m.id for m in must_haves]
        if len(ids) != len(set(ids)):
            raise ValueError("must-have ids must be unique")
        return must_haves


# ---------- Checking a product ----------


class Evidence(BaseModel):
    type: EvidenceType
    note: str
    quote: str | None = None
    box_2d: list[int] | None = Field(default=None, description="[ymin, xmin, ymax, xmax] on a 0-1000 scale")
    agree: int | None = Field(default=None, ge=0)
    disagree: int | None = Field(default=None, ge=0)

    @field_validator("box_2d")
    @classmethod
    def _valid_box(cls, box: list[int] | None) -> list[int] | None:
        if box is None:
            return None
        if len(box) != 4 or any(not 0 <= v <= 1000 for v in box):
            raise ValueError("box_2d must be 4 numbers between 0 and 1000")
        ymin, xmin, ymax, xmax = box
        if ymin >= ymax or xmin >= xmax:
            raise ValueError("box_2d must be [ymin, xmin, ymax, xmax] with min < max")
        return box


class Check(BaseModel):
    must_have_id: str
    status: Status
    confidence: float = Field(ge=0, le=1)
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def _no_proof_no_pass(self) -> "Check":
        # The product rule "no proof, no pass" is enforced here so it holds everywhere.
        if self.status == "pass" and (self.evidence is None or not self.evidence.note.strip()):
            self.status = "unclear"
        return self


class Verification(BaseModel):
    product_id: str
    checks: list[Check]
    reason: str = ""

    @computed_field
    @property
    def all_pass(self) -> bool:
        return bool(self.checks) and all(c.status == "pass" for c in self.checks)

    @computed_field
    @property
    def overall(self) -> Status:
        if any(c.status == "fail" for c in self.checks):
            return "fail"
        return "pass" if self.all_pass else "unclear"


# ---------- Products (Firestore `products` collection) ----------


class Attribute(BaseModel):
    value: str | bool | float
    confidence: float = Field(ge=0, le=1)
    note: str = ""
    source: Literal["gemini", "catalogue"] = "gemini"
    gemini_value: str | bool | float | None = Field(
        default=None, description="What Gemini saw in the photo, when the catalogue value was used instead"
    )


class Price(BaseModel):
    mrp: int = Field(gt=0)
    selling: int = Field(gt=0)
    demo: bool = True


class Warehouse(BaseModel):
    city: str
    pincode: str


class Cod(BaseModel):
    available: bool
    fee: int = Field(default=0, ge=0)


class Returns(BaseModel):
    returnable: bool
    window_days: int = Field(default=0, ge=0)
    exchange: bool = False


class Review(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str
    demo: bool = True
    flagged: bool = Field(default=False, description="Model Armor marked it as suspicious")


class Images(BaseModel):
    original: str
    gemini: str
    thumb: str


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    brand: str | None = None
    gender: Gender
    master_category: str = Field(alias="masterCategory")
    sub_category: str = Field(alias="subCategory")
    article_type: str = Field(alias="articleType")
    base_colour: str | None = Field(default=None, alias="baseColour")
    usage: str | None = None
    group: str
    description: str = ""
    attributes: dict[str, Attribute] = Field(default_factory=dict)
    catalogue_details: dict[str, str] = Field(default_factory=dict)
    needs_review: list[str] = Field(default_factory=list)
    price: Price | None = None
    sizes: dict[str, int] = Field(default_factory=dict)
    warehouse: Warehouse | None = None
    delivery_fee: int = Field(default=0, ge=0)
    cod: Cod | None = None
    returns: Returns | None = None
    reviews: list[Review] = Field(default_factory=list)
    rating_avg: float | None = None
    rating_count: int = 0
    images: Images | None = None
    embedding: list[float] | None = Field(default=None, repr=False)
    demo_data: bool = True

    @field_validator("gender", mode="before")
    @classmethod
    def _lowercase_gender(cls, gender: str) -> str:
        return gender.lower() if isinstance(gender, str) else gender

    @field_validator("group")
    @classmethod
    def _known_group(cls, group: str) -> str:
        if group not in GROUP_NAMES:
            raise ValueError(f"unknown group '{group}'")
        return group

    def to_firestore(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)
