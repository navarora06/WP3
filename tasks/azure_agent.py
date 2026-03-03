"""
Azure AI service clients for WP3.

- transcribe_audio : Azure Speech SDK  (continuous recognition for long audio)
- translate_fi_to_en : Azure Translator REST API
- run_gap_analysis_agent : Azure OpenAI GPT-4o two-pass NLI pipeline
"""

import json
import os
import subprocess
import tempfile
import threading
import logging
import requests

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio format conversion (Azure Speech SDK only supports WAV natively)
# ---------------------------------------------------------------------------

def _ensure_wav(audio_path: str) -> str:
    """Convert audio to 16kHz mono WAV if not already WAV. Returns path to WAV file."""
    ext = os.path.splitext(audio_path)[1].lower()
    if ext == ".wav":
        return audio_path

    wav_path = tempfile.mktemp(suffix=".wav")
    log.info("Converting %s to WAV: %s", audio_path, wav_path)
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
        check=True,
        capture_output=True,
    )
    return wav_path


# ---------------------------------------------------------------------------
# Azure Speech – transcription with speaker diarization
# ---------------------------------------------------------------------------

def _speaker_label(raw_id: str | None, mapping: dict) -> str:
    """Map raw speaker ids (Guest-1, Guest-2, …) to Speaker A, Speaker B, …"""
    if not raw_id:
        return "Speaker"
    if raw_id not in mapping:
        mapping[raw_id] = chr(ord("A") + len(mapping))
    return f"Speaker {mapping[raw_id]}"


