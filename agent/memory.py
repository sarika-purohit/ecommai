"""Memory mode: fill what a new search leaves open from the shopper's past chats about the SAME product.

Pure functions, no I/O. The caller reads the past product entries (agent/chats.py ChatProduct) and passes them in.

A past entry counts `0.5 ** (age_days / HALF_LIFE_DAYS)`, its age taken from its `timestamps.updated_at`, times
ORDER_DECAY for each newer entry (so chats from the same day still count the newest most). For each key of its
`filters` the values are voted by that weight (text compared ignoring case, lists as a set): the highest total wins and
an exact tie gives nothing for that key. A colour is different (agent/mix.py): when the past chats chose two or three
colours, each keeps its share and they're added as weighted nice-to-haves, so the results show mostly the newest
colour and fewer of the older ones. A key listed in the entry's `remembered` is skipped, because it came
from memory and not from the shopper; otherwise memory would feed on itself for ever. Dates (`deadline_date`), where
to look (`source`) and the soft wishes (`prefer`) are never remembered.

Only what the brief leaves open is filled, and only when the value is valid for the product (skipped silently
otherwise). `ShoppingBrief.remembered` names what came from memory, in the keys of `filters`.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from agent import brief as briefs
from agent.chats import ChatProduct, chat_filters
from agent.mix import MIXED
from agent.models import Gender, MustHave, NiceToHave, Rule, ShoppingBrief
from agent.workflow import ChangeProblem, _set_size, _standard
from data_prep import schemas

HALF_LIFE_DAYS = 30
ORDER_DECAY = 0.6
MIX_MIN_SHARE = 0.1
MIX_MAX_COLOURS = 3
NEVER_REMEMBERED = {"deadline_date", "source", "prefer"}
TOP_LEVEL = {"gender", "size", "budget_max_inr", "min_rating"}


def weight(entry: ChatProduct, now: datetime) -> float:
    """1 for an entry updated just now, a half after HALF_LIFE_DAYS, and so on."""
    age_days = max((now - entry.timestamps.updated_at).total_seconds(), 0.0) / 86400
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _canon(value: Any) -> Any:
    """A value in a form that compares and hashes the way the vote needs."""
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, (int, float)):
        return ("n", float(value))
    if isinstance(value, str):
        return ("s", value.casefold())
    if isinstance(value, dict):
        return ("d", tuple(sorted((key, _canon(item)) for key, item in value.items())))
    if isinstance(value, list):
        return ("l", frozenset(_canon(item) for item in value))
    return ("o", repr(value))


def _winner(votes: dict[Any, list]) -> list:
    """[value] for the heaviest value, or [] when there is none or the top two tie exactly."""
    ranked = sorted(votes.values(), key=lambda vote: vote[0], reverse=True)
    if len(ranked) > 1 and math.isclose(ranked[0][0], ranked[1][0], rel_tol=1e-9):
        return []
    return [ranked[0][1]]


@dataclass(frozen=True)
class Mix:
    """Past colours with their shares, largest first, adding up to 1."""
    shares: tuple[tuple[str, float], ...]


def _mix(votes: dict[Any, list]) -> Mix | None:
    """The plain colours' shares when two or more each have at least MIX_MIN_SHARE, else None."""
    plain = sorted(((w, value) for w, value in votes.values() if isinstance(value, str)), key=lambda v: v[0], reverse=True)
    total = sum(w for w, _ in plain)
    kept = [(value, w) for w, value in plain if total and w / total >= MIX_MIN_SHARE][:MIX_MAX_COLOURS]
    if len(kept) < 2:
        return None
    total = sum(w for _, w in kept)
    return Mix(tuple((value, w / total) for value, w in kept))


def suggest(entries: Sequence[ChatProduct], now: datetime) -> dict[str, Any]:
    """The value to suggest for each key, in `filters` form (a Mix for colours), from past entries about one product."""
    newest_first = sorted({e.timestamps.updated_at for e in entries}, reverse=True)
    place = {moment: n for n, moment in enumerate(newest_first)}
    votes: dict[str, dict[Any, list]] = {}
    for entry in sorted(entries, key=lambda e: e.timestamps.updated_at, reverse=True):
        w = weight(entry, now) * ORDER_DECAY ** place[entry.timestamps.updated_at]
        for key, value in entry.filters.items():
            if key in NEVER_REMEMBERED or key in entry.remembered:
                continue
            slot = votes.setdefault(key, {}).setdefault(_canon(value), [0.0, value])
            slot[0] += w
    found: dict[str, Any] = {}
    for key, cands in votes.items():
        if key in MIXED and (blend := _mix(cands)):
            found[key] = blend
        elif won := _winner(cands):
            found[key] = won[0]
    return found


def _wish(form: Any) -> tuple[Rule, str | bool | float]:
    """A `filters` form turned back into (rule, value): the reverse of chats._form."""
    if isinstance(form, bool):
        return ("present" if form else "absent"), True
    if isinstance(form, str):
        return "is", form
    if isinstance(form, dict) and len(form) == 1:
        (name, value), = form.items()
        if name == "not" and isinstance(value, str):
            return "is_not", value
        if name in ("at_most", "at_least") and isinstance(value, (int, float)) and not isinstance(value, bool):
            return name, float(value)
    raise ValueError("not a filter form")


