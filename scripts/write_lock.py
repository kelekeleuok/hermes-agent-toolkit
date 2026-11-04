#!/usr/bin/env python3
"""write_lock.py — single-writer lock for a shared agent workspace.

WHY THIS EXISTS
---------------
One agent often has **several wake-up entries**: a chat session a human types
into, an auto-session spawned by an incoming message, and a scheduler/cron
session. All of them share one working directory but cannot see each other and
have **no mutex**. Two of them read-modify-write the same file minutes apart and
silently clobber each other.

Observed in production: the same maintenance script edited by the chat session
at 12:26 and by the cron session at 12:28; the second write silently discarded
the first.

SEMANTICS
---------
One *workbench* (one writable workspace) may be held by **one session at a
time**. A session that cannot take the lock must not write: it degrades to
read-only plus a report.

  * main session (a human is talking to it)  -> may acquire any time
  * sub sessions (cron / message-triggered)  -> `guard` before ANY producing
    write (edit scripts, write DB, change config, git commit)
  * stale locks (holder process gone / TTL exceeded) are reclaimable, so a
    crashed holder cannot deadlock the workspace

Multiple workbenches are supported: each line of work locks its own area and
does not block the others. Lock files live under the state dir, one per name.

EXIT CODES: 0 = held / writable, 3 = held by someone else (do NOT write),
            2 = usage error.

USAGE
-----
    write_lock.py acquire --who <session-id> [--role main|sub] [--ttl 1800] \
                          [--note text] [--workbench name] [--hold-pid PID]
    write_lock.py guard   --who <session-id> [--role sub]     # alias of acquire
    write_lock.py release --who <session-id> [--force]
    write_lock.py status  [--workbench name]
    write_lock.py list

All commands accept `--state-dir` (default: $XDG_STATE_HOME/agent-write-lock,
falling back to ~/.local/state/agent-write-lock).

No dependencies beyond the stdlib. Removing this file removes the feature:
nothing else imports it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import glob
import json
import os
import sys

DEFAULT_WORKBENCH = "default"
DEFAULT_TTL = 1800  # seconds; holder crash must not lock the workspace forever
ROLE_MAIN = "main"
ROLE_SUB = "sub"


def state_dir(explicit: str | None = None) -> str:
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "agent-write-lock")


def lock_path(workbench: str = DEFAULT_WORKBENCH, sd: str | None = None) -> str:
    wb = (workbench or DEFAULT_WORKBENCH).strip() or DEFAULT_WORKBENCH
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in wb)
    return os.path.join(state_dir(sd), f"workbench-{safe}.lock")


def lock_glob(sd: str | None = None) -> str:
    return os.path.join(state_dir(sd), "workbench-*.lock")


def _now_iso() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user -> alive
    except Exception:
        return True


def _read_holder(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw:
            return None
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _is_stale(h: dict | None) -> tuple[bool, str]:
    """Staleness is decided by **TTL first**.

    This tool is a short-lived CLI: the process that takes the lock exits
    immediately, so judging staleness by the lock script's own pid would
    reclaim a lock that was just taken. Pass `hold_pid` = the long-lived
    session process instead if you want a faster, process-based signal.
    """
    if not h:
        return True, "no holder"
    hp = int(h.get("hold_pid") or 0)
    if hp and not _pid_alive(hp):
        return True, f"holder process {hp} is gone"
    try:
        ts = dt.datetime.strptime(str(h.get("ts")), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True, "unparseable holder timestamp"
    age = (dt.datetime.now() - ts).total_seconds()
    ttl = int(h.get("ttl") or DEFAULT_TTL)
    if age > ttl:
        return True, f"held {int(age)}s > TTL {ttl}s"
    return False, f"held by {h.get('who')}({h.get('role')}) for {int(age)}s"


def _fmt(h: dict | None) -> str:
    if not h:
        return "(idle)"
    return (f"who={h.get('who')} role={h.get('role')} hold_pid={h.get('hold_pid') or '-'} "
            f"ts={h.get('ts')} ttl={h.get('ttl')} note={h.get('note') or '-'}")


def _run_exclusive(path: str, fn):
    """Read-modify-write the lock file under flock, so concurrent acquires
    do not overwrite each other."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return fn(fh)
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def acquire(path: str, who: str, role: str, ttl: int, note: str, hold_pid: int = 0) -> int:
    def _do(fh) -> int:
        fh.seek(0)
        try:
            held = json.loads(fh.read() or "{}")
            held = held if isinstance(held, dict) else {}
        except Exception:
            held = {}
        stale, why = _is_stale(held or None)
        if held and not stale and held.get("who") != who:
            print(f"[lock:busy] {_fmt(held)} -> {why} | do NOT write; go read-only and report")
            return 3
        if held and not stale and held.get("who") == who:
            print(f"[lock:mine] {_fmt(held)} | renewing")
        elif held:
            print(f"[lock:reclaimed] {_fmt(held)} -> {why}")
        rec = {"who": who, "role": role, "hold_pid": int(hold_pid or 0), "ts": _now_iso(),
               "ttl": int(ttl), "note": note or ""}
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(rec, ensure_ascii=False))
        fh.flush()
        print(f"[lock:acquired] {_fmt(rec)}")
        return 0
    return _run_exclusive(path, _do)


