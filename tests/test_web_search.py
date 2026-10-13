from google.genai import errors, types

from agent import web_search
from agent.models import MustHave, ShoppingBrief
from tests.fixtures.app.fakes import FakeClient

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
ENTRY_POINT = (
    '<style>.container{align-items:center;border-radius:8px}@media(prefers-color-scheme:dark){.chip{color:#fff}}'
    '</style><div class="container"><a class="chip" href="https://www.google.com/search?q=cotton+kurta+pockets">'
    "cotton kurta with pockets</a></div>"
)
LINES = [
    "Sangria Printed Pure Cotton Kurta with Pockets | Myntra | ₹695 | cotton, pockets",
    "Invented Kurta at https://www.myntra.com/kurtas/made-up/123 | Myntra | ₹499 | cotton, pockets",
    "Cotton Kurta Haul | YouTube | unknown | pockets",
    "Cotton Straight Kurta with Side Pocket | jivithacreations.com | unknown | cotton, pockets",
]


def brief() -> ShoppingBrief:
    return ShoppingBrief(
        category="Kurtas", gender="women", budget_max_inr=1500, pincode="411038",
        must_haves=[MustHave(id="m1", attribute="fabric", rule="is", value="cotton"),
                    MustHave(id="m2", attribute="pockets", rule="present", value=True)],
    )


def chunk(name: str, domain: str) -> types.GroundingChunk:
    return types.GroundingChunk(web=types.GroundingChunkWeb(uri=REDIRECT + name, title=domain, domain=domain))


def support(text: str, line: int, chunks: list[int], *, from_start: bool = False) -> types.GroundingSupport:
    """A support ending on `line`, with byte offsets; Vertex often starts the segment at 0 (from_start)."""
    before = "\n".join(LINES[:line])
    start = 0 if from_start or not line else len(before.encode("utf-8")) + 1
    end = len("\n".join(LINES[: line + 1]).encode("utf-8"))
    return types.GroundingSupport(
        segment=types.Segment(start_index=start or None, end_index=end, text=text.encode("utf-8")[start:end].decode()),
        grounding_chunk_indices=chunks,
    )


def grounded_response(text: str = "\n".join(LINES), *, metadata: types.GroundingMetadata | None = None):
    metadata = metadata or types.GroundingMetadata(
        web_search_queries=["women cotton kurta with pockets under 1500", "cotton kurta pocket myntra"],
        search_entry_point=types.SearchEntryPoint(rendered_content=ENTRY_POINT),
        grounding_chunks=[chunk("myntra-1", "myntra.com"), chunk("yt-1", "youtube.com"),
                          chunk("jivitha-1", "jivithacreations.com")],
        grounding_supports=[support(text, 0, [0]), support(text, 2, [1]), support(text, 3, [2], from_start=True)],
    )
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]),
                                    grounding_metadata=metadata)]
    )


def test_only_lines_grounded_on_a_store_page_are_kept_with_the_chunks_own_link():
    findings = web_search.findings_from(grounded_response())
    assert [result.model_dump() for result in findings.results] == [
        {"title": "Sangria Printed Pure Cotton Kurta with Pockets", "url": REDIRECT + "myntra-1", "site": "myntra.com",
         "price_text": "₹695", "snippet": "cotton, pockets"},
        {"title": "Cotton Straight Kurta with Side Pocket", "url": REDIRECT + "jivitha-1",
         "site": "jivithacreations.com", "price_text": None, "snippet": "cotton, pockets"},
    ]
    assert all(result.url.startswith(REDIRECT) for result in findings.results)
    assert "made-up" not in findings.model_dump_json()


def test_the_search_suggestions_html_and_queries_pass_through_unchanged():
    findings = web_search.findings_from(grounded_response())
    assert findings.search_entry_point_html == ENTRY_POINT
    assert findings.queries == ["women cotton kurta with pockets under 1500", "cotton kurta pocket myntra"]


def test_an_answer_without_grounding_shows_nothing():
    metadata = types.GroundingMetadata(web_search_queries=["kurta"], grounding_chunks=[], grounding_supports=[])
    assert web_search.findings_from(grounded_response(metadata=metadata)) is None
    plain = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[types.Part(text="x")]))])
    assert web_search.findings_from(plain) is None


def test_suggestions_without_grounded_listings_are_still_passed_on():
    metadata = types.GroundingMetadata(web_search_queries=["kurta"],
                                       search_entry_point=types.SearchEntryPoint(rendered_content=ENTRY_POINT))
    findings = web_search.findings_from(grounded_response(metadata=metadata))
    assert findings.results == [] and findings.search_entry_point_html == ENTRY_POINT


def test_find_on_web_makes_one_grounded_gemini_call():
    client = FakeClient(lambda contents: "unused")
    client.models.generate_content = lambda *, model, contents, config: (
        client.models.calls.append((model, contents, config)) or grounded_response()
    )
    findings = web_search.find_on_web(brief(), "cotton kurta with pockets under 1500", client=client, model="m")
    assert len(findings.results) == 2
    [(model, prompt, config)] = client.models.calls
    assert model == "m"
    assert config.tools[0].google_search is not None
    assert "Must have: cotton; pockets" in prompt and "Budget: up to ₹1,500" in prompt
    assert "Product: women's kurta" in prompt and f"at most {web_search.MAX_SEARCHES} Google searches" in prompt


def test_a_failing_web_search_never_raises():
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    client = FakeClient(lambda contents: busy)
    assert web_search.find_on_web(brief(), "kurta", client=client, model="m") is None


def test_lines_are_read_with_byte_offsets_and_tolerant_formatting():
    assert web_search.byte_lines("₹1\nab") == [(0, 4, "₹1"), (5, 7, "ab")]
    assert web_search.parse_line("1. **Kurta** | Ajio | Rs 999 | pockets") == ("Kurta", "Rs 999", "pockets")
    assert web_search.parse_line("- Kurta | Ajio | N/A") == ("Kurta", None, None)
    assert web_search.parse_line("Here are some options:") is None


def test_social_and_video_sites_are_not_stores():
    chunks = [chunk("a", "in.pinterest.com"), chunk("b", "www.youtube.com"), chunk("c", "netflix.com")]
    assert web_search.store_chunk(chunks, [0, 1, 2]).uri == REDIRECT + "c"
    assert web_search.store_chunk(chunks, [0, 1, 7]) is None
