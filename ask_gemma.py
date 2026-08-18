from dataclasses import dataclass
import json
import re
import sys
import urllib.request

from config import CHAT_MODEL
from retrieval import evidence_context, execute_plan
from retrieval_planner import PlannerError, plan_question


MODEL = CHAT_MODEL


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    plan: object
    planner_seconds: float
    synthesized: bool


def ask_gemma(question, context):
    prompt = f"""
You are Obsidianizer, a local reasoning layer over a private Obsidian vault.

Use ONLY the vault evidence supplied below.

Rules:
- Separate evidence from inference.
- Do not invent notes, facts, links, tags, or relationships.
- Cite evidence only with its supplied ID, such as [E1].
- Never write or guess a Markdown path; Python resolves evidence IDs to paths.
- Prefer authored THINKING, PROJECT, AREA, CAPTURE, and NOTE evidence when interpreting the user's thinking.
- Treat REFERENCE material as saved material, not necessarily something the user believes or wrote.
- Distinguish direct connections from inferred connections.
- If evidence is weak or ambiguous, say so.
- Be concise but substantive.
- Only propose memory for a durable preference, principle, recurring pattern, decision, or constraint.
- Do not propose temporary facts, one-off questions, search results, or generic observations.
- If durable memory is warranted, append exactly:

[[MEMORY_PROPOSAL]]
One concise durable memory statement.
[[/MEMORY_PROPOSAL]]

- Otherwise omit the memory block.

QUESTION:
{question}

VAULT EVIDENCE:
{context}

ANSWER:
""".strip()
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)["response"].strip()


def resolve_citations(answer, evidence):
    paths = {item.evidence_id: item.path for item in evidence}

    def replace_id(match):
        evidence_id = match.group(1)
        if evidence_id not in paths:
            raise RuntimeError(
                f"Synthesis cited unavailable evidence: {evidence_id}"
            )
        return f"[{paths[evidence_id]}]"

    resolved = re.sub(r"\[(E\d+)\]", replace_id, answer)
    allowed_paths = set(paths.values())

    for citation in re.findall(r"\[([^\]]+\.md)\]", resolved):
        if citation not in allowed_paths:
            raise RuntimeError(
                "Synthesis produced an unsupported Markdown citation."
            )

    return resolved


def answer_question(
    question,
    *,
    planner_fn=plan_question,
    retrieval_fn=execute_plan,
    synthesis_fn=ask_gemma,
):
    plan, planner_seconds = planner_fn(question)
    retrieval = retrieval_fn(plan, question)

    if retrieval.direct_answer is not None:
        return AnswerResult(
            retrieval.direct_answer,
            plan,
            planner_seconds,
            False,
        )

    if not retrieval.needs_synthesis or not retrieval.evidence:
        raise RuntimeError("Retrieval returned no answerable evidence.")

    answer = synthesis_fn(question, evidence_context(retrieval))
    answer = resolve_citations(answer, retrieval.evidence)
    return AnswerResult(answer, plan, planner_seconds, True)


def main():
    args = sys.argv[1:]
    quiet = False

    if "--quiet" in args:
        quiet = True
        args.remove("--quiet")

    if not args:
        raise SystemExit(
            'Usage: python3 ask_gemma.py "your question"'
        )

    question = " ".join(args).strip()

    try:
        result = answer_question(question)
    except PlannerError as exc:
        raise SystemExit(f"Planner failed: {exc}") from exc
    except Exception as exc:
        raise SystemExit(f"Answer failed: {exc}") from exc

    if not quiet:
        print()
        print(
            f"Planner: {result.plan.mode}/{result.plan.operation} "
            f"({result.planner_seconds:.2f}s)"
        )
        print(
            "Synthesis:",
            "Gemma" if result.synthesized else "not needed",
        )
        print()
        print("=" * 60)
        print()

    print(result.answer)


if __name__ == "__main__":
    main()
