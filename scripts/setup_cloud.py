"""One-time Google Cloud and Firebase setup (IMPLEMENTATION.md step 0.1).

Safe to run again: anything that already exists is left as it is. Uses your
Application Default Credentials (`gcloud auth application-default login`).

    docker run --rm --env-file .env -v "$PWD":/app \
        -v "$HOME/.config/gcloud":/root/.config/gcloud:ro \
        ecommai-dev python -m scripts.setup_cloud --web-env web/.env.local
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import google.auth
from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession

# These don't need billing, and the billing check itself needs cloudbilling.
BASE_SERVICES = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudbilling.googleapis.com",
    "firebase.googleapis.com",
    "identitytoolkit.googleapis.com",
]
SERVICES = [
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "modelarmor.googleapis.com",
    "bigquery.googleapis.com",
    "firebaseapphosting.googleapis.com",
]


@dataclass
class Result:
    step: str
    status: str  # ok, exists, failed, missing
    detail: str = ""


class Api:
    def __init__(self, project: str):
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session = AuthorizedSession(credentials.with_quota_project(project))

    def call(self, method: str, url: str, **kwargs) -> tuple[int, dict]:
        response = self.session.request(method, url, timeout=60, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body

    def wait(self, url: str, timeout_s: int = 300) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            _, operation = self.call("GET", url)
            if operation.get("done"):
                return operation
            time.sleep(3)
        return {"error": {"message": f"timed out waiting for {url}"}}


def error_text(body: dict) -> str:
    error = body.get("error", {})
    return error.get("message", str(body))[:300] if isinstance(error, dict) else str(error)[:300]


def enable_services(api: Api, project: str, services: list[str]) -> Result:
    url = f"https://serviceusage.googleapis.com/v1/projects/{project}/services:batchEnable"
    code, body = api.call("POST", url, json={"serviceIds": services})
    if code >= 400:
        return Result("enable services", "failed", error_text(body))
    if not body.get("done"):
        body = api.wait(f"https://serviceusage.googleapis.com/v1/{body['name']}")
    if "error" in body:
        return Result("enable services", "failed", error_text(body))
    return Result("enable services", "ok", f"{len(services)} services")


def check_billing(api: Api, project: str) -> Result:
    code, body = api.call("GET", f"https://cloudbilling.googleapis.com/v1/projects/{project}/billingInfo")
    if code >= 400:
        return Result("billing", "failed", error_text(body))
    if not body.get("billingEnabled"):
        return Result("billing", "missing", "no billing account linked; link one under Billing in the Cloud console")
    return Result("billing", "ok", body.get("billingAccountName", ""))


def create_firestore(api: Api, project: str, location: str) -> Result:
    base = f"https://firestore.googleapis.com/v1/projects/{project}/databases"
    code, body = api.call("GET", f"{base}/(default)")
    if code == 200:
        found = body.get("locationId")
        status = "exists" if found == location else "failed"
        return Result("firestore", status, f"(default) in {found}, type {body.get('type')}")
    code, body = api.call(
        "POST", f"{base}?databaseId=(default)", json={"locationId": location, "type": "FIRESTORE_NATIVE"}
    )
    if code >= 400:
        return Result("firestore", "failed", error_text(body))
    return Result("firestore", "ok", f"(default) in {location}")


def create_bucket(api: Api, project: str, bucket: str, location: str) -> Result:
    code, body = api.call("GET", f"https://storage.googleapis.com/storage/v1/b/{bucket}")
    if code == 200:
        return Result("photo bucket", "exists", f"gs://{bucket} in {body.get('location')}")
    code, body = api.call(
        "POST",
        f"https://storage.googleapis.com/storage/v1/b?project={project}",
        json={
            "name": bucket,
            "location": location.upper(),
            "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}},
        },
    )
    if code >= 400:
        return Result("photo bucket", "failed", error_text(body))
    return Result("photo bucket", "ok", f"gs://{bucket}")


def make_bucket_public(api: Api, bucket: str) -> Result:
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam"
    code, policy = api.call("GET", url)
    if code >= 400:
        return Result("public photo links", "failed", error_text(policy))
    binding = {"role": "roles/storage.objectViewer", "members": ["allUsers"]}
    if any(b["role"] == binding["role"] and "allUsers" in b.get("members", []) for b in policy.get("bindings", [])):
        return Result("public photo links", "exists")
    policy.setdefault("bindings", []).append(binding)
    code, body = api.call("PUT", url, json=policy)
    if code >= 400:
        return Result("public photo links", "failed", error_text(body))
    return Result("public photo links", "ok", "anyone can view photos; nobody else can change them")


def create_dataset(api: Api, project: str, dataset: str, location: str) -> Result:
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets"
    code, _ = api.call("GET", f"{base}/{dataset}")
    if code == 200:
        return Result("bigquery dataset", "exists", dataset)
    code, body = api.call(
        "POST", base, json={"datasetReference": {"projectId": project, "datasetId": dataset}, "location": location}
    )
    if code >= 400:
        return Result("bigquery dataset", "failed", error_text(body))
    return Result("bigquery dataset", "ok", f"{dataset} in {location}")


def create_armor_template(api: Api, project: str, location: str, template: str) -> Result:
    base = f"https://modelarmor.{location}.rep.googleapis.com/v1/projects/{project}/locations/{location}/templates"
    code, _ = api.call("GET", f"{base}/{template}")
    if code == 200:
        return Result("model armor template", "exists", template)
    # MEDIUM_AND_ABOVE missed gentle review tricks; LOW_AND_ABOVE caught them without flagging normal reviews.
    filters = {"piAndJailbreakFilterSettings": {"filterEnforcement": "ENABLED", "confidenceLevel": "LOW_AND_ABOVE"}}
    code, body = api.call("POST", f"{base}?templateId={template}", json={"filterConfig": filters})
    if code >= 400:
        return Result("model armor template", "failed", error_text(body))
    return Result("model armor template", "ok", f"{template} in {location}")


def check_google_sign_in(api: Api, project: str) -> Result:
    url = f"https://identitytoolkit.googleapis.com/admin/v2/projects/{project}/defaultSupportedIdpConfigs/google.com"
    code, body = api.call("GET", url)
    if code == 200 and body.get("enabled"):
        return Result("google sign-in", "ok")
    if code in (200, 404):
        return Result("google sign-in", "missing", "turn on Google under Authentication > Sign-in method in Firebase")
    return Result("google sign-in", "failed", error_text(body))


def firebase_web_config(api: Api, project: str) -> tuple[Result, dict]:
    base = f"https://firebase.googleapis.com/v1beta1/projects/{project}"
    code, body = api.call("GET", f"{base}/webApps")
    if code >= 400:
        return Result("firebase web app", "failed", error_text(body)), {}
    apps = body.get("apps", [])
    status = "exists"
    if not apps:
        code, operation = api.call("POST", f"{base}/webApps", json={"displayName": "EcommAI web"})
        if code >= 400:
            return Result("firebase web app", "failed", error_text(operation)), {}
        done = api.wait(f"https://firebase.googleapis.com/v1beta1/{operation['name']}")
        if "error" in done:
            return Result("firebase web app", "failed", error_text(done)), {}
        apps = [done["response"]]
        status = "ok"
    app_id = apps[0]["appId"]
    code, config = api.call("GET", f"{base}/webApps/{app_id}/config")
    if code >= 400:
        return Result("firebase web app", "failed", error_text(config)), {}
    return Result("firebase web app", status, app_id), config


def write_web_env(path: Path, config: dict) -> None:
    values = {
        "NEXT_PUBLIC_FIREBASE_API_KEY": config.get("apiKey", ""),
        "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN": config.get("authDomain", ""),
        "NEXT_PUBLIC_FIREBASE_PROJECT_ID": config.get("projectId", ""),
        "NEXT_PUBLIC_FIREBASE_APP_ID": config.get("appId", ""),
    }
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0] not in values]
    path.write_text("\n".join(kept + [f"{k}={v}" for k, v in values.items()]) + "\n")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--web-env", type=Path, help="write the Firebase web config to this file")
    args = parser.parse_args()

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit("Set GOOGLE_CLOUD_PROJECT in .env first.")
    region = os.environ.get("FIRESTORE_LOCATION", "asia-south1")
    bucket = os.environ.get("GCS_BUCKET") or f"{project}-catalog"
    dataset = os.environ.get("BIGQUERY_DATASET", "ecommai")
    armor_location = os.environ.get("MODEL_ARMOR_LOCATION", region)
    armor_template = os.environ.get("MODEL_ARMOR_TEMPLATE") or "ecommai-armor"

    api = Api(project)
    results = [enable_services(api, project, BASE_SERVICES), check_billing(api, project)]
    if results[-1].status == "ok":
        results += [
            enable_services(api, project, SERVICES),
            create_firestore(api, project, region),
            create_bucket(api, project, bucket, region),
            make_bucket_public(api, bucket),
            create_dataset(api, project, dataset, region),
            create_armor_template(api, project, armor_location, armor_template),
        ]
    results.append(check_google_sign_in(api, project))
    web_app, config = firebase_web_config(api, project)
    results.append(web_app)
    if config and args.web_env:
        write_web_env(args.web_env, config)
        results.append(Result("web app settings", "ok", f"written to {args.web_env}"))

    for r in results:
        print(f"{r.status.upper():8} {r.step:22} {r.detail}")
    if any(r.status in ("failed", "missing") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
