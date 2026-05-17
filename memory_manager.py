"""
memory_manager.py — AMICA Memory System
Reads, formats, and updates the user's personal memory file.
"""
import json
import re
from datetime import datetime, timedelta, date
from pathlib import Path

MEMORY_FILE = Path(__file__).parent / "memory.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def load_memory() -> dict:
    if not MEMORY_FILE.exists():
        return _default_memory()
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)


def save_memory(memory: dict) -> None:
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
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0]


_TIME_WORD = re.compile(r'\b(morning|evening|night|afternoon|bedtime|daily|twice)\b', re.I)


def _med_time(t: str) -> str:
    """Extract the core time-of-day word from a verbose time string."""
    m = _TIME_WORD.search(t)
    return m.group(1).lower() if m else _trunc(t, 8)


def build_system_prompt(memory: dict, client_time: str = "") -> str:
    """Build a compact system prompt targeting ~130 tokens to stay under the 85s timeout.

    Prompt processing runs at ~349ms/token on the UNO Q. Every extra token costs ~350ms.
    Target: ≤130 tokens → ~45s prompt + ~38s generation = ~83s total, inside the 110s LLAMA_TIMEOUT.
    """
    name = memory.get("profile", {}).get("name", "Friend")
    profile_notes = memory.get("profile", {}).get("notes", "")
    try:
        now = datetime.fromisoformat(client_time) if client_time else datetime.now()
    except ValueError:
        now = datetime.now()
    today = now.strftime("%a %d %b %Y")

    lines = [
        f"You are AMICA, talking directly with {name}. Today is {today}. Warm and gentle. Always say 'you'/'your' when speaking to them — never use their name. Reply in 2-3 short sentences.",
        f"About {name}:",
    ]

    # ~18 tokens: profile notes, hard-capped at 70 chars
    if profile_notes:
        lines.append(_trunc(profile_notes, 70))

    # ~20 tokens: meds — name + dose + extracted time keyword
    meds = memory.get("medications", [])
    if meds:
        parts = []
        for m in meds[:5]:
            s = m.get('name', '?')
            if m.get("dose"): s += f" {m['dose']}"
            if m.get("time"): s += f" {_med_time(m['time'])}"
            parts.append(s)
        lines.append("Meds: " + ", ".join(parts))

    # people — most recently added/updated first, up to 10
    people = sorted(
        memory.get("family_and_friends", []),
        key=lambda p: p.get("updated", p.get("added", "")),
        reverse=True,
    )
    if people:
        parts = []
        for p in people[:10]:
            s = f"{p.get('name','?')} ({_trunc(p.get('relation',''), 15)}"
            traits = p.get('traits', '').strip()
            if traits:
                s += f", {_trunc(traits, 25)}"
            s += ")"
            parts.append(s)
        lines.append("People: " + ", ".join(parts))

    # upcoming events: up to 5, description capped at 28 chars
    events = memory.get("upcoming_events", [])
    if events:
        today_dt = now.date()
        upcoming = []
        no_date = []
        for e in sorted(events, key=lambda x: x.get("date", "~")):  # "~" sorts undated last
            ed = _event_date(e)
            if ed is not None:
                days = (ed - today_dt).days
                if days < 0:
                    continue
                when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days}d")
                upcoming.append(f"{_trunc(e.get('description','?'), 40)} ({when})")
                if len(upcoming) >= 5:
                    break
            else:
                no_date.append(_trunc(e.get('description','?'), 28))
        # include undated events if they fit
        all_events = upcoming + no_date[:max(0, 5 - len(upcoming))]
        if all_events:
            lines.append("Events: " + "; ".join(all_events))

    # Memory tag instruction — server parses this to save facts without a second LLM call.
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    lines.append(
        f"To save something to memory, append ONE tag — "
        f"[MEM:person|NAME|RELATION|TRAITS] or [MEM:event|{tomorrow}|DESC] or [MEM:med|NAME|DOSE|TIME]. "
        f"Eg: [MEM:person|Stacey|friend|likes gardening]"
    )

    return "\n".join(lines)


def add_note(content: str) -> None:
    memory = load_memory()
    notes = memory.setdefault("recent_notes", [])
    notes.append({"date": _now(), "content": content})
    memory["recent_notes"] = notes[-50:]
    save_memory(memory)


