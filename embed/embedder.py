"""
Thin wrapper around SentenceTransformer to make the model swappable.

Current: allenai/specter2_base  (768-dim, trained on scientific papers)

To swap model: change DEFAULT_MODEL, delete rag_index.faiss + rag_chunks.pkl,
and re-run --ingest-sources.  The FAISS index dimension is set automatically.
"""

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "allenai/specter2_base"


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = SentenceTransformer(model_name, device=device)
        print(f"[Embedder] {model_name} loaded on {device.upper()}")

    @property
    def dimension(self) -> int:
        # Handle sentence-transformers API rename across versions
        getter = getattr(self._model, "get_embedding_dimension", None) \
              or getattr(self._model, "get_sentence_embedding_dimension")
        return getter()

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
