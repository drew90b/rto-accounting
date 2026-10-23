"""
Shared receipt/document upload logic.

save_uploaded_file() writes an UploadFile to storage/receipts/<record_type>/<id>/
and creates the corresponding Document row. Files are stored under a random
name (never the uploaded one); the original name is kept for display only. Used by the Documents module's own
upload route and by the Purchases module, which attaches a receipt in the same
request that creates the purchase.

Caller must db.commit() after this returns.
"""
import os
import shutil
import uuid
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
    # The uploaded name is untrusted: it can contain "../" and two uploads can
    # share a name. Store under a random name; original_filename keeps the
    # user's name for display and download.
    original_name = os.path.basename(file.filename.replace("\\", "/"))
    ext = os.path.splitext(original_name)[1].lower()
    dest_path = os.path.join(dest_dir, f"{uuid.uuid4().hex}{ext}")
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = Document(
        linked_record_type=linked_record_type,
        linked_record_id=record_id,
        file_path=dest_path,
        original_filename=original_name,
        file_type=ext,
        uploaded_by=uploaded_by or None,
        notes=notes or None,
    )
    db.add(doc)
    db.flush()
    doc.document_id = f"D-{doc.id:04d}"
    return doc