def add_medication(name: str, dose: str = "", time: str = "") -> None:
    memory = load_memory()
    meds = memory.setdefault("medications", [])
    for m in meds:
        if m.get("name", "").lower() == name.lower():
            if dose: m["dose"] = dose
            if time: m["time"] = time
            m["updated"] = _now()
            save_memory(memory)
            return
    meds.append({"name": name, "dose": dose, "time": time, "added": _now()})
    save_memory(memory)


def remove_medication(name: str) -> None:
    memory = load_memory()
    memory["medications"] = [
        m for m in memory.get("medications", [])
        if m.get("name", "").lower() != name.lower()
    ]
    save_memory(memory)


def add_event(date_str: str, description: str) -> None:
    memory = load_memory()
    events = memory.setdefault("upcoming_events", [])
    if any(e.get("description", "").lower() == description.lower() for e in events):
        return
    events.append({"date": date_str, "description": description, "added": _now()})
    save_memory(memory)


def remove_event(index: int) -> None:
    memory = load_memory()
    events = memory.get("upcoming_events", [])
    if 0 <= index < len(events):
        events.pop(index)
        memory["upcoming_events"] = events
        save_memory(memory)


def clean_old_events(days: int = 30) -> None:
    memory = load_memory()
    cutoff = (datetime.now() - timedelta(days=days)).date()
    before = len(memory.get("upcoming_events", []))
    memory["upcoming_events"] = [
        e for e in memory.get("upcoming_events", [])
        if _event_date(e) is None or _event_date(e) >= cutoff
    ]
    if len(memory["upcoming_events"]) < before:
        save_memory(memory)


_MEM_TAG_RE = re.compile(r'\[MEM:([^\]]+)\]', re.IGNORECASE)


def parse_mem_tag(response: str, client_time: str = "") -> bool:
    """Parse a [MEM:...] tag written by AMICA into memory.
    Format: [MEM:person|NAME|RELATION] or [MEM:event|YYYY-MM-DD|DESC] or [MEM:med|NAME|DOSE|TIME]
    """
    m = _MEM_TAG_RE.search(response)
    if not m:
        return False
    parts = [p.strip() for p in m.group(1).split('|')]
    if len(parts) < 2:
        return False
    kind = parts[0].lower()
    if kind == "person" and len(parts) >= 3:
        name, relation = parts[1], parts[2]
        traits = parts[3] if len(parts) > 3 else ""
        if len(name) >= 2:
            add_person(name.title(), relation.lower(), traits)
            return True
    elif kind == "event" and len(parts) >= 3:
        date_str, description = parts[1], parts[2]
        add_event(date_str, description)
        return True
    elif kind == "med" and len(parts) >= 2:
        name = parts[1]
        dose = parts[2] if len(parts) > 2 else ""
        time_str = parts[3] if len(parts) > 3 else ""
        if len(name) >= 2:
            add_medication(name.title(), dose, time_str)
            return True
    return False


