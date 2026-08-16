import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

vault_path = os.getenv("VAULT_PATH")

if not vault_path:
    raise RuntimeError(
        "VAULT_PATH is not configured. "
        "Copy .env.example to .env and set your local Obsidian vault path."
    )

VAULT = Path(vault_path).expanduser()

EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:4b")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemma4:12b-mlx")

def _path_list(name):
    value = os.getenv(name, "")
    return [
        item.strip().lower().replace("\\", "/")
        for item in value.split(",")
        if item.strip()
    ]


ROLE_PATHS = {
    "THINKING": _path_list("THINKING_PATHS"),
    "PROJECT": _path_list("PROJECT_PATHS"),
    "AREA": _path_list("AREA_PATHS"),
    "REFERENCE": _path_list("REFERENCE_PATHS"),
    "CAPTURE": _path_list("CAPTURE_PATHS"),
    "ARCHIVE": _path_list("ARCHIVE_PATHS"),
}


def source_type(path):
    normalized = str(path).lower().replace("\\", "/")

    for role, patterns in ROLE_PATHS.items():
        if any(pattern in normalized for pattern in patterns):
            return role

    return "NOTE"

INTERNAL_EXCLUDE_DIRS = {
    ".obsidian",
    "_agent-instructions",
    "_obsidianizer-memory",
}

INDEX_EXCLUDE_DIRS = (
    INTERNAL_EXCLUDE_DIRS
    | set(_path_list("INDEX_EXCLUDE_DIRS"))
)



def is_index_excluded(path):
    normalized = str(path).lower().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]

    return any(part in INDEX_EXCLUDE_DIRS for part in parts)
