import os
import json
import logging

from tasks import celery_app
from app.config import Config
from app.extensions import init_db
from app.models import GapReport, GapItem, GapLabel, InterviewText, SupportDoc, Status
from app.util import db_session
from app.storage_backend import StorageBackend
from tasks.azure_agent import run_gap_analysis_agent
from tasks.report_excel import generate_gap_report_excel

log = logging.getLogger(__name__)


@celery_app.task
def gap_analysis_task(report_id: int):
    """Run NLI gap analysis using Azure OpenAI agent, save results, generate Excel."""
    cfg = Config()
    init_db(cfg.DATABASE_URL)
    storage = StorageBackend(cfg.STORAGE_ROOT)

    with db_session() as db:
        report = db.get(GapReport, report_id)
        if not report:
            log.error("GapReport %s not found", report_id)
            return
        report.status = Status.PROCESSING

        interview_id = report.interview_id
        doc_id = report.doc_id

        itext = (
            db.query(InterviewText)
            .filter(InterviewText.interview_id == interview_id)
            .order_by(InterviewText.created_at.desc())
            .first()
        )
        doc = db.get(SupportDoc, doc_id)

        transcript_en = (itext.transcript_en or "").strip() if itext else ""
        segments_json = (itext.segments_json or {}).get("segments", []) if itext else []
        doc_title = doc.title if doc else "Unknown"
        doc_text = (doc.extracted_text_en or "").strip() if doc else ""

    if not transcript_en or not doc_text:
        log.warning("Report %s: empty transcript or doc text", report_id)
        with db_session() as db:
            report = db.get(GapReport, report_id)
            if report:
                report.status = Status.FAILED
                report.summary_json = {"error": "Empty transcript or document text"}
        return

    try:
        log.info("Running gap analysis agent for report %s", report_id)
        result = run_gap_analysis_agent(transcript_en, segments_json, doc_title, doc_text)

        gap_items = result.get("gap_analysis", [])
        out_of_scope = result.get("out_of_scope", [])

        supported = sum(1 for i in gap_items if i.get("label") == "SUPPORTED")
        contradicted = sum(1 for i in gap_items if i.get("label") == "CONTRADICTED")
        unknown = sum(1 for i in gap_items if i.get("label") == "UNKNOWN")
        summary = {
            "total_claims": len(gap_items),
            "supported": supported,
            "contradicted": contradicted,
            "unknown": unknown,
            "out_of_scope_filtered": len(out_of_scope),
        }

        report_data = {
            "gap_analysis": gap_items,
            "out_of_scope": out_of_scope,
            "summary": summary,
        }

        excel_key = f"reports/gap_report_{report_id}.xlsx"
        excel_path = storage.resolve_path(excel_key)
        os.makedirs(os.path.dirname(excel_path), exist_ok=True)
        generate_gap_report_excel(excel_path, report_data, report_id)

        with db_session() as db:
            report = db.get(GapReport, report_id)
            if not report:
                return

            db.query(GapItem).filter(GapItem.report_id == report_id).delete()

            for item in gap_items:
                label_str = item.get("label", "UNKNOWN").upper()
                try:
                    label = GapLabel(label_str)
                except ValueError:
                    label = GapLabel.UNKNOWN

                db.add(GapItem(
                    report_id=report_id,
                    claim_text=item.get("claim", ""),
                    label=label,
                    interview_evidence=item.get("interview_evidence", ""),
                    doc_evidence=item.get("doc_evidence", ""),
                    confidence=item.get("confidence", "Low"),
                    action_suggestion=item.get("action_suggestion", ""),
                ))

            report.report_json = report_data
            report.summary_json = summary
            report.report_storage_key = excel_key
            report.status = Status.READY

        log.info("Report %s ready (%d claims, %d out-of-scope)",
                 report_id, len(gap_items), len(out_of_scope))

    except Exception:
        log.exception("Gap analysis failed for report %s", report_id)
        with db_session() as db:
            report = db.get(GapReport, report_id)
            if report:
                report.status = Status.FAILED
