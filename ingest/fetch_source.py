"""
Fetches LaTeX source archives from ArXiv's e-print endpoint.

For each paper in physics_papers.json, downloads the source tarball and
extracts it to sources/{arxiv_id}/.  Produces source_index.json that maps
each arxiv_id to its status and relevant file paths.

Three possible outcomes per paper:
  tex      — .tar.gz or single .gz containing .tex source
  pdf_only — ArXiv source is a bare PDF (author submitted PDF, no .tex)
  unknown  — unrecognised format or download failure

Usage (called from PhysicsScript.py --download-source):
  from ingest.fetch_source import fetch_all_sources
  fetch_all_sources()
"""

import gzip
import io
import json
import os
import tarfile
import time

import requests

from ingest.detect_source import find_main_tex

_SOURCE_URL = "https://arxiv.org/e-print/{}"
_HEADERS = {"User-Agent": "wikivsrag-research/1.0"}

SOURCES_DIR = "sources"
SOURCE_INDEX_PATH = "source_index.json"
DOWNLOAD_DELAY = 4  # seconds — respects ArXiv bulk-access rate limit

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".eps", ".ps", ".svg", ".gif"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_all_sources(
    papers_path: str = "physics_papers.json",
    sources_dir: str = SOURCES_DIR,
    index_path: str = SOURCE_INDEX_PATH,
) -> dict:
    """
    Download source archives for every paper in papers_path.
    Resumes automatically if interrupted — already-fetched IDs are skipped.

    Returns the completed source index dict.
    """
    with open(papers_path, encoding="utf-8") as fh:
        papers = json.load(fh)

    index: dict = {}
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as fh:
            index = json.load(fh)
        already = sum(1 for e in index.values() if e["status"] in ("tex", "pdf_only"))
        print(f"Resuming — {already}/{len(papers)} papers already fetched.")

    os.makedirs(sources_dir, exist_ok=True)

    for i, paper in enumerate(papers):
        arxiv_id = paper["arxiv_id"]
        if arxiv_id in index:
            print(f"[{i+1}/{len(papers)}] Skip {arxiv_id} (done)")
            continue

        print(f"[{i+1}/{len(papers)}] {arxiv_id}  {paper['title'][:55]}...")

        url = _SOURCE_URL.format(arxiv_id)
        try:
            resp = requests.get(url, timeout=30, headers=_HEADERS)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  Fetch failed: {exc}")
            index[arxiv_id] = _failed_entry()
            _save_index(index, index_path)
            time.sleep(DOWNLOAD_DELAY)
            continue

        entry = _process_response(arxiv_id, resp.content, sources_dir)
        index[arxiv_id] = entry
        _save_index(index, index_path)

        main_name = os.path.basename(entry["main_tex"]) if entry["main_tex"] else "—"
        print(f"  [{entry['status']}]  main: {main_name}")
        time.sleep(DOWNLOAD_DELAY)

    tex_n = sum(1 for e in index.values() if e["status"] == "tex")
    pdf_n = sum(1 for e in index.values() if e["status"] == "pdf_only")
    fail_n = len(index) - tex_n - pdf_n
    print(f"\nDone. {tex_n} tex  |  {pdf_n} pdf-only  |  {fail_n} failed/unknown")
    print(f"Source index written to: {index_path}")
    return index


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _process_response(arxiv_id: str, content: bytes, sources_dir: str) -> dict:
    safe_id = arxiv_id.replace("/", "_")
    paper_dir = os.path.join(sources_dir, safe_id)
    os.makedirs(paper_dir, exist_ok=True)

    # Detect type by magic bytes — more reliable than Content-Type header
    if content[:4] == b"%PDF":
        return _handle_pdf_only(content, paper_dir)

    if content[:2] == b"\x1f\x8b":
        return _handle_gzip(content, paper_dir)

    # Unknown format — save raw bytes and mark as unknown
    with open(os.path.join(paper_dir, "raw_source.bin"), "wb") as fh:
        fh.write(content)
    return _unknown_entry()


def _handle_pdf_only(content: bytes, paper_dir: str) -> dict:
    pdf_path = os.path.join(paper_dir, "source.pdf")
    with open(pdf_path, "wb") as fh:
        fh.write(content)
    return {
        "status": "pdf_only",
        "main_tex": None,
        "bib_file": None,
        "image_files": [],
        "pdf_fallback": pdf_path,
    }


def _handle_gzip(content: bytes, paper_dir: str) -> dict:
    bio = io.BytesIO(content)

    # Try .tar.gz first — the most common ArXiv source format
    try:
        bio.seek(0)
        with tarfile.open(fileobj=bio, mode="r:gz") as tar:
            safe_members = [
                m for m in tar.getmembers()
                if not os.path.isabs(m.name) and ".." not in m.name
            ]
            tar.extractall(paper_dir, members=safe_members)
        return _index_extracted_dir(paper_dir)
    except tarfile.TarError:
        pass

    # Not a tarball — single gzipped file (typically one .tex file)
    try:
        bio.seek(0)
        decompressed = gzip.decompress(content)
        main_tex = os.path.join(paper_dir, "main.tex")
        with open(main_tex, "wb") as fh:
            fh.write(decompressed)
        return {
            "status": "tex",
            "main_tex": main_tex,
            "bib_file": None,
            "image_files": [],
            "pdf_fallback": None,
        }
    except Exception:
        pass

    return _unknown_entry()


def _index_extracted_dir(paper_dir: str) -> dict:
    tex_files, bib_files, image_files = [], [], []

    for root, _, files in os.walk(paper_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext == ".tex":
                tex_files.append(fpath)
            elif ext == ".bib":
                bib_files.append(fpath)
            elif ext in _IMAGE_EXTS:
                image_files.append(fpath)

    if not tex_files:
        # Archive contained no .tex — treat as pdf_only if a PDF is present
        pdf_candidates = [
            f for f in os.listdir(paper_dir)
            if f.lower().endswith(".pdf")
        ]
        return {
            "status": "pdf_only",
            "main_tex": None,
            "bib_file": bib_files[0] if bib_files else None,
            "image_files": image_files,
            "pdf_fallback": os.path.join(paper_dir, pdf_candidates[0]) if pdf_candidates else None,
        }

    main_tex = find_main_tex(tex_files)
    return {
        "status": "tex",
        "main_tex": main_tex,
        "bib_file": bib_files[0] if bib_files else None,
        "image_files": image_files,
        "pdf_fallback": None,
    }


def _failed_entry() -> dict:
    return {"status": "fetch_failed", "main_tex": None, "bib_file": None, "image_files": [], "pdf_fallback": None}


def _unknown_entry() -> dict:
    return {"status": "unknown", "main_tex": None, "bib_file": None, "image_files": [], "pdf_fallback": None}


def _save_index(index: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)
