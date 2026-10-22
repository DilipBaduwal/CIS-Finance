"""PostgreSQL connection-string normalization with remote TLS enforcement."""

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def secure_postgres_dsn(dsn: str, timeout_seconds: int = 10) -> str:
    parsed = urlparse(dsn)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        raise ValueError("DATABASE_URL must be a valid postgres:// or postgresql:// URL.")
    parameters = dict(parse_qsl(parsed.query, keep_blank_values=True))
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.hostname.lower() not in local_hosts and parameters.get("sslmode") not in (
        "require",
        "verify-ca",
        "verify-full",
    ):
        parameters["sslmode"] = "require"
    parameters.setdefault("connect_timeout", str(timeout_seconds))
    return urlunparse(parsed._replace(query=urlencode(parameters)))
