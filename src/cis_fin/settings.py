"""Environment-backed application settings without eager cloud dependencies."""

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class Settings:
    database_url: Optional[str]
    qdrant_url: Optional[str]
    qdrant_api_key: Optional[str]
    s3_endpoint_url: Optional[str]
    s3_bucket: Optional[str]
    s3_access_key_id: Optional[str]
    s3_secret_access_key: Optional[str]
    s3_region: Optional[str]
    extractor: str
    max_concurrent_ingestions: int
    ollama_base_url: str
    ollama_model: str
    ollama_embedding_model: str
    qdrant_collection: str
    qdrant_embedding_version: str

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        if environ is None:
            try:
                from dotenv import load_dotenv
            except ImportError as exc:
                raise RuntimeError("python-dotenv is missing; install requirements.txt.") from exc
            env_file = os.environ.get("CIS_FIN_ENV_FILE")
            load_dotenv(dotenv_path=env_file, override=False)
            values = os.environ
        else:
            values = environ

        def configured(name: str) -> Optional[str]:
            value = values.get(name, "").strip()
            return value or None

        concurrency = configured("CIS_FIN_MAX_CONCURRENT_INGESTIONS") or "2"
        try:
            max_concurrent_ingestions = int(concurrency)
        except ValueError as exc:
            raise ValueError("CIS_FIN_MAX_CONCURRENT_INGESTIONS must be an integer between 1 and 16.") from exc
        if not 1 <= max_concurrent_ingestions <= 16:
            raise ValueError("CIS_FIN_MAX_CONCURRENT_INGESTIONS must be between 1 and 16.")
        qdrant_collection = configured("QDRANT_COLLECTION") or "cis_fin_chunks_nomic_v1"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", qdrant_collection):
            raise ValueError("QDRANT_COLLECTION may contain only letters, numbers, underscores, and hyphens.")

        return cls(
            database_url=configured("DATABASE_URL"),
            qdrant_url=configured("QDRANT_URL"),
            qdrant_api_key=configured("QDRANT_API_KEY"),
            s3_endpoint_url=configured("S3_ENDPOINT_URL"),
            s3_bucket=configured("S3_BUCKET"),
            s3_access_key_id=configured("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=configured("S3_SECRET_ACCESS_KEY"),
            s3_region=configured("S3_REGION"),
            extractor=configured("CIS_FIN_EXTRACTOR") or "pymupdf",
            max_concurrent_ingestions=max_concurrent_ingestions,
            ollama_base_url=configured("OLLAMA_BASE_URL") or "http://127.0.0.1:11434",
            ollama_model=configured("OLLAMA_MODEL") or "qwen2.5:3b",
            ollama_embedding_model=configured("OLLAMA_EMBEDDING_MODEL") or "nomic-embed-text:latest",
            qdrant_collection=qdrant_collection,
            qdrant_embedding_version=configured("QDRANT_EMBEDDING_VERSION") or "nomic-embed-text-v1",
        )

    def missing_for(self, service: str) -> Tuple[str, ...]:
        required = {
            "database": (("DATABASE_URL", self.database_url),),
            "qdrant": (("QDRANT_URL", self.qdrant_url),),
            "storage": (
                ("S3_BUCKET", self.s3_bucket),
                ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
            ),
        }
        if service not in required:
            raise ValueError("Unknown service configuration: {}".format(service))
        return tuple(name for name, value in required[service] if not value)
