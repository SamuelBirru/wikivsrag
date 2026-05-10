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

Four questions were tested head-to-head across both systems.

### Results summary

| # | Question type | Winner | Reason |
|---|---|---|---|
| 1 | Experimental results across papers | Neither | Papers in dataset were mostly theoretical — data problem, not a system problem |
| 2 | Broad synthesis — first run ("How is ML applied to quantum computing?") | **Wiki** | Retrieved 5 distinct concept pages; RAG flooded from one paper (pre-MMR) |
| 3 | Specific paper lookup ("What technique does the rhombus qubit use?") | **RAG** | Found verbatim details; Wiki conflated two similar papers and named the wrong technique |
| 4 | Broad synthesis — second run ("How is ML applied to quantum computing?") | **RAG** | MMR now working; Wiki retrieved duplicate pages and pulled off-topic results due to weak TF-IDF retrieval |

**Final score: RAG 2 — Wiki 1 — Draw 1**

### Verdict for physics paper Q&A

**RAG with MMR is the better approach for this use case.** Here is why:

Physics papers are dense with specific terminology, equations, methods, and results. The questions most worth asking — "what did this paper find?", "what method was used?", "what are the experimental parameters?" — all require faithfulness to the source text. RAG preserves that. Wiki rewrites it, and in a field as precise as physics, rewriting introduces errors.

Wiki's core weakness showed up consistently: its TF-IDF retrieval cannot understand question intent the way embedding search can, and its synthesis step blends papers that use similar terminology but make different claims. In physics, conflating two papers is worse than returning no answer.

Wiki's theoretical advantage — cross-paper synthesis — did not materialise in practice because the concept pages were too general and the retrieval too noisy to focus on the right concepts for a given question.

**RAG is recommended** unless your questions are deliberately broad overviews ("summarise all approaches to quantum error correction") where faithfulness to individual papers matters less than a high-level map of the field.

### Key tradeoffs

| | RAG | LLM Wiki |
|---|---|---|
| Setup time | Fast — embedding only, no LLM calls | Slow — one LLM call per paper |
| Query time | Fast | Moderate |
| Source fidelity | High — verbatim paper text | Lower — LLM rewrites and can blend papers |
| Retrieval quality | Strong — semantic embedding search | Weaker — TF-IDF keyword matching |
| Hallucination risk | Low at retrieval; only at generation | Higher — synthesis step introduces errors |
| Cross-paper synthesis | Weaker — retrieves chunks, not concepts | Stronger in theory; inconsistent in practice |
| Best question type | Specific lookup, factual, paper-targeted | Broad overview of a well-defined concept |

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
