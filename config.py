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
