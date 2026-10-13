"""Deploy EcommAI to Cloud Run (IMPLEMENTATION.md steps 2.10 and 3.8).

Run from the repo root with Docker running and Application Default Credentials
(`gcloud auth application-default login`). Settings come from .env:

    uv run python -m scripts.deploy setup    # once: Artifact Registry repo, service accounts, roles
    uv run python -m scripts.deploy agent    # build, push and deploy the agent API
    uv run python -m scripts.deploy web      # build and deploy the web app, then point the agent at it
    uv run python -m scripts.deploy status
    uv run python -m scripts.deploy logs

Safe to run again: anything already right is left alone, and an unchanged image or setting
doesn't make a new revision. Commands that don't build (setup, status, logs, agent --image)
also run in the dev container:

    docker run --rm -v "$PWD":/app -v "$HOME/.config/gcloud":/root/.config/gcloud:ro \
        ecommai-dev python -m scripts.deploy status
"""

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import google.auth
import requests
from dotenv import dotenv_values
from google.auth.transport.requests import AuthorizedSession, Request

from scripts import setup_cloud
from scripts.setup_cloud import Result, error_text

ROOT = Path(__file__).resolve().parent.parent
REGION = "asia-south1"
REGISTRY = f"{REGION}-docker.pkg.dev"
REPOSITORY = "ecommai"
AGENT = "ecommai-agent"
WEB = "ecommai-web"
PLATFORM = "linux/amd64"
LOCAL_WEB_URL = "http://localhost:3000"
DOCKER_CONFIG_DIR = ROOT / ".docker-config"
RUN_API = "https://run.googleapis.com/v2"

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
SERVICES = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "identitytoolkit.googleapis.com",
]
AGENT_ROLES = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/modelarmor.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
]
AGENT_SETTINGS = {
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GCS_BUCKET",
    "UPLOAD_BUCKET",
    "FIRESTORE_LOCATION",
    "FIRESTORE_DATABASE",
    "BIGQUERY_DATASET",
    "EMBEDDING_DIMENSIONS",
    "ADK_MODEL",
}
AGENT_SETTING_PREFIXES = ("GEMINI_", "MODEL_ARMOR_")
NEVER_DEPLOYED = ("AUTH_MODE", "KAGGLE_", "GOOGLE_CLOUD_QUOTA_PROJECT")
FIREBASE_SETTINGS = [
    "NEXT_PUBLIC_FIREBASE_API_KEY",
    "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
    "NEXT_PUBLIC_FIREBASE_APP_ID",
]
POLICY_ATTEMPTS = 6


class DeployError(Exception):
    pass


class Api(setup_cloud.Api):
    """Google Cloud REST calls as the person deploying, billed to the project."""

    def __init__(self, project: str) -> None:
        credentials, _ = google.auth.default(scopes=SCOPES)
        self.credentials = credentials.with_quota_project(project)
        self.session = AuthorizedSession(self.credentials)

    def token(self) -> str:
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        return self.credentials.token


# ---------- Names ----------


def service_account(project: str, account_id: str) -> str:
    return f"{account_id}@{project}.iam.gserviceaccount.com"


def service_path(project: str, service: str) -> str:
    return f"projects/{project}/locations/{REGION}/services/{service}"


def image_name(project: str, name: str) -> str:
    return f"{REGISTRY}/{project}/{REPOSITORY}/{name}"


def image_tag(image_id: str) -> str:
    return image_id.removeprefix("sha256:")[:12]


def read_settings(path: Path = ROOT / ".env") -> dict[str, str]:
    return {name: value for name, value in dotenv_values(path).items() if value is not None}


# ---------- IAM policies ----------


@dataclass
class Policy:
    """Where to read and write one resource's IAM policy."""

    label: str
    read: Callable[[], tuple[int, dict]]
    write: Callable[[dict], tuple[int, dict]]


def members_of(bindings: list[dict], role: str) -> set[str]:
    return {m for b in bindings if b["role"] == role and "condition" not in b for m in b.get("members", [])}


