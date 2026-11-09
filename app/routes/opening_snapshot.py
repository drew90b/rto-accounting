"""
Opening Snapshot screens (admin only; the router is mounted with require_admin).

There is deliberately no edit or delete route for any recorded figure, entry or
file. Things are added beside the original report, or withdrawn with a reason.
"""
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models.enums import OpeningEntryType, OpeningEvidenceKind, OpeningFigureStatus, OpeningSection
from app.models.opening_snapshot import OpeningFigure, OpeningFigureEvidence
from app.models.user import User
from app.services import opening_snapshot_service as svc

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_CHICAGO = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _money(value) -> str:
    if value is None:
        return ""
    return f"${value:,.0f}" if value == value.to_integral_value() else f"${value:,.2f}"


def _local(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.replace(tzinfo=_UTC).astimezone(_CHICAGO).strftime("%b %-d, %Y %-I:%M %p %Z")


def _day(d: Optional[date]) -> str:
    return d.strftime("%b %-d, %Y") if d else ""


templates.env.filters["osmoney"] = _money
templates.env.filters["oslocal"] = _local
templates.env.filters["osday"] = _day

SECTION_CHOICES = [(s.value, label) for s, label in svc.SECTION_ORDER]
KIND_CHOICES = [(k.value, k.value.replace("_", " ").capitalize()) for k in OpeningEvidenceKind]


def _date_or_none(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise svc.SnapshotError("Dates must look like 2026-11-02.")


def _page(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(name, {"request": request, **ctx}, status_code=status_code)


def _figure_or_404(db: Session, figure_id: int) -> Optional[OpeningFigure]:
    return db.query(OpeningFigure).filter(OpeningFigure.id == figure_id).first()


def _not_found(request: Request):
    return _page(request, "opening_snapshot/error.html", 404, title="Not found",
                 message="That item doesn't exist.")


def _conflict(request: Request, message: str):
    return _page(request, "opening_snapshot/error.html", 409, title="Can't do that", message=message)


def _back(path: str, msg: str):
    return RedirectResponse(url=f"{path}?msg={msg.replace(' ', '+')}", status_code=303)


# ---- snapshot setup and overview ----------------------------------------

@router.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_db)):
    snap = svc.get_snapshot(db)
    if snap is None:
        return _page(request, "opening_snapshot/none.html")
    return _page(request, "opening_snapshot/overview.html", snapshot=snap,
                 sections=svc.overview(db, snap),
                 withdrawn_count=sum(1 for f in snap.figures if f.is_withdrawn))


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, db: Session = Depends(get_db)):
    if svc.get_snapshot(db) is not None:
        return RedirectResponse(url="/finance/opening/", status_code=303)
    return _page(request, "opening_snapshot/setup.html", form={}, error=None)


@router.post("/setup")
def setup(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
          name: str = Form(...), as_of_date: str = Form(...), label: str = Form(...),
          cutoff_note: str = Form("")):
    form = {"name": name, "as_of_date": as_of_date, "label": label, "cutoff_note": cutoff_note}
    try:
        svc.create_snapshot(db, user, name=name, as_of_date=_date_or_none(as_of_date) or date.today(),
                            label=label, cutoff_note=cutoff_note)
    except svc.Conflict as e:
        return _conflict(request, str(e))
    except svc.SnapshotError as e:
        return _page(request, "opening_snapshot/setup.html", 400, form=form, error=str(e))
    db.commit()
    return _back("/finance/opening/", "Snapshot created")


# ---- add a figure --------------------------------------------------------

def _figure_form(request, status_code=200, form=None, error=None):
    return _page(request, "opening_snapshot/figure_new.html", status_code, form=form or {}, error=error,
                 sections=SECTION_CHOICES, today=date.today().isoformat())


@router.get("/figures/new", response_class=HTMLResponse)
def new_figure_form(request: Request, db: Session = Depends(get_db)):
    if svc.get_snapshot(db) is None:
        return RedirectResponse(url="/finance/opening/", status_code=303)
    return _figure_form(request, form={"status": "reported", "reported_via": "text message",
                                       "reported_cutoff": "Unknown"})


