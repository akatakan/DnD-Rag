from llama_index.core import VectorStoreIndex
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.tools import QueryEngineTool, ToolMetadata
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from qdrant_client import QdrantClient, AsyncQdrantClient
import yaml

from config import QDRANT_URL, COLLECTION_PREFIX, EMBED_MODEL, METADATA_FILE


def build_tools(llm) -> list[QueryEngineTool]:
    client = QdrantClient(url=QDRANT_URL)
    aclient = AsyncQdrantClient(url=QDRANT_URL)
    embed_model = OllamaEmbedding(model_name=EMBED_MODEL)

    with open(METADATA_FILE) as f:
        metadata = yaml.safe_load(f)

    existing = {c.name for c in client.get_collections().collections}
    tools = []

    for collection_name in existing:
        if not collection_name.startswith(COLLECTION_PREFIX):
            continue

        book_name = collection_name.removeprefix(COLLECTION_PREFIX)
        book_meta = metadata.get(book_name, {})
        description = book_meta.get("description", f"{book_name} kitabı")

        vector_store = QdrantVectorStore(client=client, aclient=aclient, collection_name=collection_name)
        index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)

        retriever = VectorIndexRetriever(index=index, similarity_top_k=4)
        query_engine = RetrieverQueryEngine.from_args(retriever=retriever, llm=llm)

        tools.append(
            QueryEngineTool(
                query_engine=query_engine,
                metadata=ToolMetadata(name=book_name, description=description),
            )
        )
        print(f"[tool] {book_name} aracı oluşturuldu")

    return tools
