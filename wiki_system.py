"""
LLM Wiki system for physics papers - based on Karpathy's LLM Wiki pattern.

Instead of retrieving raw chunks at query time, this system builds a persistent,
synthesized wiki of physics concepts that the LLM maintains and queries.

Three-phase workflow:
  1. Ingest  — Claude (via Bedrock) reads each paper and extracts key concepts.
               Saves to wiki_extractions.json. Supports resume if interrupted.
  2. Build   — Claude synthesizes per-concept markdown wiki pages in ./wiki/
  3. Query   — TF-IDF search finds relevant wiki pages; Claude synthesizes an answer.

Setup:
  pip install anthropic scikit-learn
  set AWS_ACCESS_KEY_ID=your_access_key
  set AWS_SECRET_ACCESS_KEY=your_secret_key
  set AWS_REGION=us-east-1  (or whichever region has Bedrock enabled)

Usage:
  python wiki_system.py --ingest             # process papers (~2-10 min for 100 papers)
  python wiki_system.py --build              # synthesize wiki pages from extractions
  python wiki_system.py "your question"      # query the wiki
  python wiki_system.py -k 6 "your question" # use 6 wiki pages
"""

import json
import os
import re
import sys

import anthropic
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

# Bedrock cross-region inference profile ID — verify in AWS Console > Bedrock > Model access
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "us.anthropic.claude-sonnet-4-6-20251031-v1:0")
PAPERS_PATH = "physics_papers.json"
TEXTS_PATH = "paper_texts.json"
EXTRACTIONS_PATH = "wiki_extractions.json"
WIKI_DIR = "wiki"
DEFAULT_K = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bedrock_client() -> anthropic.AnthropicBedrock:
    if not hasattr(_bedrock_client, "_instance"):
        try:
            _bedrock_client._instance = anthropic.AnthropicBedrock(
                aws_access_key=os.environ["AWS_ACCESS_KEY_ID"],
                aws_secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                aws_region=os.getenv("AWS_REGION", "us-east-1"),
            )
        except KeyError as e:
            sys.exit(f"Missing AWS credential env var: {e}")
    return _bedrock_client._instance


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _wiki_page_path(concept: str) -> str:
    return os.path.join(WIKI_DIR, f"{_slugify(concept)}.md")


# ---------------------------------------------------------------------------
# Phase 1 — Ingest
# ---------------------------------------------------------------------------

