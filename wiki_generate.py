"""
Synthesizes a browsable physics wiki from ingested chunks.

Groups chunks by paper + top-level section, calls Claude once per section
to write a coherent markdown wiki page, then writes everything to output_dir
with an _index.json for navigation.

Usage:
  python wiki_generate.py --output-dir wiki_output
  python wiki_generate.py --output-dir wiki_output --paper 2301.12345
  python wiki_generate.py --output-dir wiki_output --dry-run
"""

import json
import os
import pickle
import re
import sys
from collections import defaultdict

import anthropic
import faiss
import numpy as np
from dotenv import load_dotenv

from embed.embedder import Embedder

load_dotenv()

CHUNKS_PATH = "rag_chunks.pkl"
INDEX_PATH = "rag_index.faiss"
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "us.anthropic.claude-sonnet-4-6-20251031-v1:0")
MAX_CONTEXT_WORDS = 3000   # cap per wiki page to stay under token limits
MAX_TOKENS_OUT = 8192
DEFAULT_K = 8
MMR_LAMBDA = 0.6
MMR_FETCH = 50


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _chunk_section(chunk: dict) -> str:
    path = chunk.get("meta", {}).get("section_path", [])
    return path[0] if path else "Introduction"


def _chunk_paper_id(chunk: dict) -> str:
    return chunk.get("meta", {}).get("id", "unknown")


def _group_chunks(chunks: list[dict], paper_filter: str | None) -> dict:
    """
    Returns {paper_id: {section_title: [chunk, ...]}}
    """
    groups: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for c in chunks:
        pid = _chunk_paper_id(c)
        if paper_filter and pid != paper_filter:
            continue
        section = _chunk_section(c)
        groups[pid][section].append(c)
    return groups


# ---------------------------------------------------------------------------
# MMR re-ranking
# ---------------------------------------------------------------------------

def _mmr(query_emb: np.ndarray, candidate_ids: list[int], index, k: int) -> list[int]:
    if len(candidate_ids) <= k:
        return candidate_ids
    cand_embs = np.array([index.reconstruct(int(i)) for i in candidate_ids])
    query_sims = (cand_embs @ query_emb.T).flatten()
    selected, remaining = [], list(range(len(candidate_ids)))
    while len(selected) < k and remaining:
        if not selected:
            best = int(np.argmax(query_sims[remaining]))
        else:
            sel_embs = cand_embs[selected]
            scores = [
                MMR_LAMBDA * query_sims[i] - (1 - MMR_LAMBDA) * float(np.max(cand_embs[i] @ sel_embs.T))
                for i in remaining
            ]
            best = remaining[int(np.argmax(scores))]
        selected.append(best)
        remaining.remove(best)
    return [candidate_ids[i] for i in selected]


# ---------------------------------------------------------------------------
# Wiki page synthesis
# ---------------------------------------------------------------------------

def _build_context(section_chunks: list[dict]) -> str:
    """
    Assemble a context string from section chunks, ordered by type priority,
    capped at MAX_CONTEXT_WORDS.
    """
    type_order = ["prose", "theorem", "definition", "proof", "equation", "figure", "table"]

    def sort_key(c):
        ct = c.get("meta", {}).get("chunk_type", "prose")
        try:
            return type_order.index(ct)
        except ValueError:
            return len(type_order)

    ordered = sorted(section_chunks, key=sort_key)

    parts = []
    word_count = 0
    for chunk in ordered:
        text = chunk.get("text", "")
        words = len(text.split())
        if word_count + words > MAX_CONTEXT_WORDS:
            remaining = MAX_CONTEXT_WORDS - word_count
            if remaining > 50:
                text = " ".join(text.split()[:remaining]) + " [truncated]"
                parts.append(text)
            break
        parts.append(text)
        word_count += words

    return "\n\n---\n\n".join(parts)


