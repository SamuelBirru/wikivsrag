"""
Traditional RAG system for physics papers from ArXiv.

Uses sentence-transformers for embeddings, FAISS for vector search,
and Claude via Amazon Bedrock for generation.

If paper_texts.json exists (from PhysicsScript.py --download), full paper text
is chunked and indexed. Otherwise falls back to abstracts only.

For the LaTeX-first pipeline (recommended), use --ingest-sources instead of
--ingest.  This reads source_index.json produced by PhysicsScript.py
--download-source and routes each paper through the LaTeX parser or PDF fallback.

Setup:
  pip install sentence-transformers faiss-cpu numpy anthropic
  set AWS_ACCESS_KEY_ID=your_access_key
  set AWS_SECRET_ACCESS_KEY=your_secret_key
  set AWS_REGION=us-east-1  (or whichever region has Bedrock enabled)

Usage:
  python rag_system.py --ingest              # build index from PDFs / abstracts
  python rag_system.py --ingest-sources      # build index from LaTeX sources (Phase 1)
  python rag_system.py "your question"       # query
  python rag_system.py -k 8 "your question" # query with 8 retrieved chunks
"""

import json
import os
import pickle
import sys

import anthropic
import faiss
import numpy as np
from dotenv import load_dotenv
from embed.embedder import Embedder

# Phase 1 imports (only used by --ingest-sources)
try:
    from ingest.parse_latex import parse_latex_file
    from ingest.parse_bib import parse_bib_file, resolve_citations
    from store.chunk_store import ChunkStore
    from store.metadata_index import MetadataIndex
    _PHASE1_AVAILABLE = True
except ImportError:
    _PHASE1_AVAILABLE = False

load_dotenv()

# Bedrock cross-region inference profile ID — verify in AWS Console > Bedrock > Model access
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "us.anthropic.claude-sonnet-4-6-20251031-v1:0")
INDEX_PATH = "rag_index.faiss"
CHUNKS_PATH = "rag_chunks.pkl"
PAPERS_PATH = "physics_papers.json"
TEXTS_PATH = "paper_texts.json"
DEFAULT_K = 5
CHUNK_WORDS = 400
CHUNK_OVERLAP = 50
MMR_LAMBDA = 0.6   # 0 = max diversity, 1 = max relevance
MMR_FETCH = 50     # candidate pool size before MMR re-ranks

# Phase 1 paths
SOURCE_INDEX_PATH = "source_index.json"
METADATA_INDEX_PATH = "rag_metadata.json"


def _split_into_chunks(text: str, chunk_size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + chunk_size]))
        if i + chunk_size >= len(words):
            break
        i += chunk_size - overlap
    return chunks


def _paper_header(paper: dict) -> str:
    authors = ", ".join(paper["authors"][:3])
    if len(paper["authors"]) > 3:
        authors += " et al."
    return (
        f"Title: {paper['title']}\n"
        f"Authors: {authors}\n"
        f"Published: {paper['published']}\n\n"
    )


def ingest(papers_path: str = PAPERS_PATH, texts_path: str = TEXTS_PATH) -> None:
    if not os.path.exists(papers_path):
        sys.exit(f"Error: {papers_path} not found. Run PhysicsScript.py first.")

    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)

    full_texts: dict = {}
    if os.path.exists(texts_path):
        with open(texts_path, encoding="utf-8") as f:
            full_texts = json.load(f)
        print(f"Full text available for {len(full_texts)}/{len(papers)} papers — chunking full content.")
    else:
        print(f"No {texts_path} found — indexing abstracts only. Run: python PhysicsScript.py --download")

    chunks = []
    for paper in papers:
        arxiv_id = paper["arxiv_id"]
        header = _paper_header(paper)
        meta = {"id": arxiv_id, "title": paper["title"], "url": paper["url"]}

        if arxiv_id in full_texts:
            # Chunk the full paper text; prepend header to each chunk so the LLM
            # always knows which paper a chunk belongs to
            body_chunks = _split_into_chunks(full_texts[arxiv_id])
            for ci, body in enumerate(body_chunks):
                chunks.append({"text": header + body, "meta": {**meta, "chunk": ci + 1, "total_chunks": len(body_chunks)}})
        else:
            # Fallback: abstract only
            chunks.append({"text": header + f"Abstract: {paper['abstract']}", "meta": {**meta, "chunk": 1, "total_chunks": 1}})

    embedder = Embedder()
    print(f"Encoding {len(chunks)} chunks from {len(papers)} papers with {embedder.model_name}...")
    texts = [c["text"] for c in chunks]

    embeddings = embedder.encode(texts, show_progress_bar=True)

    index = faiss.IndexFlatIP(embedder.dimension)
    index.add(embeddings)

    faiss.write_index(index, INDEX_PATH)
    with open(CHUNKS_PATH, "wb") as f:
        pickle.dump(chunks, f)

    print(f"Saved: {INDEX_PATH}, {CHUNKS_PATH}")
    print(f"Done — {len(chunks)} chunks indexed ({len(papers)} papers).")


