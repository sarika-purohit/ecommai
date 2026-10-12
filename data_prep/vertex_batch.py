"""Vertex AI batch prediction for Gemini, through Cloud Storage.

One JSONL line per item, in the format Agent Platform documents for batch inference
(https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/batch-prediction-from-cloud-storage):

    {"key": "<stable key>", "request": {"contents": [...], "systemInstruction": {...},
        "generationConfig": {...}}}

`request` is the same body a normal generateContent call sends, so a photo is a
`fileData.fileUri` of `gs://...`. The `key` is ours: the service echoes the request, and
sometimes the key, and we map every result line back to it. Reviews can build lines with
`request_line`. Embeddings use a different batch method (`batches.create_embeddings`) and a
different output shape; they can reuse `upload_jsonl`, the state file and `poll_job`, and
parse their own output.

A job is recorded in `data/batch/<name>.json`. Running the same name again reattaches to a
job that is still going, or downloads a finished one, instead of submitting a second job.

Gemini publisher models, including gemini-3.8-flash, accept batch jobs on the `global`
location. gemini-3.8-flash is not on `us-central1` (the create call returns 404). Tuned
models cannot use `global`. A response schema that compiles past the batch grammar limit
(height about 5888, or 64KB of regex) fails every line; leave the schema off and validate
the JSON yourself, which is what enrichment does.

    python -c "from data_prep.vertex_batch import request_line"
"""

import hashlib
import json
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from google import genai
from google.genai import types
from google.genai.models import _GenerateContentParameters_to_vertex, _GenerateContentResponse_from_vertex
from pydantic import BaseModel

DEFAULT_LOCATION = "global"
DEFAULT_BUCKET = "ecommai-511419-catalog"
STATE_DIR = Path("data/batch")
OBJECT_ADMIN = "roles/storage.objectAdmin"
RUNNING = {"JOB_STATE_QUEUED", "JOB_STATE_PENDING", "JOB_STATE_RUNNING", "JOB_STATE_UPDATING", "JOB_STATE_CANCELLING"}
SUCCEEDED = {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"}
FAILED = {"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED", "JOB_STATE_PAUSED"}

Log = Callable[[str], None]


class Jobs(Protocol):
    def create(self, *, model: str, src: str, config: Any) -> Any: ...

    def get(self, *, name: str) -> Any: ...


@dataclass
class BatchItem:
    key: str
    text: str | None = None
    error: str = ""
    prompt_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0


@dataclass
class JobRecord:
    name: str
    resource: str
    location: str
    model: str
    input_uri: str
    output_uri: str
    keys: list[str]
    signatures: dict[str, str] = field(default_factory=dict)
    created: str = ""
    state: str = ""
    previous: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return self.__dict__

    @classmethod
    def from_json(cls, data: dict) -> "JobRecord":
        known = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        return cls(**known)


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump(mode="json", by_alias=True, exclude_none=True))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _api_client(project: str, location: str) -> Any:
    return genai.Client(vertexai=True, project=project or "unspecified", location=location)._api_client


def request_line(
    key: str,
    contents: Any,
    config: types.GenerateContentConfig | None = None,
    *,
    model: str,
    project: str = "",
    location: str = DEFAULT_LOCATION,
) -> dict:
    """One batch input line for a Gemini generateContent request."""
    if not key or not str(key).strip():
        raise ValueError("a batch line needs a non-empty key")
    params = types._GenerateContentParameters(model=model, contents=contents, config=config)
    body = _GenerateContentParameters_to_vertex(_api_client(project, location), params)
    request = jsonable({name: value for name, value in body.items() if name != "_url"})
    return {"key": str(key), "request": request}


def request_signature(request: dict) -> str:
    """Stable id for an echoed request, so a result line can be matched after its key is dropped."""
    texts: list[str] = []
    uris: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
            file_data = node.get("fileData") or node.get("file_data") or {}
            if isinstance(file_data, dict):
                uri = file_data.get("fileUri") or file_data.get("file_uri")
                if isinstance(uri, str):
                    uris.append(uri)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(request)
    digest = hashlib.sha256("\n".join([*uris, *texts]).encode()).hexdigest()
    return digest


