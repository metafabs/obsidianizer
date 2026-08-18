from collections import Counter, defaultdict
import re
import sqlite3
import sys

from config import source_type


DB = "data/vault.db"


def compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def excluded_by_default(path):
    normalized = path.lower()
    return (
        "archive/monday archive/monday/" in normalized
        or "/meetings/" in normalized
        or normalized.startswith("copilot/")
    )


def topic_matches(query, *, db_path=DB, include_all=False):
    query = query.strip().lower()
    query_compact = compact(query)
    db = sqlite3.connect(db_path)

    try:
        rows = db.execute(
            """
            SELECT id, path, title, content
            FROM notes
            """
        ).fetchall()
        tags_by_note = defaultdict(list)
        links_by_note = defaultdict(list)
        for note_id, tag in db.execute(
            "SELECT note_id, tag FROM note_tags"
        ):
            tags_by_note[note_id].append(tag)
        for note_id, target in db.execute(
            "SELECT note_id, target FROM links"
        ):
            links_by_note[note_id].append(target)
    finally:
        db.close()

    matched = []

    for note_id, path, title, content in rows:
        if not include_all and excluded_by_default(path):
            continue

        tags = tags_by_note[note_id]
        links = links_by_note[note_id]
        title_match = query in title.lower()
        content_match = query in content.lower()
        tag_match = any(compact(tag) == query_compact for tag in tags)
        link_match = any(
            compact(link.split("/")[-1]) == query_compact
            for link in links
        )

        if not (title_match or content_match or tag_match or link_match):
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
            "content": content,
            "tags": tags,
            "links": links,
            "type": source_type(path),
            "reasons": reasons,
        })

    return matched


def calculate_topic_stats(query, *, db_path=DB, include_all=False):
    matched = topic_matches(
        query,
        db_path=db_path,
        include_all=include_all,
    )
    types = Counter()
    tags = Counter()
    links = Counter()
    reasons = Counter()
    query_compact = compact(query)

    for note in matched:
        types[note["type"]] += 1
        reasons.update(note["reasons"])

        for tag in note["tags"]:
            if compact(tag) != query_compact:
                tags[tag] += 1

        for link in note["links"]:
            if compact(link.split("/")[-1]) != query_compact:
                links[link] += 1

    return {
        "query": query,
        "matched": matched,
        "types": types,
        "tags": tags,
        "links": links,
        "reasons": reasons,
        "include_all": include_all,
    }


def render_topic_stats(stats):
    query = stats["query"]
    matched = stats["matched"]
    lines = [
        "",
        f'TOPIC STATS — "{query.lower()}"',
        "=" * 40,
        (
            "Scope: entire indexed vault"
            if stats["include_all"]
            else "Scope: default knowledge corpus"
        ),
        "",
        f"Direct topic notes: {len(matched)}",
        "",
        "MATCH SIGNALS",
    ]

    lines.extend(
        f"{count:4}  {reason.upper()}"
        for reason, count in stats["reasons"].most_common()
    )
    lines.extend(["", "BY SOURCE TYPE"])
    lines.extend(
        f"{count:4}  {kind}"
        for kind, count in stats["types"].most_common()
    )
    lines.extend(["", "TOP RELATED TAGS"])
    lines.extend(
        f"{count:4}  #{tag}"
        for tag, count in stats["tags"].most_common(12)
    )
    lines.extend(["", "TOP RELATED LINKS"])
    lines.extend(
        f"{count:4}  [[{link}]]"
        for link, count in stats["links"].most_common(12)
    )
    lines.extend(["", "MATCHING NOTES"])

    for note in matched[:15]:
        reason = "/".join(note["reasons"]).upper()
        lines.append(
            f"- [{note['type']}] {note['title']} — {reason}"
        )

    if len(matched) > 15:
        lines.append(f"...and {len(matched) - 15} more.")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if not args:
        raise SystemExit(
            'Usage: python3 topic_stats.py "topic" [--all]'
        )

    include_all = "--all" in args
    args = [arg for arg in args if arg != "--all"]
    query = " ".join(args).strip()
    stats = calculate_topic_stats(query, include_all=include_all)
    print(render_topic_stats(stats))


if __name__ == "__main__":
    main()
