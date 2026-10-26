"""Scheduler lab — the video's second scenario.

An agent generates a week-long retail rota. Unlike the support-bot suite, the
ground truth is a deterministic Python grader (hard rules, no LLM judge), and
the interesting comparison is model size × prompt quality × harness shape
(single prompt vs. a generate→evaluate→repair loop).

Instance is deliberately tight: Sunday has exactly 4 available employees for
4 slots, and Emma is weekend-only — the constraint that breaks naive attempts.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field

from .codex_backend import run_model
from .pricing import estimate_cost
from .evals import _SEMAPHORE

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SHIFTS = {"morning": 2, "afternoon": 1, "evening": 2}

EMPLOYEES = {
    "Alice": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "Bob": ["Monday", "Wednesday", "Friday", "Saturday", "Sunday"],
    "Carol": ["Tuesday", "Thursday", "Saturday", "Sunday"],
    "Dave": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    "Emma": ["Saturday", "Sunday"],
    "Frank": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "Grace": ["Monday", "Wednesday", "Thursday", "Saturday", "Sunday"],
    "Harry": ["Tuesday", "Thursday", "Friday", "Saturday"],
    "Irene": ["Tuesday", "Wednesday", "Friday", "Sunday"],
    "Sally": ["Monday", "Wednesday", "Friday", "Sunday"],
}

SENIORS = {"Alice", "Dave", "Harry", "Sally"}

RULES = [
    "Each shift has exactly the required headcount (2 morning, 1 afternoon, "
    "2 evening per day).",
    "An employee can only be scheduled on days they are available.",
    "No employee works more than one shift per day.",
    "No employee works more than 5 shifts in the week.",
    "No employee works more than 2 consecutive days.",
    "Every employee works at least 1 shift in the week.",
    "Every day must have at least one senior employee on shift "
    "(seniors: Alice, Dave, Harry, Sally).",
]

# --------------------------------------------------------------------- grader


def grade_schedule(schedule: dict) -> list[dict]:
    """Deterministic violation check — the official score for every trial."""
    violations = []
    if not isinstance(schedule, dict):
        return [{"rule": "schema", "detail": "output is not a JSON object"}]

    for day in schedule:
        if day not in DAYS:
            violations.append({"rule": "schema", "detail": f"unknown day: {day}"})
    for day in DAYS:
        if day not in schedule:
            violations.append({"rule": "coverage", "detail": f"{day} missing entirely"})
            continue
        day_sched = schedule[day] or {}
        seen_today = []
        for shift, required in SHIFTS.items():
            assigned = day_sched.get(shift) or []
            if len(assigned) != required:
                violations.append({
                    "rule": "coverage",
                    "detail": f"{day} {shift}: {len(assigned)} assigned, needs {required}",
                    "evidence": assigned,
                })
            for name in assigned:
                if name not in EMPLOYEES:
                    violations.append({
                        "rule": "schema", "detail": f"{day} {shift}: unknown employee {name!r}"})
                    continue
                if day not in EMPLOYEES[name]:
                    violations.append({
                        "rule": "availability",
                        "detail": f"{name} scheduled {day} but is not available that day",
                    })
                if name in seen_today:
                    violations.append({
                        "rule": "one_shift_per_day",
                        "detail": f"{name} works two shifts on {day}",
                    })
                seen_today.append(name)
        if not any(name in SENIORS for name in seen_today):
            violations.append({
                "rule": "senior_coverage",
                "detail": f"{day} has no senior on shift",
            })

    totals = {name: 0 for name in EMPLOYEES}
    worked = {name: set() for name in EMPLOYEES}
    for di, day in enumerate(DAYS):
        for shift in SHIFTS:
            for name in (schedule.get(day) or {}).get(shift) or []:
                if name in totals:
                    totals[name] += 1
                    worked[name].add(di)
    for name, days_worked in worked.items():
        wd = sorted(days_worked)
        for k in range(len(wd) - 2):
            if wd[k + 1] - wd[k] == 1 and wd[k + 2] - wd[k + 1] == 1:
                violations.append({
                    "rule": "consecutive_days",
                    "detail": (
                        f"{name} works 3+ consecutive days "
                        f"({DAYS[wd[k]]}–{DAYS[wd[k + 2]]})"
                    ),
                })
                break
    for name, n in totals.items():
        if n > 5:
            violations.append({
                "rule": "max_shifts", "detail": f"{name} works {n} shifts (max 5)"})
        elif n == 0:
            violations.append({
                "rule": "min_shifts", "detail": f"{name} has no shifts this week"})
    return violations


# ------------------------------------------------------------------- prompts


def _instance_block() -> str:
    lines = [
        f"- {name}{' (senior)' if name in SENIORS else ''}: "
        f"available {', '.join(days)}"
        for name, days in EMPLOYEES.items()
    ]
    return "\n".join(lines)


BASELINE_PROMPT = f"""\
<role>
You are a scheduling assistant for a retail store. Build the staff rota for
next week.
</role>

