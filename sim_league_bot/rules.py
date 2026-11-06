"""Loads the rule book from rules/*.md (one file per top-level numbered
section, see the repo README for why) and finds/edits individual numbered
rules within a section file. Git (in this repo's working tree) is the
version-history mechanism - every edit here is followed by a commit."""
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

# Matches the start of a numbered rule paragraph, e.g. "3.4.2.1. " or "10.4. "
_RULE_START = re.compile(r"^(\d+(?:\.\d+)*)\.\s")


def load_rulebook_text() -> str:
    """The whole rule book, concatenated in section order, for Claude's context."""
    parts = []
    for path in sorted(RULES_DIR.glob("*.md")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _section_file_for(rule_number: str) -> Optional[Path]:
    top = rule_number.split(".")[0]
    matches = sorted(RULES_DIR.glob(f"{int(top):02d}-*.md"))
    return matches[0] if matches else None


@dataclass
class RuleBlock:
    file: Path
    rule_number: str
    text: str  # full paragraph text, including the leading "N.N. " prefix
    start: int  # index into the file's list of blocks
    end: int


def _blocks(text: str) -> list:
    """Splits a section file's body into blank-line-separated paragraphs,
    keeping each paragraph's raw text (including any sub-bullets that
    follow it before the next blank line)."""
    return [b for b in re.split(r"\n\s*\n", text) if b.strip()]


def find_rule(rule_number: str) -> Optional[RuleBlock]:
    """Finds the exact paragraph whose rule number matches (e.g. "3.4.2.1"),
    not a prefix match - "3.4" won't match "3.4.2.1"."""
    path = _section_file_for(rule_number)
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    blocks = _blocks(text)
    for i, block in enumerate(blocks):
        m = _RULE_START.match(block.strip())
        if m and m.group(1) == rule_number:
            return RuleBlock(file=path, rule_number=rule_number, text=block.strip(), start=i, end=i)
    return None


def replace_rule(rule_number: str, new_text: str, editor_name: str, editor_email: str) -> str:
    """Replaces one rule paragraph's text in its section file and commits
    the change. Returns the git commit's short hash. Raises ValueError if
    the rule number isn't found, or RuntimeError if new_text no longer
    starts with the same rule number (editing a rule into a different
    numbered rule would silently corrupt citations elsewhere)."""
    block = find_rule(rule_number)
    if block is None:
        raise ValueError(f"No rule {rule_number} found.")
    new_text = new_text.strip()
    m = _RULE_START.match(new_text)
    if not m or m.group(1) != rule_number:
        raise RuntimeError(f"Edited text must still start with '{rule_number}. '")

    text = block.file.read_text(encoding="utf-8")
    blocks = _blocks(text)
    blocks[block.start] = new_text
    new_file_text = "\n\n".join(blocks) + "\n"
    block.file.write_text(new_file_text, encoding="utf-8")

    repo_dir = block.file.parent.parent
    subprocess.run(["git", "add", str(block.file.relative_to(repo_dir))], cwd=repo_dir, check=True)
    result = subprocess.run(
        [
            "git",
            "-c", f"user.name={editor_name}",
            "-c", f"user.email={editor_email}",
            "commit", "-m", f"Edit rule {rule_number} (via Discord)",
        ],
        cwd=repo_dir, check=True, capture_output=True, text=True,
    )
    print(result.stdout, file=sys.stderr)
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True)
    return rev.stdout.strip()


def diff_since(ref: str) -> str:
    """Plain-text diff of the rules/ folder since a given ref (e.g. a season tag)."""
    repo_dir = RULES_DIR.parent
    result = subprocess.run(
        ["git", "diff", ref, "--", "rules/"], cwd=repo_dir, check=True, capture_output=True, text=True,
    )
    return result.stdout
