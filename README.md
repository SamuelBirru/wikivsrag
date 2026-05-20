# wikivsrag

Compares two approaches to question-answering over 100 physics papers from ArXiv:

- **RAG** — retrieves raw paper chunks at query time using vector search, then generates an answer
- **LLM Wiki** — pre-builds a synthesized knowledge base of physics concepts, then answers from that

---

## Requirements

```
pip install arxiv requests pymupdf sentence-transformers faiss-cpu numpy anthropic python-dotenv scikit-learn
```

---

## Credentials

Create a `.env` file in the project root:

```
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_REGION=us-east-1
CLAUDE_MODEL=us.anthropic.claude-sonnet-4-6
```

The model ID must match a cross-region inference profile enabled in your AWS account. Check **AWS Console → Bedrock → Cross-region inference** for the exact ID.

---

## Setup (run once)

### 1. Fetch 100 physics papers from ArXiv
```
python PhysicsScript.py
```
Outputs: `physics_papers.json`, `physics_papers.csv`

### 2. Download full paper PDFs and extract text
```
python PhysicsScript.py --download
```
Outputs: `paper_texts.json`, `pdfs/`  
Takes ~7 minutes (rate-limited to respect ArXiv). Resumes safely if interrupted.

### 3. Build the RAG index

**Recommended — LaTeX-first pipeline (Phase 1):**
```
python PhysicsScript.py --download-source
python rag_system.py --ingest-sources
```
Downloads LaTeX source archives and parses them structure-aware: equations, theorems, figures, and proofs become typed chunks with section paths and LaTeX labels preserved. Falls back to PDF text or abstract when source is unavailable.  
Outputs: `rag_index.faiss`, `rag_chunks.pkl`, `rag_metadata.json`

`rag_metadata.json` is a structural index enabling direct lookups by equation number, LaTeX label (`eq:hamiltonian`), chunk type, or section path — bypassing FAISS for exact-reference queries.

**Legacy — PDF pipeline:**
```
python rag_system.py --ingest
```
Chunks each paper into ~400-word pieces and embeds them. No structure awareness.  
Outputs: `rag_index.faiss`, `rag_chunks.pkl`

### 4. Build the Wiki
```
python wiki_system.py --ingest
python wiki_system.py --build
```
`--ingest`: Claude reads each paper in full and extracts key physics concepts + contributions. Saves to `wiki_extractions.json`. Resumes if interrupted.  
`--build`: Synthesizes one markdown wiki page per concept into `wiki/`.

Note: The ingest step makes one Claude API call per paper (100 calls total). This is the most expensive step — monitor your Bedrock usage.

---

## Usage

### Query RAG
```
python rag_system.py "What methods are used to study quantum entanglement?"
```

### Query Wiki
```
python wiki_system.py "What methods are used to study quantum entanglement?"
```

### Compare both side-by-side
```
python compare.py "What methods are used to study quantum entanglement?"
```

### Adjust how many results are retrieved (default: 5 for RAG, 4 for Wiki)
```
python rag_system.py -k 8 "your question"
python wiki_system.py -k 6 "your question"
python compare.py -k 6 "your question"
```

---

## How each system works

### RAG
1. At ingest (LaTeX path): papers are parsed into typed chunks — equations, theorems, figures, proofs, prose — with section paths and LaTeX labels preserved. Chunks are embedded with `allenai/specter2_base` (trained on scientific papers) into a FAISS index. A structural `MetadataIndex` is also built for exact-reference lookups.
2. At query: if the question looks like a direct reference (`"equation 4"`, `"eq:hamiltonian"`), the structural index resolves it immediately without FAISS. Otherwise the question is embedded and a pool of 50 candidates is retrieved from FAISS.
3. **MMR (Maximal Marginal Relevance)** re-ranks the pool to select `k` chunks that balance relevance against redundancy — ensuring diversity across papers
4. Claude reads those chunks and generates an answer

### LLM Wiki
1. **Ingest**: Claude reads each paper in full and extracts key physics concepts + what the paper contributes to each. Saves to `wiki_extractions.json`
2. **Build**: Contributions are grouped by concept; Claude writes a synthesized markdown page per concept into `wiki/`
3. At query: TF-IDF search finds the `k` most relevant concept pages; Claude synthesizes an answer from them

---

## Evaluation

### Systematic test — 20 questions judged by Claude

