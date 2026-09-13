"""
Shared receipt/document upload logic.

save_uploaded_file() writes an UploadFile to storage/receipts/<record_type>/<id>/
and creates the corresponding Document row. Used by the Documents module's own
upload route and by the Purchases module, which attaches a receipt in the same
request that creates the purchase.

Caller must db.commit() after this returns.
"""
import os
import shutil
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document import Document
from app.config import STORAGE_DIR


def save_uploaded_file(
    *,
    linked_record_type: str,
    record_id: int,
    file,
    uploaded_by: Optional[str] = None,
    notes: Optional[str] = None,
    db: Session,
) -> Document:
    dest_dir = os.path.join(STORAGE_DIR, linked_record_type, str(record_id))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, file.filename)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    ext = os.path.splitext(file.filename)[1].lower()
    doc = Document(
        linked_record_type=linked_record_type,
        linked_record_id=record_id,
        file_path=dest_path,
        original_filename=file.filename,
        file_type=ext,
        uploaded_by=uploaded_by or None,
        notes=notes or None,
    )
    db.add(doc)
    db.flush()
    doc.document_id = f"D-{doc.id:04d}"
    return doc
