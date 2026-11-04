"""Index/contents parsing and safe multi-range page selection."""

import re
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class IndexEntry:
    title: str
    start_page: int
    end_page: int
    index_page: int = 0

def merge_page_ranges(ranges: Iterable[tuple]) -> list:
    merged = []
    for start, end in sorted((int(a), int(b)) for a, b in ranges):
        if start < 1 or end < start:
            raise ValueError("invalid page range")
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def parse_index_lines(lines: Iterable[str]) -> list:
    entries = []
    pattern = re.compile(r"^\s*(.+?)(?:\.{2,}|\s)\s*(\d+)\s*(?:[-–—]|\bto\b)\s*(\d+)\s*$|^\s*(.+?)(?:\.{2,}|\s)\s*(\d+)\s*$", re.I)
    cleaned_lines = [" ".join(line.split()) for line in lines if line.strip()]
    stop_markers = ("online integrated annual report", "about this report", "attending the", "our focus on")
    for stop_index, line in enumerate(cleaned_lines):
        if any(marker in line.lower() for marker in stop_markers):
            cleaned_lines = cleaned_lines[:stop_index]
            break
    for index_page, line in enumerate(cleaned_lines, 1):
        match = pattern.match(line.strip())
        if match:
            title = match.group(1) or match.group(4)
            start = int(match.group(2) or match.group(5))
            end = int(match.group(3) or start)
            entries.append(IndexEntry(title.strip(" ."), start, end, index_page))
        elif line.isdigit() and entries is not None:
            # Many designed annual-report contents pages put the title and its
            # page number on separate lines. Pair a standalone page number with
            # the immediately preceding non-numeric title.
            page_number = int(line)
            if page_number > 0 and cleaned_lines[index_page - 2].lower() not in {"contents", "table of contents", "reporting suite 2024-25"}:
                title = cleaned_lines[index_page - 2].strip(" .")
                if title and not title.isdigit():
                    entries.append(IndexEntry(title, page_number, page_number, index_page))
    return entries

def detect_index_entries(pages: Iterable) -> list:
    """Find likely contents pages and return entries with original PDF page numbers."""
    detected = []
    explicit_detected = []
    for page in pages:
        text = getattr(page, "text", "")
        lowered = text.lower()
        page_entries = parse_index_lines(text.splitlines())
        is_likely_index = bool(re.search(r"\b(?:contents|table of contents|index)\b", lowered))
        # Some annual reports use a designed heading or an image-like text
        # layout without the literal word "contents". Three or more dotted/page
        # entries are a reliable secondary signal.
        if not is_likely_index and len(page_entries) < 3:
            continue
        for index, entry in enumerate(page_entries):
            end = entry.end_page
            if end == entry.start_page and index + 1 < len(page_entries):
                next_start = page_entries[index + 1].start_page
                if next_start > entry.start_page:
                    end = next_start - 1
            entry = IndexEntry(entry.title, entry.start_page, end, int(getattr(page, "page_number", entry.index_page)))
            if entry.start_page > 0:
                detected.append(entry)
                if is_likely_index:
                    explicit_detected.append(entry)
    if explicit_detected:
        by_page = {}
        for entry in explicit_detected:
            by_page.setdefault(entry.index_page, []).append(entry)
        detected = max(by_page.values(), key=len)
    unique = {}
    for entry in detected:
        unique[(entry.title.lower(), entry.start_page, entry.end_page)] = entry
    return list(unique.values())

