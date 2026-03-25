"""
Helpers for generating text and image embeddings using Azure OpenAI and Azure AI Vision.
"""

import logging
import time

import requests
from openai import AzureOpenAI

from app.config import Config

log = logging.getLogger(__name__)

_cfg = None
_client = None


def _get_config():
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg


def _get_openai_client():
    global _client
    if _client is None:
        cfg = _get_config()
        _client = AzureOpenAI(
            azure_endpoint=cfg.AZURE_AI_ENDPOINT,
            api_key=cfg.AZURE_AI_PROJECT_KEY,
            api_version="2024-06-01",
        )
    return _client


def embed_texts(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    """Embed a list of texts using Azure OpenAI text-embedding-3-small. Returns list of 1536-dim vectors."""
    cfg = _get_config()
    client = _get_openai_client()
    deployment = cfg.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(5):
            try:
                resp = client.embeddings.create(input=batch, model=deployment)
                all_embeddings.extend([d.embedding for d in resp.data])
                break
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    wait = 2 ** (attempt + 1)
                    log.warning("Embedding rate-limited, retrying in %ds", wait)
                    time.sleep(wait)
                else:
                    raise
        time.sleep(0.5)

    return all_embeddings


def _parse_image_analysis_json(data: dict) -> str:
    """Extract human-readable strings from Image Analysis JSON (caption + read/OCR)."""
    parts: list[str] = []

    cap = data.get("captionResult")
    if isinstance(cap, dict):
        t = cap.get("text")
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())

    rr = data.get("readResult")
    if isinstance(rr, dict):
        for block in rr.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            for line in block.get("lines") or []:
                if isinstance(line, dict):
                    lt = line.get("text")
                    if isinstance(lt, str) and lt.strip():
                        parts.append(lt.strip())
        for page in rr.get("pages") or []:
            if not isinstance(page, dict):
                continue
            for line in page.get("lines") or []:
                if isinstance(line, dict):
                    lt = line.get("text")
                    if isinstance(lt, str) and lt.strip():
                        parts.append(lt.strip())

    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return " ".join(out)[:2500]


def vision_image_text_for_index(image_bytes: bytes) -> str:
    """
    Caption + on-image OCR via Azure AI Vision Image Analysis.
    Stored in Search `content` and embedded as `content_vector` so text queries
    (e.g. SIM, zoom) retrieve figure chunks—not only image_vector search.
    """
    cfg = _get_config()
    endpoint = cfg.AZURE_VISION_ENDPOINT.rstrip("/")
    key = cfg.AZURE_VISION_KEY
    if not endpoint or not key:
        return ""

    url = f"{endpoint}/computervision/imageanalysis:analyze"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/octet-stream",
    }
    param_sets = [
        {"api-version": "2024-02-01", "features": "caption,read", "model-version": "latest"},
        {"api-version": "2023-10-01", "features": "caption,read", "model-version": "latest"},
        {"api-version": "2024-02-01", "features": "caption"},
        {"api-version": "2023-10-01", "features": "caption"},
    ]
    for params in param_sets:
        try:
            resp = requests.post(url, params=params, headers=headers, data=image_bytes, timeout=60)
            resp.raise_for_status()
            text = _parse_image_analysis_json(resp.json())
            if text.strip():
                return text.strip()
        except Exception as e:
            log.debug("Vision image analysis failed for %s: %s", params.get("api-version"), e)
            continue
    log.debug(
        "Vision Image Analysis returned no caption/OCR for an image; "
        "text vector search may not surface this figure."
    )
    return ""


def embed_image(image_bytes: bytes) -> list[float]:
    """Embed an image using Azure AI Vision Florence multimodal embeddings (1024-dim)."""
    cfg = _get_config()
    endpoint = cfg.AZURE_VISION_ENDPOINT.rstrip("/")
    key = cfg.AZURE_VISION_KEY

    if not endpoint or not key:
        log.warning("Azure Vision not configured; skipping image embedding")
        return []

    url = f"{endpoint}/computervision/retrieval:vectorizeImage"
    params = {"api-version": "2024-02-01", "model-version": "2023-04-15"}
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/octet-stream",
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, params=params, headers=headers, data=image_bytes, timeout=30)
            resp.raise_for_status()
            return resp.json().get("vector", [])
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429 and attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                log.error("Vision embedding failed: %s", e)
                return []

    return []
