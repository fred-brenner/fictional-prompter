/* Scheduler lab — generate→evaluate→repair rota trials (video part 2). */

const sched = {
  boot: null,
  approach: "small-baseline",
  runId: null,
  pollTimer: null,
};

const $$ = (s) => document.querySelector(s);

async function schedApi(path, body) {
  const res = await fetch(path, body && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

/* ---------- tabs ---------- */

function bindTabs() {
  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => {
      const t = b.dataset.tab;
      document.querySelectorAll(".tab").forEach((x) =>
        x.classList.toggle("active", x === b));
      $$("#tab-prompt").classList.toggle("hidden", t !== "prompt");
      $$("#tab-sched").classList.toggle("hidden", t !== "sched");
      // header model/effort/run controls belong to the workbench tab
      $$(".header-controls").style.display = t === "prompt" ? "" : "none";
      if (t === "sched" && !sched.boot) schedInit();
    }));
}

/* ---------- bootstrap ---------- */

async function schedInit() {
  const b = await schedApi("/api/sched/bootstrap");
  sched.boot = b;

  // employees table
  const short = { Monday: "Mo", Tuesday: "Tu", Wednesday: "We", Thursday: "Th", Friday: "Fr", Saturday: "Sa", Sunday: "Su" };
  $$("#emp-table").innerHTML =
    "<tr><th>employee</th>" + b.days.map((d) => `<th>${short[d]}</th>`).join("") + "</tr>" +
    b.employees.map((e) =>
      `<tr><td>${e.name}${e.senior ? " <span class='senior' title='senior'>★</span>" : ""}</td>` +
      b.days.map((d) => `<td class="${e.days.includes(d) ? "avail" : ""}">${e.days.includes(d) ? "●" : ""}</td>`).join("") +
      "</tr>").join("");

  // shift grid
  $$("#shift-table").innerHTML =
    "<tr><th>shift</th>" + b.days.map((d) => `<th>${short[d]}</th>`).join("") + "</tr>" +
    Object.entries(b.shifts).map(([s, n]) =>
      `<tr><td>${s}</td>` + b.days.map(() => `<td class="need">${n}</td>`).join("") + "</tr>").join("");

  $$("#rules-list").innerHTML = b.rules.map((r) => `<li>${r}</li>`).join("");

  // model select mirrors the workbench's known-good list
  const msel = $$("#sched-model");
  const wb = await fetch("/api/bootstrap").then((r) => r.json());
  msel.innerHTML = (wb.models || []).map((m) =>
    `<option value="${m.slug}">${m.slug}</option>`).join("");

  renderApproaches();
  $$("#sched-run").addEventListener("click", startSchedRun);
  refreshSchedRuns();
}

function renderApproaches() {
  const wrap = $$("#approaches");
  wrap.innerHTML = "";
  for (const a of sched.boot.approaches) {
    const card = document.createElement("label");
    card.className = "approach" + (a.id === sched.approach ? " active" : "");
    card.innerHTML =
      `<input type="radio" name="approach" ${a.id === sched.approach ? "checked" : ""}>
       <div><b>${a.label}</b><div class="hint">${a.description}</div>
       <div class="hint mono">${a.model} · ${a.effort} · ${a.mode}</div></div>`;
    card.addEventListener("click", () => {
      sched.approach = a.id;
      renderApproaches();
      updatePromptPreview();
    });
    wrap.appendChild(card);
  }
  updatePromptPreview();
  syncOverrides();
}

function currentApproach() {
  return sched.boot.approaches.find((a) => a.id === sched.approach);
}

function syncOverrides() {
  const a = currentApproach();
  $$("#sched-model").value = a.model;
  $$("#sched-effort").value = a.effort;
}

function updatePromptPreview() {
  const a = currentApproach();
  const texts = sched.boot.prompt_texts;
  $$("#sched-prompt-preview").value = texts[a.prompt_id] || "";
}

/* ---------- runs ---------- */

async function startSchedRun() {
  const a = currentApproach();
  const preview = $$("#sched-prompt-preview").value;
  const body = {
    approach_id: a.id,
    trials: parseInt($$("#trials").value || "3", 10),
    soft_constraints: $$("#soft-constraints").value.trim(),
    model: $$("#sched-model").value || a.model,
    effort: $$("#sched-effort").value || a.effort,
  };
  const defaultText = sched.boot.prompt_texts[a.prompt_id] || "";
  if (preview !== defaultText) body.prompt_override = preview;

  $$("#sched-run").disabled = true;
  try {
    const { run_id } = await schedApi("/api/sched/run", body);
    sched.runId = run_id;
    renderTrialSkeleton(body.trials);
    pollSchedRun();
  } catch (e) {
    console.error(e);
    $$("#sched-run").disabled = false;
  }
}