def _fmt_ts(seconds: float) -> str:
    mm = int(seconds // 60)
    ss = int(seconds % 60)
    return f"{mm:02d}:{ss:02d}"


def transcribe_audio(audio_path: str, is_finnish: bool = True) -> tuple[str, list[dict]]:
    """Transcribe audio with speaker diarization using ConversationTranscriber."""
    import azure.cognitiveservices.speech as speechsdk

    wav_path = _ensure_wav(audio_path)

    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=os.environ["AZURE_SPEECH_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
        )
        speech_config.speech_recognition_language = "fi-FI" if is_finnish else "en-US"

        audio_config = speechsdk.AudioConfig(filename=wav_path)
        transcriber = speechsdk.transcription.ConversationTranscriber(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        done = threading.Event()
        results: list[dict] = []
        errors: list[str] = []

        def on_transcribed(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                results.append({
                    "text": evt.result.text,
                    "offset_s": evt.result.offset / 10_000_000,
                    "duration_s": evt.result.duration / 10_000_000,
                    "speaker_id": evt.result.speaker_id,
                })

        def on_canceled(evt):
            if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
                errors.append(evt.cancellation_details.error_details)
            done.set()

        def on_stopped(_evt):
            done.set()

        transcriber.transcribed.connect(on_transcribed)
        transcriber.canceled.connect(on_canceled)
        transcriber.session_stopped.connect(on_stopped)

        transcriber.start_transcribing_async().get()
        done.wait()
        transcriber.stop_transcribing_async().get()

        if errors:
            raise RuntimeError(f"Azure Speech error: {errors[0]}")

        speaker_map: dict[str, str] = {}
        segments = []
        for r in results:
            label = _speaker_label(r.get("speaker_id"), speaker_map)
            segments.append({
                "start": r["offset_s"],
                "end": r["offset_s"] + r["duration_s"],
                "speaker": label,
                "text": r["text"],
            })

        return segments

    finally:
        if wav_path != audio_path and os.path.exists(wav_path):
            os.remove(wav_path)


def _detect_names_from_greetings(segments: list[dict]) -> dict[str, str]:
    """Deterministic greeting-based speaker identification.

    When Speaker A says "Hello Laura" or "Hi Laura", Speaker A is NOT Laura.
    Returns a mapping like {"Speaker A": "Kari", "Speaker B": "Laura"}.
    """
    import re

    greeting_pattern = re.compile(
        r'\b(?:hello|hi|hey|hei|moi|terve|good morning|good afternoon)\s+([A-Z][a-z]{2,})',
        re.IGNORECASE,
    )

    speaker_greeted: dict[str, list[str]] = {}
    all_names: set[str] = set()

    for s in segments[:40]:
        speaker = s.get("speaker", "")
        if not speaker:
            continue
        for match in greeting_pattern.finditer(s.get("text", "")):
            name = match.group(1).strip().rstrip(".,!?")
            if len(name) >= 2:
                speaker_greeted.setdefault(speaker, []).append(name)
                all_names.add(name)

    if len(speaker_greeted) < 2 or len(all_names) < 2:
        return {}

    speakers = sorted(speaker_greeted.keys())
    name_map: dict[str, str] = {}

    for speaker in speakers:
        greeted_names = speaker_greeted.get(speaker, [])
        if greeted_names:
            addressed_name = max(set(greeted_names), key=greeted_names.count)
            other_names = all_names - {addressed_name}
            if other_names:
                name_map[speaker] = other_names.pop()

    return name_map


def resolve_speaker_names(segments: list[dict]) -> list[dict]:
    """Identify real speaker names using deterministic greeting analysis,
    with GPT-4o as fallback.
    """
    if not segments:
        return segments

    speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})
    if len(speakers) < 2:
        return segments

    # Try deterministic greeting-based detection first
    name_map = _detect_names_from_greetings(segments)
    if name_map and len(name_map) >= 2:
        log.info("Resolved speaker names (deterministic): %s", name_map)
        return [
            {**s, "speaker": name_map.get(s.get("speaker", ""), s.get("speaker", ""))}
            for s in segments
        ]

    # Fallback: use GPT-4o for ambiguous cases
    preview = segments[:min(30, len(segments))]
    preview_text = "\n".join(
        f"[{_fmt_ts(s['start'])}] {s['speaker']}: {s['text']}" for s in preview
    )

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_key=os.environ["AZURE_AI_PROJECT_KEY"],
            api_version="2024-10-21",
            azure_endpoint=os.environ["AZURE_AI_ENDPOINT"],
        )

        nli_deployment = os.environ.get(
            "AZURE_OPENAI_NLI_DEPLOYMENT",
            os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        )

        prompt = (
            "Below is a transcribed and machine-translated conversation. "
            "Speakers are labeled generically. Determine each speaker's real name.\n\n"
            "RULE: When someone says 'Hello X' or 'Hi X', they are ADDRESSING X. "
            "The speaker is NOT X — the speaker is the OTHER person.\n\n"
            "Example:\n"
            "  Speaker A: 'Hello Laura' → Speaker A is greeting Laura → Speaker A = Kari\n"
            "  Speaker B: 'Hi Kari' → Speaker B is greeting Kari → Speaker B = Laura\n\n"
            "IGNORE garbled translation artifacts. ONLY use clear greetings.\n\n"
            "Return a JSON object mapping labels to names.\n\n"
            f"Transcript:\n{preview_text}"
        )

        response = client.chat.completions.create(
            model=nli_deployment,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200,
        )

        name_map = json.loads(response.choices[0].message.content)
        log.info("Resolved speaker names (GPT): %s", name_map)

        return [
            {**s, "speaker": name_map.get(s.get("speaker", ""), s.get("speaker", ""))}
            for s in segments
        ]

    except Exception:
        log.warning("Could not resolve speaker names, keeping generic labels", exc_info=True)
        return segments


# ---------------------------------------------------------------------------
# Azure Translator – FI → EN
# ---------------------------------------------------------------------------