def write_jsonl(lines: Sequence[dict], path: Path) -> None:
    keys = [line.get("key") for line in lines]
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("every batch line needs a string key")
    if len(keys) != len(set(keys)):
        raise ValueError("batch line keys must be unique")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")


def state_path(state_dir: Path, name: str) -> Path:
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"job name {name!r} must be a plain file name")
    return state_dir / f"{name}.json"


def load_state(state_dir: Path, name: str) -> JobRecord | None:
    path = state_path(state_dir, name)
    if not path.exists():
        return None
    return JobRecord.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_state(state_dir: Path, record: JobRecord) -> None:
    path = state_path(state_dir, record.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_json(), indent=2) + "\n", encoding="utf-8")


def job_state(job: Any) -> str:
    state = getattr(job, "state", "")
    return str(getattr(state, "value", state) or "")


def job_error(job: Any) -> str:
    error = getattr(job, "error", None)
    if not error:
        return ""
    return str(getattr(error, "message", None) or error)


def _count(job: Any, name: str) -> int:
    stats = getattr(job, "completion_stats", None)
    value = getattr(stats, name, None) if stats else None
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def progress(job: Any) -> str:
    done = _count(job, "successful_count") + _count(job, "failed_count")
    incomplete = _count(job, "incomplete_count")
    return f"{job_state(job)}, {done} finished ({_count(job, 'failed_count')} failed), {incomplete} still going"


def gcs_name(uri: str, bucket: str) -> str:
    prefix = f"gs://{bucket}/"
    if not uri.startswith(prefix):
        raise ValueError(f"{uri} is not in gs://{bucket}/")
    return uri[len(prefix):]


def upload_jsonl(uri: str, path: Path, *, project: str) -> None:
    from google.cloud import storage

    bucket_name, _, blob_name = uri.removeprefix("gs://").partition("/")
    storage.Client(project=project).bucket(bucket_name).blob(blob_name).upload_from_filename(path)


def download_jsonl(uri: str, *, project: str) -> list[str]:
    """Every JSONL line written under a gs:// prefix."""
    from google.cloud import storage

    bucket_name, _, prefix = uri.removeprefix("gs://").partition("/")
    lines = []
    for blob in storage.Client(project=project).list_blobs(bucket_name, prefix=prefix):
        if blob.name.endswith(".jsonl"):
            lines += blob.download_as_text(encoding="utf-8").splitlines()
    return lines


def _usage(metadata: Any) -> tuple[int, int, int, int]:
    if metadata is None:
        return 0, 0, 0, 0
    return (
        getattr(metadata, "prompt_token_count", None) or 0,
        getattr(metadata, "cached_content_token_count", None) or 0,
        getattr(metadata, "candidates_token_count", None) or 0,
        getattr(metadata, "thoughts_token_count", None) or 0,
    )


def short_status(status: str) -> str:
    match = re.search(r"Constraint is too tall: \d+ \(vs max of \d+\)", status)
    if match:
        return match.group(0)
    text = " ".join(status.split())
    return text if len(text) <= 300 else text[:297] + "..."


def parse_output_line(line: dict, signatures: dict[str, str]) -> BatchItem | None:
    """One result line. None when the line has no key and doesn't match a submitted request."""
    request = line.get("request") or line.get("instance") or {}
    key = line.get("key") or line.get("custom_id") or line.get("customId")
    if not isinstance(key, str) or not key:
        key = signatures.get(request_signature(request)) if isinstance(request, dict) else None
    if not key:
        return None
    status = str(line.get("status") or "").strip()
    response = line.get("response") if isinstance(line.get("response"), dict) else line.get("prediction")
    text, prompt, cached, output, thinking = None, 0, 0, 0, 0
    if isinstance(response, dict) and response:
        try:
            parsed = types.GenerateContentResponse.model_validate(_GenerateContentResponse_from_vertex(response))
        except Exception as error:
            return BatchItem(key, error=f"could not read the response: {error}")
        text = (parsed.text or "").strip() or None
        prompt, cached, output, thinking = _usage(parsed.usage_metadata)
    if status or not text:
        return BatchItem(key, text, short_status(status) or "empty answer", prompt, cached, output, thinking)
    return BatchItem(key, text, "", prompt, cached, output, thinking)


