from dataclasses import dataclass
import json
import time
import urllib.request

from config import CHAT_MODEL


PLANNER_PROMPT = """Choose evidence for a local notes question. Return JSON only.
Modes: structural, semantic, hybrid.
Operations:
- latest_notes: one or more newest notes by requested recency facts
- recent_notes: notes in a time window; hybrid means interpret that corpus
- topic_stats: exact direct-match count/statistics
- authorship_evidence: whether the user wrote about one topic, plus related ideas
- exact_lookup: a specifically named note
- structural_search: exact title/tag/link/heading/content evidence
- semantic_search: themes, concepts, similarities, connections
Fields: mode, operation, topic, time_scope, days, limit, authored_only, recency.
time_scope is none, recent, this_week, this_month, or last_n_days.
Exact metadata, lookup, search, and counts must use structural mode.
Conceptual themes/connections between ideas use semantic_search in semantic mode.
Interpreting a recent corpus uses recent_notes in hybrid mode.
Use recent_notes only when the question explicitly asks for a time period or recency;
never invent a time scope for an otherwise conceptual question.
Authorship evidence uses authorship_evidence in hybrid mode.
Use latest_notes, not recent_notes, when asking for newest-note facts.
For latest_notes, recency is an array containing every requested fact:
modified for updated/changed, created for creation metadata, and added for first
seen in the vault/index. Never substitute one recency fact for another.
Questions about themes or thinking within any time scope must use hybrid
recent_notes, never authorship_evidence; use authored_only true for the user's
own writing/thinking corpus.
Set authored_only true only when explicitly asking what the user wrote or thought;
possessive words such as "my note" or "my vault" do not imply authored_only.
This authored_only rule also applies to latest_notes.
Use null when topic/days do not apply. Never answer the question."""


MODES = {"structural", "semantic", "hybrid"}
OPERATIONS = {
    "latest_notes",
    "recent_notes",
    "topic_stats",
    "authorship_evidence",
    "exact_lookup",
    "structural_search",
    "semantic_search",
}
TIME_SCOPES = {
    "none",
    "recent",
    "this_week",
    "this_month",
    "last_n_days",
}
RECENCY_KINDS = {"modified", "created", "added"}
VALID_COMBINATIONS = {
    "structural": {
        "latest_notes",
        "recent_notes",
        "topic_stats",
        "exact_lookup",
        "structural_search",
    },
    "semantic": {"semantic_search"},
    "hybrid": {"recent_notes", "authorship_evidence"},
}
TOPIC_OPERATIONS = {
    "topic_stats",
    "authorship_evidence",
    "exact_lookup",
    "structural_search",
}
DEFAULT_LIMITS = {
    "latest_notes": 1,
    "recent_notes": 30,
    "topic_stats": 12,
    "authorship_evidence": 5,
    "exact_lookup": 5,
    "structural_search": 12,
    "semantic_search": 12,
}


class PlannerError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalPlan:
    mode: str
    operation: str
    topic: str | None = None
    time_scope: str = "none"
    days: int | None = None
    limit: int = 12
    authored_only: bool = False
    recency: tuple[str, ...] = ("modified",)


def validate_plan(value):
    if not isinstance(value, dict):
        raise PlannerError("Planner response must be a JSON object.")

    mode = value.get("mode")
    operation = value.get("operation")

    if mode not in MODES:
        raise PlannerError(f"Unsupported retrieval mode: {mode!r}")

    if operation not in OPERATIONS:
        raise PlannerError(f"Unsupported retrieval operation: {operation!r}")

    if operation not in VALID_COMBINATIONS[mode]:
        raise PlannerError(
            f"Invalid retrieval combination: {mode}/{operation}"
        )

    topic = value.get("topic")
    if topic is not None:
        if not isinstance(topic, str):
            raise PlannerError("Planner topic must be text or null.")
        topic = topic.strip().rstrip("?")
        if not topic or len(topic) > 200:
            raise PlannerError("Planner topic is empty or too long.")

    if operation in TOPIC_OPERATIONS and not topic:
        raise PlannerError(f"Operation {operation} requires a topic.")

    time_scope = value.get("time_scope") or "none"
    if time_scope not in TIME_SCOPES:
        raise PlannerError(f"Unsupported time scope: {time_scope!r}")

    if operation == "recent_notes" and time_scope == "none":
        time_scope = "recent"

    if operation != "recent_notes" and time_scope != "none":
        raise PlannerError(
            f"Operation {operation} cannot use a time scope."
        )

    days = value.get("days")
    if time_scope == "last_n_days":
        if not isinstance(days, int) or isinstance(days, bool):
            raise PlannerError("last_n_days requires an integer day count.")
        if not 1 <= days <= 365:
            raise PlannerError("Planner day count must be between 1 and 365.")
    elif days is not None:
        raise PlannerError("Planner days must be null for this time scope.")

    limit = value.get("limit")
    if limit is None:
        limit = DEFAULT_LIMITS[operation]
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise PlannerError("Planner limit must be an integer.")
    limit = min(max(limit, 1), 30)

    authored_only = value.get("authored_only")
    if authored_only is None:
        authored_only = False
    if not isinstance(authored_only, bool):
        raise PlannerError("Planner authored_only must be boolean.")

    recency = value.get("recency")
    if operation == "latest_notes":
        if recency is None:
            recency = ["modified"]
        elif isinstance(recency, str):
            recency = [recency]
        if not isinstance(recency, list) or not recency:
            raise PlannerError("latest_notes requires one or more recency facts.")
        if any(item not in RECENCY_KINDS for item in recency):
            raise PlannerError("Planner returned an unsupported recency fact.")
        recency = tuple(dict.fromkeys(recency))
    else:
        if recency not in (None, []):
            raise PlannerError(
                f"Operation {operation} cannot use recency facts."
            )
        recency = ()

    return RetrievalPlan(
        mode=mode,
        operation=operation,
        topic=topic,
        time_scope=time_scope,
        days=days,
        limit=limit,
        authored_only=authored_only,
        recency=recency,
    )


def call_planner(question):
    prompt = f"{PLANNER_PROMPT}\nQuestion: {json.dumps(question)}"
    payload = json.dumps({
        "model": CHAT_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": 0,
            "num_predict": 160,
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)["response"]


def plan_question(question, generate_fn=call_planner):
    started = time.monotonic()

    try:
        raw = generate_fn(question)
        if isinstance(raw, str):
            raw = raw.strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end < start:
                raise PlannerError("Planner returned no JSON object.")
            value = json.loads(raw[start:end + 1])
        else:
            value = raw
        plan = validate_plan(value)
    except PlannerError:
        raise
    except Exception as exc:
        raise PlannerError(f"Retrieval planning failed: {exc}") from exc

    return plan, time.monotonic() - started
