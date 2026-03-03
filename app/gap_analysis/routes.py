import json
import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import select, desc

from app.models import Interview, SupportDoc, GapReport, GapItem, Status
from app.util import db_session
from tasks.gap import gap_analysis_task

bp = Blueprint("gap", __name__, url_prefix="/gap")


@bp.get("/new")
@login_required
def new_gap():
    uid = int(current_user.get_id())

    with db_session() as db:
        interviews = (
            db.query(Interview)
            .filter(Interview.created_by == uid, Interview.status == Status.READY)
            .order_by(Interview.created_at.desc())
            .all()
        )

        docs = (
            db.query(SupportDoc)
            .filter(SupportDoc.created_by == uid, SupportDoc.status == Status.READY)
            .order_by(SupportDoc.created_at.desc())
            .all()
        )

        reports = (
            db.query(GapReport)
            .filter(GapReport.created_by == uid)
            .order_by(GapReport.created_at.desc())
            .all()
        )

        interview_map = {i.id: i.title for i in interviews}
        doc_map = {d.id: d.title for d in docs}
        for r in reports:
            if r.interview_id not in interview_map:
                iv = db.get(Interview, r.interview_id)
                if iv:
                    interview_map[iv.id] = iv.title
            if r.doc_id not in doc_map:
                dc = db.get(SupportDoc, r.doc_id)
                if dc:
                    doc_map[dc.id] = dc.title

    return render_template(
        "gap_analysis/new.html",
        interviews=interviews,
        docs=docs,
        reports=reports,
        interview_map=interview_map,
        doc_map=doc_map,
    )


@bp.post("/run")
@login_required
def run_gap():
    uid = int(current_user.get_id())
    interview_id = request.form.get("interview_id", type=int)
    doc_id = request.form.get("doc_id", type=int)

    if not interview_id or not doc_id:
        flash("Please select both an interview and a supporting document.", "danger")
        return redirect(url_for("gap.new_gap"))

    with db_session() as db:
        interview = db.get(Interview, interview_id)
        doc = db.get(SupportDoc, doc_id)

        if not interview or interview.created_by != uid or interview.status != Status.READY:
            flash("Invalid interview selection.", "danger")
            return redirect(url_for("gap.new_gap"))

        if not doc or doc.created_by != uid or doc.status != Status.READY:
            flash("Invalid document selection.", "danger")
            return redirect(url_for("gap.new_gap"))

        report = GapReport(
            interview_id=interview_id,
            doc_id=doc_id,
            created_by=uid,
            status=Status.PROCESSING,
        )
        db.add(report)
        db.flush()
        report_id = report.id

    gap_analysis_task.delay(report_id)

    flash("Gap analysis started. It will appear below when complete.", "success")
    return redirect(url_for("gap.new_gap"))


@bp.get("/report/<int:report_id>")
@login_required
def view_report(report_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        rpt = db.get(GapReport, report_id)
        if not rpt:
            abort(404)
        if rpt.created_by != uid:
            abort(403)

        items = (
            db.query(GapItem)
            .filter(GapItem.report_id == report_id)
            .order_by(GapItem.id)
            .all()
        )

        rows = []
        for it in items:
            rows.append({
                "claim": it.claim_text,
                "label": it.label.value if it.label else "UNKNOWN",
                "interview_evidence": it.interview_evidence or "",
                "doc_evidence": it.doc_evidence or "",
                "confidence": it.confidence or "Low",
                "action": "",
                "suggestion": it.action_suggestion or "",
            })

        summary = rpt.summary_json or {}
        out_of_scope = (rpt.report_json or {}).get("out_of_scope", [])

    return render_template(
        "gap_analysis/report_view.html",
        rpt=rpt,
        rows=rows,
        summary=summary,
        out_of_scope=out_of_scope,
    )


@bp.get("/download/<int:report_id>")
@login_required
def download_report(report_id: int):
    uid = int(current_user.get_id())
    with db_session() as db:
        rpt = db.get(GapReport, report_id)
        if not rpt:
            abort(404)
        if rpt.created_by != uid:
            abort(403)
        key = rpt.report_storage_key

    if not key:
        abort(404)

    path = current_app.storage.resolve_path(key)
    if not os.path.exists(path):
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"gap_report_{report_id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.get("/history")
@login_required
def history():
    uid = int(current_user.get_id())
    with db_session() as db:
        reports = (
            db.query(GapReport)
            .filter(GapReport.created_by == uid)
            .order_by(GapReport.created_at.desc())
            .all()
        )
        interview_map = {}
        doc_map = {}
        for r in reports:
            if r.interview_id not in interview_map:
                iv = db.get(Interview, r.interview_id)
                if iv:
                    interview_map[iv.id] = iv.title
            if r.doc_id not in doc_map:
                dc = db.get(SupportDoc, r.doc_id)
                if dc:
                    doc_map[dc.id] = dc.title

    return render_template(
        "gap_analysis/history.html",
        reports=reports,
        interview_map=interview_map,
        doc_map=doc_map,
    )