A structured evaluation was run across 20 questions covering three types: specific paper lookup, broad synthesis, and factual recall. Both systems were evaluated using Claude Sonnet 4.6 via Amazon Bedrock.

Run it yourself:
```
python eval.py --run
python eval.py --report   # writes eval_report.md
```

### Results with Claude Sonnet 4.6

**Wiki is the stronger system overall**, but each has a clear niche.

**Wiki wins on synthesis questions** — almost every time. It identifies cross-paper patterns, provides structured tables, and connects concepts across the dataset. On questions like "How do multiple papers use tensor networks?" or "What approaches to quantum error correction appear across these papers?", Wiki gave structured, multi-angle answers that RAG couldn't match because it retrieves chunks from individual papers rather than pre-synthesized concept pages.

**RAG wins on specific direct lookups** — when the answer lives in one paper, RAG pulls the exact text. On q04 (what ML technique does a specific paper use), Wiki explicitly admitted it didn't know; RAG quoted the paper directly. For precise numerical facts and specific experimental parameters, RAG is more reliable because it returns verbatim source text rather than a rewrite.

**Key failure modes:**
- RAG failed q10 (Rayleigh criterion) because the relevant section wasn't in the retrieved chunks — a fundamental limitation of chunking
- Wiki failed q04 because the extraction step didn't capture the specific ML method — a limitation of what Claude chose to summarize at ingest time

### Verdict

| Question type | Better system |
|---|---|
| Broad synthesis, cross-paper connections | Wiki |
| Specific lookup in a single paper | RAG |
| Precise numerical facts | RAG |
| Conceptual explanations | Wiki |

A hybrid approach — use Wiki for exploration, RAG to verify specific claims — would outperform either alone.

### Key tradeoffs

| | RAG | LLM Wiki |
|---|---|---|
| Setup cost | Cheap — embedding only, no LLM calls | Expensive — one LLM call per paper at ingest |
| Query time | Fast | Moderate |
| Source fidelity | High — verbatim paper text | Lower — Claude rewrites and can blend papers |
| Retrieval quality | Strong — semantic embedding search | Weaker — TF-IDF keyword matching |
| Hallucination risk | Low at retrieval; only at generation | Depends on model quality |
| Cross-paper synthesis | Weaker — retrieves chunks, not concepts | Strong — pre-synthesized concept pages |
| Model dependency | Low — embedding model is separate from LLM | High — synthesis quality determines everything |
| Best question type | Specific lookup, factual, paper-targeted | Broad synthesis, conceptual questions |

---

## Configuration

Create or edit `.env` in the project root to set credentials and model:

| Variable | Default in `.env` | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | — | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | — | AWS secret key |
| `AWS_REGION` | `us-east-1` | Region with Bedrock enabled |
| `CLAUDE_MODEL` | `us.anthropic.claude-sonnet-4-6` | Bedrock cross-region inference profile ID |

Tunable constants in `rag_system.py`:

| Constant | Default | Description |
|---|---|---|
| `MMR_LAMBDA` | `0.6` | Relevance/diversity balance (0 = max diversity, 1 = max relevance) |
| `MMR_FETCH` | `50` | Candidate pool size before MMR re-ranks |
| `CHUNK_WORDS` | `400` | Words per chunk |
| `CHUNK_OVERLAP` | `50` | Overlap between consecutive chunks |

---

## File reference

| File | Description |
|---|---|
| `PhysicsScript.py` | Fetches papers from ArXiv API, downloads PDFs |
| `rag_system.py` | RAG pipeline with MMR (ingest + query) |
| `wiki_system.py` | LLM Wiki pipeline (ingest + build + query) |
| `compare.py` | Runs both systems on the same question side-by-side |
| `eval.py` | Runs both systems on all questions in `questions.json`, writes `eval_report.md` |
| `.env` | AWS credentials and model config (not committed to git) |
| `physics_papers.json` | Paper metadata (title, abstract, authors, URLs) |
| `paper_texts.json` | Full extracted text per paper |
| `wiki_extractions.json` | Raw concept extractions (intermediate file) |
| `wiki/` | Synthesized concept pages in markdown |
| `rag_index.faiss` | FAISS vector index |
| `rag_chunks.pkl` | Chunk text + metadata for RAG |
| `eval_results.json` | Raw evaluation results |
| `eval_report.md` | Formatted evaluation report |
