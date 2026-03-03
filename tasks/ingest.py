import os
import logging

from tasks import celery_app
from app.config import Config
from app.extensions import init_db
from app.models import Interview, InterviewText, SupportDoc, Status
from app.util import db_session
from app.storage_backend import StorageBackend
from tasks.azure_agent import (
    transcribe_audio, translate_fi_to_en, translate_segments_fi_to_en,
    resolve_speaker_names, _fmt_ts,
)

log = logging.getLogger(__name__)


def _format_transcript(segments: list[dict]) -> str:
    """Build a human-readable transcript with timestamps and speaker labels."""
    lines = []
    for s in segments:
        ts = _fmt_ts(s["start"])
        speaker = s.get("speaker", "Speaker")
        lines.append(f"[{ts}] {speaker}: {s['text']}")
    return "\n".join(lines)


def _init():
    cfg = Config()
    init_db(cfg.DATABASE_URL)
    storage = StorageBackend(cfg.STORAGE_ROOT)
    return cfg, storage


# ---------- Doc extraction (unchanged – runs locally) ----------

def extract_doc_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        import fitz
        doc = fitz.open(file_path)
        parts = []
        for page in doc:
            txt = page.get_text("text")
            if txt:
                parts.append(txt)
        return "\n".join(parts).strip()

    if ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()

    if ext == ".docx":
        from docx import Document
        d = Document(file_path)
        return "\n".join(p.text for p in d.paragraphs).strip()

    if ext in [".html", ".htm"]:
        from bs4 import BeautifulSoup
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n").strip()

    return ""


# ---------- Celery tasks ----------

@celery_app.task
def ingest_audio_task(interview_id: int):
    """Transcribe audio via Azure Speech, translate if Finnish, save to DB only."""
    cfg, storage = _init()

    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview:
            return
        interview.status = Status.PROCESSING
        is_fi = bool(getattr(interview, "is_finnish", False))
        audio_key = interview.audio_storage_key

    audio_path = storage.resolve_path(audio_key)
    log.info("Transcribing interview %s (%s)", interview_id, audio_path)

    try:
        segments = transcribe_audio(audio_path, is_finnish=is_fi)
        segments = resolve_speaker_names(segments)
        transcript_raw = _format_transcript(segments)

        if is_fi:
            en_segments = translate_segments_fi_to_en(segments)
            transcript_en = _format_transcript(en_segments)
            transcript_fi = transcript_raw
            model_translation = "azure-translator:fi-en"
            stored_segments = en_segments
        else:
            transcript_en = transcript_raw
            transcript_fi = None
            model_translation = None
            stored_segments = segments

        with db_session() as db:
            interview = db.get(Interview, interview_id)
            if not interview:
                return
            db.add(InterviewText(
                interview_id=interview_id,
                transcript_fi=transcript_fi,
                transcript_en=transcript_en,
                model_asr="azure-speech:conversation-transcriber",
                model_translation=model_translation,
                segments_json={"segments": stored_segments},
            ))
            interview.status = Status.READY

        log.info("Interview %s ready", interview_id)

    except Exception:
        log.exception("Failed to process interview %s", interview_id)
        with db_session() as db:
            interview = db.get(Interview, interview_id)
            if interview:
                interview.status = Status.FAILED


@celery_app.task
def ingest_doc_task(doc_id: int):
    """Extract text from Finnish doc, translate via Azure Translator, save to DB only."""
    cfg, storage = _init()

    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            return
        doc.status = Status.PROCESSING
        is_fi = bool(getattr(doc, "is_finnish", False))
        doc_key = doc.file_storage_key

    file_path = storage.resolve_path(doc_key)
    log.info("Processing doc %s (%s)", doc_id, file_path)

    try:
        extracted_text = extract_doc_text(file_path).strip()
        text_en = translate_fi_to_en(extracted_text) if is_fi else extracted_text

        with db_session() as db:
            doc = db.get(SupportDoc, doc_id)
            if not doc:
                return
            doc.extracted_text_en = text_en
            doc.status = Status.READY

        log.info("Doc %s ready", doc_id)

    except Exception:
        log.exception("Failed to process doc %s", doc_id)
        with db_session() as db:
            doc = db.get(SupportDoc, doc_id)
            if doc:
                doc.status = Status.FAILED
