"""Offline tests for eval/demo_stories.py: the story file, choosing what to check, taps, boxes and the report."""

from datetime import date

import pytest

from agent.delivery import lookup
from agent.models import MustHave
from agent.tradeoffs import Tradeoff
from eval import demo_stories
from tests.fixtures.ranking.factory import THU, make_brief
from tests.fixtures.retrieve.factory import hit, product


def test_the_story_file_is_valid():
    stories = demo_stories.load_stories()
    on_stage = [story for story in stories if story.stage]
    assert len(on_stage) >= 5
    for story in stories:
        assert story.shopper_text.strip()
        assert lookup(story.pincode)
        assert story.now.tzinfo is not None


def test_a_follow_up_either_taps_or_says():
    assert demo_stories.FollowUp(tap="budget").tap == "budget"
    with pytest.raises(ValueError):
        demo_stories.FollowUp()
    with pytest.raises(ValueError):
        demo_stories.FollowUp(tap="budget", say="cheaper")


def test_pick_refuses_unknown_story_ids():
    stories = demo_stories.load_stories()
    assert demo_stories.pick(stories, ["neha"])[0].id == "neha"
    with pytest.raises(ValueError):
        demo_stories.pick(stories, ["nobody"])


def _hits(within: int, outside: int):
    hits = [hit(product(f"w{i}"), 0.1 + i / 100) for i in range(within)]
    hits += [hit(product(f"o{i}"), 0.2 + i / 100).model_copy(update={"over_budget": True}) for i in range(outside)]
    return sorted(hits, key=lambda h: h.distance)


def test_twelve_are_checked_with_at_most_four_over_the_limits():
    chosen = demo_stories.choose_for_check(_hits(20, 10))
    assert len(chosen) == 12
    assert sum(h.over_budget for h in chosen) == 4
    assert [h.distance for h in chosen] == sorted(h.distance for h in chosen)


def test_over_limit_products_fill_the_places_when_few_are_within():
    chosen = demo_stories.choose_for_check(_hits(3, 20))
    assert len(chosen) == 12
    assert sum(not h.over_budget for h in chosen) == 3


def test_tapping_drop_removes_every_must_have_on_that_detail():
    brief = make_brief([
        MustHave(id="m1", attribute="pockets", rule="present", value=True),
        MustHave(id="m2", attribute="pockets", rule="is", value="side"),
        MustHave(id="m3", attribute="fabric", rule="is", value="cotton"),
    ])
    tapped = demo_stories.apply_tap(brief, "must_have:pockets", [])
    assert [m.id for m in tapped.must_haves] == ["m3"]


def test_tapping_budget_or_deadline_uses_the_card_value():
    brief = make_brief(budget=1200, deadline=THU)
    cards = [Tradeoff(relax="budget", to=1450, matches=3), Tradeoff(relax="deadline", to="2026-10-17", matches=2)]
    assert demo_stories.apply_tap(brief, "budget", cards).budget_max_inr == 1450
    assert demo_stories.apply_tap(brief, "deadline", cards).deadline_date == date(2026, 10, 17)
    with pytest.raises(ValueError):
        demo_stories.apply_tap(brief, "deadline", cards[:1])


def test_box_pixels_turn_a_0_to_1000_box_into_pixels():
    assert demo_stories.box_pixels([100, 200, 500, 600], 1000, 2000) == (200, 200, 600, 1000)


def test_label_boxes_overlap_only_when_they_share_area():
    assert demo_stories.overlaps((0, 0, 10, 10), (5, 5, 15, 15))
    assert not demo_stories.overlaps((0, 0, 10, 10), (0, 10, 10, 20))


def test_checklist_key_ignores_must_have_ids_and_order():
    a = make_brief([MustHave(id="m1", attribute="fabric", rule="is", value="cotton"),
                    MustHave(id="m2", attribute="pockets", rule="present", value=True)], budget=1200)
    b = make_brief([MustHave(id="x", attribute="pockets", rule="present", value=True),
                    MustHave(id="y", attribute="fabric", rule="is", value="cotton")], budget=1200)
    assert demo_stories.checklist_key(a) == demo_stories.checklist_key(b)
    assert demo_stories.checklist_key(a) != demo_stories.checklist_key(b.model_copy(update={"budget_max_inr": 1400}))


def test_tradeoff_text_reads_like_the_app():
    assert demo_stories.tradeoff_text({"relax": "must_have:sleeve_length", "matches": 1}) == "Drop sleeve length: 1 option"
    assert demo_stories.tradeoff_text({"relax": "budget", "to": 1450, "matches": 5}) == "Allow ₹1,450: 5 options"
    assert demo_stories.tradeoff_text({"relax": "deadline", "to": "2026-10-16", "matches": 2}) == "Allow Fri 16 Oct: 2 options"


def _block(passed: int) -> dict:
    match = {"product_id": "1", "title": "Kurta", "total_price": 999, "delivery_date": "2026-10-15", "reason": "All confirmed",
             "ticks": [{"label": "pockets", "status": "pass", "proof": "image: pocket on the chest box [1, 2, 3, 4]"}]}
    return {
        "brief": {"category": "Kurtas", "gender": "women"},
        "chips": ["pockets", "≤ ₹1,200"],
        "counts": {"found": 40, "quick_dropped": 20, "checked": 12, "over_limit_checked": 2, "passed": passed,
                   "unclear": 1, "rejected": {"no pockets": 30}},
        "matches": [match] * passed,
        "almost": [],
        "rejected_after_checking": [{"product_id": "2", "title": "Other", "total_price": 700, "reason": "no pockets",
                                     "proof": {"proof": 'reviews: fake pocket "pocket is stitched shut" (0 agree, 2 disagree)'}}],
        "tradeoffs": [{"relax": "must_have:pockets", "matches": 4}],
        "planted": [], "tricks": [], "screening": {},
        "checking": {"saved_checks_used": 36, "gemini_calls": 0, "cost_usd": 0.0},
        "timings_ms": {"find_ms": 600, "quick_ms": 1, "check_ms": 200, "rank_ms": 50, "total_ms": 900},
    }


def test_cold_difference_names_products_the_live_check_decided_differently():
    saved = {"matches": [{"product_id": "1", "title": "A"}], "almost": [{"product_id": "2", "title": "B"}]}
    cold = {"matches": [{"product_id": "1", "title": "A"}, {"product_id": "2", "title": "B"}], "almost": []}
    text = demo_stories.cold_difference(saved, cold)
    assert "also full matches: B (2)" in text
    assert "no longer not sure: 2" in text
    assert demo_stories.cold_difference(saved, saved) == "Same answer as the saved checks."


def test_the_report_section_shows_text_counts_proof_and_cards():
    story = demo_stories.load_stories()[0].model_dump(mode="json")
    record = {"story": story, "city": "Pune", "saved": _block(1), "boxes": [], "follow_ups": [], "brief_ms": 3000,
              "nice_to_haves": [], "not_checkable": [], "question": None, "same_as_prechecked": True}
    text = "\n".join(demo_stories.story_section(record, {}))
    assert story["shopper_text"] in text
    assert "**1 full matches**" in text
    assert "pocket on the chest" in text
    assert "stitched shut" in text
    assert "Drop pockets: 4 options" in text
    assert "| 3900 |" in text
