import os
from io import BytesIO

from flask import Blueprint, render_template, redirect, url_for, flash, current_app, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import select, desc
from werkzeug.utils import secure_filename

from app.admin_upload.forms import UploadAudioForm, UploadDocForm
from app.admin_upload.services import audit
from app.models import Interview, SupportDoc, InterviewText, Status
from app.util import db_session, text_to_pdf_bytes, safe_filename

from tasks.ingest import ingest_audio_task, ingest_doc_task, extract_doc_text


bp = Blueprint("admin_upload", __name__, url_prefix="/admin")


# ----------------------------
# Pages
# ----------------------------
@bp.get("/upload")
@login_required
def index():
    return render_template(
        "admin_upload/index.html",
        audio_form=UploadAudioForm(),
        doc_form=UploadDocForm(),
    )


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


@bp.get("/interview/<int:interview_id>")
@login_required
def detail_interview(interview_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview or interview.created_by != uid:
            abort(404)

    return render_template("admin_upload/detail.html", interview=interview)


# ----------------------------
# Upload handlers
# ----------------------------
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

        audit(
            db,
            interview.created_by,
            "UPLOAD_AUDIO",
            "INTERVIEW",
            interview.id,
            {"title": interview.title, "is_finnish": interview.is_finnish, "audio_key": key},
        )

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

        audit(
            db,
            doc.created_by,
            "UPLOAD_DOC",
            "SUPPORT_DOC",
            doc.id,
            {"title": doc.title, "is_finnish": doc.is_finnish, "doc_key": key},
        )

        if not doc.is_finnish:
            file_path = current_app.storage.resolve_path(doc.file_storage_key)
            extracted_text = extract_doc_text(file_path).strip()
            doc.extracted_text_en = extracted_text
            doc.status = Status.READY

            audit(
                db,
                doc.created_by,
                "DOC_READY",
                "SUPPORT_DOC",
                doc.id,
                {"status": "READY"},
            )
        else:
            ingest_doc_task.delay(doc.id)

    flash("Document uploaded.", "success")
    return redirect(url_for("admin_upload.history_uploads"))


# ----------------------------
# Delete handlers
# ----------------------------
@bp.post("/delete/interview/<int:interview_id>")
@login_required
def delete_interview(interview_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview:
            abort(404)
        if interview.created_by != uid:
            abort(403)

        try:
            if interview.audio_storage_key:
                path = current_app.storage.resolve_path(interview.audio_storage_key)
                if os.path.exists(path):
                    os.remove(path)
        except Exception:
            pass

        db.query(InterviewText).filter(InterviewText.interview_id == interview_id).delete()
        db.delete(interview)

        audit(db, uid, "DELETE_AUDIO", "INTERVIEW", interview_id, None)

    flash("Interview deleted.", "success")
    return redirect(url_for("admin_upload.history_uploads"))


@bp.post("/delete/doc/<int:doc_id>")
@login_required
def delete_doc(doc_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            abort(404)
        if doc.created_by != uid:
            abort(403)

        try:
            if doc.file_storage_key:
                path = current_app.storage.resolve_path(doc.file_storage_key)
                if os.path.exists(path):
                    os.remove(path)
        except Exception:
            pass

        db.delete(doc)
        audit(db, uid, "DELETE_DOC", "SUPPORT_DOC", doc_id, None)

    flash("Document deleted.", "success")
    return redirect(url_for("admin_upload.history_uploads"))


# ----------------------------
# Download / View
# ----------------------------
@bp.get("/download/interview/<int:interview_id>/transcript")
@login_required
def download_interview_transcript(interview_id: int):
    uid = int(current_user.get_id())

    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview or interview.created_by != uid:
            abort(404)

        title = interview.title

        itext = (db.query(InterviewText)
                 .filter(InterviewText.interview_id == interview_id)
                 .order_by(InterviewText.created_at.desc())
                 .first())
        if not itext:
            abort(404)

        text = itext.transcript_en or itext.transcript_fi or ""

    pdf_bytes = text_to_pdf_bytes(f"{title} — Transcript (EN)", text)
    bio = BytesIO(pdf_bytes)
    bio.seek(0)

    filename = safe_filename(title) + "_transcript_en.pdf"
    return send_file(bio, as_attachment=True, download_name=filename, mimetype="application/pdf")


@bp.get("/download/doc/<int:doc_id>/pdf")
@login_required
def download_doc_pdf(doc_id: int):
    """English docs: serve original file (preserves formatting/images).
    Finnish docs: serve PDF generated from translated English text.
    """
    uid = int(current_user.get_id())

    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc or doc.created_by != uid:
            abort(404)

        title = doc.title
        is_finnish = doc.is_finnish
        file_key = doc.file_storage_key
        translated_text = doc.extracted_text_en or ""

    if is_finnish:
        if not translated_text:
            abort(404)
        pdf_bytes = text_to_pdf_bytes(f"{title} (EN)", translated_text)
        bio = BytesIO(pdf_bytes)
        bio.seek(0)
        filename = safe_filename(title) + "_en.pdf"
        return send_file(bio, as_attachment=True, download_name=filename, mimetype="application/pdf")

    if not file_key:
        abort(404)

    file_path = current_app.storage.resolve_path(file_key)
    if not os.path.exists(file_path):
        abort(404)

    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
    }
    mimetype = mime_map.get(ext, "application/octet-stream")
    download_name = safe_filename(title) + ext

    return send_file(file_path, as_attachment=True, download_name=download_name, mimetype=mimetype)


@bp.get("/history/interview/<int:interview_id>/view")
@login_required
def view_interview_text(interview_id: int):
    uid = int(current_user.get_id())

    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview or interview.created_by != uid:
            abort(404)

        t = (db.query(InterviewText)
             .filter(InterviewText.interview_id == interview_id)
             .order_by(InterviewText.created_at.desc())
             .first())
        if not t:
            abort(404)

        text = t.transcript_en or t.transcript_fi or ""

    return render_template(
        "admin_upload/view.html",
        title=f"Interview #{interview_id} — Transcript (EN)",
        text=text,
    )


@bp.get("/history/doc/<int:doc_id>/view")
@login_required
def view_doc_text(doc_id: int):
    uid = int(current_user.get_id())

    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc or doc.created_by != uid:
            abort(404)

        text = doc.extracted_text_en or ""
        title = doc.title

    return render_template(
        "admin_upload/view.html",
        title=f"{title} — Extracted Text (EN)",
        text=text,
    )