def _chunk_text(text: str, max_chars: int = 4500) -> list[str]:
    """Split text into chunks respecting sentence boundaries where possible."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        cut = text.rfind(". ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        else:
            cut += 2
        chunks.append(text[:cut])
        text = text[cut:]
    return chunks


def _translator_headers():
    return {
        "Ocp-Apim-Subscription-Key": os.environ["AZURE_TRANSLATOR_KEY"],
        "Ocp-Apim-Subscription-Region": os.environ["AZURE_TRANSLATOR_REGION"],
        "Content-Type": "application/json",
    }


def _translator_url():
    endpoint = os.environ.get(
        "AZURE_TRANSLATOR_ENDPOINT",
        "https://api.cognitive.microsofttranslator.com",
    )
    return f"{endpoint}/translate"


def _translate_batch(texts: list[str]) -> list[str]:
    """Translate a list of texts in a single API call with retry on 429."""
    import time

    url = _translator_url()
    params = {"api-version": "3.0", "from": "fi", "to": "en"}
    headers = _translator_headers()
    body = [{"text": t} for t in texts]

    for attempt in range(5):
        resp = requests.post(url, params=params, headers=headers, json=body, timeout=60)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** attempt))
            log.warning("Translator 429, retrying in %ds", wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return [item["translations"][0]["text"] for item in resp.json()]

    resp.raise_for_status()
    return []


def translate_fi_to_en(text: str) -> str:
    """Translate Finnish text to English via Azure Translator REST API."""
    if not text or not text.strip():
        return ""
    chunks = _chunk_text(text)
    parts = _translate_batch(chunks) if len(chunks) <= 25 else []
    if not parts:
        parts = []
        for chunk in chunks:
            parts.extend(_translate_batch([chunk]))
    return " ".join(parts).strip()


def translate_segments_fi_to_en(segments: list[dict]) -> list[dict]:
    """Translate segments in context groups for better quality.

    Instead of translating each segment independently (which loses context),
    we join groups of consecutive segments into paragraphs, translate the
    paragraph, then split back. This gives the translator surrounding context
    for more accurate translation.
    """
    if not segments:
        return segments

    GROUP_SIZE = 5
    SEPARATOR = " ||| "
    translated = [""] * len(segments)

    for start in range(0, len(segments), GROUP_SIZE):
        group = segments[start:start + GROUP_SIZE]
        group_texts = [s["text"] for s in group]

        joined = SEPARATOR.join(group_texts)
        result = _translate_batch([joined])

        if result:
            parts = result[0].split("|||")
            for j, part in enumerate(parts):
                idx = start + j
                if idx < len(segments):
                    translated[idx] = part.strip()

            # If splitting produced fewer parts than expected, translate remaining individually
            if len(parts) < len(group):
                for j in range(len(parts), len(group)):
                    idx = start + j
                    if idx < len(segments):
                        individual = _translate_batch([group_texts[j]])
                        translated[idx] = individual[0] if individual else group_texts[j]
        else:
            for j, s in enumerate(group):
                idx = start + j
                individual = _translate_batch([s["text"]])
                translated[idx] = individual[0] if individual else s["text"]

    return [
        {**s, "text": translated[i]}
        for i, s in enumerate(segments)
    ]


# ---------------------------------------------------------------------------
# Azure OpenAI – two-pass NLI gap analysis pipeline
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM_PROMPT = """\
You are a senior technical documentation expert. Your ONLY job right now is to \
extract claims from an interview transcript. Do NOT classify or judge them.

You will receive an INTERVIEW TRANSCRIPT (English) with timestamps and speakers.

TASK: Read every sentence. Sort each sentence into one of two buckets:

1. CLAIMS – ANY statement that describes, asserts, or implies something \
about a product, system, feature, component, process, procedure, specification, \
location, layout, dimension, material, technology, configuration, capability, \
limitation, compatibility, instruction, or step. This includes:
  - Descriptions of physical features ("There are two cameras on the back")
  - Location/layout statements ("The USB-C port is at the bottom")
  - How-to instructions ("You insert the SIM card from the right side")
  - Capability statements ("It supports dual SIM")
  - Compatibility/limitation ("You cannot use SD card with dual SIM")
  - Comparisons ("There are two variants of the phone")
  - Any statement that could be TRUE or FALSE when checked against documentation

CRITICAL: Be MAXIMALLY inclusive. If a sentence contains ANY factual content \
about the product or system, it is a CLAIM. You should extract 40-80+ claims \
from a typical 20-30 minute technical interview. If you extract fewer than 30, \
you are being too conservative.

