import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent import auth
from agent.app import create_app
from agent.auth import DEMO_USER_ID, Authenticator, AuthNotAvailable, bearer_token
from tests.fixtures.app.fakes import BENGALURU, FakeDB, backend, sneakers

ORIGIN = "https://ecommai.web.app"
CLAIMS = {"uid": "shopper-1", "name": "Neha", "email": "neha@example.com"}


def verify(token: str) -> dict:
    if token == "good-token":
        return CLAIMS
    if token == "keys-down":
        raise AuthNotAvailable("couldn't fetch Google's keys")
    raise ValueError("Token expired")


def signed_in_client(env: dict | None = None) -> tuple[TestClient, object]:
    wired = backend(FakeDB(sneakers(2)))
    app = create_app(wired, env=env if env is not None else {"ALLOWED_ORIGINS": ORIGIN}, verify_token=verify)
    return TestClient(app), wired


GOOD = {"Authorization": "Bearer good-token"}

PROTECTED = [
    ("post", "/api/brief", {"text": "sneakers", "pincode": BENGALURU, "language": "en"}),
    ("post", "/api/brief/update", {"session_id": "x", "change": {"type": "set_budget", "budget_max_inr": 900}}),
    ("post", "/api/search", {"session_id": "x"}),
    ("get", "/api/products/s1", None),
    ("get", "/api/rules", None),
    ("put", "/api/rules", {"rules": []}),
    ("post", "/api/feedback", {"session_id": "x", "product_id": "s1", "action": "like"}),
    ("get", "/api/me", None),
    ("put", "/api/me", {"pincode": BENGALURU}),
    ("delete", "/api/me", None),
    ("delete", "/api/me/taste", None),
    ("post", "/api/checkout", {"product_id": "s1", "size": "UK 9"}),
]


@pytest.mark.parametrize("method, path, body", PROTECTED)
def test_every_api_endpoint_needs_a_sign_in_token(method, path, body):
    client, wired = signed_in_client()
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json() == {"detail": "Please sign in again."}
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.request(method, path, json=body, headers={"Authorization": "Bearer expired"}).status_code == 401
    assert wired.store.db.docs("users") == {}


def test_a_valid_token_works_and_keeps_name_and_email_in_the_users_document():
    client, wired = signed_in_client()
    response = client.get("/api/me", headers=GOOD)
    assert response.status_code == 200
    assert response.json()["name"] == "Neha" and response.json()["email"] == "neha@example.com"
    assert wired.store.db.docs("users")["shopper-1"] == {"name": "Neha", "email": "neha@example.com"}
    made = client.post("/api/brief", json={"text": "white sneakers", "pincode": BENGALURU, "language": "en"}, headers=GOOD)
    assert made.status_code == 200
    session = wired.store.db.docs("sessions")[made.json()["session_id"]]
    assert session["user_id"] == "shopper-1" and "Neha" not in str(session)


def test_sign_in_being_unavailable_is_a_503_not_a_401():
    client, _ = signed_in_client()
    assert client.get("/api/me", headers={"Authorization": "Bearer keys-down"}).status_code == 503


def test_dev_mode_lets_requests_without_a_token_act_as_the_demo_shopper():
    client, wired = signed_in_client({"AUTH_MODE": "dev"})
    assert client.get("/api/me").json()["name"] == "Demo Shopper"
    assert DEMO_USER_ID in wired.store.db.docs("users")
    assert client.get("/api/me", headers={"Authorization": "Bearer expired"}).status_code == 401
    assert client.get("/api/me", headers=GOOD).json()["name"] == "Neha"


def test_dev_mode_is_refused_on_cloud_run():
    with pytest.raises(RuntimeError, match="refused on Cloud Run"):
        create_app(backend(FakeDB()), env={"AUTH_MODE": "dev", "K_SERVICE": "ecommai-agent"})
    assert auth.auth_mode({"K_SERVICE": "ecommai-agent"}) == auth.FIREBASE
    with pytest.raises(RuntimeError):
        auth.auth_mode({"AUTH_MODE": "open"})


DEVICE = {"X-Device-Id": "0f6c2a0e-6b1d-4c43-9a51-3c1f7d2b8e11"}


def test_with_sign_in_off_each_browser_is_its_own_shopper_even_on_cloud_run():
    client, wired = signed_in_client({"SIGN_IN": "off", "K_SERVICE": "ecommai-agent"})
    me = client.get("/api/me", headers=DEVICE)
    assert me.status_code == 200 and not me.json()["name"]
    assert client.put("/api/me", json={"pincode": BENGALURU}, headers=DEVICE).status_code == 200
    assert client.put("/api/me", json={"pincode": BENGALURU}, headers=DEVICE).status_code == 200
    assert f"device-{DEVICE['X-Device-Id']}" in wired.store.db.docs("users")
    assert client.get("/api/me").status_code == 400
    assert client.get("/api/me", headers={"X-Device-Id": "short"}).status_code == 400
    assert client.get("/api/me", headers={**DEVICE, **GOOD}).json()["name"] == "Neha"


def test_with_sign_in_off_each_address_is_limited():
    limiter = auth.RateLimiter(limit=2, window_s=60, clock=lambda: 0.0)
    gate = Authenticator(auth.OPEN, verify, limiter)
    device = DEVICE["X-Device-Id"]
    assert gate.user_for(None, device, "1.2.3.4").uid == f"device-{device}"
    gate.user_for(None, device, "1.2.3.4")
    with pytest.raises(HTTPException) as refused:
        gate.user_for(None, device, "1.2.3.4")
    assert refused.value.status_code == 429
    assert gate.user_for(None, device, "5.6.7.8").uid == f"device-{device}"


def test_the_health_check_needs_no_token():
    client, _ = signed_in_client()
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "header, token",
    [("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", None), ("Bearer ", None), (None, None), ("abc", None)],
)
def test_bearer_token(header, token):
    assert bearer_token(header) == token


def test_a_token_without_a_uid_is_refused():
    authenticator = Authenticator(auth.FIREBASE, lambda token: {"email": "x@example.com"})
    with pytest.raises(HTTPException) as refused:
        authenticator.user_for("Bearer t")
    assert refused.value.status_code == 401


# ---------- CORS ----------


def test_only_allowed_origins_get_cors_headers():
    client, _ = signed_in_client({"ALLOWED_ORIGINS": f"{ORIGIN}, http://localhost:3000/"})
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}

    allowed = client.options("/api/search", headers={"Origin": ORIGIN, **preflight})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    local = client.options("/api/brief", headers={"Origin": "http://localhost:3000", **preflight})
    assert local.headers["access-control-allow-origin"] == "http://localhost:3000"

    refused = client.options("/api/search", headers={"Origin": "https://evil.example", **preflight})
    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers
    simple = client.get("/api/me", headers={"Origin": "https://evil.example", **GOOD})
    assert "access-control-allow-origin" not in simple.headers
    assert client.get("/api/me", headers={"Origin": ORIGIN, **GOOD}).headers["access-control-allow-origin"] == ORIGIN
