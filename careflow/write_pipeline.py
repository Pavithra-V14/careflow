"""The five checks before ANY write tool executes (BUILD-SPEC.md 8), in this order, each its own explicit refusal:

  1. SCHEMA    the arguments validate against the tool's pydantic model (types, ranges, enums; extra fields are refused).                -> status `invalid`
  2. AUTHORITY the actor has rights on THIS specific row, not just on the tool class (`authorize` looks at the validated arguments).     -> status `refused`
  3. POLICY    the clinic's autonomy mode allows this class of action to run without a human first (careflow/autonomy.py).               -> status `needs_human`
  4. BUDGET    the job still has tool-call allowance (careflow/budget.py); the safety class is only counted, never refused.               -> status `budget_exhausted`
  5. KEY       an idempotency key is present and well-formed; a replay is honoured by the executor (every write module already replays).  -> status `refused` (reason `missing_idempotency_key`)

Only when all five pass is `execute` called, with the VALIDATED arguments. The result is ALWAYS an explicit status (`ok | empty | degraded | unavailable` from the tool, or one of the refusals above),
never a bare empty value, and the reason is a closed word, never text from an exception or an argument. A refusal at any step means nothing was executed. `needs_human` is not a failure: it is the
answer that a person must decide (the validated proposal is returned so a person can); it is what `shadow` mode means for bookings.

The pipeline imports no database code and holds no state: it is exercised with fake executors.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from careflow.autonomy import Policy
from careflow.budget import BudgetExceeded, CallClass, RunBudget

Status = Literal["ok", "empty", "degraded", "unavailable", "invalid", "refused", "needs_human", "budget_exhausted"]
REASONS: Final = (
    "bad_arguments", "not_authorized", "human_decides", "tool_call_ceiling", "missing_idempotency_key", "executed", "nothing_to_do", "source_degraded", "source_unavailable", "none",
)
EXECUTOR_STATUSES: Final = frozenset({"ok", "empty", "degraded", "unavailable"})


class StrictArgs(BaseModel):
    """Base for a write tool's arguments: unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel):
    status: Status
    reason: str  # one of REASONS
    data: Any = None  # present for ok, empty and degraded, and the validated proposal for needs_human


def _key_ok(key: object) -> bool:
    return isinstance(key, str) and 0 < len(key.strip()) and len(key) <= 200


async def guarded_write(
    *, schema: type[StrictArgs], raw_args: dict[str, Any], authorize: Callable[[Any], Awaitable[bool]], action_class: str, policy: Policy, budget: RunBudget | None,
    idempotency_key: object, execute: Callable[[Any, str], Awaitable[ToolResult]], call_class: CallClass = CallClass.ORDINARY,
) -> ToolResult:
    """Run the five checks in order, then `execute(validated_args, idempotency_key)`. Never raises for a refusal; an exception from `execute` itself propagates (the caller's transaction rolls back)."""
    try:
        args = schema.model_validate(raw_args)
    except ValidationError:
        return ToolResult(status="invalid", reason="bad_arguments")
    if not await authorize(args):
        return ToolResult(status="refused", reason="not_authorized")
    if not policy.may_run_without_a_human_first(action_class):
        return ToolResult(status="needs_human", reason="human_decides", data=args.model_dump(mode="json"))
    if budget is not None:
        try:
            budget.tool_call(call_class)
        except BudgetExceeded:
            return ToolResult(status="budget_exhausted", reason="tool_call_ceiling")
    if not _key_ok(idempotency_key):
        return ToolResult(status="refused", reason="missing_idempotency_key")
    result = await execute(args, str(idempotency_key))
    if not isinstance(result, ToolResult) or result.status not in EXECUTOR_STATUSES:
        raise TypeError("a write executor must return ok, empty, degraded or unavailable")
    return result
