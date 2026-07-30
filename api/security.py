from urllib.parse import urlparse


LOCAL_AUTH_PEPPER = "tetsu-local-development-pepper"


def validate_public_security(
    public_mode: bool,
    auth_pepper: str,
    origins: list[str],
) -> None:
    if not public_mode:
        return
    if len(auth_pepper) < 32 or auth_pepper == LOCAL_AUTH_PEPPER:
        raise RuntimeError(
            "PUBLIC_MODE için en az 32 karakterlik özel AUTH_PEPPER gereklidir."
        )
    if not origins:
        raise RuntimeError("PUBLIC_MODE için WEB_ORIGIN gereklidir.")
    for origin in origins:
        parsed = urlparse(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path != ""
            or parsed.params
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise RuntimeError(
                "PUBLIC_MODE WEB_ORIGIN değerleri wildcard içermeyen HTTPS origin olmalıdır."
            )
