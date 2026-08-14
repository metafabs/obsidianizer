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

if len(sys.argv) < 2:
    raise SystemExit(
        'Usage: python3 did_i_write.py "topic"'
    )

query = " ".join(sys.argv[1:]).strip()
terms = re.findall(r"[A-Za-z0-9_-]+", query.lower())


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


def is_authored_layer(kind):
    return kind in {
        "THINKING",
        "PROJECT",
        "AREA",
        "CAPTURE",
    }


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
        return json.load(response)["embeddings"][0]


def decode(blob):
    vector = array("f")
    vector.frombytes(blob)
    return vector


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(x * x for x in b))

    if not ma or not mb:
        return 0

    return dot / (ma * mb)


vault = sqlite3.connect(VAULT_DB)

metadata = {}

for path, title, content, tags, links in vault.execute("""
    SELECT
        n.path,
        n.title,
        n.content,
        COALESCE(GROUP_CONCAT(DISTINCT t.tag), ''),
        COALESCE(GROUP_CONCAT(DISTINCT l.target), '')
    FROM notes n
    LEFT JOIN note_tags t ON t.note_id = n.id
    LEFT JOIN links l ON l.note_id = n.id
    GROUP BY n.id
"""):
    metadata[path] = {
        "title": title,
        "content": content,
        "tags": tags,
        "links": links,
    }

vault.close()


query_vector = embed(query)

semantic = sqlite3.connect(SEMANTIC_DB)

rows = semantic.execute("""
    SELECT path, title, content, embedding
    FROM chunks
""").fetchall()

semantic.close()


best_by_note = {}

for path, title, chunk, blob in rows:
    kind = source_type(path)

    if kind == "ARCHIVE":
        continue

    score = cosine(query_vector, decode(blob))

    if (
        path not in best_by_note
        or score > best_by_note[path]["score"]
    ):
        best_by_note[path] = {
            "path": path,
            "title": title,
            "kind": kind,
            "score": score,
        }


direct = []
related_authored = []
references = []

for path, result in best_by_note.items():
    meta = metadata[path]

    combined = " ".join([
        meta["title"],
        meta["content"],
        meta["tags"],
        meta["links"],
    ]).lower()

    direct_match = all(
        term in combined
        for term in terms
    )

    if direct_match and is_authored_layer(result["kind"]):
        direct.append(result)

    elif result["score"] >= 0.56 and is_authored_layer(result["kind"]):
        related_authored.append(result)

    elif result["score"] >= 0.56 and result["kind"] == "REFERENCE":
        references.append(result)


direct.sort(key=lambda x: x["score"], reverse=True)
related_authored.sort(key=lambda x: x["score"], reverse=True)
references.sort(key=lambda x: x["score"], reverse=True)


print()
print(f'Did I write about: "{query}"?')
print()

if direct:
    print("YES — direct evidence exists in your own notes.")
else:
    print("NO DIRECT MATCH in your own notes.")

if related_authored:
    print(
        f"But I found {len(related_authored)} "
        "semantically related notes in your thinking/work."
    )

if references:
    print(
        f"And {len(references)} related reference notes "
        "that you saved."
    )


if direct:
    print("\nDIRECT")
    for item in direct[:5]:
        print(
            f"- [{item['kind']}] "
            f"{item['title']} ({item['score']:.3f})"
        )
        print(f"  {item['path']}")


if related_authored:
    print("\nRELATED THINKING")
    for item in related_authored[:5]:
        print(
            f"- [{item['kind']}] "
            f"{item['title']} ({item['score']:.3f})"
        )
        print(f"  {item['path']}")


if references:
    print("\nREFERENCE MATERIAL")
    for item in references[:5]:
        print(
            f"- {item['title']} ({item['score']:.3f})"
        )
        print(f"  {item['path']}")
