import json
import os
from datetime import datetime, timezone

# Memory is stored as a simple JSON file in the project root
MEMORY_FILE = "david_memory.json"


def _load():
    """Read all saved memories. Returns a list (empty if none yet)."""
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_all(memories):
    """Write the full memory list back to disk."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, indent=2, ensure_ascii=False)


def save_memory(note: str, topic: str = "general") -> str:
    """Save a note to David's long-term memory. Use this to remember an insight,
    a market analysis, a conclusion, or anything the user asks you to remember."""
    memories = _load()
    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "topic": topic.lower().strip(),
        "note": note.strip(),
    }
    memories.append(entry)
    _save_all(memories)
    return f"Saved to memory under '{entry['topic']}' ({entry['date']})."


def recall_memory(topic: str = "") -> str:
    """Recall past notes from David's memory. If a topic is given, returns notes
    matching that topic. If left blank, returns all recent memories. Use this at
    the start of analysis to check what you concluded before."""
    memories = _load()
    if not memories:
        return "No memories saved yet."

    if topic:
        # Normalise: treat spaces and underscores the same, match on any word
        topic_l = topic.lower().strip().replace("_", " ")
        words = [w for w in topic_l.split() if len(w) > 2]
        matches = []
        for m in memories:
            haystack = (m["topic"] + " " + m["note"]).lower().replace("_", " ")
            # Match if the full phrase OR any significant word appears
            if topic_l in haystack or any(w in haystack for w in words):
                matches.append(m)
    else:
        matches = memories

    if not matches:
        return f"No memories found for '{topic}'."

    # Show most recent first, cap at 10 so it doesn't flood the context
    matches = matches[-10:]
    lines = [f"[{m['date']}] ({m['topic']}) {m['note']}" for m in matches]
    return "\n".join(lines)

def delete_memory(search: str) -> str:
    """Delete saved memories that match a search term (matches topic or note text).
    Use when the user asks you to forget, remove, or update something. Be careful —
    this permanently removes matching notes."""
    memories = _load()
    if not memories:
        return "No memories to delete."

    term = search.lower().strip().replace("_", " ")
    kept = []
    removed = []
    for m in memories:
        haystack = (m["topic"] + " " + m["note"]).lower().replace("_", " ")
        if term in haystack:
            removed.append(m)
        else:
            kept.append(m)

    if not removed:
        return f"No memories matched '{search}'. Nothing deleted."

    _save_all(kept)
    lines = [f"Deleted {len(removed)} memory(ies) matching '{search}':"]
    for m in removed:
        lines.append(f"  - [{m['date']}] ({m['topic']}) {m['note'][:60]}")
    return "\n".join(lines)