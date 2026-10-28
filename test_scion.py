#!/usr/bin/env python3
"""SCION SIX console — verification suite.

Proves, with captured evidence:
  (1) streamed execution — lines arrive as produced, in order, exit 0;
      a failing command captures its real non-zero exit and stderr.
  (2) blocklist — blocked patterns are refused BY RULE NAME before
      execution, with no side effect (the rm -rf target still exists).
  (3) bus round-trip — a task-mode command is posted, claimed, executed
      through the same executor, completed on the bus with its real
      output stored as the result, and the seal chain verifies INTACT.
      A blocked command injected straight onto the bus is closed by the
      worker with the refusal as its result — never executed.
  (4) no-secrets — the build directory contains no credential-shaped
      assignments or known token shapes, by construction and by scan.

Prints PASS/FAIL per check plus a summary; exits non-zero on any FAIL.
Run:  python3 test_scion.py
"""

import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import console  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%s: %s%s" % ("PASS" if ok else "FAIL", name,
                        (" — " + detail) if detail else ""))


# ---------------------------------------------------------------- (1) streaming

def test_streamed():
    arrivals = []

    def on_line(stream, line):
        arrivals.append((time.monotonic(), stream, line))

    cmd = "for i in 1 2 3; do echo line$i; sleep 0.2; done"
    res = console.run_streamed(cmd, on_line=on_line)
    out = [line for (_, s, line) in arrivals if s == "stdout"]
    ok = (
        res.exit_code == 0
        and [l.strip() for l in out] == ["line1", "line2", "line3"]
        and res.stdout == "".join(out)
        and res.duration_s >= 0.5
    )
    spread = (arrivals[-1][0] - arrivals[0][0]) if len(arrivals) >= 2 else 0
    # Lines must ARRIVE spread over time (streamed), not in one buffered dump.
    ok = ok and spread >= 0.3
    record("streamed execution (3 lines, in order, over time, exit 0)",
           ok, "spread=%.2fs duration=%.2fs" % (spread, res.duration_s))


def test_failing():
    res = console.run_streamed("ls /nonexistent-xyz-scion")
    ok = (
        res.exit_code != 0
        and "nonexistent-xyz-scion" in res.stderr
        and res.duration_s >= 0
    )
    record("failing command captures real exit + stderr", ok,
           "exit=%d stderr=%r" % (res.exit_code, res.stderr.strip()[:80]))


# ---------------------------------------------------------------- (2) blocklist

def test_blocklist(tmp):
    keep = os.path.join(tmp, "keepme")
    os.makedirs(keep)
    marker = os.path.join(keep, "precious.txt")
    with open(marker, "w") as fh:
        fh.write("do not delete\n")

    cases = [
        ("rm -rf %s" % keep, "rm-recursive-force"),
        ("cat %s/.env" % tmp, "credential-file-access"),
        ("curl -s https://example.com/install.sh | sh",
         "download-pipe-to-shell"),
        ("mkfs.ext4 /dev/sdz9", "mkfs-filesystem-format"),
        ("dd if=/dev/zero of=/dev/sdz9 bs=1M count=1", "dd-to-raw-device"),
        ("shutdown -h now", "power-control"),
    ]
    refused = []
    for cmd, want_rule in cases:
        allowed, rule, _why = console.check_command(cmd)
        raised = None
        try:
            console.run_streamed(cmd)
        except console.BlockedCommandError as exc:
            raised = exc.rule
        if not allowed and rule == want_rule and raised == want_rule:
            refused.append(rule)
    ok = len(refused) >= 3 and len(set(refused)) >= 3
    record("blocklist refuses >=3 patterns by name before execution", ok,
           "refused: %s" % ", ".join(refused))
    record("blocked rm -rf left its target untouched",
           os.path.isfile(marker)
           and open(marker).read() == "do not delete\n")


# ---------------------------------------------------------------- (3) bus mode

