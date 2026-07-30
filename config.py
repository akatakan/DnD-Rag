import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
METADATA_FILE = DATA_DIR / "metadata.yaml"
EVALUATION_FILE = ROOT_DIR / "evaluation" / "questions.yaml"
SESSION_DB = Path(os.getenv("SESSION_DB", ROOT_DIR / "runtime" / "sessions.db"))
MEMORY_MESSAGE_LIMIT = int(os.getenv("MEMORY_MESSAGE_LIMIT", "8"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_PREFIX = "dnd_"
INDEX_VERSION = "2-hybrid-bm25"

OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "").strip()
GEMINI_LLM_MODEL = os.getenv("GEMINI_LLM_MODEL", "gemini-2.5-flash-lite")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
SPARSE_MODEL = os.getenv("SPARSE_MODEL", "Qdrant/bm25")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "10"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "4"))
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.6"))
HYBRID_ENABLED = env_bool("HYBRID_ENABLED", False)
RERANK_ENABLED = env_bool("RERANK_ENABLED", False)
ROUTER_MAX_BOOKS = int(os.getenv("ROUTER_MAX_BOOKS", "2"))

RAG_SYSTEM_PROMPT = """Sen D&D kurallarını açıklayan, kaynak odaklı bir asistansın.
Sana verilen bağlam dışındaki bilgileri kuralmış gibi sunma. Bağlam soruyu
yanıtlamıyorsa bunu açıkça söyle. Kuralları yaratıcı anlatımdan ayır ve kısa,
doğrudan bir yanıt ver. Her önemli kural iddiasını [Kitap, s. N] biçiminde
kaynaklandır. Kitap ve sayfa bilgisi bağlamın metadata alanlarında bulunur."""

RAG_USER_PROMPT = """Bağlam:
{context_str}

Soru: {query_str}
Yanıt:"""
