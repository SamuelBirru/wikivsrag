# wikivsrag

Compares two approaches to question-answering over 100 physics papers from ArXiv:

- **RAG** — retrieves raw paper chunks at query time using vector search, then generates an answer
- **LLM Wiki** — pre-builds a synthesized knowledge base of physics concepts, then answers from that

---

## Requirements

```
pip install arxiv requests pymupdf sentence-transformers faiss-cpu numpy ollama scikit-learn
```

Install and start [Ollama](https://ollama.com/download), then pull a model:

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
`--ingest`: Ollama reads each paper and extracts key physics concepts. Saves to `wiki_extractions.json`. Resumes if interrupted.  
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
1. At ingest: each paper is split into ~400-word chunks and converted to vector embeddings (sentence-transformers + FAISS)
2. At query: your question is embedded and the `k` most similar chunks are retrieved mathematically
3. Ollama reads those chunks and writes an answer

### LLM Wiki
1. **Ingest**: Ollama reads each paper and extracts physics concepts + what the paper contributes to each
2. **Build**: contributions are grouped by concept; Ollama writes a synthesized markdown page per concept
3. At query: TF-IDF search finds the `k` most relevant wiki pages; Ollama synthesizes an answer from them

---

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `OLLAMA_MODEL` | `llama3.2` | Any model pulled via `ollama pull` |

Example:
```
$env:OLLAMA_MODEL="llama3.2:1b"   # faster, smaller model
python compare.py "your question"
```

---

## File reference

| File | Description |
|---|---|
| `PhysicsScript.py` | Fetches papers from ArXiv API, downloads PDFs |
| `rag_system.py` | RAG pipeline (ingest + query) |
| `wiki_system.py` | LLM Wiki pipeline (ingest + build + query) |
| `compare.py` | Runs both systems on the same question |
| `physics_papers.json` | Paper metadata (title, abstract, authors, URLs) |
| `paper_texts.json` | Full extracted text per paper |
| `wiki_extractions.json` | Raw concept extractions (intermediate file) |
| `wiki/` | Synthesized concept pages in markdown |
| `rag_index.faiss` | FAISS vector index |
| `rag_chunks.pkl` | Chunk text + metadata for RAG |
