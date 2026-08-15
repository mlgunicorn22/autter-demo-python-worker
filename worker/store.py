from dataclasses import dataclass, field
import threading, time, uuid

PAYLOAD_RETENTION_SECONDS = 7 * 24 * 60 * 60
ORGANIZATION_RETENTION_SECONDS = 30 * 24 * 60 * 60

@dataclass
class Job:
    id:str; org_id:str; kind:str; payload:dict; status:str="queued"; attempts:int=0; error:str|None=None; visible_at:float=0; idempotency_key:str|None=None; expires_at:float|None=None; deleted_at:float|None=None
jobs:list[Job]=[]
_submit_lock = threading.Lock()

def purge_expired_jobs(now=None):
    now = time.time() if now is None else now
    with _submit_lock:
        expired = [job for job in jobs if job.expires_at is not None and job.expires_at <= now]
        for job in expired:
            job.deleted_at = now
            jobs.remove(job)
        return len(expired)


def _job_is_expired(job, now=None):
    now = time.time() if now is None else now
    return job.expires_at is not None and job.expires_at <= now


def submit_job(org_id, kind, payload, idempotency_key=None, *, retry=False):
    if idempotency_key is None:
        raise ValueError("Idempotency key is required")

    normalized_key = str(idempotency_key).strip()
    if not normalized_key:
        raise ValueError("Idempotency key is required")

    with _submit_lock:
        purge_expired_jobs()
        for existing in jobs:
            if existing.org_id == org_id and existing.idempotency_key == normalized_key:
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
        now = time.time()
        job=Job(id=str(uuid.uuid4()),org_id=org_id,kind=kind,payload=payload,idempotency_key=normalized_key,expires_at=now + PAYLOAD_RETENTION_SECONDS)
        jobs.append(job); return job

def next_job(org_id):
    now=time.time()
    with _submit_lock:
        purge_expired_jobs(now)
        for job in jobs:
            if job.org_id != org_id:
                continue
            if _job_is_expired(job, now):
                jobs.remove(job)
                continue
            if job.status in ("queued","processing") and job.visible_at <= now:
                job.status="processing"; job.visible_at=now+1; return job
        return None