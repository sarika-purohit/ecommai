import io
import json
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.genai import errors, types
from PIL import Image

from scripts import check_models
from scripts.check_models import CHECK_NAMES, Result, Settings, SetupError, draw_box, parse_box, summarize

ENV = {"GOOGLE_CLOUD_PROJECT": "demo-project", "MODEL_ARMOR_TEMPLATE": "ecommai-armor"}
GOOD_BOX = [80, 230, 330, 770]
HINGLISH = "Mujhe office ke liye cotton kurta chahiye, pockets ke saath, budget barah sau rupaye."


def text_response(text: str) -> types.GenerateContentResponse:
    part = types.Part.from_text(text=text)
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[part]))])


def picture_response() -> types.GenerateContentResponse:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buffer, format="PNG")
    part = types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png")
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[part]))])


def has_audio(contents: list) -> bool:
    parts = [item for item in contents if isinstance(item, types.Part) and item.inline_data]
    return any(part.inline_data.mime_type == "audio/wav" for part in parts)


class FakeModels:
    """Answers like a working Agent Platform. Change an attribute to make one call misbehave."""

    def __init__(self) -> None:
        self.box_answer = json.dumps({"box_2d": GOOD_BOX})
        self.transcript = HINGLISH
        self.dimensions: int | None = None
        self.combined = "supported"
        self.error: Exception | None = None

    def generate_content(self, *, model, contents, config=None):
        if self.error:
            raise self.error
        if config and config.response_modalities:
            return picture_response()
        if config and config.response_mime_type == "application/json":
            return text_response(self.box_answer)
        if has_audio(contents):
            return text_response(self.transcript)
        return text_response("Blue and white stripes.")

    def embed_content(self, *, model, contents, config):
        parts = contents[0].parts
        if len(parts) > 1 and self.combined == "refused":
            raise errors.ClientError(400, {"error": {"code": 400, "message": "Too many parts"}})
        count = len(parts) if self.combined == "split" else 1
        values = [0.01] * (self.dimensions or config.output_dimensionality)
        return types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=values)] * count)


class FakeSession:
    async def send_client_content(self, *, turns, turn_complete):
        self.turns = turns

    async def receive(self):
        audio = types.Part(inline_data=types.Blob(data=b"\x00\x00" * 2400, mime_type="audio/pcm;rate=24000"))
        speaking = types.LiveServerContent(model_turn=types.Content(parts=[audio]))
        yield types.LiveServerMessage(server_content=speaking)
        yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))


class FakeLive:
    def __init__(self) -> None:
        self.error: Exception | None = None

    @asynccontextmanager
    async def connect(self, *, model, config):
        if self.error:
            raise self.error
        yield FakeSession()


class FakeHttp:
    def __init__(self) -> None:
        self.match_state = "MATCH_FOUND"
        self.urls: list[str] = []

    def post(self, url, *, json, timeout):
        self.urls.append(url)
        result = {"executionState": "EXECUTION_SUCCESS", "matchState": self.match_state, "confidenceLevel": "HIGH"}
        payload = {"sanitizationResult": {"filterResults": {"pi_and_jailbreak": {"piAndJailbreakFilterResult": result}}}}
        return SimpleNamespace(status_code=200, text=str(payload), json=lambda: payload)


@pytest.fixture
def fake():
    models, live, http = FakeModels(), FakeLive(), FakeHttp()
    clients = check_models.Clients(
        genai=SimpleNamespace(models=models),
        live=SimpleNamespace(aio=SimpleNamespace(live=live)),
        http=http,
    )
    return SimpleNamespace(models=models, live=live, http=http, clients=clients)


def run(fake, tmp_path, *args: str, env: dict[str, str] = ENV) -> int:
    return check_models.main(
        ["--out", str(tmp_path), *args],
        env=env,
        credentials_loader=lambda project: "fake-credentials",
        connector=lambda settings, credentials: fake.clients,
    )


def statuses(output: str) -> dict[str, str]:
    rows = [line.split() for line in output.splitlines() if line.startswith(("OK ", "FAIL "))]
    return {row[1]: row[0] for row in rows}


# ---------- Collar box ----------


@pytest.mark.parametrize("box", [GOOD_BOX, [0, 0, 1000, 1000]])
def test_parse_box_accepts_a_good_box(box):
    assert parse_box(json.dumps({"box_2d": box})) == box


@pytest.mark.parametrize(
    "answer",
    [
        "the collar is at the top",
        "[80, 230, 330, 770]",
        '{"box": [80, 230, 330, 770]}',
        '{"box_2d": [80, 230, 330]}',
        '{"box_2d": [80, 230, 330, 770, 900]}',
        '{"box_2d": "80, 230, 330, 770"}',
        '{"box_2d": [80.5, 230, 330, 770]}',
        '{"box_2d": [true, 230, 330, 770]}',
    ],
)
def test_parse_box_rejects_bad_shapes(answer):
    with pytest.raises(ValueError):
        parse_box(answer)


