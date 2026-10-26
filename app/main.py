"""FastAPI app serving the prompt workbench UI and eval API."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .codex_backend import DEFAULT_EFFORT, DEFAULT_MODEL, MODELS
from .evals import RUNS, RunConfig, start_run
from .local_backend import LOCAL_MODELS, VENV_PYTHON, local_model_path, shutdown_worker
from .prompts import PRESETS, PRESETS_BY_ID
from .scheduling import (
    APPROACHES,
    DAYS,
    EMPLOYEES,
    PROMPT_TEXTS,
    RULES,
    SCHED_RUNS,
    SENIORS,
    SHIFTS,
    start_sched_run,
)
from .testcases import TEST_CASES, TEST_CASES_BY_ID
from .tools import TOOL_SCHEMAS

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Prompt Playbook")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def _codex_status() -> dict:
    exe = shutil.which("codex")
    if not exe:
        return {"available": False}
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        )
        return {"available": True, "version": out.stdout.strip(), "path": exe}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)}


@app.get("/api/bootstrap")
def bootstrap():
    return {
        "presets": PRESETS,
        "test_cases": TEST_CASES,
        "tools": TOOL_SCHEMAS,
        "defaults": {"model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT},
        "models": MODELS,
        "codex": _codex_status(),
        "local": {
            "available": VENV_PYTHON.exists()
            and any(
                (p := local_model_path(s)) is not None and p.exists()
                for s in LOCAL_MODELS
            ),
            "models": {
                s: str(local_model_path(s)) for s in LOCAL_MODELS
            },
        },
    }


@app.on_event("shutdown")
def _shutdown():
    shutdown_worker()


class RunRequest(BaseModel):
    prompt: str
    label: str = "custom prompt"
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    judge_model: str | None = "gpt-6-luna"
    judge_effort: str = "medium"
    tools_enabled: bool = True
    stop_sequences: list[str] = []
    case_ids: list[str] | None = None


@app.post("/api/run")
async def create_run(req: RunRequest):
    unknown = [c for c in (req.case_ids or []) if c not in TEST_CASES_BY_ID]
    if unknown:
        raise HTTPException(400, f"unknown case ids: {unknown}")
    config = RunConfig(
        model=req.model,
        effort=req.effort,
        tools_enabled=req.tools_enabled,
        stop_sequences=req.stop_sequences,
        judge_model=req.judge_model,
        judge_effort=req.judge_effort,
    )
    run = start_run(req.prompt, config, TEST_CASES_BY_ID, req.label, req.case_ids)
    return {"run_id": run.id}


@app.get("/api/runs")
def list_runs():
    return [r.summary() for r in sorted(RUNS.values(), key=lambda r: r.created_at)]


@app.get("/api/run/{run_id}")
def get_run(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run.detail()


@app.get("/api/preset/{preset_id}")
def get_preset(preset_id: str):
    preset = PRESETS_BY_ID.get(preset_id)
    if not preset:
        raise HTTPException(404, "preset not found")
    return preset


# ----------------------------------------------------------- scheduler lab


@app.get("/api/sched/bootstrap")
def sched_bootstrap():
    return {
        "employees": [
            {"name": n, "days": d, "senior": n in SENIORS}
            for n, d in EMPLOYEES.items()
        ],
        "days": DAYS,
        "shifts": SHIFTS,
        "rules": RULES,
        "approaches": APPROACHES,
        "prompt_texts": PROMPT_TEXTS,
        "codex": _codex_status(),
    }


class SchedRunRequest(BaseModel):
    approach_id: str
    trials: int = 3
    soft_constraints: str = ""
    model: str | None = None
    effort: str | None = None
    prompt_override: str | None = None


@app.post("/api/sched/run")
async def sched_create_run(req: SchedRunRequest):
    if req.approach_id not in {a["id"] for a in APPROACHES}:
        raise HTTPException(400, f"unknown approach: {req.approach_id}")
    run = start_sched_run(
        req.approach_id,
        req.trials,
        req.soft_constraints,
        model=req.model,
        effort=req.effort,
        prompt_override=req.prompt_override,
    )
    return {"run_id": run.id}


@app.get("/api/sched/runs")
def sched_list_runs():
    return [r.summary() for r in sorted(SCHED_RUNS.values(), key=lambda r: r.created_at)]


@app.get("/api/sched/run/{run_id}")
def sched_get_run(run_id: str):
    run = SCHED_RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run.detail()
