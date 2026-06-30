from llama_index.core import Settings
from llama_index.core.query_engine import RouterQueryEngine
from llama_index.core.selectors import LLMSingleSelector
from llama_index.llms.ollama import Ollama
from llama_index.llms.openai_like import OpenAILike

from config import OLLAMA_LLM_MODEL, GEMINI_LLM_MODEL, GEMINI_API_KEY
from retriever import build_tools

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


def build_engine(provider: str = "ollama") -> RouterQueryEngine:
    if provider == "gemini":
        llm = OpenAILike(
            model=GEMINI_LLM_MODEL,
            api_key=GEMINI_API_KEY,
            api_base=GEMINI_BASE,
            is_chat_model=True,
            context_window=1_000_000,
        )
    else:
        llm = Ollama(model=OLLAMA_LLM_MODEL, request_timeout=180.0)

    Settings.llm = llm
    tools = build_tools(llm)

    if not tools:
        raise RuntimeError("Hiç araç bulunamadı. Önce ingestion.py çalıştır.")

    return RouterQueryEngine(
        selector=LLMSingleSelector.from_defaults(llm=llm),
        query_engine_tools=tools,
        verbose=True,
    )
