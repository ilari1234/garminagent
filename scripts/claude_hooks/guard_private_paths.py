"""Claude Code PreToolUse hook: block shell commands that name `.env` files or `data/`.

Why: the deny rules in `.claude/settings.json` cover the Read tool only, so `cat .env` or
`Get-Content data\\raw\\x.json` through Bash or PowerShell got past them. This hook checks
the command text of those two tools. It is a backstop on command text, not a boundary: a
script that opens files itself, a recursive search from the repo root, or an obfuscated
path still gets through (SECURITY.md, threats and controls).

Exit 2 blocks the call and shows stderr to Claude; exit 0 lets it through. A payload that
can't be parsed is blocked too (fail closed). Standard library only, so it runs with any
Python 3 on PATH without the project's virtual environment.
"""

import json
import os
import re
import sys
from typing import cast

# Characters that can come right before a path in a shell command (start of a word,
# after a quote, `=`, a redirect, a pipe and so on).
_BEFORE = r"""(?<![^\s'"`=:;|&(<>,])"""
# `.env` may also follow a directory separator (`config/.env`).
_BEFORE_ENV = r"""(?<![^\s'"`=:;|&(<>,/\\])"""
_RELATIVE = r"(?:\.{1,2}[/\\])*"

_ENV = re.compile(_BEFORE_ENV + r"\.env(?:\.[\w.-]+)?(?![\w.-])", re.IGNORECASE)
_ENV_ALLOWED = frozenset({".env.example"})
_DATA_DIR = re.compile(_BEFORE + _RELATIVE + r"data[/\\]", re.IGNORECASE)
# A bare `data` word (`ls data`). Skipped for git and gh, whose messages are prose.
_DATA_WORD = re.compile(_BEFORE + _RELATIVE + r"data(?=$|[\s'\"`;|&)<>,])", re.IGNORECASE)
# Globs that could expand to either (`cat .e*`, `ls d?ta/`). A doubled `**` right after a word
# is Markdown bold in a heredoc (`done**`), not a glob, so it doesn't count.
_ENV_GLOB = re.compile(_BEFORE_ENV + r"\.e[\w.-]*(?:\?|\[|\*(?!\*))", re.IGNORECASE)
_DATA_GLOB = re.compile(_BEFORE + _RELATIVE + r"d[\w-]{0,3}(?:\?|\*(?!\*))", re.IGNORECASE)
_PROSE_COMMANDS = re.compile(r"^\s*(?:git|gh)\s", re.IGNORECASE)

_MESSAGE = (
    "Blocked by scripts/claude_hooks/guard_private_paths.py: the command names {what}. `.env*` holds "
    "secrets and `data/` holds personal health data; CLAUDE.md forbids reading them. Don't "
    "work around this. If the match is prose (a commit or PR message), write the text to a "
    "file with the Write tool and pass it with -F or --body-file. If you need something from "
    "these paths, ask the user to run a structure-only script in their own terminal."
)


def _command_from(payload: object) -> str | None:
    """The shell command in a hook payload, or None when the call carries none."""
    if not isinstance(payload, dict):
        return None
    tool_input = cast(dict[str, object], payload).get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = cast(dict[str, object], tool_input).get("command")
    return command if isinstance(command, str) else None


def _roots(payload: object) -> list[str]:
    """Spellings of the main checkout's root, for absolute paths to `data/`.

    `data/` lives in the main checkout only (it is gitignored), so a session in a worktree
    under `.claude/worktrees/` must still match the main checkout's path.
    """
    candidates: list[str] = []
    if env_dir := os.environ.get("CLAUDE_PROJECT_DIR"):
        candidates.append(env_dir)
    if isinstance(payload, dict):
        cwd = cast(dict[str, object], payload).get("cwd")
        if isinstance(cwd, str):
            candidates.append(cwd)
    roots: set[str] = set()
    for candidate in candidates:
        path = candidate.replace("\\", "/").rstrip("/").lower()
        path = path.split("/.claude/worktrees/", 1)[0]
        roots.add(path)
        # Git Bash spells C:/x as /c/x, and the reverse.
        if re.match(r"^[a-z]:/", path):
            roots.add(f"/{path[0]}{path[2:]}")
        elif re.match(r"^/[a-z]/", path):
            roots.add(f"{path[1]}:{path[2:]}")
    return sorted(root for root in roots if root)


def find_violation(command: str, roots: list[str]) -> str | None:
    """What the command names that it mustn't, or None. Pure, for the tests."""
    for match in _ENV.finditer(command):
        if match.group(0).lower() not in _ENV_ALLOWED:
            return f"`{match.group(0)}`"
    if _DATA_DIR.search(command):
        return "the `data/` directory"
    if not _PROSE_COMMANDS.match(command) and _DATA_WORD.search(command):
        return "the `data/` directory"
    normalized = command.replace("\\", "/").lower()
    for root in roots:
        if re.search(re.escape(root) + r"/data(?:$|[/\s'\"`;|&)<>,])", normalized):
            return "the `data/` directory"
    if _ENV_GLOB.search(command) or _DATA_GLOB.search(command):
        return "a glob that could expand to `.env*` or `data/`"
    return None


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload: object = json.loads(raw)
    except ValueError:
        print("Blocked by scripts/claude_hooks/guard_private_paths.py: unreadable hook input.", file=sys.stderr)
        return 2
    command = _command_from(payload)
    if command is None:
        return 0
    what = find_violation(command, _roots(payload))
    if what is None:
        return 0
    print(_MESSAGE.format(what=what), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