def with_grants(policy: dict, grants: Mapping[str, Sequence[str]]) -> tuple[dict, list[str]]:
    """A copy of `policy` where every member has its role, plus the grants that were missing."""
    policy = copy.deepcopy(policy)
    bindings = policy.setdefault("bindings", [])
    added = []
    for role, members in grants.items():
        missing = [m for m in members if m not in members_of(bindings, role)]
        if not missing:
            continue
        binding = next((b for b in bindings if b["role"] == role and "condition" not in b), None)
        if binding is None:
            binding = {"role": role, "members": []}
            bindings.append(binding)
        binding.setdefault("members", []).extend(missing)
        added += [f"{role} for {m}" for m in missing]
    return policy, added


def grant(policy: Policy, grants: Mapping[str, Sequence[str]]) -> Result:
    for _ in range(POLICY_ATTEMPTS):
        code, current = policy.read()
        if code >= 400:
            return Result(policy.label, "failed", error_text(current))
        updated, added = with_grants(current, grants)
        if not added:
            return Result(policy.label, "exists", ", ".join(grants))
        code, body = policy.write(updated)
        # 409/412: someone else changed the policy (etag). 400 "does not exist": a new account isn't visible yet.
        if code in (409, 412) or (code == 400 and "does not exist" in error_text(body)):
            time.sleep(5)
            continue
        if code >= 400:
            return Result(policy.label, "failed", error_text(body))
        return Result(policy.label, "ok", "; ".join(added))
    return Result(policy.label, "failed", "the policy kept changing or the account isn't visible yet; run again")


def project_policy(api: Api, project: str) -> Policy:
    url = f"https://cloudresourcemanager.googleapis.com/v3/projects/{project}"
    return Policy(
        "project roles",
        lambda: api.call("POST", f"{url}:getIamPolicy", json={"options": {"requestedPolicyVersion": 3}}),
        lambda policy: api.call("POST", f"{url}:setIamPolicy", json={"policy": policy}),
    )


def bucket_policy(api: Api, bucket: str) -> Policy:
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam"
    return Policy(
        f"gs://{bucket}",
        lambda: api.call("GET", url, params={"optionsRequestedPolicyVersion": 3}),
        lambda policy: api.call("PUT", url, json=policy),
    )


def account_policy(api: Api, email: str) -> Policy:
    url = f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{email}"
    return Policy(
        f"deploy as {email.split('@')[0]}",
        lambda: api.call("POST", f"{url}:getIamPolicy", params={"options.requestedPolicyVersion": 3}),
        lambda policy: api.call("POST", f"{url}:setIamPolicy", json={"policy": policy}),
    )


def run_policy(api: Api, name: str) -> Policy:
    url = f"{RUN_API}/{name}"
    return Policy(
        f"public {name.rsplit('/', 1)[-1]}",
        lambda: api.call("GET", f"{url}:getIamPolicy"),
        lambda policy: api.call("POST", f"{url}:setIamPolicy", json={"policy": policy}),
    )


# ---------- setup ----------


def enable_missing_services(api: Api, project: str) -> Result:
    base = f"https://serviceusage.googleapis.com/v1/projects/{project}/services"
    missing = [name for name in SERVICES if api.call("GET", f"{base}/{name}")[1].get("state") != "ENABLED"]
    if not missing:
        return Result("services", "exists", f"{len(SERVICES)} already on")
    return setup_cloud.enable_services(api, project, missing)


def create_repository(api: Api, project: str) -> Result:
    base = f"https://artifactregistry.googleapis.com/v1/projects/{project}/locations/{REGION}/repositories"
    where = f"{REGISTRY}/{project}/{REPOSITORY}"
    code, body = api.call("GET", f"{base}/{REPOSITORY}")
    if code == 200:
        return Result("docker repository", "exists", where)
    if code != 404:
        return Result("docker repository", "failed", error_text(body))
    code, operation = api.call(
        "POST", base, params={"repositoryId": REPOSITORY},
        json={"format": "DOCKER", "description": "EcommAI container images"},
    )
    if code >= 400:
        return Result("docker repository", "failed", error_text(operation))
    if not operation.get("done"):
        operation = api.wait(f"https://artifactregistry.googleapis.com/v1/{operation['name']}")
    if "error" in operation:
        return Result("docker repository", "failed", error_text(operation))
    return Result("docker repository", "ok", where)


