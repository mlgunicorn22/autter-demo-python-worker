from dataclasses import dataclass, field
import time, uuid
@dataclass
class Job:
    id:str; org_id:str; kind:str; payload:dict; status:str="queued"; attempts:int=0; error:str|None=None; visible_at:float=0; idempotency_key:str|None=None
jobs:list[Job]=[]
def submit_job(org_id, kind, payload, idempotency_key=None):
    job=Job(id=str(uuid.uuid4()),org_id=org_id,kind=kind,payload=payload,idempotency_key=idempotency_key)
    jobs.append(job); return job
def next_job(org_id):
    now=time.time()
    for job in jobs:
        if job.status in ("queued","processing") and job.visible_at <= now:
            job.status="processing"; job.visible_at=now+1; return job
    return None