from sqlalchemy import select, desc

from app.models import Interview, SupportDoc, Status, GapReport, GapItem, AuditLog

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_file, abort
from flask_login import login_required, current_user
import json, os, time
from app.util import db_session
from app.models import Interview, SupportDoc, GapReport, Status

bp = Blueprint("gap", __name__, url_prefix="/gap")

@bp.get("/new")
@login_required
def new_gap():
    uid = int(current_user.get_id())

    with db_session() as db:
        interviews = (db.query(Interview)
                      .filter(Interview.created_by == uid)
                      .filter(Interview.status == Status.READY)
                      .order_by(Interview.created_at.desc())
                      .all())

        docs = (db.query(SupportDoc)
                .filter(SupportDoc.created_by == uid)
                .filter(SupportDoc.status == Status.READY)
                .order_by(SupportDoc.created_at.desc())
                .all())

    return render_template("gap_analysis/new.html", interviews=interviews, docs=docs)


@bp.post("/run")
@login_required
def run_gap():
    uid = int(current_user._user_id)
    interview_id = int(request.form.get("interview_id"))
    doc_id = int(request.form.get("doc_id"))

    # Demo “heavy processing”
    time.sleep(2)

    # Static demo rows in your desired format
    rows = [
        {
            "claim": "The system uses AES-256 encryption.",
            "label": "SUPPORTED",
            "interview_evidence": '00:12 – Speaker A: "We use AES-256 encryption for data security."',
            "doc_evidence": 'Section 2.1: "Data is encrypted using AES-256."',
            "confidence": "High",
            "action": "",
            "suggestion": "Add to documentation"
        },
        {
            "claim": "Backups are performed daily.",
            "label": "CONTRADICTED",
            "interview_evidence": '05:45 – Speaker B: "We perform backups every day."',
            "doc_evidence": 'Para 3.4: "Backups are conducted weekly."',
            "confidence": "Medium",
            "action": "",
            "suggestion": "Review contradiction"
        },
        {
            "claim": "AI model is trained on latest data.",
            "label": "UNKNOWN",
            "interview_evidence": '09:10 – Speaker C: "The model is regularly updated."',
            "doc_evidence": "No mention of training data in the document.",
            "confidence": "Low",
            "action": "",
            "suggestion": "Investigate further"
        },
    ]

    with db_session() as db:
        rpt = GapReport(
            created_by=uid,
            interview_id=interview_id,
            doc_id=doc_id,
            status=Status.READY,
            report_json=json.dumps(rows),
        )
        db.add(rpt)
        db.flush()

        # Write downloadable HTML artifact (storage key)
        key = f"reports/gap/{rpt.id}.html"
        html = render_template("gap/report_download.html", rows=rows, report_id=rpt.id)
        current_app.storage.save_text(key, html)
        rpt.report_storage_key = key

    flash("Gap report generated (demo mode).", "success")
    return redirect(url_for("gap.view_report", report_id=rpt.id))


# @bp.post("/run")
# @login_required
# def run():
#     form = NewGapReportForm()
#     with db_session() as db:
#         interviews = db.execute(select(Interview).where(Interview.status == Status.READY)).scalars().all()
#         docs = db.execute(select(SupportDoc).where(SupportDoc.status == Status.READY)).scalars().all()
#     form.interview_id.choices = [(i.id, str(i.id)) for i in interviews]
#     form.support_doc_ids.choices = [(d.id, str(d.id)) for d in docs]
#
#     if not form.validate_on_submit():
#         flash("Invalid selection", "danger")
#         return redirect(url_for("gap_analysis.new"))
#
#     selected_docs = form.support_doc_ids.data[:3]
#     if len(selected_docs) > 3:
#         flash("Please select up to 3 supporting docs", "warning")
#         return redirect(url_for("gap_analysis.new"))
#
#     with db_session() as db:
#         report = GapReport(
#             interview_id=form.interview_id.data,
#             support_doc_ids_json={"doc_ids": selected_docs},
#             created_by=int(current_user.get_id())
#         )
#         db.add(report)
#         db.flush()
#         db.add(AuditLog(
#             user_id=report.created_by,
#             action="RUN_GAP",
#             entity_type="GAP_REPORT",
#             entity_id=report.id,
#             meta_json={"interview_id": report.interview_id, "doc_ids": selected_docs}
#         ))
#
#         gap_analysis_task.delay(report.id)
#
#     flash("Gap analysis started. Refresh the report page in a moment.", "success")
#     return redirect(url_for("gap_analysis.report", report_id=report.id))

@bp.get("/report/<int:report_id>")
@login_required
def view_report(report_id: int):
    uid = int(current_user._user_id)
    with db_session() as db:
        rpt = db.get(GapReport, report_id)
        if not rpt:
            abort(404)
        if rpt.created_by != uid:
            abort(403)
        rows = json.loads(rpt.report_json or "[]")
    return render_template("gap/report_view.html", rpt=rpt, rows=rows)


# @bp.get("/report/<int:report_id>")
# @login_required
# def report(report_id: int):
#     with db_session() as db:
#         report = db.get(GapReport, report_id)
#         items = db.execute(select(GapItem).where(GapItem.report_id == report_id).order_by(GapItem.id)).scalars().all()
#         return render_template("gap_analysis/report.html", report=report, items=items)

@bp.get("/download/<int:report_id>")
@login_required
def download_report(report_id: int):
    uid = int(current_user._user_id)
    with db_session() as db:
        rpt = db.get(GapReport, report_id)
        if not rpt:
            abort(404)
        if rpt.created_by != uid:
            abort(403)
        key = rpt.report_storage_key

    path = current_app.storage.resolve_path(key)
    if not os.path.exists(path):
        abort(404)

    return send_file(path, as_attachment=True, download_name=f"gap_report_{report_id}.html")

@bp.get("/history")
@login_required
def history():
    uid = int(current_user._user_id)
    with db_session() as db:
        reports = (db.query(GapReport)
                   .filter(GapReport.created_by == uid)
                   .order_by(GapReport.created_at.desc())
                   .all())
    return render_template("gap/history.html", reports=reports)


# @bp.get("/history")
# @login_required
# def history():
#     with db_session() as db:
#         reports = db.execute(select(GapReport).order_by(desc(GapReport.created_at))).scalars().all()
#         return render_template("gap_analysis/history.html", reports=reports)
