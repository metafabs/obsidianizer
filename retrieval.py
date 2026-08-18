from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from config import source_type
from retrieval_planner import RetrievalPlan
from semantic_search import search_semantic
from topic_stats import topic_matches


SOURCE_DB = Path("data/vault.db")
SEMANTIC_DB = Path("data/semantic.db")
AUTHORED_TYPES = {"THINKING", "PROJECT", "AREA", "CAPTURE", "NOTE"}
DEFAULT_RECENT_DAYS = 30
SYNTHESIS_NOTE_LIMIT = 12
CONTENT_LIMIT = 900


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    path: str
    title: str
    source_type: str
    modified: str | None
    content: str
    reason: str
    created: str | None = None


@dataclass(frozen=True)
class RetrievalResult:
    mode: str
    operation: str
    evidence: tuple[EvidenceItem, ...]
    direct_answer: str | None
    needs_synthesis: bool
    scope: str


def is_authored(path):
    return source_type(path) in AUTHORED_TYPES


def _connect(path):
    return sqlite3.connect(path)


def _trim_content(content):
    content = content.strip()
    if len(content) <= CONTENT_LIMIT:
        return content
    return content[:CONTENT_LIMIT].rstrip() + "…"


def _evidence(rows):
    return tuple(
        EvidenceItem(
            evidence_id=f"E{index}",
            path=row["path"],
            title=row["title"],
            source_type=source_type(row["path"]),
            modified=row.get("modified"),
            content=_trim_content(row.get("content", "")),
            reason=row.get("reason", "retrieved evidence"),
            created=row.get("created"),
        )
        for index, row in enumerate(rows, 1)
    )


def _load_notes(source_db=SOURCE_DB):
    db = _connect(source_db)

    try:
        return [
            {
                "path": path,
                "title": title,
                "content": content,
                "modified": modified,
                "modified_ns": modified_ns,
                "created": created,
            }
            for path, title, content, modified, modified_ns, created in db.execute(
                """
                SELECT path, title, content, modified, modified_ns, created
                FROM notes
                ORDER BY modified_ns DESC, id DESC
                """
            )
        ]
    finally:
        db.close()


def _latest_rows(recency, limit, authored_only, source_db):
    if recency == "modified":
        order = "modified_ns DESC, id DESC"
        condition = "modified_ns IS NOT NULL"
    elif recency == "created":
        order = "created DESC, id DESC"
        condition = "created IS NOT NULL AND trim(created) <> ''"
    else:
        raise ValueError(f"Unsupported deterministic recency: {recency}")

    db = _connect(source_db)
    try:
        rows = [
            {
                "path": path,
                "title": title,
                "content": content,
                "modified": modified,
                "modified_ns": modified_ns,
                "created": created,
            }
            for path, title, content, modified, modified_ns, created
            in db.execute(
                f"""
                SELECT path, title, content, modified, modified_ns, created
                FROM notes
                WHERE {condition}
                ORDER BY {order}
                """
            )
        ]
    finally:
        db.close()

    if authored_only:
        rows = [row for row in rows if is_authored(row["path"])]

    for row in rows:
        row["reason"] = f"most recently {recency}"

    return rows[:limit]


def _time_boundary(time_scope, days, now):
    if time_scope == "recent":
        boundary = now - timedelta(days=DEFAULT_RECENT_DAYS)
        description = f"the last {DEFAULT_RECENT_DAYS} days"
    elif time_scope == "this_week":
        boundary = now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ) - timedelta(days=now.weekday())
        description = "this week"
    elif time_scope == "this_month":
        boundary = now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        description = "this month"
    elif time_scope == "last_n_days":
        boundary = now - timedelta(days=days)
        description = f"the last {days} days"
    else:
        raise ValueError(f"Unsupported time scope: {time_scope}")

    return int(boundary.timestamp() * 1_000_000_000), description


def _recent_rows(plan, source_db, now):
    boundary_ns, description = _time_boundary(
        plan.time_scope,
        plan.days,
        now,
    )
    rows = [
        row
        for row in _load_notes(source_db)
        if row["modified_ns"] >= boundary_ns
        and (not plan.authored_only or is_authored(row["path"]))
    ]

    for row in rows:
        row["reason"] = f"modified during {description}"

    return rows[:plan.limit], description


def _format_note_list(lead, evidence, timestamp="modified"):
    if not evidence:
        return f"{lead}: no indexed notes matched."

    lines = [lead + ":", ""]
    for item in evidence:
        timestamp_value = getattr(item, timestamp)
        timestamp_text = (
            f" — {timestamp} {timestamp_value}" if timestamp_value else ""
        )
        lines.extend([
            f"- **{item.title}**{timestamp_text}",
            item.path,
        ])
    return "\n".join(lines)


