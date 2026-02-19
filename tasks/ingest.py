import os
from typing import Tuple, Dict, Optional

from tasks import celery_app
from app.config import Config
from app.extensions import init_db
from app.models import Interview, InterviewText, SupportDoc, Status
from app.util import db_session
from app.storage_backend import StorageBackend

# ---------- Hugging Face / Model config ----------
ASR_MODEL_SIZE = os.environ.get("ASR_MODEL_SIZE", "small")  # small/medium/large-v3 later
NLLB_MODEL = os.environ.get("NLLB_MODEL", "facebook/nllb-200-distilled-600M")

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
DEVICE = os.environ.get("MODEL_DEVICE", "cpu")  # later: "cuda"

# ---------- Global model singletons ----------
_whisper = None
_translator = None
_translator_tokenizer = None

def _init():
    cfg = Config()
    init_db(cfg.DATABASE_URL)
    storage = StorageBackend(cfg.STORAGE_ROOT)
    return cfg, storage

# ---------- ASR (faster-whisper) ----------
def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        # compute_type could be "int8" on CPU for speed
        compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
        _whisper = WhisperModel(ASR_MODEL_SIZE, device=DEVICE, compute_type=compute_type)
    return _whisper

def asr_audio(audio_path: str) -> Tuple[str, Dict]:
    model = _get_whisper()
    segments, info = model.transcribe(audio_path, vad_filter=True)
    texts = []
    segs = []
    for s in segments:
        texts.append(s.text)
        segs.append({"start": s.start, "end": s.end, "text": s.text})
    transcript = " ".join(texts).strip()
    return transcript, {"language": getattr(info, "language", None), "segments": segs}

# ---------- Translation (NLLB) ----------
def _get_translator():
    global _translator, _translator_tokenizer
    if _translator is None or _translator_tokenizer is None:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

        _translator_tokenizer = AutoTokenizer.from_pretrained(NLLB_MODEL, token=HF_TOKEN)
        _translator = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL, token=HF_TOKEN)
        _translator.eval()
    return _translator, _translator_tokenizer

def translate_fi_to_en(text_fi: str) -> str:
    if not text_fi.strip():
        return ""

    model, tok = _get_translator()

    # NLLB language tags
    src_lang = "fin_Latn"
    tgt_lang = "eng_Latn"
    tok.src_lang = src_lang

    # Chunk long text to avoid max length issues
    max_chars = 2500
    chunks = [text_fi[i:i+max_chars] for i in range(0, len(text_fi), max_chars)]

    out_parts = []
    import torch

    for ch in chunks:
        inputs = tok(ch, return_tensors="pt", truncation=True)
        forced_bos_token_id = tok.convert_tokens_to_ids(tgt_lang)

        with torch.no_grad():
            generated = model.generate(
                **inputs,
                forced_bos_token_id=forced_bos_token_id,
                max_new_tokens=512,
            )

        out_parts.append(tok.batch_decode(generated, skip_special_tokens=True)[0])

    return "\n".join(out_parts).strip()

# ---------- Doc extraction (still stub for now) ----------
def extract_doc_text(file_path: str) -> str:
    # We'll implement PDF/DOCX/HTML next phase; for now basic placeholder
    # (You can at least read .txt)
    if file_path.lower().endswith(".txt"):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    return f"TODO: Extract text from {os.path.basename(file_path)}"

# ---------- Celery tasks ----------
@celery_app.task
def ingest_audio_task(interview_id: int):
    cfg, storage = _init()

    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview:
            return
        interview.status = Status.PROCESSING
        is_fi = bool(getattr(interview, "is_finnish", False))
        audio_key = interview.audio_storage_key

    audio_path = storage.resolve_path(audio_key)
    transcript_raw, segments = asr_audio(audio_path)

    if is_fi:
        transcript_en = translate_fi_to_en(transcript_raw)
        transcript_fi = transcript_raw
        model_translation = NLLB_MODEL
    else:
        transcript_en = transcript_raw
        transcript_fi = None
        model_translation = None

    transcript_key = f"uploads/audio/{interview_id}/transcript_en.txt"
    storage.save_text(transcript_key, transcript_en)

    with db_session() as db:
        interview = db.get(Interview, interview_id)
        if not interview:
            return

        db.add(InterviewText(
            interview_id=interview_id,
            transcript_fi=transcript_fi,
            transcript_en=transcript_en,
            transcript_en_storage_key=transcript_key,
            model_asr=f"faster-whisper:{ASR_MODEL_SIZE}",
            model_translation=model_translation,
            segments_json=segments,
        ))
        interview.status = Status.READY


@celery_app.task
def ingest_doc_task(doc_id: int):
    cfg, storage = _init()

    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            return
        doc.status = Status.PROCESSING
        is_fi = bool(getattr(doc, "is_finnish", False))
        doc_key = doc.file_storage_key

    file_path = storage.resolve_path(doc_key)
    extracted_text = extract_doc_text(file_path).strip()

    text_en = translate_fi_to_en(extracted_text) if is_fi else extracted_text

    text_key = f"uploads/docs/{doc_id}/text_en.txt"
    storage.save_text(text_key, text_en)

    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            return
        doc.extracted_text_en = text_en
        doc.extracted_text_en_storage_key = text_key
        doc.status = Status.READY
