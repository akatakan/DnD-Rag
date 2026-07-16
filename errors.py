class AppError(RuntimeError):
    """An operational error that can be shown safely in the UI."""


def unavailable(service: str, action: str) -> AppError:
    return AppError(
        f"{service} servisine ulaşılamadı. {action} ve ardından tekrar deneyin."
    )


def normalize_error(error: Exception, provider: str) -> AppError:
    if isinstance(error, AppError):
        return error

    chain = (error, error.__cause__, error.__context__)
    details = " ".join(
        f"{type(item).__module__}.{type(item).__name__} {item}".lower()
        for item in chain
        if item is not None
    )
    if "qdrant" in details or "6333" in details:
        return unavailable("Qdrant", "Docker servisinin çalıştığını doğrulayın")
    if provider == "gemini":
        if any(term in details for term in ("401", "403", "api key", "unauth")):
            return AppError("Gemini API anahtarı geçersiz veya yetkisiz.")
        return unavailable("Gemini", "API anahtarını ve ağ bağlantısını doğrulayın")
    return unavailable(
        "Ollama",
        "Ollama'nın çalıştığını ve gerekli modellerin yüklü olduğunu doğrulayın",
    )
