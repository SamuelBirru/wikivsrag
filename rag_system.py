"""
Traditional RAG system for physics papers from ArXiv.

Uses sentence-transformers for embeddings, FAISS for vector search,
and Ollama (local LLM) for generation.

If paper_texts.json exists (from PhysicsScript.py --download), full paper text
is chunked and indexed. Otherwise falls back to abstracts only.

Setup:
  pip install sentence-transformers faiss-cpu numpy ollama
  ollama pull llama3.2

Usage:
  python rag_system.py --ingest              # build index
  python rag_system.py "your question"       # query
  python rag_system.py -k 8 "your question" # query with 8 retrieved chunks
"""

import json
import os
import pickle
import sys

import faiss
import numpy as np
import ollama
from sentence_transformers import SentenceTransformer

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
EMBED_MODEL = "all-MiniLM-L6-v2"
INDEX_PATH = "rag_index.faiss"
CHUNKS_PATH = "rag_chunks.pkl"
PAPERS_PATH = "physics_papers.json"
TEXTS_PATH = "paper_texts.json"
DEFAULT_K = 5
CHUNK_WORDS = 400
CHUNK_OVERLAP = 50


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

    print(f"Encoding {len(chunks)} chunks from {len(papers)} papers with {EMBED_MODEL}...")
    embedder = SentenceTransformer(EMBED_MODEL)
    texts = [c["text"] for c in chunks]

    embeddings = embedder.encode(texts, show_progress_bar=True, convert_to_numpy=True).astype("float32")
    faiss.normalize_L2(embeddings)

    index = faiss.IndexFlatIP(embeddings.shape[1])  # cosine similarity via inner product
    index.add(embeddings)

    faiss.write_index(index, INDEX_PATH)
    with open(CHUNKS_PATH, "wb") as f:
        pickle.dump(chunks, f)

    print(f"Saved: {INDEX_PATH}, {CHUNKS_PATH}")
    print(f"Done — {len(chunks)} chunks indexed ({len(papers)} papers).")


def _load_index():
    if not os.path.exists(INDEX_PATH) or not os.path.exists(CHUNKS_PATH):
        sys.exit("Index not found. Run: python rag_system.py --ingest")
    index = faiss.read_index(INDEX_PATH)
    with open(CHUNKS_PATH, "rb") as f:
        chunks = pickle.load(f)
    return index, chunks


def query(question: str, k: int = DEFAULT_K) -> dict:
    index, chunks = _load_index()
    embedder = SentenceTransformer(EMBED_MODEL)

    q_emb = embedder.encode([question], convert_to_numpy=True).astype("float32")
    faiss.normalize_L2(q_emb)
    scores, indices = index.search(q_emb, k)

    hits = [(chunks[i], float(scores[0][j])) for j, i in enumerate(indices[0])]
    context = "\n\n---\n\n".join(c["text"] for c, _ in hits)

    prompt = (
        "You are a physics research assistant. Use only the paper excerpts below to answer the question.\n\n"
        f"Excerpts:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer based on the provided excerpts. "
        "If the excerpts don't contain enough information, say so."
    )

    print(f"[RAG] Querying {OLLAMA_MODEL} with {k} retrieved chunks...")
    try:
        response = ollama.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        sys.exit(f"Ollama error: {e}\nIs Ollama running? Try: ollama serve")

    return {
        "answer": response.message.content,
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

    if args[0] == "--ingest":
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
