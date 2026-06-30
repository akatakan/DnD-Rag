from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.readers.file import PyMuPDFReader
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
import yaml

from config import DATA_DIR, METADATA_FILE, QDRANT_URL, COLLECTION_PREFIX, EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP


def get_existing_collections(client: QdrantClient) -> set[str]:
    return {c.name for c in client.get_collections().collections}


def ingest_all():
    client = QdrantClient(url=QDRANT_URL)
    embed_model = OllamaEmbedding(model_name=EMBED_MODEL)
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    existing = get_existing_collections(client)

    with open(METADATA_FILE) as f:
        metadata = yaml.safe_load(f)

    for pdf_path in DATA_DIR.glob("*.pdf"):
        book_name = pdf_path.stem
        collection_name = COLLECTION_PREFIX + book_name

        if collection_name in existing:
            print(f"[skip] {book_name} zaten mevcut")
            continue

        print(f"[ingest] {book_name} processing...")

        documents = PyMuPDFReader().load(file_path=pdf_path)
        nodes = splitter.get_nodes_from_documents(documents)

        # embed boyutunu öğrenmek için bir deneme embed
        sample_embedding = embed_model.get_text_embedding("test")
        vector_size = len(sample_embedding)

        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

        vector_store = QdrantVectorStore(client=client, collection_name=collection_name)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        VectorStoreIndex(
            nodes,
            storage_context=storage_context,
            embed_model=embed_model,
        )

        print(f"[done] {book_name} → {len(nodes)} chunk, collection: {collection_name}")


if __name__ == "__main__":
    ingest_all()
