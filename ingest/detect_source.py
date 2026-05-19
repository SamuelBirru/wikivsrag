
"""
Identifies the main .tex entry point within an extracted paper directory.
"""

import os


def find_main_tex(tex_files: list[str]) -> str:
    """
    Given a list of .tex file paths, return the one most likely to be the
    document entry point.

    Detection order:
      1. File containing \\begin{document}  (definitive signal)
      2. If multiple claim \\begin{document}, prefer the shallowest / largest
      3. Common filename heuristics: main.tex, paper.tex, manuscript.tex, ...
      4. Largest file by byte size (last resort)
    """
    if not tex_files:
        raise ValueError("No .tex files provided")

    if len(tex_files) == 1:
        return tex_files[0]

    # Priority 1: contains \begin{document}
    candidates = []
    for path in tex_files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                if "\\begin{document}" in fh.read():
                    candidates.append(path)
        except OSError:
            pass

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        # Prefer the shallowest file (fewest path separators), then largest
        candidates.sort(key=lambda p: (p.count(os.sep), -os.path.getsize(p)))
        return candidates[0]

    # Priority 2: filename heuristics
    preferred = [
        "main.tex", "paper.tex", "manuscript.tex",
        "article.tex", "thesis.tex", "preprint.tex",
    ]
    for name in preferred:
        for path in tex_files:
            if os.path.basename(path).lower() == name:
                return path

    # Priority 3: largest file by byte size
    return max(tex_files, key=os.path.getsize)