2. OUT OF SCOPE – ONLY these narrow categories:
  - Pure greetings ("Hello", "Hi Kari", "Nice to meet you")
  - Pure filler with zero factual content ("okay", "right", "oh well", "good good")
  - Procedural meta-talk with no product info ("let's move on", "shall we go through")
  - Questions that contain no assertions (but if a question implies a fact, \
    extract the implied fact as a claim)

Return a JSON object with exactly this structure:
{
  "claims": [
    {
      "claim": "The factual statement, rephrased clearly if needed.",
      "interview_evidence": "HH:MM – Speaker: exact quote from transcript",
      "original_index": 1
    }
  ],
  "out_of_scope": [
    {
      "sentence": "The excluded sentence.",
      "reason": "greeting | filler | question | procedural"
    }
  ]
}

Be thorough. Process EVERY sentence. Number claims starting from 1. \
Err heavily on the side of including something as a claim.\
"""

CLASSIFY_SYSTEM_PROMPT = """\
You are a precise technical auditor comparing interview claims against \
supporting documentation. Your goal is ACCURACY — correctly identifying \
what is supported, what is contradicted, and what is not covered.

You will receive:
1. A batch of CLAIMS extracted from an interview transcript.
2. A SUPPORTING DOCUMENT (English text).

IMPORTANT: The interview transcript was machine-translated from Finnish. \
Minor wording differences between the interview and document are expected \
due to translation artifacts. Focus on FACTUAL MEANING, not exact wording.

For EACH claim, follow this reasoning process:

STEP 1 – SEARCH: Find the most relevant passage in the document. Quote it \
exactly (up to 2 sentences). If nothing is relevant, state "No relevant \
passage found."

STEP 2 – REASON: Compare the FACTUAL MEANING of the claim against the \
quoted passage:
  a) Do they convey the same factual information, even if worded differently?
  b) Do they make genuinely OPPOSITE or INCOMPATIBLE factual statements?
  c) Is the topic simply not addressed in the document?

STEP 3 – LABEL: Assign exactly one label:

  SUPPORTED: The document confirms the same factual information as the claim. \
  The claim and document agree on the key facts, even if they use different \
  wording, different levels of detail, or slightly different phrasing. \
  A simplified version of a documented fact is still SUPPORTED. \
  A more detailed version of a documented fact is still SUPPORTED.

  CONTRADICTED: The document makes a GENUINELY OPPOSITE or INCOMPATIBLE \
  factual statement. Examples of real contradictions:
    - Claim says "X uses version 3" but document says "X uses version 5"
    - Claim says "backups run daily" but document says "backups run weekly"
    - Claim says "the system supports feature X" but document says "feature X \
      is not supported"
  NOT a contradiction: same fact stated with different wording or detail level.

  UNKNOWN: The document does not address this topic at all, or only mentions \
  it too vaguely to confirm or deny.

CRITICAL RULES:
  - Different wording ≠ contradiction. Focus on whether the FACTS agree.
  - A simplified claim that captures the essence of a documented fact = SUPPORTED.
  - The interview is translated — expect imprecise language. Be generous with \
    wording differences, strict with factual differences.
  - When in doubt between SUPPORTED and CONTRADICTED → check: do the core \
    facts actually conflict? If not, it is SUPPORTED.
  - When in doubt between SUPPORTED and UNKNOWN → choose UNKNOWN.
  - Subjective claims ("easy", "fast") without documentary evidence = UNKNOWN.

Return a JSON object:
{
  "results": [
    {
      "original_index": 1,
      "claim": "The claim text.",
      "label": "SUPPORTED",
      "doc_evidence": "Exact quote from document, or 'No relevant passage found.'",
      "reasoning": "Step-by-step explanation of why this label was chosen.",
      "confidence": "High | Medium | Low",
      "action_suggestion": "A concrete recommendation."
    }
  ]
}

