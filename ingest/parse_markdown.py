"""
Parse Datalab-style Markdown (from the OCR fallback) into the same typed
chunk dicts that parse_latex.py produces, so PDF-only papers feed RAG, the
metadata index, and the section/concept wikis identically.

Datalab's /convert markdown preserves:
  - section structure as ATX headings (#, ##, ...)
  - inline math as $...$  and block equations as $$...$$  (often with a
    trailing equation number like  \\quad (3)  or  (3))
  - tables as GFM pipe tables
  - figures as ![alt](path) image refs

We map those to chunk_type in {prose, equation, figure, table}, tracking a
section_path stack from the heading hierarchy and lifting equation numbers
into meta.equation_number so structural lookups still work.
"""

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)]*)\)")
# Trailing equation tag:  ... \quad (3)   or   ... (3)   or  ...(3a)
_EQ_NUM = re.compile(r"(?:\\quad\s*|\\qquad\s*|\s)\(([0-9]+[A-Za-z]?)\)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _mk(text, latex_raw, chunk_type, section_path, meta, **extra):
    m = {
        **meta,
        "chunk_type": chunk_type,
        "section_path": list(section_path),
        "label": extra.get("label"),
        "caption": extra.get("caption"),
        "figure_ref": extra.get("figure_ref"),
        "equation_number": extra.get("equation_number"),
        "latex_raw": latex_raw,
        "source_file": meta.get("source_file", "datalab"),
    }
    return {"text": text, "meta": m}


def _split_words(text, chunk_words, overlap):
    words = text.split()
    if not words:
        return []
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i : i + chunk_words]))
        if i + chunk_words >= len(words):
            break
        i += chunk_words - overlap
    return out


def parse_datalab_markdown(
    md: str,
    meta: dict,
    chunk_words: int = 300,
    chunk_overlap: int = 50,
) -> list[dict]:
    """Convert Datalab markdown into a list of {text, meta} chunk dicts."""
    chunks: list[dict] = []
    section_stack: list[tuple[int, str]] = []  # (heading level, title)
    prose_buf: list[str] = []

    def section_path():
        return [title for _, title in section_stack]

    def flush_prose():
        text = "\n".join(prose_buf).strip()
        prose_buf.clear()
        if not text:
            return
        for piece in _split_words(text, chunk_words, chunk_overlap):
            chunks.append(_mk(piece, piece, "prose", section_path(), meta))

    lines = md.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # --- Heading: update the section stack -----------------------------
        h = _HEADING.match(line)
        if h:
            flush_prose()
            level, title = len(h.group(1)), h.group(2).strip()
            if title:  # skip empty '######' separators Datalab sometimes emits
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                section_stack.append((level, title))
            i += 1
            continue

        # --- Block equation: $$ ... $$ (may span lines) --------------------
        if stripped.startswith("$$"):
            flush_prose()
            buf = [line]
            # balanced if this line already closes the block (>= 2 '$$')
            closed = stripped.count("$$") >= 2
            j = i
            while not closed and j + 1 < n:
                j += 1
                buf.append(lines[j])
                if "$$" in lines[j]:
                    closed = True
            block = "\n".join(buf).strip()
            eq_num = None
            m = _EQ_NUM.search(block.rstrip("$ ").rstrip())
            if m:
                eq_num = m.group(1)
            chunks.append(
                _mk(block, block, "equation", section_path(), meta,
                    equation_number=eq_num)
            )
            i = j + 1
            continue

        # --- Figure: ![alt](path) ------------------------------------------
        img = _IMAGE.search(line)
        if img:
            flush_prose()
            caption = img.group("alt").strip() or None
            chunks.append(
                _mk(line.strip(), line.strip(), "figure", section_path(), meta,
                    caption=caption, figure_ref=img.group("path"))
            )
            i += 1
            continue

        # --- Table: consecutive pipe rows ----------------------------------
        if _TABLE_ROW.match(line):
            flush_prose()
            buf = []
            while i < n and _TABLE_ROW.match(lines[i]):
                buf.append(lines[i])
                i += 1
            table = "\n".join(buf).strip()
            chunks.append(_mk(table, table, "table", section_path(), meta))
            continue

        # --- Ordinary prose ------------------------------------------------
        prose_buf.append(line)
        i += 1

    flush_prose()
    return chunks
