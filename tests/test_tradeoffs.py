import pytest

from agent.models import MustHave
from agent.rank import rank
from agent.tradeoffs import Tradeoff, suggest_tradeoffs
from tests.fixtures.ranking.factory import FRI, SAT, SUN, THU, make_brief, make_candidate


def summary(tradeoffs: list[Tradeoff]) -> list[tuple]:
    return [(t.relax, t.to, t.matches) for t in tradeoffs]


def test_one_more_day_is_offered_when_it_is_enough():
    candidates = [
        make_candidate("a", "pppp", arrives=FRI),
        make_candidate("b", "pppp", arrives=FRI),
        make_candidate("c", "pppp", arrives=SAT),
        make_candidate("d", "pfpp", arrives=THU),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(budget=1200, deadline=THU), candidates)
    assert summary(tradeoffs) == [("deadline", "2026-10-16", 2), ("must_have:pockets", None, 1)]


def test_two_more_days_are_offered_only_when_one_is_not_enough():
    candidates = [
        make_candidate("a", "pppp", arrives=SAT),
        make_candidate("b", "pppp", arrives=SAT),
        make_candidate("c", "pppp", arrives=SUN),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(deadline=THU), candidates)
    assert summary(tradeoffs) == [("deadline", "2026-10-17", 2)]


def test_a_bigger_budget_counts_only_products_within_it():
    candidates = [
        make_candidate("a", "pppp", price=1300, arrives=THU),
        make_candidate("b", "pppp", price=1450, arrives=THU),
        make_candidate("c", "pppp", price=1451, arrives=THU),
        make_candidate("d", "ppfp", price=1100, arrives=THU),
        make_candidate("e", "ppfp", price=1500, arrives=THU),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(budget=1200, deadline=THU), candidates)
    assert summary(tradeoffs) == [("budget", 1450, 2), ("must_have:sleeve_length", None, 1)]


def test_dropping_each_must_have_counts_products_that_pass_everything_else():
    candidates = [
        *(make_candidate(f"p{n}", "pfpp") for n in range(4)),
        make_candidate("s1", "pppu"),
        make_candidate("s2", "pppu"),
        make_candidate("f1", "fppp"),
        make_candidate("two-fails", "ffpp"),
        make_candidate("fail-and-unsure", "pfpu"),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(), candidates)
    assert summary(tradeoffs) == [
        ("must_have:pockets", None, 4),
        ("must_have:sheerness", None, 2),
        ("must_have:fabric", None, 1),
    ]


def test_with_a_few_matches_only_options_that_add_more_are_offered():
    candidates = [
        make_candidate("ok1", "pppp", price=1000, arrives=THU),
        make_candidate("ok2", "pppp", price=1100, arrives=THU),
        make_candidate("late", "pppp", price=1000, arrives=FRI),
        make_candidate("over", "pppp", price=1300, arrives=THU),
        make_candidate("no-pocket", "pfpp", price=1000, arrives=THU),
        make_candidate("no-pocket-late", "pfpp", price=1000, arrives=SAT),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(budget=1200, deadline=THU), candidates)
    assert summary(tradeoffs) == [
        ("deadline", "2026-10-16", 3),
        ("budget", 1450, 3),
        ("must_have:pockets", None, 3),
    ]


def test_order_is_deadline_then_budget_then_must_haves_by_matches_and_at_most_four():
    candidates = [
        make_candidate("late", "pppp", arrives=FRI),
        make_candidate("over", "pppp", price=1300, arrives=THU),
        make_candidate("f1", "fppp", arrives=THU),
        *(make_candidate(f"p{n}", "pfpp", arrives=THU) for n in range(3)),
        *(make_candidate(f"s{n}", "ppfp", arrives=THU) for n in range(2)),
        make_candidate("u1", "pppu", arrives=THU),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(budget=1200, deadline=THU), candidates)
    assert summary(tradeoffs) == [
        ("deadline", "2026-10-16", 1),
        ("budget", 1450, 1),
        ("must_have:pockets", None, 3),
        ("must_have:sleeve_length", None, 2),
    ]