@pytest.mark.parametrize("box", [[-1, 230, 330, 770], [80, 230, 1001, 770], [330, 230, 80, 770], [80, 770, 330, 770]])
def test_parse_box_rejects_out_of_range_or_flipped_boxes(box):
    with pytest.raises(ValueError, match="box_2d"):
        parse_box(json.dumps({"box_2d": box}))


def test_draw_box_marks_the_box_on_a_copy_of_the_photo(tmp_path):
    out = draw_box(check_models.SHIRT_PHOTO, GOOD_BOX, tmp_path / "box.jpg")
    with Image.open(check_models.SHIRT_PHOTO) as original, Image.open(out) as marked:
        assert marked.size == original.size
        width, height = marked.size
        left_edge_middle = (round(230 * width / 1000) + 2, round(205 * height / 1000))
        red, green, blue = marked.convert("RGB").getpixel(left_edge_middle)
    assert red > 200 and green < 80 and blue < 80


def test_assets_are_the_size_and_format_the_checks_expect():
    with Image.open(check_models.SHIRT_PHOTO) as photo:
        assert max(photo.size) <= 1000
    with wave.open(str(check_models.VOICE_CLIP)) as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (16000, 1, 2)
        assert 1 < clip.getnframes() / clip.getframerate() < 15


# ---------- Settings ----------


def test_settings_defaults_fill_in_blank_values():
    settings = Settings.from_env({"GOOGLE_CLOUD_PROJECT": "p", "GEMINI_IMAGE_MODEL": "", "MODEL_ARMOR_TEMPLATE": " "})
    assert settings == Settings(
        project="p",
        location="global",
        live_location="us-central1",
        flash_model="gemini-3.8-flash",
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
        image_model="gemini-nano-banana-2.1",
        live_model="gemini-3.8-live",
        armor_location="asia-south1",
        armor_template="",
    )


def test_settings_reject_dimensions_that_are_not_a_number():
    with pytest.raises(SetupError, match="EMBEDDING_DIMENSIONS"):
        Settings.from_env({"GOOGLE_CLOUD_PROJECT": "p", "EMBEDDING_DIMENSIONS": "768d"})


# ---------- Report and exit codes ----------


def test_all_checks_pass_and_the_model_names_are_printed(fake, tmp_path, capsys):
    assert run(fake, tmp_path) == 0
    out = capsys.readouterr().out
    assert statuses(out) == {name: "OK" for name in CHECK_NAMES}
    for name in ("gemini-3.8-flash", "gemini-embedding-2", "gemini-nano-banana-2.1", "gemini-3.8-live", "asia-south1"):
        assert name in out
    assert "7 of 7 checks OK." in out and "All required checks passed." in out
    assert {path.name for path in tmp_path.iterdir()} == {"collar_box.jpg", "watch.png", "live_hello.wav"}
    assert fake.http.urls == [
        "https://modelarmor.asia-south1.rep.googleapis.com/v1/projects/demo-project"
        "/locations/asia-south1/templates/ecommai-armor:sanitizeUserPrompt"
    ]


def test_a_required_failure_exits_with_1(fake, tmp_path, capsys):
    fake.models.dimensions = 3072
    assert run(fake, tmp_path) == 1
    out = capsys.readouterr().out
    assert statuses(out)["embedding"] == "FAIL"
    assert "3072 numbers instead of 768" in out
    assert "Required checks that failed: embedding." in out


def test_only_the_live_check_failing_exits_with_0(fake, tmp_path, capsys):
    fake.live.error = ConnectionError("model not offered here")
    assert run(fake, tmp_path) == 0
    out = capsys.readouterr().out
    assert statuses(out)["live"] == "FAIL"
    assert "(optional) ConnectionError: model not offered here" in out
    assert "6 of 7 checks OK." in out and "(they don't fail the run): live." in out


def test_summary_counts_required_and_optional_failures():
    def result(name: str, ok: bool, required: bool = True) -> Result:
        return Result(name, ok, required, "detail", 5)

    results = [result("embedding", False), result("live", False, required=False), result("flash_photo", True)]
    text, code = summarize(results)
    assert code == 1
    assert text.splitlines()[0] == "1 of 3 checks OK."
    assert summarize([result("live", False, required=False), result("flash_photo", True)])[1] == 0


def test_an_api_error_shows_the_error_and_a_hint(fake, tmp_path, capsys):
    fake.models.error = errors.ClientError(404, {"error": {"code": 404, "message": "Publisher model not found"}})
    assert run(fake, tmp_path, "--only", "flash_photo") == 1
    out = capsys.readouterr().out
    assert "ClientError: 404" in out and "Publisher model not found" in out
    assert "Not found: check the model or template name" in out


# ---------- Choosing checks ----------