function renderTrialSkeleton(n) {
  $$("#trial-cards").innerHTML = "";
  for (let i = 0; i < n; i++) {
    const det = document.createElement("details");
    det.className = "case";
    det.id = `trial-${i}`;
    det.innerHTML =
      `<summary><span class="status-dot running"></span>
       <span class="case-name">Trial ${i + 1}</span>
       <span class="verdict"></span><span class="meta"></span></summary>
       <div class="case-body"><div class="col sched-out"></div><div class="col viol-list"></div></div>`;
    $$("#trial-cards").appendChild(det);
  }
}

async function pollSchedRun() {
  clearTimeout(sched.pollTimer);
  try {
    const run = await schedApi(`/api/sched/run/${sched.runId}`);
    run.results.forEach((r, i) => renderTrial(i, r));
    const el = $$("#sched-score");
    el.textContent = `${run.passed}/${run.total} trials clean · ${run.violations} violations`;
    el.className = "badge " + (run.status === "done" ? (run.passed === run.total ? "ok" : "bad") : "");
    if (run.status === "running") {
      sched.pollTimer = setTimeout(pollSchedRun, 2000);
    } else {
      $$("#sched-run").disabled = false;
      refreshSchedRuns();
    }
  } catch (e) {
    console.error(e);
    $$("#sched-run").disabled = false;
  }
}

function scheduleTable(schedule) {
  if (!schedule) return "<pre class='output'>no parseable schedule</pre>";
  const days = sched.boot.days;
  const shifts = Object.keys(sched.boot.shifts);
  let html = "<table class='mini result-grid'><tr><th>shift</th>" +
    days.map((d) => `<th>${d.slice(0, 2)}</th>`).join("") + "</tr>";
  for (const s of shifts) {
    html += `<tr><td>${s}</td>` + days.map((d) => {
      const names = ((schedule[d] || {})[s] || []).join(", ");
      return `<td>${names || "—"}</td>`;
    }).join("") + "</tr>";
  }
  return html + "</table>";
}

function renderTrial(i, r) {
  const det = $$(`#trial-${i}`);
  if (!det) return;
  const dot = det.querySelector(".status-dot");
  const verdict = det.querySelector(".verdict");
  const meta = det.querySelector(".meta");
  const state = r.status === "done" ? (r.passed ? "pass" : "fail") : r.status;
  dot.className = "status-dot " + state;
  verdict.textContent = r.status === "done"
    ? (r.passed ? "CLEAN" : `${r.violation_count} VIOLATIONS`) : r.status.toUpperCase();
  verdict.className = "verdict " + (r.status === "done" ? (r.passed ? "pass" : "fail") : "");
  meta.textContent = r.status === "done"
    ? `${r.cost_usd != null ? "$" + r.cost_usd.toFixed(4) + " · " : ""}${r.tokens ? r.tokens + " tok · " : ""}${(r.latency_ms / 1000).toFixed(1)}s`
    : "";
  if (r.status !== "done" && r.status !== "error") return;

  det.querySelector(".sched-out").innerHTML =
    `<h4>Schedule</h4>` + (r.error ? `<pre class="output">Error: ${r.error}</pre>` : scheduleTable(r.schedule));
  const vl = det.querySelector(".viol-list");
  let violHtml = `<h4>Violations (${r.violations.length})</h4>`;
  violHtml += r.violations.length
    ? r.violations.map((v) => `<div class="toolcall"><span class="tname">${v.rule}</span> ${v.detail}</div>`).join("")
    : "<div class='rationale'>none — schedule is fully compliant</div>";
  if (r.rounds && r.rounds.length) {
    violHtml += `<h4>Loop trace</h4>` + r.rounds.map((rd) =>
      `<details class="round"><summary>${rd.stage}</summary><pre class="output">${rd.output.slice(0, 3000)}</pre></details>`).join("");
  }
  vl.innerHTML = violHtml;
}

/* ---------- history ---------- */

async function refreshSchedRuns() {
  const runs = await schedApi("/api/sched/runs");
  $$("#sched-run-rows").innerHTML = runs.slice().reverse().map((r) => `
    <tr data-run="${r.id}">
      <td>${r.id}</td><td>${r.approach}</td>
      <td>${r.model} · ${r.effort}</td>
      <td class="${r.status === "done" && r.passed === r.total ? "result-pass" : "result-fail"}">
        ${r.status === "done" ? `${r.passed}/${r.total}` : `${r.passed}/${r.total}…`}
      </td>
      <td>${r.violations}</td>
      <td>${r.tokens ? r.tokens.toLocaleString() : "—"}</td>
      <td title="estimate at OpenAI API list prices">${r.cost_usd != null ? "$" + r.cost_usd.toFixed(4) : "—"}</td>
      <td>${r.elapsed_s != null ? r.elapsed_s.toFixed(1) + "s" : "—"}</td>
      <td>${r.status}</td>
      <td>${new Date(r.created_at * 1000).toLocaleTimeString()}</td>
    </tr>`).join("");
}

bindTabs();
