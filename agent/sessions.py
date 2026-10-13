"""Saved state: shoppers and searches in Firestore (PLAN.md appendix F), uploaded photos in Cloud Storage.

users/{uid}       name, email, pincode, language, rules, taste, likes, dislikes
sessions/{id}     user_id, brief, the saved rules it was made with, rules switched off for it, history, results,
                  feedback, and the shopper's photo until the search has embedded it
uploads/{uid}/... the shopper's photos, in a bucket that isn't public; deleted once the search has used them

Name and email are kept only in users/{uid}, and nothing here logs them or the uid.
"""

import os
import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from functools import cache
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.vector import Vector
from pydantic import BaseModel, Field

from agent.learn import TasteProfile, TasteVector
from agent.models import MustHave, Product, ShoppingBrief
from agent.verify import PRODUCT_FIELDS

USERS = "users"
SESSIONS = "sessions"
PRODUCTS = "products"
UPLOADS = "uploads"
VECTOR_FIELD = "embedding"
DELETE_BATCH = 400
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
GS_LINK = re.compile(r"^gs://(?P<bucket>[^/]+)/(?P<name>.+)$")


class UserDoc(BaseModel):
    name: str = ""
    email: str = ""
    pincode: str | None = None
    language: str | None = None
    rules: list[MustHave] = Field(default_factory=list)
    taste: dict[str, TasteVector] = Field(default_factory=dict)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)


