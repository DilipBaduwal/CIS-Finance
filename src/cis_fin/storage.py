"""S3-compatible object-store operations used only by explicit ingestion."""

from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from cis_fin.settings import Settings


class ObjectStoreError(Exception):
    """An object could not be safely verified or stored."""


class ObjectStore(Protocol):
    def ensure_pdf(self, path: Path, object_key: str, checksum: str) -> None:
        ...


class S3ObjectStore:
    def __init__(self, settings: Settings):
        missing = settings.missing_for("storage")
        if missing:
            raise ObjectStoreError("Configure {} in .env before ingestion.".format(", ".join(missing)))
        if settings.s3_endpoint_url:
            parsed = urlparse(settings.s3_endpoint_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ObjectStoreError("S3_ENDPOINT_URL must be a valid http(s) URL.")
            if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ObjectStoreError("Remote object-storage endpoints must use HTTPS.")
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:
            raise ObjectStoreError("S3 client is missing; install requirements.txt.") from exc
        self._client_error = ClientError
        self._boto_core_error = BotoCoreError
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region or "us-east-1",
            config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3, "mode": "standard"}),
        )

    def ensure_pdf(self, path: Path, object_key: str, checksum: str) -> None:
        expected_size = path.stat().st_size
        try:
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=object_key)
            except self._client_error as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if code not in ("404", "NoSuchKey", "NotFound") and status != 404:
                    raise
                head = None

            if head is not None:
                metadata = head.get("Metadata", {})
                if head.get("ContentLength") != expected_size or metadata.get("sha256") != checksum:
                    raise ObjectStoreError(
                        "The deterministic object key already exists with mismatched size or checksum metadata; "
                        "quarantine and investigate it before retrying."
                    )
                return

            self.client.upload_file(
                str(path),
                self.bucket,
                object_key,
                ExtraArgs={
                    "ContentType": "application/pdf",
                    "Metadata": {"sha256": checksum},
                },
            )
            uploaded = self.client.head_object(Bucket=self.bucket, Key=object_key)
            if (
                uploaded.get("ContentLength") != expected_size
                or uploaded.get("Metadata", {}).get("sha256") != checksum
            ):
                raise ObjectStoreError(
                    "Uploaded object failed size/checksum-metadata verification; the ingestion job will remain recoverable."
                )
        except ObjectStoreError:
            raise
        except (self._client_error, self._boto_core_error, OSError) as exc:
            raise ObjectStoreError(
                "Object storage operation failed ({}). Check endpoint, bucket, network, and service-account permissions.".format(
                    type(exc).__name__
                )
            ) from exc
