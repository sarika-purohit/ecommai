import copy

import pytest

from scripts import deploy
from scripts.deploy import (
    AGENT,
    RUN_API,
    DeployError,
    Policy,
    agent_env,
    agent_service,
    agent_urls,
    apply_service,
    describe,
    differs,
    image_tag,
    log_line,
    login_free_docker_config,
    missing_web_files,
    service_path,
    web_build_args,
    web_service,
    with_domains,
    with_grants,
)

PROJECT = "demo-project"
NAME = service_path(PROJECT, AGENT)
IMAGE = "asia-south1-docker.pkg.dev/demo-project/ecommai/agent:0123456789ab"
REVISION = f"projects/{PROJECT}/locations/asia-south1/revisions/ecommai-agent-00001-abc"
SETTINGS = {
    "GOOGLE_CLOUD_PROJECT": PROJECT,
    "GOOGLE_CLOUD_QUOTA_PROJECT": PROJECT,
    "GOOGLE_CLOUD_LOCATION": "global",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "GCS_BUCKET": "demo-project-catalog",
    "UPLOAD_BUCKET": "demo-project-uploads",
    "FIRESTORE_LOCATION": "asia-south1",
    "BIGQUERY_DATASET": "ecommai",
    "MODEL_ARMOR_LOCATION": "asia-south1",
    "MODEL_ARMOR_TEMPLATE": "ecommai-armor",
    "GEMINI_MODEL": "gemini-3.8-flash",
    "GEMINI_LITE_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_IMAGE_MODEL": "",
    "GEMINI_LIVE_LOCATION": "us-central1",
    "EMBEDDING_DIMENSIONS": "768",
    "KAGGLE_API_TOKEN": "KGAT_not-for-the-cloud",
    "AUTH_MODE": "dev",
    "APP_BASE_URL": "http://192.168.1.20:3000",
    "ALLOWED_ORIGINS": "http://192.168.1.20:3000",
    "SOMETHING_ELSE": "x",
}
FIREBASE = {
    "NEXT_PUBLIC_FIREBASE_API_KEY": "AIza-demo",
    "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN": "demo-project.firebaseapp.com",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "demo-project",
    "NEXT_PUBLIC_FIREBASE_APP_ID": "1:2:web:3",
}


class FakeApi:
    """Answers calls in order from a list of (status, body) pairs and records what was sent."""

    def __init__(self, *answers: tuple[int, dict], operation: dict | None = None) -> None:
        self.answers = list(answers)
        self.operation = operation or {"done": True}
        self.calls: list[tuple[str, str, dict]] = []

    def call(self, method: str, url: str, **kwargs) -> tuple[int, dict]:
        self.calls.append((method, url, kwargs))
        return self.answers.pop(0)

    def wait(self, url: str, timeout_s: int = 300) -> dict:
        self.calls.append(("WAIT", url, {}))
        return self.operation


def served(body: dict) -> dict:
    """`body` as Cloud Run returns it once deployed: extra fields, and false or zero values left out."""
    service = copy.deepcopy(body)
    uri = "https://ecommai-agent-xyz-el.a.run.app"
    service.update(
        name=NAME, uri=uri, urls=[uri, "https://ecommai-agent-123.asia-south1.run.app"], etag="abc",
        terminalCondition={"type": "Ready", "state": "CONDITION_SUCCEEDED"},
        latestReadyRevision=REVISION, latestCreatedRevision=REVISION,
    )
    template = service["template"]
    template["revision"] = ""
    if not template["scaling"]["minInstanceCount"]:
        del template["scaling"]["minInstanceCount"]
    container = template["containers"][0]
    container["name"] = "agent-1"
    if container["resources"]["cpuIdle"] is False:
        del container["resources"]["cpuIdle"]
    if "startupProbe" in container:
        container["startupProbe"]["httpGet"]["port"] = 8080
    return service


def agent_body(env: dict[str, str] | None = None, image: str = IMAGE) -> dict:
    return agent_service(PROJECT, image, env or agent_env(SETTINGS, ["http://localhost:3000"], "http://localhost:3000"))


# ---------- Settings sent to the agent ----------


