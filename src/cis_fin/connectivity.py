"""Read-only checks for configured external services."""

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


@dataclass(frozen=True)
class ProbeResult:
    service: str
    ok: bool
    message: str


def _missing_result(settings: Settings, service: str) -> Optional[ProbeResult]:
    missing = settings.missing_for(service)
    if not missing:
        return None
    names = ", ".join(missing)
    return ProbeResult(
        service,
        False,
        "Not configured: set {} in your local .env. No connection was attempted.".format(names),
    )


def _is_loopback(hostname: Optional[str]) -> bool:
    if hostname in ("localhost",):
        return True
    if not hostname:
        return False
    try:
        return socket.gethostbyname(hostname).startswith("127.")
    except (OSError, socket.gaierror):
        return False


def check_database(settings: Settings) -> ProbeResult:
    missing = _missing_result(settings, "database")
    if missing:
        return missing

    url = urlparse(settings.database_url or "")
    if url.scheme not in ("postgres", "postgresql"):
        return ProbeResult("PostgreSQL", False, "DATABASE_URL must use the postgres:// or postgresql:// scheme.")

    try:
        import psycopg
    except ImportError:
        return ProbeResult(
            "PostgreSQL",
            False,
            "PostgreSQL driver is missing. Run `python -m pip install -r requirements.txt`.",
        )

    try:
        safe_url = secure_postgres_dsn(settings.database_url or "", timeout_seconds=5)
    except ValueError as exc:
        return ProbeResult("PostgreSQL", False, str(exc))
    try:
        with psycopg.connect(safe_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
        if row != (1,):
            return ProbeResult("PostgreSQL", False, "The server responded, but the read-only test query returned an unexpected result.")
        return ProbeResult("PostgreSQL", True, "Connected successfully; read-only SELECT 1 passed.")
    except psycopg.OperationalError as exc:
        return ProbeResult(
            "PostgreSQL",
            False,
            "Could not connect ({}). Check DATABASE_URL, network access, TLS, and provider allowlists; no data was changed.".format(
                type(exc).__name__
            ),
        )
    except psycopg.Error as exc:
        return ProbeResult(
            "PostgreSQL",
            False,
            "Database rejected the read-only check ({}). Verify credentials, database name, and role permissions; no data was changed.".format(
                type(exc).__name__
            ),
        )


def check_qdrant(settings: Settings) -> ProbeResult:
    missing = _missing_result(settings, "qdrant")
    if missing:
        return missing

    parsed = urlparse(settings.qdrant_url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ProbeResult("Qdrant", False, "QDRANT_URL must be a valid http(s) URL.")
    if parsed.scheme != "https" and not _is_loopback(parsed.hostname):
        return ProbeResult(
            "Qdrant",
            False,
            "Remote Qdrant URLs must use HTTPS. Use HTTP only for a local loopback service.",
        )
    if not _is_loopback(parsed.hostname) and not settings.qdrant_api_key:
        return ProbeResult(
            "Qdrant",
            False,
            "Qdrant Cloud requires an API key. Create a cluster API key and set QDRANT_API_KEY in your local .env; no request was attempted.",
        )

    endpoint = (settings.qdrant_url or "").rstrip("/") + "/collections"
    headers = {"Accept": "application/json"}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                return ProbeResult("Qdrant", False, "Qdrant returned HTTP {} for a read-only collection-list request.".format(response.status))
            json.loads(response.read().decode("utf-8"))
        return ProbeResult("Qdrant", True, "Connected successfully; read-only collection-list request passed.")
    except urllib.error.HTTPError as exc:
        reason = "Check QDRANT_URL and API-key permissions; no collection was created or modified."
        if exc.code in (401, 403):
            reason = "Qdrant denied access (HTTP {}). Check QDRANT_API_KEY and endpoint permissions.".format(exc.code)
        return ProbeResult("Qdrant", False, reason)
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
        return ProbeResult(
            "Qdrant",
            False,
            "Could not complete the read-only Qdrant check ({}). Check endpoint, DNS, TLS, and firewall.".format(
                type(exc).__name__
            ),
        )


def check_storage(settings: Settings) -> ProbeResult:
    missing = _missing_result(settings, "storage")
    if missing:
        return missing

    if settings.s3_endpoint_url:
        endpoint = urlparse(settings.s3_endpoint_url)
        if endpoint.scheme not in ("http", "https") or not endpoint.netloc:
            return ProbeResult("Object storage", False, "S3_ENDPOINT_URL must be a valid http(s) URL.")
        if endpoint.scheme != "https" and not _is_loopback(endpoint.hostname):
            return ProbeResult(
                "Object storage",
                False,
                "Remote object-storage endpoints must use HTTPS. Use HTTP only for a local loopback service.",
            )

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return ProbeResult(
            "Object storage",
            False,
            "S3 client is missing. Run `python -m pip install -r requirements.txt`.",
        )

    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region or "us-east-1",
        )
        client.head_bucket(Bucket=settings.s3_bucket)
        return ProbeResult("Object storage", True, "Connected successfully; read-only bucket check passed.")
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "unknown"))
        if code in ("403", "AccessDenied"):
            message = "Storage denied access. Verify the bucket name and grant the service account bucket-read permission."
        elif code in ("404", "NoSuchBucket", "NotFound"):
            message = "Bucket was not found. Verify S3_BUCKET and the configured storage account/region."
        else:
            message = "Storage rejected the read-only bucket check ({}). Verify endpoint, bucket, and least-privilege permissions.".format(code)
        return ProbeResult("Object storage", False, message)
    except (BotoCoreError, OSError, ValueError) as exc:
        return ProbeResult(
            "Object storage",
            False,
            "Could not complete the read-only storage check ({}). Check S3_ENDPOINT_URL, network access, and TLS.".format(
                type(exc).__name__
            ),
        )
