#!/usr/bin/env python3
"""liveness_probe.py — check that a scheduled job produced *output*, not just an exit code.

WHY THIS EXISTS
---------------
The classic silent failure of a cron/scheduler pipeline is not a crash. It is:
the process exits 0, the job reports success, the dashboard stays green —
and the artifact is **stale, empty, or wrong**. Nothing alerts, because
everything that is being watched succeeded.

Call-level health checks ("did the command fail three times?") cannot see this.
The only thing that can is the artifact itself.

THREE CRITERIA (all must pass, per check)
-----------------------------------------
  1. FRESH        the newest matching file's mtime is within `max_age_seconds`
  2. NON-EMPTY    size >= `min_bytes` and line count >= `min_lines`
  3. COHERENT     every `content` regex matches (e.g. today's date stamp is
                  actually present, required sections exist) — this is what
                  catches "the job ran but wrote yesterday's numbers again"

USAGE
-----
    liveness_probe.py spec.json
    liveness_probe.py spec.json --json
    liveness_probe.py spec.json --explain      # show what each check resolved to

Exit codes: 0 = all healthy, 1 = at least one unhealthy, 2 = usage/IO error.

SPEC FORMAT (JSON, one entry per artifact)
------------------------------------------
    {
      "checks": [
        {
          "name": "daily-report",
          "path": "/srv/reports/daily-*.md",
          "max_age_seconds": 93600,
          "min_bytes": 2000,
          "min_lines": 40,
          "content": [
            {"pattern": "\\\\bas of \\\\d{4}-\\\\d{2}-\\\\d{2}\\\\b", "description": "carries a date stamp"}
          ],
          "must_contain_all": ["## Summary", "## Risks"]
        }
      ]
    }

`path` accepts a glob; the newest match is used. Relative paths resolve against
the spec file's directory (so a repo can be moved). A check with no `content`
and no `must_contain_all` still enforces criteria 1 and 2.

Design note: this tool never *creates* anything. A probe that writes to the
artifact it is checking can make itself pass; keep it read-only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time


def _resolve(p: str, base: str) -> str:
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(base, p)
    return p


def _newest(paths: list[str]) -> str | None:
    best, best_m = None, -1.0
    for p in paths:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > best_m:
            best, best_m = p, m
    return best


def check_one(c: dict, base: str) -> dict:
    name = c.get("name") or c.get("path") or "<unnamed>"
    pattern = _resolve(str(c.get("path") or ""), base)
    out = {"name": name, "path": pattern, "ok": True, "problems": [], "resolved": None,
           "age_seconds": None, "bytes": None, "lines": None}

    matches = sorted(glob.glob(pattern))
    if not matches:
        out["ok"] = False
        out["problems"].append("no file matches the glob — the job produced nothing")
        return out

    target = _newest(matches)
    if not target:
        out["ok"] = False
        out["problems"].append("glob matched entries but none could be stat'ed")
        return out
    out["resolved"] = target
    try:
        st = os.stat(target)
    except OSError as e:
        out["ok"] = False
        out["problems"].append(f"cannot stat {target}: {e}")
        return out

    # 1. fresh
    age = time.time() - st.st_mtime
    out["age_seconds"] = int(age)
    max_age = c.get("max_age_seconds")
    if max_age is not None and age > float(max_age):
        out["ok"] = False
        out["problems"].append(
            f"stale: newest match is {int(age)}s old (max {int(float(max_age))}s) -> {target}")

    # 2. non-empty / plausible size
    out["bytes"] = st.st_size
    min_bytes = int(c.get("min_bytes") or 0)
    if min_bytes and st.st_size < min_bytes:
        out["ok"] = False
        out["problems"].append(f"too small: {st.st_size} bytes < {min_bytes}")

    try:
        with open(target, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        out["ok"] = False
        out["problems"].append(f"cannot read {target}: {e}")
        return out

    nlines = text.count("\n") + (0 if text.endswith("\n") else 1)
    out["lines"] = nlines
    min_lines = int(c.get("min_lines") or 0)
    if min_lines and nlines < min_lines:
        out["ok"] = False
        out["problems"].append(f"too few lines: {nlines} < {min_lines}")

    # 3. coherent
    for rule in c.get("content") or []:
        pat = str(rule.get("pattern") or "")
        desc = rule.get("description") or pat
        if not pat:
            continue
        if not re.search(pat, text):
            out["ok"] = False
            out["problems"].append(f"content check failed: {desc}")
    for needle in c.get("must_contain_all") or []:
        if needle not in text:
            out["ok"] = False
            out["problems"].append(f"missing required text: {needle!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe that a scheduled job produced fresh, non-empty, coherent output")
    ap.add_argument("spec", help="JSON spec file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true", help="show resolved paths, age, size, lines")
    ap.add_argument("--quiet", action="store_true", help="only print failures (for cron)")
    a = ap.parse_args()

    try:
        with open(a.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"cannot load spec: {e}", file=sys.stderr)
        return 2

    checks = spec.get("checks") or []
    if not checks:
        print("spec has no checks", file=sys.stderr)
        return 2

    base = os.path.dirname(os.path.abspath(a.spec))
    results = [check_one(c, base) for c in checks]
    bad = [r for r in results if not r["ok"]]

    if a.json:
        print(json.dumps({"ok": not bad, "results": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["ok"] and a.quiet:
                continue
            flag = "OK  " if r["ok"] else "FAIL"
            print(f"[{flag}] {r['name']}")
            if a.explain or not r["ok"]:
                print(f"       path  : {r['resolved'] or r['path']}")
                if r["age_seconds"] is not None:
                    print(f"       age   : {r['age_seconds']}s  bytes: {r['bytes']}  lines: {r['lines']}")
                for p in r["problems"]:
                    print(f"       - {p}")
        if a.quiet and not bad:
            pass  # silence = healthy
        print(f"\n{len(results) - len(bad)}/{len(results)} healthy")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