<employees>
{_instance_block()}
</employees>

<shifts>
Every day (Monday–Sunday) needs a morning shift of 2 people, an
afternoon shift of 1 person, and an evening shift of 2 people.
</shifts>

<rules>
{chr(10).join("- " + r for r in RULES)}
</rules>

<output_format>
Output only a JSON object, no commentary:
{{"Monday": {{"morning": ["name", "name"], "afternoon": ["name"], "evening": ["name", "name"]}}, ...}}
</output_format>
"""

BETTER_PROMPT = BASELINE_PROMPT.replace(
    "</rules>",
    """\
</rules>

<approach>
- Assign the most constrained employees first (e.g. staff who can only work
  weekends), then fill the remaining slots.
- Balance the load across the week so nobody idles or exceeds 5 shifts.
- Before outputting, re-check EVERY rule for EVERY day and fix any violation
  you find. Do not output until the schedule is fully compliant.
</approach>""",
)

GENERATE_PROMPT = BASELINE_PROMPT  # loop generator = same simple brief

EVALUATE_PROMPT = """\
<role>
You are a strict schedule auditor for a retail store. Check the draft rota
against every rule and report every violation you find.
</role>

<employees>
{employees}
</employees>

<shifts>
Every day (Monday–Sunday) needs a morning shift of 2 people, an
afternoon shift of 1 person, and an evening shift of 2 people.
</shifts>

<rules>
{rules}
{soft_constraints}
</rules>

<draft_schedule>
{draft}
</draft_schedule>

Check each rule for each day. For every violation report the rule, the
day/shift, and the employees involved with evidence. Output only JSON:
{{"violations": [{{"rule": "...", "detail": "..."}}]}} or {{"violations": []}}
"""

REPAIR_PROMPT = """\
<role>
You are a scheduling assistant. Fix the staff rota so it violates no rules.
</role>

<employees>
{employees}
</employees>

<shifts>
Every day needs a morning shift of 2 people, an afternoon shift
of 1 person, and an evening shift of 2 people.
</shifts>

<rules>
{rules}
{soft_constraints}
</rules>

<draft_schedule>
{draft}
</draft_schedule>

<violations_found>
{violations}
</violations_found>

Make targeted repairs that fix exactly these violations while keeping the
rest of the schedule intact. Output only the corrected JSON object, no
commentary.
"""

# ------------------------------------------------------------------ approach


APPROACHES = [
    {
        "id": "small-baseline",
        "label": "Small model · baseline prompt",
        "model": "gpt-5.6-luna", "effort": "low",
        "mode": "single", "prompt_id": "baseline",
        "description": "The naive starting point — like the video's first pass.",
    },
    {
        "id": "large-baseline",
        "label": "Large model · baseline prompt",
        "model": "gpt-6.1-sol", "effort": "low",
        "mode": "single", "prompt_id": "baseline",
        "description": "Same prompt, more capable model — fewer violations expected.",
    },
    {
        "id": "large-thinking",
        "label": "Large model · high effort",
        "model": "gpt-6.1-sol", "effort": "high",
        "mode": "single", "prompt_id": "baseline",
        "description": "The 'adaptive thinking' analog — reliable but slow and pricey.",
    },
    {
        "id": "small-better",
        "label": "Small model · better prompt",
        "model": "gpt-5.6-luna", "effort": "low",
        "mode": "single", "prompt_id": "better",
        "description": "Prompt tells it to plan order and verify its work first.",
    },
    {
        "id": "agentic-loop",
        "label": "Agentic loop · generate→evaluate→repair",
        "model": "gpt-5.6-luna", "effort": "low",
        "mode": "loop", "prompt_id": "loop",
        "description": "Three small prompts: draft, audit, repair. Soft constraints "
                       "can be injected into the evaluator at runtime.",
    },
]

APPROACHES_BY_ID = {a["id"]: a for a in APPROACHES}
PROMPT_TEXTS = {
    "baseline": BASELINE_PROMPT,
    "better": BETTER_PROMPT,
    "loop": GENERATE_PROMPT,
    "evaluate": EVALUATE_PROMPT,
    "repair": REPAIR_PROMPT,
}

FRAMING = """\
You will now act as the assistant defined by the prompt below. Follow it \
exactly and produce only the output it asks for — no commentary, no shell \
commands, no file writes.

