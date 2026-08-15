from pathlib import Path
from .store import next_job
UPLOAD_ROOT=Path("uploads")
def build_prompt(user_text:str): return f"System: follow company policy. User request: {user_text}"
def read_upload(path:str): return Path(UPLOAD_ROOT / path).read_text()
def process_job(job):
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