class SessionDoc(BaseModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    query_text: str = Field(default="", description="The shopper's words, for the meaning search")
    brief: ShoppingBrief
    rules: list[MustHave] = Field(default_factory=list, description="The saved rules this search was made with")
    disabled_rules: list[str] = Field(default_factory=list, description="key() of saved rules switched off")
    photo: str | None = Field(default=None, description="gs:// upload or https link, until the search embeds it")
    photo_vector: list[float] | None = None
    history: list[dict] = Field(default_factory=list)
    results: dict | None = None
    feedback: list[dict] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _plain_vector(value: Any) -> list[float] | None:
    return None if value is None else [float(v) for v in value]


def _session_fields(session: SessionDoc, names: Sequence[str]) -> dict[str, Any]:
    data = session.model_dump(mode="json", include=set(names) - {"created_at", "updated_at", "photo_vector"})
    for name in names:
        if name in ("created_at", "updated_at"):
            data[name] = getattr(session, name)
        elif name == "photo_vector":
            data[name] = Vector(session.photo_vector) if session.photo_vector else None
    return data


def _taste_to_firestore(profile: TasteProfile) -> dict:
    return {kind: {"vector": Vector(taste.vector), "count": taste.count} for kind, taste in profile.items()}


def _user_from_firestore(data: dict) -> UserDoc:
    taste = {
        kind: {"vector": _plain_vector(item.get("vector")), "count": item.get("count")}
        for kind, item in (data.get("taste") or {}).items()
        if isinstance(item, dict)
    }
    return UserDoc.model_validate({**data, "taste": taste})


class Store:
    def __init__(self, db: Any, bucket: Any = None) -> None:
        self.db = db
        self.bucket = bucket

    # ---------- Shoppers ----------

    def user(self, uid: str) -> UserDoc:
        snapshot = self.db.collection(USERS).document(uid).get()
        return _user_from_firestore(snapshot.to_dict() or {}) if snapshot.exists else UserDoc()

    def update_user(self, uid: str, fields: dict[str, Any]) -> None:
        """Replace just these fields, creating the document when needed."""
        data = dict(fields)
        if "taste" in data:
            data["taste"] = _taste_to_firestore(data["taste"])
        if "rules" in data:
            data["rules"] = [rule.model_dump(mode="json") for rule in data["rules"]]
        self.db.collection(USERS).document(uid).set(data, merge=list(data))

    def remember_profile(self, uid: str, name: str, email: str, current: UserDoc) -> None:
        if (current.name, current.email) != (name, email):
            self.update_user(uid, {"name": name, "email": email})

    def delete_user(self, uid: str) -> int:
        """Delete the shopper, every search they made and every photo they uploaded. Returns sessions deleted."""
        query = self.db.collection(SESSIONS).where(filter=FieldFilter("user_id", "==", uid))
        refs = [snapshot.reference for snapshot in query.stream()]
        for start in range(0, len(refs), DELETE_BATCH):
            batch = self.db.batch()
            for ref in refs[start : start + DELETE_BATCH]:
                batch.delete(ref)
            batch.commit()
        self.db.collection(USERS).document(uid).delete()
        if self.bucket is not None:
            try:
                for blob in self.bucket.list_blobs(prefix=f"{UPLOADS}/{uid}/"):
                    blob.delete()
            except NotFound:
                pass
        return len(refs)

    # ---------- Searches ----------

    def new_session(
        self, uid: str, brief: ShoppingBrief, *, query_text: str, rules: list[MustHave], photo: str | None
    ) -> SessionDoc:
        moment = now_utc()
        session = SessionDoc(
            id=uuid.uuid4().hex,
            user_id=uid,
            created_at=moment,
            updated_at=moment,
            query_text=query_text,
            brief=brief,
            rules=rules,
            photo=photo,
        )
        self.save_session(session)
        return session

    def session(self, uid: str, session_id: str) -> SessionDoc | None:
        """The session, or None when it doesn't exist or belongs to someone else."""
        if not session_id or "/" in session_id:
            return None
        snapshot = self.db.collection(SESSIONS).document(session_id).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        if data.get("user_id") != uid:
            return None
        return SessionDoc.model_validate({**data, "id": snapshot.id, "photo_vector": _plain_vector(data.get("photo_vector"))})

    def save_session(self, session: SessionDoc) -> None:
        session.updated_at = now_utc()
        names = [name for name in SessionDoc.model_fields if name != "id"]
        self.db.collection(SESSIONS).document(session.id).set(_session_fields(session, names))

    def update_session(self, session: SessionDoc, names: Sequence[str]) -> None:
        """Write just these fields, so a search saving its results and an edit to the checklist don't clash."""
        session.updated_at = now_utc()
        data = _session_fields(session, [*names, "updated_at"])
        self.db.collection(SESSIONS).document(session.id).set(data, merge=list(data))

    # ---------- Products ----------

    def product(self, product_id: str, *, with_embedding: bool = False) -> Product | None:
        if not product_id or "/" in product_id:
            return None
        fields = [*PRODUCT_FIELDS, VECTOR_FIELD] if with_embedding else PRODUCT_FIELDS
        snapshot = self.db.collection(PRODUCTS).document(product_id).get(field_paths=fields)
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        data[VECTOR_FIELD] = _plain_vector(data.get(VECTOR_FIELD)) if with_embedding else None
        return Product.model_validate({**data, "id": snapshot.id})

    # ---------- Uploaded photos ----------

    def upload_photo(self, uid: str, data: bytes, mime_type: str) -> str:
        if self.bucket is None:
            raise RuntimeError("no upload bucket is set up")
        name = f"{UPLOADS}/{uid}/{uuid.uuid4().hex}{PHOTO_TYPES[mime_type]}"
        self.bucket.blob(name).upload_from_string(data, content_type=mime_type)
        return f"gs://{self.bucket.name}/{name}"

    def read_photo(self, link: str) -> tuple[bytes, str]:
        name = self._own_upload(link)
        mime_type = next((kind for kind, ext in PHOTO_TYPES.items() if name.endswith(ext)), "image/jpeg")
        return self.bucket.blob(name).download_as_bytes(), mime_type

    def delete_photo(self, link: str) -> None:
        self.bucket.blob(self._own_upload(link)).delete()

    def is_upload(self, link: str | None) -> bool:
        match = GS_LINK.match(link or "")
        return bool(match and self.bucket is not None and match["bucket"] == self.bucket.name)

    def _own_upload(self, link: str) -> str:
        match = GS_LINK.match(link)
        if not match or self.bucket is None or match["bucket"] != self.bucket.name:
            raise ValueError("not one of our uploads")
        return match["name"]


def upload_bucket_name(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("UPLOAD_BUCKET") or f"{env.get('GOOGLE_CLOUD_PROJECT', 'ecommai')}-uploads"


@cache
def default_store() -> Store:
    from google.cloud import storage

    from agent.retrieve import default_db

    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    return Store(default_db(), client.bucket(upload_bucket_name()))
