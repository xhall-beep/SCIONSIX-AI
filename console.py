#!/usr/bin/env python3
"""SCION SIX console — the honest merged rebuild.

SCION SIX existed as two failed halves:

  * SCIONSIX-AI   — a stock Android/Gradle scaffold with no feature code;
                    its one real artefact was an on-device Termux build
                    loop (terminal_orchestrator.sh).
  * scionsix-core — a Kivy command console whose streamed-output front end
                    was plausible, but whose orchestration half ("ori")
                    was vaporous and carried a hardcoded credential
                    (compromised; designed out here — no secrets in this
                    codebase, by construction and by test).

This rebuild is the merged core both halves were reaching for:

  1. A command console that executes shell commands with output STREAMED
     line-by-line as it is produced (stdout and stderr on separate reader
     threads), capturing the real exit code and duration.
  2. A safety layer in front of execution: an explicit blocklist, checked
     BEFORE anything runs. A blocked command is refused with the rule
     named — it is never executed.
  3. A bus-backed task mode on the verified ApexYX bus (mesh_bus.py):
     a command-task is posted to the bus, a local worker claims it
     (exactly-once), runs it through the SAME safety-checked streamed
     executor, posts the captured output back as a sealed result row,
     and closes the task. The seal chain stays verifiable end to end.
     The bus's own exec tier is never invoked — this console calls the
     bus API directly, exactly like the Q3 APEX Core console.

Usage:
    # one-shot (scriptable / testable)
    python3 console.py --cmd run  --text "echo hello; sleep 1; echo done"
    python3 console.py --cmd task --text "echo hello from the bus"
    python3 console.py --cmd verify
    python3 console.py --cmd health

    # interactive terminal UI
    python3 console.py
    scion> run ls -la
    scion> task echo hello from the bus
    scion> work            # run the worker loop once
    scion> verify
    scion> health
    scion> quit

Bus location: the bus library is loaded from the ApexYX-Sovereign rebuild
next door (override with APEX_BUS_PATH=/path/to/mesh_bus.py). Data lives
in MESH_DIR — default is a workspace-local ``.mesh`` folder inside this
build directory, never the host default ``~/.mesh``, unless MESH_DIR is
already set in the environment (tests use a throwaway directory).

Phone packaging note: the Android/Gradle APK and the on-device Termux
build loop from SCIONSIX-AI are the phone-side packaging step, NOT part
of this verified core. The Kivy shell from scionsix-core would wrap
exactly the functions below; nothing here depends on a GUI.

Python 3 standard library only. $0.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
def _default_bus_path():
    # The bus repo's canonical name is ApexYX-Sovereign; the original
    # rebuild layout used a lowercase sibling dir. Accept either.
    for _name in ("apexyx-sovereign", "ApexYX-Sovereign"):
        _cand = os.path.join(HERE, "..", _name, "mesh_bus", "mesh_bus.py")
        if os.path.exists(_cand):
            return _cand
    return os.path.join(HERE, "..", "apexyx-sovereign",
                        "mesh_bus", "mesh_bus.py")


DEFAULT_BUS_PATH = _default_bus_path()
DEFAULT_MESH_DIR = os.path.join(HERE, ".mesh")
DEFAULT_NODE = "scion-six"
RESULT_TEXT_LIMIT = 3500  # bus rows cap at 4000 chars; keep headroom

_bus_module = None


# ---------------------------------------------------------------- safety layer
# Every rule is (name, compiled pattern, why). A command is checked against
# ALL rules before execution; the first match refuses it, by name. This is
# a guardrail for an agent-driven console, not a sandbox: it stops the
# known-catastrophic and known-credential shapes cold, and everything it
# refuses is refused BEFORE a process exists.

BLOCK_RULES = [
    (
        "rm-recursive-force",
        re.compile(r"\brm\b(?=[^\n|;&]*-[a-zA-Z]*r)(?=[^\n|;&]*-[a-zA-Z]*f)"),
        "recursive+forced delete (rm -rf ...) is never run from this console",
    ),
    (
        "mkfs-filesystem-format",
        re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"),
        "formatting a filesystem destroys everything on it",
    ),
    (
        "dd-to-raw-device",
        re.compile(r"\bdd\b[^\n|;&]*\bof=/dev/(sd|hd|nvme|mmcblk|disk|vd)[a-z0-9]*"),
        "dd writing straight to a raw block device bypasses the filesystem",
    ),
    (
        "power-control",
        re.compile(r"\b(shutdown|poweroff|halt)\b|\breboot\b|\binit\s+[06]\b"),
        "powering off / rebooting the host is the owner's hand only",
    ),
    (
        "credential-file-access",
        re.compile(
            r"(\.env\b|credential|\.pem\b|\.key\b|id_rsa|id_ed25519\b"
            r"|/\.ssh/|\.aws/|\.gnupg/)",
            re.IGNORECASE,
        ),
        "credential material is never read (or otherwise touched) by command",
    ),
    (
        "download-pipe-to-shell",
        re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|dash|fish)\b"),
        "piping a download straight into a shell runs unreviewed code",
    ),
    (
        "fork-bomb",
        re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "a fork bomb wedges the host",
    ),
]


class BlockedCommandError(Exception):
    """Raised before execution when a command matches the blocklist."""

    def __init__(self, command, rule, why):
        super().__init__("BLOCKED by rule %r: %s" % (rule, why))
        self.command = command
        self.rule = rule
        self.why = why


def check_command(command):
    """Return (allowed: bool, rule_name: str|None, why: str|None).

    Pure check — runs nothing. Empty commands are refused as invalid.
    """
    if command is None or not str(command).strip():
        return False, "empty-command", "an empty command is a bug, not a task"
    text = str(command)
    for name, pattern, why in BLOCK_RULES:
        if pattern.search(text):
            return False, name, why
    return True, None, None


def ensure_allowed(command):
    """Raise BlockedCommandError (naming the rule) if the command is blocked."""
    allowed, rule, why = check_command(command)
    if not allowed:
        raise BlockedCommandError(str(command), rule, why)


# ---------------------------------------------------------------- executor

class ExecResult:
    """The real outcome of one executed command."""

    def __init__(self, command, exit_code, duration_s, stdout_lines,
                 stderr_lines):
        self.command = command
        self.exit_code = exit_code
        self.duration_s = duration_s
        self.stdout_lines = stdout_lines
        self.stderr_lines = stderr_lines

    @property
    def stdout(self):
        return "".join(self.stdout_lines)

    @property
    def stderr(self):
        return "".join(self.stderr_lines)

    def to_dict(self):
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def run_streamed(command, on_line=None, timeout=None):
    """Execute `command` through the shell, streaming output as produced.

    Safety first: the blocklist is checked before any process exists.
    stdout and stderr are drained on separate reader threads, line by
    line, the moment each line is produced — `on_line(stream, line)` is
    called per line in arrival order per stream ("stdout" / "stderr").
    Returns an ExecResult with the real exit code and wall duration.
    Raises BlockedCommandError for blocked commands (nothing runs).
    """
    ensure_allowed(command)
    started = time.monotonic()
    proc = subprocess.Popen(
        str(command),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_lines, stderr_lines = [], []

    def drain(stream, sink, name):
        try:
            for line in stream:
                sink.append(line)
                if on_line is not None:
                    on_line(name, line)
        finally:
            stream.close()

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, stdout_lines, "stdout")),
        threading.Thread(target=drain, args=(proc.stderr, stderr_lines, "stderr")),
    ]
    for t in threads:
        t.start()
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        exit_code = proc.wait()
        stderr_lines.append("[scion] killed after %ss timeout\n" % timeout)
    for t in threads:
        t.join()
    return ExecResult(str(command), exit_code, time.monotonic() - started,
                      stdout_lines, stderr_lines)


# ---------------------------------------------------------------- bus loading

def mesh_dir(mesh_dir_arg=None):
    """Resolve the bus data directory: explicit arg > $MESH_DIR > local .mesh."""
    resolved = mesh_dir_arg or os.environ.get("MESH_DIR") or DEFAULT_MESH_DIR
    return os.path.abspath(resolved)


def _load_bus(mesh_dir_arg=None):
    """Import mesh_bus.py (once) and point it at the resolved MESH_DIR."""
    global _bus_module
    resolved = mesh_dir(mesh_dir_arg)
    if _bus_module is None:
        bus_path = os.path.abspath(
            os.environ.get("APEX_BUS_PATH", DEFAULT_BUS_PATH)
        )
        if not os.path.isfile(bus_path):
            raise FileNotFoundError(
                "ApexYX bus library not found at %s "
                "(set APEX_BUS_PATH to mesh_bus.py)" % bus_path
            )
        spec = importlib.util.spec_from_file_location("mesh_bus", bus_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _bus_module = module
    _bus_module.MESH_DIR = resolved
    os.environ["MESH_DIR"] = resolved
    return _bus_module, resolved


def _connect(mesh_dir_arg=None):
    bus, resolved = _load_bus(mesh_dir_arg)
    return bus, bus.db(), resolved


# ---------------------------------------------------------------- bus task mode
# Tasks are bus rows of kind "cmd" whose text is the command. The worker
# claims one (exactly-once), executes it through run_streamed (so the
# blocklist applies identically), posts the captured output as a sealed
# row of kind "result" with tgt=<task id>, then closes the task with
# bus.done(). Blocked commands are never executed: if one reaches the
# worker (posted by another tool), it is closed with the refusal as its
# result — the refusal is the honest outcome.

def post_task(command, node=DEFAULT_NODE, mesh_dir_arg=None):
    """Safety-check, then post a command-task to the bus. Returns its id.

    Raises BlockedCommandError before posting if the command is blocked,
    ValueError on empty input.
    """
    if command is None or not str(command).strip():
        raise ValueError("command must not be empty")
    ensure_allowed(command)
    bus, conn, _ = _connect(mesh_dir_arg)
    try:
        return int(bus.post(conn, node, "cmd", str(command).strip(), ""))
    finally:
        conn.close()


def _format_result(res):
    """Render an ExecResult as the result-row text (bus caps at 4000)."""
    body = res.stdout
    if res.stderr:
        body += "\n[stderr]\n" + res.stderr
    if len(body) > RESULT_TEXT_LIMIT:
        body = "[... output truncated ...]\n" + body[-RESULT_TEXT_LIMIT:]
    return "exit_code=%d duration=%.3fs\n%s" % (
        res.exit_code, res.duration_s, body)


def worker_once(node=DEFAULT_NODE, mesh_dir_arg=None, on_line=None):
    """Claim and execute the oldest pending command-task, once.

    Returns a dict describing what happened, or None when no task waits:
    {"task_id", "command", "exit_code"|None, "blocked"|None, "result_id"}.
    """
    bus, conn, _ = _connect(mesh_dir_arg)
    try:
        rows = bus.export(conn, kind="cmd", since=0, limit=100000)
        pending = [r for r in rows if r["state"] == "new"]
        if not pending:
            return None
        task = pending[0]
        if not bus.claim(conn, int(task["id"]), node):
            return None  # another worker won it; exactly-once holds
        command = task["text"]
        allowed, rule, why = check_command(command)
        if not allowed:
            result_text = "BLOCKED by rule %r: %s — command not executed" % (
                rule, why)
            result_id = int(bus.post(conn, node, "result", result_text,
                                     str(task["id"])))
            bus.done(conn, int(task["id"]), node)
            return {"task_id": int(task["id"]), "command": command,
                    "exit_code": None, "blocked": rule,
                    "result_id": result_id}
        res = run_streamed(command, on_line=on_line)
        result_id = int(bus.post(conn, node, "result", _format_result(res),
                                 str(task["id"])))
        bus.done(conn, int(task["id"]), node)
        return {"task_id": int(task["id"]), "command": command,
                "exit_code": res.exit_code, "blocked": None,
                "result_id": result_id}
    finally:
        conn.close()


def run_task(command, node=DEFAULT_NODE, mesh_dir_arg=None, on_line=None):
    """Post a command-task and work the loop until THIS task is done.

    Returns {"task_id", "state", "result"} with the bus's real stored
    result text. Raises BlockedCommandError before posting if blocked.
    """
    task_id = post_task(command, node=node, mesh_dir_arg=mesh_dir_arg)
    bus, conn, _ = _connect(mesh_dir_arg)
    try:
        for _ in range(100):
            row = conn.execute(
                "SELECT state FROM messages WHERE id=?", (task_id,)
            ).fetchone()
            if row is not None and row["state"] == "done":
                break
            conn.close()
            worker_once(node=node, mesh_dir_arg=mesh_dir_arg, on_line=on_line)
            bus, conn, _ = _connect(mesh_dir_arg)
        else:
            raise RuntimeError("task %d never reached done" % task_id)
        results = [r for r in bus.export(conn, kind="result", since=0,
                                         limit=100000)
                   if r["for"] == str(task_id)]
        state = conn.execute(
            "SELECT state FROM messages WHERE id=?", (task_id,)
        ).fetchone()["state"]
        return {"task_id": task_id, "state": state,
                "result": results[-1]["text"] if results else None}
    finally:
        conn.close()


def get_task(task_id, mesh_dir_arg=None):
    """Return one bus row as a dict (or None)."""
    bus, conn, _ = _connect(mesh_dir_arg)
    try:
        rows = bus.export(conn, since=0, limit=100000)
    finally:
        conn.close()
    for r in rows:
        if r["id"] == int(task_id):
            return r
    return None


def get_result(task_id, mesh_dir_arg=None):
    """Return the stored result text for a task (or None)."""
    bus, conn, _ = _connect(mesh_dir_arg)
    try:
        rows = bus.export(conn, kind="result", since=0, limit=100000)
    finally:
        conn.close()
    mine = [r for r in rows if r["for"] == str(task_id)]
    return mine[-1]["text"] if mine else None


def verify_chain(mesh_dir_arg=None):
    """Walk the bus seal chain. Returns {"ok", "verdict", "bad_rows", ...}."""
    bus, conn, resolved = _connect(mesh_dir_arg)
    try:
        bad = [int(i) for i in bus.verify(conn)]
    finally:
        conn.close()
    return {
        "ok": not bad,
        "verdict": "INTACT" if not bad else "TAMPERED",
        "bad_rows": bad,
        "mesh_dir": resolved,
        "db": bus.db_path(),
    }


def bus_health(mesh_dir_arg=None):
    """Real bus status plus this console's own facts."""
    bus, conn, resolved = _connect(mesh_dir_arg)
    try:
        st = bus.status(conn)
    finally:
        conn.close()
    by_state = st.get("by_state", {})
    return {
        "ok": True,
        "mesh_dir": resolved,
        "db": st.get("db"),
        "total": st.get("total", 0),
        "new": by_state.get("new", 0),
        "claimed": by_state.get("claimed", 0),
        "done": by_state.get("done", 0),
        "by_state": by_state,
        "by_kind": st.get("by_kind", {}),
        "block_rules": [name for name, _, _ in BLOCK_RULES],
        "python": sys.version.split()[0],
    }