def test_without_a_deadline_or_budget_only_must_haves_are_offered():
    candidates = [make_candidate("a", "pfpp", price=5000, arrives=SUN), make_candidate("b", "pppf")]
    tradeoffs = suggest_tradeoffs(make_brief(), candidates)
    assert summary(tradeoffs) == [("must_have:pockets", None, 1), ("must_have:sheerness", None, 1)]


def test_two_must_haves_on_one_detail_are_dropped_together():
    must_haves = [
        MustHave(id="m1", attribute="fabric", rule="is_not", value="polyester", from_rule=True),
        MustHave(id="m2", attribute="fabric", rule="is_not", value="rayon", from_rule=True),
        MustHave(id="m3", attribute="pockets", rule="present", value=True),
    ]
    candidates = [make_candidate("polyester", "fpp"), make_candidate("rayon", "pfp")]
    tradeoffs = suggest_tradeoffs(make_brief(must_haves), candidates)
    assert summary(tradeoffs) == [("must_have:fabric", None, 2)]


def test_no_suggestions_when_there_are_already_enough_matches():
    candidates = [
        *(make_candidate(f"ok{n}", "pppp", arrives=THU) for n in range(3)),
        make_candidate("late", "pppp", arrives=FRI),
        make_candidate("no-pocket", "pfpp", arrives=THU),
    ]
    brief = make_brief(budget=1200, deadline=THU)
    assert suggest_tradeoffs(brief, candidates) == []
    assert summary(suggest_tradeoffs(brief, candidates, min_matches=5)) == [
        ("deadline", "2026-10-16", 4),
        ("must_have:pockets", None, 4),
    ]


def test_no_suggestions_when_nothing_helps():
    candidates = [make_candidate("a", "ffpp", price=2000, arrives=SUN), make_candidate("b", "ppff")]
    assert suggest_tradeoffs(make_brief(budget=1200, deadline=THU), candidates) == []
    assert suggest_tradeoffs(make_brief(budget=1200, deadline=THU), []) == []


def test_counts_match_what_rank_would_show_after_the_change():
    candidates = [
        make_candidate("a", "pppp", price=1300, title="Kurta X"),
        make_candidate("a-copy", "pppp", price=1350, title="Kurta X"),
        make_candidate("b", "pppp", price=1400),
    ]
    tradeoffs = suggest_tradeoffs(make_brief(budget=1200), candidates)
    assert summary(tradeoffs) == [("budget", 1450, 2)]
    assert len(rank(make_brief(budget=1450), candidates).matches) == 2


@pytest.mark.parametrize("budget, raised", [(1000, 1200), (999, 1200), (1001, 1250), (1250, 1500), (100, 150)])
def test_budget_goes_up_about_a_fifth_rounded_up_to_the_next_50(budget, raised):
    candidates = [make_candidate("a", "pppp", price=raised), make_candidate("b", "pppp", price=raised + 1)]
    tradeoffs = suggest_tradeoffs(make_brief(budget=budget), candidates)
    assert summary(tradeoffs) == [("budget", raised, 1)]


def test_tradeoffs_serialise_like_the_done_event():
    candidates = [make_candidate("a", "pppp", arrives=FRI), make_candidate("b", "pfpp", price=1300, arrives=THU)]
    brief = make_brief(budget=1200, deadline=THU)
    results = rank(brief, candidates)
    results.tradeoffs = suggest_tradeoffs(brief, candidates)
    done = results.model_dump(mode="json", exclude={"matches", "almost"}, exclude_none=True)
    assert done == {
        "checked": 2,
        "passed": 0,
        "unclear": 0,
        "rejected": {"late delivery": 1, "over budget": 1},
        "duplicates": 0,
        "tradeoffs": [{"relax": "deadline", "to": "2026-10-16", "matches": 1}],
    }