def parse_output(lines: Iterable[str], signatures: dict[str, str]) -> dict[str, BatchItem]:
    found: dict[str, BatchItem] = {}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(line, dict):
            continue
        item = parse_output_line(line, signatures)
        if item is not None:
            found[item.key] = item
    return found


def iam_command(bucket: str, member: str) -> str:
    return (
        f"gcloud storage buckets add-iam-policy-binding gs://{bucket} "
        f"--member={member} --role={OBJECT_ADMIN}"
    )


def service_agent(project: str, session: Any) -> str:
    response = session.get(f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}", timeout=60)
    if response.status_code != 200:
        raise RuntimeError(f"could not read project number for {project}: HTTP {response.status_code}")
    number = response.json().get("projectNumber")
    return f"serviceAccount:service-{number}@gcp-sa-aiplatform.iam.gserviceaccount.com"


def ensure_bucket_access(bucket: str, project: str, session: Any | None = None) -> str:
    """Give the Vertex AI service agent objectAdmin on the bucket. Returns what changed."""
    if session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession, Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"], quota_project_id=project
        )
        credentials.refresh(Request())
        session = AuthorizedSession(credentials)
    member = service_agent(project, session)
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam"
    response = session.get(url, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(
            f"could not read IAM for gs://{bucket}: HTTP {response.status_code} {response.text}\n"
            + iam_command(bucket, member)
        )
    policy = response.json()
    for binding in policy.get("bindings", []):
        if binding.get("role") == OBJECT_ADMIN and member in binding.get("members", []):
            return f"{member} already has {OBJECT_ADMIN} on gs://{bucket}"
    bindings = policy.setdefault("bindings", [])
    existing = next((binding for binding in bindings if binding.get("role") == OBJECT_ADMIN), None)
    if existing:
        existing.setdefault("members", []).append(member)
    else:
        bindings.append({"role": OBJECT_ADMIN, "members": [member]})
    updated = session.put(url, json={"bindings": bindings, "etag": policy.get("etag")}, timeout=60)
    if updated.status_code != 200:
        raise RuntimeError(
            f"could not grant {OBJECT_ADMIN} to {member}: HTTP {updated.status_code} {updated.text}\n"
            + iam_command(bucket, member)
        )
    return f"Granted {OBJECT_ADMIN} on gs://{bucket} to {member}"


def open_client(project: str, location: str) -> genai.Client:
    return genai.Client(vertexai=True, project=project, location=location)


def location_rejected(error: Exception) -> bool:
    text = str(error).lower()
    return any(phrase in text for phrase in ("not found", "not supported", "unsupported", "was not found", "invalid location"))


def _uris(name: str, bucket: str) -> tuple[str, str]:
    base = f"gs://{bucket}/batch/{name}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # A new folder per submission, so a replacement job is not mixed with an older result file.
    return f"{base}/input.jsonl", f"{base}/{stamp}/"


def _submit(
    name: str,
    lines: Sequence[dict],
    *,
    model: str,
    bucket: str,
    project: str,
    location: str,
    jobs: Jobs,
    upload: Callable[[str, Path], None],
) -> JobRecord:
    input_uri, output_uri = _uris(name, bucket)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.jsonl"
        write_jsonl(lines, path)
        upload(input_uri, path)
    job = jobs.create(model=model, src=input_uri, config=types.CreateBatchJobConfig(display_name=name, dest=output_uri))
    return JobRecord(
        name=name,
        resource=str(job.name),
        location=location,
        model=model,
        input_uri=input_uri,
        output_uri=output_uri,
        keys=[line["key"] for line in lines],
        signatures={request_signature(line["request"]): line["key"] for line in lines},
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        state=job_state(job),
    )


def requests_unchanged(record: JobRecord, lines: Sequence[dict]) -> bool:
    """True when every requested line still matches the body stored for that key."""
    if not record.signatures:
        return True
    by_key = {key: signature for signature, key in record.signatures.items()}
    return all(by_key.get(line["key"]) == request_signature(line["request"]) for line in lines)


def poll_job(jobs: Jobs, resource: str, *, poll_seconds: float, sleep: Callable[[float], None], log: Log) -> Any:
    while True:
        job = jobs.get(name=resource)
        state = job_state(job)
        log(f"  {progress(job)}")
        if state in SUCCEEDED or state in FAILED:
            return job
        sleep(poll_seconds)


def run_job(
    name: str,
    lines: Sequence[dict],
    *,
    model: str,
    bucket: str = DEFAULT_BUCKET,
    project: str,
    location: str = DEFAULT_LOCATION,
    state_dir: Path = STATE_DIR,
    poll_seconds: float = 60,
    jobs: Jobs | None = None,
    upload: Callable[[str, Path], None] | None = None,
    download: Callable[[str], list[str]] | None = None,
    grant_access: Callable[[], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Log = print,
) -> dict[str, BatchItem]:
    """Submit `lines` (or reattach) and return `{key: BatchItem}`.

    A line missing from the result was not returned by the job. `BatchItem.error` is set when the
    line came back but the model failed or the answer was empty.
    """
    if not lines and load_state(state_dir, name) is None:
        return {}
    # The client has to stay referenced for the whole job: dropping it closes its HTTP session.
    clients: list[genai.Client] = []
    if jobs is None:
        clients.append(open_client(project, location))
        jobs = clients[-1].batches
    upload = upload or (lambda uri, path: upload_jsonl(uri, path, project=project))
    download = download or (lambda uri: download_jsonl(uri, project=project))
    record = load_state(state_dir, name)
    requested = {line["key"] for line in lines}
    reattach = False
    earlier: list[str] = []
    if record and record.resource:
        job = jobs.get(name=record.resource)
        state = job_state(job)
        covered = not requested or requested <= set(record.keys)
        same = requests_unchanged(record, lines)
        if state in RUNNING or (state in SUCCEEDED and covered and same):
            reattach = True
            log(f"Reattaching to {record.resource} ({state}, {len(record.keys)} requests)")
        elif state in FAILED or (state in SUCCEEDED and not (covered and same)):
            if state in FAILED:
                why = job_error(job) or "the job failed"
            elif not same:
                why = "the requests changed"
            else:
                why = "that job was for a different set of keys"
            log(f"Not reusing {record.resource} ({state}: {why}). Submitting a new job.")
            earlier = [*record.previous, record.resource]
        else:
            reattach = True
            log(f"Reattaching to {record.resource} ({state})")
    if not reattach:
        if not lines:
            return {}
        if grant_access is not None:
            log(grant_access())
        record = None
        last_error: Exception | None = None
        for candidate in (location, "us-central1") if location == "global" else (location,):
            try:
                if candidate == location:
                    active = jobs
                else:
                    clients.append(open_client(project, candidate))
                    active = clients[-1].batches
                record = _submit(
                    name, lines, model=model, bucket=bucket, project=project, location=candidate,
                    jobs=active, upload=upload,
                )
                jobs = active
                location = candidate
                break
            except Exception as error:
                last_error = error
                if candidate == "global" and location_rejected(error):
                    log(f"global rejected the batch job ({error}). Trying us-central1.")
                    continue
                raise
        if record is None:
            raise last_error or RuntimeError("the batch job was not submitted")
        record.previous = earlier
        log(f"Submitted {record.resource} ({len(lines)} requests) -> {record.output_uri}")
        save_state(state_dir, record)
    assert record is not None
    job = poll_job(jobs, record.resource, poll_seconds=poll_seconds, sleep=sleep, log=log)
    record.state = job_state(job)
    save_state(state_dir, record)
    if record.state not in SUCCEEDED:
        detail = job_error(job)
        hint = ""
        if "permission" in detail.lower() or "403" in detail or "storage.objects" in detail:
            hint = "\n" + iam_command(bucket, "serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-aiplatform.iam.gserviceaccount.com")
        raise RuntimeError(f"batch job {record.resource} ended {record.state}: {detail}{hint}")
    output: list[str] = []
    for attempt in range(1, 6):
        output = download(record.output_uri)
        if output:
            break
        log(f"  no output yet under {record.output_uri} (try {attempt})")
        sleep(poll_seconds)
    log(f"Downloaded {len(output)} result lines from {record.output_uri}")
    found = parse_output(output, record.signatures)
    for key in record.keys:
        found.setdefault(key, BatchItem(key, error="no result line"))
    if requested:
        return {key: found[key] for key in requested if key in found}
    return found
