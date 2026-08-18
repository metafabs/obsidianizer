from array import array
import json
import math
import re
import sqlite3
import sys
import urllib.request

from config import EMBED_MODEL, source_type


SEMANTIC_DB = "data/semantic.db"
VAULT_DB = "data/vault.db"
MODEL = EMBED_MODEL


def embed(text):
    payload = json.dumps({
        "model": MODEL,
        "input": text,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://localhost:11434/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)["embeddings"][0]


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
    normalized = path.lower()
    return (
        "archive/monday archive/monday/" in normalized
        or normalized.startswith("copilot/")
    )


def load_metadata(vault_db=VAULT_DB):
    vault = sqlite3.connect(vault_db)

    try:
        return {
            path: {
                "title": title,
                "tags": tags,
                "links": links,
            }
            for path, title, tags, links in vault.execute(
                """
                SELECT
                    n.path,
                    n.title,
                    COALESCE(GROUP_CONCAT(DISTINCT t.tag), ''),
                    COALESCE(GROUP_CONCAT(DISTINCT l.target), '')
                FROM notes n
                LEFT JOIN note_tags t ON t.note_id = n.id
                LEFT JOIN links l ON l.note_id = n.id
                GROUP BY n.id
                """
            )
        }
    finally:
        vault.close()


def search_semantic(
    query,
    *,
    allowed_paths=None,
    limit=12,
    include_all=False,
    vault_db=VAULT_DB,
    semantic_db=SEMANTIC_DB,
    embed_fn=embed,
):
    if allowed_paths is not None:
        allowed_paths = set(allowed_paths)
        if not allowed_paths:
            return []

    terms = re.findall(r"[A-Za-z0-9_-]+", query.lower())
    metadata = load_metadata(vault_db)
    query_vector = embed_fn(query)
    semantic = sqlite3.connect(semantic_db)

    try:
        rows = semantic.execute(
            """
            SELECT path, title, chunk_index, content, embedding
            FROM chunks
            """
        ).fetchall()
    finally:
        semantic.close()

    results = []

    for path, title, chunk_index, content, blob in rows:
        if allowed_paths is not None and path not in allowed_paths:
            continue
        if not include_all and is_background(path):
            continue

        semantic_score = cosine(query_vector, decode_vector(blob))
        meta = metadata.get(path, {})
        title_l = meta.get("title", title).lower()
        tags_l = meta.get("tags", "").lower()
        links_l = meta.get("links", "").lower()
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

        results.append({
            "score": semantic_score + lexical_bonus,
            "semantic": semantic_score,
            "path": path,
            "title": title,
            "chunk": chunk_index,
            "content": content,
        })

    results.sort(key=lambda item: item["score"], reverse=True)
    strongest = []
    seen = set()

    for result in results:
        if result["path"] in seen:
            continue
        seen.add(result["path"])
        strongest.append(result)
        if limit is not None and len(strongest) >= limit:
            break

    return strongest


def render_results(query, results, include_all=False):
    lines = [
        "",
        f'Hybrid search: "{query}"',
        (
            "Scope: entire corpus"
            if include_all
            else "Scope: default knowledge corpus"
        ),
        "",
    ]

    for index, result in enumerate(results, 1):
        kind = source_type(result["path"])
        lines.extend([
            (
                f"{index:2}. {result['score']:.4f} "
                f"(semantic {result['semantic']:.4f}) "
                f"[{kind}] {result['title']}"
            ),
            f"    {result['path']}",
            f"    {snippet(result['content'])}",
            "",
        ])

    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if not args:
        raise SystemExit(
            'Usage: python3 semantic_search.py "your query" [--all]'
        )

    include_all = "--all" in args
    args = [arg for arg in args if arg != "--all"]
    query = " ".join(args).strip()
    results = search_semantic(query, include_all=include_all)
    print(render_results(query, results, include_all=include_all))


if __name__ == "__main__":
    main()