Confidence guidelines:
- High: Clear factual match or clear factual conflict.
- Medium: Partial evidence, some interpretation needed.
- Low: Weak or indirect evidence.

Action suggestions:
- SUPPORTED → "Confirm in next review" or "Already documented"
- CONTRADICTED → "Review contradiction: document says X but interview says Y"
- UNKNOWN → "Investigate further" or "Add to documentation backlog"\
"""


def _get_nli_client():
    """Create AzureOpenAI client for NLI tasks."""
    from openai import AzureOpenAI
    return AzureOpenAI(
        api_key=os.environ["AZURE_AI_PROJECT_KEY"],
        api_version="2024-10-21",
        azure_endpoint=os.environ["AZURE_AI_ENDPOINT"],
    )


def _nli_model() -> str:
    return os.environ.get("AZURE_OPENAI_NLI_DEPLOYMENT",
                          os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"))


def _parse_json_response(response) -> dict:
    """Parse JSON from an OpenAI response, with repair for truncated output."""
    raw = response.choices[0].message.content or ""
    finish_reason = response.choices[0].finish_reason
    log.info("GPT response: finish_reason=%s, length=%d chars", finish_reason, len(raw))

    if finish_reason == "length":
        log.warning("Response was truncated (hit max_tokens). Attempting repair.")

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("JSON parse failed, attempting repair. First 500 chars: %s", raw[:500])
        repaired = _repair_truncated_json(raw)
        return json.loads(repaired)


def _repair_truncated_json(raw: str) -> str:
    """Best-effort repair of truncated JSON from LLM output."""
    raw = raw.rstrip()
    if raw.endswith(","):
        raw = raw[:-1]

    open_braces = raw.count("{") - raw.count("}")
    open_brackets = raw.count("[") - raw.count("]")

    in_string = False
    escaped = False
    for ch in raw:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string

    if in_string:
        raw += '"'

    raw = raw.rstrip().rstrip(",")
    raw += "]" * max(0, open_brackets)
    raw += "}" * max(0, open_braces)

    return raw


def _extract_claims(client, transcript_text: str) -> dict:
    """Pass 1: Extract claims from the transcript in chunks.

    Long transcripts are split into chunks of ~30 lines so the model
    processes every segment instead of cherry-picking a subset.
    """
    import time as _time

    model = _nli_model()
    lines = transcript_text.strip().split("\n")
    total_lines = len(lines)
    log.info("Pass 1 – Extracting claims from %d lines using %s", total_lines, model)

    CHUNK_SIZE = 30
    all_claims: list[dict] = []
    all_out_of_scope: list[dict] = []
    claim_index = 1

    for chunk_start in range(0, total_lines, CHUNK_SIZE):
        chunk_lines = lines[chunk_start:chunk_start + CHUNK_SIZE]
        chunk_text = "\n".join(chunk_lines)
        chunk_num = (chunk_start // CHUNK_SIZE) + 1
        total_chunks = (total_lines + CHUNK_SIZE - 1) // CHUNK_SIZE

        user_msg = (
            f"CHUNK {chunk_num}/{total_chunks} (lines {chunk_start + 1}-{chunk_start + len(chunk_lines)} "
            f"of {total_lines}):\n\n{chunk_text}\n\n"
            f"Extract ALL claims and out-of-scope items from this chunk. "
            f"Start numbering claims from {claim_index}."
        )

        log.info("Extraction chunk %d/%d (%d lines)", chunk_num, total_chunks, len(chunk_lines))

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=8000,
                )
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = 30 * (attempt + 1)
                    log.warning("429 on extraction chunk %d, retrying in %ds", chunk_num, wait)
                    _time.sleep(wait)
                else:
                    raise

        result = _parse_json_response(response)
        chunk_claims = result.get("claims", [])
        chunk_oos = result.get("out_of_scope", [])

        for c in chunk_claims:
            c["original_index"] = claim_index
            claim_index += 1

        all_claims.extend(chunk_claims)
        all_out_of_scope.extend(chunk_oos)

        if chunk_start + CHUNK_SIZE < total_lines:
            _time.sleep(2)

    log.info("Pass 1 complete: %d claims, %d out-of-scope from %d lines",
             len(all_claims), len(all_out_of_scope), total_lines)

    return {"claims": all_claims, "out_of_scope": all_out_of_scope}


def _classify_claims_batch(client, claims: list[dict], doc_title: str, doc_text: str) -> list[dict]:
    """Pass 2: Classify a batch of claims against the document with chain-of-thought.
    Retries up to 3 times on rate-limit (429) errors with exponential backoff.
    """
    import time as _time
    from openai import RateLimitError

    model = _nli_model()

    claims_text = "\n".join(
        f"{c['original_index']}. {c['claim']}\n   Evidence: {c.get('interview_evidence', 'N/A')}"
        for c in claims
    )

    user_msg = f"""=== CLAIMS TO CLASSIFY ===
{claims_text}

