"""
Fast lookup index over chunk metadata, stored outside FAISS.

Supports:
  - Label lookup: "eq:hamiltonian" -> chunk index in ChunkStore
  - Type lookup:  "equation" -> [chunk indices]
  - Section lookup: "Methods > Hamiltonian" -> [chunk indices]

This is what makes queries like "equation 4" or "figure showing phase diagram"
resolve correctly without a semantic search.
"""

import json
import os

from ingest.parse_latex import Chunk


class MetadataIndex:
    def __init__(self):
        self._by_label: dict[str, int] = {}
        self._by_type: dict[str, list[int]] = {}
        self._by_section: dict[str, list[int]] = {}
        self._by_eq_number: dict[str, list[int]] = {}

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, chunks: list) -> None:
        """
        Build the index from a list of Chunk objects or legacy {text, meta} dicts.
        Call this after constructing a ChunkStore.
        """
        self._by_label = {}
        self._by_type = {}
        self._by_section = {}
        self._by_eq_number = {}

        for i, item in enumerate(chunks):
            if isinstance(item, Chunk):
                label = item.label
                ctype = item.chunk_type
                sec_path = item.section_path
                eq_num = item.equation_number
            else:
                m = item.get("meta", {})
                label = m.get("label")
                ctype = m.get("chunk_type", "legacy")
                sec_path = m.get("section_path", [])
                eq_num = m.get("equation_number")

            if label:
                self._by_label[label] = i

            self._by_type.setdefault(ctype, []).append(i)

            if sec_path:
                key = " > ".join(sec_path)
                self._by_section.setdefault(key, []).append(i)

            if eq_num:
                self._by_eq_number.setdefault(str(eq_num), []).append(i)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup_label(self, label: str) -> int | None:
        return self._by_label.get(label)

    def lookup_equation_number(self, num: str) -> list[int]:
        return self._by_eq_number.get(str(num), [])

    def lookup_type(self, chunk_type: str) -> list[int]:
        return self._by_type.get(chunk_type, [])

    def lookup_section(self, section_key: str) -> list[int]:
        return self._by_section.get(section_key, [])

    def try_direct_lookup(self, query: str) -> int | None:
        """
        Check if a query looks like a label or equation number reference and
        return the chunk index if found, otherwise None.

        Examples that resolve directly:
          "equation 4"  ->  eq_number lookup
          "eq:hamiltonian"  ->  label lookup
          "figure 2"  ->  label "fig:2" or similar (best-effort)
        """
        q = query.strip().lower()

        # "equation N" or "eq. N" — only resolve if unambiguous (one paper has it)
        m_eq = __import__("re").match(r"(?:equation|eq\.?)\s+(\d+)", q)
        if m_eq:
            matches = self.lookup_equation_number(m_eq.group(1))
            if len(matches) == 1:
                return matches[0]

        # Raw label pattern like eq:..., fig:..., tab:...
        if ":" in q and not q.startswith("http"):
            return self.lookup_label(query.strip())

        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        data = {
            "by_label": self._by_label,
            "by_type": self._by_type,
            "by_section": self._by_section,
            "by_eq_number": self._by_eq_number,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "MetadataIndex":
        idx = cls()
        if not os.path.exists(path):
            return idx
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        idx._by_label = data.get("by_label", {})
        idx._by_type = data.get("by_type", {})
        idx._by_section = data.get("by_section", {})
        # Migrate legacy format where eq numbers were stored as ints, not lists
        raw_eq = data.get("by_eq_number", {})
        idx._by_eq_number = {
            k: v if isinstance(v, list) else [v]
            for k, v in raw_eq.items()
        }
        return idx
