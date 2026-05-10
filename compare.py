"""
Run the same question through both RAG and Wiki systems and compare results side-by-side.

Both systems must be set up first:
  python rag_system.py --ingest
  python wiki_system.py --ingest && python wiki_system.py --build

Usage:
  python compare.py "What are the main research themes in these physics papers?"
  python compare.py -k 6 "How is machine learning used in physics research?"
"""

import sys
import time

import rag_system
import wiki_system

DEFAULT_K = 5


def compare(question: str, k: int = DEFAULT_K) -> None:
    print(f"\nQuestion: {question}")
    print("="*60)

    print("\n[1/2] Running RAG system...")
    t0 = time.time()
    rag_result = rag_system.query(question, k=k)
    rag_time = time.time() - t0

    print("\n[2/2] Running Wiki system...")
    t0 = time.time()
    wiki_result = wiki_system.query(question, k=k)
    wiki_time = time.time() - t0

    # ---- Side-by-side output ----
    print(f"\n{'='*60}")
    print("RAG ANSWER  (retrieves raw paper chunks at query time)")
    print(f"{'='*60}")
    print(rag_result["answer"])
    print(f"\nSources ({len(rag_result['sources'])} chunks, {rag_time:.1f}s):")
    for s in rag_result["sources"]:
        print(f"  [{s['score']:.3f}] {s['title']}")

    print(f"\n{'='*60}")
    print("WIKI ANSWER  (answers from pre-synthesized knowledge base)")
    print(f"{'='*60}")
    print(wiki_result["answer"])
    print(f"\nPages consulted ({len(wiki_result['pages'])} concepts, {wiki_time:.1f}s):")
    for p in wiki_result["pages"]:
        print(f"  [{p['score']:.3f}] {p['concept']}")

    print(f"\n{'='*60}")
    print(f"Timing — RAG: {rag_time:.1f}s | Wiki: {wiki_time:.1f}s")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    k = DEFAULT_K
    if "-k" in args:
        idx = args.index("-k")
        k = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    question = " ".join(args)
    compare(question, k=k)
