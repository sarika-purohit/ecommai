"""Step 6, Learn: a style profile per product type, from the products the shopper liked, saved or bought.

Pure logic only. Saving the profile in Firestore (`users/{id}.taste`) is up to the caller.
"""

import math
from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["like", "save", "buy"]

ACTION_WEIGHTS: dict[str, float] = {"like": 1.0, "save": 1.0, "buy": 2.0}


class TasteVector(BaseModel):
    vector: list[float] = Field(min_length=1)
    count: float = Field(gt=0, description="Total weight averaged so far; a purchase counts double")


TasteProfile = dict[str, TasteVector]


def update_taste(profile: TasteProfile, article_type: str, embedding: list[float], action: Action) -> TasteProfile:
    """Return a new profile with this product's embedding folded into the running average for its type."""
    weight = ACTION_WEIGHTS.get(action)
    if weight is None:
        raise ValueError(f"unknown action '{action}'")
    current = profile.get(article_type)
    if current is None:
        updated = TasteVector(vector=list(embedding), count=weight)
    else:
        if len(embedding) != len(current.vector):
            raise ValueError(f"embedding has {len(embedding)} numbers, the profile has {len(current.vector)}")
        total = current.count + weight
        vector = [(old * current.count + new * weight) / total for old, new in zip(current.vector, embedding)]
        updated = TasteVector(vector=vector, count=total)
    return {**profile, article_type: updated}


def clear_taste() -> TasteProfile:
    return {}


def vectors(profile: TasteProfile) -> dict[str, list[float]]:
    """The taste vectors per product type, in the shape `rank()` takes."""
    return {article_type: taste.vector for article_type, taste in profile.items()}


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vectors have different lengths ({len(a)} and {len(b)})")
    norms = math.hypot(*a) * math.hypot(*b)
    return math.sumprod(a, b) / norms if norms else 0.0
