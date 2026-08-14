import sqlite3
import json
import math
import sys
import re
import urllib.request
from array import array

SEMANTIC_DB = "data/semantic.db"
VAULT_DB = "data/vault.db"
MODEL = "qwen3-embedding:4b"

args = sys.argv[1:]

if not args:
    raise SystemExit(
        'Usage: python3 semantic_search.py "your query" [--all]'
    )

include_all = "--all" in args
args = [a for a in args if a != "--all"]

query = " ".join(args).strip()
terms = re.findall(r"[A-Za-z0-9_-]+", query.lower())


def embed(text):
    payload = json.dumps({
        "model": MODEL,
        "input": text
    }).encode("utf-8")

    request = urllib.request.Request(
        "http://localhost:11434/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.load(response)

    return data["embeddings"][0]


def decode_vector(blob):
    values = array("f")
    values.frombytes(blob)
    return values


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a == 0 or mag_b == 0:
        return 0

    return dot / (mag_a * mag_b)


def snippet(text, limit=280):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def is_background(path):
    p = path.lower()

    return (
        "archive/monday archive/monday/" in p
        or p.startswith("copilot/")
    )



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

    return "OTHER"


# Load tags + links from structural database
vault = sqlite3.connect(VAULT_DB)

metadata = {}

for path, title, tags, links in vault.execute("""
    SELECT
        n.path,
        n.title,
        COALESCE(GROUP_CONCAT(DISTINCT t.tag), ''),
        COALESCE(GROUP_CONCAT(DISTINCT l.target), '')
    FROM notes n
    LEFT JOIN note_tags t ON t.note_id = n.id
    LEFT JOIN links l ON l.note_id = n.id
    GROUP BY n.id
"""):
    metadata[path] = {
        "title": title.lower(),
        "tags": tags.lower(),
        "links": links.lower(),
    }

vault.close()


query_vector = embed(query)

db = sqlite3.connect(SEMANTIC_DB)

rows = db.execute("""
    SELECT path, title, chunk_index, content, embedding
    FROM chunks
""").fetchall()

results = []

for path, title, chunk_index, content, blob in rows:

    if not include_all and is_background(path):
        continue

    semantic_score = cosine(
        query_vector,
        decode_vector(blob)
    )

    meta = metadata.get(path, {})
    title_l = meta.get("title", title.lower())
    tags_l = meta.get("tags", "")
    links_l = meta.get("links", "")
    content_l = content.lower()

    lexical_bonus = 0

    for term in terms:
        if term in title_l:
            lexical_bonus += 0.08

        if term in tags_l:
            lexical_bonus += 0.06

        if term in links_l:
            lexical_bonus += 0.04

        if term in content_l:
            lexical_bonus += 0.015

    final_score = semantic_score + lexical_bonus

    results.append({
        "score": final_score,
        "semantic": semantic_score,
        "path": path,
        "title": title,
        "chunk": chunk_index,
        "content": content,
    })


results.sort(
    key=lambda x: x["score"],
    reverse=True
)


# Strongest chunk per note
seen = set()
notes = []

for result in results:
    if result["path"] in seen:
        continue

    seen.add(result["path"])
    notes.append(result)

    if len(notes) >= 12:
        break


print(f'\nHybrid search: "{query}"')
print(
    "Scope:",
    "entire corpus" if include_all
    else "default knowledge corpus"
)
print()

for i, result in enumerate(notes, 1):
    kind = source_type(result["path"])

    print(
        f"{i:2}. {result['score']:.4f} "
        f"(semantic {result['semantic']:.4f}) "
        f"[{kind}] {result['title']}"
    )
    print(f"    {result['path']}")
    print(f"    {snippet(result['content'])}")
    print()

db.close()
