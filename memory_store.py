from datetime import datetime
from pathlib import Path

from config import VAULT


MEMORY_FILE = (
    VAULT
    / "_obsidianizer-memory"
    / "AI-MEMORY.md"
)


def memory_path():
    return MEMORY_FILE


def is_allowed_write(path):
    candidate = Path(path).expanduser().resolve()
    allowed = MEMORY_FILE.expanduser().resolve()

    return candidate == allowed


def save_memory(text, sources=None):
    target = memory_path()

    if not is_allowed_write(target):
        raise RuntimeError(
            "Refusing write: target is outside approved memory file."
        )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    is_new = not target.exists()

    with target.open(
        "a",
        encoding="utf-8",
    ) as handle:

        if is_new:
            handle.write(
                "# Obsidianizer AI Memory\n\n"
                "> Explicitly approved durable memories only.\n"
            )

        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        handle.write(
            f"\n## {timestamp}\n\n"
            f"{text.strip()}\n"
        )

        if sources:
            handle.write("\nSources:\n")

            for source in sources:
                handle.write(
                    f"- `{source}`\n"
                )

    return target
