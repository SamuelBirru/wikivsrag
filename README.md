# wikivsrag

Compares two approaches to question-answering over 100 physics papers from ArXiv:

- **RAG** — retrieves raw paper chunks at query time using vector search, then generates an answer
- **LLM Wiki** — pre-builds a synthesized knowledge base of physics concepts, then answers from that

---

## Requirements

```
pip install arxiv requests pymupdf sentence-transformers faiss-cpu numpy ollama scikit-learn
```

Install [Ollama](https://ollama.com/download), then pull a model:

```
ollama pull llama3.2
```

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
```
python rag_system.py --ingest
```
Chunks each paper into ~400-word pieces and embeds them into a FAISS vector index.  
Outputs: `rag_index.faiss`, `rag_chunks.pkl`

### 4. Build the Wiki
```
python wiki_system.py --ingest
python wiki_system.py --build
```
`--ingest`: Ollama reads each paper and extracts physics concepts. Saves to `wiki_extractions.json`. Resumes if interrupted.  
`--build`: Synthesizes one markdown wiki page per concept into `wiki/`.

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

### Adjust how many results are retrieved (default: 5)
```
python rag_system.py -k 8 "your question"
python wiki_system.py -k 6 "your question"
python compare.py -k 6 "your question"
```

---

## How each system works

### RAG
1. At ingest: each paper is split into ~400-word chunks and embedded into a FAISS vector index
2. At query: the question is embedded and a pool of 50 candidate chunks is retrieved from FAISS
3. **MMR (Maximal Marginal Relevance)** re-ranks the pool to select `k` chunks that balance relevance to the query against redundancy with each other — ensuring diversity across papers
4. Ollama reads those chunks and generates an answer

### LLM Wiki
1. **Ingest**: Ollama reads each paper (up to 5000 words) and extracts key physics concepts + what the paper contributes to each. Saves to `wiki_extractions.json`.
2. **Build**: Contributions are grouped by concept; Ollama writes a synthesized markdown page per concept into `wiki/`
3. At query: TF-IDF search finds the `k` most relevant concept pages; Ollama synthesizes an answer from them

---

## Evaluation

Three questions were tested head-to-head. Here is an honest assessment of each system based on observed results.

### Results summary

| Question type | Winner | Reason |
|---|---|---|
| Broad synthesis ("How is ML applied to quantum computing?") | **Wiki** | Pulled 5 distinct concept pages; RAG still flooded from one paper despite MMR |
| Specific paper lookup ("What technique does the rhombus qubit use?") | **RAG** | Found verbatim details from the paper; Wiki blended two papers and named the wrong technique |
| Experimental results across papers | Neither | Both struggled; papers in dataset were mostly theoretical |

### When to use RAG

- **Specific, narrow questions** targeting one paper or one finding
- When **source fidelity matters** — RAG returns verbatim paper text, so it cannot invent details that aren't there
- When you need **fast setup** — ingest is just embedding, no LLM calls required
- When the corpus changes frequently — re-indexing is cheap

### When to use LLM Wiki

- **Broad synthesis questions** that span multiple papers ("What are the main approaches to X?")
- When you want **structured, readable answers** — Wiki pages are pre-written in clean prose
- When the corpus is **stable** — the upfront ingest cost (100 LLM calls) is paid once
- When you want knowledge to **compound** — adding new papers enriches existing concept pages

### Key tradeoffs

| | RAG | LLM Wiki |
|---|---|---|
| Setup time | Fast (embedding only) | Slow (one LLM call per paper) |
| Query time | Fast | Moderate |
| Source fidelity | High — verbatim text | Lower — LLM rewrites and may blend papers |
| Cross-paper synthesis | Weak — retrieves chunks, not concepts | Strong — synthesis is done at build time |
| Hallucination risk | Low at retrieval; risk at generation | Higher — synthesis can conflate similar papers |
| Best question type | Specific lookup | Broad synthesis |

### Honest verdict

Neither approach dominates. RAG is safer and more faithful to sources; Wiki produces more readable and connected answers but introduces a synthesis step where information can be lost or confused. In production, the two are often combined: RAG for retrieval, with a Wiki-style pre-processing layer to enrich the chunks before embedding.

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `OLLAMA_MODEL` | `llama3.2` | Any model pulled via `ollama pull` |

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
| `physics_papers.json` | Paper metadata (title, abstract, authors, URLs) |
| `paper_texts.json` | Full extracted text per paper |
| `wiki_extractions.json` | Raw concept extractions (intermediate file) |
| `wiki/` | Synthesized concept pages in markdown |
| `rag_index.faiss` | FAISS vector index |
| `rag_chunks.pkl` | Chunk text + metadata for RAG |
