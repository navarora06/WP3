# WP3 — Tacit knowledge back-office

Flask app, Celery workers, and storage for **interview + document** ingestion, **gap analysis** (Azure OpenAI NLI), review workflow, and **Create Knowledge** (Azure AI Search + Cosmos Gremlin + optional image extraction).

## Documentation

- **System architecture (WP3 + Azure + rag-agent):** [docs/ARCHITECTURE_HLD.md](docs/ARCHITECTURE_HLD.md)

## Create Knowledge and document figures

When a reviewed gap report runs **Create Knowledge** (`tasks/knowledge.py`):

- Text chunks (interview, document, claims) are embedded into **Azure AI Search** (`content_vector`).
- Images embedded in the **supporting PDF/DOCX** are extracted, embedded with **Azure AI Vision** (`image_vector`), and upserted as `document_image` rows. The same Vision resource is used for **Image Analysis** (**caption + Read/OCR**); that text is stored in Search **`content`** and embedded as **`content_vector`** so RAG text queries (e.g. SIM card, zoom) can retrieve figures—not only multimodal vector search.
- Each figure is also **written to disk** under:

  `storage/uploads/docs/{doc_id}/extracted_rpt{report_id}_{index}.{ext}`

  The search document includes **`image_storage_key`** (that relative path from `STORAGE_ROOT`).

The **rag-agent** query UI serves those files via **`GET /knowledge-image`** when its **`STORAGE_ROOT`** points at the **same** storage directory (see rag-agent `README.md` and `docker-compose.yml`).

**After upgrading** the index schema or knowledge pipeline, **run Create Knowledge again** for a report so new fields and files are populated.

**Re-running Create Knowledge** for the same report **deletes all Azure Search rows for that `report_id`**, then upserts the current set—so you do **not** need to wipe the whole index manually; stale chunk ids from an older extraction are removed automatically.

## Configuration

See `.env.example` for database, Redis, Azure endpoints, and `STORAGE_ROOT` (default `./storage`).
