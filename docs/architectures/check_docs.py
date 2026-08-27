"""Verify the architecture docs against the C4 model.

Implements checks 3-5 of `docs/architectures/README.md` §5:

3. every `view_key` / `also_narrates` entry resolves to a view in `workspace.dsl`,
   and every view in `workspace.dsl` is claimed by exactly one document;
4. every relative link in this directory resolves;
5. every `owns:` path still exists.

Also enforces the frontmatter contract from README §3 and the fixed section order
from `TEMPLATE.md`.

Run from the repository root:

    uv run python docs/architectures/check_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
REPO = DOCS.parent.parent
WORKSPACE = DOCS / "workspace.dsl"

REQUIRED_KEYS = ("c4_level", "view_key", "diagram", "owns", "status", "last_verified")
VALID_LEVELS = {"1", "2", "3", "index"}
VALID_STATUS = {"implemented", "partial", "deprecated"}

# TEMPLATE.md section order. Index-level documents (README, TEMPLATE) are exempt.
SECTIONS = (
    "1. Responsibilities",
    "2. Elements",
    "3. Interfaces",
    "4. Invariants",
    "5. Failure and degradation",
    "6. Known gaps",
    "7. Related",
)
# Sections TEMPLATE.md marks as omittable.
OPTIONAL_SECTIONS = {"3. Interfaces", "5. Failure and degradation"}

# Documents that define the harness rather than narrate a view.
META_DOCS = {"README.md", "TEMPLATE.md"}

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")
_VIEW = re.compile(
    r"^\s*(?:systemLandscape|systemContext|container|component|dynamic|deployment|filtered|custom)\b[^\n]*$",
    re.MULTILINE,
)


def fail(problems: list[str], message: str) -> None:
    problems.append(message)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    match = _FRONTMATTER.match(text)
    if match is None:
        return None
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def parse_list(value: str) -> list[str]:
    value = value.strip()
    if not value or value == "null":
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip("'\"") for item in value.split(",") if item.strip()]


def workspace_view_keys(problems: list[str]) -> set[str]:
    """Collect view keys from workspace.dsl.

    The key is the first quoted token on a view declaration, except for a
    `deployment` view, where the environment name comes first and the key second.
    """
    if not WORKSPACE.exists():
        fail(problems, f"missing {WORKSPACE.relative_to(REPO)}")
        return set()

    keys: set[str] = set()
    for line in _VIEW.findall(WORKSPACE.read_text(encoding="utf-8")):
        quoted = re.findall(r'"([^"]*)"', line)
        if not quoted:
            continue
        keyword = line.split()[0]
        if keyword == "deployment":
            # deployment <scope> <environment> <key> <description>
            keys.add(quoted[1] if len(quoted) > 1 else quoted[0])
        elif keyword in {"systemLandscape", "custom", "filtered"}:
            keys.add(quoted[0])
        else:
            # <keyword> <scope> <key> <description>
            keys.add(quoted[0])
    return keys


def check_links(path: Path, text: str, problems: list[str]) -> None:
    # Fenced blocks hold worked examples (TEMPLATE.md) whose links are illustrative.
    for inline, image in _LINK.findall(_FENCE.sub("", text)):
        target = inline or image
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        if not (path.parent / target).exists():
            fail(problems, f"{path.relative_to(REPO)}: broken link -> {target}")


def check_sections(path: Path, text: str, problems: list[str]) -> None:
    found = re.findall(r"^## (.+)$", text, re.MULTILINE)
    found = [heading for heading in found if not heading.startswith("Appendix")]
    expected = [section for section in SECTIONS if section in found]
    missing = [
        section
        for section in SECTIONS
        if section not in found and section not in OPTIONAL_SECTIONS
    ]
    if missing:
        fail(problems, f"{path.relative_to(REPO)}: missing section(s) {', '.join(missing)}")
    if found != expected:
        fail(
            problems,
            f"{path.relative_to(REPO)}: sections out of TEMPLATE.md order -> {found}",
        )


def main() -> int:
    problems: list[str] = []
    views = workspace_view_keys(problems)
    claimed: dict[str, str] = {}

    documents = sorted(DOCS.glob("*.md"))
    if not documents:
        fail(problems, "no architecture documents found")

    for path in documents:
        text = path.read_text(encoding="utf-8")
        name = path.name
        rel = path.relative_to(REPO)

        front = parse_frontmatter(text)
        if front is None:
            fail(problems, f"{rel}: missing frontmatter block")
            continue

        for key in REQUIRED_KEYS:
            if key not in front:
                fail(problems, f"{rel}: frontmatter missing '{key}'")

        level = front.get("c4_level", "")
        if level not in VALID_LEVELS:
            fail(problems, f"{rel}: c4_level '{level}' not in {sorted(VALID_LEVELS)}")

        status = front.get("status", "")
        if status not in VALID_STATUS:
            fail(problems, f"{rel}: status '{status}' not in {sorted(VALID_STATUS)}")

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", front.get("last_verified", "")):
            fail(problems, f"{rel}: last_verified must be an ISO date")

        owns = front.get("owns", "")
        if owns and not (REPO / owns).exists():
            fail(problems, f"{rel}: owns path does not exist -> {owns}")

        diagram = front.get("diagram", "null")
        if diagram != "null" and not (path.parent / diagram).exists():
            fail(problems, f"{rel}: diagram does not exist -> {diagram}")

        keys = parse_list(front.get("view_key", "")) + parse_list(
            front.get("also_narrates", "")
        )
        for key in keys:
            if key not in views:
                fail(problems, f"{rel}: view_key '{key}' is not a view in workspace.dsl")
            elif key in claimed:
                fail(problems, f"{rel}: view '{key}' already claimed by {claimed[key]}")
            else:
                claimed[key] = name

        check_links(path, text, problems)
        if name not in META_DOCS:
            check_sections(path, text, problems)
            if status == "partial" and "## 6. Known gaps" not in text:
                fail(problems, f"{rel}: status is 'partial' but no 'Known gaps' section")

    for view in sorted(views - set(claimed)):
        fail(problems, f"workspace.dsl: view '{view}' has no document")

    if problems:
        print(f"FAIL: {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: {len(documents)} document(s), {len(views)} view(s), all claimed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
