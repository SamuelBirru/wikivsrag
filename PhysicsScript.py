"""
Fetches 100 physics papers from ArXiv using the official ArXiv API.
Saves results to both JSON and CSV.
Optionally downloads full PDFs and extracts their text, or downloads LaTeX source archives.

Install dependencies: pip install arxiv requests pymupdf

Usage:
  python PhysicsScript.py                # fetch metadata -> physics_papers.json
  python PhysicsScript.py --download     # download PDFs and extract full text -> paper_texts.json
  python PhysicsScript.py --download-source  # download LaTeX source archives -> sources/, source_index.json
"""

import arxiv
import json
import csv
import os
import sys
import time
from datetime import datetime

import requests

PHYSICS_CATEGORIES = [
    "physics",
    "cond-mat",
    "quant-ph",
    "hep-th",
    "astro-ph",
    "gr-qc",
    "nucl-th",
    "math-ph",
]

QUERY = " OR ".join(f"cat:{cat}" for cat in PHYSICS_CATEGORIES)
PAPERS_PATH = "physics_papers.json"
TEXTS_PATH = "paper_texts.json"
PDF_DIR = "pdfs"
DOWNLOAD_DELAY = 4  # seconds between PDF downloads (ArXiv rate limit courtesy)


def fetch_papers(max_results: int = 100) -> list[dict]:
    client = arxiv.Client(page_size=100, delay_seconds=1.0)
    search = arxiv.Search(
        query=QUERY,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    papers = []
    for result in client.results(search):
        papers.append({
            "arxiv_id": result.entry_id.split("/abs/")[-1],
            "title": result.title,
            "authors": [a.name for a in result.authors],
            "abstract": result.summary.replace("\n", " ").strip(),
            "categories": result.categories,
            "published": result.published.strftime("%Y-%m-%d"),
            "updated": result.updated.strftime("%Y-%m-%d"),
            "url": result.entry_id,
            "pdf_url": result.pdf_url,
        })

    return papers


def save_json(papers: list[dict], path: str = PAPERS_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(papers, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(papers)} papers to {path}")


def save_csv(papers: list[dict], path: str = "physics_papers.csv") -> None:
    if not papers:
        return
    fieldnames = ["arxiv_id", "title", "authors", "abstract", "categories",
                  "published", "updated", "url", "pdf_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for paper in papers:
            row = paper.copy()
            row["authors"] = "; ".join(paper["authors"])
            row["categories"] = "; ".join(paper["categories"])
            writer.writerow(row)
    print(f"Saved {len(papers)} papers to {path}")


def download_and_extract(
    papers_path: str = PAPERS_PATH,
    texts_path: str = TEXTS_PATH,
    pdf_dir: str = PDF_DIR,
) -> None:
    try:
        import fitz  # pymupdf
    except ImportError:
        sys.exit("pymupdf not installed. Run: pip install pymupdf")

    if not os.path.exists(papers_path):
        sys.exit(f"{papers_path} not found. Run PhysicsScript.py first.")

    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)

    # Load existing extractions for resume support
    texts: dict = {}
    if os.path.exists(texts_path):
        with open(texts_path, encoding="utf-8") as f:
            texts = json.load(f)
        print(f"Resuming — {len(texts)} papers already extracted.")

    os.makedirs(pdf_dir, exist_ok=True)
    failed = []

    for i, paper in enumerate(papers):
        arxiv_id = paper["arxiv_id"]
        if arxiv_id in texts:
            print(f"[{i+1}/{len(papers)}] Skip {arxiv_id} (done)")
            continue

        pdf_path = os.path.join(pdf_dir, f"{arxiv_id.replace('/', '_')}.pdf")
        print(f"[{i+1}/{len(papers)}] Downloading: {paper['title'][:60]}...")

        # Download PDF
        try:
            response = requests.get(paper["pdf_url"], timeout=30, headers={"User-Agent": "wikivsrag-research/1.0"})
            response.raise_for_status()
            with open(pdf_path, "wb") as f:
                f.write(response.content)
        except Exception as e:
            print(f"  Download failed: {e}")
            failed.append(arxiv_id)
            time.sleep(DOWNLOAD_DELAY)
            continue

        # Extract text with pymupdf
        try:
            doc = fitz.open(pdf_path)
            full_text = "\n".join(page.get_text() for page in doc)
            doc.close()

            if len(full_text.strip()) < 100:
                raise ValueError("Extracted text too short — likely a scanned/image PDF")

            texts[arxiv_id] = full_text.strip()
            print(f"  Extracted {len(full_text.split()):,} words.")
        except Exception as e:
            print(f"  Extraction failed: {e}")
            failed.append(arxiv_id)
            time.sleep(DOWNLOAD_DELAY)
            continue

        # Save after every paper so progress survives interruption
        with open(texts_path, "w", encoding="utf-8") as f:
            json.dump(texts, f, ensure_ascii=False)

        time.sleep(DOWNLOAD_DELAY)

    print(f"\nDone. {len(texts)} papers extracted to {texts_path}.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")
    print("Re-run --ingest on rag_system.py and wiki_system.py to use full text.")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--download-source" in args:
        from ingest.fetch_source import fetch_all_sources
        if not os.path.exists(PAPERS_PATH):
            sys.exit(f"{PAPERS_PATH} not found. Run PhysicsScript.py first.")
        fetch_all_sources(papers_path=PAPERS_PATH)
        print("\nNext step: python rag_system.py --ingest-sources")
    elif "--download" in args:
        download_and_extract()
    else:
        print(f"Fetching 100 physics papers from ArXiv ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})...")
        papers = fetch_papers(max_results=100)
        print(f"Retrieved {len(papers)} papers.")
        save_json(papers)
        save_csv(papers)
        print("\nNext steps:")
        print("  python PhysicsScript.py --download         (PDF text extraction)")
        print("  python PhysicsScript.py --download-source  (LaTeX source — preferred)")
