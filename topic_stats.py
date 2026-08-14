import sqlite3
import sys
import re
from collections import Counter

DB = "data/vault.db"

args = sys.argv[1:]

if not args:
    raise SystemExit(
        'Usage: python3 topic_stats.py "topic" [--all]'
    )

include_all = "--all" in args
args = [a for a in args if a != "--all"]

query = " ".join(args).strip().lower()


def compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


query_compact = compact(query)

db = sqlite3.connect(DB)


def source_type(path):
    p = path.lower()

    if p.startswith("wiki/"):
        return "THINKING"
    if "projects/" in p:
        return "PROJECT"
    if "areas/" in p:
        return "AREA"
    if "resources/" in p:
        return "REFERENCE"
    if p.startswith("inbox/"):
        return "CAPTURE"
    if "archive/" in p:
        return "ARCHIVE"
    if p.startswith("copilot/"):
        return "COPILOT"

    return "OTHER"


def excluded_by_default(path):
    p = path.lower()

    return (
        "archive/monday archive/monday/" in p
        or "/meetings/" in p
        or p.startswith("copilot/")
    )


notes = db.execute("""
    SELECT id, path, title, content
    FROM notes
""").fetchall()

matched = []

for note_id, path, title, content in notes:

    if not include_all and excluded_by_default(path):
        continue

    tags = [
        row[0]
        for row in db.execute(
            "SELECT tag FROM note_tags WHERE note_id = ?",
            (note_id,)
        )
    ]

    links = [
        row[0]
        for row in db.execute(
            "SELECT target FROM links WHERE note_id = ?",
            (note_id,)
        )
    ]

    title_l = title.lower()
    content_l = content.lower()

    title_match = query in title_l
    content_match = query in content_l

    tag_match = any(
        compact(tag) == query_compact
        for tag in tags
    )

    link_match = any(
        compact(link.split("/")[-1]) == query_compact
        for link in links
    )

    if not (
        title_match
        or content_match
        or tag_match
        or link_match
    ):
        continue

    reasons = []

    if title_match:
        reasons.append("title")
    if tag_match:
        reasons.append("tag")
    if link_match:
        reasons.append("link")
    if content_match:
        reasons.append("content")

    matched.append({
        "id": note_id,
        "path": path,
        "title": title,
        "tags": tags,
        "links": links,
        "type": source_type(path),
        "reasons": reasons,
    })


types = Counter()
tags = Counter()
links = Counter()
reasons = Counter()

for note in matched:
    types[note["type"]] += 1

    for reason in note["reasons"]:
        reasons[reason] += 1

    for tag in note["tags"]:
        if compact(tag) != query_compact:
            tags[tag] += 1

    for link in note["links"]:
        if compact(link.split("/")[-1]) != query_compact:
            links[link] += 1


print()
print(f'TOPIC STATS — "{query}"')
print("=" * 40)

print(
    "Scope:",
    "entire indexed vault" if include_all
    else "default knowledge corpus"
)

print(f"\nDirect topic notes: {len(matched)}")

print("\nMATCH SIGNALS")
for reason, count in reasons.most_common():
    print(f"{count:4}  {reason.upper()}")

print("\nBY SOURCE TYPE")
for kind, count in types.most_common():
    print(f"{count:4}  {kind}")

print("\nTOP RELATED TAGS")
for tag, count in tags.most_common(12):
    print(f"{count:4}  #{tag}")

print("\nTOP RELATED LINKS")
for link, count in links.most_common(12):
    print(f"{count:4}  [[{link}]]")

print("\nMATCHING NOTES")
for note in matched[:15]:
    reason = "/".join(note["reasons"]).upper()
    print(
        f"- [{note['type']}] "
        f"{note['title']} — {reason}"
    )

if len(matched) > 15:
    print(f"...and {len(matched) - 15} more.")

db.close()
