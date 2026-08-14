from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr
from sqlalchemy.orm import Session

from app.agent.contracts import AgentActionPreview
from app.config import Settings, get_settings


class AgentToolError(RuntimeError):
    """Base error for registry and policy failures."""


class DuplicateAgentToolError(AgentToolError):
    pass


class UnknownAgentToolError(AgentToolError):
    pass


class AgentToolPolicyError(AgentToolError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class UnsafeToolArgumentsError(AgentToolError):
    pass


class UnsafeToolOutputError(AgentToolError):
    pass


class ToolEffect(StrEnum):
    READ = "read"
    WRITE = "write"
    EXTERNAL_ACTION = "external_action"


class ToolDisposition(StrEnum):
    READY = "ready"
    EXECUTED = "executed"
    PROPOSAL_REQUIRED = "proposal_required"


class ToolCapability(StrEnum):
    STANDARD = "standard"
    PURCHASING = "purchasing"


@dataclass(frozen=True, slots=True)
class AgentToolContext:
    """Trusted execution context assembled by the server, never by model arguments."""

    db: Session
    workspace_id: int
    user_id: int
    request_id: str | None = None

    def __post_init__(self) -> None:
        if self.workspace_id < 1 or self.user_id < 1:
            raise ValueError("Agent tool context requires valid server tenant identifiers")
        if self.request_id is not None and len(self.request_id) > 64:
            raise ValueError("Agent tool request_id is too long")

    @classmethod
    def from_session(cls, db: Session, *, request_id: str | None = None) -> AgentToolContext:
        workspace_id = db.info.get("workspace_id")
        user_id = db.info.get("user_id")
        if not isinstance(workspace_id, int) or not isinstance(user_id, int):
            raise AgentToolPolicyError(
                "Agent tool session is missing authenticated tenant context",
                code="invalid_tool_context",
            )
        return cls(
            db=db,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request_id,
        )


AgentToolHandler = Callable[[AgentToolContext, BaseModel], BaseModel | Mapping[str, Any]]
AgentToolPreviewBuilder = Callable[
    [AgentToolContext, BaseModel],
    AgentActionPreview | Mapping[str, Any],
]


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    effect: ToolEffect
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: AgentToolHandler
    confirmation_required: bool = False
    capability: ToolCapability = ToolCapability.STANDARD
    preview_builder: AgentToolPreviewBuilder | None = None
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("Agent tool names must be bounded snake_case identifiers")
        if not self.description.strip() or len(self.description) > 500:
            raise ValueError("Agent tool descriptions must contain 1 to 500 characters")
        if not re.fullmatch(r"[1-9][0-9]*\.[0-9]+", self.version):
            raise ValueError("Agent tool versions must use a major.minor format")
        if not isinstance(self.effect, ToolEffect):
            raise TypeError("Agent tool effect must be a ToolEffect")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("Agent tool input_model must be a Pydantic model")
        if not isinstance(self.output_model, type) or not issubclass(self.output_model, BaseModel):
            raise TypeError("Agent tool output_model must be a Pydantic model")
        if not callable(self.handler):
            raise TypeError("Agent tool handler must be callable")
        if not isinstance(self.capability, ToolCapability):
            raise TypeError("Agent tool capability must be a ToolCapability")
        if self.effect is ToolEffect.READ:
            if self.confirmation_required:
                raise ValueError(
                    "READ tools execute directly and cannot require action confirmation"
                )
            if self.preview_builder is not None:
                raise ValueError("READ tools cannot define an action preview builder")
            if self.capability is ToolCapability.PURCHASING:
                raise ValueError("Purchasing tools must be consequential actions")
        else:
            if not self.confirmation_required:
                raise ValueError("WRITE and EXTERNAL_ACTION tools must require confirmation")
            if not callable(self.preview_builder):
                raise ValueError(
                    "WRITE and EXTERNAL_ACTION tools must define a code-owned preview builder"
                )
            if (
                self.capability is ToolCapability.PURCHASING
                and self.effect is not ToolEffect.EXTERNAL_ACTION
            ):
                raise ValueError("Purchasing tools must be external actions")


class AgentToolMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    name: str
    description: str
    effect: ToolEffect
    confirmation_required: bool
    capability: ToolCapability
    version: str
    input_schema: dict[str, Any]


class AgentToolDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    _registry_nonce: object | None = PrivateAttr(default=None)
    _registry_proof: str | None = PrivateAttr(default=None)
    _issued_workspace_id: int | None = PrivateAttr(default=None)
    _issued_user_id: int | None = PrivateAttr(default=None)
    _issued_at: float | None = PrivateAttr(default=None)

    tool_name: str
    tool_version: str
    effect: ToolEffect
    capability: ToolCapability = ToolCapability.STANDARD
    disposition: ToolDisposition
    normalized_arguments: dict[str, Any]
    preview: AgentActionPreview | None = None
    output: dict[str, Any] | None = None


class AgentToolRegistry:
    """Server allowlist for typed tools callable from future model output.

    `dispatch` is intentionally not a general executor. It can execute a READ
    handler only. Consequential tools return validated proposal material for a
    separate durable confirmation workflow; their handler is never called here.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        self._settings = settings or get_settings()
        self._dispatch_nonce = object()
        self._dispatch_secret = secrets.token_bytes(32)

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise DuplicateAgentToolError(f"Agent tool '{tool.name}' is already registered")
        _require_strict_model(tool.input_model, label="input")
        _require_strict_model(tool.output_model, label="output")
        _reject_forbidden_schema_keys(tool.input_model.model_json_schema(), label="input")
        _reject_forbidden_schema_keys(tool.output_model.model_json_schema(), label="output")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownAgentToolError(f"Unknown agent tool '{name}'") from None

    def metadata(self) -> list[AgentToolMetadata]:
        return [
            AgentToolMetadata(
                name=tool.name,
                description=tool.description,
                effect=tool.effect,
                confirmation_required=tool.confirmation_required,
                capability=tool.capability,
                version=tool.version,
                input_schema=tool.input_model.model_json_schema(),
            )
            for tool in self._tools.values()
        ]

    def prepare(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        context: AgentToolContext,
    ) -> AgentToolDispatchResult:
        tool = self.get(name)
        self._validate_context(context)
        if not self._settings.agent_enabled:
            raise AgentToolPolicyError("The ExpenseOps Agent is disabled", code="agent_disabled")

        _reject_forbidden_argument_keys(arguments)
        _require_finite_json(
            arguments,
            label="arguments",
            error_type=UnsafeToolArgumentsError,
        )
        validated_arguments = tool.input_model.model_validate(dict(arguments), strict=True)
        normalized_arguments = validated_arguments.model_dump(mode="json")
        _require_finite_json(
            normalized_arguments,
            label="arguments",
            error_type=UnsafeToolArgumentsError,
        )

        if tool.effect is ToolEffect.READ:
            if not self._settings.agent_read_tools_enabled:
                raise AgentToolPolicyError(
                    "Agent read tools are disabled",
                    code="read_tools_disabled",
                )
            return self._issue(
                AgentToolDispatchResult(
                    tool_name=tool.name,
                    tool_version=tool.version,
                    effect=tool.effect,
                    capability=tool.capability,
                    disposition=ToolDisposition.READY,
                    normalized_arguments=normalized_arguments,
                ),
                context=context,
            )

        if not self._settings.agent_write_actions_enabled:
            raise AgentToolPolicyError(
                "Agent write actions are disabled",
                code="write_actions_disabled",
            )
        if (
            tool.capability is ToolCapability.PURCHASING
            and not self._settings.agent_purchasing_enabled
        ):
            raise AgentToolPolicyError(
                "Agent purchasing actions are disabled",
                code="purchasing_disabled",
            )
        preview = AgentActionPreview.model_validate(
            tool.preview_builder(context, validated_arguments)
        )
        return self._issue(
            AgentToolDispatchResult(
                tool_name=tool.name,
                tool_version=tool.version,
                effect=tool.effect,
                capability=tool.capability,
                disposition=ToolDisposition.PROPOSAL_REQUIRED,
                normalized_arguments=normalized_arguments,
                preview=preview,
            ),
            context=context,
        )

    def execute_read(
        self,
        prepared: AgentToolDispatchResult,
        *,
        context: AgentToolContext,
    ) -> AgentToolDispatchResult:
        self._validate_context(context)
        tool = self.validate_issued(prepared, context=context)
        if (
            prepared.disposition is not ToolDisposition.READY
            or prepared.effect is not ToolEffect.READ
            or tool.effect is not ToolEffect.READ
            or prepared.tool_version != tool.version
        ):
            raise AgentToolPolicyError(
                "Only a prepared READ tool can be executed",
                code="invalid_tool_dispatch",
            )
        _reject_forbidden_argument_keys(prepared.normalized_arguments)
        _require_finite_json(
            prepared.normalized_arguments,
            label="arguments",
            error_type=UnsafeToolArgumentsError,
        )
        validated_arguments = tool.input_model.model_validate(
            prepared.normalized_arguments,
            strict=True,
        )
        raw_output = tool.handler(context, validated_arguments)
        validated_output = tool.output_model.model_validate(raw_output, strict=True)
        normalized_output = validated_output.model_dump(mode="json")
        _reject_forbidden_output_keys(normalized_output)
        _require_finite_json(
            normalized_output,
            label="output",
            error_type=UnsafeToolOutputError,
        )
        return self._issue(
            AgentToolDispatchResult(
                tool_name=tool.name,
                tool_version=tool.version,
                effect=tool.effect,
                capability=tool.capability,
                disposition=ToolDisposition.EXECUTED,
                normalized_arguments=prepared.normalized_arguments,
                output=normalized_output,
            ),
            context=context,
        )

    def validate_issued(
        self,
        dispatch: AgentToolDispatchResult,
        *,
        context: AgentToolContext,
    ) -> AgentTool:
        """Verify that this exact dispatch was issued by this registry instance."""

        expected_proof = self._dispatch_proof(dispatch)
        if (
            dispatch._registry_nonce is not self._dispatch_nonce
            or dispatch._registry_proof is None
            or not hmac.compare_digest(dispatch._registry_proof, expected_proof)
            or dispatch._issued_workspace_id != context.workspace_id
            or dispatch._issued_user_id != context.user_id
            or dispatch._issued_at is None
            or time.monotonic() - dispatch._issued_at > 300
        ):
            raise AgentToolPolicyError(
                "Agent tool dispatch was not issued by the trusted registry",
                code="untrusted_tool_dispatch",
            )
        tool = self.get(dispatch.tool_name)
        if (
            dispatch.tool_version != tool.version
            or dispatch.effect is not tool.effect
            or dispatch.capability is not tool.capability
        ):
            raise AgentToolPolicyError(
                "Agent tool dispatch no longer matches the registered tool",
                code="invalid_tool_dispatch",
            )
        self._enforce_policy(tool)
        return tool

    def _issue(
        self,
        dispatch: AgentToolDispatchResult,
        *,
        context: AgentToolContext,
    ) -> AgentToolDispatchResult:
        dispatch._registry_nonce = self._dispatch_nonce
        dispatch._issued_workspace_id = context.workspace_id
        dispatch._issued_user_id = context.user_id
        dispatch._issued_at = time.monotonic()
        dispatch._registry_proof = self._dispatch_proof(dispatch)
        return dispatch

    def _dispatch_proof(self, dispatch: AgentToolDispatchResult) -> str:
        encoded = json.dumps(
            {
                "dispatch": dispatch.model_dump(mode="json"),
                "issued_at": dispatch._issued_at,
                "user_id": dispatch._issued_user_id,
                "workspace_id": dispatch._issued_workspace_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hmac.new(self._dispatch_secret, encoded, hashlib.sha256).hexdigest()

    def _enforce_policy(self, tool: AgentTool) -> None:
        if not self._settings.agent_enabled:
            raise AgentToolPolicyError("The ExpenseOps Agent is disabled", code="agent_disabled")
        if tool.effect is ToolEffect.READ:
            if not self._settings.agent_read_tools_enabled:
                raise AgentToolPolicyError(
                    "Agent read tools are disabled",
                    code="read_tools_disabled",
                )
            return
        if not self._settings.agent_write_actions_enabled:
            raise AgentToolPolicyError(
                "Agent write actions are disabled",
                code="write_actions_disabled",
            )
        if (
            tool.capability is ToolCapability.PURCHASING
            and not self._settings.agent_purchasing_enabled
        ):
            raise AgentToolPolicyError(
                "Agent purchasing actions are disabled",
                code="purchasing_disabled",
            )

    @staticmethod
    def _validate_context(context: AgentToolContext) -> None:
        if (
            context.db.info.get("workspace_id") != context.workspace_id
            or context.db.info.get("user_id") != context.user_id
        ):
            raise AgentToolPolicyError(
                "Agent tool context does not match the authenticated database session",
                code="invalid_tool_context",
            )


_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "private_key",
    "secret",
    "session_cookie",
    "token",
}
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)
_SERVER_CONTEXT_KEYS = {
    "actor_user_id",
    "db",
    "request_id",
    "session",
    "tenant_id",
    "user_id",
    "workspace_id",
}
_MAX_ARGUMENT_DEPTH = 20


def _require_strict_model(model: type[BaseModel], *, label: str) -> None:
    if model.model_config.get("extra") != "forbid":
        raise ValueError(f"Agent tool {label}_model must configure extra='forbid'")


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")


def _is_forbidden_key(value: Any) -> bool:
    key = _normalized_key(value)
    compact = key.replace("_", "")
    return (
        key in _SENSITIVE_EXACT_KEYS
        or key in _SERVER_CONTEXT_KEYS
        or any(key.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES)
        or any(
            compact.endswith(suffix)
            for suffix in (
                "apikey",
                "token",
                "secret",
                "password",
                "privatekey",
                "credential",
                "credentials",
                "cookie",
            )
        )
        or compact
        in {
            "actoruserid",
            "requestid",
            "tenantid",
            "userid",
            "workspaceid",
        }
    )


def _reject_forbidden_schema_keys(schema: Mapping[str, Any], *, label: str) -> None:
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key in properties:
            if _is_forbidden_key(key):
                raise ValueError(f"Agent tool {label} schema contains forbidden field '{key}'")
    for value in schema.values():
        if isinstance(value, Mapping):
            _reject_forbidden_schema_keys(value, label=label)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    _reject_forbidden_schema_keys(item, label=label)


def _reject_forbidden_argument_keys(
    value: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> None:
    _reject_forbidden_payload_keys(
        value,
        label="arguments",
        error_type=UnsafeToolArgumentsError,
        depth=depth,
        seen=seen,
    )


def _reject_forbidden_output_keys(value: Any) -> None:
    _reject_forbidden_payload_keys(
        value,
        label="output",
        error_type=UnsafeToolOutputError,
    )


def _reject_forbidden_payload_keys(
    value: Any,
    *,
    label: str,
    error_type: type[AgentToolError],
    depth: int = 0,
    seen: set[int] | None = None,
) -> None:
    if depth > _MAX_ARGUMENT_DEPTH:
        raise error_type(f"Agent tool {label} is nested too deeply")
    seen = seen if seen is not None else set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise error_type(f"Agent tool {label} contains a recursive structure")
        seen.add(identity)
        try:
            for key, item in value.items():
                if _is_forbidden_key(key):
                    raise error_type(f"Agent tool {label} contains forbidden field '{key}'")
                _reject_forbidden_payload_keys(
                    item,
                    label=label,
                    error_type=error_type,
                    depth=depth + 1,
                    seen=seen,
                )
        finally:
            seen.remove(identity)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in seen:
            raise error_type(f"Agent tool {label} contains a recursive structure")
        seen.add(identity)
        try:
            for item in value:
                _reject_forbidden_payload_keys(
                    item,
                    label=label,
                    error_type=error_type,
                    depth=depth + 1,
                    seen=seen,
                )
        finally:
            seen.remove(identity)


def _require_finite_json(
    value: Any,
    *,
    label: str,
    error_type: type[AgentToolError],
) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise error_type(f"Agent tool {label} must be strict JSON") from exc
