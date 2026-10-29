#!/usr/bin/env python3
"""The Six Scions — SCION SIX as an orchestrator, not just a console.

SCIONSIX-AI's idea content described a Six-Scion orchestrator. The
merged console (console.py) built the executor, the safety layer, and
a generic bus worker — but no scions. This module is the roster:

  Six offices, each owning a kind of work. The repo's own artefacts
  supply the taxonomy: the SCIONSIX CONTROL PLANE planner in
  segment2.sh decomposed requests into battery / location / network /
  sms / call / whoami / file queries and update / install / uninstall
  actions; console.py added streamed command execution, verification
  of the seal chain, and bus task work. Grouped, those are six kinds
  of work, and each kind gets exactly one scion:

    I   Runner  — cmd, exec            general command execution
    II  Scout   — recon, scan          read-only discovery queries
    III Mason   — build, install,      build / package / system work
                  update
    IV  Warden  — verify, audit        seal-chain verification,
                                       health, safety audits
    V   Keeper  — registry, state      name-registry upkeep, state
                                       snapshots, registrations
    VI  Herald  — announce, notify     announcements and reports

  (The source docs name the offices' work but never named the six
  themselves; the names here are the founding roster, registered in
  name_registry.json as grandfathered approved entries. Renaming an
  office is a registry edit, not a code change — dispatch and claim
  logic keys off work kinds and scion ids, not display names.)

How work flows:

  * `dispatch(work, command)` is the orchestrator: it routes by work
    kind to the one scion that owns that kind, safety-checks the
    command through the console's blocklist BEFORE posting (a blocked
    command is refused, never posted), and posts a bus row of kind
    "scion-task" with tgt=<scion id> and a JSON envelope
    {"work": ..., "command": ...} as its text.
  * `work_once(scion_id)` is a scion on duty: it scans pending
    "scion-task" rows and claims ONLY rows addressed to it whose work
    kind is inside its own claim filter (both conditions — a row
    routed to another office, or carrying a kind outside the filter,
    is left for its owner). Claiming is the bus's exactly-once claim.
    The claimed command runs through the console's SAME streamed,
    safety-checked executor; output is posted back as a sealed
    "result" row and the task is closed. A blocked command injected
    onto the bus by another tool is closed with the refusal as its
    result — never executed (the console worker's rule, kept).
  * `pending()` / `roster()` report the queue and the six.

Everything runs on the verified ApexYX bus through console.py's
connection helpers; the bus's own exec tier is never invoked.

Python 3 standard library only. No secrets, by construction.
"""

import json

import console

TASK_KIND = "scion-task"

# The founding roster. id -> office. work_kinds is the claim filter:
# the set of work kinds this scion may claim, and the set dispatch()
# routes to it. The six filters partition the work-kind space.
SCIONS = {
    "runner": {
        "name": "Runner",
        "numeral": "I",
        "mandate": "General command execution: run the command, "
                   "stream it, report its real outcome.",
        "work_kinds": ("cmd", "exec"),
    },
    "scout": {
        "name": "Scout",
        "numeral": "II",
        "mandate": "Read-only discovery: battery, network, location, "
                   "file and identity queries — look, never change.",
        "work_kinds": ("recon", "scan"),
    },
    "mason": {
        "name": "Mason",
        "numeral": "III",
        "mandate": "Build and system work: builds, installs, updates "
                   "— the changes the other offices only describe.",
        "work_kinds": ("build", "install", "update"),
    },
    "warden": {
        "name": "Warden",
        "numeral": "IV",
        "mandate": "Verification and audit: seal-chain checks, health "
                   "probes, safety audits. Trusts nothing unverified.",
        "work_kinds": ("verify", "audit"),
    },
    "keeper": {
        "name": "Keeper",
        "numeral": "V",
        "mandate": "Registry and state: name-registry upkeep, state "
                   "snapshots, Foundry registrations.",
        "work_kinds": ("registry", "state"),
    },
    "herald": {
        "name": "Herald",
        "numeral": "VI",
        "mandate": "Announcements: registrations, completions and "
                   "reports, posted where the estate reads them.",
        "work_kinds": ("announce", "notify"),
    },
}

# work kind -> owning scion id (built from the roster; the partition
# is asserted in the test suite: every kind has exactly one owner).
ROUTES = {}
for _sid, _office in SCIONS.items():
    for _kind in _office["work_kinds"]:
        ROUTES[_kind] = _sid

NODE_PREFIX = "scion-"


def node_of(scion_id):
    """Bus node name a scion acts under."""
    return NODE_PREFIX + scion_id


def roster():
    """The six offices, as a list of plain dicts."""
    return [
        {"id": sid, "name": o["name"], "numeral": o["numeral"],
         "mandate": o["mandate"], "work_kinds": list(o["work_kinds"]),
         "node": node_of(sid)}
        for sid, o in SCIONS.items()
    ]


def owner_of(work):
    """Which scion owns a work kind (None when no office claims it)."""
    return ROUTES.get(str(work).strip().lower())


def _envelope(work, command):
    return json.dumps({"work": work, "command": command,
                       "via": "scions"})


