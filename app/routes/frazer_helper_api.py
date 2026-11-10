"""
Machine endpoints for the Frazer export helper. NOT mounted behind the sign-in dependencies: they accept only a helper token
(Authorization: Bearer fxh_...), which is not a session, opens no page, and can do exactly these five things. Responses carry
run status and per-file outcomes (file names, row counts, upload ids) and never a customer name, balance or vehicle.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import frazer_helper_service as svc
from app.services import frazer_intake_service as intake

router = APIRouter()


def token_auth(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    raw = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
    try:
        t = svc.authenticate(db, raw)
    except svc.Unauthorized as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"})
    db.commit()                      # remember last_used_at even if the call then fails
    return t


def _err(e: svc.HelperError):
    return JSONResponse({"error": str(e), **({"active_run": e.run.run_id} if isinstance(e, svc.ActiveRun) else {})}, status_code=e.status_code)


def _run_json(run) -> dict:
    return {"run_id": run.run_id, "status": run.status, "started_at": run.started_at.isoformat(timespec="seconds") + "Z",
            "business_date": run.business_date, "results": run.results_json, "sync": run.sync_json, "summary": run.summary,
            "plan": (run.params_json or {}).get("plan"),
            "lease_expires_at": run.lease_expires_at.isoformat(timespec="seconds") + "Z"}


class StartBody(BaseModel):
    helper_version: str = ""
    host: str = ""
    first_coverage_start: Optional[str] = None      # only used before the first complete run; later periods come from the checkpoint
    timezone: str = ""
    retry_of: Optional[str] = None


@router.get("/status")
def status(token=Depends(token_auth), db: Session = Depends(get_db)):
    f = svc.freshness(db)
    cp = svc.coverage_checkpoint(db)
    return {"layouts_approved": sorted(svc.layouts(db)),
            "last": {t: {"upload": f[t]["upload"].upload_id if f[t]["upload"] else None,
                         "uploaded_at": f[t]["upload"].uploaded_at.isoformat(timespec="seconds") + "Z" if f[t]["upload"] else None,
                         "stale": f[t]["stale"]} for t in svc.EXPORT_TYPES},
            "last_complete_run": f["last_complete_run"].run_id if f["last_complete_run"] else None,
            "server_time": svc._now().isoformat(timespec="seconds") + "Z", "business_date": svc.business_date(svc._now()).isoformat(),
            "coverage_checkpoint": ({"end": cp["end"].isoformat(), "run_id": cp["run_id"]} if cp else None)}


@router.post("/runs")
def start(body: StartBody, token=Depends(token_auth), db: Session = Depends(get_db)):
    try:
        run = svc.start_run(db, token, params=body.model_dump(exclude={"retry_of"}), retry_of=body.retry_of)
        db.commit()
    except svc.HelperError as e:
        db.rollback()
        return _err(e)
    return _run_json(run)


@router.post("/runs/{run_id}/files")
async def send_file(run_id: str, token=Depends(token_auth), db: Session = Depends(get_db), file: UploadFile = File(...),
                    export_type: str = Form(...), sha256: str = Form(...), generated_at: str = Form(...), export_date: str = Form(...),
                    coverage_start: str = Form(""), coverage_end: str = Form(""), original_filename: str = Form(""),
                    generated_local: str = Form(""), timezone: str = Form(""), period_readback: str = Form(""), generation: str = Form("")):
    data = await file.read(intake.MAX_UPLOAD_BYTES + 1)
    try:
        run = svc.get_run(db, token, run_id)
        if len(data) > intake.MAX_UPLOAD_BYTES:
            raise svc.HelperError("That file is larger than 10 MB.")
        res = svc.submit_file(db, token, run, export_type=export_type, filename=file.filename or "export.csv", data=data, sha256=sha256,
                              generated_at=generated_at, export_date=export_date, coverage_start=coverage_start, coverage_end=coverage_end,
                              meta={"original_filename": original_filename, "generated_local": generated_local, "timezone": timezone,
                                    "period_readback": period_readback, "generation": generation})
    except svc.HelperError as e:
        db.rollback()
        return _err(e)
    from app.models.frazer_intake import FrazerUpload
    stored = db.get(FrazerUpload, res["upload_pk"]).stored_path if res.get("outcome") in ("stored", "stored_with_errors") else None
    try:
        db.commit()
    except Exception:
        db.rollback()
        if stored:
            intake.discard_file(stored)       # the original is written before the commit; do not leave an orphan
        raise
    return res


@router.post("/runs/{run_id}/failures")
def failure(run_id: str, token=Depends(token_auth), db: Session = Depends(get_db), export_type: str = Form(...), outcome: str = Form(...),
            message: str = Form("")):
    try:
        res = svc.report_failure(db, svc.get_run(db, token, run_id), export_type, outcome, message)
        db.commit()
    except svc.HelperError as e:
        db.rollback()
        return _err(e)
    return res


@router.post("/runs/{run_id}/finish")
def finish(run_id: str, token=Depends(token_auth), db: Session = Depends(get_db)):
    try:
        run = svc.finish_run(db, svc.get_run(db, token, run_id))
        db.commit()
    except svc.HelperError as e:
        db.rollback()
        return _err(e)
    return _run_json(run)
