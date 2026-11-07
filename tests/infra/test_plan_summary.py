import io
import json

import pytest

from plan_summary import Change, main, parse, render

SENTINEL = "SENTINEL-VALUE-0000"


def change(address: str, *actions: str, reason: str | None = None) -> dict[str, object]:
    detail: dict[str, object] = {
        "actions": list(actions),
        "before": {"arn": SENTINEL},
        "after": {"arn": SENTINEL, "tags": {"owner": SENTINEL}},
        "after_unknown": {"id": True},
        "importing": {"id": SENTINEL},
    }
    item: dict[str, object] = {"address": address, "type": "aws_s3_bucket", "name": "x", "change": detail}
    if reason is not None:
        item["action_reason"] = reason
    return item


def plan(*changes: dict[str, object]) -> dict[str, object]:
    return {
        "format_version": "1.2",
        "terraform_version": "1.17.0",
        "variables": {"account_id": {"value": SENTINEL}},
        "planned_values": {"outputs": {"bucket": {"value": SENTINEL}}},
        "output_changes": {"bucket": {"actions": ["create"], "after": SENTINEL}},
        "configuration": {"provider_config": {"aws": {"expressions": {"region": {"constant_value": SENTINEL}}}}},
        "resource_changes": list(changes),
    }


def run(payload: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(io.StringIO(payload), stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_prints_addresses_actions_and_known_reasons_only() -> None:
    code, out, err = run(
        json.dumps(
            plan(
                change("aws_s3_bucket.state", "create"),
                change("aws_kms_key.state", "update"),
                change("aws_iam_role.ci", "delete", "create", reason="replace_because_cannot_update"),
                change("aws_iam_policy.old", "delete", reason="delete_because_no_resource_config"),
                change("data.aws_caller_identity.current", "read"),
                change("aws_s3_bucket.unchanged", "no-op"),
            )
        )
    )

    assert (code, err) == (0, "")
    assert out == (
        "  - aws_iam_policy.old  (delete_because_no_resource_config)\n"
        "-/+ aws_iam_role.ci  (replace_because_cannot_update)\n"
        "  ~ aws_kms_key.state\n"
        "  + aws_s3_bucket.state\n"
        " <= data.aws_caller_identity.current\n"
        "\n"
        "Plan: 1 to create, 1 to update, 1 to replace, 1 to delete, 1 to read.\n"
    )


def test_never_prints_values_unknown_actions_or_unknown_reasons() -> None:
    code, out, err = run(
        json.dumps(
            plan(
                change("aws_s3_bucket.state", "create", reason=SENTINEL),
                change("aws_s3_bucket.odd", SENTINEL),
            )
        )
    )

    assert code == 0
    assert SENTINEL not in out + err
    assert "? aws_s3_bucket.odd" in out
    assert "1 to unknown" in out


def test_all_replace_forms_count_as_replace() -> None:
    changes = parse(
        plan(
            change("a.one", "delete", "create"),
            change("a.two", "create", "delete"),
            change("a.three", "create", "forget"),
            change("a.four", "forget", "create"),
        )
    )

    assert {c.action for c in changes} == {"replace"}


def test_no_changes() -> None:
    assert run(json.dumps(plan(change("a.b", "no-op")))) == (0, "No changes.\n", "")
    assert run(json.dumps({"format_version": "1.2"})) == (0, "No changes.\n", "")


def test_escapes_control_characters_in_addresses() -> None:
    [parsed] = parse(plan(change('aws_s3_bucket.x["a\nb\x1b[2J"]', "create")))

    assert "\n" not in parsed.address
    assert "\x1b" not in parsed.address


@pytest.mark.parametrize(
    "payload",
    [
        "not json " + SENTINEL,
        json.dumps([SENTINEL]),
        json.dumps({"resource_changes": [], "secret": SENTINEL}),
        json.dumps({"format_version": "1.2", "resource_changes": SENTINEL}),
        json.dumps({"format_version": "1.2", "resource_changes": [SENTINEL]}),
        json.dumps({"format_version": "1.2", "resource_changes": [{"address": "a.b", "change": SENTINEL}]}),
        json.dumps({"format_version": "1.2", "resource_changes": [{"address": 1, "change": {"actions": []}}]}),
        json.dumps(
            {"format_version": "1.2", "resource_changes": [{"address": "a.b", "change": {"actions": [SENTINEL, 1]}}]}
        ),
    ],
)
def test_rejects_other_input_without_quoting_it(payload: str) -> None:
    code, out, err = run(payload)

    assert code == 2
    assert out == ""
    assert err.startswith("plan_summary: ")
    assert SENTINEL not in err


def test_render_sorts_by_address() -> None:
    text = render([Change("b.y", "create", "+", None), Change("a.x", "delete", "-", None)])

    assert text.splitlines()[:2] == ["- a.x", "+ b.y"]
