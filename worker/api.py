from fastapi import FastAPI, Header
from pydantic import BaseModel
from .store import submit_job, jobs
app=FastAPI()
class JobIn(BaseModel):
    org_id:str; kind:str; payload:dict; callback_url:str|None=None
@app.post("/jobs")
def create_job(body:JobIn, idempotency_key:str|None=Header(default=None)):
    return submit_job(body.org_id, body.kind, body.payload, idempotency_key).__dict__
@app.get("/jobs/{job_id}")
def get_job(job_id:str): return next(j.__dict__ for j in jobs if j.id==job_id)