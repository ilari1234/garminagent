"""Print a Terraform plan as resource addresses and actions only.

Plan output holds ARNs, account IDs, endpoints and attribute values, and this repo's CI logs,
PRs and chats are public (ADR 0008). The owner shares this summary instead of the plan:

    terraform plan -out=plan.tfplan
    terraform show -json plan.tfplan | python infra/scripts/plan_summary.py

Only three things are printed per change: its address, its action (from a fixed set) and its
reason (only if it is one of Terraform's known enum values). Attribute values, outputs,
provider config, import IDs and diagnostics are never read into the output, so a new field in
Terraform's JSON format can't leak through here.

Addresses include `for_each` keys, so resources must never be keyed by an account ID, email
or secret (infra/README.md). Standard library only: it runs on the owner's machine without
the project environment.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TextIO, cast

# Terraform's action lists (`change.actions`, internal/command/jsonplan in v1.17.0) mapped to
# the words and symbols `terraform plan` uses. Anything else prints as "unknown" rather than
# echoing the input.
ACTIONS: Mapping[tuple[str, ...], tuple[str, str]] = {
    ("create",): ("create", "+"),
    ("update",): ("update", "~"),
    ("delete",): ("delete", "-"),
    ("delete", "create"): ("replace", "-/+"),
    ("create", "delete"): ("replace", "+/-"),
    ("forget", "create"): ("replace", "./+"),
    ("create", "forget"): ("replace", "+/."),
    ("read",): ("read", "<="),
    ("forget",): ("forget", "."),
}
NO_OP = ("no-op",)
UNKNOWN = ("unknown", "?")
SUMMARY_ORDER = ("create", "update", "replace", "delete", "read", "forget", "unknown")

# Terraform's `action_reason` values (same source): a reason is printed only if listed here.
REASONS = frozenset(
    {
        "replace_because_tainted",
        "replace_because_cannot_update",
        "replace_by_request",
        "replace_by_triggers",
        "delete_because_no_resource_config",
        "delete_because_wrong_repetition",
        "delete_because_count_index",
        "delete_because_each_key",
        "delete_because_no_module",
        "delete_because_no_move_target",
        "read_because_config_unknown",
        "read_because_dependency_pending",
        "read_because_check_nested",
    }
)


class PlanFormatError(ValueError):
    """The input isn't `terraform show -json` output. Messages never quote the input."""


@dataclass(frozen=True)
class Change:
    address: str
    action: str
    symbol: str
    reason: str | None


def parse(plan: object) -> list[Change]:
    """Extract address, action and reason of every resource change that isn't a no-op."""
    root = _object(plan, "plan")
    if "format_version" not in root:
        raise PlanFormatError("not a Terraform JSON plan (no format_version)")
    changes: list[Change] = []
    for index, item in enumerate(_list(root.get("resource_changes", []), "resource_changes")):
        change = _parse_change(_object(item, f"resource_changes[{index}]"), index)
        if change is not None:
            changes.append(change)
    return changes


def _parse_change(item: dict[str, object], index: int) -> Change | None:
    address = item.get("address")
    if not isinstance(address, str):
        raise PlanFormatError(f"resource_changes[{index}] has no address")
    actions = _list(_object(item.get("change"), f"resource_changes[{index}].change").get("actions"), "actions")
    if not all(isinstance(action, str) for action in actions):
        raise PlanFormatError(f"resource_changes[{index}] has a non-string action")
    key = tuple(cast(list[str], actions))
    if key == NO_OP:
        return None
    action, symbol = ACTIONS.get(key, UNKNOWN)
    reason = item.get("action_reason")
    return Change(
        address=_printable(address),
        action=action,
        symbol=symbol,
        reason=reason if isinstance(reason, str) and reason in REASONS else None,
    )


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PlanFormatError(f"{where} is not an object")
    return cast(dict[str, object], value)


def _list(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise PlanFormatError(f"{where} is not a list")
    return cast(list[object], value)


def _printable(address: str) -> str:
    """Escape control characters a `for_each` key could carry into the terminal."""
    if address.isprintable():
        return address
    return address.encode("unicode_escape").decode("ascii")


def render(changes: Iterable[Change]) -> str:
    ordered = sorted(changes, key=lambda change: change.address)
    if not ordered:
        return "No changes.\n"
    width = max(len(change.symbol) for change in ordered)
    lines = [
        f"{change.symbol:>{width}} {change.address}" + (f"  ({change.reason})" if change.reason else "")
        for change in ordered
    ]
    counts = Counter(change.action for change in ordered)
    summary = ", ".join(f"{counts[action]} to {action}" for action in SUMMARY_ORDER if counts[action])
    return "\n".join([*lines, "", f"Plan: {summary}.", ""])


def main(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    try:
        plan: object = json.load(stdin)
    except json.JSONDecodeError:
        stderr.write("plan_summary: input is not JSON; pipe in `terraform show -json <planfile>`\n")
        return 2
    try:
        stdout.write(render(parse(plan)))
    except PlanFormatError as exc:
        stderr.write(f"plan_summary: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
