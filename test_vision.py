#!/usr/bin/env python3
"""SCION SIX vision build — verification suite (scions / gate / foundry).

Proves, hermetically (temp bus, temp registry — the shipped registry
is only ever READ here):
  (1) the roster: six offices whose claim filters partition the
      chartered work kinds (every kind owned exactly once).
  (2) the Naming Gate: a compliant name passes clean; a registered
      name, a normalized variant of one, a bare generic word, a
      reserved-root name, and a malformed name are each refused with
      the violated rule NAMED.
  (3) the Foundry end-to-end: a passing definition is registered in
      the registry file AND announced on the bus (seal chain INTACT);
      a gate-failing definition registers NOTHING (registry file
      byte-identical, no announcement); a malformed definition is
      refused by the template check.
  (4) a scion round-trip on a temp bus: dispatch routes cmd->Runner
      and verify->Warden; Runner's work_once completes ONLY its own
      task (the claim filter leaves Warden's untouched); Warden then
      completes its own; results hold the commands' real output; the
      seal chain verifies INTACT. Dispatch refuses an unowned work
      kind, a cross-charter assignment, and a blocklisted command
      (before posting).

Prints PASS/FAIL per check plus a summary; exits non-zero on any FAIL.
Run:  python3 test_vision.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import console  # noqa: E402
import foundry  # noqa: E402
import naming_gate  # noqa: E402
import scions  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%s: %s%s" % ("PASS" if ok else "FAIL", name,
                        (" — " + detail) if detail else ""))


def rules_of(verdict):
    return [v["rule"] for v in verdict.get("violations", [])]


# ---------------------------------------------------------------- (1) roster

def test_roster():
    kinds = [k for o in scions.SCIONS.values() for k in o["work_kinds"]]
    ok = (
        len(scions.SCIONS) == 6
        and len(kinds) == len(set(kinds)) == len(scions.ROUTES)
        and all(scions.owner_of(k) for k in kinds)
    )
    record("roster: six offices, claim filters partition the work kinds",
           ok, "%d kinds, each owned exactly once" % len(kinds))


# ---------------------------------------------------------------- (2) the gate

def test_gate():
    good = naming_gate.check_name("Quillwarden", kind="agent")
    record("gate accepts a compliant name", good["ok"]
           and good["violations"] == [],
           "violations=%s" % (good["violations"],))

    cases = [
        ("HookForge", "NG-COLLISION"),      # already registered
        ("hook forge", "NG-COLLISION"),     # same name, normalized
        ("Agent", "NG-GENERIC"),            # a category, not a name
        ("Scion Prime", "NG-RESERVED"),     # reserved root
        ("9!!", "NG-FORMAT"),               # not a name at all
    ]
    refused = []
    for name, want in cases:
        verdict = naming_gate.check_name(name, kind="agent")
        if not verdict["ok"] and want in rules_of(verdict):
            refused.append("%s->%s" % (name, want))
    record("gate rejects violations with the rule named",
           len(refused) == len(cases), "; ".join(refused))


# ---------------------------------------------------------------- (3) foundry

def test_foundry(tmp):
    reg = os.path.join(tmp, "registry.json")
    shutil.copyfile(naming_gate.DEFAULT_REGISTRY_PATH, reg)
    md = os.path.join(tmp, "mesh-foundry")

    before = open(reg, "rb").read()
    bad = foundry.forge(
        foundry.new_definition("HookForge", "A hook workshop.",
                               ["cmd"]),
        registry_path=reg, mesh_dir_arg=md)
    after = open(reg, "rb").read()
    bus, conn, _ = console._connect(md)
    try:
        announces = bus.export(conn, kind=foundry.ANNOUNCE_KIND,
                               since=0, limit=100)
    finally:
        conn.close()
    record("foundry: gate failure registers nothing (registry "
           "untouched, no announcement)",
           not bad["ok"] and "NG-COLLISION" in rules_of(bad)
           and before == after and announces == [])

    malformed = foundry.forge({"name": "Teleportron", "role": "",
                               "work_kinds": ["teleport"]},
                              registry_path=reg, mesh_dir_arg=md)
    record("foundry: malformed definition refused by template check",
           not malformed["ok"]
           and "FD-TEMPLATE" in rules_of(malformed)
           and open(reg, "rb").read() == before)

    good = foundry.forge(
        foundry.new_definition(
            "Quillwarden",
            "Formats raw lyrics into clean section tags.",
            ["cmd", "registry"]),
        registry_path=reg, mesh_dir_arg=md)
    registry = naming_gate.load_registry(reg)
    bus, conn, _ = console._connect(md)
    try:
        announces = bus.export(conn, kind=foundry.ANNOUNCE_KIND,
                               since=0, limit=100)
    finally:
        conn.close()
    verdict = console.verify_chain(mesh_dir_arg=md)
    entry = good.get("entry") or {}
    record("foundry: pass registers the agent + posts the bus "
           "announcement (chain INTACT)",
           good["ok"]
           and entry.get("origin") == "foundry"
           and entry.get("status") == "approved"
           and naming_gate.is_registered("Quillwarden",
                                         registry=registry)
           and len(announces) == 1
           and "Quillwarden" in announces[0]["text"]
           and good["announce_id"] == announces[0]["id"]
           and verdict["ok"],
           "announce #%s" % (good.get("announce_id"),))


# ---------------------------------------------------------------- (4) scions

def test_scion_roundtrip(tmp):
    md = os.path.join(tmp, "mesh-scions")
    t_runner = scions.dispatch("cmd", "echo scion-roundtrip-ok",
                               mesh_dir_arg=md)
    t_warden = scions.dispatch("verify", "echo warden-check-ok",
                               mesh_dir_arg=md)
    routed = (t_runner["scion"] == "runner"
              and t_warden["scion"] == "warden")
    pend = scions.pending(mesh_dir_arg=md)
    routed = routed and pend.get("runner") == [t_runner["task_id"]] \
        and pend.get("warden") == [t_warden["task_id"]]

    did = scions.work_once("runner", mesh_dir_arg=md)
    warden_row = console.get_task(t_warden["task_id"], mesh_dir_arg=md)
    result = console.get_result(t_runner["task_id"], mesh_dir_arg=md)
    filter_ok = (
        did is not None and did["task_id"] == t_runner["task_id"]
        and did["exit_code"] == 0
        and result is not None and "scion-roundtrip-ok" in result
        and warden_row["state"] == "new"  # Runner left Warden's task
    )
    record("scion dispatch routes by kind; claim filter honoured "
           "(Runner completes only its own)", routed and filter_ok)

    did2 = scions.work_once("warden", mesh_dir_arg=md)
    result2 = console.get_result(t_warden["task_id"], mesh_dir_arg=md)
    verdict = console.verify_chain(mesh_dir_arg=md)
    record("scion round-trip completes (Warden's task done, result "
           "stored, chain INTACT)",
           did2 is not None and did2["task_id"] == t_warden["task_id"]
           and did2["exit_code"] == 0
           and result2 is not None and "warden-check-ok" in result2
           and verdict["ok"] and verdict["verdict"] == "INTACT")

    refused = []
    try:
        scions.dispatch("teleport", "echo nowhere", mesh_dir_arg=md)
    except ValueError:
        refused.append("unowned kind")
    try:
        scions.dispatch_to("runner", "verify", "echo nope",
                           mesh_dir_arg=md)
    except ValueError:
        refused.append("cross-charter")
    try:
        scions.dispatch("cmd", "rm -rf %s" % tmp, mesh_dir_arg=md)
    except console.BlockedCommandError:
        refused.append("blocklisted pre-post")
    record("dispatch refuses unowned kinds, cross-charter work, and "
           "blocklisted commands", len(refused) == 3,
           "; ".join(refused))


def main():
    tmp = tempfile.mkdtemp(prefix="scion-vision-test-")
    os.environ["MESH_DIR"] = os.path.join(tmp, "mesh")
    for fn, label in ((test_roster, "roster"),
                      (test_gate, "naming gate"),
                      (test_foundry, "foundry"),
                      (test_scion_roundtrip, "scion round-trip")):
        try:
            fn(tmp) if fn in (test_foundry, test_scion_roundtrip) else fn()
        except Exception as exc:  # noqa: BLE001
            record(label, False, "exception: %r" % exc)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("SUMMARY: %d/%d checks passed" % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