def ingest_sources(
    source_index_path: str = SOURCE_INDEX_PATH,
    papers_path: str = PAPERS_PATH,
    texts_path: str = TEXTS_PATH,
) -> None:
    """
    Phase 1 ingestion: parse LaTeX source files where available, fall back to
    PDF text or abstract otherwise.  Produces the same rag_index.faiss and
    rag_chunks.pkl as --ingest, plus rag_metadata.json for label lookups.
    """
    if not _PHASE1_AVAILABLE:
        sys.exit("Phase 1 modules not found. Make sure ingest/, store/, embed/ directories exist.")

    if not os.path.exists(source_index_path):
        sys.exit(f"{source_index_path} not found. Run: python PhysicsScript.py --download-source")

    if not os.path.exists(papers_path):
        sys.exit(f"{papers_path} not found. Run: python PhysicsScript.py")

    with open(source_index_path, encoding="utf-8") as fh:
        source_index = json.load(fh)

    with open(papers_path, encoding="utf-8") as fh:
        papers = json.load(fh)

    full_texts: dict = {}
    if os.path.exists(texts_path):
        with open(texts_path, encoding="utf-8") as fh:
            full_texts = json.load(fh)

    # Build a lookup: arxiv_id -> paper metadata
    paper_meta = {
        p["arxiv_id"]: {"id": p["arxiv_id"], "title": p["title"], "url": p["url"]}
        for p in papers
    }

    store = ChunkStore()
    tex_count = pdf_count = abstract_count = 0

    for paper in papers:
        arxiv_id = paper["arxiv_id"]
        meta = paper_meta[arxiv_id]
        entry = source_index.get(arxiv_id, {})
        status = entry.get("status", "missing")

        if status == "tex" and entry.get("main_tex"):
            # --- LaTeX path ---
            try:
                chunks = parse_latex_file(entry["main_tex"], meta)
                if chunks:
                    # Resolve \cite{} keys to human-readable references
                    bib: dict = {}
                    if entry.get("bib_file"):
                        bib = parse_bib_file(entry["bib_file"])
                    if bib:
                        for c in chunks:
                            if isinstance(c, dict):
                                c["text"] = resolve_citations(c["text"], bib)
                            else:
                                c.text = resolve_citations(c.text, bib)
                    store.chunks.extend(chunks)
                    tex_count += 1
                    print(f"  [tex]  {arxiv_id}  {len(chunks)} chunks  ({len(bib)} bib entries)")
                    continue
            except Exception as exc:
                print(f"  [tex-err]  {arxiv_id}: {exc} — falling back")

        if arxiv_id in full_texts:
            # --- PDF text fallback ---
            header = _paper_header(paper)
            body_chunks = _split_into_chunks(full_texts[arxiv_id])
            for ci, body in enumerate(body_chunks):
                store.chunks.append({
                    "text": header + body,
                    "meta": {**meta, "chunk": ci + 1, "total_chunks": len(body_chunks),
                             "chunk_type": "prose", "section_path": []},
                })
            pdf_count += 1
            print(f"  [pdf]  {arxiv_id}  {len(body_chunks)} chunks")
            continue

        # --- Abstract-only fallback ---
        store.chunks.append({
            "text": _paper_header(paper) + f"Abstract: {paper['abstract']}",
            "meta": {**meta, "chunk": 1, "total_chunks": 1,
                     "chunk_type": "prose", "section_path": []},
        })
        abstract_count += 1

    print(f"\nIngested: {tex_count} tex  |  {pdf_count} pdf  |  {abstract_count} abstract-only")
    print(f"Total chunks: {len(store)}")
    print(f"Type breakdown: {store.stats()}")

    # Embed
    embedder = Embedder()
    rag_list = store.to_rag_list()
    texts = [c["text"] for c in rag_list]
    print(f"\nEncoding {len(texts)} chunks with {embedder.model_name}...")
    embeddings = embedder.encode(texts, show_progress_bar=True)

    # Build FAISS index
    index = faiss.IndexFlatIP(embedder.dimension)
    index.add(embeddings)
    faiss.write_index(index, INDEX_PATH)

    # Save chunks (store format, backwards-compatible with _load_index)
    with open(CHUNKS_PATH, "wb") as fh:
        pickle.dump(rag_list, fh)

    # Save metadata index for label / equation-number lookups
    meta_idx = MetadataIndex()
    meta_idx.build(store.chunks)
    meta_idx.save(METADATA_INDEX_PATH)

    print(f"\nSaved: {INDEX_PATH}, {CHUNKS_PATH}, {METADATA_INDEX_PATH}")
    print(f"Done — {len(texts)} chunks from {len(papers)} papers.")


