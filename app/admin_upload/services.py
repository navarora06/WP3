import os
import uuid
from app.models import Interview, SupportDoc, Status, AuditLog

def save_upload(file_storage, target_dir: str) -> str:
    ext = os.path.splitext(file_storage.filename)[1].lower()
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(target_dir, filename)
    file_storage.save(path)
    return path

def audit(db, user_id: int, action: str, entity_type: str, entity_id: int, meta: dict | None = None):
    db.add(AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        meta_json=meta
    ))

def create_interview(db, storage_root: str, title: str, company_domain: str | None, audio_file) -> Interview:
    audio_dir = os.path.join(storage_root, "uploads", "audio")
    path = save_upload(audio_file, audio_dir)
    interview = Interview(
        title=title,
        company_domain=company_domain,
        audio_file_path=path,
        status=Status.UPLOADED,
    )
    db.add(interview)
    db.flush()
    return interview

def create_support_doc(db, storage_root: str, title: str, doc_file) -> SupportDoc:
    docs_dir = os.path.join(storage_root, "uploads", "docs")
    path = save_upload(doc_file, docs_dir)
    doc = SupportDoc(
        title=title,
        file_path=path,
        status=Status.UPLOADED,
    )
    db.add(doc)
    db.flush()
    return doc