# ---------------------------------------------------------------- presentation

def _print_line(stream, line):
    if stream == "stderr":
        sys.stderr.write(line)
        sys.stderr.flush()
    else:
        sys.stdout.write(line)
        sys.stdout.flush()


def run_cmd(args):
    """One-shot mode: perform one real operation and print its real result."""
    md = args.mesh_dir
    if args.cmd == "run":
        try:
            res = run_streamed(args.text, on_line=_print_line)
        except BlockedCommandError as exc:
            print(str(exc), file=sys.stderr)
            return 126
        return res.exit_code
    if args.cmd == "task":
        try:
            out = run_task(args.text, node=args.node, mesh_dir_arg=md,
                           on_line=_print_line)
        except BlockedCommandError as exc:
            print(str(exc), file=sys.stderr)
            return 126
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "verify":
        result = verify_chain(mesh_dir_arg=md)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "health":
        print(json.dumps(bus_health(mesh_dir_arg=md), indent=2))
        return 0
    print("error: unknown --cmd %r" % args.cmd, file=sys.stderr)
    return 2


HELP = """commands:
  run <command...>       execute now, output streamed, exit code is real
  task <command...>      post to the bus and work it to done, print result
  work                   claim + execute one pending bus task
  verify                 walk the bus seal chain (INTACT / TAMPERED)
  health                 bus counts + blocklist rules in force
  help                   this text
  quit / exit            leave
Blocked commands (rm -rf, mkfs, dd to a device, shutdown/reboot,
credential-file access, curl|sh, fork bomb) are refused by rule name
before anything runs."""


