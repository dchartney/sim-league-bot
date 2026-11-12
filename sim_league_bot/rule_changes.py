"""Plan -> preview -> apply engine for rule-book changes made from chat.

Three operations on numbered rules (all inside rules/*.md):
  replace  - new text for an existing rule (must keep the same number)
  delete   - remove a rule (refuses if it still has sub-rules)
  renumber - change a rule's number, and its sub-rules' numbers with it, within
             the same top-level section (e.g. 1.5.3 -> 1.5.2)

`plan()` works purely in memory and touches nothing on disk: it validates the
operations in order, builds the new file texts, a unified diff, and a
cross-reference report (rules whose text mentions a rule number that no longer
exists, or that was deleted/renumbered and so may now point at the wrong rule).
`apply()` re-checks the files haven't changed since the plan was made, writes
them, and makes ONE git commit authored by the admin. Pushing the commit to the
rule-edits branch is rules.push_edits(), called by the caller.

Rule text itself is never rewritten by renumber: a mention of "1.5.3" inside
another rule's body is reported as a warning for a human to fix, not changed."""
import difflib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import rules

# Lenient definition match for the checker only: a few source rules are typed
# "8.3.1 text" with no period after the number.
_DEFINES = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_REFERENCE = re.compile(r"(?<![\d.$/:%-])(\d{1,2}\.\d{1,2}(?:\.\d{1,2})*)(?![\d%/:])")


@dataclass
class Plan:
    ops: list
    original: dict = field(default_factory=dict)  # Path -> text at plan time
    updated: dict = field(default_factory=dict)   # Path -> new text (changed files only)
    diff: str = ""
    new_dangling: list = field(default_factory=list)  # refs to missing rules created by this change
    warnings: list = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


def _load_all() -> dict:
    return {p: p.read_text(encoding="utf-8") for p in sorted(rules.RULES_DIR.glob("*.md"))}


def _defined_and_refs(texts: dict):
    defined = {str(n) for n in range(1, 13)}
    refs = []  # (rule_context, referenced_number)
    for text in texts.values():
        for block in rules._blocks(text):
            first = block.strip().splitlines()[0]
            m = _DEFINES.match(first)
            ctx = m.group(1) if m else "-"
            if m:
                defined.add(m.group(1))
            body = block.strip()[m.end():] if m else block
            for r in _REFERENCE.findall(body):
                refs.append((ctx, r))
    return defined, refs


def check_references(texts: Optional[dict] = None) -> list:
    """Dangling cross-references as 'Rule X mentions Y' strings."""
    defined, refs = _defined_and_refs(texts if texts is not None else _load_all())
    return sorted({f"Rule {ctx} mentions {ref}" for ctx, ref in refs if ref not in defined})


def _norm_ops(raw_ops) -> list:
    ops = []
    for o in raw_ops or []:
        op = str(o.get("op", "")).strip().lower()
        number = str(o.get("number", "")).strip().rstrip(".")
        if op not in ("replace", "delete", "renumber") or not number:
            raise ValueError(f"Bad operation: {o!r}")
        item = {"op": op, "number": number}
        if op == "replace":
            item["text"] = str(o.get("text", "")).strip()
        if op == "renumber":
            item["new_number"] = str(o.get("new_number", "")).strip().rstrip(".")
            if not item["new_number"]:
                raise ValueError(f"Renumber of {number} needs new_number.")
        ops.append(item)
    if not ops:
        raise ValueError("No operations given.")
    return ops


def _locate(blocks_by_file: dict, number: str):
    """(path, index) of the block whose rule number is exactly `number`."""
    for path, blocks in blocks_by_file.items():
        for i, b in enumerate(blocks):
            m = rules._RULE_START.match(b.strip())
            if m and m.group(1) == number:
                return path, i
    return None


def _has_descendants(blocks_by_file: dict, number: str) -> bool:
    prefix = number + "."
    for blocks in blocks_by_file.values():
        for b in blocks:
            m = rules._RULE_START.match(b.strip())
            if m and m.group(1).startswith(prefix):
                return True
    return False