def _event_date(e: dict):
    try:
        return datetime.strptime(e["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None


def add_person(name: str, relation: str = "", traits: str = "") -> None:
    memory = load_memory()
    people = memory.setdefault("family_and_friends", [])
    for p in people:
        if p.get("name", "").lower() == name.lower():
            if relation: p["relation"] = relation
            if traits: p["traits"] = traits
            p["updated"] = _now()
            save_memory(memory)
            return
    people.append({"name": name, "relation": relation, "traits": traits, "added": _now()})
    save_memory(memory)


def remove_person(name: str) -> None:
    memory = load_memory()
    memory["family_and_friends"] = [
        p for p in memory.get("family_and_friends", [])
        if p.get("name", "").lower() != name.lower()
    ]
    save_memory(memory)


def update_profile(name: str = "", notes: str = "") -> None:
    memory = load_memory()
    if name: memory.setdefault("profile", {})["name"] = name
    if notes is not None: memory.setdefault("profile", {})["notes"] = notes
    save_memory(memory)


def merge_extracted_facts(facts: dict) -> None:
    if not facts:
        return
    memory = load_memory()
    changed = False

    for med in facts.get("medications", []):
        name = med.get("name", "").strip()
        if len(name) < 2:
            continue
        meds = memory.setdefault("medications", [])
        if not any(m.get("name", "").lower() == name.lower() for m in meds):
            meds.append({"name": name, "dose": med.get("dose", ""), "time": med.get("time", ""), "added": _now()})
            changed = True

    for person in facts.get("people", []):
        name = person.get("name", "").strip()
        if len(name) < 2:
            continue
        people = memory.setdefault("family_and_friends", [])
        if not any(p.get("name", "").lower() == name.lower() for p in people):
            people.append({"name": name, "relation": person.get("relation", ""), "traits": person.get("traits", ""), "added": _now()})
            changed = True

    for event in facts.get("events", []):
        desc = event.get("description", "").strip()
        if not desc:
            continue
        events = memory.setdefault("upcoming_events", [])
        if not any(e.get("description", "").lower() == desc.lower() for e in events):
            events.append({"date": event.get("date", ""), "description": desc, "added": _now()})
            changed = True

    if changed:
        save_memory(memory)


_EXPLICIT_TRIGGERS = re.compile(
    r"(remind\s+me(?:\s+to|\s+that|\s+about)?"   # "remind me", "remind me to", "remind me about"
    r"|remember(?:\s+that|\s+i|\s+that\s+i)?"     # "remember", "remember that"
    r"|don'?t\s+forget"
    r"|please\s+remember"
    r"|make\s+a\s+note"
    r"|set\s+(?:a\s+)?reminder"
    r"|add\s+(?:a\s+)?reminder"
    r"|note\s+that"
    r"|i\s+need\s+to\s+remember"
    r"|(?:please\s+)?add\s+(?:that\s+)?"
    r"|(?:please\s+)?schedule\s+"
    r"|(?:please\s+)?put\s+(?:that\s+)?)",
    re.IGNORECASE,
)

_FACT_CLEANUP = re.compile(
    r"\s+to\s+(?:my\s+)?(?:events?|calendar|schedule|diary|reminders?)",
    re.IGNORECASE,
)

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_MED_RE = re.compile(
    r"\bi(?:'?m)? (?:take|taking|on)\s+(?:a\s+)?([a-z][a-z\s\-]{1,30}?)(?:\s+(\d+\s*mg|\d+\s*mcg|\d+\s*ml))?"
    r"(?:\s+(?:every\s+)?(morning|evening|night|afternoon|at night|at morning|daily|twice a day|once a day))?",
    re.IGNORECASE,
)

# Matches person introductions anywhere in the fact text (search, not match)
# Patterns: "Tom is my son" / "Stacey my new neighbour" / "my neighbour Stacey" / "Sarah as my friend"
_PERSON_RE = re.compile(
    r"\b(?P<n1>[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+is\s+my\s+(?P<r1>\w+)"
    r"|\b(?P<n2>[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+as\s+(?:my\s+)?(?P<r2>\w+)"
    r"|\bmy\s+(?:new\s+)?(?P<r3>\w+)\s+is\s+(?P<n3>[A-Z][a-z]+(?: [A-Z][a-z]+)?)"
    r"|\bmy\s+(?:new\s+)?(?P<r4>\w+)\s+(?P<n4>[A-Z][a-z]+(?: [A-Z][a-z]+)?)"
    r"|\b(?P<n5>[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+(?:is\s+)?my\s+(?:new\s+)?(?P<r5>\w+)",
    re.IGNORECASE,
)

_BAD_RELATIONS = {"the", "a", "an", "my", "your", "our", "their", "this", "that", "it",
                  "new", "own", "very", "so", "quite", "really"}

# Relationship words that must appear before a name will be saved in Pass 1 (no trigger required).
# Prevents casual mentions like "my daughter yesterday" from saving "Yesterday" as a person.
_KNOWN_RELATION_WORDS = {
    "son", "daughter", "wife", "husband", "partner", "boyfriend", "girlfriend",
    "brother", "sister", "sibling", "mother", "father", "mum", "mom", "dad",
    "gran", "grandma", "grandpa", "grandfather", "grandmother", "nana", "papa",
    "granny", "grandad", "granddad", "nephew", "niece", "uncle", "aunt", "auntie",
    "cousin", "friend", "neighbour", "neighbor", "colleague", "workmate",
    "carer", "helper", "nurse", "doctor", "dentist", "therapist", "acquaintance",
}

# Typo-tolerant tomorrow: matches "tomorrow", "tomorrwo", "tomorow", "tommorow"
_TOMORROW_RE = re.compile(r'\bto?mmo?rro?w\b', re.I)

# Catches "I met a new friend Stacey", "met my neighbour James", "know a woman called Helen"
_MET_PERSON_RE = re.compile(
    r'\b(?:met|meeting|know|knowing|found|finding|introduced\s+to)\s+'
    r'(?:\w+\s+){0,3}?'                        # 0-3 filler words ("a new", "my neighbour", etc.)
    r'(?:(?P<prel>friend|neighbour|neighbor|colleague|acquaintance|carer|helper|doctor|nurse)\s+)?'
    r'(?:called\s+|named\s+)?'
    r'(?P<pname>[A-Z][a-z]+(?: [A-Z][a-z]+)?)', # the name itself
    re.IGNORECASE,
)

# Common words that look like names but aren't (verbs, articles, pronouns)
_NON_NAME_PREFIX = re.compile(
    r'^(?:met|got|saw|found|have|had|is|was|are|were|been|a|an|the|'
    r'my|our|your|his|her|their|its|this|that|these|those|'
    r'i|he|she|they|we|it|me|him|us|you)\s+',
    re.IGNORECASE,
)

# Filler words at the start of a message that aren't part of the content
_FILLER_PREFIX = re.compile(
    r'^(?:sorry[,.\s]+(?:i\s+(?:mean|meant)[,.\s]+)?|'
    r'i\s+(?:mean|meant|was\s+saying)[,.\s]+|'
    r'well[,.\s]+|ok(?:ay)?[,.\s]+|actually[,.\s]+|'
    r'oh[,.\s]+|right[,.\s]+|no[,.\s]+i\s+mean[,.\s]+)',
    re.IGNORECASE,
)


def append_profile_note(note: str) -> None:
    """Append a new fact to profile notes, avoiding duplicates."""
    memory = load_memory()
    existing = memory.get("profile", {}).get("notes", "")
    if note.lower() in existing.lower():
        return
    sep = "; " if existing else ""
    memory.setdefault("profile", {})["notes"] = existing + sep + note
    save_memory(memory)


def _extract_person_from_match(pm) -> tuple[str, str] | tuple[None, None]:
    """Extract (name, relation) from a _PERSON_RE match, or (None, None) if invalid."""
    gd = pm.groupdict()
    person_name = next((v for k, v in sorted(gd.items()) if k.startswith('n') and v), None)
    relation = next((v for k, v in sorted(gd.items()) if k.startswith('r') and v), None)
    if not person_name or not relation:
        return None, None
    while True:
        cleaned = _NON_NAME_PREFIX.sub('', person_name, count=1).strip()
        if cleaned == person_name:
            break
        person_name = cleaned
    return person_name.strip().title(), relation.strip().lower()


def parse_and_save_explicit_memory(msg: str, client_time: str = "") -> bool:
    """Parse explicit memory requests from chat and save to memory.json.

    Pass 1: Structural patterns run on the whole message — no trigger word needed.
    Pass 2: Trigger-based extraction (or bare "I …" statement) for events and notes.
    This two-pass design means misspelled trigger words never block person/med saves.
    """
    try:
        now = datetime.fromisoformat(client_time) if client_time else datetime.now()
    except ValueError:
        now = datetime.now()

    # ── Pass 1: Structural patterns on whole message ──────────────────────────
    # Runs BEFORE the trigger check so a typo like "rememeber" never blocks saves.

    # "Stacey my new neighbour" / "Tom is my son" — name+relation anywhere in msg
    pm = _PERSON_RE.search(msg)
    if pm:
        person_name, relation = _extract_person_from_match(pm)
        # Require a known relationship word so casual phrases like "my daughter
        # yesterday" don't save "Yesterday" as a person.  Also require Title Case
        # (person_name[0].isupper()) so common nouns matched by the regex are skipped.
        if (person_name and relation
                and len(person_name) >= 2 and len(relation) >= 2
                and relation not in _BAD_RELATIONS
                and relation in _KNOWN_RELATION_WORDS
                and person_name[0].isupper()):
            add_person(person_name, relation)
            return True

    # "I met a new friend Stacey" / "met my neighbour James"
    mp = _MET_PERSON_RE.search(msg)
    if mp:
        raw_name = mp.group('pname').strip()
        # Reject matches like "new friend" (lazy quantifier can grab them with IGNORECASE).
        # A real name must start with an uppercase letter in the original message.
        if raw_name[0].isupper():
            person_name = raw_name.title()
            relation = (mp.group('prel') or "friend").strip().lower()
            if len(person_name) >= 2 and person_name.lower() not in _BAD_RELATIONS:
                add_person(person_name, relation)
                return True

    # "I'm taking metformin 500mg morning"
    msg_lower = msg.lower()
    m2 = _MED_RE.search(msg_lower)
    if m2:
        name = m2.group(1).strip().rstrip("s").strip()
        dose = (m2.group(2) or "").strip()
        time_of_day = (m2.group(3) or "").strip()
        if len(name) >= 2 and name not in ("a", "an", "the", "some"):
            add_medication(name.title(), dose, time_of_day)
            return True

    # ── Pass 2: Trigger-based (or bare "I …") extraction ──────────────────────
    matches = list(_EXPLICIT_TRIGGERS.finditer(msg))
    has_trigger = bool(matches)

    if has_trigger:
        first = matches[0]
        fact = msg[first.end():].strip()

        # Decide whether the extracted fact is "uninformative":
        # - empty, or just "this/that/it"
        # - OR very short (≤2 words) with no date signal ("remind me schedule it")
        _ds_check = ["tomorrow", "today", "tonight", "next ", "on "] + _DAYS
        _uninformative = (
            not fact
            or fact.lower().rstrip('.,!? ') in ("this", "that", "it", "")
            or (len(fact.split()) <= 2 and not any(s in fact.lower() for s in _ds_check))
        )

        if _uninformative:
            # Real content is BEFORE the first trigger
            # e.g. "meeting Karl at 4pm tomorrow remind me schedule it"
            candidate = msg[:first.start()].strip()
            candidate = re.sub(r"^[^A-Za-z]+", "", candidate).strip()
            if candidate:
                fact = candidate

        if not fact:
            return False

        # Strip filler openers ("sorry I mean…", "well…") and leading "to "
        fact = _FILLER_PREFIX.sub('', fact).strip()
        fact = re.sub(r'^to\s+', '', fact, flags=re.IGNORECASE).strip()
        fact = _FACT_CLEANUP.sub("", fact).strip()
        if not fact:
            return False

    elif msg_lower.strip().startswith("i ") or msg_lower.strip().startswith("i'"):
        fact = msg.strip()
    else:
        return False

    lower = fact.lower()

    # ── Medication ───────────────────────────────────────────────────────────
    m2 = _MED_RE.search(lower)
    if m2:
        name = m2.group(1).strip().rstrip("s").strip()
        dose = (m2.group(2) or "").strip()
        time_of_day = (m2.group(3) or "").strip()
        if len(name) >= 2 and name not in ("a", "an", "the", "some"):
            add_medication(name.title(), dose, time_of_day)
            return True

    # ── Person via name+relation patterns ────────────────────────────────────
    pm = _PERSON_RE.search(fact)
    if pm:
        person_name, relation = _extract_person_from_match(pm)
        # In Pass 2 (trigger context) we don't require _KNOWN_RELATION_WORDS
        if (person_name and relation
                and len(person_name) >= 2 and len(relation) >= 2
                and relation not in _BAD_RELATIONS):
            add_person(person_name, relation)
            return True

    # ── Event (date signal present) ──────────────────────────────────────────
    event_date = _parse_date(lower, now)
    has_date = (
        bool(_TOMORROW_RE.search(lower))
        or any(sig in lower for sig in (["today", "tonight", "next ", "on "] + _DAYS))
    )
    if has_date:
        desc = fact[0].upper() + fact[1:]
        add_event(event_date, desc)
        return True

    # ── Person via "met/know/found [Name]" ───────────────────────────────────
    mp = _MET_PERSON_RE.search(fact)
    if mp:
        person_name = mp.group('pname').strip().title()
        relation = (mp.group('prel') or "friend").strip().lower()
        if len(person_name) >= 2 and person_name.lower() not in _BAD_RELATIONS:
            add_person(person_name, relation)
            return True

    # ── Profile note fallback — only when there was an explicit trigger ───────
    if has_trigger and (lower.startswith("i ") or lower.startswith("i'")):
        append_profile_note(fact[0].upper() + fact[1:])
        return True

    return False


def _parse_date(text: str, now: datetime) -> str:
    if "today" in text or "tonight" in text:
        return now.date().isoformat()
    if _TOMORROW_RE.search(text):
        return (now + timedelta(days=1)).date().isoformat()
    for i, day in enumerate(_DAYS):
        if day in text:
            days_ahead = (i - now.weekday()) % 7 or 7
            return (now + timedelta(days=days_ahead)).date().isoformat()
    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})\b", text)
    if m:
        try:
            d = date(now.year, int(m.group(2)), int(m.group(1)))
            if d < now.date():
                d = d.replace(year=now.year + 1)
            return d.isoformat()
        except ValueError:
            pass
    return ""


def get_profile_name() -> str:
    memory = load_memory()
    return memory.get("profile", {}).get("name", "Friend")
