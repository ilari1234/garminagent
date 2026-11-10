"""The Claude Code hook that blocks shell commands naming `.env` files or `data/`.

Runs the script as Claude Code does (JSON on stdin, exit code 2 blocks) so the test covers
the real interface, not just the matcher.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "claude_hooks" / "guard_private_paths.py"
ROOT = "C:\\work\\example-repo"


def run_hook(payload: object, *, raw: str | None = None) -> subprocess.CompletedProcess[str]:
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(  # noqa: S603  (fixed interpreter and script, no shell)
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_PROJECT_DIR": ROOT + "\\.claude\\worktrees\\some-branch"},
    )


def shell(command: str, tool: str = "Bash") -> dict[str, object]:
    return {"tool_name": tool, "tool_input": {"command": command}, "cwd": ROOT}


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "cat ./.env",
        "type .env.local",
        "grep TOKEN config/.env",
        "source .env && env",
        "uv run --env-file .env garminagent status",
        "cp .env.example .env",
        "cat data/spike/report.json",
        "ls ./data/",
        "ls ../data/raw",
        "find data -name '*.json'",
        "du -sh data",
        "tar czf out.tgz data",
        "cat C:/work/example-repo/data/raw/x.json",
        "cat /c/work/example-repo/data/raw/x.json",
        "head C:\\work\\example-repo\\data\\raw\\x.json",
        "cat .e*",
        "cat .en?",
        "ls d?ta/",
        "ls da*",
    ],
)
def test_blocks_bash(command: str) -> None:
    result = run_hook(shell(command))
    assert result.returncode == 2, command
    assert "guard_private_paths" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "Get-Content .env",
        "Get-Content -Path .\\data\\raw\\x.json",
        "Get-ChildItem data",
        "Select-String TOKEN .\\.env.local",
    ],
)
def test_blocks_powershell(command: str) -> None:
    assert run_hook(shell(command, tool="PowerShell")).returncode == 2, command


@pytest.mark.parametrize(
    "command",
    [
        "cat .env.example",
        "git diff .env.example",
        "uv run pytest",
        "ls .venv",
        "echo $env:PATH",
        "python -c 'import os; os.environ'",
        "cat src/garminagent/storage/metadata.py",
        "ls tests/fixtures/data_sample",
        "grep -rn TEST_DATABASE_URL tests",
        'git commit -m "Keep data handling rules in one place"',
        "gh pr create --title 'Count data rows' --body-file body.md",
        "ls docs",
        "cat C:/work/example-repo/docs/plan.md",
        "cat C:/other/data-tools/readme.md",
        # Markdown bold in a heredoc, not a glob (a false positive found in use).
        "python - <<'EOF'\ntext = '**Phase 1 is done** (2026)'\nEOF",
        "cat > notes.md <<'EOF'\n**Draft** notes, **done** today\nEOF",
    ],
)
def test_allows(command: str) -> None:
    result = run_hook(shell(command))
    assert result.returncode == 0, (command, result.stderr)


def test_allows_tools_without_a_command() -> None:
    assert run_hook({"tool_name": "Bash", "tool_input": {}}).returncode == 0


def test_blocks_unreadable_input() -> None:
    assert run_hook(None, raw="not json").returncode == 2


def test_message_does_not_echo_the_command() -> None:
    result = run_hook(shell("cat data/raw/secret-name.json"))
    assert "secret-name" not in result.stderr
