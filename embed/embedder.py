"""
Thin wrapper around SentenceTransformer to make the model swappable.

Phase 1: all-MiniLM-L6-v2  (384-dim, already installed, no changes)
Phase 2: allenai/specter2_base  (768-dim, scientific domain, pip install)

To swap: change DEFAULT_MODEL below, delete rag_index.faiss + rag_chunks.pkl,
and re-run --ingest-sources.  The FAISS index dimension is set automatically
from the first batch of embeddings.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "all-MiniLM-L6-v2"
# Phase 2 swap: DEFAULT_MODEL = "allenai/specter2_base"


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def encode(
        self,
        texts: list[str],
        show_progress_bar: bool = False,
        batch_size: int = 64,
    ) -> np.ndarray:
        """Return L2-normalised float32 embeddings, shape (len(texts), dim)."""
        import faiss

        vecs = self._model.encode(
            texts,
            show_progress_bar=show_progress_bar,
            batch_size=batch_size,
            convert_to_numpy=True,
        ).astype("float32")
        faiss.normalize_L2(vecs)
        return vecs