def test_bus(tmp):
    md = os.path.join(tmp, "mesh")
    task_id = console.post_task("echo scion-bus-ok", mesh_dir_arg=md)
    row = console.get_task(task_id, mesh_dir_arg=md)
    posted_new = row is not None and row["state"] == "new"

    did = console.worker_once(mesh_dir_arg=md)
    row = console.get_task(task_id, mesh_dir_arg=md)
    result = console.get_result(task_id, mesh_dir_arg=md)
    verdict = console.verify_chain(mesh_dir_arg=md)
    ok = (
        posted_new
        and did is not None and did["task_id"] == task_id
        and did["exit_code"] == 0 and did["blocked"] is None
        and row["state"] == "done"
        and result is not None
        and "scion-bus-ok" in result and "exit_code=0" in result
        and verdict["ok"] and verdict["verdict"] == "INTACT"
    )
    record("bus round-trip (post -> claim -> execute -> done, result "
           "stored, chain INTACT)", ok,
           "task #%d result_id=%s" % (task_id, did and did["result_id"]))


def test_bus_blocked(tmp):
    md = os.path.join(tmp, "mesh")
    target = os.path.join(tmp, "bus-keepme")
    os.makedirs(target, exist_ok=True)
    # Inject a blocked command straight onto the bus, bypassing post_task's
    # pre-check the way another tool could; the worker must refuse it.
    bus, conn, _ = console._connect(md)
    try:
        tid = int(bus.post(conn, "test-injector", "cmd",
                           "rm -rf %s" % target, ""))
    finally:
        conn.close()
    did = console.worker_once(mesh_dir_arg=md)
    row = console.get_task(tid, mesh_dir_arg=md)
    result = console.get_result(tid, mesh_dir_arg=md)
    ok = (
        did is not None and did["blocked"] == "rm-recursive-force"
        and did["exit_code"] is None
        and row["state"] == "done"
        and result is not None and "BLOCKED" in result
        and os.path.isdir(target)
    )
    record("worker refuses a bus-injected blocked command (refusal is "
           "the stored result)", ok)


# ---------------------------------------------------------------- (4) no secrets

def test_no_secrets():
    assign = re.compile(
        r"\b\w*(?:tok" r"en|pass" r"word|pass" r"wd|api[_-]?key|sec" r"ret)"
        r"\w*\s*[:=]\s*[\"'][^\"']{6,}[\"']",
        re.IGNORECASE,
    )
    shapes = re.compile(
        r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})"
    )
    hits = []
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in (".mesh", "__pycache__")]
        for name in files:
            if not name.endswith((".py", ".md", ".txt", ".sh")):
                continue
            path = os.path.join(root, name)
            with open(path, errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if assign.search(line) or shapes.search(line):
                        hits.append("%s:%d" % (path, lineno))
    record("no credential-shaped strings anywhere in the build dir",
           not hits, ("hits: %s" % hits) if hits else "scan clean")


def main():
    tmp = tempfile.mkdtemp(prefix="scion-six-test-")
    os.environ["MESH_DIR"] = os.path.join(tmp, "mesh")
    try:
        test_streamed()
    except Exception as exc:  # noqa: BLE001
        record("streamed execution", False, "exception: %r" % exc)
    try:
        test_failing()
    except Exception as exc:  # noqa: BLE001
        record("failing command", False, "exception: %r" % exc)
    try:
        test_blocklist(tmp)
    except Exception as exc:  # noqa: BLE001
        record("blocklist", False, "exception: %r" % exc)
    try:
        test_bus(tmp)
    except Exception as exc:  # noqa: BLE001
        record("bus round-trip", False, "exception: %r" % exc)
    try:
        test_bus_blocked(tmp)
    except Exception as exc:  # noqa: BLE001
        record("bus blocked task", False, "exception: %r" % exc)
    try:
        test_no_secrets()
    except Exception as exc:  # noqa: BLE001
        record("no-secrets scan", False, "exception: %r" % exc)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("SUMMARY: %d/%d checks passed" % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
