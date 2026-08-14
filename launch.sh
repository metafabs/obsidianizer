#!/bin/zsh

APP="$(cd "$(dirname "$0")" && pwd)"
URL="http://localhost:8501"
PY="$APP/.venv/bin/python"

RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
GREEN=$'\033[38;5;114m'
YELLOW=$'\033[38;5;221m'
PINK=$'\033[38;5;213m'
BLUE=$'\033[38;5;117m'
LAVENDER=$'\033[38;5;183m'
RED=$'\033[38;5;203m'

if [[ ! -f "$APP/.env" ]]; then
    echo ""
    echo "Obsidianizer is not configured yet."
    echo ""
    echo "Copy:"
    echo "  .env.example -> .env"
    echo ""
    echo "Then add your real local vault path to .env."
    exit 1
fi

if [[ ! -x "$PY" ]]; then
    echo ""
    echo "Python environment not found."
    echo ""
    echo "Run:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

cd "$APP" || exit 1

REFRESH_STATUS="current"

if "$PY" refresh_vault.py > /tmp/obsidianizer-refresh.log 2>&1; then
    REFRESH_STATUS="current"
else
    REFRESH_STATUS="warning"
fi

COUNTS=$("$PY" - <<'PY'
import sqlite3

def count(db_path, query):
    try:
        db = sqlite3.connect(db_path)
        value = db.execute(query).fetchone()[0]
        db.close()
        return value
    except Exception:
        return "?"

print(
    count("data/vault.db", "SELECT COUNT(*) FROM notes"),
    count("data/semantic.db", "SELECT COUNT(DISTINCT path) FROM chunks"),
    count("data/semantic.db", "SELECT COUNT(*) FROM chunks"),
)
PY
)

read NOTE_COUNT SEMANTIC_NOTES CHUNK_COUNT <<< "$COUNTS"

printf '\033[3J\033[2J\033[H'

printf "    ${BLUE}╭──────────────────────────────────╮${RESET}\n"
printf "    ${BLUE}│${RESET}    ${BOLD}o b s i d i a n i z e r${RESET}     ${BLUE}│${RESET}\n"
printf "    ${BLUE}╰──────────────────────────────────╯${RESET}\n"
printf "\n"
printf "          ${DIM}your local knowledge system${RESET}\n"
printf "\n"

printf "              ${YELLOW}${BOLD}✦ YOUR VAULT ✦${RESET}\n"
printf "              ${DIM}%s indexed notes${RESET}\n" "$NOTE_COUNT"
printf "                    ${LAVENDER}│${RESET}\n"
printf "          ${LAVENDER}┌─────────┴─────────┐${RESET}\n"
printf "          ${LAVENDER}▼                   ▼${RESET}\n"
printf "    ${BLUE}${BOLD}EXACT + STATS${RESET}       ${GREEN}${BOLD}SEMANTIC MAP${RESET}\n"
printf "       ${DIM}SQLite${RESET}             ${DIM}Qwen 4B${RESET}\n"
printf "                           ${DIM}%s notes${RESET}\n" "$SEMANTIC_NOTES"
printf "                           ${DIM}%s chunks${RESET}\n" "$CHUNK_COUNT"
printf "          ${LAVENDER}│                   │${RESET}\n"
printf "          ${LAVENDER}└─────────┬─────────┘${RESET}\n"
printf "                    ${LAVENDER}▼${RESET}\n"
printf "             ${PINK}${BOLD}HYBRID RETRIEVAL${RESET}\n"
printf "                    ${LAVENDER}│${RESET}\n"
printf "                    ${LAVENDER}▼${RESET}\n"
printf "                ${YELLOW}${BOLD}CHAT UI${RESET}\n"
printf "\n"

printf "    ${LAVENDER}${BOLD}TOOLS${RESET}  ${GREEN}✓${RESET} Find  ${GREEN}✓${RESET} Surface  ${GREEN}✓${RESET} Stats\n"
printf "    ${LAVENDER}${BOLD}AI${RESET}     ${GREEN}✓${RESET} Qwen 4B retrieval   ${GREEN}✓${RESET} Gemma 12B synthesis\n"

if [[ "$REFRESH_STATUS" == "current" ]]; then
    printf "    ${GREEN}✓${RESET} Vault sync current"
else
    printf "    ${RED}⚠${RESET} Vault sync warning"
fi

if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    printf "   ${GREEN}✓${RESET} Ollama"
else
    printf "   ${RED}✗${RESET} Ollama"
fi

printf "   ${GREEN}◆${RESET} local only\n"

printf "\n"
printf "           ${YELLOW}✦${RESET}  ${BOLD}Press ENTER to open chat${RESET}  ${PINK}✦${RESET}\n"
printf "\n"

read

if ! curl -fsS "$URL" >/dev/null 2>&1; then
    nohup "$APP/.venv/bin/streamlit" run "$APP/app.py" \
        --server.headless true \
        > /tmp/obsidianizer.log 2>&1 &
    sleep 2
fi

open "$URL"
