"""M0 spike helper: dump raw postmortem sources (HTML / PDF / MD) to plain text.

Deliberately crude. The point of the spike is to see what the raw formats look
like before any real parser exists (M2), so this does the minimum: strip
script/style/nav, keep block structure as newlines, and report how much of the
file was chrome vs. content.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

RAW = Path(__file__).parent / "raw"
OUT = Path(__file__).parent / "text"
OUT.mkdir(exist_ok=True)

CHROME_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "svg", "iframe", "form", "aside"]


def html_to_text(path: Path) -> tuple[str, dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(CHROME_TAGS):
        t.decompose()
    # v1 took soup.find("article") and silently returned an author-bio card on
    # github.blog and a related-post card on slack.engineering (0 and 124 chars
    # of text). v2: consider every <article>/<main> and keep the longest.
    candidates = soup.find_all(["article", "main"]) or [soup.body or soup]
    node = max(candidates, key=lambda n: len(n.get_text()))
    text = node.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    stats = {
        "raw_bytes": len(raw),
        "text_chars": len(text),
        "chrome_ratio": round(1 - len(text) / max(len(raw), 1), 3),
        "container": node.name,
    }
    return text, stats


def pdf_to_text(path: Path) -> tuple[str, dict]:
    reader = PdfReader(str(path))
    pages = [p.extract_text() or "" for p in reader.pages]
    text = "\n\n".join(pages)
    stats = {"raw_bytes": path.stat().st_size, "text_chars": len(text), "pages": len(pages)}
    return text, stats


def main() -> None:
    for src in sorted(RAW.iterdir()):
        if src.name.startswith("danluu"):
            continue
        if src.suffix == ".html":
            text, stats = html_to_text(src)
        elif src.suffix == ".pdf":
            text, stats = pdf_to_text(src)
        elif src.suffix == ".md":
            text = src.read_text(encoding="utf-8", errors="replace")
            stats = {"raw_bytes": len(text), "text_chars": len(text)}
        else:
            continue
        (OUT / (src.stem + ".txt")).write_text(text, encoding="utf-8")
        print(f"{src.name:40s} {stats}")


if __name__ == "__main__":
    sys.exit(main())
