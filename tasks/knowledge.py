"""
Celery task: Create Knowledge pipeline.

When triggered for a reviewed gap report, this task:
1. Loads interview transcript segments, supporting doc text, and gap items
2. Chunks texts (interview by speaker turns, doc by paragraphs ~500 tokens)
3. Embeds text chunks via Azure OpenAI text-embedding-3-small
4. Extracts images from the stored doc file and embeds via Azure AI Vision
5. Upserts all chunks + vectors into Azure AI Search
6. Builds knowledge graph vertices/edges in Cosmos DB Gremlin
7. Marks GapReport.knowledge_created = True
"""

import logging
import os
import re
from io import BytesIO

from tasks import celery_app
from app.config import Config
from app.extensions import init_db
from app.models import GapReport, GapItem, GapLabel, InterviewText, SupportDoc, Interview
from app.util import db_session
from app.storage_backend import StorageBackend
from tasks.embeddings import embed_texts, embed_image, vision_image_text_for_index
from tasks.search_index import ensure_index, upsert_documents, delete_documents_for_report
from tasks.graph import ensure_graph, add_vertex, add_edge, cleanup as gremlin_cleanup

log = logging.getLogger(__name__)

PARAGRAPH_TOKEN_TARGET = 500


def _chunk_by_paragraphs(text: str) -> list[str]:
    """Split text into paragraph-level chunks of roughly PARAGRAPH_TOKEN_TARGET tokens."""
    paragraphs = re.split(r'\n{2,}', text.strip())
    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        tokens = len(para.split())
        if current_len + tokens > PARAGRAPH_TOKEN_TARGET and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = tokens
        else:
            current.append(para)
            current_len += tokens

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _chunk_by_segments(segments: list[dict]) -> list[str]:
    """Chunk interview transcript by speaker turns (segments)."""
    chunks = []
    current = []
    current_len = 0

    for seg in segments:
        text = seg.get("text", "").strip()
        speaker = seg.get("speaker", "")
        ts = seg.get("offset_formatted", "")
        if not text:
            continue

        line = f"[{ts}] {speaker}: {text}" if ts else f"{speaker}: {text}"
        tokens = len(text.split())

        if current_len + tokens > PARAGRAPH_TOKEN_TARGET and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = tokens
        else:
            current.append(line)
            current_len += tokens

    if current:
        chunks.append("\n".join(current))
    return chunks


def _page_text_blocks_fitz(page) -> list[tuple[float, float, str]]:
    """(y0, y1, text) for each text block on a PDF page (PyMuPDF)."""
    blocks: list[tuple[float, float, str]] = []
    try:
        d = page.get_text("dict")
    except Exception:
        return blocks
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        bbox = b.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        y0, y1 = float(bbox[1]), float(bbox[3])
        line_parts: list[str] = []
        for line in b.get("lines", []):
            span_txt = "".join(
                (s.get("text") or s.get("c") or "") for s in line.get("spans", [])
            )
            if span_txt.strip():
                line_parts.append(span_txt.strip())
        if line_parts:
            blocks.append((y0, y1, " ".join(line_parts)))
    return blocks


def _text_above_image_on_page(
    page, xref: int, text_blocks: list[tuple[float, float, str]], fallback_page_text: str
) -> str:
    """Use layout: take text blocks ending above the figure (manual headings / captions)."""
    img_y0 = None
    try:
        rects = page.get_image_rects(xref)
        if rects:
            img_y0 = float(rects[0].y0)
    except Exception:
        pass

    if img_y0 is not None and text_blocks:
        # Blocks whose bottom is at or above the top of the image (PDF y grows downward)
        above = [b for b in text_blocks if b[1] <= img_y0 + 3.0]
        if above:
            above.sort(key=lambda b: b[1], reverse=True)
            parts: list[str] = []
            n = 0
            for _y0, _y1, txt in above:
                if txt and txt not in parts:
                    parts.append(txt)
                    n += len(txt)
                    if n > 480:
                        break
            return " ".join(parts)[:500]

    return (fallback_page_text or "").strip()[:400]


def _extract_images_from_doc(
    storage: StorageBackend, storage_key: str
) -> list[tuple[bytes, str | None, str, int]]:
    """
    Extract images from PDF/DOCX.

    Returns list of (image_bytes, format_hint, section_context, page_number).
    section_context is text from the PDF page placed above the figure when possible
    (so embeddings align with manual headings). page_number is 1-based; 0 = unknown (DOCX).
    """
    out: list[tuple[bytes, str | None, str, int]] = []
    path = storage.resolve_path(storage_key)
    if not os.path.exists(path):
        return out

    lower = storage_key.lower()
    if lower.endswith(".pdf"):
        try:
            import fitz
            doc = fitz.open(path)
            try:
                for pno, page in enumerate(doc):
                    page_num = pno + 1
                    text_blocks = _page_text_blocks_fitz(page)
                    fallback = (page.get_text() or "").strip()
                    for img_info in page.get_images(full=True):
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        if not base_image or not base_image.get("image"):
                            continue
                        ext = base_image.get("ext")
                        if isinstance(ext, bytes):
                            hint = ext.decode("ascii", errors="ignore") or None
                        elif isinstance(ext, str):
                            hint = ext
                        else:
                            hint = None
                        section = _text_above_image_on_page(page, xref, text_blocks, fallback)
                        out.append((base_image["image"], hint, section, page_num))
            finally:
                doc.close()
        except Exception:
            log.warning("Could not extract images from PDF: %s", path)
    elif lower.endswith((".docx", ".doc")):
        try:
            from docx import Document
            doc = Document(path)
            for rel in doc.part.rels.values():
                if "image" in rel.reltype:
                    out.append((rel.target_part.blob, None, "", 0))
        except Exception:
            log.warning("Could not extract images from DOCX: %s", path)

    return out


