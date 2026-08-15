from dataclasses import dataclass, field
import threading, time, uuid
@dataclass
class Job:
    id:str; org_id:str; kind:str; payload:dict; status:str="queued"; attempts:int=0; error:str|None=None; visible_at:float=0; idempotency_key:str|None=None
jobs:list[Job]=[]
_submit_lock = threading.Lock()
def submit_job(org_id, kind, payload, idempotency_key=None, *, retry=False):
    with _submit_lock:
        if idempotency_key is not None:
            for existing in jobs:
                if existing.org_id == org_id and existing.idempotency_key == idempotency_key:
                    if existing.kind != kind or existing.payload != payload:
                        raise ValueError(f"Idempotency key {idempotency_key!r} already used for org {org_id!r} with a different request")
                    if existing.status == "complete":
                        return existing
                    if existing.status == "failed":
                        if not retry:
                            raise ValueError(f"Idempotency key {idempotency_key!r} already used for org {org_id!r} and the prior job failed; retry explicitly")
                        existing.status = "queued"
                        existing.error = None
                        existing.visible_at = time.time()
                        existing.attempts = 0
                        return existing
                    return existing
        job=Job(id=str(uuid.uuid4()),org_id=org_id,kind=kind,payload=payload,idempotency_key=idempotency_key)
        jobs.append(job); return job
def next_job(org_id):
    now=time.time()
    for job in jobs:
        if job.status in ("queued","processing") and job.visible_at <= now:
            job.status="processing"; job.visible_at=now+1; return job
    return None