def test_only_runs_just_the_named_checks(fake, tmp_path, capsys):
    assert run(fake, tmp_path, "--only", "embedding", "--only", "model_armor") == 0
    out = capsys.readouterr().out
    assert set(statuses(out)) == {"embedding", "model_armor"}
    assert "2 of 2 checks OK." in out


def test_skip_leaves_out_the_named_checks(fake, tmp_path, capsys):
    assert run(fake, tmp_path, "--skip", "live", "--skip", "image_generation") == 0
    assert set(statuses(capsys.readouterr().out)) == set(CHECK_NAMES) - {"live", "image_generation"}


@pytest.mark.parametrize("args", [["--only", "nope"], ["--skip", "flash"], ["--only", "live", "--skip", "live"]])
def test_bad_choices_are_rejected(fake, tmp_path, args):
    with pytest.raises(SystemExit) as exit_info:
        run(fake, tmp_path, *args)
    assert exit_info.value.code == 2


# ---------- Friendly setup errors ----------


def test_missing_project_says_how_to_set_it(capsys):
    def no_call(project):
        raise AssertionError("credentials shouldn't be loaded without a project")

    assert check_models.main([], env={"GOOGLE_CLOUD_PROJECT": " "}, credentials_loader=no_call) == 1
    err = capsys.readouterr().err
    assert "GOOGLE_CLOUD_PROJECT isn't set" in err and ".env" in err


def test_missing_credentials_say_to_run_the_gcloud_login(monkeypatch, capsys):
    def no_credentials(**kwargs):
        raise DefaultCredentialsError("Your default credentials were not found.")

    monkeypatch.setattr(check_models.google.auth, "default", no_credentials)
    assert check_models.main([], env=ENV, connector=lambda settings, credentials: pytest.fail("connected")) == 1
    err = capsys.readouterr().err
    assert "No Application Default Credentials found" in err
    assert "gcloud auth application-default login" in err


def test_expired_credentials_say_to_sign_in_again(monkeypatch, capsys):
    class ExpiredCredentials:
        def refresh(self, request):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(check_models.google.auth, "default", lambda **kwargs: (ExpiredCredentials(), "demo-project"))
    assert check_models.main([], env=ENV) == 1
    err = capsys.readouterr().err
    assert "Token has been expired or revoked" in err and "gcloud auth application-default login" in err


# ---------- Individual checks failing ----------


def test_model_armor_without_a_template_prints_the_gcloud_command(fake, tmp_path, capsys):
    assert run(fake, tmp_path, "--only", "model_armor", env={"GOOGLE_CLOUD_PROJECT": "demo-project"}) == 1
    out = capsys.readouterr().out
    assert statuses(out) == {"model_armor": "FAIL"}
    assert 'api_endpoint_overrides/modelarmor "https://modelarmor.asia-south1.rep.googleapis.com/"' in out
    assert "gcloud model-armor templates create ecommai-armor --project=demo-project --location=asia-south1" in out
    assert "--pi-and-jailbreak-filter-settings-enforcement=enabled" in out
    assert fake.http.urls == []


def test_model_armor_fails_when_the_trick_text_is_not_flagged(fake, tmp_path, capsys):
    fake.http.match_state = "NO_MATCH_FOUND"
    assert run(fake, tmp_path, "--only", "model_armor") == 1
    assert "didn't flag the trick text (NO_MATCH_FOUND)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "combined, message",
    [
        ("split", "gave 2 embeddings instead of 1"),
        ("refused", "was refused, so combined embeddings may not be supported"),
    ],
)
def test_embedding_says_clearly_when_photo_and_title_cannot_go_in_one_call(fake, tmp_path, capsys, combined, message):
    fake.models.combined = combined
    assert run(fake, tmp_path, "--only", "embedding") == 1
    assert message in capsys.readouterr().out


def test_photo_spot_fails_on_a_flipped_box(fake, tmp_path, capsys):
    fake.models.box_answer = json.dumps({"box_2d": [500, 100, 200, 900]})
    assert run(fake, tmp_path, "--only", "flash_photo_spot") == 1
    assert "each min below its max" in capsys.readouterr().out
    assert not (tmp_path / "collar_box.jpg").exists()


def test_audio_fails_when_the_transcript_misses_the_request(fake, tmp_path, capsys):
    fake.models.transcript = "Sorry, I can't make out any words."
    assert run(fake, tmp_path, "--only", "flash_audio") == 1
    assert "doesn't mention kurta, cotton or pockets" in capsys.readouterr().out


# ---------- Real services ----------


@pytest.mark.live
def test_every_model_works_through_agent_platform(tmp_path):
    """Needs .env with GOOGLE_CLOUD_PROJECT and MODEL_ARMOR_TEMPLATE, plus Application Default Credentials."""
    assert check_models.main(["--out", str(tmp_path)]) == 0
