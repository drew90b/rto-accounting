import os
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import Document
from app.models.enums import LinkedRecordType
from app.services.document_service import save_uploaded_file

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_documents(
    request: Request,
    db: Session = Depends(get_db),
    record_type: Optional[str] = None,
):
    query = db.query(Document)
    if record_type:
        query = query.filter(Document.linked_record_type == record_type)
    docs = query.order_by(Document.upload_timestamp.desc()).all()
    return templates.TemplateResponse("documents/list.html", {
        "request": request,
        "documents": docs,
        "record_type_filter": record_type or "",
        "record_types": [r.value for r in LinkedRecordType],
    })


@router.post("/upload")
async def upload_document(
    db: Session = Depends(get_db),
    linked_record_type: str = Form(...),
    linked_record_id: str = Form(...),
    uploaded_by: str = Form(""),
    notes: str = Form(""),
    file: UploadFile = File(...),
):
    record_id = int(linked_record_id)
    save_uploaded_file(
        linked_record_type=linked_record_type,
        record_id=record_id,
        file=file,
        uploaded_by=uploaded_by,
        notes=notes,
        db=db,
    )
    db.commit()
    return RedirectResponse(url="/documents/?msg=Document+uploaded", status_code=303)


@router.get("/{doc_id}/download")
def download_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not os.path.exists(doc.file_path):
        return RedirectResponse(url="/documents/")
    return FileResponse(doc.file_path, filename=doc.original_filename)
