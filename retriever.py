from dataclasses import dataclass

import yaml
from llama_index.core import VectorStoreIndex
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from llama_index.core.vector_stores.types import VectorStoreQueryMode
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient, QdrantClient

from config import (
    COLLECTION_PREFIX,
    EMBED_MODEL,
    HYBRID_ALPHA,
    HYBRID_ENABLED,
    INDEX_VERSION,
    METADATA_FILE,
    QDRANT_URL,
    RETRIEVAL_TOP_K,
    SPARSE_MODEL,
)
from errors import AppError, unavailable


@dataclass(frozen=True)
class BookInfo:
    book_id: str
    title: str
    description: str
    collection_name: str


def load_book_catalog() -> dict[str, BookInfo]:
    with open(METADATA_FILE, encoding="utf-8") as metadata_file:
        raw = yaml.safe_load(metadata_file) or {}
    return {
        book_id: BookInfo(
            book_id=book_id,
            title=values.get("title", book_id),
            description=values.get("description", f"{book_id} kitabı"),
            collection_name=COLLECTION_PREFIX + book_id,
        )
        for book_id, values in raw.items()
    }


def build_metadata_filters(
    book_id: str,
    page_from: int | None = None,
    page_to: int | None = None,
) -> MetadataFilters:
    filters = [MetadataFilter(key="source_book_id", value=book_id)]
    if page_from is not None:
        filters.append(
            MetadataFilter(
                key="page_number", value=page_from, operator=FilterOperator.GTE
            )
        )
    if page_to is not None:
        filters.append(
            MetadataFilter(
                key="page_number", value=page_to, operator=FilterOperator.LTE
            )
        )
    return MetadataFilters(filters=filters)


def _clients() -> tuple[QdrantClient, AsyncQdrantClient]:
    kwargs = {"url": QDRANT_URL, "timeout": 20, "check_compatibility": False}
    return QdrantClient(**kwargs), AsyncQdrantClient(**kwargs)


def _existing_collections(client: QdrantClient) -> set[str]:
    try:
        return {item.name for item in client.get_collections().collections}
    except Exception as error:
        raise unavailable(
            "Qdrant", "Docker servisinin çalıştığını doğrulayın"
        ) from error


def _validate_index(client: QdrantClient, book: BookInfo) -> None:
    points, _ = client.scroll(
        collection_name=book.collection_name,
        limit=1,
        with_payload=["index_version"],
        with_vectors=False,
    )
    version = points[0].payload.get("index_version") if points else None
    if version != INDEX_VERSION:
        raise AppError(
            f"{book.title} indeksi eski formatta. `uv run python ingestion.py` "
            "komutunu çalıştırın."
        )


def build_retrievers(
    allowed_books: tuple[str, ...] | None = None,
    page_from: int | None = None,
    page_to: int | None = None,
    hybrid_enabled: bool = HYBRID_ENABLED,
) -> tuple[dict[str, VectorIndexRetriever], dict[str, BookInfo]]:
    client, async_client = _clients()
    existing = _existing_collections(client)
    catalog = load_book_catalog()
    selected = set(allowed_books or catalog)
    embed_model = OllamaEmbedding(model_name=EMBED_MODEL)
    retrievers = {}
    active_catalog = {}

    for book_id, book in catalog.items():
        if book_id not in selected or book.collection_name not in existing:
            continue
        _validate_index(client, book)
        vector_store_kwargs = {
            "client": client,
            "aclient": async_client,
            "collection_name": book.collection_name,
            "enable_hybrid": hybrid_enabled,
        }
        if hybrid_enabled:
            vector_store_kwargs["fastembed_sparse_model"] = SPARSE_MODEL
        vector_store = QdrantVectorStore(**vector_store_kwargs)
        index = VectorStoreIndex.from_vector_store(
            vector_store, embed_model=embed_model
        )
        mode = (
            VectorStoreQueryMode.HYBRID
            if hybrid_enabled
            else VectorStoreQueryMode.DEFAULT
        )
        retrievers[book_id] = VectorIndexRetriever(
            index=index,
            similarity_top_k=RETRIEVAL_TOP_K,
            sparse_top_k=RETRIEVAL_TOP_K,
            hybrid_top_k=RETRIEVAL_TOP_K,
            vector_store_query_mode=mode,
            alpha=HYBRID_ALPHA,
            filters=build_metadata_filters(book_id, page_from, page_to),
        )
        active_catalog[book_id] = book

    return retrievers, active_catalog
