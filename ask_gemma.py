import json
import subprocess
import sys
import urllib.request

MODEL = "gemma4:12b-mlx"


def retrieve(question):
    result = subprocess.run(
        [sys.executable, "semantic_search.py", question],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return result.stdout.strip()


def ask_gemma(question, context):
    prompt = f"""
You are Obsidianizer, a local reasoning layer over a private Obsidian vault.

Use ONLY the vault evidence supplied below.

Rules:
- Separate evidence from inference.
- Do not invent notes, facts, links, tags, or relationships.
- Prefer the user's authored THINKING, PROJECT, AREA, and CAPTURE notes when interpreting their own thinking.
- Treat REFERENCE material as material they saved, not necessarily something they believe or wrote.
- When making a claim, cite the relevant Markdown path in square brackets.
- A relationship or category must be supported by the supplied note evidence, not merely suggested by the user's question.
- Do not classify a concept as martial arts, productivity, marketing, philosophy, or any other domain unless the supplied evidence supports that classification.
- When connecting two ideas, distinguish clearly between:
  1. DIRECT CONNECTION — the notes explicitly connect them.
  2. INFERRED CONNECTION — you see a plausible relationship across separate notes.
- Never turn an inferred connection into a fact.
- If the evidence is weak or ambiguous, say so.
- Look for patterns and connections, not generic advice.
- Be concise but substantive.

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
        "options": {
            "temperature": 0.2
        }
    }).encode("utf-8")

    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)

    return data["response"].strip()


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

    if not quiet:
        print()
        print("Retrieving vault evidence...")

    context = retrieve(question)

    if not quiet:
        print("Asking Gemma...")
        print()
        print("=" * 60)
        print()

    answer = ask_gemma(question, context)
    print(answer)


if __name__ == "__main__":
    main()
