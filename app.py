from pathlib import Path
import hashlib
from urllib.parse import quote
import streamlit as st
import subprocess
import sys

from config import VAULT
from memory_store import save_memory

st.set_page_config(
    page_title="Obsidianizer",
    page_icon="🧠",
    layout="centered",
)

st.title("Obsidianizer")
st.caption("Ask your vault. Local only. Read-only by default.")

if st.button("↻ Refresh vault"):
    with st.spinner("Checking Obsidian for changes..."):
        result = subprocess.run(
            [sys.executable, "refresh_vault.py"],
            capture_output=True,
            text=True,
        )

    if result.returncode == 0:
        output = result.stdout

        if "Vault index is already current" in output:
            st.success("Vault is already current.")
        else:
            new = changed = deleted = chunks = "0"

            for line in output.splitlines():
                stripped = line.strip()

                if stripped.startswith("New:"):
                    new = stripped.split(":", 1)[1].strip()

                elif stripped.startswith("Changed:"):
                    changed = stripped.split(":", 1)[1].strip()

                elif stripped.startswith("Deleted:"):
                    deleted = stripped.split(":", 1)[1].strip()

                elif stripped.startswith("Chunks requiring embeddings:"):
                    chunks = stripped.split(":", 1)[1].strip()

            st.success(
                f"Vault refreshed · "
                f"{new} new · "
                f"{changed} changed · "
                f"{deleted} deleted · "
                f"{chunks} chunks embedded"
            )
    else:
        st.error("Vault refresh failed.")
        with st.expander("Show refresh error"):
            st.code(result.stderr or result.stdout)

if "messages" not in st.session_state:
    st.session_state.messages = []


def obsidian_url(relative_path):
    full_path = VAULT / relative_path
    encoded = quote(str(full_path), safe="")
    return f"obsidian://open?path={encoded}"


def split_memory_proposal(reply):
    start_marker = "[[MEMORY_PROPOSAL]]"
    end_marker = "[[/MEMORY_PROPOSAL]]"

    if start_marker not in reply or end_marker not in reply:
        return reply, None

    before, rest = reply.split(start_marker, 1)
    proposal, after = rest.split(end_marker, 1)

    clean_reply = (before.rstrip() + "\n" + after.lstrip()).strip()
    proposal = proposal.strip()

    return clean_reply, proposal or None



def explicit_memory_proposal(prompt):
    lower = prompt.lower().strip()

    durable_starts = (
        "i prefer ",
        "i always ",
        "i usually ",
        "i avoid ",
        "i never ",
        "my preference is ",
        "my default is ",
        "remember that ",
        "from now on ",
    )

    if lower.startswith(durable_starts):
        return prompt.strip()

    return None


def render_assistant_reply(reply):
    clean_reply, proposal = split_memory_proposal(reply)

    section_names = {
        "DIRECT",
        "RELATED THINKING",
        "REFERENCE MATERIAL",
    }

    for line in clean_reply.splitlines():
        stripped = line.strip()

        if not stripped:
            st.write("")
            continue

        if stripped in section_names:
            st.markdown(f"### {stripped.title()}")
            continue

        if stripped.endswith(".md"):
            url = obsidian_url(stripped)

            st.markdown(
                f'<a href="{url}" '
                f'style="text-decoration:none;font-weight:600;">'
                f'Open in Obsidian ↗</a>',
                unsafe_allow_html=True,
            )

            st.caption(stripped)
            continue

        st.markdown(line)

    if proposal:
        proposal_id = hashlib.sha1(
            reply.encode("utf-8")
        ).hexdigest()[:12]

        status_key = f"memory_status_{proposal_id}"
        status = st.session_state.get(status_key)

        st.divider()
        st.markdown("**Possible memory**")
        st.markdown(proposal)

        if status == "saved":
            st.success("✓ Saved to AI memory")

        elif status == "rejected":
            st.caption("Memory dismissed.")

        else:
            accept_col, reject_col = st.columns(2)

            if accept_col.button(
                "Accept memory",
                key=f"accept_memory_{proposal_id}",
                use_container_width=True,
            ):
                try:
                    save_memory(proposal)
                    st.session_state[status_key] = "saved"
                    st.rerun()
                except Exception as exc:
                    st.error(f"Memory save failed: {exc}")

            if reject_col.button(
                "Reject",
                key=f"reject_memory_{proposal_id}",
                use_container_width=True,
            ):
                st.session_state[status_key] = "rejected"
                st.rerun()


def ask_vault(question):
    result = subprocess.run(
        [sys.executable, "ask_gemma.py", "--quiet", question],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"Gemma failed:\n\n{result.stderr}"

    return result.stdout.strip()


def surface_notes(topic):
    result = subprocess.run(
        [sys.executable, "semantic_search.py", topic],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"Search failed:\n\n{result.stderr}"

    return result.stdout.strip()


def topic_stats(topic):
    result = subprocess.run(
        [sys.executable, "topic_stats.py", topic],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"Stats failed:\n\n{result.stderr}"

    return result.stdout.strip()


def did_i_write(topic):
    result = subprocess.run(
        [sys.executable, "did_i_write.py", topic],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"Search failed:\n\n{result.stderr}"

    return result.stdout.strip()


def route(prompt):
    lower = prompt.lower().strip()

    surface_prefixes = [
        "surface all notes about ",
        "surface notes about ",
        "show me all notes about ",
        "find all notes about ",
        "find notes about ",
    ]

    for prefix in surface_prefixes:
        if lower.startswith(prefix):
            topic = prompt[len(prefix):].strip().rstrip("?")

            if not topic:
                return "Tell me what topic you want me to surface."

            return surface_notes(topic)

    stats_prefixes = [
        "how many notes about ",
        "how many notes on ",
        "how many notes do i have about ",
        "how many notes do i have on ",
        "stats about ",
        "stats on ",
        "give me stats about ",
        "give me stats on ",
        "show me stats about ",
        "show me stats on ",
    ]

    for prefix in stats_prefixes:
        if lower.startswith(prefix):
            topic = prompt[len(prefix):].strip().rstrip("?")

            if not topic:
                return "Tell me which topic you want stats for."

            return topic_stats(topic)

    prefixes = [
        "did i ever write about ",
        "did i write about ",
        "have i ever written about ",
    ]

    for prefix in prefixes:
        if lower.startswith(prefix):
            topic = prompt[len(prefix):].strip().rstrip("?")

            if not topic:
                return "Tell me what topic you want me to look for."

            return did_i_write(topic)

    return ask_vault(prompt)


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_assistant_reply(message["content"])
        else:
            st.markdown(message["content"])


prompt = st.chat_input("Ask something about your vault...")

if prompt:
    st.session_state.messages.append({
        "role": "user",
        "content": prompt,
    })

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("Reading your vault..."):
        reply = route(prompt)

        proposal = explicit_memory_proposal(prompt)

        if proposal:
            clean_reply, _ = split_memory_proposal(reply)
            reply = (
                clean_reply
                + "\n\n[[MEMORY_PROPOSAL]]\n"
                + proposal
                + "\n[[/MEMORY_PROPOSAL]]"
            ).strip()

    with st.chat_message("assistant"):
        render_assistant_reply(reply)

    st.session_state.messages.append({
        "role": "assistant",
        "content": reply,
    })