def test_agent_env_has_the_code_settings_and_never_dev_sign_in_or_keys():
    env = agent_env(SETTINGS, ["http://localhost:3000"], "http://localhost:3000")

    for name in ("AUTH_MODE", "KAGGLE_API_TOKEN", "GOOGLE_CLOUD_QUOTA_PROJECT", "SOMETHING_ELSE", "GEMINI_IMAGE_MODEL"):
        assert name not in env
    for name in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI", "GCS_BUCKET",
                 "UPLOAD_BUCKET", "FIRESTORE_LOCATION", "BIGQUERY_DATASET", "MODEL_ARMOR_LOCATION",
                 "MODEL_ARMOR_TEMPLATE", "GEMINI_MODEL", "GEMINI_LITE_MODEL", "GEMINI_LIVE_LOCATION",
                 "EMBEDDING_DIMENSIONS"):
        assert env[name] == SETTINGS[name]
    assert env["ALLOWED_ORIGINS"] == env["APP_BASE_URL"] == "http://localhost:3000"


def test_agent_env_lists_every_allowed_origin():
    env = agent_env(SETTINGS, ["https://web.example.run.app", "http://localhost:3000"], "https://web.example.run.app")

    assert env["ALLOWED_ORIGINS"] == "https://web.example.run.app,http://localhost:3000"
    assert env["APP_BASE_URL"] == "https://web.example.run.app"


def test_a_new_agent_allows_the_local_app():
    assert agent_urls(None, []) == (["http://localhost:3000"], "http://localhost:3000")


def test_a_redeploy_keeps_the_deployed_web_address():
    env = agent_env(SETTINGS, ["https://web.run.app", "http://localhost:3000"], "https://web.run.app")
    current = served(agent_body(env))

    assert agent_urls(current, []) == (["https://web.run.app", "http://localhost:3000"], "https://web.run.app")


def test_web_url_replaces_the_origins_and_the_first_is_the_base():
    current = served(agent_body())

    assert agent_urls(current, ["https://web.run.app/", "https://web-123.run.app"]) == (
        ["https://web.run.app", "https://web-123.run.app"], "https://web.run.app",
    )


# ---------- Cloud Run service bodies ----------


def test_agent_service_keeps_cpu_and_an_instance_ready():
    body = agent_service(PROJECT, IMAGE, {"GOOGLE_CLOUD_PROJECT": PROJECT})
    template = body["template"]
    container = template["containers"][0]

    assert container["resources"]["limits"] == {"cpu": "1", "memory": "1Gi"}
    assert container["resources"]["cpuIdle"] is False
    assert template["scaling"] == {"minInstanceCount": 1, "maxInstanceCount": 3}
    assert template["timeout"] == "300s"
    assert template["maxInstanceRequestConcurrency"] == 20
    assert template["serviceAccount"] == "ecommai-agent@demo-project.iam.gserviceaccount.com"
    assert container["image"] == IMAGE
    assert container["env"] == [{"name": "GOOGLE_CLOUD_PROJECT", "value": PROJECT}]
    assert container["startupProbe"]["httpGet"] == {"path": "/health"}
    assert "name" not in body


def test_web_service_runs_as_its_own_account_with_cpu_only_during_requests():
    template = web_service(PROJECT, "asia-south1-docker.pkg.dev/demo-project/ecommai/web:abc", 0)["template"]

    assert template["serviceAccount"] == "ecommai-web@demo-project.iam.gserviceaccount.com"
    assert template["containers"][0]["resources"]["cpuIdle"] is True
    assert template["scaling"] == {"minInstanceCount": 0, "maxInstanceCount": 3}


def test_differs_ignores_what_cloud_run_adds_or_leaves_out():
    assert not differs(served(agent_body()), agent_body())
    web = web_service(PROJECT, "asia-south1-docker.pkg.dev/demo-project/ecommai/web:abc", 0)
    assert not differs(served(web), web)


def test_differs_sees_a_new_image_or_setting():
    current = served(agent_body())
    other_model = agent_env({**SETTINGS, "GEMINI_MODEL": "other"}, ["http://localhost:3000"], "http://localhost:3000")

    assert differs(current, agent_body(image=IMAGE.replace("0123456789ab", "ba9876543210")))
    assert differs(current, agent_body(other_model))
    assert differs(current, agent_body({"GOOGLE_CLOUD_PROJECT": PROJECT}))


