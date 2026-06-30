import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATA_DIR          = Path(__file__).parent / "data"
METADATA_FILE     = DATA_DIR / "metadata.yaml"

QDRANT_URL        = "http://localhost:6333"
COLLECTION_PREFIX = "dnd_"

OLLAMA_LLM_MODEL  = "llama3.2:3b"
GEMINI_LLM_MODEL  = "gemini-2.5-flash-lite"
EMBED_MODEL       = "nomic-embed-text"

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")

CHUNK_SIZE        = 512
CHUNK_OVERLAP     = 64
