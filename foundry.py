#!/usr/bin/env python3
"""The Agent Foundry — where new agents are made, through the Gate.

The third piece of SCIONSIX-AI's idea content. The Foundry takes an
agent definition built from a template, runs its proposed name
through the Naming Gate (naming_gate.py), and then:

  * on a PASS — registers the agent in the name registry (status
    "approved", origin "foundry", with its role and chartered work
    kinds recorded), and posts a bus announcement (kind "announce",
    from the Keeper's office — registry work is Keeper's charter —
    so every other office can see a new agent exists);
  * on a FAIL — refuses, returns the gate's named violations, and
    changes NOTHING: no registry write, no bus post. A name that
    fails the gate does not exist as far as the estate is concerned.

The definition itself is also validated against the Scion charter:
an agent may only be chartered for work kinds the six offices own
(scions.ROUTES) — the Foundry does not mint agents for unowned kinds
of work.

Registration is the point of no return by design: the registry is
append-only from code (entries are never edited or removed here),
because a registered name may already be printed on a storefront
somewhere.

Python 3 standard library only. No secrets, by construction.
"""

import re

import console
import naming_gate
import scions

ANNOUNCE_KIND = "announce"

# The template every agent definition starts from. forge() fills a
# copy; callers may pass a partial dict and the rest takes defaults.
TEMPLATE = {
    "name": "",            # proposed agent name — goes through the Gate
    "role": "",            # one line: what this agent is for
    "work_kinds": [],      # kinds from scions.ROUTES it is chartered for
    "kind": "agent",       # registry kind (the Foundry makes agents)
}


def _slug(text):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or "agent"


def new_definition(name, role, work_kinds):
    """A definition dict from the template, filled in."""
    definition = dict(TEMPLATE)
    definition.update({
        "name": name,
        "role": role,
        "work_kinds": [str(k).strip().lower() for k in work_kinds],
    })
    return definition


def _template_violations(definition):
    """Validate a definition's shape (before the Gate sees the name)."""
    out = []

    def refuse(detail):
        out.append({"rule": "FD-TEMPLATE", "detail": detail})

    if not str(definition.get("name", "")).strip():
        refuse("the definition has no name to put through the Gate")
    if not str(definition.get("role", "")).strip():
        refuse("the definition has no role — an agent without a stated "
               "purpose is not registered")
    kinds = definition.get("work_kinds") or []
    if not kinds:
        refuse("the definition charters no work kinds — an agent that "
               "may claim nothing is not registered")
    for kind in kinds:
        if str(kind).strip().lower() not in scions.ROUTES:
            refuse("work kind %r is owned by no scion office "
                   "(chartered kinds: %s)"
                   % (kind, ", ".join(sorted(scions.ROUTES))))
    return out


def forge(definition, registry_path=None, mesh_dir_arg=None,
          announce=True):
    """Create an agent: template check -> Naming Gate -> register.

    Returns {"ok": True, "entry": <registry entry>, "announce_id": id}
    on success, or {"ok": False, "violations": [...]} on refusal —
    with the registry file byte-identical and nothing posted.
    """
    definition = dict(TEMPLATE, **(definition or {}))

    problems = _template_violations(definition)
    if problems:
        return {"ok": False, "violations": problems}

    registry = naming_gate.load_registry(registry_path)
    gate = naming_gate.check_name(definition["name"], kind="agent",
                                  registry=registry)
    if not gate["ok"]:
        return {"ok": False, "violations": gate["violations"]}

    entry = {
        "name": str(definition["name"]).strip(),
        "kind": "agent",
        "status": "approved",
        "origin": "foundry",
        "role": str(definition["role"]).strip(),
        "work_kinds": [str(k).strip().lower()
                       for k in definition["work_kinds"]],
        "node": "agent-" + _slug(definition["name"]),
    }
    registry["names"].append(entry)
    naming_gate.save_registry(registry, registry_path)

    announce_id = None
    if announce:
        text = ("FOUNDRY: new agent %r registered — %s "
                "(chartered work: %s)"
                % (entry["name"], entry["role"],
                   ", ".join(entry["work_kinds"])))
        bus, conn, _ = console._connect(mesh_dir_arg)
        try:
            announce_id = int(bus.post(conn, scions.node_of("keeper"),
                                       ANNOUNCE_KIND, text, "foundry"))
        finally:
            conn.close()
    return {"ok": True, "entry": entry, "announce_id": announce_id}
