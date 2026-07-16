from collections.abc import Callable

from llama_index.core import ChatPromptTemplate, Settings
from llama_index.core.base.response.schema import Response
from llama_index.core.postprocessor import LLMRerank
from llama_index.core.response_synthesizers import get_response_synthesizer
from llama_index.core.schema import QueryBundle
from llama_index.core.tools import ToolMetadata
from llama_index.llms.ollama import Ollama
from llama_index.llms.openai_like import OpenAILike

from config import (
    GEMINI_API_KEY,
    GEMINI_LLM_MODEL,
    HYBRID_ENABLED,
    OLLAMA_LLM_MODEL,
    RAG_SYSTEM_PROMPT,
    RAG_USER_PROMPT,
    RERANK_ENABLED,
    RERANK_TOP_N,
    ROUTER_MAX_BOOKS,
)
from errors import AppError, normalize_error
from retriever import build_retrievers
from router import RobustBookSelector

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
SYNTHESIS_TOP_N = 4
ProgressCallback = Callable[[str, str], None]

CREATIVE_PROMPT = """Sen bir D&D sahne anlatıcısısın. Aşağıdaki kaynaklı kural
cevabını değiştirmeden, oyuncunun sorusuna kısa ve canlı bir sahne anlatımıyla
cevap ver. Yeni mekanik, sayı veya kural uydurma. Kural referanslarını aynen koru.
Oyun state'ini yalnızca anlatı bağlamı olarak kullan.

Soru: {query}
Kaynaklı kural cevabı: {rule_answer}
Oyun bağlamı: {game_context}

Anlatım:"""


def build_llm(provider: str):
    if provider not in {"ollama", "gemini"}:
        raise AppError(f"Desteklenmeyen LLM sağlayıcısı: {provider}")
    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise AppError("Gemini için GEMINI_API_KEY tanımlanmalıdır.")
        return OpenAILike(
            model=GEMINI_LLM_MODEL,
            api_key=GEMINI_API_KEY,
            api_base=GEMINI_BASE,
            is_chat_model=True,
            context_window=1_000_000,
        )
    return Ollama(model=OLLAMA_LLM_MODEL, request_timeout=180.0)


