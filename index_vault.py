from pathlib import Path
from datetime import datetime
import sqlite3
import re

from config import VAULT, is_index_excluded
DB = Path("data/vault.db")

def is_knowledge_note(path):
    relative = path.relative_to(VAULT)
    return not is_index_excluded(relative)

def extract_frontmatter_tags(text):
    tags = []

    if not text.startswith("---"):
        return tags

    parts = text.split("---", 2)
    if len(parts) < 3:
        return tags

    lines = parts[1].splitlines()
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

def parse_note(path, vault=VAULT):
    text = path.read_text(encoding="utf-8")
    stat = path.stat()

    frontmatter_tags = extract_frontmatter_tags(text)

    inline_tags = re.findall(
        r"(?<!\w)#([A-Za-z0-9_-]+)",
        text
    )

    tags = sorted(set(frontmatter_tags + inline_tags))

    links = sorted(set(
        match.split("|")[0].split("#")[0].strip()
        for match in re.findall(r"\[\[([^\]]+)\]\]", text)
        if match.strip()
    ))

    headings = re.findall(
        r"^#{1,6}\s+(.+)$",
        text,
        flags=re.MULTILINE
    )

    plain_text = re.sub(r"\[\[|\]\]|[#*_>`~-]", " ", text)
    word_count = len(re.findall(r"\b\w+\b", plain_text))

    relative = path.relative_to(vault)

    return {
        "path": str(relative),
        "folder": str(relative.parent),
        "title": path.stem,
        "content": text,
        "word_count": word_count,
        "modified": datetime.fromtimestamp(
            stat.st_mtime
        ).isoformat(timespec="seconds"),
        "modified_ns": stat.st_mtime_ns,
        "created": (
            datetime.fromtimestamp(stat.st_birthtime).isoformat(
                timespec="seconds"
            )
            if hasattr(stat, "st_birthtime")
            else None
        ),
        "size": stat.st_size,
        "tags": tags,
        "links": links,
        "headings": headings,
    }

def create_database(conn):
    conn.executescript("""
        DROP TABLE IF EXISTS note_tags;
        DROP TABLE IF EXISTS links;
        DROP TABLE IF EXISTS headings;
        DROP TABLE IF EXISTS notes;

        CREATE TABLE notes (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            folder TEXT,
            title TEXT,
            content TEXT,
            word_count INTEGER,
            modified TEXT,
            modified_ns INTEGER,
            created TEXT,
            size INTEGER
        );

        CREATE TABLE note_tags (
            note_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY(note_id) REFERENCES notes(id)
        );

        CREATE TABLE links (
            note_id INTEGER NOT NULL,
            target TEXT NOT NULL,
            FOREIGN KEY(note_id) REFERENCES notes(id)
        );

        CREATE TABLE headings (
            note_id INTEGER NOT NULL,
            heading TEXT NOT NULL,
            FOREIGN KEY(note_id) REFERENCES notes(id)
        );

        CREATE INDEX idx_tags_tag ON note_tags(tag);
        CREATE INDEX idx_links_target ON links(target);
        CREATE INDEX idx_notes_title ON notes(title);
    """)

def main():
    DB.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(
        path for path in VAULT.rglob("*.md")
        if is_knowledge_note(path)
    )

    conn = sqlite3.connect(DB)
    create_database(conn)

    errors = []

    for path in files:
        try:
            note = parse_note(path)

            cursor = conn.execute("""
                INSERT INTO notes
                (
                    path, folder, title, content, word_count, modified,
                    modified_ns, created, size
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                note["path"],
                note["folder"],
                note["title"],
                note["content"],
                note["word_count"],
                note["modified"],
                note["modified_ns"],
                note["created"],
                note["size"],
            ))

            note_id = cursor.lastrowid

            conn.executemany(
                "INSERT INTO note_tags (note_id, tag) VALUES (?, ?)",
                [(note_id, tag) for tag in note["tags"]]
            )

            conn.executemany(
                "INSERT INTO links (note_id, target) VALUES (?, ?)",
                [(note_id, link) for link in note["links"]]
            )

            conn.executemany(
                "INSERT INTO headings (note_id, heading) VALUES (?, ?)",
                [(note_id, heading) for heading in note["headings"]]
            )

        except Exception as exc:
            errors.append((path, str(exc)))

    conn.commit()

    note_count = conn.execute(
        "SELECT COUNT(*) FROM notes"
    ).fetchone()[0]

    tag_count = conn.execute(
        "SELECT COUNT(*) FROM note_tags"
    ).fetchone()[0]

    unique_tags = conn.execute(
        "SELECT COUNT(DISTINCT tag) FROM note_tags"
    ).fetchone()[0]

    link_count = conn.execute(
        "SELECT COUNT(*) FROM links"
    ).fetchone()[0]

    conn.close()

    print(f"Database: {DB.resolve()}")
    print(f"Notes indexed: {note_count}")
    print(f"Tag assignments: {tag_count}")
    print(f"Unique tags: {unique_tags}")
    print(f"Wikilinks: {link_count}")
    print(f"Errors: {len(errors)}")

    if errors:
        print("\nFirst errors:")
        for path, error in errors[:10]:
            print(f"  {path}: {error}")

    print("\nINDEX COMPLETE")

if __name__ == "__main__":
    main()