def ingest(papers_path: str = PAPERS_PATH, texts_path: str = TEXTS_PATH) -> None:
    if not os.path.exists(papers_path):
        sys.exit(f"Error: {papers_path} not found. Run PhysicsScript.py first.")

    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)

    full_texts: dict = {}
    if os.path.exists(texts_path):
        with open(texts_path, encoding="utf-8") as f:
            full_texts = json.load(f)
        print(f"Full text available for {len(full_texts)}/{len(papers)} papers.")
    else:
        print(f"No {texts_path} found — using abstracts only. Run: python PhysicsScript.py --download")

    # Load existing extractions for resume support
    extractions: dict = {}
    if os.path.exists(EXTRACTIONS_PATH):
        with open(EXTRACTIONS_PATH, encoding="utf-8") as f:
            extractions = json.load(f)
        print(f"Resuming — {len(extractions)} papers already processed.")

    new_count = 0
    for i, paper in enumerate(papers):
        arxiv_id = paper["arxiv_id"]
        if arxiv_id in extractions:
            print(f"[{i+1}/{len(papers)}] Skip {arxiv_id} (done)")
            continue

        source = "full text" if arxiv_id in full_texts else "abstract"
        print(f"[{i+1}/{len(papers)}] ({source}) {paper['title'][:60]}...")
        concepts = _extract_concepts(paper, full_texts.get(arxiv_id))
        extractions[arxiv_id] = {
            "title": paper["title"],
            "url": paper["url"],
            "concepts": concepts,
        }
        new_count += 1

        # Save after every paper so progress survives interruption
        with open(EXTRACTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(extractions, f, indent=2, ensure_ascii=False)

    print(f"\nIngest complete. {new_count} new papers processed. Total: {len(extractions)}.")
    print("Next step: python wiki_system.py --build")


def _extract_concepts(paper: dict, full_text: str | None = None) -> list[dict]:
    if full_text:
        body = full_text
        content_label = "Full paper text"
    else:
        body = paper["abstract"]
        content_label = "Abstract"

    prompt = (
        f'Extract the key physics concepts from this paper and describe what it contributes to each.\n\n'
        f'Title: {paper["title"]}\n'
        f'{content_label}:\n{body}\n\n'
        'Return ONLY valid JSON (no other text):\n'
        '{\n'
        '  "concepts": [\n'
        '    {\n'
        '      "name": "Concept Name (standard physics terminology)",\n'
        '      "contribution": "2-3 sentences on what this paper adds to this concept."\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        'Include 2-4 concepts.'
    )
    try:
        response = _bedrock_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = re.sub(r"```(?:json)?\s*|\s*```", "", response.content[0].text).strip()
        data = json.loads(text)
        return data.get("concepts", [])
    except Exception as e:
        print(f"  Warning: extraction failed ({e}). Skipping concepts for this paper.")
        return []


# ---------------------------------------------------------------------------
# Phase 2 — Build wiki pages
# ---------------------------------------------------------------------------

def build_wiki() -> None:
    if not os.path.exists(EXTRACTIONS_PATH):
        sys.exit("Run --ingest first.")

    with open(EXTRACTIONS_PATH, encoding="utf-8") as f:
        extractions = json.load(f)

    # Group paper contributions by concept name
    concept_papers: dict[str, list[dict]] = {}
    for arxiv_id, data in extractions.items():
        for concept in data.get("concepts", []):
            name = concept["name"]
            if name not in concept_papers:
                concept_papers[name] = []
            concept_papers[name].append({
                "title": data["title"],
                "url": data["url"],
                "contribution": concept["contribution"],
            })

    os.makedirs(WIKI_DIR, exist_ok=True)
    wiki_index: dict = {}

    for concept, papers in sorted(concept_papers.items()):
        print(f"Building: {concept} ({len(papers)} papers)...")
        content = _synthesize_page(concept, papers)
        page_path = _wiki_page_path(concept)
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(content)
        wiki_index[concept] = {"path": page_path, "paper_count": len(papers)}

    index_path = os.path.join(WIKI_DIR, "_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(wiki_index, f, indent=2)

    print(f"\nWiki built: {len(wiki_index)} concept pages in ./{WIKI_DIR}/")
    print("Query with: python wiki_system.py \"your question\"")


def _synthesize_page(concept: str, papers: list[dict]) -> str:
    contributions_text = "\n\n".join(
        f"Paper: {p['title']}\nURL: {p['url']}\nContribution: {p['contribution']}"
        for p in papers
    )

    if len(papers) == 1:
        p = papers[0]
        return (
            f"# {concept}\n\n"
            f"## Overview\n{p['contribution']}\n\n"
            f"## Papers\n- [{p['title']}]({p['url']})\n"
        )

    prompt = (
        f'Write a wiki page about "{concept}" in physics by synthesizing these paper contributions.\n\n'
        f"{contributions_text}\n\n"
        f"Write a markdown page with exactly these sections:\n"
        f"# {concept}\n\n"
        f"## Overview\n"
        f"(2-3 sentence summary of this concept)\n\n"
        f"## Key Findings from Literature\n"
        f"(Synthesized bullet points from the papers — focus on trends and insights)\n\n"
        f"## Related Concepts\n"
        f"(3-5 related physics concepts as a bullet list)\n\n"
        f"## Papers\n"
        f"(List each paper title and URL with a one-line description)"
    )

    try:
        response = _bedrock_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"  Warning: synthesis failed for {concept} ({e}). Writing raw notes.")
        return f"# {concept}\n\n" + contributions_text


# ---------------------------------------------------------------------------
# Phase 3 — Query
# ---------------------------------------------------------------------------

def _load_wiki() -> tuple[dict, dict]:
    index_path = os.path.join(WIKI_DIR, "_index.json")
    if not os.path.exists(index_path):
        return {}, {}

    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    pages = {}
    for concept, meta in index.items():
        path = meta["path"]
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                pages[concept] = f.read()

    return index, pages


def query(question: str, k: int = DEFAULT_K) -> dict:
    _, pages = _load_wiki()
    if not pages:
        sys.exit(
            "Wiki not found. Run:\n"
            "  python wiki_system.py --ingest\n"
            "  python wiki_system.py --build"
        )

    concepts = list(pages.keys())
    texts = [pages[c] for c in concepts]

    # TF-IDF search over wiki pages
    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(texts)
    q_vec = vectorizer.transform([question])
    scores = cosine_similarity(q_vec, tfidf_matrix).flatten()

    top_indices = scores.argsort()[::-1][:k]
    relevant = [
        (concepts[i], pages[concepts[i]], float(scores[i]))
        for i in top_indices
        if scores[i] > 0
    ]

    if not relevant:
        return {"answer": "No relevant wiki pages found for this question.", "pages": []}

    wiki_context = "\n\n===\n\n".join(
        f"WIKI PAGE: {concept}\n\n{content}"
        for concept, content, _ in relevant
    )

    prompt = (
        "You are a physics research assistant with access to a curated wiki built from ArXiv papers. "
        "Use the wiki pages below to answer the question. "
        "Synthesize insights across pages and cite specific concepts.\n\n"
        f"{wiki_context}\n\n"
        f"Question: {question}"
    )

    print(f"[Wiki] Querying {CLAUDE_MODEL} with {len(relevant)} wiki pages...")
    try:
        response = _bedrock_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        sys.exit(f"Bedrock API error: {e}")

    return {
        "answer": response.content[0].text,
        "pages": [{"concept": c, "score": s} for c, _, s in relevant],
    }


def _print_result(result: dict, k: int) -> None:
    print(f"\n{'='*60}")
    print("WIKI ANSWER")
    print("="*60)
    print(result["answer"])
    print(f"\n{'='*60}")
    print(f"WIKI PAGES CONSULTED (top {k})")
    print("="*60)
    for p in result["pages"]:
        print(f"  [{p['score']:.3f}] {p['concept']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    if args[0] == "--ingest":
        ingest()
    elif args[0] == "--build":
        build_wiki()
    else:
        k = DEFAULT_K
        if "-k" in args:
            idx = args.index("-k")
            k = int(args[idx + 1])
            args = args[:idx] + args[idx + 2:]
        question = " ".join(args)
        result = query(question, k=k)
        _print_result(result, k)