def release(path: str, who: str, force: bool) -> int:
    def _do(fh) -> int:
        fh.seek(0)
        try:
            held = json.loads(fh.read() or "{}")
            held = held if isinstance(held, dict) else {}
        except Exception:
            held = {}
        if not held:
            print("[lock:release] already idle")
            return 0
        if held.get("who") != who and not force:
            print(f"[lock:refused] holder is not you: {_fmt(held)} (use --force to override)")
            return 3
        fh.seek(0)
        fh.truncate()
        print(f"[lock:released] {_fmt(held)}")
        return 0
    return _run_exclusive(path, _do)


def status(path: str) -> int:
    held = _read_holder(path)
    stale, why = _is_stale(held)
    print(f"lock file : {path}")
    print(f"holder    : {_fmt(held)}")
    print(f"state     : {'idle/reclaimable' if stale else 'held'} ({why})")
    return 0


def list_locks(a) -> int:
    files = sorted(glob.glob(lock_glob(a.state_dir)))
    if not files:
        print("(no lock files yet)")
        return 0
    for f in files:
        held = _read_holder(f)
        stale, why = _is_stale(held)
        print(f"{os.path.basename(f)}  [{'idle' if stale else 'held'}]  {_fmt(held)}  ({why})")
    return 0


def _add_common(p) -> None:
    p.add_argument("--state-dir", default=None,
                   help="where lock files live (default: $XDG_STATE_HOME/agent-write-lock)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Single-writer lock for a shared agent workspace")
    _add_common(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("acquire", "take the lock (failure => read-only, do not write)"),
                           ("guard", "same as acquire (semantic alias for sub-sessions)")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--who", required=True, help="session id, e.g. chat-main / cron-42")
        p.add_argument("--role", default=ROLE_SUB, choices=[ROLE_MAIN, ROLE_SUB])
        p.add_argument("--ttl", type=int, default=DEFAULT_TTL, help=f"seconds, default {DEFAULT_TTL}")
        p.add_argument("--hold-pid", type=int, default=0,
                       help="optional long-lived session pid; if gone the lock is reclaimable")
        p.add_argument("--note", default="", help="what this batch is doing")
        p.add_argument("--workbench", default=DEFAULT_WORKBENCH, help="workspace name to lock")
        _add_common(p)
    r = sub.add_parser("release", help="release the lock when the batch ends")
    r.add_argument("--who", required=True)
    r.add_argument("--force", action="store_true")
    r.add_argument("--workbench", default=DEFAULT_WORKBENCH)
    _add_common(r)
    s = sub.add_parser("status", help="show holder and staleness")
    s.add_argument("--workbench", default=DEFAULT_WORKBENCH)
    _add_common(s)
    l = sub.add_parser("list", help="list every workbench lock")
    _add_common(l)

    args = ap.parse_args()
    if args.cmd in ("acquire", "guard"):
        return acquire(lock_path(args.workbench, args.state_dir), args.who, args.role,
                       args.ttl, args.note, args.hold_pid)
    if args.cmd == "release":
        return release(lock_path(args.workbench, args.state_dir), args.who, args.force)
    if args.cmd == "list":
        return list_locks(args)
    return status(lock_path(args.workbench, args.state_dir))


if __name__ == "__main__":
    sys.exit(main())