def _format_unavailable_added():
    return (
        "Latest note added to the vault: unavailable from the current index. "
        "There is no persisted first-seen/indexed timestamp, so modified time "
        "or filesystem creation time cannot answer this reliably."
    )


def _exact_lookup(topic, limit, authored_only, source_db):
    db = _connect(source_db)

    try:
        rows = db.execute(
            """
            SELECT path, title, content, modified
            FROM notes
            WHERE lower(title) = lower(?)
               OR lower(path) = lower(?)
            ORDER BY modified_ns DESC, id DESC
            LIMIT ?
            """,
            (topic, topic, limit),
        ).fetchall()
    finally:
        db.close()

    results = [
        {
            "path": path,
            "title": title,
            "content": content,
            "modified": modified,
            "reason": "exact title or path match",
        }
        for path, title, content, modified in rows
    ]
    if authored_only:
        results = [row for row in results if is_authored(row["path"])]
    return results


def _topic_rows(topic, limit, authored_only, source_db):
    matches = topic_matches(topic, db_path=source_db)
    if authored_only:
        matches = [item for item in matches if is_authored(item["path"])]
    return [
        {
            "path": item["path"],
            "title": item["title"],
            "content": item["content"],
            "modified": None,
            "reason": "direct " + "/".join(item["reasons"]) + " match",
        }
        for item in matches[:limit]
    ]


def _semantic_rows(
    query,
    *,
    limit,
    source_db,
    semantic_db,
    allowed_paths=None,
    semantic_search_fn=search_semantic,
):
    results = semantic_search_fn(
        query,
        allowed_paths=allowed_paths,
        limit=limit,
        vault_db=source_db,
        semantic_db=semantic_db,
    )
    return [
        {
            "path": item["path"],
            "title": item["title"],
            "content": item["content"],
            "modified": None,
            "reason": f"semantic score {item['semantic']:.3f}",
        }
        for item in results
    ]


def authorship_evidence(
    topic,
    *,
    limit=5,
    source_db=SOURCE_DB,
    semantic_db=SEMANTIC_DB,
    semantic_search_fn=search_semantic,
):
    matches = topic_matches(topic, db_path=source_db)
    direct = [item for item in matches if is_authored(item["path"])]
    related = semantic_search_fn(
        topic,
        limit=None,
        vault_db=source_db,
        semantic_db=semantic_db,
    )
    direct_paths = {item["path"] for item in direct}
    related_authored = []
    references = []

    for item in related:
        if item["path"] in direct_paths or item["semantic"] < 0.56:
            continue
        kind = source_type(item["path"])
        if kind in AUTHORED_TYPES:
            related_authored.append(item)
        elif kind == "REFERENCE":
            references.append(item)

    return {
        "direct": direct[:limit],
        "related_authored": related_authored[:limit],
        "references": references[:limit],
    }


def _format_authorship(topic, groups):
    direct = groups["direct"]
    related = groups["related_authored"]
    references = groups["references"]
    lines = [
        (
            f"Yes — I found {len(direct)} direct authored "
            f"match{'es' if len(direct) != 1 else ''} for **{topic}**."
            if direct
            else f"No direct authored match was found for **{topic}**."
        )
    ]

    if direct:
        lines.extend(["", "DIRECT"])
        for item in direct:
            lines.extend([f"- {item['title']}", item["path"]])

    if related:
        lines.extend(["", "RELATED THINKING"])
        for item in related:
            lines.extend([f"- {item['title']}", item["path"]])

    if references:
        lines.extend(["", "REFERENCE MATERIAL"])
        for item in references:
            lines.extend([f"- {item['title']}", item["path"]])

    return "\n".join(lines)


def evidence_context(result):
    lines = [f"RETRIEVAL SCOPE: {result.scope}"]

    for item in result.evidence:
        lines.extend([
            "",
            f"[{item.evidence_id}]",
            f"Title: {item.title}",
            f"Path: {item.path}",
            f"Source type: {item.source_type}",
            f"Modified: {item.modified or 'not supplied'}",
            f"Reason: {item.reason}",
            "Content:",
            item.content,
        ])

    return "\n".join(lines)