def interactive(mesh_dir_arg=None, node=DEFAULT_NODE):
    """Simple terminal UI over the exact same functions as --cmd mode."""
    _, resolved = _load_bus(mesh_dir_arg)
    print("SCION SIX console — bus at %s" % os.path.join(resolved, "bus.db"))
    print("Commands run for real, streamed, safety-checked. Type 'help'.")
    while True:
        try:
            line = input("scion> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if cmd in ("quit", "exit"):
                return 0
            elif cmd == "help":
                print(HELP)
            elif cmd == "run":
                res = run_streamed(rest, on_line=_print_line)
                print("[exit %d in %.2fs]" % (res.exit_code, res.duration_s))
            elif cmd == "task":
                out = run_task(rest, node=node, mesh_dir_arg=mesh_dir_arg,
                               on_line=_print_line)
                print(json.dumps(out, indent=2))
            elif cmd == "work":
                did = worker_once(node=node, mesh_dir_arg=mesh_dir_arg,
                                  on_line=_print_line)
                print(json.dumps(did, indent=2) if did else "(no task waiting)")
            elif cmd == "verify":
                result = verify_chain(mesh_dir_arg=mesh_dir_arg)
                print("chain %s%s" % (
                    result["verdict"],
                    (" — bad rows: %s" % result["bad_rows"])
                    if result["bad_rows"] else "",
                ))
            elif cmd == "health":
                print(json.dumps(bus_health(mesh_dir_arg=mesh_dir_arg),
                                 indent=2))
            else:
                print("unknown command %r — type 'help'" % cmd)
        except BlockedCommandError as exc:
            print(str(exc))
        except (ValueError, LookupError, RuntimeError) as exc:
            print("error: %s" % exc)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="SCION SIX console — streamed, safety-checked, bus-backed"
    )
    ap.add_argument("--cmd", choices=["run", "task", "verify", "health"],
                    help="one-shot mode: run one operation and exit")
    ap.add_argument("--text", default="", help="command text (run / task)")
    ap.add_argument("--node", default=DEFAULT_NODE,
                    help="bus node name to act as")
    ap.add_argument("--mesh-dir", default=None,
                    help="bus data dir (default: $MESH_DIR or ./.mesh here)")
    args = ap.parse_args(argv)
    if args.cmd:
        return run_cmd(args)
    return interactive(mesh_dir_arg=args.mesh_dir, node=args.node)


if __name__ == "__main__":
    sys.exit(main())
