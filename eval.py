"""
Systematic evaluation of RAG vs LLM Wiki across all questions in questions.json.
Claude acts as the judge — run --report and paste the output into the chat.

Usage:
  python eval.py --run      # run both systems on every question (resumes if interrupted)
  python eval.py --report   # print all Q&A pairs formatted for Claude to judge
"""

import json
import os
import sys
import time

import rag_system
import wiki_system

QUESTIONS_PATH = "questions.json"
RESULTS_PATH = "eval_results.json"


def run() -> None:
    if not os.path.exists(QUESTIONS_PATH):
        sys.exit(f"{QUESTIONS_PATH} not found.")

    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        questions = json.load(f)

    results: dict = {}
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, encoding="utf-8") as f:
            results = json.load(f)
        print(f"Resuming — {len(results)}/{len(questions)} questions already done.\n")

    for q in questions:
        qid = q["id"]
        if qid in results:
            print(f"[{qid}] Skip (done)")
            continue

        print(f"\n[{qid}] {q['question']}")
        print("-" * 60)

        t0 = time.time()
        try:
            rag_result = rag_system.query(q["question"])
            rag_answer = rag_result["answer"]
            rag_sources = [s["title"] for s in rag_result["sources"]]
        except Exception as e:
            rag_answer = f"ERROR: {e}"
            rag_sources = []
        rag_time = round(time.time() - t0, 1)

        t0 = time.time()
        try:
            wiki_result = wiki_system.query(q["question"])
            wiki_answer = wiki_result["answer"]
            wiki_pages = [p["concept"] for p in wiki_result["pages"]]
        except Exception as e:
            wiki_answer = f"ERROR: {e}"
            wiki_pages = []
        wiki_time = round(time.time() - t0, 1)

        results[qid] = {
            "id": qid,
            "type": q["type"],
            "question": q["question"],
            "rag": {"answer": rag_answer, "sources": rag_sources, "time": rag_time},
            "wiki": {"answer": wiki_answer, "pages": wiki_pages, "time": wiki_time},
        }

        with open(RESULTS_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print(f"RAG ({rag_time}s): {rag_answer[:120].strip()}...")
        print(f"Wiki ({wiki_time}s): {wiki_answer[:120].strip()}...")

    print(f"\nDone. Results saved to {RESULTS_PATH}")
    print("Next: python eval.py --report   (paste output to Claude for judging)")


def report() -> None:
    if not os.path.exists(RESULTS_PATH):
        sys.exit(f"{RESULTS_PATH} not found. Run: python eval.py --run")

    with open(RESULTS_PATH, encoding="utf-8") as f:
        results = json.load(f)

    print("=" * 70)
    print("EVALUATION REPORT — paste this to Claude for judging")
    print("=" * 70)

    by_type = {"specific": [], "synthesis": [], "factual": []}
    for r in results.values():
        by_type.get(r["type"], by_type["specific"]).append(r)

    for qtype, items in by_type.items():
        if not items:
            continue
        print(f"\n{'='*70}")
        print(f"QUESTION TYPE: {qtype.upper()}")
        print(f"{'='*70}")

        for r in items:
            print(f"\n[{r['id']}] {r['question']}")
            print(f"\n  RAG ({r['rag']['time']}s) — sources: {', '.join(r['rag']['sources'][:3])}")
            print(f"  {r['rag']['answer']}")
            print(f"\n  WIKI ({r['wiki']['time']}s) — pages: {', '.join(r['wiki']['pages'][:3])}")
            print(f"  {r['wiki']['answer']}")
            print(f"\n  {'─'*60}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    if "--run" in args:
        run()
    elif "--report" in args:
        report()
