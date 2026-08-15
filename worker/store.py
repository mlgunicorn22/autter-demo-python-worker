from dataclasses import dataclass
import threading
import time
import uuid

PAYLOAD_RETENTION_SECONDS = 7 * 24 * 60 * 60


@dataclass
class Job:
    id: str
    org_id: str
    kind: str
    payload: dict
    status: str = "queued"
    attempts: int = 0
    error: str | None = None
    visible_at: float = 0
    idempotency_key: str | None = None
    expires_at: float | None = None


jobs: list[Job] = []
_submit_lock = threading.Lock()


def _normalize_idempotency_key(idempotency_key):
    if idempotency_key is None:
        raise ValueError("Idempotency key is required")

    normalized_key = str(idempotency_key).strip()
    if not normalized_key:
        raise ValueError("Idempotency key is required")
    return normalized_key


def _purge_expired_jobs_locked(now=None):
    now = time.time() if now is None else now
    expired = [job for job in jobs if job.expires_at is not None and job.expires_at <= now]
    for job in expired:
        jobs.remove(job)
    return len(expired)


def purge_expired_jobs(now=None):
    now = time.time() if now is None else now
    with _submit_lock:
        return _purge_expired_jobs_locked(now)


def _find_existing_job(org_id, normalized_key):
    for existing in jobs:
        if existing.org_id == org_id and existing.idempotency_key == normalized_key:
            return existing
    return None


def _build_new_job(org_id, kind, payload, normalized_key, *, now=None):
    now = time.time() if now is None else now
    job = Job(
        id=str(uuid.uuid4()),
        org_id=org_id,
        kind=kind,
        payload=payload,
        idempotency_key=normalized_key,
        expires_at=now + PAYLOAD_RETENTION_SECONDS,
    )
    jobs.append(job)
    return job


def submit_job(org_id, kind, payload, idempotency_key=None, *, retry=False):
    normalized_key = _normalize_idempotency_key(idempotency_key)

    with _submit_lock:
        _purge_expired_jobs_locked()
        existing = _find_existing_job(org_id, normalized_key)
        if existing is not None:
            if existing.kind != kind or existing.payload != payload:
                raise ValueError(f"Idempotency key {normalized_key!r} already used for org {org_id!r} with a different request")
            if existing.status == "complete":
                return existing
            if existing.status == "failed":
                if not retry:
                    raise ValueError(f"Idempotency key {normalized_key!r} already used for org {org_id!r} and the prior job failed; retry explicitly")
                existing.status = "queued"
                existing.error = None
                existing.visible_at = time.time()
                existing.attempts = 0
                existing.expires_at = time.time() + PAYLOAD_RETENTION_SECONDS
                return existing
            return existing
        return _build_new_job(org_id, kind, payload, normalized_key)


def next_job(org_id):
    now = time.time()
    with _submit_lock:
        _purge_expired_jobs_locked(now)
        for job in jobs:
            if job.org_id != org_id:
                continue
            if job.status in ("queued", "processing") and job.visible_at <= now:
                job.status = "processing"
                job.visible_at = now + 1
                return job
        return None
