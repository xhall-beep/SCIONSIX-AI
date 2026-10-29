#!/usr/bin/env python3
"""The Naming Gate — name approval enforced as code.

SCIONSIX-AI's idea content named three pieces the merged console never
built: a Six-Scion orchestrator, a Naming Gate, and an Agent Foundry.
This module is the Gate.

Why a gate at all: in this estate a name is a commitment. A product
name goes on Stripe links, storefront pages, and trademark searches; an
agent name goes on the bus and into charters. Renaming after launch is
expensive and sometimes impossible, so the check happens BEFORE
anything registers — and nothing registers without passing. That is
the whole design: `foundry.forge()` calls `check_name()` first, and a
failure is a refusal with the violated rules named, never a warning
that can be clicked through.

The gate follows the governance pattern held in sovereign-vault
(`policy.py` / `policy.json`): every rule has an id, every refusal
names its rule, and anything not clearly allowed fails closed
(SV-DEFAULT-DENY's local equivalent is NG-REGISTRY below). The vault's
files hold that pattern; the concrete name rules are defined here,
derived from the estate's own naming practice:

  * Names already in use are registered (name_registry.json, shipped
    beside this file) — estate products (HookForge, Dark Engine, ...),
    estate agents (Weave, Apex, Acore, ...), the six founding Scion
    offices, and known external collisions (e.g. the US "VOID &
    VESSEL" registration, which is NOT the owner's).
  * A new name must not collide with, or sit confusingly inside, a
    registered one.
  * A new name must not squat on a reserved root (Scion, Sovereign,
    ApexYX, ORI) — those lines belong to their registered entries.
  * A new name must not be a bare generic word ("Agent", "Bot",
    "Orchestrator"): a name that describes a category cannot be a
    name.
  * A new name must be well-formed: letters/digits/spaces/hyphens,
    starting with a letter.

Registry entries that predate the gate (the founding roster, existing
products) are grandfathered: they are IN the registry as approved, and
the gate judges only new proposals against them.

Python 3 standard library only. No secrets, by construction.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY_PATH = os.path.join(HERE, "name_registry.json")

# Roots that belong to existing registered lines. A NEW name containing
# one of these as a whole word is refused unless the full name is
# itself already registered (in which case the collision rules answer
# first). These are the estate's flagship lines, per the repos
# themselves: SCION SIX, the Sovereign vault/concierge line, ApexYX,
# and the original ORI SCION node name.
RESERVED_ROOTS = ("scion", "sovereign", "apexyx", "ori")

# A name that is ONLY one of these words names a category, not a thing.
GENERIC_WORDS = (
    "agent", "agents", "ai", "app", "assistant", "bot", "console",
    "helper", "manager", "orchestrator", "system", "tool",
)

_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z0-9 '\-]*$")
MIN_LEN = 3
MAX_LEN = 40


# ---------------------------------------------------------------- registry

def load_registry(path=None):
    """Load the name registry. Returns None if the file is absent —
    callers that must decide (the gate) treat that as fail-closed."""
    path = path or DEFAULT_REGISTRY_PATH
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "names" not in data:
        raise ValueError("registry at %s has no 'names' list" % path)
    return data


def save_registry(data, path=None):
    """Write the registry back (sorted keys, stable formatting)."""
    path = path or DEFAULT_REGISTRY_PATH
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def normalize(name):
    """Comparison form: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def word_set(name):
    """The set of significant words in a name (for similarity checks)."""
    return set(re.findall(r"[a-z0-9]+", str(name).lower()))


# ---------------------------------------------------------------- the gate

def check_name(name, kind="agent", registry=None, registry_path=None):
    """Run the Naming Gate over a proposed name.

    Returns {"ok": bool, "name": name, "kind": kind,
             "violations": [{"rule": <id>, "detail": <why>}, ...]}.
    ok is True only when violations is empty. Pure check — registers
    nothing, writes nothing.
    """
    violations = []

    def refuse(rule, detail):
        violations.append({"rule": rule, "detail": detail})

    # NG-REGISTRY — fail closed when collisions cannot be checked.
    if registry is None:
        registry = load_registry(registry_path)
        if registry is None:
            refuse("NG-REGISTRY",
                   "name registry not found at %s — the gate cannot "
                   "check collisions, so it fails closed"
                   % (registry_path or DEFAULT_REGISTRY_PATH))
            return {"ok": False, "name": name, "kind": kind,
                    "violations": violations}

    entries = registry.get("names", [])

    # NG-FORMAT — the name must be a name.
    text = "" if name is None else str(name)
    if not (MIN_LEN <= len(text.strip()) <= MAX_LEN) \
            or not _NAME_OK.match(text.strip()):
        refuse("NG-FORMAT",
               "a name is %d-%d characters, letters/digits/spaces/"
               "hyphens, starting with a letter; %r is not"
               % (MIN_LEN, MAX_LEN, text))

    norm = normalize(text)
    words = word_set(text)

    # NG-COLLISION — identical or confusingly contained in a registered
    # name (either direction: the proposal inside a registered name, or
    # a registered name inside the proposal).
    for entry in entries:
        other = entry.get("name", "")
        if normalize(other) == norm and norm:
            refuse("NG-COLLISION",
                   "%r is already registered (%s, %s)"
                   % (text, entry.get("kind", "?"),
                      entry.get("status", "?")))
            break
        o_words = word_set(other)
        if words and o_words and (words <= o_words or o_words <= words):
            refuse("NG-COLLISION",
                   "%r is confusingly similar to registered %r "
                   "(shared word set)" % (text, other))
            break

    # NG-RESERVED — flagship roots belong to their registered lines.
    for root in RESERVED_ROOTS:
        if root in words:
            refuse("NG-RESERVED",
                   "%r uses the reserved root %r — that line's names "
                   "are already registered and new ones are the "
                   "owner's word, not the gate's" % (text, root))
            break

    # NG-GENERIC — a bare category word is not a name.
    if len(words) == 1 and next(iter(words), "") in GENERIC_WORDS:
        refuse("NG-GENERIC",
               "%r is a bare generic word — it names a category, "
               "not a thing" % text)

    return {"ok": not violations, "name": name, "kind": kind,
            "violations": violations}


def is_registered(name, registry=None, registry_path=None):
    """True when `name` (any casing/spacing) is in the registry."""
    if registry is None:
        registry = load_registry(registry_path)
        if registry is None:
            return False
    norm = normalize(name)
    return any(normalize(e.get("name", "")) == norm
               for e in registry.get("names", []))