class MultiBookQueryEngine:
    def __init__(self, llm, selector, retrievers, catalog, rerank_enabled: bool):
        self._llm = llm
        self._selector = selector
        self._retrievers = retrievers
        self._book_ids = list(retrievers)
        self._book_titles = {book_id: catalog[book_id].title for book_id in self._book_ids}
        self._metadatas = [
            ToolMetadata(name=book_id, description=catalog[book_id].description)
            for book_id in self._book_ids
        ]
        self._reranker = LLMRerank(llm=llm, top_n=RERANK_TOP_N) if rerank_enabled else None
        rag_prompt = ChatPromptTemplate.from_messages(
            [("system", RAG_SYSTEM_PROMPT), ("user", RAG_USER_PROMPT)]
        )
        self._synthesizer = get_response_synthesizer(
            llm=llm, text_qa_template=rag_prompt, response_mode="compact"
        )

    @staticmethod
    def _notify(progress: ProgressCallback | None, stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    def route(self, query: str) -> tuple[list[str], object]:
        result = self._selector.select(self._metadatas, query)
        return [self._book_ids[index] for index in result.inds], result

    @staticmethod
    def _balanced_nodes(nodes_by_book: dict[str, list]) -> list:
        if not nodes_by_book:
            return []
        quota = max(1, SYNTHESIS_TOP_N // len(nodes_by_book))
        selected = [node for nodes in nodes_by_book.values() for node in nodes[:quota]]
        remaining = sorted(
            [node for nodes in nodes_by_book.values() for node in nodes[quota:]],
            key=lambda item: item.score if item.score is not None else float("-inf"),
            reverse=True,
        )
        selected.extend(remaining[: max(0, SYNTHESIS_TOP_N - len(selected))])
        return selected[:SYNTHESIS_TOP_N]

    @staticmethod
    def _preserve_selected_books(nodes: list, nodes_by_book: dict[str, list]) -> list:
        present = {node.node.metadata.get("source_book_id") for node in nodes}
        for book_id, candidates in nodes_by_book.items():
            if book_id not in present and candidates:
                if len(nodes) >= SYNTHESIS_TOP_N:
                    nodes[-1] = candidates[0]
                else:
                    nodes.append(candidates[0])
                present.add(book_id)
        return nodes

    def query(
        self,
        query: str,
        progress: ProgressCallback | None = None,
        memory_context: str = "",
        game_context: str = "",
        response_mode: str = "rules",
    ):
        self._notify(progress, "routing", "Kaynak kapsamı belirleniyor")
        book_ids, selector_result = self.route(query)
        titles = [self._book_titles[book_id] for book_id in book_ids]
        self._notify(progress, "routing", f"Seçilen kaynaklar: {', '.join(titles)}")

        nodes_by_book = {}
        for book_id in book_ids:
            title = self._book_titles[book_id]
            self._notify(progress, "reading", f"{title} taranıyor")
            nodes = self._retrievers[book_id].retrieve(query)
            nodes_by_book[book_id] = nodes
            self._notify(progress, "reading", f"{title}: {len(nodes)} aday parça bulundu")

        if self._reranker:
            candidates = [node for nodes in nodes_by_book.values() for node in nodes]
            self._notify(progress, "reranking", f"{len(candidates)} aday yeniden sıralanıyor")
            nodes = self._reranker.postprocess_nodes(
                candidates, query_bundle=QueryBundle(query_str=query)
            )
            nodes = self._preserve_selected_books(nodes, nodes_by_book)
            self._notify(progress, "reranking", f"En ilgili {len(nodes)} parça seçildi")
        else:
            nodes = self._balanced_nodes(nodes_by_book)

        synthesis_query = query
        context_parts = []
        if memory_context:
            context_parts.append(f"Konuşma hafızası:\n{memory_context}")
        if game_context:
            context_parts.append(f"Güncel oyun state'i:\n{game_context}")
        if context_parts:
            synthesis_query = f"{query}\n\n" + "\n\n".join(context_parts)

        self._notify(progress, "synthesis", f"{len(nodes)} kaynak parçasından kural cevabı hazırlanıyor")
        rule_response = self._synthesizer.synthesize(query=synthesis_query, nodes=nodes)
        rule_response.metadata = rule_response.metadata or {}
        rule_response.metadata.update(
            selector_result=selector_result,
            selected_books=book_ids,
            response_mode=response_mode,
            rule_answer=str(rule_response),
        )

        if response_mode == "story":
            self._notify(progress, "creative", "Kural cevabı sahne anlatımına dönüştürülüyor")
            creative = self._llm.complete(
                CREATIVE_PROMPT.format(
                    query=query,
                    rule_answer=str(rule_response),
                    game_context=game_context or "Oyun state'i yok.",
                )
            )
            response = Response(
                response=str(creative),
                source_nodes=rule_response.source_nodes,
                metadata=rule_response.metadata,
            )
        else:
            response = rule_response

        self._notify(progress, "complete", "Yanıt ve kaynaklar hazır")
        return response


class SafeQueryEngine:
    def __init__(self, engine: MultiBookQueryEngine, provider: str):
        self._engine = engine
        self._provider = provider

    def query(self, query: str, **kwargs):
        try:
            return self._engine.query(query, **kwargs)
        except Exception as error:
            raise normalize_error(error, self._provider) from error

    def route(self, query: str) -> list[str]:
        try:
            books, _ = self._engine.route(query)
            return books
        except Exception as error:
            raise normalize_error(error, self._provider) from error


def build_engine(
    provider: str = "ollama",
    allowed_books: tuple[str, ...] | None = None,
    page_from: int | None = None,
    page_to: int | None = None,
    hybrid_enabled: bool = HYBRID_ENABLED,
    rerank_enabled: bool = RERANK_ENABLED,
) -> SafeQueryEngine:
    llm = build_llm(provider)
    Settings.llm = llm
    retrievers, catalog = build_retrievers(
        allowed_books=allowed_books,
        page_from=page_from,
        page_to=page_to,
        hybrid_enabled=hybrid_enabled,
    )
    if not retrievers:
        raise AppError("İndekslenmiş kitap bulunamadı. Önce ingestion.py çalıştırın.")
    selector = RobustBookSelector(llm=llm, max_outputs=min(ROUTER_MAX_BOOKS, len(retrievers)))
    return SafeQueryEngine(
        MultiBookQueryEngine(llm, selector, retrievers, catalog, rerank_enabled),
        provider,
    )
