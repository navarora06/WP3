import os
from tasks import celery_app
from app.config import Config
from app.extensions import init_db
from app.models import GapReport, GapItem, GapStatus, GapLabel, InterviewText, SupportDoc
from app.util import db_session
from tasks.lightrag_client import lightrag_query
from tasks.pdf import render_gap_report_pdf

def extract_claims_from_transcript(transcript_en: str, max_claims: int = 15):
    sentences = [s.strip() for s in transcript_en.split(".") if s.strip()]
    return [{"text": s + ".", "refs": {}} for s in sentences[:max_claims]]

def classify_claim_with_evidence(claim: str, evidence: list[dict]):
    if not evidence:
        return GapLabel.UNKNOWN, 0.2, "No supporting evidence retrieved.", {"evidence": []}
    ev_text = " ".join([e.get("text", "") for e in evidence]).lower()
    overlap = sum(1 for w in claim.lower().split() if w in ev_text)
    if overlap > 3:
        return GapLabel.SUPPORTED, 0.6, "Evidence appears to support the claim (heuristic).", {"evidence": evidence}
    return GapLabel.UNKNOWN, 0.4, "Evidence retrieved but not clearly supporting (heuristic).", {"evidence": evidence}

@celery_app.task
def gap_analysis_task(report_id: int):
    cfg = Config()
    init_db(cfg.DATABASE_URL)

    with db_session() as db:
        report = db.get(GapReport, report_id)
        report.status = GapStatus.RUNNING
        interview_id = report.interview_id
        doc_ids = report.support_doc_ids_json.get("doc_ids", [])

        itext = (
            db.query(InterviewText)
            .filter(InterviewText.interview_id == interview_id)
            .order_by(InterviewText.created_at.desc())
            .first()
        )
        docs = db.query(SupportDoc).filter(SupportDoc.id.in_(doc_ids)).all()

        transcript_en = (itext.transcript_en or "").strip() if itext else ""
        _ = {d.id: (d.extracted_text_en or "") for d in docs}

    claims = extract_claims_from_transcript(transcript_en, max_claims=20)

    supported = contradicted = unknown = 0
    stored_items = []

    for c in claims:
        claim_text = c["text"]
        res = lightrag_query(query=claim_text, filters={"doc_ids": doc_ids}, top_k=5)
        evidence = res.get("results", [])

        label, conf, rationale, ev_json = classify_claim_with_evidence(claim_text, evidence)

        if label == GapLabel.SUPPORTED:
            supported += 1
        elif label == GapLabel.CONTRADICTED:
            contradicted += 1
        else:
            unknown += 1

        stored_items.append({
            "claim": claim_text,
            "label": label.value,
            "confidence": conf,
            "rationale": rationale,
            "claim_refs": c.get("refs", {}),
            "doc_evidence": ev_json,
        })

    summary = {
        "total_claims": len(stored_items),
        "supported": supported,
        "contradicted": contradicted,
        "unknown": unknown,
    }

    with db_session() as db:
        report = db.get(GapReport, report_id)

        db.query(GapItem).filter(GapItem.report_id == report_id).delete()

        for it in stored_items:
            db.add(GapItem(
                report_id=report_id,
                claim_text=it["claim"],
                claim_refs_json=it["claim_refs"],
                label=GapLabel(it["label"]),
                confidence=float(it["confidence"]),
                rationale=it["rationale"],
                doc_evidence_json=it["doc_evidence"],
            ))

        report.summary_json = summary

        pdf_name = f"gap_report_{report_id}.pdf"
        pdf_path = os.path.join(cfg.STORAGE_ROOT, "reports", pdf_name)
        render_gap_report_pdf(
            output_path=pdf_path,
            title=f"Knowledge Gap Report #{report_id}",
            summary=summary,
            items=stored_items,
        )
        report.pdf_path = pdf_path
        report.status = GapStatus.READY