@router.post("/figures/new")
def new_figure(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
               section: str = Form(""), label: str = Form(""), amount: str = Form(""),
               count: str = Form(""), statement: str = Form(""), reported_by: str = Form(""),
               reported_via: str = Form(""), reported_on: str = Form(""), time_note: str = Form(""),
               reported_cutoff: str = Form(""), status: str = Form("reported"),
               pending_need: str = Form(""), basis_note: str = Form(""), due_note: str = Form("")):
    snap = svc.get_snapshot(db)
    if snap is None:
        return RedirectResponse(url="/finance/opening/", status_code=303)
    form = dict(section=section, label=label, amount=amount, count=count, statement=statement,
                reported_by=reported_by, reported_via=reported_via, reported_on=reported_on,
                time_note=time_note, reported_cutoff=reported_cutoff, status=status,
                pending_need=pending_need, basis_note=basis_note, due_note=due_note)
    try:
        fig = svc.create_figure(
            db, snap, user, section=section, label=label, amount=amount, count=count,
            statement=statement, reported_by=reported_by, reported_via=reported_via,
            reported_on=_date_or_none(reported_on), time_note=time_note, cutoff=reported_cutoff,
            status=status, pending_need=pending_need, basis_note=basis_note, due_note=due_note,
        )
    except svc.SnapshotError as e:
        db.rollback()
        return _figure_form(request, 400, form, str(e))
    db.commit()
    return _back(f"/finance/opening/figures/{fig.id}", "Figure recorded")


# ---- one figure ----------------------------------------------------------

@router.get("/figures/{figure_id}", response_class=HTMLResponse)
def figure_detail(figure_id: int, request: Request, db: Session = Depends(get_db)):
    fig = _figure_or_404(db, figure_id)
    if fig is None:
        return _not_found(request)
    return _page(request, "opening_snapshot/figure.html", figure=fig,
                 understanding=svc.current_understanding(fig), error=None)


def _clarify_form(request, fig, status_code=200, form=None, error=None):
    corrections = [e for e in fig.entries
                   if e.entry_type in (OpeningEntryType.clarification, OpeningEntryType.correction)]
    return _page(request, "opening_snapshot/clarify.html", status_code, figure=fig, form=form or {},
                 error=error, basis_choices=svc.BASIS_CHOICES, correctable=corrections)


@router.get("/figures/{figure_id}/clarify", response_class=HTMLResponse)
def clarify_form(figure_id: int, request: Request, db: Session = Depends(get_db)):
    fig = _figure_or_404(db, figure_id)
    if fig is None:
        return _not_found(request)
    if fig.is_withdrawn:
        return _conflict(request, "This figure was withdrawn. Nothing more can be added to it.")
    return _clarify_form(request, fig)


@router.post("/figures/{figure_id}/clarify")
def clarify(figure_id: int, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_admin), body: str = Form(""), refers_to_date: str = Form(""),
            cutoff_update: str = Form(""), basis_update: str = Form(""), basis_other: str = Form(""),
            basis_confirmed: str = Form(""), corrects_entry_id: str = Form("")):
    fig = _figure_or_404(db, figure_id)
    if fig is None:
        return _not_found(request)
    form = dict(body=body, refers_to_date=refers_to_date, cutoff_update=cutoff_update,
                basis_update=basis_update, basis_other=basis_other, basis_confirmed=basis_confirmed,
                corrects_entry_id=corrects_entry_id)
    try:
        svc.add_clarification(db, fig, user, body=body, refers_to_date=_date_or_none(refers_to_date),
                              cutoff_update=cutoff_update, basis_update=basis_update,
                              basis_other=basis_other, basis_confirmed=basis_confirmed,
                              corrects_entry_id=corrects_entry_id)
    except svc.Conflict as e:
        db.rollback()
        return _conflict(request, str(e))
    except svc.SnapshotError as e:
        db.rollback()
        return _clarify_form(request, fig, 400, form, str(e))
    db.commit()
    return _back(f"/finance/opening/figures/{figure_id}", "Clarification added. The original report is unchanged")


