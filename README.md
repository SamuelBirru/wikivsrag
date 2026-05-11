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

### Systematic test — 20 questions judged by Claude

A structured evaluation was run across 20 questions covering three types: specific paper lookup, broad synthesis, and factual recall. Both systems were run on every question and Claude judged each pair of answers.

**Final score: RAG 13 — Wiki 3 — Tie 4**

| Question type | RAG wins | Wiki wins | Ties |
|---|---|---|---|
| Specific (10 questions) | 9 | 0 | 1 |
| Synthesis (6 questions) | 2 | 3 | 1 |
| Factual (4 questions) | 2 | 0 | 2 |

### What went wrong with Wiki

Wiki's failures fell into two distinct categories:

**1. Hallucination during `--build`** — The synthesis step produced confident but fabricated content. Examples: inventing "Hořava-Lifshitz gravity" as the framework for the altermagnetic magnon paper (it uses Schwinger-Keldysh field theory), fabricating a "Rayleigh-Faber-Kraichnan condition" with a made-up formula, and attributing Fourier engineering from one qubit paper to a different qubit paper. These errors were then stored as ground truth in the wiki pages and returned confidently at query time.

**2. TF-IDF retrieval failures** — Wiki's keyword-based retrieval consistently landed on the wrong concept pages for specific questions. Embedding search (used by RAG) understands question intent semantically; TF-IDF matches keywords and gets confused when physics terminology overlaps across unrelated topics.

**Root cause** — llama3.2 (3B parameters) is not large or capable enough to reliably synthesise dense physics content into accurate wiki pages. The LLM Wiki approach is sound in principle, but it places a high demand on the synthesis model. A weak model produces plausible-sounding but wrong notes, and those errors compound at query time.

### What Wiki got right

Wiki won all three of its wins on synthesis questions, which is where it has a structural advantage. When its concept pages happened to be accurate and TF-IDF retrieved the right pages, its pre-synthesised answers were more structured and readable than RAG's on-the-fly generation. The potential is real — it just wasn't realised reliably with a 3B model.

### Would a stronger model change the outcome?

Yes — significantly. With a frontier model like Claude Sonnet 4.6 or 4.7:

- The `--build` hallucinations would largely disappear. A larger model reads dense physics accurately and writes faithful summaries rather than plausible fabrications.
- Concept extraction during `--ingest` would produce cleaner, more consistent concept names, reducing the duplicate-page problem (e.g. "Quantum computing" vs "Quantum Computing") and improving TF-IDF retrieval.
- Cross-paper synthesis would become genuinely useful — a capable model can identify non-obvious connections between papers that a 3B model misses entirely.

**With a frontier model, Wiki would likely match or beat RAG on synthesis questions and close the gap significantly on specific questions.** RAG would still hold an edge on precise factual recall (specific numbers, exact methods) because it returns verbatim text rather than a rewrite — but the catastrophic hallucination failures that cost Wiki most of its points would be eliminated.

The TF-IDF retrieval weakness would remain regardless of model size — that is an architectural limitation. Replacing TF-IDF with embedding-based wiki page retrieval (the same approach RAG uses for chunks) would close that gap and make the comparison genuinely competitive.

### Verdict for physics paper Q&A with a small local model

**RAG with MMR is the better approach when using a small local model (≤7B parameters).** It is safer, more faithful to sources, and does not depend on the synthesis model being accurate. Wiki's advantage requires synthesis quality the small model cannot reliably provide.

**With a frontier model (Claude Sonnet/Opus, GPT-4o), the comparison becomes genuinely close.** Wiki's structural advantages — compounding knowledge, pre-synthesised concept pages, cross-paper connections — materialise properly when the synthesis model is capable enough to write accurate notes.

### Key tradeoffs

| | RAG | LLM Wiki |
|---|---|---|
| Setup time | Fast — embedding only, no LLM calls | Slow — one LLM call per paper |
| Query time | Fast | Moderate |
| Source fidelity | High — verbatim paper text | Lower — LLM rewrites and can blend papers |
| Retrieval quality | Strong — semantic embedding search | Weaker — TF-IDF keyword matching |
| Hallucination risk | Low at retrieval; only at generation | Higher with weak models; low with frontier models |
| Cross-paper synthesis | Weaker — retrieves chunks, not concepts | Weak with small models; strong with frontier models |
| Model dependency | Low — embedding model is separate from generation | High — synthesis quality determines everything |
| Best question type | Specific lookup, factual, paper-targeted | Broad synthesis, especially with a capable model |

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
