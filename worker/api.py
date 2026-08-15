import logging
import time
import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from .store import submit_job, jobs, purge_expired_jobs

logger = logging.getLogger(__name__)

PUBLIC_JOB_FIELDS = {"id", "org_id", "kind", "status"}


def serialize_public_job(job):
    return {key: getattr(job, key) for key in PUBLIC_JOB_FIELDS if hasattr(job, key)}


def _safe_error_detail(message: str, *, correlation_id: str):
    logger.warning("%s: %s", correlation_id, message)
    return {"detail": "request failed"}


class InMemoryRateLimiter:
    def __init__(self, client_limit=2, org_limit=3, window_seconds=60, queue_limit=10, unauthenticated_limit=2, unauthenticated_window_seconds=30):
        self.client_limit = client_limit
        self.org_limit = org_limit
        self.window_seconds = window_seconds
        self.queue_limit = queue_limit
        self.unauthenticated_limit = unauthenticated_limit
        self.unauthenticated_window_seconds = unauthenticated_window_seconds
        self._client_requests = defaultdict(deque)
        self._org_requests = defaultdict(deque)
        self._unauthenticated_requests = defaultdict(deque)

    def clear(self):
        self._client_requests.clear()
        self._org_requests.clear()
        self._unauthenticated_requests.clear()

    def allow(self, client_key: str, org_id: str, *, authenticated: bool = True):
        now = time.monotonic()
        client_window = self._client_requests[client_key]
        org_window = self._org_requests[org_id]
        unauth_window = self._unauthenticated_requests[client_key]

        while client_window and now - client_window[0] >= self.window_seconds:
            client_window.popleft()
        while org_window and now - org_window[0] >= self.window_seconds:
            org_window.popleft()
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

        if len(client_window) >= self.client_limit:
            retry_after = max(1, int(self.window_seconds - (now - client_window[0])))
            return False, retry_after

        if len(org_window) >= self.org_limit:
            retry_after = max(1, int(self.window_seconds - (now - org_window[0])))
            return False, retry_after

        client_window.append(now)
        org_window.append(now)
        return True, 0


rate_limiter = InMemoryRateLimiter(client_limit=2, org_limit=3, window_seconds=60, queue_limit=10, unauthenticated_limit=2, unauthenticated_window_seconds=30)
app=FastAPI()


class JobIn(BaseModel):
    org_id:str; kind:str; payload:dict; callback_url:str|None=None; retry:bool=False

class JobResponse(BaseModel):
    id:str
    org_id:str
    kind:str
    status:str

@app.post("/jobs")
def create_job(body:JobIn, request: Request, idempotency_key:str=Header(..., min_length=1)):
    client_ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    allowed, retry_after = rate_limiter.allow(client_ip, body.org_id)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(retry_after)})
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(status_code=400, detail="Idempotency key is required")
    try:
        job = submit_job(body.org_id, body.kind, body.payload, normalized_key, retry=body.retry)
        return JobResponse.model_validate(serialize_public_job(job))
    except ValueError as exc:
        correlation_id = str(uuid.uuid4())
        logger.exception("job creation failed [%s]", correlation_id)
        raise HTTPException(status_code=409, detail=_safe_error_detail("idempotency conflict", correlation_id=correlation_id)["detail"]) from exc

@app.get("/jobs/{job_id}")
def get_job(job_id:str, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=404, detail="job not found")

    caller_org = auth_header.split(" ", 1)[1].strip()
    if not caller_org:
        raise HTTPException(status_code=404, detail="job not found")

    client_ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    allowed, retry_after = rate_limiter.allow(client_ip, caller_org, authenticated=True)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(retry_after)})

    purge_expired_jobs()
    job = next((j for j in jobs if j.id == job_id), None)
    if job is None or job.expires_at is not None and job.expires_at <= time.time() or job.org_id != caller_org:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse.model_validate(serialize_public_job(job))