def create_service_account(api: Api, project: str, account_id: str, display_name: str) -> Result:
    base = f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts"
    email = service_account(project, account_id)
    code, body = api.call("GET", f"{base}/{email}")
    if code == 200:
        return Result(f"account {account_id}", "exists", email)
    if code != 404:
        return Result(f"account {account_id}", "failed", error_text(body))
    code, body = api.call("POST", base, json={"accountId": account_id, "serviceAccount": {"displayName": display_name}})
    if code == 409:
        return Result(f"account {account_id}", "exists", email)
    if code >= 400:
        return Result(f"account {account_id}", "failed", error_text(body))
    return Result(f"account {account_id}", "ok", email)


def deployer(api: Api) -> str:
    """The signed-in account as an IAM member, e.g. "user:someone@example.com"."""
    response = requests.post("https://oauth2.googleapis.com/tokeninfo", data={"access_token": api.token()}, timeout=30)
    email = response.json().get("email") if response.ok else None
    if not email:
        raise DeployError("Couldn't tell which account is signed in; run `gcloud auth application-default login`.")
    kind = "serviceAccount" if email.endswith(".gserviceaccount.com") else "user"
    return f"{kind}:{email}"


def allow_deploying_as(api: Api, email: str, member: str) -> Result:
    url = f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{email}:testIamPermissions"
    code, body = api.call("POST", url, json={"permissions": ["iam.serviceAccounts.actAs"]})
    if code == 200 and body.get("permissions"):
        return Result(f"deploy as {email.split('@')[0]}", "exists", f"{member} can already act as it")
    return grant(account_policy(api, email), {"roles/iam.serviceAccountUser": [member]})


def upload_bucket(settings: Mapping[str, str]) -> str:
    return settings.get("UPLOAD_BUCKET") or f"{settings['GOOGLE_CLOUD_PROJECT']}-uploads"


def catalog_bucket(settings: Mapping[str, str]) -> str:
    return settings.get("GCS_BUCKET") or f"{settings['GOOGLE_CLOUD_PROJECT']}-catalog"


def setup(api: Api, settings: Mapping[str, str]) -> list[Result]:
    project = settings["GOOGLE_CLOUD_PROJECT"]
    agent_account = service_account(project, AGENT)
    web_account = service_account(project, WEB)
    agent_member = f"serviceAccount:{agent_account}"
    me = deployer(api)
    return [
        enable_missing_services(api, project),
        create_repository(api, project),
        create_service_account(api, project, AGENT, "EcommAI agent API (Cloud Run)"),
        create_service_account(api, project, WEB, "EcommAI web app (Cloud Run, no roles)"),
        grant(project_policy(api, project), {role: [agent_member] for role in AGENT_ROLES}),
        grant(bucket_policy(api, upload_bucket(settings)), {"roles/storage.objectAdmin": [agent_member]}),
        grant(bucket_policy(api, catalog_bucket(settings)), {"roles/storage.objectViewer": [agent_member]}),
        allow_deploying_as(api, agent_account, me),
        allow_deploying_as(api, web_account, me),
    ]


# ---------- Images ----------


def docker(*args: str, env: Mapping[str, str] | None = None, stdin: str | None = None) -> None:
    subprocess.run(["docker", *args], env={**os.environ, **(env or {})}, input=stdin, text=True, check=True)