def _with_must_haves(brief: ShoppingBrief, attribute: str, value: Any) -> ShoppingBrief | None:
    """The brief with this attribute's remembered must-haves added, or None when they don't fit the product."""
    group = schemas.group_for(brief.category)
    forms = value if isinstance(value, list) else [value]
    try:
        wishes = [_standard(group, attribute, *_wish(form)) for form in forms]
    except (ChangeProblem, ValueError):
        return None
    if not wishes:
        return None
    values = briefs.allowed_values(group, attribute)
    if not set.intersection(*(briefs.satisfying(values, rule, wanted) for rule, wanted in wishes)):
        return None
    taken = {m.id for m in brief.must_haves}
    added: list[MustHave] = []
    for rule, wanted in dict.fromkeys(wishes):
        must_id = next(f"m{n}" for n in range(1, 1000) if f"m{n}" not in taken)
        taken.add(must_id)
        added.append(MustHave(id=must_id, attribute=attribute, rule=rule, value=wanted))
    return brief.model_copy(update={"must_haves": [*brief.must_haves, *added]})


def _with_mix(brief: ShoppingBrief, attribute: str, blend: Mix) -> ShoppingBrief | None:
    """The brief with the colours as weighted nice-to-haves (one valid colour is a must-have), or None if none fit."""
    if any(n.attribute == attribute for n in brief.nice_to_haves):
        return None
    group = schemas.group_for(brief.category)
    valid: list[tuple[str | bool | float, float]] = []
    for value, share in blend.shares:
        try:
            rule, wanted = _standard(group, attribute, "is", value)
        except ChangeProblem:
            continue
        if rule == "is" and all(wanted != seen for seen, _ in valid):
            valid.append((wanted, share))
    if len(valid) < 2:
        return _with_must_haves(brief, attribute, valid[0][0]) if valid else None
    total = sum(share for _, share in valid)
    nice = [NiceToHave(attribute=attribute, value=value, weight=round(share / total, 3)) for value, share in valid]
    return brief.model_copy(update={"nice_to_haves": [*brief.nice_to_haves, *nice]})


def fill(brief: ShoppingBrief, suggestions: dict[str, Any]) -> ShoppingBrief:
    """The brief with the suggestions added where it leaves a key open; `remembered` names what was added."""
    group = schemas.group_for(brief.category)
    if group is None or not suggestions:
        return brief
    added: list[str] = []
    gender = suggestions.get("gender")
    if brief.gender is None and isinstance(gender, str) and gender in get_args(Gender):
        brief = brief.model_copy(update={"gender": gender})
        added.append("gender")
    size = suggestions.get("size")
    if group != "bags" and brief.size is None and isinstance(size, str):
        try:
            brief = _set_size(brief, size)
            added.append("size")
        except ChangeProblem:
            pass
    budget = suggestions.get("budget_max_inr")
    if brief.budget_max_inr is None and isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
        brief = brief.model_copy(update={"budget_max_inr": budget})
        added.append("budget_max_inr")
    rating = suggestions.get("min_rating")
    if brief.min_rating is None and isinstance(rating, (int, float)) and not isinstance(rating, bool) and 1 <= rating <= 5:
        brief = brief.model_copy(update={"min_rating": float(rating)})
        added.append("min_rating")
    for key, value in suggestions.items():
        if key in TOP_LEVEL and not (key == "size" and group == "bags"):
            continue
        if any(m.attribute == key for m in brief.must_haves):
            continue
        found = _with_mix(brief, key, value) if isinstance(value, Mix) else _with_must_haves(brief, key, value)
        if found is not None:
            brief = found
            added.append(key)
    if not added:
        return brief
    order = list(chat_filters(brief, ""))
    remembered = sorted({*brief.remembered, *added}, key=lambda key: order.index(key) if key in order else len(order))
    return brief.model_copy(update={"remembered": remembered})


def forget(brief: ShoppingBrief) -> ShoppingBrief:
    """The brief without anything that came from memory (the shopper's own words and saved rules stay)."""
    remembered = set(brief.remembered)
    if not remembered:
        return brief
    update: dict[str, Any] = {
        "must_haves": [m for m in brief.must_haves if m.from_rule or m.attribute not in remembered],
        "nice_to_haves": [n for n in brief.nice_to_haves if not (n.weight and n.attribute in remembered)],
        "remembered": [],
    }
    for key in ("gender", "budget_max_inr", "min_rating"):
        if key in remembered:
            update[key] = None
    if "size" in remembered and schemas.group_for(brief.category) != "bags":
        update["size"] = None
        update["missing"] = brief.missing if "size" in brief.missing else [*brief.missing, "size"]
    return brief.model_copy(update=update)


def carry(previous: ShoppingBrief, new: ShoppingBrief) -> ShoppingBrief:
    """After an edit, a key stays remembered only while its value is unchanged. A colour mix keeps its shares (Gemini
    writes the checklist again without them) until the shopper names a colour."""
    if not previous.remembered:
        return new
    shares = {(n.attribute, _canon(n.value)): n.weight for n in previous.nice_to_haves if n.weight}
    nice = [n if n.weight or (n.attribute, _canon(n.value)) not in shares
            else n.model_copy(update={"weight": shares[(n.attribute, _canon(n.value))]}) for n in new.nice_to_haves]
    new = new.model_copy(update={"nice_to_haves": nice})
    mixed = {n.attribute for n in nice if n.weight} - {m.attribute for m in new.must_haves}
    before, after = chat_filters(previous, ""), chat_filters(new, "")
    kept = [k for k in previous.remembered
            if k in mixed or (k in after and k in before and _canon(before[k]) == _canon(after[k]))]
    return new.model_copy(update={"remembered": kept})
