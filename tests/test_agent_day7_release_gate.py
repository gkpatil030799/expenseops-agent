from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.read_tools import build_read_tool_registry
from app.agent.runtime import AgentRuntimeError, _sdk_tool
from app.config import Settings
from scripts.agent_day7_gate_cases import (
    BETA_EVAL_CASES,
    CHAOS_DRILLS,
    PROMPT_INJECTION_DRILLS,
    TENANCY_DRILLS,
)

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_BETA_CATEGORIES = {
    "financial": 6,
    "household": 4,
    "receipts": 3,
    "deals": 3,
    "errands": 3,
    "integrations": 1,
    "context": 6,
    "multi_domain": 5,
    "safety": 13,
    "failure": 6,
}
EXPECTED_PROMPT_INJECTION_FIELDS = [
    "merchant",
    "transaction description",
    "receipt line",
    "promotion headline",
    "promotion promo code",
    "errand title",
    "errand place",
    "household item name",
    "conversation text",
    "page context",
    "multi-tool combination",
]
EXPECTED_TENANCY_PATHS = [
    "conversation ID guessing",
    "run ID guessing",
    "contextual remote ID",
    "model-selected remote ID",
    "receipt child data",
    "deal ID",
    "household item ID",
    "errand and plan IDs",
    "mixed local and remote second tool",
    "same-workspace other private owner",
]


def _target_function(nodeid: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    path_value, function_name = nodeid.split("::", maxsplit=1)
    tree = ast.parse((ROOT / path_value).read_text(encoding="utf-8"))
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    assert len(matches) == 1, f"missing or ambiguous semantic gate target: {nodeid}"
    return matches[0]


def _has_semantic_assertion(target: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(target):
        if isinstance(node, ast.Assert):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "raises"
        ):
            return True
    return False


def _target_source(nodeid: str) -> str:
    path_value, _function_name = nodeid.split("::", maxsplit=1)
    target = _target_function(nodeid)
    lines = (ROOT / path_value).read_text(encoding="utf-8").splitlines()
    assert target.end_lineno is not None
    start_lineno = min([target.lineno, *(decorator.lineno for decorator in target.decorator_list)])
    return "\n".join(lines[start_lineno - 1 : target.end_lineno])


def _assert_semantic_case(case) -> None:
    target = _target_function(case.nodeid)
    assert _has_semantic_assertion(target), case.nodeid
    source = _target_source(case.nodeid)
    for marker in case.source_markers:
        assert marker in source, f"{case.nodeid} is missing semantic marker {marker!r}"


def test_day7_beta_registry_maps_exactly_50_requirements_to_semantic_assertions():
    assert [case.case_id for case in BETA_EVAL_CASES] == [f"{value:02d}" for value in range(1, 51)]
    assert {
        category: sum(case.category == category for case in BETA_EVAL_CASES)
        for category in EXPECTED_BETA_CATEGORIES
    } == EXPECTED_BETA_CATEGORIES
    for case in BETA_EVAL_CASES:
        _assert_semantic_case(case)


def test_day7_chaos_registry_maps_exactly_17_drills_to_semantic_assertions():
    assert [case.case_id for case in CHAOS_DRILLS] == [f"{value:02d}" for value in range(1, 18)]
    assert len({case.name for case in CHAOS_DRILLS}) == 17
    for case in CHAOS_DRILLS:
        _assert_semantic_case(case)


def test_day7_prompt_injection_registry_maps_every_required_source_and_combination():
    assert [case.case_id for case in PROMPT_INJECTION_DRILLS] == [
        f"{value:02d}" for value in range(1, 12)
    ]
    assert [case.name for case in PROMPT_INJECTION_DRILLS] == (EXPECTED_PROMPT_INJECTION_FIELDS)
    for case in PROMPT_INJECTION_DRILLS:
        assert case.category == "prompt_injection"
        _assert_semantic_case(case)


def test_day7_tenancy_registry_maps_all_ten_explicit_isolation_paths():
    assert [case.case_id for case in TENANCY_DRILLS] == [f"{value:02d}" for value in range(1, 11)]
    assert [case.name for case in TENANCY_DRILLS] == EXPECTED_TENANCY_PATHS
    for case in TENANCY_DRILLS:
        assert case.category == "tenancy"
        _assert_semantic_case(case)


def test_registry_exposes_no_secret_or_arbitrary_execution_capability():
    registry = build_read_tool_registry(
        Settings(
            _env_file=None,
            agent_enabled=True,
            agent_read_tools_enabled=True,
            agent_write_actions_enabled=False,
            agent_proactive_enabled=False,
            agent_purchasing_enabled=False,
        )
    )
    metadata = registry.metadata()
    names = {item.name for item in metadata}

    assert names == {
        "get_classification_activity",
        "get_spending_insights",
        "get_lifestyle_dining_insights",
        "search_transactions",
        "get_household_replenishment",
        "get_receipts",
        "get_relevant_deals",
        "get_errands_and_plan",
        "get_integration_status",
    }
    assert {item.effect for item in metadata} == {"read"}
    forbidden_fragments = {
        "sql",
        "shell",
        "python",
        "url",
        "http",
        "secret",
        "token",
        "write",
        "execute",
        "order",
        "purchase",
        "splitwise",
    }
    assert not any(
        fragment in name.casefold() for name in names for fragment in forbidden_fragments
    )


@pytest.mark.parametrize("raw_arguments", ["{not-json", "[]"])
def test_day7_malformed_provider_tool_payload_fails_before_executor(raw_arguments):
    metadata = SimpleNamespace(
        name="search_transactions",
        description="Read matching transaction rows.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def invoke(self, _tool_name, _arguments):
            self.calls += 1
            raise AssertionError("malformed provider payload must not reach the executor")

    executor = RecordingExecutor()
    tool = _sdk_tool(metadata, executor)

    with pytest.raises(AgentRuntimeError) as raised:
        asyncio.run(tool.on_invoke_tool(None, raw_arguments))

    assert raised.value.code == "invalid_tool_arguments"
    assert executor.calls == 0