def docker_output(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout.strip()


def docker_host() -> str:
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    return docker_output("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")


def login_free_docker_config() -> dict:
    # An entry under "auths" stops Docker from saving the login in the system keychain instead of this folder.
    return {"auths": {REGISTRY: {}}}


def tag_exists(api: Api, project: str, name: str, tag: str) -> bool:
    url = (
        f"https://artifactregistry.googleapis.com/v1/projects/{project}/locations/{REGION}"
        f"/repositories/{REPOSITORY}/packages/{name}/tags/{tag}"
    )
    return api.call("GET", url)[0] == 200


def push(api: Api, image: str) -> None:
    """Push with a short-lived access token kept in .docker-config/ only while pushing."""
    DOCKER_CONFIG_DIR.mkdir(exist_ok=True)
    config = DOCKER_CONFIG_DIR / "config.json"
    env = {"DOCKER_CONFIG": str(DOCKER_CONFIG_DIR), "DOCKER_HOST": docker_host()}
    config.write_text(json.dumps(login_free_docker_config()))
    try:
        docker(
            "login", "--username", "oauth2accesstoken", "--password-stdin", f"https://{REGISTRY}",
            env=env, stdin=api.token(),
        )
        docker("push", image, env=env)
    finally:
        config.unlink(missing_ok=True)


def build_args(values: Mapping[str, str]) -> list[str]:
    return [flag for name, value in values.items() for flag in ("--build-arg", f"{name}={value}")]


def build_and_push(
    api: Api, project: str, name: str, dockerfile: Path, context: Path, args: Mapping[str, str] | None = None
) -> str:
    """Build for Cloud Run and push, tagged with the image ID so an unchanged build reuses the pushed image."""
    if not shutil.which("docker"):
        raise DeployError("Docker isn't available here. Run this on your laptop, or pass --image.")
    local = f"ecommai-{name}:deploy"
    docker(
        "build", "--platform", PLATFORM, "--provenance=false", "-f", str(dockerfile), "-t", local,
        *build_args(args or {}), str(context),
        env={"BUILDX_CONFIG": str(DOCKER_CONFIG_DIR / "buildx")},
    )
    tag = image_tag(docker_output("image", "inspect", "--format", "{{.Id}}", local))
    image = f"{image_name(project, name)}:{tag}"
    if tag_exists(api, project, name, tag):
        print(f"{image} is already in Artifact Registry")
        return image
    docker("tag", local, image)
    push(api, image)
    return image


# ---------- Cloud Run services ----------


def agent_env(settings: Mapping[str, str], origins: Sequence[str], base_url: str) -> dict[str, str]:
    """The .env settings the agent's code reads, never the dev sign-in switch, local-only settings or keys."""
    env = {
        name: value.strip()
        for name, value in settings.items()
        if value.strip()
        and not name.startswith(NEVER_DEPLOYED)
        and (name in AGENT_SETTINGS or name.startswith(AGENT_SETTING_PREFIXES))
    }
    return {**env, "ALLOWED_ORIGINS": ",".join(origins), "APP_BASE_URL": base_url}


def env_list(env: Mapping[str, str]) -> list[dict]:
    return [{"name": name, "value": value} for name, value in sorted(env.items())]


def service_env(service: Mapping[str, Any] | None) -> dict[str, str]:
    containers = (service or {}).get("template", {}).get("containers", [{}])
    return {item["name"]: item.get("value", "") for item in containers[0].get("env", [])}


def service_image(service: Mapping[str, Any]) -> str:
    return service["template"]["containers"][0]["image"]


def service_urls(service: Mapping[str, Any]) -> list[str]:
    return [service["uri"], *[url for url in service.get("urls", []) if url != service["uri"]]]


def agent_urls(current: Mapping[str, Any] | None, web_urls: Sequence[str]) -> tuple[list[str], str]:
    """(allowed origins, app base URL): the given web addresses, else what's deployed, else the local app."""
    if web_urls:
        urls = [url.rstrip("/") for url in web_urls]
        return urls, urls[0]
    env = service_env(current)
    if env.get("ALLOWED_ORIGINS") and env.get("APP_BASE_URL"):
        return env["ALLOWED_ORIGINS"].split(","), env["APP_BASE_URL"]
    return [LOCAL_WEB_URL], LOCAL_WEB_URL


def agent_service(project: str, image: str, env: Mapping[str, str]) -> dict:
    # CPU stays allocated between requests: checks that finish after the 8 s deadline are saved in the background.
    return {
        "ingress": "INGRESS_TRAFFIC_ALL",
        "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}],
        "template": {
            "serviceAccount": service_account(project, AGENT),
            "scaling": {"minInstanceCount": 1, "maxInstanceCount": 3},
            "timeout": "300s",
            "maxInstanceRequestConcurrency": 20,
            "containers": [
                {
                    "image": image,
                    "ports": [{"name": "http1", "containerPort": 8080}],
                    "env": env_list(env),
                    "resources": {"limits": {"cpu": "1", "memory": "1Gi"}, "cpuIdle": False, "startupCpuBoost": True},
                    "startupProbe": {
                        "httpGet": {"path": "/health"},
                        "periodSeconds": 2,
                        "timeoutSeconds": 2,
                        "failureThreshold": 30,
                    },
                }
            ],
        },
    }


def web_service(project: str, image: str, min_instances: int) -> dict:
    return {
        "ingress": "INGRESS_TRAFFIC_ALL",
        "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}],
        "template": {
            "serviceAccount": service_account(project, WEB),
            "scaling": {"minInstanceCount": min_instances, "maxInstanceCount": 3},
            "timeout": "60s",
            "maxInstanceRequestConcurrency": 80,
            "containers": [
                {
                    "image": image,
                    "ports": [{"name": "http1", "containerPort": 8080}],
                    "resources": {"limits": {"cpu": "1", "memory": "512Mi"}, "cpuIdle": True, "startupCpuBoost": True},
                }
            ],
        },
    }


def differs(current: Any, desired: Any) -> bool:
    """True unless `current` (as Cloud Run returns it) already has every value in `desired`.

    Cloud Run leaves out false, zero and empty values, so a missing value matches one of those.
    """
    if isinstance(desired, dict):
        current = current or {}
        return not isinstance(current, dict) or any(differs(current.get(key), value) for key, value in desired.items())
    if isinstance(desired, list):
        current = current or []
        return len(current) != len(desired) or any(
            differs(have, want) for have, want in zip(current, desired, strict=True)
        )
    return current != desired and not (current is None and not desired)


def get_service(api: Api, name: str) -> dict | None:
    code, body = api.call("GET", f"{RUN_API}/{name}")
    if code == 404:
        return None
    if code >= 400:
        raise DeployError(f"{name}: {error_text(body)}")
    return body


def is_ready(service: Mapping[str, Any]) -> bool:
    return (
        service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
        and service.get("latestReadyRevision") == service.get("latestCreatedRevision")
    )


def apply_service(api: Api, name: str, current: dict | None, body: dict) -> tuple[dict, bool]:
    """Create or update the service and wait until its newest revision serves. Returns (service, changed)."""
    if current is not None and not differs(current, body):
        return current, False
    parent, service_id = name.rsplit("/services/", 1)
    if current is None:
        code, operation = api.call("POST", f"{RUN_API}/{parent}/services", params={"serviceId": service_id}, json=body)
    else:
        code, operation = api.call("PATCH", f"{RUN_API}/{name}", json=body)
    if code >= 400:
        raise DeployError(f"{service_id}: {error_text(operation)}")
    done = api.wait(f"{RUN_API}/{operation['name']}", timeout_s=900)
    see_logs = f"See: python -m scripts.deploy logs --service {service_id}"
    if "error" in done:
        raise DeployError(f"{service_id}: {error_text(done)}. {see_logs}")
    service = get_service(api, name)
    if service is None or not is_ready(service):
        raise DeployError(f"{service_id} isn't ready. {see_logs}")
    return service, True


def deploy_agent(api: Api, settings: Mapping[str, str], image: str | None, web_urls: Sequence[str]) -> dict:
    project = settings["GOOGLE_CLOUD_PROJECT"]
    name = service_path(project, AGENT)
    current = get_service(api, name)
    image = image or build_and_push(api, project, "agent", ROOT / "agent" / "Dockerfile", ROOT)
    origins, base_url = agent_urls(current, web_urls)
    body = agent_service(project, image, agent_env(settings, origins, base_url))
    service, changed = apply_service(api, name, current, body)
    print(f"{'DEPLOYED' if changed else 'SAME':8} {AGENT:22} {service['latestReadyRevision'].rsplit('/', 1)[-1]}")
    report([grant(run_policy(api, name), {"roles/run.invoker": ["allUsers"]})])
    return service


# ---------- The web app ----------


def missing_web_files(web: Path = ROOT / "web") -> list[str]:
    missing = [f"web/{name}" for name in ("Dockerfile", ".dockerignore") if not (web / name).exists()]
    if '"standalone"' not in (web / "next.config.ts").read_text():
        missing.append('output: "standalone" in web/next.config.ts')
    return missing


def web_build_args(web_env: Mapping[str, str | None], api_url: str) -> dict[str, str]:
    """NEXT_PUBLIC_* values baked into the app: Firebase from web/.env.local and the deployed agent, never mock data."""
    values = {name: (web_env.get(name) or "").strip() for name in FIREBASE_SETTINGS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise DeployError(f"Missing in the web settings: {', '.join(missing)}")
    return {**values, "NEXT_PUBLIC_API_BASE_URL": api_url.rstrip("/")}


def with_domains(current: Sequence[str], hosts: Sequence[str]) -> list[str] | None:
    missing = [host for host in dict.fromkeys(hosts) if host not in current]
    return [*current, *missing] if missing else None


def add_authorized_domains(api: Api, project: str, hosts: Sequence[str]) -> Result:
    url = f"https://identitytoolkit.googleapis.com/admin/v2/projects/{project}/config"
    code, config = api.call("GET", url)
    if code >= 400:
        return Result("sign-in domains", "failed", error_text(config))
    domains = with_domains(config.get("authorizedDomains", []), hosts)
    if domains is None:
        return Result("sign-in domains", "exists", ", ".join(hosts))
    code, body = api.call("PATCH", url, params={"updateMask": "authorizedDomains"}, json={"authorizedDomains": domains})
    if code >= 400:
        return Result("sign-in domains", "failed", error_text(body))
    return Result("sign-in domains", "ok", ", ".join(hosts))


def deploy_web(api: Api, settings: Mapping[str, str], web_env: Path, min_instances: int) -> dict:
    project = settings["GOOGLE_CLOUD_PROJECT"]
    agent = get_service(api, service_path(project, AGENT))
    if agent is None:
        raise DeployError("Deploy the agent first: python -m scripts.deploy agent")
    missing = missing_web_files()
    if missing:
        raise DeployError(f"The web app isn't ready for Cloud Run yet; add {', '.join(missing)}")
    args = web_build_args(dotenv_values(web_env), agent["uri"])
    image = build_and_push(api, project, "web", ROOT / "web" / "Dockerfile", ROOT / "web", args)
    name = service_path(project, WEB)
    service, changed = apply_service(api, name, get_service(api, name), web_service(project, image, min_instances))
    print(f"{'DEPLOYED' if changed else 'SAME':8} {WEB:22} {service['latestReadyRevision'].rsplit('/', 1)[-1]}")
    urls = service_urls(service)
    report([
        grant(run_policy(api, name), {"roles/run.invoker": ["allUsers"]}),
        add_authorized_domains(api, project, [urlparse(url).hostname or "" for url in urls]),
    ])
    deploy_agent(api, settings, service_image(agent), urls)
    return service


# ---------- status and logs ----------


def describe(service: Mapping[str, Any]) -> list[str]:
    template = service["template"]
    container = template["containers"][0]
    scaling = template.get("scaling", {})
    resources = container.get("resources", {})
    limits = resources.get("limits", {})
    cpu = "only during requests" if resources.get("cpuIdle") else "always allocated"
    return [
        f"  url        {service.get('uri')}",
        f"  revision   {service.get('latestReadyRevision', '-').rsplit('/', 1)[-1]}",
        f"  image      {container.get('image')}",
        f"  instances  min {scaling.get('minInstanceCount', 0)}, max {scaling.get('maxInstanceCount', '-')}",
        f"  resources  {limits.get('cpu')} vCPU, {limits.get('memory')}, CPU {cpu}",
        f"  requests   timeout {template.get('timeout')}, {template.get('maxInstanceRequestConcurrency')} at once",
        f"  env        {', '.join(sorted(service_env(service))) or '-'}",
    ]


def status(api: Api, project: str) -> None:
    for name in (AGENT, WEB):
        service = get_service(api, service_path(project, name))
        print(name)
        print("\n".join(describe(service)) if service else "  not deployed")


def log_line(entry: Mapping[str, Any]) -> str:
    request = entry.get("httpRequest") or {}
    audit = entry.get("protoPayload") or {}
    text = entry.get("textPayload") or entry.get("jsonPayload", {}).get("message") or ""
    if not text and request:
        text = " ".join(str(request.get(key, "")) for key in ("requestMethod", "requestUrl", "status", "latency"))
    if not text and audit:
        text = f"{audit.get('methodName', '')} {audit.get('status', {}).get('message', '')}"
    if not text:
        text = json.dumps(entry.get("jsonPayload") or {})
    revision = entry.get("resource", {}).get("labels", {}).get("revision_name", "")
    return f"{entry.get('timestamp', '')[:19]} {entry.get('severity', 'DEFAULT'):8} {revision} {text.strip()[:600]}"


def logs(api: Api, project: str, service: str, minutes: int, severity: str, limit: int = 100) -> list[str]:
    since = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    query = (
        f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" '
        f'AND severity>={severity} AND timestamp>="{since}"'
    )
    code, body = api.call(
        "POST", "https://logging.googleapis.com/v2/entries:list",
        json={"resourceNames": [f"projects/{project}"], "filter": query, "orderBy": "timestamp desc",
              "pageSize": limit},
    )
    if code >= 400:
        raise DeployError(error_text(body))
    return [log_line(entry) for entry in reversed(body.get("entries", []))]


# ---------- Command line ----------


def report(results: list[Result]) -> None:
    for r in results:
        print(f"{r.status.upper():8} {r.step:22} {r.detail}")
    if any(r.status in ("failed", "missing") for r in results):
        raise DeployError("Some steps failed; see above.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Artifact Registry repo, service accounts and their roles")
    agent = commands.add_parser("agent", help="build, push and deploy the agent API")
    agent.add_argument("--image", help="deploy this pushed image instead of building one")
    agent.add_argument(
        "--web-url", action="append", default=[],
        help="the web app's address (repeat to allow more origins); default: keep what's deployed, "
        f"or {LOCAL_WEB_URL} the first time",
    )
    web = commands.add_parser("web", help="build and deploy the web app, then point the agent at it")
    web.add_argument("--web-env", type=Path, default=ROOT / "web" / ".env.local", help="the Firebase web settings")
    web.add_argument("--min-instances", type=int, default=0)
    commands.add_parser("status", help="both services' address, revision, image and settings")
    log = commands.add_parser("logs", help="recent Cloud Run logs")
    log.add_argument("--service", choices=[AGENT, WEB], default=AGENT)
    log.add_argument("--minutes", type=int, default=60)
    log.add_argument("--severity", default="WARNING", help="lowest severity to show, e.g. DEFAULT, INFO, ERROR")
    args = parser.parse_args()

    settings = read_settings()
    project = settings.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit("Set GOOGLE_CLOUD_PROJECT in .env first.")
    api = Api(project)
    started = time.monotonic()
    try:
        if args.command == "setup":
            report(setup(api, settings))
        elif args.command == "agent":
            service = deploy_agent(api, settings, args.image, args.web_url)
            print(f"\nAgent: {service['uri']}  ({time.monotonic() - started:.0f} s)")
            print(f"Next: scripts/smoke_test.sh {service['uri']}")
        elif args.command == "web":
            service = deploy_web(api, settings, args.web_env, args.min_instances)
            print(f"\nWeb app: {service['uri']}  ({time.monotonic() - started:.0f} s)")
        elif args.command == "status":
            status(api, project)
        elif args.command == "logs":
            print("\n".join(logs(api, project, args.service, args.minutes, args.severity)) or "no entries")
    except (DeployError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