def _normalize_image_ext(hint: str | None, img_bytes: bytes) -> str:
    if hint:
        h = hint.lower().strip(".")
        if h in ("jpeg", "jpe"):
            h = "jpg"
        if h in ("jpg", "png", "webp", "gif"):
            return h
    try:
        from PIL import Image

        with Image.open(BytesIO(img_bytes)) as im:
            fmt = (im.format or "PNG").lower()
            if fmt in ("jpeg", "jpg"):
                return "jpg"
            if fmt in ("png", "webp", "gif"):
                return fmt
    except Exception:
        pass
    return "png"


def _save_extracted_image(
    storage: StorageBackend,
    doc_id: int,
    report_id: int,
    img_idx: int,
    img_bytes: bytes,
    format_hint: str | None,
) -> str | None:
    """Write extracted bytes under uploads/docs/{doc_id}/ and return storage-relative key."""
    ext = _normalize_image_ext(format_hint, img_bytes)
    rel_key = f"uploads/docs/{doc_id}/extracted_rpt{report_id}_{img_idx}.{ext}"
    try:
        path = storage.resolve_path(rel_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(img_bytes)
        return rel_key
    except Exception:
        log.exception("Failed to save extracted image %s", rel_key)
        return None


@celery_app.task
def create_knowledge_task(report_id: int):
    """Main knowledge creation pipeline."""
    cfg = Config()
    init_db(cfg.DATABASE_URL)
    storage = StorageBackend(cfg.STORAGE_ROOT)

    with db_session() as db:
        report = db.get(GapReport, report_id)
        if not report:
            log.error("GapReport %s not found", report_id)
            return
        if not report.is_reviewed:
            log.error("GapReport %s not reviewed; skipping knowledge creation", report_id)
            return

        interview = db.get(Interview, report.interview_id)
        doc = db.get(SupportDoc, report.doc_id)

        itext = (
            db.query(InterviewText)
            .filter(InterviewText.interview_id == report.interview_id)
            .order_by(InterviewText.created_at.desc())
            .first()
        )

        transcript_en = (itext.transcript_en or "").strip() if itext else ""
        segments = (itext.segments_json or {}).get("segments", []) if itext else []
        doc_text = (doc.extracted_text_en or "").strip() if doc else ""
        doc_storage_key = doc.file_storage_key if doc else ""

        interview_title = interview.title if interview else "Unknown"
        doc_title = doc.title if doc else "Unknown"
        interview_id = report.interview_id
        doc_id = report.doc_id

        add_to_doc_items = (
            db.query(GapItem)
            .filter(
                GapItem.report_id == report_id,
                GapItem.action_suggestion == "Add to documentation",
            )
            .all()
        )
        claim_data = [
            {
                "id": item.id,
                "text": item.claim_text,
                "label": item.label.value if item.label else "UNKNOWN",
                "interview_evidence": item.interview_evidence or "",
                "doc_evidence": item.doc_evidence or "",
            }
            for item in add_to_doc_items
        ]

    log.info("Knowledge pipeline starting for report %s (%d claims to index)",
             report_id, len(claim_data))

    # --- 1. Chunk texts ---
    interview_chunks = _chunk_by_segments(segments) if segments else _chunk_by_paragraphs(transcript_en)
    doc_chunks = _chunk_by_paragraphs(doc_text) if doc_text else []
    claim_texts = [c["text"] for c in claim_data]

    all_texts = interview_chunks + doc_chunks + claim_texts
    if not all_texts:
        log.warning("No text to index for report %s", report_id)
        return

    # --- 2. Embed texts ---
    log.info("Embedding %d text chunks", len(all_texts))
    all_vectors = embed_texts(all_texts)

    iv_vectors = all_vectors[:len(interview_chunks)]
    doc_vectors = all_vectors[len(interview_chunks):len(interview_chunks) + len(doc_chunks)]
    claim_vectors = all_vectors[len(interview_chunks) + len(doc_chunks):]

    # --- 3. Extract, persist, Vision caption/OCR + embeddings (text + image vectors) ---
    image_docs: list[dict] = []
    image_rows: list[dict] = []
    if doc_storage_key:
        images = _extract_images_from_doc(storage, doc_storage_key)
        log.info("Extracted %d images from document", len(images))
        for img_idx, (img_bytes, fmt_hint, section_ctx, page_num) in enumerate(images):
            img_vec = embed_image(img_bytes)
            if not img_vec:
                continue
            vision_txt = vision_image_text_for_index(img_bytes)
            if vision_txt:
                log.info("Image %d: indexed %d chars of caption/OCR", img_idx, len(vision_txt))
            header_lines = [f"[Image {img_idx + 1} from {doc_title}]"]
            if page_num:
                header_lines.append(f"Page {page_num} in the manual.")
            if section_ctx.strip():
                header_lines.append(
                    "Text and headings on the same manual page above this figure: "
                    + section_ctx.strip()[:450]
                )
            header = "\n".join(header_lines)
            content = f"{header}\n{vision_txt}" if vision_txt else header
            storage_key_img = _save_extracted_image(
                storage, doc_id, report_id, img_idx, img_bytes, fmt_hint
            )
            image_rows.append(
                {
                    "img_idx": img_idx,
                    "content": content,
                    "img_vec": img_vec,
                    "image_storage_key": storage_key_img or "",
                }
            )

    if image_rows:
        img_content_vectors = embed_texts([r["content"] for r in image_rows])
        for row, cv in zip(image_rows, img_content_vectors):
            image_docs.append(
                {
                    "id": f"rpt{report_id}_img_{row['img_idx']}",
                    "content": row["content"],
                    "source_type": "document_image",
                    "source_id": doc_id,
                    "report_id": report_id,
                    "chunk_index": row["img_idx"],
                    "image_storage_key": row["image_storage_key"],
                    "content_vector": cv,
                    "image_vector": row["img_vec"],
                }
            )

    # --- 4. Build search documents ---
    search_docs = []

    for idx, (chunk, vec) in enumerate(zip(interview_chunks, iv_vectors)):
        search_docs.append({
            "id": f"rpt{report_id}_iv_{idx}",
            "content": chunk,
            "source_type": "interview",
            "source_id": interview_id,
            "report_id": report_id,
            "chunk_index": idx,
            "image_storage_key": "",
            "content_vector": vec,
            "image_vector": [],
        })

    for idx, (chunk, vec) in enumerate(zip(doc_chunks, doc_vectors)):
        search_docs.append({
            "id": f"rpt{report_id}_doc_{idx}",
            "content": chunk,
            "source_type": "document",
            "source_id": doc_id,
            "report_id": report_id,
            "chunk_index": idx,
            "image_storage_key": "",
            "content_vector": vec,
            "image_vector": [],
        })

    for idx, (claim, vec) in enumerate(zip(claim_data, claim_vectors)):
        search_docs.append({
            "id": f"rpt{report_id}_claim_{claim['id']}",
            "content": claim["text"],
            "source_type": "claim",
            "source_id": claim["id"],
            "report_id": report_id,
            "chunk_index": idx,
            "image_storage_key": "",
            "content_vector": vec,
            "image_vector": [],
        })

    search_docs.extend(image_docs)

    # --- 5. Replace Search rows for this report, then upsert ---
    log.info("Upserting %d documents into Azure AI Search", len(search_docs))
    ensure_index()
    delete_documents_for_report(report_id)
    upsert_documents(search_docs)

    # --- 6. Build knowledge graph ---
    log.info("Building knowledge graph for report %s", report_id)
    ensure_graph()

    iv_vertex_id = f"interview_{interview_id}"
    doc_vertex_id = f"document_{doc_id}"
    add_vertex("interview", iv_vertex_id, {"title": interview_title, "report_id": str(report_id)})
    add_vertex("document", doc_vertex_id, {"title": doc_title, "report_id": str(report_id)})

    for claim in claim_data:
        claim_vertex_id = f"claim_{claim['id']}"
        add_vertex("claim", claim_vertex_id, {
            "text": claim["text"][:200],
            "label": claim["label"],
            "report_id": str(report_id),
        })
        add_edge("has_claim", iv_vertex_id, claim_vertex_id)

        if claim["label"] == "SUPPORTED":
            add_edge("supported_by", claim_vertex_id, doc_vertex_id)
        elif claim["label"] == "CONTRADICTED":
            add_edge("contradicted_by", claim_vertex_id, doc_vertex_id)

    for idx, chunk in enumerate(interview_chunks):
        chunk_id = f"chunk_iv_{report_id}_{idx}"
        add_vertex("chunk", chunk_id, {"text": chunk[:200], "source_type": "interview", "report_id": str(report_id)})
        add_edge("extracted_from", chunk_id, iv_vertex_id)

    for idx, chunk in enumerate(doc_chunks):
        chunk_id = f"chunk_doc_{report_id}_{idx}"
        add_vertex("chunk", chunk_id, {"text": chunk[:200], "source_type": "document", "report_id": str(report_id)})
        add_edge("extracted_from", chunk_id, doc_vertex_id)

    gremlin_cleanup()

    # --- 7. Mark as done ---
    with db_session() as db:
        report = db.get(GapReport, report_id)
        if report:
            report.knowledge_created = True

    log.info("Knowledge creation complete for report %s", report_id)
