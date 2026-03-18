"""
Helpers for generating text and image embeddings using Azure OpenAI and Azure AI Vision.
"""

import base64
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