def _synthesize_page(
    client: anthropic.AnthropicBedrock,
    paper_title: str,
    paper_url: str,
    section_title: str,
    context: str,
) -> str:
    prompt = f"""You are writing a page for a physics research wiki.

Paper: {paper_title}
ArXiv: {paper_url}
Section: {section_title}

Below are the extracted chunks from this section (prose, equations, figures, theorems):

{context}

Write a clear, well-structured wiki page in markdown. Follow these rules:
- Start with a one-paragraph summary of what this section covers
- Preserve all equations verbatim inside $$ ... $$ blocks
- For figures, include the caption and any description on a new line prefixed with >
- Highlight theorems and definitions in > **Theorem/Definition:** blocks
- End with a ## References subsection listing any cited works mentioned
- Do not invent content not present in the chunks
- Use ## subheadings to organise if the section is long"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS_OUT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text)
    return text[:60]


def _page_path(output_dir: str, paper_id: str, section_title: str) -> str:
    return os.path.join(output_dir, paper_id.replace("/", "_"), _slug(section_title) + ".md")


def _write_page(output_dir: str, paper_id: str, section_title: str, content: str) -> str:
    path = _page_path(output_dir, paper_id, section_title)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {section_title}\n\n")
        fh.write(content)
    return path


def _write_index(output_dir: str, index: list[dict]) -> None:
    path = os.path.join(output_dir, "_index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)
    print(f"\nIndex written to {path}")


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------

def _quality_report(groups: dict, chunks: list[dict]) -> None:
    print("\n--- Quality checks ---")

    # Equation chunks with no equation number
    eq_chunks = [c for c in chunks if c.get("meta", {}).get("chunk_type") == "equation"]
    unnumbered = sum(1 for c in eq_chunks if not c.get("meta", {}).get("equation_number"))
    print(f"  Equations: {len(eq_chunks)} total, {unnumbered} unnumbered")

    # Figures missing descriptions
    fig_chunks = [c for c in chunks if c.get("meta", {}).get("chunk_type") == "figure"]
    no_cap = sum(1 for c in fig_chunks if not c.get("meta", {}).get("caption"))
    print(f"  Figures: {len(fig_chunks)} total, {no_cap} missing captions")

    # Papers with no bib references in prose
    cite_pattern = re.compile(r"\[.+?\(.+?\)")
    papers_no_cites = []
    for pid, sections in groups.items():
        all_text = " ".join(c.get("text", "") for s in sections.values() for c in s)
        if not cite_pattern.search(all_text):
            papers_no_cites.append(pid)
    if papers_no_cites:
        print(f"  Papers with no resolved citations: {len(papers_no_cites)}")


# ---------------------------------------------------------------------------
# Sleep prevention (Windows)
# ---------------------------------------------------------------------------

def _wake_lock_acquire() -> None:
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        print("[wake lock] system sleep disabled for duration of generation")
    except Exception:
        pass


def _wake_lock_release() -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS only
        print("[wake lock] system sleep re-enabled")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate(output_dir: str, paper_filter: str | None = None, dry_run: bool = False) -> None:
    if not os.path.exists(CHUNKS_PATH):
        sys.exit(f"{CHUNKS_PATH} not found. Run: python rag_system.py --ingest-sources")

    with open(CHUNKS_PATH, "rb") as fh:
        chunks = pickle.load(fh)

    groups = _group_chunks(chunks, paper_filter)
    if not groups:
        sys.exit("No chunks matched. Check --paper filter.")

    print(f"Generating wiki for {len(groups)} papers across {sum(len(s) for s in groups.values())} sections...")

    if dry_run:
        for pid, sections in groups.items():
            print(f"\n  {pid}")
            for sec, sc in sections.items():
                print(f"    [{len(sc):3d} chunks]  {sec}")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Prevent Windows from sleeping mid-generation
    _wake_lock_acquire()

    # Load existing index to resume — pages already on disk are skipped
    index_path = os.path.join(output_dir, "_index.json")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as fh:
            index_entries = json.load(fh)
        done_files = {e["file"] for e in index_entries}
        print(f"Resuming — {len(index_entries)} pages already written, skipping those.")
    else:
        index_entries = []
        done_files = set()

    try:
        client = anthropic.AnthropicBedrock(
            aws_access_key=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
        )
    except KeyError as e:
        sys.exit(f"Missing AWS credential: {e}")

    total_pages = sum(len(s) for s in groups.values())
    done = len(index_entries)

    for paper_id, sections in groups.items():
        first_chunk = next(iter(next(iter(sections.values()))))
        paper_title = first_chunk.get("meta", {}).get("title", paper_id)
        paper_url = first_chunk.get("meta", {}).get("url", "")

        for section_title, section_chunks in sections.items():
            done += 1
            page_path = _page_path(output_dir, paper_id, section_title)
            rel_path = os.path.relpath(page_path, output_dir)

            if rel_path in done_files:
                print(f"  [{done}/{total_pages}] {paper_id} / {section_title[:50]} (skip)")
                continue

            print(f"  [{done}/{total_pages}] {paper_id} / {section_title[:50]}")

            context = _build_context(section_chunks)
            try:
                page_md = _synthesize_page(client, paper_title, paper_url, section_title, context)
            except anthropic.APIError as e:
                print(f"    API error: {e} — skipping")
                done -= 1
                continue

            _write_page(output_dir, paper_id, section_title, page_md)
            index_entries.append({
                "paper_id": paper_id,
                "paper_title": paper_title,
                "paper_url": paper_url,
                "section": section_title,
                "file": rel_path,
                "chunk_count": len(section_chunks),
            })
            done_files.add(rel_path)

            # Save index after every page so progress survives a crash
            _write_index(output_dir, index_entries)

    _quality_report(groups, chunks)
    print(f"\nDone — {len(index_entries)} wiki pages written to {output_dir}/")
    _wake_lock_release()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query(question: str, output_dir: str = "wiki_output", k: int = DEFAULT_K) -> dict:
    if not os.path.exists(INDEX_PATH) or not os.path.exists(CHUNKS_PATH):
        sys.exit(f"Index not found. Run: python rag_system.py --ingest-sources")

    index = faiss.read_index(INDEX_PATH)
    with open(CHUNKS_PATH, "rb") as fh:
        chunks = pickle.load(fh)

    embedder = Embedder()
    q_emb = embedder.encode([question])

    fetch_n = min(index.ntotal, max(MMR_FETCH, k * 10))
    _, raw_indices = index.search(q_emb, fetch_n)
    selected_ids = _mmr(q_emb, raw_indices[0].tolist(), index, k)

    # Map each selected chunk → its wiki page, deduplicated by page path
    seen_pages: dict[str, str] = {}  # page_path -> content
    sources = []
    for idx in selected_ids:
        chunk = chunks[idx]
        paper_id = _chunk_paper_id(chunk)
        section = _chunk_section(chunk)
        page_path = os.path.join(output_dir, paper_id.replace("/", "_"), _slug(section) + ".md")

        if page_path in seen_pages:
            continue

        if os.path.exists(page_path):
            with open(page_path, encoding="utf-8") as fh:
                seen_pages[page_path] = fh.read()
        else:
            # Wiki page not generated yet — fall back to raw chunk text
            seen_pages[page_path] = chunk.get("text", "")

        sources.append({
            "title": chunk.get("meta", {}).get("title", paper_id),
            "paper_id": paper_id,
            "section": section,
            "url": chunk.get("meta", {}).get("url", ""),
            "wiki_page": page_path if os.path.exists(page_path) else "(raw chunk fallback)",
        })

    context = "\n\n---\n\n".join(seen_pages.values())

    prompt = (
        "You are a physics research assistant with access to pre-synthesized wiki pages "
        "from recent ArXiv papers. Use only the content below to answer the question. "
        "Preserve any equations exactly as written.\n\n"
        f"Wiki pages:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer based on the provided pages. If the pages don't contain enough information, say so."
    )

    try:
        client = anthropic.AnthropicBedrock(
            aws_access_key=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
        )
    except KeyError as e:
        sys.exit(f"Missing AWS credential: {e}")

    print(f"[Wiki] Querying {CLAUDE_MODEL} with {len(seen_pages)} wiki pages (k={k})...")
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS_OUT,
        messages=[{"role": "user", "content": prompt}],
    )

    return {"answer": response.content[0].text, "sources": sources}


def _print_query_result(result: dict, k: int) -> None:
    print(f"\n{'='*60}")
    print("WIKI ANSWER")
    print("="*60)
    print(result["answer"])
    print(f"\n{'='*60}")
    print(f"SOURCES (top {k} wiki pages)")
    print("="*60)
    for s in result["sources"]:
        print(f"  {s['title']}")
        print(f"    Section : {s['section']}")
        print(f"    ArXiv   : {s['url']}")
        print(f"    Page    : {s['wiki_page']}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    output_dir = "wiki_output"
    paper_filter = None
    dry_run = False
    k = DEFAULT_K

    if "--output-dir" in args:
        idx = args.index("--output-dir")
        output_dir = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--paper" in args:
        idx = args.index("--paper")
        paper_filter = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "-k" in args:
        idx = args.index("-k")
        k = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    if "--dry-run" in args:
        dry_run = True
        args = [a for a in args if a != "--dry-run"]

    # If remaining arg looks like a question, run query mode
    remaining = [a for a in args if not a.startswith("--")]
    if remaining:
        question = " ".join(remaining)
        result = query(question, output_dir=output_dir, k=k)
        _print_query_result(result, k)
    else:
        generate(output_dir, paper_filter, dry_run)