=== SUPPORTING DOCUMENT: {doc_title} ===
{doc_text}

Classify each claim against the document. Follow the reasoning steps strictly. \
Return JSON only."""

    log.info("Pass 2 – Classifying batch of %d claims using %s", len(claims), model)

    for attempt in range(4):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=16000,
            )
            result = _parse_json_response(response)
            return result.get("results", [])
        except RateLimitError as e:
            wait = min(30 * (2 ** attempt), 120)
            log.warning("429 on classification (attempt %d/4), retrying in %ds: %s",
                        attempt + 1, wait, e)
            _time.sleep(wait)

    log.error("Classification batch failed after 4 attempts")
    return []


def run_gap_analysis_agent(
    transcript_en: str,
    segments_json: list[dict] | None,
    doc_title: str,
    doc_text: str,
) -> dict:
    """Two-pass NLI gap analysis: extract claims, then classify each against the document."""
    import time

    client = _get_nli_client()

    # Build formatted transcript
    if segments_json:
        seg_lines = []
        for s in segments_json:
            ts = _fmt_ts(s["start"])
            speaker = s.get("speaker", "Speaker")
            seg_lines.append(f"[{ts}] {speaker}: {s['text']}")
        transcript_text = "\n".join(seg_lines)
    else:
        transcript_text = transcript_en

    # Pass 1: Extract claims
    extraction = _extract_claims(client, transcript_text)
    claims = extraction.get("claims", [])
    out_of_scope = extraction.get("out_of_scope", [])

    if not claims:
        log.warning("No claims extracted from transcript")
        return {"gap_analysis": [], "out_of_scope": out_of_scope, "summary": {}}

    # Pass 2: Classify in batches of 5
    BATCH_SIZE = 5
    all_classified: list[dict] = []

    for i in range(0, len(claims), BATCH_SIZE):
        batch = claims[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(claims) + BATCH_SIZE - 1) // BATCH_SIZE
        log.info("Classification batch %d/%d (%d claims)", batch_num, total_batches, len(batch))

        classified = _classify_claims_batch(client, batch, doc_title, doc_text)
        all_classified.extend(classified)

        if i + BATCH_SIZE < len(claims):
            time.sleep(3)

    # Merge extraction evidence with classification results
    claim_lookup = {c.get("original_index"): c for c in claims}
    gap_analysis = []
    for item in all_classified:
        idx = item.get("original_index")
        orig = claim_lookup.get(idx, {})

        gap_analysis.append({
            "claim": item.get("claim", orig.get("claim", "")),
            "label": item.get("label", "UNKNOWN"),
            "interview_evidence": orig.get("interview_evidence", ""),
            "doc_evidence": item.get("doc_evidence", ""),
            "confidence": item.get("confidence", "Low"),
            "reasoning": item.get("reasoning", ""),
            "action_suggestion": item.get("action_suggestion", ""),
        })

    log.info("Two-pass NLI complete: %d claims classified", len(gap_analysis))

    return {
        "gap_analysis": gap_analysis,
        "out_of_scope": out_of_scope,
    }
