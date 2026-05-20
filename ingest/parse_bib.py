"""
Parses .bib files and resolves \cite{key} tokens in chunk text.

parse_bib_file(path)  -> dict[key, BibEntry]
resolve_citations(text, bib) -> str   (replaces [key] with human-readable refs)
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BibEntry:
    key: str
    entry_type: str                  # article, inproceedings, book, misc, …
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    booktitle: str = ""
    volume: str = ""
    pages: str = ""
    publisher: str = ""
    url: str = ""

    def short_ref(self) -> str:
        """Single-line citation string suitable for embedding in prose."""
        parts = []
        if self.authors:
            first = self.authors[0]
            last_name = first.split(",")[0].strip() if "," in first else first.split()[-1]
            suffix = " et al." if len(self.authors) > 2 else (
                f" and {self.authors[1].split(',')[0].strip()}" if len(self.authors) == 2 else ""
            )
            parts.append(last_name + suffix)
        if self.year:
            parts.append(f"({self.year})")
        if self.title:
            parts.append(f'"{self.title}"')
        venue = self.journal or self.booktitle or self.publisher
        if venue:
            parts.append(venue)
        return ", ".join(parts) if parts else self.key


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_bib_file(path: str) -> dict[str, BibEntry]:
    """Return a dict mapping cite-key -> BibEntry."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return {}

    text = _strip_bib_comments(text)
    return _parse_entries(text)


def _strip_bib_comments(text: str) -> str:
    return re.sub(r"%[^\n]*", "", text)


def _parse_entries(text: str) -> dict[str, BibEntry]:
    entries: dict[str, BibEntry] = {}
    # Match @type{key, ...} blocks
    for m in re.finditer(r"@(\w+)\s*\{", text):
        entry_type = m.group(1).lower()
        if entry_type == "string" or entry_type == "preamble" or entry_type == "comment":
            continue
        start = m.end()
        body = _extract_brace_block(text, start - 1)
        if body is None:
            continue
        inner = body[1:-1]  # strip outer braces

        # First token before the first comma is the key
        key_match = re.match(r"\s*([^,\s]+)\s*,", inner)
        if not key_match:
            continue
        key = key_match.group(1)
        fields_text = inner[key_match.end():]

        fields = _parse_fields(fields_text)
        entry = BibEntry(key=key, entry_type=entry_type)

        entry.title = _clean_braces(fields.get("title", ""))
        entry.year = fields.get("year", "").strip("{} ")
        entry.journal = _clean_braces(fields.get("journal", ""))
        entry.booktitle = _clean_braces(fields.get("booktitle", ""))
        entry.volume = fields.get("volume", "").strip("{} ")
        entry.pages = fields.get("pages", "").strip("{} ")
        entry.publisher = _clean_braces(fields.get("publisher", ""))
        entry.url = _clean_braces(fields.get("url", ""))

        raw_authors = _clean_braces(fields.get("author", ""))
        if raw_authors:
            entry.authors = [a.strip() for a in re.split(r"\band\b", raw_authors, flags=re.IGNORECASE) if a.strip()]

        entries[key] = entry

    return entries


def _parse_fields(text: str) -> dict[str, str]:
    """Extract field_name = {value} or field_name = "value" pairs."""
    fields: dict[str, str] = {}
    i = 0
    while i < len(text):
        # Skip whitespace and commas
        while i < len(text) and text[i] in " \t\n\r,":
            i += 1
        if i >= len(text):
            break

        # Read field name
        name_m = re.match(r"(\w+)\s*=\s*", text[i:])
        if not name_m:
            i += 1
            continue
        field_name = name_m.group(1).lower()
        i += name_m.end()

        if i >= len(text):
            break

        # Read value: {…} or "…" or bare word/number
        if text[i] == "{":
            block = _extract_brace_block(text, i)
            if block is None:
                break
            fields[field_name] = block
            i += len(block)
        elif text[i] == '"':
            end = text.find('"', i + 1)
            if end == -1:
                break
            fields[field_name] = text[i + 1:end]
            i = end + 1
        else:
            # bare number or macro name
            end = i
            while end < len(text) and text[end] not in " \t\n\r,{}":
                end += 1
            fields[field_name] = text[i:end]
            i = end

    return fields


def _extract_brace_block(text: str, start: int) -> Optional[str]:
    """Return the substring from text[start] up to and including the matching }."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _clean_braces(s: str) -> str:
    """Remove surrounding braces and leading/trailing whitespace."""
    s = s.strip()
    while s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    return s


# ---------------------------------------------------------------------------
# Citation resolver
# ---------------------------------------------------------------------------

def resolve_citations(text: str, bib: dict[str, BibEntry]) -> str:
    """
    Replace [key] and [key1, key2, ...] tokens produced by parse_latex's
    \\cite substitution with human-readable short references.
    """
    def _replace(m: re.Match) -> str:
        keys = [k.strip() for k in m.group(1).split(",")]
        resolved = []
        for k in keys:
            if k in bib:
                resolved.append(bib[k].short_ref())
            else:
                resolved.append(k)  # key not in bib — leave as-is
        return "[" + "; ".join(resolved) + "]"

    return re.sub(r"\[([^\[\]]+)\]", _replace, text)