def plan(raw_ops) -> Plan:
    try:
        ops = _norm_ops(raw_ops)
    except ValueError as exc:
        return Plan(ops=[], error=str(exc))

    original = _load_all()
    blocks_by_file = {p: rules._blocks(t) for p, t in original.items()}
    touched = set()
    touched_numbers = []  # numbers deleted or renumbered away (for warnings)

    for o in ops:
        n = o["number"]
        loc = _locate(blocks_by_file, n)
        if loc is None:
            return Plan(ops=ops, error=f"No rule {n} found (while applying '{o['op']} {n}').")
        path, idx = loc
        blocks = blocks_by_file[path]

        if o["op"] == "replace":
            text = o["text"]
            m = rules._RULE_START.match(text)
            if not m or m.group(1) != n:
                return Plan(ops=ops, error=f"Replacement text for {n} must still start with '{n}. '.")
            blocks[idx] = text
        elif o["op"] == "delete":
            if _has_descendants(blocks_by_file, n):
                return Plan(ops=ops, error=f"Rule {n} still has sub-rules; delete or move those first, explicitly.")
            del blocks[idx]
            touched_numbers.append(n)
        else:  # renumber
            new = o["new_number"]
            if new.split(".")[0] != n.split(".")[0]:
                return Plan(ops=ops, error=f"Renumber {n} -> {new} would move it to another section; not supported.")
            if _locate(blocks_by_file, new) is not None:
                return Plan(ops=ops, error=f"Rule {new} already exists (while renumbering {n}); delete or renumber it first.")
            # the rule and its sub-rules (n.x...) change prefix, in place
            for fp, bl in blocks_by_file.items():
                for i, b in enumerate(bl):
                    s = b.strip()
                    m = rules._RULE_START.match(s)
                    if not m:
                        continue
                    num = m.group(1)
                    if num == n or num.startswith(n + "."):
                        bl[i] = new + num[len(n):] + s[len(num):]
            touched_numbers.append(n)

        touched.add(path)

    updated = {}
    for path in touched:
        updated[path] = "\n\n".join(b.rstrip("\n") for b in blocks_by_file[path]) + "\n"
    after = dict(original)
    after.update(updated)

    diff_lines = []
    for path in sorted(updated):
        diff_lines.extend(difflib.unified_diff(
            original[path].splitlines(), updated[path].splitlines(),
            fromfile=path.name, tofile=path.name, n=1, lineterm="",
        ))

    before_bad = set(check_references(original))
    new_bad = [x for x in check_references(after) if x not in before_bad]
    # mentions of numbers this change deleted/renumbered: meaning may have changed
    _, refs_before = _defined_and_refs(original)
    warnings = sorted({f"Rule {ctx} mentions {ref}, which this change deletes or renumbers"
                       for ctx, ref in refs_before if ref in touched_numbers and ctx != "-"})

    desc = []
    for o in ops:
        desc.append({"replace": f"edit {o['number']}", "delete": f"delete {o['number']}",
                     "renumber": f"renumber {o['number']} -> {o.get('new_number')}"}[o["op"]])
    return Plan(ops=ops, original=original, updated=updated, diff="\n".join(diff_lines),
                new_dangling=new_bad, warnings=warnings, summary="; ".join(desc))


def apply(p: Plan, editor_name: str, editor_email: str) -> str:
    """Writes the planned files and commits once. Returns the short commit hash.
    Raises RuntimeError if any file changed on disk since the plan was made."""
    if p.error or not p.updated:
        raise RuntimeError(p.error or "Nothing to apply.")
    for path, text in p.original.items():
        if path in p.updated and path.read_text(encoding="utf-8") != text:
            raise RuntimeError("The rule book changed since this was proposed - please ask again.")
    repo_dir = rules.RULES_DIR.parent
    written = []
    try:
        for path, text in p.updated.items():
            path.write_text(text, encoding="utf-8")
            written.append(path)
        for path in written:
            subprocess.run(["git", "add", str(path.relative_to(repo_dir))], cwd=repo_dir, check=True)
        subprocess.run(
            ["git", "-c", f"user.name={editor_name}", "-c", f"user.email={editor_email}",
             "commit", "-m", f"Rule changes (via Discord): {p.summary}"[:200]],
            cwd=repo_dir, check=True, capture_output=True, text=True,
        )
    except Exception:
        for path in written:  # roll back so disk and git never disagree
            path.write_text(p.original[path], encoding="utf-8")
        subprocess.run(["git", "reset", "-q", "HEAD", "--"] + [str(w.relative_to(repo_dir)) for w in written],
                       cwd=repo_dir, check=False)
        raise
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True)
    return rev.stdout.strip()
