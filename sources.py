def extract_sources(response) -> list[dict[str, object]]:
    """Return unique, display-ready book/page references from a response."""
    sources = []
    seen = set()
    for item in getattr(response, "source_nodes", []) or []:
        metadata = getattr(item.node, "metadata", {}) or {}
        book = metadata.get("source_book") or metadata.get("source_file")
        page = metadata.get("page_number") or metadata.get("source")
        if not book:
            continue
        key = (str(book), str(page))
        if key in seen:
            continue
        seen.add(key)
        sources.append({"book": str(book), "page": page})
    return sources
