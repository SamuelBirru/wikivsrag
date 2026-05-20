"""
Systematic evaluation of RAG vs concept wiki vs section wiki.
Claude acts as the judge — run --report and paste the output into the chat.

Usage:
  python eval.py --run              # run all three systems (resumes if interrupted)
  python eval.py --run --no-concept # skip concept wiki (wiki_system) if not built
  python eval.py --report           # print all Q&A pairs formatted for Claude to judge
"""

import json
import os
import sys
import time

import rag_system
import wiki_generate

QUESTIONS_PATH = "questions.json"
RESULTS_PATH = "eval_results.json"
REPORT_PATH = "eval_report.md"


def run(run_concept: bool = True) -> None:
    if not os.path.exists(QUESTIONS_PATH):
        sys.exit(f"{QUESTIONS_PATH} not found.")

    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        questions = json.load(f)

    results: dict = {}
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, encoding="utf-8") as f:
            results = json.load(f)
        print(f"Resuming — {len(results)}/{len(questions)} questions already have some results.\n")

    for q in questions:
        qid = q["id"]
        existing = results.get(qid, {})
        needs_rag = "rag" not in existing
        needs_concept = run_concept and "wiki" not in existing
        needs_section = "section_wiki" not in existing

        if not needs_rag and not needs_concept and not needs_section:
            print(f"[{qid}] Skip (done)")
            continue

        print(f"\n[{qid}] {q['question']}")
        print("-" * 60)

        if needs_rag:
            t0 = time.time()
            try:
                rag_result = rag_system.query(q["question"])
                existing["rag"] = {
                    "answer": rag_result["answer"],
                    "sources": [s["title"] for s in rag_result["sources"]],
                    "time": round(time.time() - t0, 1),
                }
            except Exception as e:
                existing["rag"] = {"answer": f"ERROR: {e}", "sources": [], "time": 0}
            print(f"RAG ({existing['rag']['time']}s): {existing['rag']['answer'][:120].strip()}...")

        if needs_concept:
            import wiki_system
            t0 = time.time()
            try:
                concept_result = wiki_system.query(q["question"])
                existing["wiki"] = {
                    "answer": concept_result["answer"],
                    "pages": [p["concept"] for p in concept_result["pages"]],
                    "time": round(time.time() - t0, 1),
                }
            except Exception as e:
                existing["wiki"] = {"answer": f"ERROR: {e}", "pages": [], "time": 0}
            print(f"Concept wiki ({existing['wiki']['time']}s): {existing['wiki']['answer'][:120].strip()}...")

        if needs_section:
            t0 = time.time()
            try:
                section_result = wiki_generate.query(q["question"])
                existing["section_wiki"] = {
                    "answer": section_result["answer"],
                    "pages": [f"{s['title']} / {s['section']}" for s in section_result["sources"]],
                    "time": round(time.time() - t0, 1),
                }
            except Exception as e:
                existing["section_wiki"] = {"answer": f"ERROR: {e}", "pages": [], "time": 0}
            print(f"Section wiki ({existing['section_wiki']['time']}s): {existing['section_wiki']['answer'][:120].strip()}...")

        existing.update({"id": qid, "type": q["type"], "question": q["question"]})
        results[qid] = existing

        with open(RESULTS_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Results saved to {RESULTS_PATH}")
    print("Next: python eval.py --report   (paste output to Claude for judging)")


def report() -> None:
    if not os.path.exists(RESULTS_PATH):
        sys.exit(f"{RESULTS_PATH} not found. Run: python eval.py --run")

    with open(RESULTS_PATH, encoding="utf-8") as f:
        results = json.load(f)

    lines = ["# Evaluation Report — RAG vs Concept Wiki vs Section Wiki\n"]

    by_type: dict = {"specific": [], "synthesis": [], "factual": []}
    for r in results.values():
        by_type.get(r["type"], by_type["specific"]).append(r)

    for qtype, items in by_type.items():
        if not items:
            continue
        lines.append(f"\n## Question Type: {qtype.capitalize()}\n")

        for r in items:
            lines.append(f"### [{r['id']}] {r['question']}\n")

            rag = r.get("rag", {})
            lines.append(f"**RAG** ({rag.get('time', '?')}s) — sources: {', '.join(rag.get('sources', [])[:3])}\n")
            lines.append(f"{rag.get('answer', 'N/A')}\n")

            if "wiki" in r:
                wiki = r["wiki"]
                lines.append(f"**Concept Wiki** ({wiki.get('time', '?')}s) — pages: {', '.join(wiki.get('pages', [])[:3])}\n")
                lines.append(f"{wiki.get('answer', 'N/A')}\n")

            if "section_wiki" in r:
                sw = r["section_wiki"]
                lines.append(f"**Section Wiki** ({sw.get('time', '?')}s) — pages: {', '.join(sw.get('pages', [])[:3])}\n")
                lines.append(f"{sw.get('answer', 'N/A')}\n")

            lines.append("---\n")

    content = "\n".join(lines)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    run_concept = "--no-concept" not in args
    args = [a for a in args if a != "--no-concept"]

    if "--run" in args:
        run(run_concept=run_concept)
    elif "--report" in args:
        report()
