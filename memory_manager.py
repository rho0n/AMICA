"""
memory_manager.py — AMICA Memory System
Reads, formats, and updates the user's personal memory file.
All entries are timestamped. The LLM uses this as its long-term context.
"""
import json
import os
from datetime import datetime
from pathlib import Path

MEMORY_FILE = Path(__file__).parent / "memory.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def load_memory() -> dict:
    """Load and return the raw memory dict."""
    if not MEMORY_FILE.exists():
        return _default_memory()
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)


def save_memory(memory: dict) -> None:
    memory["last_updated"] = _now()
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)


def _default_memory() -> dict:
    return {
        "profile": {"name": "Friend", "notes": ""},
        "medications": [],
        "family_and_friends": [],
        "upcoming_events": [],
        "recent_notes": [],
        "last_updated": _now(),
    }


def _trunc(s: str, n: int) -> str:
    """Truncate string to n chars at a word boundary."""
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0]


def build_system_prompt(memory: dict) -> str:
    """
    Compact system prompt injected on every message. Target: under 300 tokens.
    """
    name = memory.get("profile", {}).get("name", "Friend")
    profile_notes = memory.get("profile", {}).get("notes", "")
    today = datetime.now().strftime("%A, %d %B %Y")

    lines = [
        f"You are AMICA, a warm caring AI companion. Today is {today}. You are speaking with {name}.",
        f"Be warm and gentle. Use short, simple sentences. Reply in 2-3 sentences maximum.",
    ]

    if profile_notes:
        lines.append(f"About {name}: {profile_notes}")

    meds = memory.get("medications", [])
    if meds:
        parts = []
        for m in meds:
            s = m.get("name", "?")
            if m.get("dose"):
                s += f" {m['dose']}"
            if m.get("time"):
                s += f" ({m['time']})"
            parts.append(s)
        lines.append("Medicines: " + "; ".join(parts))

    people = memory.get("family_and_friends", [])
    if people:
        parts = []
        for p in people:
            s = f"{p.get('name','?')} ({p.get('relation','')})"
            if p.get("traits"):
                s += f": {_trunc(p['traits'], 55)}"
            visits = p.get("recent_visits", [])
            if visits:
                s += f", last visit {visits[-1]}"
            parts.append(s)
        lines.append("People: " + "; ".join(parts))

    events = memory.get("upcoming_events", [])
    if events:
        today_dt = datetime.now().date()
        parts = []
        for e in sorted(events, key=lambda x: x.get("date", "")):
            try:
                days = (datetime.strptime(e["date"], "%Y-%m-%d").date() - today_dt).days
                when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
            except Exception:
                when = e.get("date", "?")
            parts.append(f"{e.get('description','?')} ({when})")
        lines.append("Events: " + "; ".join(parts))

    notes = memory.get("recent_notes", [])[-3:]
    if notes:
        parts = [_trunc(n.get("content", ""), 80) for n in notes]
        lines.append("Notes: " + "; ".join(parts))

    return "\n".join(lines)


def add_note(content: str) -> None:
    """Add a timestamped note to memory (called after conversations)."""
    memory = load_memory()
    notes = memory.setdefault("recent_notes", [])
    notes.append({"date": _now(), "content": content})
    # Keep last 50 notes
    memory["recent_notes"] = notes[-50:]
    save_memory(memory)


def add_event(date: str, description: str) -> None:
    """Add an upcoming event."""
    memory = load_memory()
    events = memory.setdefault("upcoming_events", [])
    events.append({"date": date, "description": description, "added": _now()})
    save_memory(memory)


def add_person(name: str, relation: str = "", traits: str = "") -> None:
    """Add or update a family member / friend."""
    memory = load_memory()
    people = memory.setdefault("family_and_friends", [])
    # Update if exists
    for p in people:
        if p.get("name", "").lower() == name.lower():
            if relation:
                p["relation"] = relation
            if traits:
                p["traits"] = traits
            p["updated"] = _now()
            save_memory(memory)
            return
    people.append({
        "name": name,
        "relation": relation,
        "traits": traits,
        "recent_visits": [],
        "added": _now(),
    })
    save_memory(memory)


def log_visit(person_name: str) -> None:
    """Log that a family member visited today."""
    memory = load_memory()
    for p in memory.get("family_and_friends", []):
        if p.get("name", "").lower() == person_name.lower():
            visits = p.setdefault("recent_visits", [])
            visits.append(datetime.now().strftime("%Y-%m-%d"))
            p["recent_visits"] = visits[-10:]  # keep last 10
            save_memory(memory)
            return


def get_profile_name() -> str:
    memory = load_memory()
    return memory.get("profile", {}).get("name", "Friend")