def _parse_envelope(text):
    try:
        env = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(env, dict) or "command" not in env:
        return None
    return env


# ---------------------------------------------------------------- dispatch

def dispatch(work, command, mesh_dir_arg=None):
    """Route a unit of work to its owning scion and post it to the bus.

    The command is safety-checked BEFORE posting (console rules): a
    blocked command raises BlockedCommandError and nothing is posted.
    An unowned work kind raises ValueError — the orchestrator refuses
    work no office is chartered for, rather than dropping it in a
    generic queue. Returns {"task_id", "scion", "work"}.
    """
    work = str(work).strip().lower()
    scion_id = owner_of(work)
    if scion_id is None:
        raise ValueError(
            "no scion owns work kind %r (chartered kinds: %s)"
            % (work, ", ".join(sorted(ROUTES))))
    if command is None or not str(command).strip():
        raise ValueError("command must not be empty")
    console.ensure_allowed(command)
    bus, conn, _ = console._connect(mesh_dir_arg)
    try:
        task_id = int(bus.post(conn, node_of(scion_id), TASK_KIND,
                               _envelope(work, str(command).strip()),
                               scion_id))
    finally:
        conn.close()
    return {"task_id": task_id, "scion": scion_id, "work": work}


def dispatch_to(scion_id, work, command, mesh_dir_arg=None):
    """Post work to a NAMED scion. Refuses when the work kind is
    outside that scion's charter (its claim filter) — an office is
    never handed work it is not chartered for."""
    scion_id = str(scion_id).strip().lower()
    office = SCIONS.get(scion_id)
    if office is None:
        raise ValueError("unknown scion %r (roster: %s)"
                         % (scion_id, ", ".join(sorted(SCIONS))))
    work = str(work).strip().lower()
    if work not in office["work_kinds"]:
        raise ValueError(
            "%s is not chartered for work kind %r (its kinds: %s)"
            % (office["name"], work, ", ".join(office["work_kinds"])))
    if command is None or not str(command).strip():
        raise ValueError("command must not be empty")
    console.ensure_allowed(command)
    bus, conn, _ = console._connect(mesh_dir_arg)
    try:
        task_id = int(bus.post(conn, node_of(scion_id), TASK_KIND,
                               _envelope(work, str(command).strip()),
                               scion_id))
    finally:
        conn.close()
    return {"task_id": task_id, "scion": scion_id, "work": work}


# ---------------------------------------------------------------- on duty

def _pending_rows(bus, conn):
    rows = bus.export(conn, kind=TASK_KIND, since=0, limit=100000)
    return [r for r in rows if r["state"] == "new"]


def pending(mesh_dir_arg=None):
    """Pending scion-tasks, grouped by owning scion id."""
    bus, conn, _ = console._connect(mesh_dir_arg)
    try:
        rows = _pending_rows(bus, conn)
    finally:
        conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["for"], []).append(int(r["id"]))
    return out


def work_once(scion_id, mesh_dir_arg=None, on_line=None):
    """Claim and execute this scion's oldest pending task, once.

    The claim filter is enforced twice: the row must be ADDRESSED to
    this scion (tgt == scion id) AND its envelope's work kind must be
    inside this scion's charter. Anything else is left pending for
    its owner. Returns the same shape as console.worker_once, plus
    "scion" and "work"; None when nothing of this scion's waits.
    """
    scion_id = str(scion_id).strip().lower()
    office = SCIONS.get(scion_id)
    if office is None:
        raise ValueError("unknown scion %r (roster: %s)"
                         % (scion_id, ", ".join(sorted(SCIONS))))
    node = node_of(scion_id)
    bus, conn, _ = console._connect(mesh_dir_arg)
    try:
        mine = []
        for r in _pending_rows(bus, conn):
            if r["for"] != scion_id:
                continue
            env = _parse_envelope(r["text"])
            if env is None:
                continue
            if str(env.get("work", "")).lower() \
                    not in office["work_kinds"]:
                continue
            mine.append((r, env))
        if not mine:
            return None
        task, env = mine[0]
        if not bus.claim(conn, int(task["id"]), node):
            return None  # another instance of this office won it
        command = str(env["command"])
        allowed, rule, why = console.check_command(command)
        if not allowed:
            result_text = ("BLOCKED by rule %r: %s — command not "
                           "executed" % (rule, why))
            result_id = int(bus.post(conn, node, "result", result_text,
                                     str(task["id"])))
            bus.done(conn, int(task["id"]), node)
            return {"task_id": int(task["id"]), "scion": scion_id,
                    "work": env.get("work"), "command": command,
                    "exit_code": None, "blocked": rule,
                    "result_id": result_id}
        res = console.run_streamed(command, on_line=on_line)
        result_id = int(bus.post(conn, node, "result",
                                 console._format_result(res),
                                 str(task["id"])))
        bus.done(conn, int(task["id"]), node)
        return {"task_id": int(task["id"]), "scion": scion_id,
                "work": env.get("work"), "command": command,
                "exit_code": res.exit_code, "blocked": None,
                "result_id": result_id}
    finally:
        conn.close()
