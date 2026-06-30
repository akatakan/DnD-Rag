# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

D&D sorularını yanıtlamak için Mistral LLM + Chroma vektör DB + LangChain kullanan Streamlit tabanlı RAG chatbot. Tamamen lokaldir, API anahtarı gerekmez — Ollama üzerinden çalışır.

## Çalıştırma

```bash
streamlit run dnd-rag.py
```

**Ön koşullar:**
- Ollama çalışıyor olmalı, iki model yüklü olmalı:
  ```bash
  ollama pull mistral
  ollama pull nomic-embed-text
  ```
- PDF dosyaları `dnd-rag.py` içindeki dizinde olmalı

## Mimari

Tek dosya (`dnd-rag.py`), iki fonksiyon:

**`pdf_loader()`** — PDF → chunk → embedding → Chroma
- `PyPDFLoader` ile PDF okur
- `RecursiveCharacterTextSplitter` ile chunk'lar (1024 char, 100 overlap)
- `OllamaEmbeddings("nomic-embed-text")` ile embed eder
- Chroma collection: `"local-rag"`

**`get_llm_response(form_input)`** — soru → MultiQuery → Mistral → cevap
- `ChatOllama("mistral")` ile LLM başlatır
- `MultiQueryRetriever`: kullanıcı sorusundan 5 alternatif soru üretir, hepsini vektör DB'de arar — tek sorgudan daha iyi retrieval sağlar
- Retriever → RAG prompt → Mistral → `StrOutputParser` zinciri

## Bilinen Sorunlar

- **Hardcoded path**: PDF dizini Windows'a özgü mutlak yol — taşınabilir değil
- **pdf_loader her sorguda çağrılıyor**: PDF'ler her sorguda yeniden yükleniyor, cache yok
- **Tek kitap collection**: 3 kitap tek Chroma collection'ında, hangi kitaptan geldiği bilgisi kaybolmuş
- **Sadece semantic search**: Hybrid search (BM25 + semantic) yok
- **requirements.txt yok**: Bağımlılıklar belgelenmemiş
