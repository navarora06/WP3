import os
import requests
from app.config import Config
from app.extensions import init_db
from app.models import LightRagIndexRef
from app.util import db_session

def lightrag_index_text(entity_type: str, entity_id: int, text: str, metadata: dict):
    cfg = Config()
    init_db(cfg.DATABASE_URL)

    # If you don't have LightRAG running yet, keep it as no-op
    if not cfg.LIGHTRAG_BASE_URL:
        with db_session() as db:
            db.add(LightRagIndexRef(
                entity_type=entity_type,
                entity_id=entity_id,
                lightrag_namespace=cfg.LIGHTRAG_NAMESPACE,
                lightrag_doc_ids_json={"note": "LightRAG not configured"},
            ))
        return

    payload = {
        "namespace": cfg.LIGHTRAG_NAMESPACE,
        "text": text,
        "metadata": metadata
    }
    r = requests.post(f"{cfg.LIGHTRAG_BASE_URL}/index", json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    with db_session() as db:
        db.add(LightRagIndexRef(
            entity_type=entity_type,
            entity_id=entity_id,
            lightrag_namespace=cfg.LIGHTRAG_NAMESPACE,
            lightrag_doc_ids_json=data,
        ))

def lightrag_query(query: str, filters: dict | None = None, top_k: int = 5) -> dict:
    cfg = Config()
    if not cfg.LIGHTRAG_BASE_URL:
        return {"results": []}

    payload = {
        "namespace": cfg.LIGHTRAG_NAMESPACE,
        "query": query,
        "top_k": top_k,
        "filters": filters or {}
    }
    r = requests.post(f"{cfg.LIGHTRAG_BASE_URL}/query", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()
