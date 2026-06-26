"""
PDF OCR fallback backends for papers that ship no LaTeX source.

Two interchangeable backends, both returning Markdown text:

  - opendataloader : local, free, Java-based (needs Java 11+). Fast layout
                     extraction but no LaTeX equation reconstruction.
  - datalab        : hosted Datalab `/convert` API (needs DATALAB_API_KEY).
                     Advertises LaTeX equation conversion; costs per page.

Used by compare_ocr.py to decide which becomes the RAG PDF fallback, and
(eventually) by rag_system.py for `pdf_only` papers.
"""

import os
import glob
import time
import shutil
import tempfile

# --- OpenDataLoader (local JAR) -------------------------------------------

# OpenDataLoader's bundled JAR is compiled for Java 11+ (class file v55). The
# Windows system default is often Java 8, so point at a known JDK 11+ if the
# active `java` is too old. Override with the OCR_JDK_BIN env var.
_DEFAULT_JDK_BIN = r"C:\Program Files\Microsoft\jdk-21.0.11.10-hotspot\bin"


def _ensure_modern_java() -> None:
    """Prepend a Java 11+ bin dir to PATH so OpenDataLoader's JAR can run."""
    jdk_bin = os.environ.get("OCR_JDK_BIN", _DEFAULT_JDK_BIN)
    if os.path.isdir(jdk_bin) and jdk_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = jdk_bin + os.pathsep + os.environ.get("PATH", "")


def extract_opendataloader(pdf_path: str) -> str:
    """Extract a PDF to Markdown with OpenDataLoader. Returns markdown text."""
    _ensure_modern_java()
    import opendataloader_pdf

    out_dir = tempfile.mkdtemp(prefix="odl_")
    try:
        opendataloader_pdf.convert(pdf_path, output_dir=out_dir, format="markdown")
        md_files = glob.glob(os.path.join(out_dir, "**", "*.md"), recursive=True)
        if not md_files:
            return ""
        # The converter emits one .md named after the input file.
        md_files.sort(key=os.path.getsize, reverse=True)
        with open(md_files[0], encoding="utf-8", errors="replace") as fh:
            return fh.read()
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# --- Datalab (hosted /convert API) ----------------------------------------

DATALAB_URL = "https://www.datalab.to/api/v1/convert"


def extract_datalab(
    pdf_path: str,
    *,
    api_key: str | None = None,
    mode: str = "accurate",
    poll_interval: float = 3.0,
    timeout: float = 600.0,
) -> str:
    """Extract a PDF to Markdown via the Datalab /convert API.

    Submits the file, polls request_check_url until status == 'complete',
    and returns the markdown. mode: fast | balanced | accurate.
    """
    import requests

    api_key = api_key or os.environ.get("DATALAB_API_KEY")
    if not api_key:
        raise RuntimeError("DATALAB_API_KEY not set (add it to .env)")

    headers = {"X-API-Key": api_key}
    with open(pdf_path, "rb") as fh:
        resp = requests.post(
            DATALAB_URL,
            headers=headers,
            files={"file": (os.path.basename(pdf_path), fh, "application/pdf")},
            data={"output_format": "markdown", "mode": mode},
            timeout=120,
        )
    resp.raise_for_status()
    submit = resp.json()
    if submit.get("success") is False:
        raise RuntimeError(f"Datalab submit failed: {submit.get('error')}")
    check_url = submit.get("request_check_url")
    if not check_url:
        raise RuntimeError(f"Datalab: no request_check_url in response: {submit}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        r = requests.get(check_url, headers=headers, timeout=60)
        r.raise_for_status()
        result = r.json()
        status = result.get("status")
        if status == "complete":
            if result.get("success") is False:
                raise RuntimeError(f"Datalab convert failed: {result.get('error')}")
            # New /convert returns content under 'markdown'; tolerate 'output'.
            return result.get("markdown") or result.get("output") or ""
        if status in ("failed", "error") or result.get("error"):
            raise RuntimeError(f"Datalab error: {result.get('error')}")
    raise TimeoutError(f"Datalab polling timed out after {timeout}s")


# --- Dispatch -------------------------------------------------------------

BACKENDS = {
    "opendataloader": extract_opendataloader,
    "datalab": extract_datalab,
}


def extract(pdf_path: str, backend: str, **kwargs) -> str:
    """Extract `pdf_path` to markdown using the named backend."""
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}; choose from {list(BACKENDS)}")
    return BACKENDS[backend](pdf_path, **kwargs)
