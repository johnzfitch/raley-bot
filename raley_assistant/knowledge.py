"""Knowledge base search for Raley assistant.

Searches clean markdown files in ~/.config/raley-assistant/knowledge/.
Books are installed there as cleaned .md files (HTML stripped).

Usage via MCP `knowledge` tool:
  knowledge q="pre-bolusing timing"
  knowledge q="lentil soup" book="type1-recipes"
"""

from __future__ import annotations

import re
from pathlib import Path

KNOWLEDGE_DIR = Path.home() / ".config" / "raley-assistant" / "knowledge"

_HEADING_RE = re.compile(r'^#{1,3} ', re.MULTILINE)


def _chunk_file(path: Path) -> list[tuple[str, str]]:
    """Split a markdown file into (heading, content) sections at # boundaries."""
    lines = path.read_text().split('\n')

    sections: list[tuple[str, str]] = []
    current_heading = path.stem
    current_lines: list[str] = []

    for line in lines:
        if _HEADING_RE.match(line):
            content = '\n'.join(current_lines).strip()
            if content:
                sections.append((current_heading, content))
            current_heading = line.lstrip('#').strip()
            current_lines = []
        else:
            current_lines.append(line)

    content = '\n'.join(current_lines).strip()
    if content:
        sections.append((current_heading, content))

    return sections


def search_knowledge(
    query: str,
    book: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Keyword search across knowledge base files.

    Args:
        query: Space-separated search terms
        book: Specific book filename stem (no extension), or None for all
        limit: Maximum results to return

    Returns list of {book, heading, snippet} dicts, ranked by keyword density.
    """
    if not KNOWLEDGE_DIR.exists():
        return []

    if book:
        target = (KNOWLEDGE_DIR / f"{book}.md").resolve()
        files = [target] if target.exists() and str(target).startswith(str(KNOWLEDGE_DIR.resolve())) else []
    else:
        files = sorted(KNOWLEDGE_DIR.glob("*.md"))

    if not files:
        return []

    keywords = [w.lower() for w in re.split(r'\W+', query) if len(w) > 2]
    if not keywords:
        return []

    scored: list[tuple[int, dict]] = []

    for f in files:
        try:
            chunks = _chunk_file(f)
        except OSError:
            continue

        for heading, content in chunks:
            combined = (heading + ' ' + content).lower()
            score = sum(combined.count(kw) for kw in keywords)
            if score == 0:
                continue

            # First 400 chars as snippet, break at word boundary
            snippet = content[:400]
            if len(content) > 400:
                cut = snippet.rfind(' ')
                snippet = snippet[:cut] + '...' if cut > 300 else snippet + '...'
            snippet = ' '.join(snippet.split())  # normalize whitespace

            scored.append((score, {
                "book": f.stem,
                "heading": heading,
                "snippet": snippet,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def list_books() -> list[dict]:
    """List installed knowledge base files."""
    if not KNOWLEDGE_DIR.exists():
        return []
    return [
        {"name": f.stem, "size_kb": f.stat().st_size // 1024}
        for f in sorted(KNOWLEDGE_DIR.glob("*.md"))
    ]


def clean_epub_markdown(text: str) -> str:
    """Strip HTML markup from EPUB-exported markdown.

    Removes HTML tags, image syntax, converts link syntax to plain text,
    and collapses excessive blank lines. Used when installing books.
    """
    # Remove inline images
    text = re.sub(r'!\[[^\]]*\]\([^\)]*\)', '', text)
    # Convert [text](url) links to plain text
    text = re.sub(r'\[([^\]]*)\]\([^\)]*\)', r'\1', text)
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip trailing whitespace from each line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    return text.strip()
