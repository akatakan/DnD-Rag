import sys
from hashlib import sha256

import yaml
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.readers.file import PyMuPDFReader
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.vector_stores.qdrant.utils import fastembed_sparse_encoder
from qdrant_client import QdrantClient

from config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_PREFIX,
    DATA_DIR,
    EMBED_MODEL,
    INDEX_VERSION,
    METADATA_FILE,
    QDRANT_URL,
    SPARSE_MODEL,
)
from errors import normalize_error


def get_existing_collections(client: QdrantClient) -> set[str]:
    return {collection.name for collection in client.get_collections().collections}


def file_sha256(path) -> str:
    digest = sha256()
    with path.open("rb") as pdf:
        for block in iter(lambda: pdf.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def index_signature(pdf_hash: str) -> str:
    return f"{INDEX_VERSION}:{EMBED_MODEL}:{SPARSE_MODEL}:{pdf_hash}"


def get_indexed_signature(client: QdrantClient, collection_name: str) -> str | None:
    points, _ = client.scroll(
        collection_name=collection_name,
        limit=1,
        with_payload=["index_signature"],
        with_vectors=False,
    )
    if not points or not points[0].payload:
        return None
    return points[0].payload.get("index_signature")


def ingest_all() -> None:
    client = QdrantClient(
        url=QDRANT_URL, timeout=20, check_compatibility=False
    )
    embed_model = OllamaEmbedding(model_name=EMBED_MODEL)
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    existing = get_existing_collections(client)

    with open(METADATA_FILE, encoding="utf-8") as metadata_file:
        metadata = yaml.safe_load(metadata_file) or {}

    # Verify both local encoders before replacing any usable collection.
    embed_model.get_text_embedding("test")
    fastembed_sparse_encoder(model_name=SPARSE_MODEL)(["test"])

    for pdf_path in sorted(DATA_DIR.glob("*.pdf")):
        book_name = pdf_path.stem
        collection_name = COLLECTION_PREFIX + book_name
        pdf_hash = file_sha256(pdf_path)
        signature = index_signature(pdf_hash)

        if (
            collection_name in existing
            and get_indexed_signature(client, collection_name) == signature
        ):
            print(f"[skip] {book_name} değişmedi")
            continue

        action = "güncelleniyor" if collection_name in existing else "işleniyor"
        print(f"[ingest] {book_name} {action}...")

        documents = PyMuPDFReader().load(file_path=pdf_path)
        book_title = metadata.get(book_name, {}).get("title", book_name)
        for page_index, document in enumerate(documents, start=1):
            raw_page = document.metadata.get("source", page_index)
            try:
                page_number = int(raw_page)
            except (TypeError, ValueError):
                page_number = page_index
            document.metadata.update(
                source_book=book_title,
                source_book_id=book_name,
                source_file=pdf_path.name,
                page_number=page_number,
                pdf_sha256=pdf_hash,
                index_version=INDEX_VERSION,
                index_signature=signature,
            )
        nodes = splitter.get_nodes_from_documents(documents)

        if collection_name in existing:
            client.delete_collection(collection_name=collection_name)

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            enable_hybrid=True,
            fastembed_sparse_model=SPARSE_MODEL,
        )
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        VectorStoreIndex(
            nodes,
            storage_context=storage_context,
            embed_model=embed_model,
            show_progress=True,
        )
        print(f"[done] {book_name}: {len(nodes)} chunk, hybrid collection hazır")


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    configure_console()
    try:
        ingest_all()
    except Exception as error:
        print(f"[error] {normalize_error(error, 'ollama')}", file=sys.stderr)
        raise SystemExit(1) from None
