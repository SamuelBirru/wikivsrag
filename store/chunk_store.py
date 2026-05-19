"""
Persistent storage for structured Chunk objects.

Wraps the existing rag_chunks.pkl format so rag_system.py works unchanged
while also carrying the richer Phase 1 metadata fields.
"""

import os
import pickle

from ingest.parse_latex import Chunk


class ChunkStore:
    def __init__(self, chunks: list | None = None):
        # Each item is either a Chunk (from LaTeX parser) or the legacy
        # {text, meta} dict (from PDF/abstract fallback).
        self.chunks: list = chunks or []

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        with open(path, "wb") as fh:
            pickle.dump(self.chunks, fh)

    @classmethod
    def load(cls, path: str) -> "ChunkStore":
        with open(path, "rb") as fh:
            return cls(pickle.load(fh))

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def to_rag_list(self) -> list[dict]:
        """
        Return a list of {text, meta} dicts compatible with rag_system.py.
        Chunk objects are converted via to_rag_dict(); legacy dicts pass through.
        """
        result = []
        for item in self.chunks:
            if isinstance(item, Chunk):
                result.append(item.to_rag_dict())
            else:
                result.append(item)
        return result

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.chunks)

    def by_type(self, chunk_type: str) -> list:
        out = []
        for item in self.chunks:
            if isinstance(item, Chunk):
                if item.chunk_type == chunk_type:
                    out.append(item)
            elif item.get("meta", {}).get("chunk_type") == chunk_type:
                out.append(item)
        return out

    def stats(self) -> dict:
        counts: dict[str, int] = {}
        for item in self.chunks:
            t = item.chunk_type if isinstance(item, Chunk) else item.get("meta", {}).get("chunk_type", "legacy")
            counts[t] = counts.get(t, 0) + 1
        return counts
