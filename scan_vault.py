from pathlib import Path

from config import VAULT

EXCLUDED_FROM_KNOWLEDGE = {
    "_agent-instructions",
    "_obsidianizer-output",
    ".obsidian",
}

def is_knowledge_note(path: Path) -> bool:
    relative = path.relative_to(VAULT)
    return not any(part in EXCLUDED_FROM_KNOWLEDGE for part in relative.parts)

def main():
    all_markdown = list(VAULT.rglob("*.md"))
    knowledge_notes = [p for p in all_markdown if is_knowledge_note(p)]
    control_notes = [
        p for p in all_markdown
        if "_agent-instructions" in p.relative_to(VAULT).parts
    ]

    print(f"Vault: {VAULT}")
    print(f"All Markdown files: {len(all_markdown)}")
    print(f"Knowledge notes: {len(knowledge_notes)}")
    print(f"Agent/control files: {len(control_notes)}")

    print("\nSample knowledge notes:")
    for note in sorted(knowledge_notes)[:10]:
        print(f"  - {note.relative_to(VAULT)}")

    print("\nREAD-ONLY CORPUS CHECK COMPLETE")

if __name__ == "__main__":
    main()
