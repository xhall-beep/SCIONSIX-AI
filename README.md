# SCIONSIX-AI — the Android / on-device side of SCION SIX

**Upgraded 2026-10-28.** SCION SIX was attempted twice: a Kivy command
console (`scionsix-core`) and this repo — a stock Android Studio
scaffold for `ai.scionsix.orchestrator` that never grew feature code.
The two attempts are now merged into one working core.

## What lives where

- **The console core lives primarily in
  [scionsix-core](https://github.com/xhall-beep/scionsix-core)** —
  `console.py`: a streamed command executor with a pre-execution
  safety blocklist, plus a bus task mode on the verified ApexYX bus
  (post → claim → execute → sealed result). Start there for the full
  documentation.
- **This repo holds the Android / on-device build side**: the Gradle
  scaffold (`app/`, `build.gradle`, `gradlew`) and its one genuinely
  useful artefact, **`scripts/terminal_orchestrator.sh`** — a Termux
  loop that builds and installs the APK on-device. It is kept as the
  packaging route for wrapping the console core into the Android app.
- For convenience, the same verified `console.py` + `test_scion.py`
  are included at this repo's root, identical to scionsix-core's, so
  the core can be run and tested from either checkout.

## Run / verify the core

```bash
python3 console.py --cmd run  --text "echo hello; sleep 1; echo done"
python3 console.py --cmd task --text "echo hello from the bus"
python3 console.py --cmd verify
python3 test_scion.py
```

The bus library is loaded from a sibling checkout of the
ApexYX-Sovereign repo (either `apexyx-sovereign` or
`ApexYX-Sovereign` capitalisation; override with `APEX_BUS_PATH`).
Python 3 standard library only, $0.

## Latest verification

2026-10-28, in this repo, on the estate VM: `python3 test_scion.py`
→ **7/7 checks passed** (streamed execution, real exit/stderr capture,
six blocklist refusals, bus round-trip with seal chain INTACT,
bus-injected blocked command refused, no-secrets scan). Verbatim
output of the original passing run is in `TEST-OUTPUT.txt`.

## Honest packaging status

The Android APK build via `terminal_orchestrator.sh` is the phone-side
packaging step; it is **not** part of the verified core above and has
not been re-verified in this upgrade. What is claimed as done is the
core: it executes, streams, refuses, and orchestrates, with captured
proof.
