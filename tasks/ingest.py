import os
from typing import Tuple, Dict

from tasks import celery_app
from app.config import Config
from app.extensions import init_db
from app.models import Interview, InterviewText, SupportDoc, Status
from app.util import db_session
from app.storage_backend import StorageBackend

# ---------- Model config ----------
# faster-whisper model sizes: tiny, base, small, medium, large-v3
ASR_MODEL_SIZE = os.environ.get("ASR_MODEL_SIZE", "small")

# FI -> EN translation (small + fast)
# Alternatives:
# - "Helsinki-NLP/opus-mt-fi-en" (default, lightweight)
# - "Helsinki-NLP/opus-mt-tc-big-fi-en" (better quality, slower)
FI_EN_MODEL = os.environ.get("FI_EN_MODEL", "Helsinki-NLP/opus-mt-fi-en")

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")

# Keep CPU for now unless you explicitly set MODEL_DEVICE=cuda
DEVICE = os.environ.get("MODEL_DEVICE", "cpu")  # "cpu" or "cuda"
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")  # int8 is faster on CPU

# ---------- Global model singletons ----------
_whisper = None
_translator_model = None
_translator_tok = None


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
        _whisper = WhisperModel(
            ASR_MODEL_SIZE,
            device=DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _whisper


def asr_audio(audio_path: str) -> Tuple[str, Dict]:
    """
    Repetition-safe ASR using faster-whisper.

    Notes:
    - `condition_on_previous_text=False` is a BIG help against loops on long audio.
    - thresholds help cut degenerate outputs and silence hallucinations.
    - For very long audio, enabling chunking can help stability (chunk_length + vad_filter).
    """

    model = _get_whisper()

    decode_options = dict(
        beam_size=5,
        best_of=5,
        temperature=0.0,
        patience=1.0,
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        vad_filter=True,
        # If you see instability on long files, uncomment these:
        # chunk_length=30,
        # vad_parameters=dict(min_silence_duration_ms=500),
    )

    segments_iterator, info = model.transcribe(audio_path, **decode_options)

    texts = []
    segs = []

    for s in segments_iterator:
        t = (s.text or "").strip()
        if not t:
            continue
        segs.append({"start": float(s.start), "end": float(s.end), "text": t})
        texts.append(t)

    transcript = " ".join(texts).strip()
    return transcript, {
        "language": getattr(info, "language", None),
        "segments": segs
    }


# ---------- Translation (FI -> EN only) ----------
def _get_translator():
    global _translator_model, _translator_tok
    if _translator_model is None or _translator_tok is None:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

        # MarianMT models work well without language tags.
        _translator_tok = AutoTokenizer.from_pretrained(FI_EN_MODEL, token=HF_TOKEN)
        _translator_model = AutoModelForSeq2SeqLM.from_pretrained(FI_EN_MODEL, token=HF_TOKEN)
        _translator_model.eval()

        # Optional: if DEVICE=cuda, move model to GPU
        try:
            if DEVICE == "cuda":
                _translator_model.to("cuda")
        except Exception:
            # keep CPU if CUDA not available
            pass

    return _translator_model, _translator_tok


def translate_fi_to_en(text_fi: str) -> str:
    if not text_fi or not text_fi.strip():
        return ""

    model, tok = _get_translator()

    # Chunk to avoid max length issues
    max_chars = 2500
    chunks = [text_fi[i:i + max_chars] for i in range(0, len(text_fi), max_chars)]

    import torch
    out_parts = []

    for ch in chunks:
        inputs = tok(ch, return_tensors="pt", truncation=True)

        # Move tensors if GPU used
        if DEVICE == "cuda":
            inputs = {k: v.to("cuda") for k, v in inputs.items()}

        with torch.no_grad():
            generated = model.generate(
                **inputs,
                max_new_tokens=512,
                num_beams=4,
                early_stopping=True,
            )

        out_parts.append(tok.batch_decode(generated, skip_special_tokens=True)[0])

    return "\n".join(out_parts).strip()


# ---------- Doc extraction ----------
def extract_doc_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        import fitz  # PyMuPDF
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
        model_translation = FI_EN_MODEL
    else:
        transcript_en = transcript_raw
        transcript_fi = None
        model_translation = None

    # Persist transcript artifact
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

    # Persist extracted English text artifact
    text_key = f"uploads/docs/{doc_id}/text_en.txt"
    storage.save_text(text_key, text_en)

    with db_session() as db:
        doc = db.get(SupportDoc, doc_id)
        if not doc:
            return
        # IMPORTANT: your model does NOT have extracted_text_en (only the storage key)
        doc.extracted_text_en_storage_key = text_key
        doc.status = Status.READY