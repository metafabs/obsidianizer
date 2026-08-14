import sqlite3
import re

DB = "data/vault.db"

WHOLE_NOTE_LIMIT = 600
TARGET_WORDS = 550
MAX_WORDS = 700
OVERLAP_WORDS = 75

def word_count(text):
    return len(re.findall(r"\b\w+\b", text))

def split_into_blocks(text):
    # Markdown headings become their own structural blocks.
    blocks = re.split(r"\n\s*\n", text)
    return [b.strip() for b in blocks if b.strip()]

def tail_words(text, count):
    words = text.split()
    return " ".join(words[-count:])

def chunk_note(text):
    total = word_count(text)

    if total <= WHOLE_NOTE_LIMIT:
        return [text.strip()]

    blocks = split_into_blocks(text)

    chunks = []
    current = []
    current_words = 0

    for block in blocks:
        block_words = word_count(block)

        # Very large blocks get split approximately by words.
        if block_words > MAX_WORDS:
            words = block.split()

            if current:
                chunks.append("\n\n".join(current))
                overlap = tail_words(chunks[-1], OVERLAP_WORDS)
                current = [overlap] if overlap else []
                current_words = word_count(overlap)

            start = 0

            while start < len(words):
                end = min(start + TARGET_WORDS, len(words))
                piece = " ".join(words[start:end])
                chunks.append(piece)

                if end == len(words):
                    break

                start = max(end - OVERLAP_WORDS, start + 1)

            current = []
            current_words = 0
            continue

        if current and current_words + block_words > MAX_WORDS:
            finished = "\n\n".join(current)
            chunks.append(finished)

            overlap = tail_words(finished, OVERLAP_WORDS)
            current = [overlap, block] if overlap else [block]
            current_words = word_count(overlap) + block_words

        else:
            current.append(block)
            current_words += block_words

    if current:
        chunks.append("\n\n".join(current))

    return chunks

db = sqlite3.connect(DB)

rows = db.execute("""
    SELECT title, path, content, word_count
    FROM notes
    WHERE lower(path) NOT LIKE '%/meetings/%'
      AND lower(path) NOT LIKE 'copilot/%'
""").fetchall()

total_chunks = 0
split_notes = 0
chunk_sizes = []

for title, path, content, words in rows:
    chunks = chunk_note(content)

    total_chunks += len(chunks)

    if len(chunks) > 1:
        split_notes += 1

    chunk_sizes.extend(word_count(c) for c in chunks)

print("Knowledge notes:", len(rows))
print("Notes requiring split:", split_notes)
print("Total semantic chunks:", total_chunks)
print("Average chunks per note:", round(total_chunks / len(rows), 2))
print("Largest chunk:", max(chunk_sizes), "words")
print()

print("SAMPLE — LONG NOTE")

sample = db.execute("""
    SELECT title, content, word_count
    FROM notes
    WHERE lower(path) NOT LIKE '%/meetings/%'
      AND lower(path) NOT LIKE 'copilot/%'
    ORDER BY word_count DESC
    LIMIT 1
""").fetchone()

title, content, words = sample
chunks = chunk_note(content)

print(title)
print("Original:", words, "words")
print("Chunks:", len(chunks))
print("Chunk sizes:", [word_count(c) for c in chunks])

db.close()
