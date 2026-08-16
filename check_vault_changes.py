from pathlib import Path
import sqlite3

from config import VAULT, is_index_excluded
DB = Path("data/vault.db")

def include_note(path):
    relative = path.relative_to(VAULT)
    return not is_index_excluded(relative)


# What exists in Obsidian right now
vault_files = {}

for path in VAULT.rglob("*.md"):
    if not include_note(path):
        continue

    relative = str(path.relative_to(VAULT))

    try:
        content = path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        vault_files[relative] = content
    except Exception as exc:
        print(f"Could not read: {relative} — {exc}")


# What Obsidianizer currently knows
db = sqlite3.connect(DB)

indexed = {
    path: content
    for path, content in db.execute(
        "SELECT path, content FROM notes"
    )
}

db.close()


vault_paths = set(vault_files)
indexed_paths = set(indexed)

new_paths = sorted(vault_paths - indexed_paths)
deleted_paths = sorted(indexed_paths - vault_paths)

changed_paths = sorted(
    path
    for path in vault_paths & indexed_paths
    if vault_files[path] != indexed[path]
)


print()
print("VAULT FRESHNESS CHECK")
print("=" * 40)
print(f"Vault notes:     {len(vault_paths)}")
print(f"Indexed notes:   {len(indexed_paths)}")
print()
print(f"New:             {len(new_paths)}")
print(f"Changed:         {len(changed_paths)}")
print(f"Deleted:         {len(deleted_paths)}")


def preview(label, paths):
    if not paths:
        return

    print()
    print(label)

    for path in paths[:10]:
        print(f"  {path}")

    if len(paths) > 10:
        print(f"  ...and {len(paths) - 10} more")


preview("NEW", new_paths)
preview("CHANGED", changed_paths)
preview("DELETED", deleted_paths)

print()

if not new_paths and not changed_paths and not deleted_paths:
    print("✓ Index is current.")
else:
    print("↻ Vault changes detected.")
