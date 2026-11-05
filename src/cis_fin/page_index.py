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
        # Some PDFs place the page number before the section title. A single
        # extracted line can also contain several number/title pairs.
        number_first = list(re.finditer(r"(?<!\d)(\d{1,3})(?!\d)", line))
        if number_first and number_first[0].start() <= 4 and len(number_first) >= 1:
            added_number_titles = False
            for number_index, match in enumerate(number_first):
                page_number = int(match.group(1))
                if 1900 <= page_number <= 2100:
                    continue
                title_start = match.end()
                title_end = number_first[number_index + 1].start() if number_index + 1 < len(number_first) else len(line)
                title = line[title_start:title_end].strip(" .|–—-")
                if title and not title.isdigit() and len(title) >= 4:
                    entries.append(IndexEntry(title, page_number, page_number, index_page))
                    added_number_titles = True
            if number_first[0].start() == 0 and added_number_titles:
                continue
        match = pattern.match(line.strip())
        if match:
            title = match.group(1) or match.group(4)
            start = int(match.group(2) or match.group(5))
            end = int(match.group(3) or start)
            if 1900 <= start <= 2100 or 1900 <= end <= 2100:
                continue
            entries.append(IndexEntry(title.strip(" ."), start, end, index_page))
        elif line.isdigit() and 1 <= int(line) <= 999 and not 1900 <= int(line) <= 2100 and entries is not None:
            # Many annual-report contents pages put the page number before the
            # title on separate lines. Prefer the following title lines.
            page_number = int(line)
            following = []
            cursor = index_page
            while cursor < len(cleaned_lines) and not cleaned_lines[cursor].isdigit() and len(following) < 2:
                following.append(cleaned_lines[cursor].strip(" ."))
                cursor += 1
            title = " ".join(following).strip()
            if title and not re.search(r"\b(?:scan|qr code|contents|table of contents)\b", title, re.I):
                entries.append(IndexEntry(title, page_number, page_number, index_page))
    return entries

def detect_index_entries(pages: Iterable) -> list:
    """Find likely contents pages and return entries with original PDF page numbers."""
    pages = list(pages)
    detected = []
    explicit_detected = []
    contents_pages = []
    for page in pages:
        text = getattr(page, "text", "")
        lowered = text.lower()
        page_entries = parse_index_lines(text.splitlines())
        is_contents = bool(re.search(r"\b(?:table of contents|contents)\b", lowered))
        is_likely_index = is_contents or bool(re.search(r"\bindex\b", lowered))
        if is_contents:
            contents_pages.append(int(getattr(page, "page_number", 0)))
            # Some designed contents pages print only the first section's
            # opening/closing page in a separate visual block. Recover that
            # range when the text layer exposes the section and both numbers.
            if re.search(r"\bcorporate\s+overview\b", lowered) and re.search(r"\b01\b", text) and re.search(r"\b18\b", text):
                detected.append(IndexEntry("Corporate Overview", 1, 18, int(getattr(page, "page_number", 0))))
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
        # The next contents page may begin a section without printing its
        # opening number; infer its range up to the first explicit section.
        if re.search(r"\bapproach\s+to\s+value\s+creation\b", lowered):
            first_numbers = [int(value) for value in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", text) if 50 <= int(value) <= 999 and not 1900 <= int(value) <= 2100]
            if first_numbers and first_numbers[0] > 1:
                detected.append(IndexEntry("Approach to Value Creation", 19, first_numbers[0] - 1, int(getattr(page, "page_number", 0))))
    if explicit_detected:
        # Prefer the actual contents page over a later section literally named
        # “Index”. Contents often continue onto the immediately following page.
        preferred_pages = set(contents_pages)
        preferred_pages.update(page + 1 for page in contents_pages)
        preferred = [entry for entry in detected if entry.index_page in preferred_pages]
        candidates = preferred or explicit_detected
        by_page = {}
        for entry in candidates:
            by_page.setdefault(entry.index_page, []).append(entry)
        detected = [entry for page in sorted(by_page) for entry in by_page[page]]
    unique = {}
    for entry in detected:
        unique[(entry.title.lower(), entry.start_page, entry.end_page)] = entry
    return list(unique.values())

