# wikivsrag

Compares three approaches to question-answering over 100 physics papers from ArXiv:

- **RAG** — retrieves raw LaTeX chunks at query time using SPECTER2 + FAISS, with structural lookup for equation labels
- **Concept Wiki** — pre-builds a synthesized knowledge base of physics concepts (one page per concept, cross-paper)
- **Section Wiki** — pre-builds LaTeX-fidelity pages per paper section (equations, theorems, figures preserved)

---

## Requirements

```
pip install -r requirements.txt
```

---

## Credentials

Create a `.env` file in the project root:

```
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_REGION=us-east-1
CLAUDE_MODEL=us.anthropic.claude-sonnet-4-6-20251031-v1:0
DATALAB_API_KEY=your_datalab_key_here   # optional — OCR fallback for PDF-only papers
```

The model ID must match a cross-region inference profile enabled in your AWS account. Check **AWS Console → Bedrock → Cross-region inference** for the exact ID.

`DATALAB_API_KEY` is only needed if your corpus contains papers with no LaTeX source (see [PDF OCR fallback](#pdf-ocr-fallback)).

---

## Setup (run once)

### 1. Fetch 100 physics papers from ArXiv
```
python PhysicsScript.py
```
Outputs: `physics_papers.json`, `physics_papers.csv`

### 2. Download LaTeX source archives
```
python PhysicsScript.py --download-source
```
Downloads `.tar.gz` source archives from ArXiv into `sources/`, extracts LaTeX and bib files.  
Outputs: `sources/`, `source_index.json`

### 3. Build the RAG index
```
python rag_system.py --ingest-sources
```
Parses LaTeX structure-aware: equations, theorems, figures, proofs, and prose become typed chunks with section paths and LaTeX labels preserved. For papers with **no LaTeX source**, falls back to **Datalab OCR** (if `DATALAB_API_KEY` is set) — which reconstructs equations as LaTeX — then plain PDF text, then abstract. See [PDF OCR fallback](#pdf-ocr-fallback). Embeds with `allenai/specter2_base`.  
Outputs: `rag_index.faiss`, `rag_chunks.pkl`, `rag_metadata.json`

`rag_metadata.json` enables direct lookups by LaTeX label (`eq:hamiltonian`) or unambiguous equation number — bypassing FAISS entirely for exact-reference queries.

### 4a. Build the Section Wiki (recommended)
```
python wiki_generate.py --output-dir wiki_output
```
Synthesizes one markdown page per paper section from the LaTeX chunks. Preserves equations in `$$ ... $$`, figures with captions, theorems and definitions. Resumes safely if interrupted.  
Outputs: `wiki_output/`, `wiki_output/_index.json`

Or use the Claude Code skill if you have Claude Code installed:
```
/physics-wiki-create --output-dir wiki_output
```

### 4b. Build the Concept Wiki (optional, for comparison)
```
python wiki_system.py --ingest
python wiki_system.py --build
```
Claude reads each paper and extracts key physics concepts, then synthesizes one page per concept into `wiki/`. Makes one LLM call per paper at ingest time.  
Outputs: `wiki_extractions.json`, `wiki/`

---

## Usage

### Query RAG
```
python rag_system.py "What methods are used to study quantum entanglement?"
python rag_system.py "eq:hamiltonian"          # direct label lookup
python rag_system.py -k 8 "your question"
```

### Query Section Wiki
```
python wiki_generate.py "What methods are used to study quantum entanglement?"
python wiki_generate.py --output-dir wiki_output -k 6 "your question"
```

### Query Concept Wiki
```
python wiki_system.py "What methods are used to study quantum entanglement?"
```

### Compare all three side-by-side
```
python compare.py "What methods are used to study quantum entanglement?"
python compare.py --no-concept "your question"   # skip concept wiki if not built
python compare.py -k 6 "your question"
```

---

## Claude Code skill

If you have [Claude Code](https://claude.ai/code) installed, the `/physics-wiki-create` skill is available after cloning:

```
/physics-wiki-create --dry-run                        # preview pages without API calls
/physics-wiki-create --output-dir wiki_output         # full generation
/physics-wiki-create --ingest --output-dir wiki_output  # re-ingest then generate
/physics-wiki-create --paper 2301.12345               # single paper only
```

The skill handles ingestion checks, progress reporting, and error guidance automatically.

---

## How each system works

### RAG
1. **Ingest**: papers are parsed into typed chunks — equations, theorems, figures, proofs, prose — with section paths and LaTeX labels preserved. Chunks are embedded with `allenai/specter2_base` into a FAISS index. A `MetadataIndex` is built for structural lookups.
2. **Query**: if the question matches a LaTeX label or unambiguous equation number, the structural index resolves it directly. Otherwise the question is embedded and 50 candidates are retrieved from FAISS.
3. **MMR** re-ranks the candidate pool to select `k` chunks balancing relevance against redundancy across papers.
4. Claude generates an answer from the selected chunks.

### Section Wiki
1. **Build**: each paper section's LaTeX chunks are assembled in type-priority order (prose → theorem → equation → figure) and fed to Claude, which writes a structured markdown page preserving all equations and figures. Pages are written to `wiki_output/<paper_id>/<section>.md`. Resumes from `_index.json` if interrupted.
2. **Query**: uses the same FAISS index as RAG — no separate embeddings needed. Top-k chunk hits are mapped to their corresponding wiki pages; Claude synthesizes an answer from the synthesized pages rather than raw chunks.

### Concept Wiki
1. **Ingest**: Claude reads each paper in full and extracts key physics concepts + contributions. Saves to `wiki_extractions.json`.
2. **Build**: contributions are grouped by concept; Claude writes one synthesized page per concept into `wiki/`. Cross-paper synthesis is pre-computed at build time.
3. **Query**: TF-IDF finds the `k` most relevant concept pages; Claude synthesizes an answer.

---

## Evaluation

### Systematic test — 20 questions judged by Claude

Questions cover three types: specific paper lookup, broad synthesis, and factual recall.

```
python eval.py --run               # run all three systems (resumes per-system)
python eval.py --run --no-concept  # skip concept wiki if not built
python eval.py --report            # writes eval_report.md
```

Paste `eval_report.md` to Claude for three-way judgment.

### Results with Claude Sonnet 4.6 (three-way, 20 questions)

| System | Wins | Ties | Losses |
|---|---|---|---|
| **Section Wiki** | **14** | 2 | 4 |
| Concept Wiki | 3 | 2 | 15 |
| RAG | 1 | 1 | 18 |

**Section Wiki dominates** — because it retrieves actual paper sections with LaTeX formulas, exact numerical values, and direct quotes rather than summaries. On specific lookups it gives the actual Fermi's Golden Rule expressions, exact Zeeman sublevel labels, or precise convergence-rate formulas. On synthesis questions it finds content the other two systems miss entirely.

**Section Wiki's failure mode is retrieval miss** — when FAISS returns irrelevant pages it honestly returns "not in these pages" rather than hallucinating. This cost it 4 questions where Concept Wiki or RAG retrieved correctly.

**Concept Wiki wins on cross-paper synthesis when Section Wiki misfires** — its pre-computed concept pages are reliable when the question spans many papers and Section Wiki's per-section retrieval doesn't land on the right pages.

**RAG wins only on narrow verbatim lookups** — the one question RAG won (ML technique in a specific paper) was one both wikis failed to index at all.

| Question type | RAG | Concept Wiki | Section Wiki |
|---|---|---|---|
| Broad synthesis, cross-paper | Weaker | Strong | Strong |
| Specific lookup, single paper | Strong | Weaker | Strong (with exact formulas) |
| Precise numerical facts | Moderate | Moderate | Strong |
| Equation/label references | Strong (structural lookup) | Weaker | Strong |
| Conceptual explanations | Moderate | Strong | Strong |

### Key tradeoffs

| | RAG | Concept Wiki | Section Wiki |
|---|---|---|---|
| Setup cost | Low — no LLM at ingest | High — 1 LLM call/paper | High — 1 LLM call/section |
| Equation fidelity | High — LaTeX preserved | Low — PDF-extracted | High — LaTeX preserved |
| Structural lookup | Yes (labels, eq numbers) | No | No |
| Cross-paper synthesis | Weaker | Strong (pre-computed) | Strong (query-time, with source fidelity) |
| Retrieval | FAISS + MMR | TF-IDF | FAISS + MMR (shared index) |
| Hallucination risk | Low at retrieval | Moderate | Moderate |

---

## PDF OCR fallback

Most papers ship LaTeX source, but some are **PDF-only**. Since the entire pipeline depends on equation fidelity, a PDF-only paper needs an extractor that reconstructs equations as LaTeX — otherwise it pollutes the index and the wikis with garbled math. We evaluated two extractors as the fallback.

### Evaluated: OpenDataLoader vs Datalab

Both were run on a representative PDF-only physics paper (`2605.05785v1`, *Optical Pulling Force in Carbon Nanotubes*, 23 pages) via `compare_ocr.py`:

| Metric | OpenDataLoader | Datalab `/convert` |
|---|---|---|
| Latency | **8.5 s** | 30.9 s |
| Output size | 65.8 KB | 79.7 KB |
| Inline math (`$…$`) | 0 | **231** |
| Block equations (`$$…$$`) | 0 | **62** |
| LaTeX commands | 0 | **1327** |
| Greek glyphs | dropped (lost) | preserved |
| Cost | free, local, offline | per-page API |

**Finding: Datalab wins decisively, OpenDataLoader is unusable for physics.** OpenDataLoader is a fast, free, local layout/text extractor — but it performs **no LaTeX equation reconstruction**. It produced *zero* math markup and even dropped Greek glyphs entirely (PDFBox `No glyph for code 0` warnings), shredding equations into fragments like `zz(,q) =i(,q)− (0,q)/`. Datalab reconstructed the same nonlocal-conductivity equation faithfully as

```latex
\sigma_{zz}(\omega, q) = i[\Pi(\omega, q) - \Pi(0, q)]/\omega
```

with the full polarizability tensor, both interband/intraband conductivity integrals, and equation numbers preserved. The 3.6× latency and per-page cost are irrelevant when the alternative output is unusable — and PDF-only papers are rare (1 in 100 in this corpus).

> OpenDataLoader's JAR requires **Java 11+**; the comparison harness auto-points at a JDK 11+ install (override with `OCR_JDK_BIN`). It's kept in `requirements.txt` for `compare_ocr.py` only — it is **not** used in the production pipeline.

### How the fallback works

`rag_system.py --ingest-sources` routes each non-LaTeX paper through Datalab `/convert`, caches the markdown to `ocr_cache/<id>.md` (the API is billed **once per paper**, even across re-ingests), and parses it into the **same typed chunks** (`prose` / `equation` / `figure`) the LaTeX parser produces — with section paths and equation numbers preserved. Those chunks land in the shared `rag_index.faiss` / `rag_chunks.pkl` / `rag_metadata.json`, so a single ingest feeds **RAG, the metadata index, and the section wiki** identically. Re-run the comparison yourself with `python compare_ocr.py`.

---

## Configuration

| Variable | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_REGION` | Region with Bedrock enabled (default: `us-east-1`) |
| `CLAUDE_MODEL` | Bedrock cross-region inference profile ID |
| `DATALAB_API_KEY` | Optional — Datalab `/convert` key for the PDF OCR fallback |
| `OCR_JDK_BIN` | Optional — path to a Java 11+ `bin/` dir for OpenDataLoader (`compare_ocr.py` only) |

Tunable constants in `rag_system.py`:

| Constant | Default | Description |
|---|---|---|
| `MMR_LAMBDA` | `0.6` | Relevance/diversity balance (0 = max diversity, 1 = max relevance) |
| `MMR_FETCH` | `50` | Candidate pool size before MMR re-ranks |
| `CHUNK_WORDS` | `400` | Words per chunk (legacy PDF pipeline only) |
| `CHUNK_OVERLAP` | `50` | Overlap between chunks (legacy PDF pipeline only) |

---

## File reference

| File | Description |
|---|---|
| `PhysicsScript.py` | Fetches papers from ArXiv, downloads PDFs or LaTeX sources |
| `rag_system.py` | RAG pipeline — LaTeX ingest, FAISS+MMR+structural query |
| `wiki_generate.py` | Section wiki — builds and queries LaTeX-fidelity section pages |
| `wiki_system.py` | Concept wiki — builds and queries cross-paper concept pages |
| `compare.py` | Three-way comparison: RAG vs concept wiki vs section wiki |
| `eval.py` | Systematic evaluation across 20 questions, per-system resume |
| `ingest/parse_latex.py` | LaTeX parser — extracts typed chunks with labels and section paths |
| `ingest/parse_markdown.py` | Parses Datalab OCR markdown into the same typed chunks as the LaTeX parser |
| `ingest/parse_bib.py` | BibTeX parser — resolves `\cite{}` keys to human-readable references |
| `ingest/ocr_fallback.py` | OCR backends — Datalab `/convert` (production) and OpenDataLoader (comparison only) |
| `ingest/fetch_source.py` | Downloads LaTeX source archives from ArXiv |
| `compare_ocr.py` | Benchmarks OCR backends on real PDFs (latency + equation-fidelity proxies) |
| `embed/embedder.py` | SPECTER2 wrapper for scientific paper embeddings |
| `store/chunk_store.py` | In-memory chunk collection with type stats |
| `store/metadata_index.py` | Structural index for label/equation/section lookups |
| `.claude/commands/physics-wiki-create.md` | Claude Code skill definition |
| `physics_papers.json` | Paper metadata (title, abstract, authors, URLs) |
| `source_index.json` | Maps arxiv IDs to local LaTeX source paths |
| `rag_metadata.json` | Structural lookup index (labels, equation numbers, sections) |
| `wiki_output/` | Section wiki pages + `_index.json` |
| `wiki/` | Concept wiki pages |
| `eval_results.json` | Raw evaluation results (all three systems) |
| `eval_report.md` | Formatted evaluation report |