def _evidence_form(request, fig, status_code=200, form=None, error=None):
    return _page(request, "opening_snapshot/evidence.html", status_code, figure=fig, form=form or {},
                 error=error, kinds=KIND_CHOICES)


@router.get("/figures/{figure_id}/evidence", response_class=HTMLResponse)
def evidence_form(figure_id: int, request: Request, db: Session = Depends(get_db)):
    fig = _figure_or_404(db, figure_id)
    if fig is None:
        return _not_found(request)
    if fig.is_withdrawn:
        return _conflict(request, "This figure was withdrawn. Nothing more can be added to it.")
    return _evidence_form(request, fig)


@router.post("/figures/{figure_id}/evidence")
async def add_evidence(figure_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_admin), kind: str = Form(""),
                       description: str = Form(""), no_pay_detail: str = Form(""),
                       file: Optional[UploadFile] = File(None)):
    fig = _figure_or_404(db, figure_id)
    if fig is None:
        return _not_found(request)
    form = dict(kind=kind, description=description, no_pay_detail=no_pay_detail)
    try:
        ev = svc.add_evidence(db, fig, user, file=file, kind=kind, description=description,
                              no_pay_detail=bool(no_pay_detail))
    except svc.Conflict as e:
        db.rollback()
        return _conflict(request, str(e))
    except svc.SnapshotError as e:
        db.rollback()
        return _evidence_form(request, fig, 400, form, str(e))
    try:
        db.commit()
    except Exception:
        db.rollback()
        svc.discard_evidence_file(ev.stored_path)
        raise
    return _back(f"/finance/opening/figures/{figure_id}", "Evidence added. The original report is unchanged")


@router.post("/figures/{figure_id}/withdraw")
def withdraw(figure_id: int, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_admin), reason: str = Form("")):
    fig = _figure_or_404(db, figure_id)
    if fig is None:
        return _not_found(request)
    try:
        svc.withdraw_figure(db, fig, user, reason)
    except svc.Conflict as e:
        db.rollback()
        return _conflict(request, str(e))
    except svc.SnapshotError as e:
        db.rollback()
        return _page(request, "opening_snapshot/figure.html", 400, figure=fig,
                     understanding=svc.current_understanding(fig), error=str(e))
    db.commit()
    return _back(f"/finance/opening/figures/{figure_id}", "Figure withdrawn. Its history is kept")


# ---- evidence download ---------------------------------------------------

@router.get("/evidence/{evidence_id}/download")
def download_evidence(evidence_id: int, request: Request, db: Session = Depends(get_db)):
    ev = db.query(OpeningFigureEvidence).filter(OpeningFigureEvidence.id == evidence_id).first()
    path = svc.evidence_file_path(ev) if ev else None
    if ev is None or path is None:
        return _not_found(request)
    return FileResponse(
        path, filename=ev.original_filename, media_type=ev.content_type,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


# ---- original report and withdrawn --------------------------------------

@router.get("/original", response_class=HTMLResponse)
def original_report(request: Request, db: Session = Depends(get_db)):
    snap = svc.get_snapshot(db)
    if snap is None:
        return RedirectResponse(url="/finance/opening/", status_code=303)
    return _page(request, "opening_snapshot/original.html", snapshot=snap, figures=snap.figures)


@router.get("/withdrawn", response_class=HTMLResponse)
def withdrawn(request: Request, db: Session = Depends(get_db)):
    snap = svc.get_snapshot(db)
    if snap is None:
        return RedirectResponse(url="/finance/opening/", status_code=303)
    return _page(request, "opening_snapshot/withdrawn.html", snapshot=snap,
                 figures=[f for f in snap.figures if f.is_withdrawn])
