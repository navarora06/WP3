from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import select, desc



from app.admin_upload.forms import UploadAudioForm, UploadDocForm
from app.admin_upload.services import create_interview, create_support_doc, audit
from app.models import Interview, SupportDoc, AuditLog, InterviewText
from app.util import db_session

import os
from werkzeug.utils import secure_filename
from flask import current_app, abort, send_file
from app.models import Status

from tasks.ingest import ingest_audio_task, ingest_doc_task

bp = Blueprint("admin_upload", __name__, url_prefix="/admin")

@bp.post("/delete/interview/<int:interview_id>")
@login_required
def delete_interview(interview_id: int):
    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview:
            abort(404)
        if interview.created_by != int(current_user.get_id()):
            abort(403)

        # delete file
        try:
            if interview.audio_file_path and os.path.exists(interview.audio_file_path):
                os.remove(interview.audio_file_path)
        except Exception:
            pass

        # delete dependent texts
        db.query(InterviewText).filter(InterviewText.interview_id == interview_id).delete()
        db.delete(interview)

        audit(db, int(current_user.get_id()), "DELETE_AUDIO", "INTERVIEW", interview_id, None)

    flash("Interview deleted.", "success")
    return redirect(url_for("admin_upload.history_uploads"))

@bp.post("/delete/doc/<int:doc_id>")
@login_required
def delete_doc(doc_id: int):
    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            abort(404)
        if doc.created_by != int(current_user.get_id()):
            abort(403)

        try:
            if doc.file_path and os.path.exists(doc.file_path):
                os.remove(doc.file_path)
        except Exception:
            pass

        db.delete(doc)
        audit(db, int(current_user.get_id()), "DELETE_DOC", "SUPPORT_DOC", doc_id, None)

    flash("Document deleted.", "success")
    return redirect(url_for("admin_upload.history_uploads"))

@bp.get("/upload")
@login_required
def index():
    return render_template(
        "admin_upload/index.html",
        audio_form=UploadAudioForm(),
        doc_form=UploadDocForm()
    )

@bp.post("/upload/audio")
@login_required
def upload_audio():
    form = UploadAudioForm()
    if not form.validate_on_submit():
        for field, errs in form.errors.items():
            for e in errs:
                flash(f"{field}: {e}", "danger")
        return redirect(url_for("admin_upload.index"))

    fs = form.audio.data
    safe = secure_filename(fs.filename or "audio.bin")

    with db_session() as db:
        interview = Interview(
            title=form.title.data,
            company_domain=form.company_domain.data,
            created_by=int(current_user.get_id()),
            is_finnish=bool(form.is_finnish.data),
            status=Status.UPLOADED,
            audio_storage_key="__pending__",
        )
        db.add(interview)
        db.flush()

        key = f"uploads/audio/{interview.id}/original_{safe}"
        current_app.storage.save_upload(key, fs)
        interview.audio_storage_key = key

        audit(db, interview.created_by, "UPLOAD_AUDIO", "INTERVIEW", interview.id,
              {"title": interview.title, "is_finnish": interview.is_finnish, "audio_key": key})

        ingest_audio_task.delay(interview.id)

    flash("Audio uploaded. Processing started.", "success")
    return redirect(url_for("admin_upload.detail_interview", interview_id=interview.id))

@bp.post("/upload/doc")
@login_required
def upload_doc():
    form = UploadDocForm()
    if not form.validate_on_submit():
        for field, errs in form.errors.items():
            for e in errs:
                flash(f"{field}: {e}", "danger")
        return redirect(url_for("admin_upload.index"))

    fs = form.doc.data
    safe = secure_filename(fs.filename or "doc.bin")

    with db_session() as db:
        doc = SupportDoc(
            title=form.title.data,
            created_by=int(current_user.get_id()),
            is_finnish=bool(form.is_finnish.data),
            status=Status.UPLOADED,
            file_storage_key="__pending__",
        )
        db.add(doc)
        db.flush()

        key = f"uploads/docs/{doc.id}/original_{safe}"
        current_app.storage.save_upload(key, fs)
        doc.file_storage_key = key

        audit(db, doc.created_by, "UPLOAD_DOC", "SUPPORT_DOC", doc.id,
              {"title": doc.title, "is_finnish": doc.is_finnish, "doc_key": key})

        ingest_doc_task.delay(doc.id)

    flash("Document uploaded. Processing started.", "success")
    return redirect(url_for("admin_upload.history_uploads"))

@bp.get("/interview/<int:interview_id>")
@login_required
def detail_interview(interview_id: int):
    with db_session() as db:
        interview = db.get(Interview, interview_id)
        return render_template("admin_upload/detail.html", interview=interview)

@bp.get("/history/uploads")
@login_required
def history_uploads():
    uid = int(current_user.get_id())
    with db_session() as db:
        interviews = db.execute(
            select(Interview).where(Interview.created_by == uid).order_by(desc(Interview.created_at))
        ).scalars().all()
        docs = db.execute(
            select(SupportDoc).where(SupportDoc.created_by == uid).order_by(desc(SupportDoc.created_at))
        ).scalars().all()
    return render_template("admin_upload/history.html", interviews=interviews, docs=docs)

@bp.get("/download/interview/<int:interview_id>/transcript")
@login_required
def download_interview_transcript(interview_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview:
            abort(404)
        if interview.created_by != uid:
            abort(403)

        itext = (db.query(InterviewText)
                 .filter(InterviewText.interview_id == interview_id)
                 .order_by(InterviewText.created_at.desc())
                 .first())
        if not itext or not itext.transcript_en_storage_key:
            abort(404)
        key = itext.transcript_en_storage_key

    path = current_app.storage.resolve_path(key)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"interview_{interview_id}_transcript_en.txt")

@bp.get("/download/doc/<int:doc_id>/text")
@login_required
def download_doc_text(doc_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            abort(404)
        if doc.created_by != uid:
            abort(403)
        if not doc.extracted_text_en_storage_key:
            abort(404)
        key = doc.extracted_text_en_storage_key

    path = current_app.storage.resolve_path(key)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"doc_{doc_id}_text_en.txt")
