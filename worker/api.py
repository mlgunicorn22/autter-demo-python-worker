import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .store import jobs, purge_expired_jobs, submit_job

logger = logging.getLogger(__name__)
AUTH_SECRET = "autter-demo-worker-secret"
TRUSTED_PROXIES = {"127.0.0.1", "::1", "localhost", "trusted-proxy"}

PUBLIC_JOB_FIELDS = {"id", "org_id", "kind", "status"}


def serialize_public_job(job):
    return {key: getattr(job, key) for key in PUBLIC_JOB_FIELDS if hasattr(job, key)}


def _safe_error_detail(message: str, *, correlation_id: str):
    logger.warning("%s: %s", correlation_id, message)
    return {"detail": "request failed"}


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_signed_token(org_id: str, *, ttl_seconds: int = 3600, sub: str | None = None) -> str:
    payload = {"org_id": org_id, "exp": int(time.time()) + ttl_seconds}
    if sub is not None:
        payload["sub"] = sub
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body_b64 = _b64url_encode(body)
    signing_key = hmac.new(AUTH_SECRET.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    signature = _b64url_encode(signing_key)
    return f"{body_b64}.{signature}"


def verify_signed_token(raw_token: str) -> dict[str, Any]:
    if not raw_token or "." not in raw_token:
        raise ValueError("invalid token")

    body_b64, signature = raw_token.split(".", 1)
    expected = hmac.new(AUTH_SECRET.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected), signature):
        raise ValueError("tampered token")

    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid token") from None

    if not isinstance(payload, dict):
        raise ValueError("invalid token")
    if "org_id" not in payload or not str(payload["org_id"]).strip():
        raise ValueError("missing org_id")
    if "exp" in payload and int(payload["exp"]) < int(time.time()):
        raise ValueError("expired token")
    return payload


def get_client_identity(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in TRUSTED_PROXIES:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return peer


def get_verified_principal(request: Request) -> dict[str, Any]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=404, detail="job not found")

    token = auth_header.split(" ", 1)[1].strip()
    try:
        principal = verify_signed_token(token)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found")

    org_id = str(principal.get("org_id", "")).strip()
    if not org_id:
        raise HTTPException(status_code=404, detail="job not found")
    return principal


class InMemoryRateLimiter:
    def __init__(self, client_limit=2, org_limit=3, window_seconds=60, queue_limit=10, unauthenticated_limit=2, unauthenticated_window_seconds=30, principal_limit=5):
        self.client_limit = client_limit
        self.org_limit = org_limit
        self.window_seconds = window_seconds
        self.queue_limit = queue_limit
        self.unauthenticated_limit = unauthenticated_limit
        self.unauthenticated_window_seconds = unauthenticated_window_seconds
        self.principal_limit = principal_limit
        self._client_requests = defaultdict(deque)
        self._org_requests = defaultdict(deque)
        self._principal_requests = defaultdict(deque)
        self._unauthenticated_requests = defaultdict(deque)

    def clear(self):
        self._client_requests.clear()
        self._org_requests.clear()
        self._principal_requests.clear()
        self._unauthenticated_requests.clear()

    def allow(self, client_key: str, org_id: str, *, authenticated: bool = True, principal_key: str | None = None):
        now = time.monotonic()
        client_window = self._client_requests[client_key]
        org_window = self._org_requests[org_id]
        principal_window = self._principal_requests[principal_key] if principal_key is not None else None
        unauth_window = self._unauthenticated_requests[client_key]

        while client_window and now - client_window[0] >= self.window_seconds:
            client_window.popleft()
        while org_window and now - org_window[0] >= self.window_seconds:
            org_window.popleft()
        if principal_window is not None:
            while principal_window and now - principal_window[0] >= self.window_seconds:
                principal_window.popleft()
        while unauth_window and now - unauth_window[0] >= self.unauthenticated_window_seconds:
            unauth_window.popleft()

        queued_jobs = sum(1 for job in jobs if job.status in {"queued", "processing"})
        if queued_jobs >= self.queue_limit:
            return False, 1

        if not authenticated:
            if len(unauth_window) >= self.unauthenticated_limit:
                retry_after = max(1, int(self.unauthenticated_window_seconds - (now - unauth_window[0])))
                return False, retry_after
            unauth_window.append(now)
            return True, 0

        if principal_window is not None and len(principal_window) >= self.principal_limit:
            retry_after = max(1, int(self.window_seconds - (now - principal_window[0])))
            return False, retry_after

        if len(client_window) >= self.client_limit:
            retry_after = max(1, int(self.window_seconds - (now - client_window[0])))
            return False, retry_after

        if len(org_window) >= self.org_limit:
            retry_after = max(1, int(self.window_seconds - (now - org_window[0])))
            return False, retry_after

        client_window.append(now)
        org_window.append(now)
        if principal_window is not None:
            principal_window.append(now)
        return True, 0


rate_limiter = InMemoryRateLimiter(client_limit=2, org_limit=3, window_seconds=60, queue_limit=10, unauthenticated_limit=2, unauthenticated_window_seconds=30)
app = FastAPI()


class JobIn(BaseModel):
    org_id: str | None = None
    kind: str
    payload: dict
    callback_url: str | None = None
    retry: bool = False


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    kind: str
    status: str


@app.post("/jobs")
def create_job(body: JobIn, request: Request, principal: dict[str, Any] = Depends(get_verified_principal), idempotency_key: str = Header(..., min_length=1)):
    org_id = str(principal.get("org_id", "")).strip()
    if not org_id:
        raise HTTPException(status_code=404, detail="job not found")

    client_identity = get_client_identity(request)
    principal_key = f"principal:{principal.get('sub', org_id)}"
    allowed, retry_after = rate_limiter.allow(client_identity, org_id, authenticated=True, principal_key=principal_key)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(retry_after)})

    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(status_code=400, detail="Idempotency key is required")
    try:
        job = submit_job(org_id, body.kind, body.payload, normalized_key, retry=body.retry)
    except ValueError as exc:
        correlation_id = str(uuid.uuid4())
        logger.exception("job creation failed [%s]", correlation_id)
        raise HTTPException(status_code=409, detail=_safe_error_detail("idempotency conflict", correlation_id=correlation_id)["detail"]) from exc
    return JobResponse.model_validate(job)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, principal: dict[str, Any] = Depends(get_verified_principal)):
    caller_org = str(principal.get("org_id", "")).strip()
    if not caller_org:
        raise HTTPException(status_code=404, detail="job not found")

    client_identity = get_client_identity(request)
    principal_key = f"principal:{principal.get('sub', caller_org)}"
    allowed, retry_after = rate_limiter.allow(client_identity, caller_org, authenticated=True, principal_key=principal_key)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(retry_after)})

    purge_expired_jobs()
    job = next((j for j in jobs if j.id == job_id), None)
    if job is None or job.expires_at is not None and job.expires_at <= time.time() or job.org_id != caller_org:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse.model_validate(job)
