"""
Azure AI Search index management for the WP3 knowledge pipeline.
"""

import logging
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
)
from azure.core.credentials import AzureKeyCredential

from app.config import Config

log = logging.getLogger(__name__)

INDEX_NAME = "wp3-knowledge"

TEXT_VECTOR_DIM = 1536
IMAGE_VECTOR_DIM = 1024


def _get_credentials():
    cfg = Config()
    return cfg.AZURE_SEARCH_ENDPOINT, AzureKeyCredential(cfg.AZURE_SEARCH_KEY)


def ensure_index():
    """Create or update the search index with the required schema."""
    endpoint, credential = _get_credentials()
    client = SearchIndexClient(endpoint=endpoint, credential=credential)

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_id", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="report_id", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=TEXT_VECTOR_DIM,
            vector_search_profile_name="text-profile",
        ),
        SearchField(
            name="image_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=IMAGE_VECTOR_DIM,
            vector_search_profile_name="image-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="hnsw-text"),
            HnswAlgorithmConfiguration(name="hnsw-image"),
        ],
        profiles=[
            VectorSearchProfile(name="text-profile", algorithm_configuration_name="hnsw-text"),
            VectorSearchProfile(name="image-profile", algorithm_configuration_name="hnsw-image"),
        ],
    )

    index = SearchIndex(name=INDEX_NAME, fields=fields, vector_search=vector_search)

    try:
        client.create_or_update_index(index)
        log.info("Search index '%s' ensured", INDEX_NAME)
    except Exception:
        log.exception("Failed to create/update search index")
        raise


def upsert_documents(docs: list[dict]):
    """Upsert a batch of documents into the search index."""
    endpoint, credential = _get_credentials()
    client = SearchClient(endpoint=endpoint, index_name=INDEX_NAME, credential=credential)

    batch_size = 100
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        try:
            result = client.upload_documents(documents=batch)
            succeeded = sum(1 for r in result if r.succeeded)
            log.info("Upserted %d/%d documents (batch %d)", succeeded, len(batch), i // batch_size + 1)
        except Exception:
            log.exception("Failed to upsert document batch %d", i // batch_size + 1)
            raise
