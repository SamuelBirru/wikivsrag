"""
Run the same question through RAG, concept wiki, and section wiki side-by-side.

Setup:
  python rag_system.py --ingest-sources
  python wiki_system.py --ingest && python wiki_system.py --build
  python wiki_generate.py --output-dir wiki_output   (or wait for generation to finish)

Usage:
  python compare.py "What are the main research themes in these physics papers?"
  python compare.py -k 6 "How is machine learning used in physics research?"
  python compare.py --no-concept "question"   # skip concept wiki if not built
"""

import sys
import time

import rag_system
import wiki_system
import wiki_generate

DEFAULT_K = 5


def compare(question: str, k: int = DEFAULT_K, run_concept: bool = True) -> None:
    print(f"\nQuestion: {question}")
    print("="*60)

    print("\n[1/3] Running RAG system...")
    t0 = time.time()
    rag_result = rag_system.query(question, k=k)
    rag_time = time.time() - t0

    concept_result = None
    concept_time = 0.0
    if run_concept:
        print("\n[2/3] Running concept wiki (wiki_system)...")
        t0 = time.time()
        concept_result = wiki_system.query(question, k=k)
        concept_time = time.time() - t0

    print(f"\n[{'3' if run_concept else '2'}/3] Running section wiki (wiki_generate)...")
    t0 = time.time()
    section_result = wiki_generate.query(question, k=k)
    section_time = time.time() - t0

    # ---- Output ----
    print(f"\n{'='*60}")
    print("RAG ANSWER  (raw LaTeX chunks, FAISS + MMR retrieval)")
    print(f"{'='*60}")
    print(rag_result["answer"])
    print(f"\nSources ({len(rag_result['sources'])} chunks, {rag_time:.1f}s):")
    for s in rag_result["sources"]:
        print(f"  [{s['score']:.3f}] {s['title']}")

    if concept_result:
        print(f"\n{'='*60}")
        print("CONCEPT WIKI ANSWER  (pre-synthesized concept pages, cross-paper)")
        print(f"{'='*60}")
        print(concept_result["answer"])
        print(f"\nPages consulted ({len(concept_result['pages'])} concepts, {concept_time:.1f}s):")
        for p in concept_result["pages"]:
            print(f"  [{p['score']:.3f}] {p['concept']}")

    print(f"\n{'='*60}")
    print("SECTION WIKI ANSWER  (LaTeX-fidelity section pages, FAISS + MMR retrieval)")
    print(f"{'='*60}")
    print(section_result["answer"])
    print(f"\nPages consulted ({len(section_result['sources'])} sections, {section_time:.1f}s):")
    for s in section_result["sources"]:
        print(f"  {s['title']}  /  {s['section']}")

    print(f"\n{'='*60}")
    timing = f"RAG: {rag_time:.1f}s"
    if concept_result:
        timing += f" | Concept wiki: {concept_time:.1f}s"
    timing += f" | Section wiki: {section_time:.1f}s"
    print(f"Timing — {timing}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    k = DEFAULT_K
    run_concept = True

    if "-k" in args:
        idx = args.index("-k")
        k = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    if "--no-concept" in args:
        run_concept = False
        args = [a for a in args if a != "--no-concept"]

    question = " ".join(args)
    compare(question, k=k, run_concept=run_concept)
