from pathlib import Path
from datetime import datetime
import re

from config import VAULT

NOTE = VAULT / "🍱PARA/🪐 Areas/Wardrobe/Personal Style Manifesto.md"

def extract_frontmatter_tags(text):
    tags = []

    if not text.startswith("---"):
        return tags

    parts = text.split("---", 2)
    if len(parts) < 3:
        return tags

    frontmatter = parts[1]
    lines = frontmatter.splitlines()

    inside_tags = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("tags:"):
            inside_tags = True

            inline = stripped[5:].strip()
            if inline:
                inline = inline.strip("[]")
                for tag in inline.split(","):
                    tag = tag.strip().strip('"').strip("'").lstrip("#")
                    if tag:
                        tags.append(tag)
            continue

        if inside_tags:
            if stripped.startswith("- "):
                tag = stripped[2:].strip().strip('"').strip("'").lstrip("#")
                if tag:
                    tags.append(tag)
            elif stripped:
                break

    return tags

def inspect_note(path):
    text = path.read_text(encoding="utf-8")

    frontmatter_tags = extract_frontmatter_tags(text)

    inline_tags = re.findall(
        r"(?<!\w)#([A-Za-z0-9_-]+)",
        text
    )

    tags = sorted(set(frontmatter_tags + inline_tags))

    wikilinks = sorted(set(
        match.split("|")[0].strip()
        for match in re.findall(r"\[\[([^\]]+)\]\]", text)
    ))

    headings = re.findall(
        r"^#{1,6}\s+(.+)$",
        text,
        flags=re.MULTILINE
    )

    plain_text = re.sub(r"\[\[|\]\]|[#*_>`~-]", " ", text)
    word_count = len(re.findall(r"\b\w+\b", plain_text))

    stat = path.stat()

    print(f"Path: {path.relative_to(VAULT)}")
    print(f"Title: {path.stem}")
    print(f"Words: {word_count}")
    print(
        "Modified:",
        datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    )

    print("\nTags:")
    for tag in tags:
        print(f"  - #{tag}")

    print("\nWikilinks:")
    for link in wikilinks:
        print(f"  - [[{link}]]")

    print("\nHeadings:")
    for heading in headings:
        print(f"  - {heading}")

if __name__ == "__main__":
    inspect_note(NOTE)
