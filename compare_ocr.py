"""
Compare PDF OCR fallback backends (OpenDataLoader vs Datalab) on real papers.

Runs each backend on one or more PDFs, measures latency, saves the markdown
for manual inspection, and reports quality proxies — with emphasis on
equation fidelity, the axis the architecture review flagged as decisive.

Usage:
    python compare_ocr.py                       # default pdf_only paper, both backends
    python compare_ocr.py --backend opendataloader   # one backend only
    python compare_ocr.py path/to/a.pdf path/to/b.pdf
    python compare_ocr.py --out ocr_compare_out      # where to save markdown
"""

import os
import re
import sys
import json
import time
import argparse

from dotenv import load_dotenv

from ingest.ocr_fallback import extract, BACKENDS

load_dotenv()

DEFAULT_PDF = os.path.join("sources", "2605.05785v1", "source.pdf")

# Heuristic quality proxies. None is ground truth, but together they
# characterize how much structure/math each backend preserves.
_MATH_INLINE = re.compile(r"(?<!\$)\$(?!\$)[^$\n]+\$(?!\$)")
_MATH_BLOCK = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]", re.DOTALL)
_LATEX_CMD = re.compile(r"\\[A-Za-z]+")
_GREEK = re.compile(r"[Ͱ-Ͽ]")  # raw unicode Greek (unconverted math)
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)


def metrics(md: str) -> dict:
    return {
        "chars": len(md),
        "lines": md.count("\n") + 1,
        "headings": len(_HEADING.findall(md)),
        "inline_math": len(_MATH_INLINE.findall(md)),
        "block_math": len(_MATH_BLOCK.findall(md)),
        "latex_cmds": len(_LATEX_CMD.findall(md)),
        "raw_greek": len(_GREEK.findall(md)),
        "md_table_rows": len(_MD_TABLE_ROW.findall(md)),
    }


def run(pdf_path: str, backends: list[str], out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paper_id = os.path.basename(os.path.dirname(pdf_path)) or os.path.splitext(
        os.path.basename(pdf_path)
    )[0]
    result = {"pdf": pdf_path, "paper_id": paper_id, "backends": {}}

    for backend in backends:
        print(f"\n=== {backend} on {paper_id} ===")
        try:
            t0 = time.time()
            md = extract(pdf_path, backend)
            elapsed = round(time.time() - t0, 1)
            m = metrics(md)
            m["seconds"] = elapsed
            out_path = os.path.join(out_dir, f"{paper_id}__{backend}.md")
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(md)
            m["output"] = out_path
            result["backends"][backend] = m
            print(f"  {elapsed}s  {m['chars']} chars  "
                  f"math: {m['inline_math']} inline / {m['block_math']} block  "
                  f"latex_cmds: {m['latex_cmds']}  raw_greek: {m['raw_greek']}")
            print(f"  saved -> {out_path}")
        except Exception as exc:
            print(f"  FAILED: {exc}")
            result["backends"][backend] = {"error": str(exc)}
    return result


def print_table(results: list[dict], backends: list[str]) -> None:
    cols = ["seconds", "chars", "headings", "inline_math", "block_math",
            "latex_cmds", "raw_greek", "md_table_rows"]
    print("\n" + "=" * 72)
    print("SUMMARY (higher math/latex = better equation fidelity;")
    print("         high raw_greek with low latex_cmds = unconverted math)")
    print("=" * 72)
    for res in results:
        print(f"\n{res['paper_id']}")
        header = f"  {'metric':<16}" + "".join(f"{b:>18}" for b in backends)
        print(header)
        for col in cols:
            row = f"  {col:<16}"
            for b in backends:
                v = res["backends"].get(b, {}).get(col, "—")
                row += f"{str(v):>18}"
            print(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*", default=[DEFAULT_PDF],
                    help="PDF paths (default: the pdf_only paper)")
    ap.add_argument("--backend", action="append", choices=list(BACKENDS),
                    help="Restrict to this backend (repeatable). Default: all.")
    ap.add_argument("--out", default="ocr_compare_out",
                    help="Directory for saved markdown outputs")
    args = ap.parse_args()

    pdfs = args.pdfs if args.pdfs else [DEFAULT_PDF]
    backends = args.backend or list(BACKENDS)

    missing = [p for p in pdfs if not os.path.isfile(p)]
    if missing:
        print(f"Missing PDFs: {missing}", file=sys.stderr)
        sys.exit(1)

    results = [run(p, backends, args.out) for p in pdfs]
    print_table(results, backends)

    summary_path = os.path.join(args.out, "ocr_compare_results.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nFull metrics -> {summary_path}")


if __name__ == "__main__":
    main()
