"""Page-aware PDF extraction and deterministic streaming chunking."""

import hashlib
import importlib.metadata
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Protocol, Sequence, Tuple


CHUNK_NAMESPACE = uuid.UUID("20aab77e-323a-4757-9a9f-c61f852a0849")


class ExtractionError(Exception):
    """A PDF could not be extracted into usable, page-referenced text."""


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    checksum: str
    page_count: int
    pages: Tuple[PageText, ...]
    warnings: Tuple[str, ...]
    engine: str
    engine_version: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    chunk_hash: str
    chunk_index: int
    page_start: int
    page_end: int
    text: str


class PageExtractor(Protocol):
    name: str

    def extract_pages(
        self,
        path: Path,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
    ) -> Iterator[PageText]:
        ...

    def version(self) -> str:
        ...


class PyMuPDFExtractor:
    name = "pymupdf"

    def extract_pages(
        self,
        path: Path,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
    ) -> Iterator[PageText]:
        try:
            import fitz
        except ImportError as exc:
            raise ExtractionError("PyMuPDF is unavailable. Install the pinned project requirements.") from exc

        try:
            with fitz.open(str(path)) as document:
                if document.is_encrypted:
                    raise ExtractionError("PDF is encrypted; provide an authorized, decrypted copy.")
                first = page_start or 1
                last = page_end or len(document)
                if first < 1 or last < first or last > len(document):
                    raise ExtractionError(
                        "Page range must be between 1 and {}.".format(len(document))
                    )
                for page_index in range(first - 1, last):
                    page = document.load_page(page_index)
                    yield PageText(page_index + 1, page.get_text("text", sort=True).strip())
        except ExtractionError:
            raise
        except (RuntimeError, ValueError, OSError) as exc:
            raise ExtractionError(
                "PyMuPDF could not read the PDF ({}). Verify that the file is a valid, accessible PDF.".format(
                    type(exc).__name__
                )
            ) from exc

    def version(self) -> str:
        try:
            return importlib.metadata.version("PyMuPDF")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"


class DoclingExtractor:
    name = "docling"

    def extract_pages(
        self,
        path: Path,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
    ) -> Iterator[PageText]:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise ExtractionError(
                "Docling is not installed. Install the optional Docling integration in the project environment."
            ) from exc

        try:
            result = DocumentConverter().convert(str(path))
            pages = {}
            for item, _level in result.document.iterate_items():
                text = getattr(item, "text", None)
                provenance = getattr(item, "prov", None) or []
                if not text:
                    continue
                for source in provenance:
                    page_number = getattr(source, "page_no", None)
                    if isinstance(page_number, int) and page_number > 0:
                        pages.setdefault(page_number, []).append(text)
            for page_number in sorted(pages):
                if page_start is not None and page_number < page_start:
                    continue
                if page_end is not None and page_number > page_end:
                    continue
                yield PageText(page_number, "\n".join(pages[page_number]).strip())
        except (RuntimeError, ValueError, OSError) as exc:
            raise ExtractionError(
                "Docling extraction failed ({}). Check the local model/runtime configuration and PDF.".format(
                    type(exc).__name__
                )
            ) from exc

    def version(self) -> str:
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"


class MinerUExtractor:
    name = "mineru"

    def extract_pages(
        self,
        path: Path,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
    ) -> Iterator[PageText]:
        raise ExtractionError(
            "MinerU is not integrated in this release. Its CLI/output contract must be selected and tested before use; "
            "choose PyMuPDF or Docling instead."
        )

    def version(self) -> str:
        return "not-integrated"


EXTRACTORS = {
    "pymupdf": PyMuPDFExtractor,
    "docling": DoclingExtractor,
    "mineru": MinerUExtractor,
}


def available_extractors() -> Tuple[str, ...]:
    return tuple(EXTRACTORS)


def extractor_version(engine: str) -> str:
    if engine not in EXTRACTORS:
        raise ExtractionError(
            "Unknown extraction engine {!r}. Available engines: {}.".format(engine, ", ".join(available_extractors()))
        )
    return EXTRACTORS[engine]().version()


def pdf_page_count(path: Path) -> int:
    if not path.is_file():
        raise ExtractionError("Input PDF does not exist or is not a file.")
    try:
        import fitz
        with fitz.open(str(path)) as document:
            if document.is_encrypted:
                raise ExtractionError("PDF is encrypted; provide an authorized, decrypted copy.")
            return len(document)
    except ExtractionError:
        raise
    except ImportError as exc:
        raise ExtractionError("PyMuPDF is required to inspect the PDF page count.") from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise ExtractionError(
            "Could not inspect PDF page count ({}); verify that the file is a valid PDF.".format(type(exc).__name__)
        ) from exc


def stream_pdf_pages(
    path: Path,
    engine: str = "pymupdf",
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
) -> Iterator[PageText]:
    """Yield page text without retaining all pages in memory."""
    if engine not in EXTRACTORS:
        raise ExtractionError(
            "Unknown extraction engine {!r}. Available engines: {}.".format(engine, ", ".join(available_extractors()))
        )
    if not path.is_file():
        raise ExtractionError("Input PDF does not exist or is not a file.")
    if path.suffix.lower() != ".pdf":
        raise ExtractionError("Only PDF input is supported by the current extraction phase.")
    total_pages = pdf_page_count(path)
    first = page_start or 1
    last = page_end or total_pages
    if first < 1 or last < first or last > total_pages:
        raise ExtractionError("Page range must be between 1 and {}.".format(total_pages))
    yield from EXTRACTORS[engine]().extract_pages(path, first, last)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ExtractionError("Cannot read input PDF ({}). Check file path and permissions.".format(type(exc).__name__)) from exc
    return digest.hexdigest()


