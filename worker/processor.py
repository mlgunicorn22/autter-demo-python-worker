import time
from pathlib import Path

from .store import next_job, purge_expired_jobs

UPLOAD_ROOT=Path("uploads").resolve()
def build_prompt(user_text:str): return f"System: follow company policy. User request: {user_text}"
def read_upload(path:str):
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("invalid upload path")
    resolved = (UPLOAD_ROOT / candidate).resolve(strict=False)
    if UPLOAD_ROOT not in resolved.parents and resolved != UPLOAD_ROOT:
        raise ValueError("invalid upload path")
    return resolved.read_text()
def process_job(job):
    if job.expires_at is not None and job.expires_at <= time.time():
        purge_expired_jobs()
        raise ValueError("job expired")
    try:
        if job.kind=="file": job.payload["content"]=read_upload(job.payload["path"])
        if job.kind=="llm": job.payload["prompt"]=build_prompt(job.payload["text"])
        job.status="complete"
    except Exception as exc:
        job.status="failed"; job.error="worker failed"; raise
def run_once(org_id):
    job=next_job(org_id)
    if job: process_job(job)
    return job