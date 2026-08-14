import sqlite3
import sys
import re

DB = "data/vault.db"

args = sys.argv[1:]

if not args:
    raise SystemExit(
        'Usage: python3 search_vault.py "search terms" [--all]'
    )

include_all = "--all" in args
args = [a for a in args if a != "--all"]

query = " ".join(args).strip()
query_lower = query.lower()

terms = re.findall(r"[A-Za-z0-9_-]+", query_lower)

db = sqlite3.connect(DB)

rows = db.execute("""
    SELECT
        n.path,
        n.title,
        n.content,
        COALESCE(GROUP_CONCAT(DISTINCT t.tag), '') AS tags,
        COALESCE(GROUP_CONCAT(DISTINCT l.target), '') AS links
    FROM notes n
    LEFT JOIN note_tags t ON t.note_id = n.id
    LEFT JOIN links l ON l.note_id = n.id
    GROUP BY n.id
""").fetchall()

results = []

def is_background_material(path):
    lower = path.lower()

    return (
        "/meetings/" in lower
        or lower.startswith("copilot/")
    )

def make_snippet(content):
    clean = re.sub(r"\s+", " ", content).strip()
    lower = clean.lower()

    position = lower.find(query_lower)

    if position == -1:
        for term in terms:
            position = lower.find(term)
            if position != -1:
                break

    if position == -1:
        return clean[:180]

    start = max(0, position - 70)
    end = min(len(clean), position + 180)

    snippet = clean[start:end]

    if start > 0:
        snippet = "…" + snippet

    if end < len(clean):
        snippet += "…"

    return snippet

for path, title, content, tags, links in rows:

    if not include_all and is_background_material(path):
        continue

    title_l = title.lower()
    content_l = content.lower()
    tags_l = tags.lower()
    links_l = links.lower()

    combined = " ".join([
        title_l,
        content_l,
        tags_l,
        links_l,
    ])

    exact_phrase = query_lower in combined
    all_terms = all(term in combined for term in terms)

    if not exact_phrase and not all_terms:
        continue

    score = 0
    match_types = []

    if query_lower in title_l:
        score += 100
        match_types.append("title")

    if query_lower in tags_l:
        score += 70
        match_types.append("tag")

    if query_lower in links_l:
        score += 60
        match_types.append("link")

    if query_lower in content_l:
        score += 40
        match_types.append("content")

    # Reward multi-word matches even when words appear separately.
    for term in terms:
        if term in title_l:
            score += 15
        if term in tags_l:
            score += 10
        if term in links_l:
            score += 8
        if term in content_l:
            score += 3

    results.append({
        "score": score,
        "path": path,
        "title": title,
        "types": match_types or ["mixed"],
        "snippet": make_snippet(content),
    })

results.sort(
    key=lambda r: (-r["score"], r["title"].lower())
)

print(f'\nSearch: "{query}"')
print(f"Relevant notes: {len(results)}")
print(
    "Scope:",
    "entire indexed vault" if include_all
    else "knowledge notes (meetings/Copilot excluded)"
)
print()

for result in results[:20]:
    kind = "/".join(result["types"]).upper()

    print(f"[{kind}] {result['title']}")
    print(f"  {result['path']}")
    print(f"  {result['snippet']}")
    print()

if len(results) > 20:
    print(f"...and {len(results) - 20} more.")

db.close()
