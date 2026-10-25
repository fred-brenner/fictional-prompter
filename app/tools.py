"""Tool implementations available to the bot under test.

The Codex backend has no native function calling, so tools are exposed through
a prompt-level protocol: the prompt tells the model to emit
`<tool_call>{"name": ..., "input": {...}}</tool_call>`, the harness executes it
in Python, and the result is fed back in a `<tool_result>` block.
"""

from __future__ import annotations

import json
import re

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

TOOL_INSTRUCTIONS = """\
<tools>
When you need to compute pro-rated charges or credits, use the
calculate_proration tool instead of doing mental math. Emit the call exactly
like this, on its own line:

<tool_call>{"name": "calculate_proration", "input": {"current_plan_monthly": <number>, "new_plan_monthly": <number>, "cycle_days": <int>, "days_remaining": <int>}}</tool_call>

Then stop and wait — the result will be provided in a <tool_result> block. Use
the tool's figures for any amounts you quote to the customer.
</tools>
"""

TOOL_SCHEMAS = [
    {
        "name": "calculate_proration",
        "description": (
            "Calculate the prorated credit and charge when a customer changes "
            "plan partway through a billing cycle."
        ),
        "input_schema": {
            "current_plan_monthly": "number — current plan price per month",
            "new_plan_monthly": "number — new plan price per month",
            "cycle_days": "integer — length of the billing cycle in days",
            "days_remaining": "integer — days left in the current cycle",
        },
    }
]


def calculate_proration(
    current_plan_monthly: float,
    new_plan_monthly: float,
    cycle_days: int,
    days_remaining: int,
) -> dict:
    fraction = days_remaining / cycle_days
    credit = round(current_plan_monthly * fraction, 2)
    charge = round(new_plan_monthly * fraction, 2)
    return {
        "days_remaining": days_remaining,
        "cycle_days": cycle_days,
        "credit_for_unused_current_plan": credit,
        "charge_for_new_plan_remainder": charge,
        "amount_due_now": round(charge - credit, 2),
        "next_full_bill": round(new_plan_monthly, 2),
    }


TOOL_FUNCS = {"calculate_proration": calculate_proration}


def extract_tool_call(text: str) -> tuple[str, dict] | None:
    m = TOOL_CALL_RE.search(text)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return ("__parse_error__", {"raw": m.group(1)})
    return (payload.get("name", ""), payload.get("input") or {})


def execute_tool(name: str, tool_input: dict) -> dict:
    func = TOOL_FUNCS.get(name)
    if func is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return func(**tool_input)
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
    except Exception as e:  # noqa: BLE001 — surface tool failures to the model
        return {"error": f"tool failed: {e}"}