def test_apply_service_creates_a_new_service_and_waits_for_it():
    body = agent_body()
    api = FakeApi((200, {"name": "projects/p/locations/asia-south1/operations/op-1"}), (200, served(body)))

    service, changed = apply_service(api, NAME, None, body)

    assert changed and service["uri"].startswith("https://")
    method, url, sent = api.calls[0]
    assert (method, url) == ("POST", f"{RUN_API}/projects/{PROJECT}/locations/asia-south1/services")
    assert sent == {"params": {"serviceId": AGENT}, "json": body}
    assert api.calls[1][:2] == ("WAIT", f"{RUN_API}/projects/p/locations/asia-south1/operations/op-1")
    assert api.calls[2][:2] == ("GET", f"{RUN_API}/{NAME}")


def test_apply_service_updates_a_changed_service():
    body = agent_body(image=IMAGE.replace("0123456789ab", "ba9876543210"))
    api = FakeApi((200, {"name": "operations/op-2"}), (200, served(body)))

    apply_service(api, NAME, served(agent_body()), body)

    assert api.calls[0] == ("PATCH", f"{RUN_API}/{NAME}", {"json": body})


def test_apply_service_leaves_an_unchanged_service_alone():
    current = served(agent_body())
    api = FakeApi()

    service, changed = apply_service(api, NAME, current, agent_body())

    assert service is current and not changed and api.calls == []


def test_apply_service_reports_a_revision_that_did_not_start():
    error = {"done": True, "error": {"message": "The user-provided container failed to start"}}
    api = FakeApi((200, {"name": "operations/op-3"}), operation=error)

    with pytest.raises(DeployError, match="failed to start"):
        apply_service(api, NAME, None, agent_body())


def test_first_agent_deploy_sends_the_local_origin_and_opens_the_service(monkeypatch):
    body = agent_body()
    api = FakeApi(
        (404, {}),
        (200, {"name": "operations/op-1"}),
        (200, served(body)),
        (200, {"etag": "e1"}),
        (200, {}),
    )

    deploy.deploy_agent(api, SETTINGS, IMAGE, [])

    sent = api.calls[1][2]["json"]
    env = {item["name"]: item["value"] for item in sent["template"]["containers"][0]["env"]}
    assert env["ALLOWED_ORIGINS"] == "http://localhost:3000" and "AUTH_MODE" not in env
    method, url, policy = api.calls[-1]
    assert (method, url) == ("POST", f"{RUN_API}/{NAME}:setIamPolicy")
    invoker = {"role": "roles/run.invoker", "members": ["allUsers"]}
    assert policy["json"]["policy"] == {"etag": "e1", "bindings": [invoker]}


# ---------- IAM policies ----------


def test_with_grants_adds_only_what_is_missing_and_keeps_the_rest():
    policy = {
        "etag": "BwX",
        "version": 3,
        "bindings": [
            {"role": "roles/owner", "members": ["user:me@example.com"]},
            {"role": "roles/datastore.user", "members": ["serviceAccount:agent"]},
            {"role": "roles/aiplatform.user", "members": ["serviceAccount:agent"], "condition": {"title": "for now"}},
        ],
    }
    original = copy.deepcopy(policy)

    updated, added = with_grants(policy, {"roles/datastore.user": ["serviceAccount:agent"],
                                          "roles/aiplatform.user": ["serviceAccount:agent"]})

    assert added == ["roles/aiplatform.user for serviceAccount:agent"]
    assert updated["etag"] == "BwX" and updated["version"] == 3
    assert updated["bindings"][:3] == original["bindings"]
    assert updated["bindings"][3] == {"role": "roles/aiplatform.user", "members": ["serviceAccount:agent"]}
    assert policy == original


def test_with_grants_extends_an_existing_binding():
    policy = {"bindings": [{"role": "roles/run.invoker", "members": ["user:me@example.com"]}]}

    updated, added = with_grants(policy, {"roles/run.invoker": ["allUsers"]})

    assert updated["bindings"] == [{"role": "roles/run.invoker", "members": ["user:me@example.com", "allUsers"]}]
    assert added == ["roles/run.invoker for allUsers"]


def test_grant_leaves_a_complete_policy_alone():
    writes = []
    policy = Policy("project roles", lambda: (200, {"bindings": [{"role": "r", "members": ["m"]}]}),
                    lambda p: writes.append(p) or (200, p))

    result = deploy.grant(policy, {"r": ["m"]})

    assert result.status == "exists" and writes == []