===== PROMPT =====
"""

MAX_REPAIRS = 2
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_schedule(text: str) -> dict | None:
    """Extract the first JSON object from the model output."""
    candidate = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for attempt in (candidate,):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


# -------------------------------------------------------------------- trials


@dataclass
class TrialResult:
    status: str = "queued"
    passed: bool | None = None
    violation_count: int | None = None
    violations: list[dict] = field(default_factory=list)
    schedule: dict | None = None
    rounds: list[dict] = field(default_factory=list)  # loop-mode detail
    tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0
    error: str | None = None


@dataclass
class SchedRun:
    id: str
    created_at: float
    approach: dict
    trials: int
    soft_constraints: str
    results: list[TrialResult] = field(default_factory=list)
    status: str = "running"
    finished_at: float | None = None

    def summary(self) -> dict:
        done = [r for r in self.results if r.status == "done"]
        passed = sum(1 for r in done if r.passed)
        viols = sum(r.violation_count or 0 for r in done)
        tokens = sum(r.tokens or 0 for r in self.results)
        cost = sum(r.cost_usd or 0 for r in self.results)
        return {
            "id": self.id,
            "created_at": self.created_at,
            "approach": self.approach["label"],
            "approach_id": self.approach["id"],
            "status": self.status,
            "passed": passed,
            "total": self.trials,
            "violations": viols,
            "tokens": tokens or None,
            "cost_usd": cost or None,
            "elapsed_s": round((self.finished_at or time.time()) - self.created_at, 1),
            "model": self.approach["model"],
            "effort": self.approach["effort"],
        }

    def detail(self) -> dict:
        d = self.summary()
        d["results"] = [
            {
                "status": r.status,
                "passed": r.passed,
                "violation_count": r.violation_count,
                "violations": r.violations,
                "schedule": r.schedule,
                "rounds": r.rounds,
                "tokens": r.tokens,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in self.results
        ]
        return d


SCHED_RUNS: dict[str, SchedRun] = {}


class _Meter:
    """Accumulates tokens and estimated cost across codex calls."""

    def __init__(self, model: str):
        self.model = model
        self.tokens = 0
        self.cost = 0.0
        self.saw_cost = False

    def add(self, call) -> None:
        self.tokens += call.tokens or 0
        c = estimate_cost(self.model, call.usage)
        if c is not None:
            self.cost += c
            self.saw_cost = True


async def _single_trial(prompt: str, model: str, effort: str) -> TrialResult:
    res = TrialResult(status="running")
    started = time.monotonic()
    meter = _Meter(model)
    call = await run_model(FRAMING + prompt, model=model, effort=effort)
    meter.add(call)
    if call.error:
        res.status = "error"
        res.error = call.error
        res.latency_ms = int((time.monotonic() - started) * 1000)
        return res
    schedule = _parse_schedule(call.text)
    res.schedule = schedule
    if schedule is None:
        res.violations = [{"rule": "schema", "detail": "unparseable output — no JSON object"}]
        res.violation_count = 1
        res.passed = False
    else:
        res.violations = grade_schedule(schedule)
        res.violation_count = len(res.violations)
        res.passed = res.violation_count == 0
    res.tokens = meter.tokens or None
    res.cost_usd = meter.cost if meter.saw_cost else None
    res.latency_ms = int((time.monotonic() - started) * 1000)
    res.status = "done"
    return res


async def _loop_trial(
    model: str, effort: str, soft_constraints: str
) -> TrialResult:
    res = TrialResult(status="running")
    started = time.monotonic()
    meter = _Meter(model)
    soft = f"- SOFT (best effort): {soft_constraints}" if soft_constraints else ""

    call = await run_model(FRAMING + GENERATE_PROMPT, model=model, effort=effort)
    meter.add(call)
    if call.error:
        res.status = "error"
        res.error = call.error
        res.latency_ms = int((time.monotonic() - started) * 1000)
        return res
    res.rounds.append({"stage": "generate", "output": call.text})
    draft = _parse_schedule(call.text)

    for attempt in range(MAX_REPAIRS + 1):
        if draft is None:
            res.violations = [{"rule": "schema", "detail": "unparseable output"}]
            res.violation_count = 1
            res.passed = False
            break
        res.violations = grade_schedule(draft)
        res.violation_count = len(res.violations)
        res.passed = res.violation_count == 0
        res.schedule = draft
        if res.passed or attempt == MAX_REPAIRS:
            break

        # LLM auditor turns the draft into an explicit violation list —
        # including soft constraints, which the Python grader can't see.
        eval_prompt = EVALUATE_PROMPT.format(
            employees=_instance_block(),
            rules="\n".join("- " + r for r in RULES),
            soft_constraints=soft,
            draft=json.dumps(draft, indent=1),
        )
        call = await run_model(FRAMING + eval_prompt, model=model, effort=effort)
        meter.add(call)
        if call.error:
            res.error = f"evaluator: {call.error}"
            break
        res.rounds.append({"stage": f"evaluate_{attempt + 1}", "output": call.text})
        found = _parse_schedule(call.text) or {}
        violations = found.get("violations") or []

        repair_prompt = REPAIR_PROMPT.format(
            employees=_instance_block(),
            rules="\n".join("- " + r for r in RULES),
            soft_constraints=soft,
            draft=json.dumps(draft, indent=1),
            violations=json.dumps(violations, indent=1),
        )
        call = await run_model(FRAMING + repair_prompt, model=model, effort=effort)
        meter.add(call)
        if call.error:
            res.error = f"repair: {call.error}"
            break
        res.rounds.append({"stage": f"repair_{attempt + 1}", "output": call.text})
        repaired = _parse_schedule(call.text)
        if repaired is not None:
            draft = repaired

    if res.schedule is None and draft is not None:
        res.schedule = draft
    res.tokens = meter.tokens or None
    res.cost_usd = meter.cost if meter.saw_cost else None
    res.latency_ms = int((time.monotonic() - started) * 1000)
    res.status = "done"
    return res


async def _run_trials(run: SchedRun, prompt: str):
    a = run.approach

    async def one(i: int):
        async with _SEMAPHORE:
            if a["mode"] == "loop":
                run.results[i] = await _loop_trial(
                    a["model"], a["effort"], run.soft_constraints
                )
            else:
                run.results[i] = await _single_trial(prompt, a["model"], a["effort"])

    await asyncio.gather(*(one(i) for i in range(run.trials)))
    run.status = "done"
    run.finished_at = time.time()


def start_sched_run(
    approach_id: str,
    trials: int,
    soft_constraints: str = "",
    model: str | None = None,
    effort: str | None = None,
    prompt_override: str | None = None,
) -> SchedRun:
    approach = dict(APPROACHES_BY_ID[approach_id])
    if model:
        approach["model"] = model
    if effort:
        approach["effort"] = effort
    prompt = prompt_override or PROMPT_TEXTS[approach["prompt_id"]]
    run = SchedRun(
        id=uuid.uuid4().hex[:8],
        created_at=time.time(),
        approach=approach,
        trials=max(1, min(trials, 8)),
        soft_constraints=soft_constraints,
        results=[TrialResult() for _ in range(max(1, min(trials, 8)))],
    )
    SCHED_RUNS[run.id] = run
    asyncio.create_task(_run_trials(run, prompt))
    return run