def execute_plan(
    plan: RetrievalPlan,
    question,
    *,
    source_db=SOURCE_DB,
    semantic_db=SEMANTIC_DB,
    semantic_search_fn=search_semantic,
    now=None,
):
    now = now or datetime.now().astimezone()

    if plan.operation == "latest_notes":
        noun = "authored note" if plan.authored_only else "note"
        rows = []
        groups = []

        for recency in plan.recency:
            start = len(rows)
            if recency == "added":
                recency_rows = []
            else:
                recency_rows = _latest_rows(
                    recency,
                    plan.limit,
                    plan.authored_only,
                    source_db,
                )
                rows.extend(recency_rows)
            groups.append((recency, start, len(rows)))

        evidence = _evidence(rows)
        answers = []
        scopes = []

        for recency, start, end in groups:
            if recency == "added":
                answers.append(_format_unavailable_added())
                scopes.append("added-to-vault unavailable: no first-seen field")
                continue

            label = "modified" if recency == "modified" else "created"
            answers.append(
                _format_note_list(
                    f"Most recently {label} {noun}",
                    evidence[start:end],
                    timestamp=label,
                )
            )
            scopes.append(
                f"notes ordered by canonical {label} timestamp"
            )

        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            "\n\n".join(answers),
            False,
            "; ".join(scopes),
        )

    if plan.operation == "recent_notes":
        rows, description = _recent_rows(plan, source_db, now)

        if plan.mode == "structural":
            evidence = _evidence(rows)
            return RetrievalResult(
                plan.mode,
                plan.operation,
                evidence,
                _format_note_list(f"Notes modified during {description}", evidence),
                False,
                description,
            )

        if len(rows) > SYNTHESIS_NOTE_LIMIT:
            try:
                rows = _semantic_rows(
                    question,
                    limit=SYNTHESIS_NOTE_LIMIT,
                    source_db=source_db,
                    semantic_db=semantic_db,
                    allowed_paths={row["path"] for row in rows},
                    semantic_search_fn=semantic_search_fn,
                )
            except Exception:
                rows = rows[:SYNTHESIS_NOTE_LIMIT]
                description += "; semantic ranking unavailable"

        evidence = _evidence(rows)
        if not evidence:
            answer = f"No indexed notes were modified during {description}."
            needs_synthesis = False
        else:
            answer = None
            needs_synthesis = True

        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            answer,
            needs_synthesis,
            description,
        )

    if plan.operation == "topic_stats":
        matches = topic_matches(plan.topic, db_path=source_db)
        if plan.authored_only:
            matches = [
                item for item in matches
                if is_authored(item["path"])
            ]
        evidence = _evidence([
            {
                "path": item["path"],
                "title": item["title"],
                "content": item["content"],
                "modified": None,
                "reason": (
                    "direct " + "/".join(item["reasons"]) + " match"
                ),
            }
            for item in matches[:plan.limit]
        ])
        count = len(matches)
        answer = (
            f"I found **{count}** directly matching indexed "
            f"note{'s' if count != 1 else ''} for **{plan.topic}**.\n\n"
            "This count uses deterministic title, content, tag, and link matches."
        )
        if evidence:
            answer += "\n\n" + _format_note_list("Matching notes", evidence)
        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            answer,
            False,
            "deterministic direct topic matches",
        )

    if plan.operation == "authorship_evidence":
        groups = authorship_evidence(
            plan.topic,
            limit=plan.limit,
            source_db=source_db,
            semantic_db=semantic_db,
            semantic_search_fn=semantic_search_fn,
        )
        rows = []
        for group, reason in (
            (groups["direct"], "direct authored match"),
            (groups["related_authored"], "semantically related authored note"),
            (groups["references"], "semantically related saved reference"),
        ):
            for item in group:
                rows.append({
                    "path": item["path"],
                    "title": item["title"],
                    "content": item.get("content", ""),
                    "modified": None,
                    "reason": reason,
                })
        evidence = _evidence(rows)
        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            _format_authorship(plan.topic, groups),
            False,
            "authored notes separated from saved references",
        )

    if plan.operation == "exact_lookup":
        evidence = _evidence(
            _exact_lookup(
                plan.topic,
                plan.limit,
                plan.authored_only,
                source_db,
            )
        )
        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            _format_note_list(f'Exact lookup for "{plan.topic}"', evidence),
            False,
            "exact title or path lookup",
        )

    if plan.operation == "structural_search":
        evidence = _evidence(
            _topic_rows(
                plan.topic,
                plan.limit,
                plan.authored_only,
                source_db,
            )
        )
        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            _format_note_list(f'Direct matches for "{plan.topic}"', evidence),
            False,
            "deterministic title, content, tag, and link search",
        )

    if plan.operation == "semantic_search":
        allowed_paths = None
        if plan.authored_only:
            allowed_paths = {
                row["path"]
                for row in _load_notes(source_db)
                if is_authored(row["path"])
            }
        rows = _semantic_rows(
            question,
            limit=plan.limit,
            source_db=source_db,
            semantic_db=semantic_db,
            allowed_paths=allowed_paths,
            semantic_search_fn=semantic_search_fn,
        )
        evidence = _evidence(rows)
        return RetrievalResult(
            plan.mode,
            plan.operation,
            evidence,
            None if evidence else "No semantic evidence was found.",
            bool(evidence),
            "semantic search across the default knowledge corpus",
        )

    raise ValueError(f"Unsupported retrieval operation: {plan.operation}")
