# Obsidianizer

Local-first AI search and reasoning for an Obsidian vault.

## What it does

- Exact vault search and statistics
- Semantic retrieval with local embeddings
- Hybrid search across notes, tags and links
- Local Gemma synthesis with cited source paths
- Incremental indexing and embedding
- Direct links back into Obsidian

## Privacy

Obsidianizer is designed to keep vault content local.

- The vault is read-only by default.
- Vault content is not sent to cloud LLMs.
- Embeddings and synthesis run locally through Ollama.
- Generated databases stay local and are ignored by Git.
- Machine-specific settings belong in .env.

Never commit your .env, vault, generated databases, API keys, credentials, private persona files, memory files, or other sensitive local configuration.

## Setup

1. Create a Python virtual environment.
2. Install requirements.txt.
3. Copy .env.example to .env.
4. Set VAULT_PATH in .env to your local Obsidian vault.
5. Install the required Ollama models.
6. Build the initial indexes.
7. Run ./launch.sh.

Current default models:
- Embeddings: qwen3-embedding:4b
- Synthesis: gemma4:12b-mlx

## Status

Early local-first MVP. The main vault remains read-only. Future memory/write-back features should be explicit and approval-first.