def _load_index():
    if not os.path.exists(INDEX_PATH) or not os.path.exists(CHUNKS_PATH):
        sys.exit("Index not found. Run: python rag_system.py --ingest")
    index = faiss.read_index(INDEX_PATH)
    with open(CHUNKS_PATH, "rb") as f:
        chunks = pickle.load(f)
    return index, chunks


def _mmr(query_emb: np.ndarray, candidate_ids: list[int], index, k: int, lambda_mult: float = MMR_LAMBDA) -> list[int]:
    """
    Maximal Marginal Relevance: select k chunks that balance relevance to the
    query against redundancy with already-selected chunks.
    """
    if len(candidate_ids) <= k:
        return candidate_ids

    # Reconstruct candidate vectors from the FAISS index (already L2-normalised)
    cand_embs = np.array([index.reconstruct(int(i)) for i in candidate_ids])  # (n, d)
    query_sims = (cand_embs @ query_emb.T).flatten()  # cosine sim to query

    selected, remaining = [], list(range(len(candidate_ids)))
    while len(selected) < k and remaining:
        if not selected:
            best = int(np.argmax(query_sims[remaining]))
        else:
            sel_embs = cand_embs[selected]  # (s, d)
            scores = [
                lambda_mult * query_sims[i] - (1 - lambda_mult) * float(np.max(cand_embs[i] @ sel_embs.T))
                for i in remaining
            ]
            best = remaining[int(np.argmax(scores))]
        selected.append(best)
        remaining.remove(best)

    return [candidate_ids[i] for i in selected]


def query(question: str, k: int = DEFAULT_K) -> dict:
    index, chunks = _load_index()
    embedder = Embedder()

    # Try structural lookup (equation numbers, LaTeX labels, section paths) before FAISS
    pinned_ids = []
    if _PHASE1_AVAILABLE and os.path.exists(METADATA_INDEX_PATH):
        meta_idx = MetadataIndex.load(METADATA_INDEX_PATH)
        direct_hit = meta_idx.try_direct_lookup(question)
        if direct_hit is not None and direct_hit < len(chunks):
            pinned_ids = [direct_hit]
            print(f"[RAG] Direct structural match at chunk {direct_hit}")

    q_emb = embedder.encode([question])

    # Fetch a larger candidate pool then re-rank with MMR for diversity
    fetch_n = min(index.ntotal, max(MMR_FETCH, k * 10))
    raw_scores, raw_indices = index.search(q_emb, fetch_n)
    # Exclude pinned chunk from MMR candidates to avoid duplicate
    candidate_ids = [i for i in raw_indices[0].tolist() if i not in pinned_ids]

    semantic_k = max(0, k - len(pinned_ids))
    selected_ids = pinned_ids + (_mmr(q_emb, candidate_ids, index, semantic_k) if semantic_k else [])

    score_map = {i: float(raw_scores[0][j]) for j, i in enumerate(raw_indices[0].tolist())}
    hits = [(chunks[i], score_map.get(i, 1.0)) for i in selected_ids]
    context = "\n\n---\n\n".join(c["text"] for c, _ in hits)

    prompt = (
        "You are a physics research assistant. Use only the paper excerpts below to answer the question.\n\n"
        f"Excerpts:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer based on the provided excerpts. "
        "If the excerpts don't contain enough information, say so."
    )

    print(f"[RAG] Querying {CLAUDE_MODEL} with {k} MMR-selected chunks (pool: {fetch_n})...")
    try:
        client = anthropic.AnthropicBedrock(
            aws_access_key=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
        )
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
    except KeyError as e:
        sys.exit(f"Missing AWS credential env var: {e}. Check your .env file.")
    except anthropic.APIError as e:
        sys.exit(f"Bedrock API error: {e}\nCheck CLAUDE_MODEL in .env — get the exact ID from AWS Console > Bedrock > Cross-region inference.")

    return {
        "answer": response.content[0].text,
        "sources": [
            {"title": c["meta"]["title"], "url": c["meta"]["url"], "score": s}
            for c, s in hits
        ],
    }


def _print_result(result: dict, k: int) -> None:
    print(f"\n{'='*60}")
    print("RAG ANSWER")
    print("="*60)
    print(result["answer"])
    print(f"\n{'='*60}")
    print(f"SOURCES (top {k} chunks retrieved)")
    print("="*60)
    for s in result["sources"]:
        print(f"  [{s['score']:.3f}] {s['title']}")
        print(f"           {s['url']}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    if args[0] == "--ingest-sources":
        ingest_sources()
    elif args[0] == "--ingest":
        ingest()
    else:
        k = DEFAULT_K
        if "-k" in args:
            idx = args.index("-k")
            k = int(args[idx + 1])
            args = args[:idx] + args[idx + 2:]
        question = " ".join(args)
        result = query(question, k=k)
        _print_result(result, k)
