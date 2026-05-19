"""
Parses a LaTeX document into structured Chunk objects.

Resolves \\input{} / \\include{} recursively, strips comments, extracts the
document body, then walks it emitting one chunk per recognised structural unit:

  prose      — cleaned paragraphs, split to ~chunk_words words with overlap
  equation   — each display-math environment (equation, align, eqnarray, …)
  figure     — \\begin{figure} blocks with caption + \\includegraphics path
  table      — \\begin{table} blocks with caption
  theorem    — theorem / lemma / proposition / corollary / conjecture / claim
  definition — definition / remark / example / notation / assumption
  proof      — proof environments

Each Chunk carries:
  text         — embedding-ready string (section context + cleaned content)
  latex_raw    — original LaTeX preserved verbatim
  chunk_type   — one of the types above
  section_path — list like ["2. Methods", "2.3 Hamiltonian"]
  label        — \\label{} value if present
  caption      — \\caption{} text (figures and tables)
  figure_ref   — \\includegraphics path (figures)
  equation_number — sequential counter within the document
  source_file  — path of the .tex file that contained this chunk
  meta         — paper-level dict: {id, title, url}
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Environment classification
# ---------------------------------------------------------------------------

MATH_ENVS = frozenset({
    "equation", "equation*",
    "align", "align*",
    "eqnarray", "eqnarray*",
    "multline", "multline*",
    "gather", "gather*",
    "flalign", "flalign*",
    "alignat", "alignat*",
    "subequations",
})

THEOREM_ENVS = frozenset({
    "theorem", "lemma", "proposition", "corollary",
    "conjecture", "claim", "fact",
})

DEFINITION_ENVS = frozenset({
    "definition", "remark", "example", "notation",
    "assumption", "observation",
})

PROOF_ENVS = frozenset({"proof"})

FIGURE_ENVS = frozenset({"figure", "figure*", "wrapfigure", "subfigure"})

TABLE_ENVS = frozenset({"table", "table*"})

# Environments whose inner text we silently discard
SKIP_ENVS = frozenset({
    "tikzpicture", "pgfpicture", "pspicture",
    "verbatim", "lstlisting", "minted",
    "thebibliography",
})

# Combined set used by the tokeniser to decide extraction vs prose
_STRUCTURED = MATH_ENVS | THEOREM_ENVS | DEFINITION_ENVS | PROOF_ENVS | FIGURE_ENVS | TABLE_ENVS | SKIP_ENVS


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    text: str
    latex_raw: str
    chunk_type: str
    section_path: list[str]
    source_file: str
    label: Optional[str] = None
    caption: Optional[str] = None
    figure_ref: Optional[str] = None
    equation_number: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def to_rag_dict(self) -> dict:
        """Return the {text, meta} format consumed by rag_system.py."""
        return {
            "text": self.text,
            "meta": {
                **self.meta,
                "chunk_type": self.chunk_type,
                "section_path": self.section_path,
                "label": self.label,
                "caption": self.caption,
                "figure_ref": self.figure_ref,
                "equation_number": self.equation_number,
                "latex_raw": self.latex_raw,
                "source_file": self.source_file,
            },
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_latex_file(
    main_tex: str,
    meta: dict,
    chunk_words: int = 300,
    chunk_overlap: int = 50,
    max_depth: int = 10,
) -> list[Chunk]:
    """
    Parse the LaTeX document rooted at main_tex and return a list of Chunks.

    Args:
        main_tex:      Path to the main .tex file.
        meta:          Paper-level metadata dict {id, title, url, …}.
        chunk_words:   Target word count for prose chunks.
        chunk_overlap: Word overlap between consecutive prose chunks.
        max_depth:     Maximum \\input{} recursion depth before warning + skip.
    """
    root_dir = os.path.dirname(os.path.abspath(main_tex))

    flat = _flatten(main_tex, root_dir, set(), depth=0, max_depth=max_depth)
    flat = _strip_comments(flat)
    body = _extract_body(flat)
    if not body:
        return []

    return _walk_body(body, main_tex, meta, chunk_words, chunk_overlap)


# ---------------------------------------------------------------------------
# Step 1: flatten \input{} / \include{}
# ---------------------------------------------------------------------------

def _read_tex(path: str) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, encoding=enc, errors="strict") as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            pass
    return ""


def _flatten(
    path: str,
    root_dir: str,
    visited: set,
    depth: int,
    max_depth: int,
) -> str:
    abs_path = os.path.abspath(path)
    if depth > max_depth:
        return f"% [WARNING: max include depth reached for {path}]\n"
    if abs_path in visited:
        return f"% [WARNING: circular include detected for {path}]\n"
    visited.add(abs_path)

    content = _read_tex(abs_path)
    content = _strip_comments(content)

    def _resolve_include(m: re.Match) -> str:
        fname = m.group(1).strip()
        base_dir = os.path.dirname(abs_path)
        for candidate in (
            os.path.join(base_dir, fname),
            os.path.join(base_dir, fname + ".tex"),
            os.path.join(root_dir, fname),
            os.path.join(root_dir, fname + ".tex"),
        ):
            if os.path.exists(candidate):
                return _flatten(candidate, root_dir, visited, depth + 1, max_depth)
        return f"% [WARNING: included file not found: {fname}]\n"

    return re.sub(r"\\(?:input|include)\{([^}]+)\}", _resolve_include, content)


# ---------------------------------------------------------------------------
# Step 2: strip % comments
# ---------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    # Remove everything from an unescaped % to the end of the line
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


# ---------------------------------------------------------------------------
# Step 3: extract \begin{document}…\end{document}
# ---------------------------------------------------------------------------

def _extract_body(text: str) -> str:
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", text, re.DOTALL)
    return m.group(1) if m else text


# ---------------------------------------------------------------------------
# Step 4: tokenise the body
# ---------------------------------------------------------------------------

# Matches \section{, \subsection{, \subsubsection{ (with optional *)
_SECTION_PAT = re.compile(r"\\((?:sub){0,2}section)\*?\{")
# Matches \begin{env_name}
_BEGIN_PAT = re.compile(r"\\begin\{(\w+\*?)\}")
# Combined: whichever comes first
_STRUCTURE_PAT = re.compile(
    r"\\((?:sub){0,2}section)\*?\{"   # group 1 = section level word
    r"|\\begin\{(\w+\*?)\}"           # group 2 = env name
)


def _tokenise(body: str) -> list:
    """
    Split body into a flat token list:
      ('text',    content)
      ('section', level:int, title:str)
      ('env',     env_name:str, full_env_text:str)
    """
    tokens = []
    pos = 0

    while pos < len(body):
        m = _STRUCTURE_PAT.search(body, pos)
        if not m:
            if pos < len(body):
                tokens.append(("text", body[pos:]))
            break

        # Prose before this structural element
        if m.start() > pos:
            tokens.append(("text", body[pos:m.start()]))

        if m.group(1):  # section command
            level = m.group(1).count("sub") + 1
            title, end_pos = _extract_braces(body, m.end() - 1)
            tokens.append(("section", level, _clean_prose(title)))
            pos = end_pos
        else:  # \begin{env}
            env_name = m.group(2)
            env_text, end_pos = _extract_environment(body, m.start(), env_name)
            tokens.append(("env", env_name, env_text))
            pos = end_pos

    return tokens


def _extract_braces(text: str, start: int) -> tuple[str, int]:
    """Extract the content of the {…} group whose '{' is at text[start]."""
    if start >= len(text) or text[start] != "{":
        return "", start
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    return text[start + 1 :], len(text)


def _extract_environment(text: str, start: int, env_name: str) -> tuple[str, int]:
    """
    Return (full_env_text, position_after_end_tag) for the environment
    beginning at text[start].  Handles nesting of the same environment name.
    """
    begin_str = f"\\begin{{{env_name}}}"
    end_str = f"\\end{{{env_name}}}"

    depth = 1
    pos = start + len(begin_str)

    while pos < len(text) and depth > 0:
        nb = text.find(begin_str, pos)
        ne = text.find(end_str, pos)

        if ne == -1:
            break  # malformed — return what we have

        if nb != -1 and nb < ne:
            depth += 1
            pos = nb + len(begin_str)
        else:
            depth -= 1
            if depth == 0:
                end_pos = ne + len(end_str)
                return text[start:end_pos], end_pos
            pos = ne + len(end_str)

    return text[start:], len(text)


# ---------------------------------------------------------------------------
# Step 5: walk tokens and emit Chunks
# ---------------------------------------------------------------------------

def _walk_body(
    body: str,
    source_file: str,
    meta: dict,
    chunk_words: int,
    chunk_overlap: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    section_path: list[str] = []
    prose_parts: list[str] = []
    eq_counter = 0

    def flush_prose():
        nonlocal prose_parts
        if not prose_parts:
            return
        raw = " ".join(prose_parts)
        prose_parts = []
        for c in _split_prose(raw, list(section_path), source_file, meta, chunk_words, chunk_overlap):
            chunks.append(c)

    for token in _tokenise(body):
        kind = token[0]

        if kind == "text":
            prose_parts.append(token[1])

        elif kind == "section":
            flush_prose()
            _, level, title = token
            section_path = _update_section_path(section_path, level, title)

        elif kind == "env":
            _, env_name, env_text = token

            if env_name in MATH_ENVS:
                flush_prose()
                eq_counter += 1
                c = _make_equation_chunk(env_text, list(section_path), source_file, meta, eq_counter)
                if c:
                    chunks.append(c)

            elif env_name in FIGURE_ENVS:
                flush_prose()
                c = _make_figure_chunk(env_text, list(section_path), source_file, meta)
                if c:
                    chunks.append(c)

            elif env_name in TABLE_ENVS:
                flush_prose()
                c = _make_table_chunk(env_text, list(section_path), source_file, meta)
                if c:
                    chunks.append(c)

            elif env_name in THEOREM_ENVS:
                flush_prose()
                c = _make_structured_chunk(env_text, "theorem", list(section_path), source_file, meta)
                if c:
                    chunks.append(c)

            elif env_name in DEFINITION_ENVS:
                flush_prose()
                c = _make_structured_chunk(env_text, "definition", list(section_path), source_file, meta)
                if c:
                    chunks.append(c)

            elif env_name in PROOF_ENVS:
                flush_prose()
                c = _make_structured_chunk(env_text, "proof", list(section_path), source_file, meta)
                if c:
                    chunks.append(c)

            elif env_name in SKIP_ENVS:
                pass  # discard entirely

            else:
                # Unknown / container environment — extract inner text as prose
                inner = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", env_text)
                prose_parts.append(inner)

    flush_prose()
    return chunks


# ---------------------------------------------------------------------------
# Section path management
# ---------------------------------------------------------------------------

def _update_section_path(current: list[str], level: int, title: str) -> list[str]:
    # level 1 = \section, 2 = \subsection, 3 = \subsubsection
    new_path = current[: level - 1]
    new_path.append(title)
    return new_path


# ---------------------------------------------------------------------------
# Prose cleaning and splitting
# ---------------------------------------------------------------------------

def _clean_prose(text: str) -> str:
    """Strip common LaTeX formatting macros while preserving readable content."""
    # Formatting macros: keep inner text
    text = re.sub(
        r"\\(?:textbf|textit|texttt|emph|textrm|textsc|text|underline)\{([^{}]*)\}",
        r"\1", text,
    )
    # Citations: keep key(s)
    text = re.sub(r"\\cite[a-z]*\{([^}]*)\}", r"[\1]", text)
    # References: keep label
    text = re.sub(r"\\(?:ref|eqref|autoref|cref|Cref)\{([^}]*)\}", r"[ref:\1]", text)
    # Labels, maketitle, structural commands: remove
    text = re.sub(r"\\(?:label|maketitle|tableofcontents|listoffigures|listoftables)\{?[^}]*\}?", "", text)
    # Footnotes: drop
    text = re.sub(r"\\footnote\{[^{}]*\}", "", text)
    # URL macros: keep URL
    text = re.sub(r"\\(?:url|path)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
    # Spacing / line-break commands
    text = re.sub(r"\\(?:noindent|newpage|clearpage|pagebreak|linebreak)\b\s*", "", text)
    text = re.sub(r"\\(?:vspace|hspace|vskip|hskip)\*?\{[^}]*\}", " ", text)
    text = re.sub(r"\\\\", " ", text)
    text = text.replace("~", " ")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_prose(
    text: str,
    section_path: list[str],
    source_file: str,
    meta: dict,
    chunk_words: int,
    chunk_overlap: int,
) -> list[Chunk]:
    cleaned = _clean_prose(text)
    words = cleaned.split()
    if len(words) < 10:
        return []

    section_ctx = " > ".join(section_path) if section_path else "document"
    chunks = []
    i = 0
    while i < len(words):
        w_slice = words[i : i + chunk_words]
        body = " ".join(w_slice)
        embed_text = f"[{section_ctx}]\n{body}"
        chunks.append(
            Chunk(
                text=embed_text,
                latex_raw=body,
                chunk_type="prose",
                section_path=section_path,
                source_file=source_file,
                meta=meta,
            )
        )
        if i + chunk_words >= len(words):
            break
        i += chunk_words - chunk_overlap

    return chunks


# ---------------------------------------------------------------------------
# Chunk factories
# ---------------------------------------------------------------------------

def _extract_label(text: str) -> Optional[str]:
    m = re.search(r"\\label\{([^}]+)\}", text)
    return m.group(1).strip() if m else None


def _extract_caption(text: str) -> Optional[str]:
    # Handles \caption{...} and \caption[short]{long}
    m = re.search(r"\\caption(?:\[[^\]]*\])?\{", text)
    if not m:
        return None
    content, _ = _extract_braces(text, m.end() - 1)
    return _clean_prose(content).strip() or None


def _extract_includegraphics(text: str) -> Optional[str]:
    m = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", text)
    return m.group(1).strip() if m else None


def _make_equation_chunk(
    env_text: str,
    section_path: list[str],
    source_file: str,
    meta: dict,
    eq_num: int,
) -> Optional[Chunk]:
    label = _extract_label(env_text)
    # Strip outer \begin{}/\end{} to get raw equation body
    inner = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", env_text).strip()
    if not inner:
        return None

    section_ctx = " > ".join(section_path) if section_path else "document"
    parts = [f"Equation {eq_num} in [{section_ctx}]"]
    if label:
        parts.append(f"(label: {label})")
    parts.append(f":\n{inner}")

    return Chunk(
        text="\n".join(parts),
        latex_raw=env_text,
        chunk_type="equation",
        section_path=section_path,
        source_file=source_file,
        label=label,
        equation_number=str(eq_num),
        meta=meta,
    )


def _make_figure_chunk(
    env_text: str,
    section_path: list[str],
    source_file: str,
    meta: dict,
) -> Optional[Chunk]:
    label = _extract_label(env_text)
    caption = _extract_caption(env_text)
    fig_ref = _extract_includegraphics(env_text)

    if not caption and not fig_ref:
        return None

    section_ctx = " > ".join(section_path) if section_path else "document"
    parts = [f"Figure in [{section_ctx}]"]
    if caption:
        parts.append(f"Caption: {caption}")
    if fig_ref:
        parts.append(f"Image file: {fig_ref}")
    if label:
        parts.append(f"Label: {label}")

    return Chunk(
        text="\n".join(parts),
        latex_raw=env_text,
        chunk_type="figure",
        section_path=section_path,
        source_file=source_file,
        label=label,
        caption=caption,
        figure_ref=fig_ref,
        meta=meta,
    )


def _make_table_chunk(
    env_text: str,
    section_path: list[str],
    source_file: str,
    meta: dict,
) -> Optional[Chunk]:
    label = _extract_label(env_text)
    caption = _extract_caption(env_text)

    # Try to pull the tabular content
    tab_m = re.search(r"\\begin\{tabular[^}]*\}(.*?)\\end\{tabular[^}]*\}", env_text, re.DOTALL)
    table_text = _clean_prose(tab_m.group(1))[:500] if tab_m else ""

    if not caption and not table_text:
        return None

    section_ctx = " > ".join(section_path) if section_path else "document"
    parts = [f"Table in [{section_ctx}]"]
    if caption:
        parts.append(f"Caption: {caption}")
    if table_text:
        parts.append(f"Content: {table_text}")

    return Chunk(
        text="\n".join(parts),
        latex_raw=env_text,
        chunk_type="table",
        section_path=section_path,
        source_file=source_file,
        label=label,
        caption=caption,
        meta=meta,
    )


def _make_structured_chunk(
    env_text: str,
    chunk_type: str,
    section_path: list[str],
    source_file: str,
    meta: dict,
) -> Optional[Chunk]:
    label = _extract_label(env_text)
    inner = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", env_text)
    inner = re.sub(r"\\label\{[^}]+\}", "", inner)
    cleaned = _clean_prose(inner)

    if len(cleaned.split()) < 5:
        return None

    section_ctx = " > ".join(section_path) if section_path else "document"
    embed_text = f"{chunk_type.capitalize()} in [{section_ctx}]:\n{cleaned}"

    return Chunk(
        text=embed_text,
        latex_raw=env_text,
        chunk_type=chunk_type,
        section_path=section_path,
        source_file=source_file,
        label=label,
        meta=meta,
    )
