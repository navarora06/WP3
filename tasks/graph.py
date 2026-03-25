"""
Cosmos DB Gremlin helpers for the WP3 knowledge graph.

Vertices: interview, document, claim, chunk
Edges: has_claim, supported_by, contradicted_by, extracted_from
"""

import logging
from gremlin_python.driver import client as gremlin_client, serializer

from app.config import Config

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    cfg = Config()
    endpoint = cfg.AZURE_COSMOS_GREMLIN_ENDPOINT
    key = cfg.AZURE_COSMOS_GREMLIN_KEY
    database = cfg.AZURE_COSMOS_GREMLIN_DATABASE
    graph = cfg.AZURE_COSMOS_GREMLIN_GRAPH

    if not endpoint or not key:
        raise RuntimeError("Cosmos DB Gremlin not configured")

    _client = gremlin_client.Client(
        url=endpoint,
        traversal_source="g",
        username=f"/dbs/{database}/colls/{graph}",
        password=key,
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )
    return _client


def _submit(query: str, bindings: dict | None = None):
    """Execute a Gremlin query and return results."""
    c = _get_client()
    try:
        future = c.submitAsync(query, bindings)
        return future.result().all().result()
    except Exception:
        log.exception("Gremlin query failed: %s", query[:200])
        raise


def _gremlin_escape(value) -> str:
    """
    Escape a value for use inside a Gremlin single-quoted string literal.
    Cosmos Gremlin rejects unescaped newlines and other control chars; apostrophe is doubled.
    """
    s = "" if value is None else str(value)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return s.replace("'", "''")


def ensure_graph():
    """Verify connectivity (Cosmos creates the DB/graph via portal)."""
    try:
        _submit("g.V().count()")
        log.info("Gremlin graph connection verified")
    except Exception:
        log.exception("Cannot connect to Gremlin graph")
        raise


def add_vertex(label: str, vertex_id: str, properties: dict):
    """Add or update a vertex."""
    props = "".join(
        f".property('{k}', '{_gremlin_escape(v)}')" for k, v in properties.items()
    )
    vid = _gremlin_escape(vertex_id)
    lbl = _gremlin_escape(label)
    query = f"g.V('{vid}').fold().coalesce(unfold(), addV('{lbl}').property('id', '{vid}'){props})"
    _submit(query)


def add_edge(label: str, from_id: str, to_id: str, properties: dict | None = None):
    """Add an edge between two vertices (idempotent via coalesce)."""
    props = ""
    if properties:
        props = "".join(
            f".property('{k}', '{_gremlin_escape(v)}')" for k, v in properties.items()
        )
    fid, tid, el = _gremlin_escape(from_id), _gremlin_escape(to_id), _gremlin_escape(label)
    query = (
        f"g.V('{fid}').coalesce("
        f"  outE('{el}').where(inV().hasId('{tid}')),"
        f"  addE('{el}').to(g.V('{tid}')){props}"
        f")"
    )
    _submit(query)


def cleanup():
    """Close the Gremlin client."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