def test_grant_reads_again_when_the_policy_changed_or_the_account_is_new(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda seconds: None)
    reads = iter([(200, {"etag": "1"}), (200, {"etag": "2"}), (200, {"etag": "3"})])
    writes = iter([(409, {"error": {"message": "concurrent change"}}),
                   (400, {"error": {"message": "Service account agent does not exist."}}),
                   (200, {})])
    sent = []
    policy = Policy("project roles", lambda: next(reads), lambda p: sent.append(p) or next(writes))

    result = deploy.grant(policy, {"roles/datastore.user": ["serviceAccount:agent"]})

    assert result.status == "ok"
    assert [p["etag"] for p in sent] == ["1", "2", "3"]


def test_grant_stops_on_a_real_error():
    policy = Policy("gs://bucket", lambda: (200, {}), lambda p: (403, {"error": {"message": "permission denied"}}))

    result = deploy.grant(policy, {"roles/storage.objectAdmin": ["serviceAccount:agent"]})

    assert result.status == "failed" and "permission denied" in result.detail


# ---------- The web app ----------


def test_web_build_args_point_at_the_agent_and_never_use_mock_data():
    web_env = {**FIREBASE, "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8080", "NEXT_PUBLIC_USE_MOCK": "1"}

    args = web_build_args(web_env, "https://ecommai-agent-xyz-el.a.run.app/")

    assert args == {**FIREBASE, "NEXT_PUBLIC_API_BASE_URL": "https://ecommai-agent-xyz-el.a.run.app"}


def test_web_build_args_need_the_firebase_settings():
    with pytest.raises(DeployError, match="NEXT_PUBLIC_FIREBASE_APP_ID"):
        web_build_args({**FIREBASE, "NEXT_PUBLIC_FIREBASE_APP_ID": ""}, "https://agent.run.app")


def test_missing_web_files(tmp_path):
    (tmp_path / "next.config.ts").write_text("const nextConfig = { poweredByHeader: false };")
    assert missing_web_files(tmp_path) == [
        "web/Dockerfile", "web/.dockerignore", 'output: "standalone" in web/next.config.ts',
    ]

    (tmp_path / "next.config.ts").write_text('const nextConfig = { output: "standalone" };')
    (tmp_path / "Dockerfile").write_text("FROM node")
    (tmp_path / ".dockerignore").write_text("node_modules")
    assert missing_web_files(tmp_path) == []


def test_with_domains_adds_each_new_host_once():
    current = ["localhost", "demo-project.firebaseapp.com"]

    assert with_domains(current, ["web-xyz.a.run.app", "web-123.run.app", "web-xyz.a.run.app"]) == [
        *current, "web-xyz.a.run.app", "web-123.run.app",
    ]
    assert with_domains(current, ["localhost"]) is None


# ---------- Images, status and logs ----------


def test_image_tag_is_the_short_image_id():
    assert image_tag("sha256:0123456789abcdef0123456789abcdef") == "0123456789ab"


def test_docker_config_starts_without_a_login():
    assert login_free_docker_config() == {"auths": {"asia-south1-docker.pkg.dev": {}}}


def test_status_shows_setting_names_not_values():
    text = "\n".join(describe(served(agent_body())))

    assert "GEMINI_MODEL" in text and "gemini-3.8-flash" not in text
    assert "min 1, max 3" in text and "CPU always allocated" in text and "ecommai-agent-00001-abc" in text


def test_log_line_shows_text_or_the_request():
    labels = {"resource": {"labels": {"revision_name": "ecommai-agent-00001-abc"}}}
    text = {"timestamp": "2026-10-13T09:00:00.123Z", "severity": "ERROR", "textPayload": "Traceback ...\n", **labels}
    http = {"requestMethod": "GET", "requestUrl": "https://x/health", "status": 200, "latency": "0.003s"}
    request = {"timestamp": "2026-10-13T09:00:01Z", "severity": "INFO", "httpRequest": http, **labels}

    audit = {"timestamp": "2026-10-13T09:00:02Z", "severity": "NOTICE", "protoPayload": {
        "methodName": "google.cloud.run.v2.Services.UpdateService", "status": {"message": "done"}, "request": {"x": 1}}}

    assert log_line(text) == "2026-10-13T09:00:00 ERROR    ecommai-agent-00001-abc Traceback ..."
    assert log_line(request).endswith("GET https://x/health 200 0.003s")
    assert log_line(audit).endswith("google.cloud.run.v2.Services.UpdateService done")
