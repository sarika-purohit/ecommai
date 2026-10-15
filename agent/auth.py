"""Sign-in: every /api/* request carries a Firebase ID token (Google sign-in) as `Authorization: Bearer <token>`.

AUTH_MODE=dev is for local work only: a request without a token acts as one fixed demo shopper. A token that is
sent is still checked. The service refuses to start in dev mode on Cloud Run (K_SERVICE is set there).

SIGN_IN=off turns sign-in off: a request without a token is the shopper of its browser, named by the random
`X-Device-Id` the app keeps in local storage. Each client address is limited to OPEN_LIMIT requests per
OPEN_WINDOW_S, so an open API can't run up the Gemini bill.
"""

import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from functools import cache
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

DEV = "dev"
FIREBASE = "firebase"
OPEN = "open"
DEMO_USER_ID = "demo-shopper"
SIGN_IN_AGAIN = "Please sign in again."
DEVICE_ID = re.compile(r"^[A-Za-z0-9-]{16,64}$")
OPEN_LIMIT = 120
OPEN_WINDOW_S = 600
TOO_MANY = "Too many searches from here right now. Please try again in a few minutes."

TokenVerifier = Callable[[str], Mapping[str, Any]]


class User(BaseModel):
    uid: str
    name: str = ""
    email: str = ""


DEMO_USER = User(uid=DEMO_USER_ID, name="Demo Shopper", email="demo@ecommai.local")


class AuthNotAvailable(Exception):
    """The sign-in check itself couldn't run (for example Google's keys couldn't be fetched)."""


def auth_mode(env: Mapping[str, str]) -> str:
    if (env.get("SIGN_IN") or "").strip().lower() == "off":
        return OPEN
    mode = (env.get("AUTH_MODE") or FIREBASE).strip().lower()
    if mode not in (DEV, FIREBASE):
        raise RuntimeError(f"AUTH_MODE must be '{FIREBASE}' or '{DEV}', not {mode!r}")
    if mode == DEV and env.get("K_SERVICE"):
        raise RuntimeError("AUTH_MODE=dev is for local work only and is refused on Cloud Run (K_SERVICE is set)")
    return mode


def bearer_token(authorization: str | None) -> str | None:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class RateLimiter:
    """At most `limit` requests per `window_s` for each key, counted on this instance."""

    def __init__(self, limit: int = OPEN_LIMIT, window_s: float = OPEN_WINDOW_S, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            times = self._seen.setdefault(key, deque())
            while times and now - times[0] > self.window_s:
                times.popleft()
            if len(times) >= self.limit:
                return False
            times.append(now)
            return True


class Authenticator:
    def __init__(self, mode: str, verify: TokenVerifier | None = None, limiter: RateLimiter | None = None) -> None:
        self.mode = mode
        self._verify = verify
        self._limiter = limiter or RateLimiter()

    def user_for(self, authorization: str | None, device_id: str | None = None, client: str | None = None) -> User:
        token = bearer_token(authorization)
        if token is None:
            if self.mode == DEV:
                return DEMO_USER
            if self.mode == OPEN:
                return self.device_user(device_id, client)
            raise HTTPException(401, SIGN_IN_AGAIN, headers={"WWW-Authenticate": "Bearer"})
        verify = self._verify or firebase_verifier()
        try:
            claims = verify(token)
        except AuthNotAvailable as error:
            log.warning("sign-in check unavailable: %s", type(error).__name__)
            raise HTTPException(503, "Sign-in is busy right now. Please try again in a moment.") from error
        except Exception as error:
            log.info("sign-in token refused: %s", type(error).__name__)
            raise HTTPException(401, SIGN_IN_AGAIN, headers={"WWW-Authenticate": "Bearer"}) from error
        uid = str(claims.get("uid") or claims.get("sub") or "")
        if not uid:
            raise HTTPException(401, SIGN_IN_AGAIN, headers={"WWW-Authenticate": "Bearer"})
        return User(uid=uid, name=str(claims.get("name") or ""), email=str(claims.get("email") or ""))

    def device_user(self, device_id: str | None, client: str | None) -> User:
        device = (device_id or "").strip()
        if not DEVICE_ID.match(device):
            raise HTTPException(400, "Please reload the page and try again.")
        if not self._limiter.allow(client or device):
            raise HTTPException(429, TOO_MANY)
        return User(uid=f"device-{device}")


def client_address(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


@cache
def firebase_verifier() -> TokenVerifier:
    import firebase_admin
    from firebase_admin import auth

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = firebase_admin.initialize_app(options={"projectId": project})

    def verify(token: str) -> Mapping[str, Any]:
        try:
            return auth.verify_id_token(token, app=app)
        except auth.CertificateFetchError as error:
            raise AuthNotAvailable(str(error)) from error

    return verify


def current_user(request: Request) -> User:
    return request.app.state.auth.user_for(
        request.headers.get("authorization"), request.headers.get("x-device-id"), client_address(request)
    )
