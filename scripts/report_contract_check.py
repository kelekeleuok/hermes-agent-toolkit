#!/usr/bin/env python3
"""report_contract_check.py — machine-checkable output shape for reports to a human.

WHY THIS EXISTS
---------------
"Please be brief" in a system prompt is a wish, and wishes drift: the model
re-reads the prompt differently every session, and the human still has to read
the whole message to find out whether anything needs their decision.

A **format contract** is different: it is checkable by a program. If the first
line is missing, the report is non-conforming — no human judgement required.

The contract this tool enforces (all parts configurable):

  1. line 1 matches a fixed pattern, e.g.  ``**Needs your approval:** none``
     — the reader knows within one line whether they must act.
  2. the body contains the required section headers, in order, e.g.
     做过 / 没成 / 接下来  (done / not done / next)
  3. the body does not exceed N characters — length is capped, not "preferred"
  4. no forbidden tokens leak in (internal paths, table names, ticket ids…)

USAGE
-----
    report_contract_check.py report.md
    report_contract_check.py report.md --max-body-chars 300 --json
    cat report.md | report_contract_check.py -

    # see what a conforming report looks like
    report_contract_check.py --print-template

Exit codes: 0 = conforming, 1 = violations found, 2 = usage/IO error.

Default config lives in a JSON file you can pass with --config:

    {
      "first_line_pattern": "\\\\*\\\\*Needs your approval:\\\\*\\\\*",
      "sections": ["Done", "Not done", "Next"],
      "require_section_order": true,
      "max_body_chars": 300,
      "forbidden_patterns": ["/srv/", "internal-", "\\\\\\\\bT-\\\\d{3,}\\\\b"]
    }

Note on line 3: count the body **excluding** the first line and section
headers, because that is what the human actually reads.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

DEFAULT_CONFIG = {
    "first_line_pattern": r"\*\*Needs your approval:\*\*",
    "sections": [],
    "require_section_order": True,
    "max_body_chars": 0,          # 0 = no cap
    "forbidden_patterns": [],
    "section_prefixes": ["#", "**", "-"],  # how a section header may be written
}

TEMPLATE = """**Needs your approval:** <one sentence, or "none">

**Done**
Two or three sentences. Concrete numbers, not adjectives.

**Not done**
What failed and why. A blocker reported is worth more than an omission.

**Next**
What happens next, and when.
"""


def _load_config(path: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return cfg


def _is_section_line(line: str, sections: list[str], prefixes: list[str]) -> tuple[bool, str | None]:
    s = line.strip()
    for name in sections:
        for p in prefixes:
            if s.startswith(p) and name in s:
                return True, name
    return False, None


def check(text: str, cfg: dict) -> list[str]:
    problems: list[str] = []
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["report is empty"]

    # 1. first line
    pat = cfg.get("first_line_pattern") or ""
    if pat and not re.search(pat, lines[0]):
        problems.append(f"line 1 does not match the approval pattern: {lines[0][:80]!r}")

    # 2. sections, in order
    sections = list(cfg.get("sections") or [])
    if sections:
        prefixes = list(cfg.get("section_prefixes") or [])
        found: list[str] = []
        for line in lines[1:]:
            ok, name = _is_section_line(line, sections, prefixes)
            if ok and name:
                found.append(name)
        missing = [s for s in sections if s not in found]
        if missing:
            problems.append(f"missing section(s): {', '.join(missing)}")
        if cfg.get("require_section_order") and not missing:
            seq = [s for s in found]
            expected = sorted(seq, key=lambda x: sections.index(x))
            if seq != expected:
                problems.append(f"sections out of order: found {seq}, expected {sections}")

    # 3. body length
    cap = int(cfg.get("max_body_chars") or 0)
    if cap:
        prefixes = list(cfg.get("section_prefixes") or [])
        body = []
        for line in lines[1:]:
            ok, _ = _is_section_line(line, sections, prefixes)
            if ok:
                continue
            body.append(line)
        n = len("".join(body).strip())
        if n > cap:
            problems.append(f"body is {n} chars, over the cap of {cap}")

    # 4. forbidden tokens
    for fp in cfg.get("forbidden_patterns") or []:
        m = re.search(fp, text)
        if m:
            # do not echo the matched text in full: it may be a credential-adjacent value
            problems.append(f"forbidden pattern {fp!r} matched at offset {m.start()}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a report against a machine-checkable output contract")
    ap.add_argument("path", nargs="?", help="report file, or '-' for stdin")
    ap.add_argument("--config", default=None, help="JSON config overriding the defaults")
    ap.add_argument("--max-body-chars", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--print-template", action="store_true", help="print a conforming template and exit")
    a = ap.parse_args()

    if a.print_template:
        print(TEMPLATE, end="")
        return 0
    if not a.path:
        print("usage: report_contract_check.py <file|-> [--config cfg.json]", file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if a.path == "-" else open(a.path, encoding="utf-8").read()
        cfg = _load_config(a.config)
    except OSError as e:
        print(f"cannot read: {e}", file=sys.stderr)
        return 2
    if a.max_body_chars is not None:
        cfg["max_body_chars"] = a.max_body_chars

    problems = check(text, cfg)
    if a.json:
        print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
    else:
        if problems:
            print(f"NON-CONFORMING ({len(problems)} issue(s)):")
            for p in problems:
                print(f"  - {p}")
        else:
            print("conforming")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