def extract_pdf(
    path: Path,
    engine: str = "pymupdf",
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
) -> ExtractedDocument:
    if engine not in EXTRACTORS:
        raise ExtractionError(
            "Unknown extraction engine {!r}. Available engines: {}.".format(engine, ", ".join(available_extractors()))
        )
    if not path.is_file():
        raise ExtractionError("Input PDF does not exist or is not a file: {}".format(path))
    if path.suffix.lower() != ".pdf":
        raise ExtractionError("Only PDF input is supported by the current extraction phase.")

    extractor = EXTRACTORS[engine]()
    total_pages = pdf_page_count(path)
    pages = tuple(stream_pdf_pages(path, engine, page_start, page_end))
    if not pages:
        raise ExtractionError("PDF has no extractable pages; it cannot be marked successfully extracted.")
    if any(page.page_number < 1 for page in pages):
        raise ExtractionError("Extraction engine returned an invalid page number.")

    non_whitespace_chars = sum(len(page.text.strip()) for page in pages)
    warnings: List[str] = []
    if non_whitespace_chars == 0:
        raise ExtractionError(
            "No selectable text was extracted. This may be a scanned PDF; use an OCR-capable extraction engine."
        )
    if non_whitespace_chars < max(50, len(pages) * 40):
        warnings.append("Low text yield; inspect the PDF for scanned pages or extraction quality issues.")
    return ExtractedDocument(
        checksum=file_sha256(path),
        page_count=total_pages,
        pages=pages,
        warnings=tuple(warnings),
        engine=engine,
        engine_version=extractor.version(),
    )


def _paragraphs(page: PageText) -> Iterator[str]:
    for paragraph in page.text.splitlines():
        value = " ".join(paragraph.split())
        if value:
            yield value


def iter_chunks(
    pages: Iterable[PageText],
    document_checksum: str,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
    document_scope: Optional[str] = None,
) -> Iterator[Chunk]:
    """Yield deterministic page-cited chunks; token sizing is approximated at four chars/token."""
    if target_tokens < 1:
        raise ValueError("target_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")
    if len(document_checksum) != 64:
        raise ValueError("document_checksum must be a SHA-256 hex digest")
    try:
        int(document_checksum, 16)
    except ValueError as exc:
        raise ValueError("document_checksum must be a SHA-256 hex digest") from exc
    stable_scope = document_scope or document_checksum
    if not stable_scope.strip():
        raise ValueError("document_scope must not be empty")

    max_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    buffer: List[Tuple[str, int]] = []
    buffer_chars = 0
    index = 0

    def render(parts: Sequence[Tuple[str, int]], chunk_index: int) -> Chunk:
        text = "\n".join(part for part, _ in parts).strip()
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        stable_id = uuid.uuid5(
            CHUNK_NAMESPACE,
            "{}:{}:{}:{}".format(stable_scope, document_checksum, chunk_index, chunk_hash),
        )
        return Chunk(
            chunk_id=str(stable_id),
            chunk_hash=chunk_hash,
            chunk_index=chunk_index,
            page_start=min(page for _, page in parts),
            page_end=max(page for _, page in parts),
            text=text,
        )

    for page in pages:
        for paragraph in _paragraphs(page):
            while len(paragraph) > max_chars:
                prefix = paragraph[:max_chars]
                boundary = prefix.rfind(" ")
                split_at = boundary if boundary > max_chars // 2 else max_chars
                segment, paragraph = paragraph[:split_at].strip(), paragraph[split_at:].strip()
                if buffer:
                    yield render(buffer, index)
                    index += 1
                    buffer = []
                    buffer_chars = 0
                yield render([(segment, page.page_number)], index)
                index += 1
            if buffer and buffer_chars + len(paragraph) + 1 > max_chars:
                completed = buffer
                yield render(completed, index)
                index += 1
                overlap = []
                overlap_size = 0
                for part in reversed(completed):
                    if overlap_size + len(part[0]) + 1 > overlap_chars:
                        break
                    overlap.insert(0, part)
                    overlap_size += len(part[0]) + 1
                buffer = overlap
                buffer_chars = overlap_size
            buffer.append((paragraph, page.page_number))
            buffer_chars += len(paragraph) + 1
    if buffer:
        yield render(buffer, index)


def write_jsonl(chunks: Iterable[Chunk], destination: Path) -> int:
    """Write chunk records incrementally; refuse to overwrite existing files."""
    if destination.exists():
        raise ExtractionError("Output already exists; choose a new path to avoid overwriting prior extraction.")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError("Could not create output directory ({}). Check destination permissions.".format(type(exc).__name__)) from exc
    count = 0
    created = False
    try:
        with destination.open("x", encoding="utf-8") as output:
            created = True
            for chunk in chunks:
                output.write(json.dumps(chunk.__dict__, ensure_ascii=False) + "\n")
                count += 1
    except FileExistsError as exc:
        raise ExtractionError("Output already exists; choose a new path to avoid overwriting prior extraction.") from exc
    except (OSError, ValueError, ExtractionError) as exc:
        if created:
            try:
                os.unlink(str(destination))
            except OSError:
                pass
        if isinstance(exc, ExtractionError):
            raise
        if isinstance(exc, ValueError):
            raise
        raise ExtractionError("Could not write extraction output ({}). Check destination permissions.".format(type(exc).__name__)) from exc
    return count
