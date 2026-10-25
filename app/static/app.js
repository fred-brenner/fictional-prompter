/* Prompt Playbook front end — talks to the FastAPI eval backend. */

const state = {
  presets: [],
  cases: [],
  tools: [],
  defaults: {},
  activePreset: null,
  currentRunId: null,
  pollTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const editor = () => $("#prompt-editor");

async function api(path, opts) {
  const res = await fetch(path, opts && {
    method: opts.method || "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts.body || {}),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

/* ---------- bootstrap ---------- */

async function init() {
  const boot = await api("/api/bootstrap");
  state.presets = boot.presets;
  state.cases = boot.test_cases;
  state.tools = boot.tools;
  state.defaults = boot.defaults;

  $("#model").value = boot.defaults.model;
  $("#effort").value = boot.defaults.effort;
  const cs = $("#codex-status");
  if (boot.codex.available) {
    cs.textContent = `codex ✓ ${boot.codex.version}`;
    cs.className = "badge ok";
  } else {
    cs.textContent = "codex CLI not found";
    cs.className = "badge bad";
  }

  renderPresets();
  renderDiffOptions();
  renderCases();
  selectPreset("v0");
  refreshRuns();

  $("#run-all").addEventListener("click", () => startRun());
  $("#diff-preset").addEventListener("change", updateDiff);
  editor().addEventListener("input", () => {
    markCustom();
    updateDiff();
  });
}

/* ---------- presets ---------- */

function renderPresets() {
  const row = $("#presets");
  row.innerHTML = "";
  for (const p of state.presets) {
    const b = document.createElement("button");
    b.textContent = p.id;
    b.title = p.label + "\n" + p.description;
    b.dataset.preset = p.id;
    b.addEventListener("click", () => selectPreset(p.id));
    row.appendChild(b);
  }
}

function renderDiffOptions() {
  const sel = $("#diff-preset");
  for (const p of state.presets) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.id;
    sel.appendChild(o);
  }
}

function selectPreset(id) {
  const p = state.presets.find((x) => x.id === id);
  if (!p) return;
  state.activePreset = id;
  editor().value = p.prompt;
  $("#tools-enabled").checked = p.config.tools_enabled;
  $("#stop-seqs").value = (p.config.stop_sequences || []).join(", ");
  document.querySelectorAll("#presets button").forEach((b) =>
    b.classList.toggle("active", b.dataset.preset === id));
  updateDiff();
}

function markCustom() {
  state.activePreset = null;
  document.querySelectorAll("#presets button").forEach((b) =>
    b.classList.remove("active"));
}

function currentLabel() {
  return state.activePreset || "custom prompt";
}

/* ---------- diff ---------- */

function lcsLines(a, b) {
  // classic LCS on lines -> list of {type: same|add|del, text}
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1
                             : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) { out.push({ t: "same", s: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "del", s: a[i] }); i++; }
    else { out.push({ t: "add", s: b[j] }); j++; }
  }
  while (i < m) out.push({ t: "del", s: a[i++] });
  while (j < n) out.push({ t: "add", s: b[j++] });
  return out;
}

function updateDiff() {
  const target = $("#diff-preset").value;
  const view = $("#diff-view");
  if (!target) { view.classList.add("hidden"); editor().classList.remove("hidden"); return; }
  const p = state.presets.find((x) => x.id === target);
  if (!p) return;
  const diff = lcsLines(p.prompt.split("\n"), editor().value.split("\n"));
  view.innerHTML = diff.map(d =>
    `<div class="line ${d.t}">${d.t === "add" ? "+" : d.t === "del" ? "−" : " "} ${escapeHtml(d.s)}</div>`
  ).join("");
  view.classList.remove("hidden");
  editor().classList.add("hidden");
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ---------- cases ---------- */

function renderCases() {
  const wrap = $("#cases");
  wrap.innerHTML = "";
  for (const c of state.cases) {
    const node = $("#case-tpl").content.cloneNode(true);
    const det = node.querySelector("details");
    det.id = `case-${c.id}`;
    det.querySelector(".case-name").textContent = c.name;
    const chip = det.querySelector(".chip");
    chip.textContent = c.category;
    chip.classList.add(c.category);
    det.querySelector(".user-msg").textContent = c.user_message;
    det.querySelector(".ctx").textContent = c.customer_context;
    det.querySelector(".rationale").textContent = c.expected_behavior;
    det.querySelector(".rerun").addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      startRun([c.id]);
    });
    wrap.appendChild(node);
    setCaseStatus(c.id, { status: "idle" });
  }
}

function setCaseStatus(id, r) {
  const det = $(`#case-${id}`);
  if (!det) return;
  const dot = det.querySelector(".status-dot");
  const verdictEl = det.querySelector(".verdict");
  const meta = det.querySelector(".meta");
  det.classList.toggle("error", r.status === "error");

  dot.className = "status-dot " + (r.status === "done" ? (r.verdict || "error") : r.status === "queued" ? "" : r.status);
  verdictEl.textContent = r.status === "done" ? (r.verdict || "?").toUpperCase()
    : r.status === "running" ? "RUNNING" : r.status === "error" ? "ERROR" : "";
  verdictEl.className = "verdict " + (r.verdict || "");
  meta.textContent = r.status === "done"
    ? `${r.cost_usd != null ? "$" + r.cost_usd.toFixed(4) + " · " : ""}${r.tokens ? r.tokens + " tok · " : ""}${(r.latency_ms / 1000).toFixed(1)}s`
    : "";

  if (r.status === "done" || r.status === "error") {
    det.querySelector(".output").textContent = r.error ? `Error: ${r.error}` : (r.output || "(empty)");
    const tc = det.querySelector(".toolcalls");
    tc.innerHTML = (r.tool_calls || []).map(t =>
      `<div class="toolcall"><span class="tname">⚙ ${escapeHtml(t.name)}</span>\n` +
      `input:  ${escapeHtml(JSON.stringify(t.input))}\n` +
      `output: ${escapeHtml(JSON.stringify(t.output))}</div>`).join("");
    det.querySelector(".rationale").textContent =
      (r.judge_rationale ? `[judge] ${r.judge_rationale}` : "");
  }
}

function resetCases() {
  for (const c of state.cases) {
    const det = $(`#case-${c.id}`);
    det.querySelector(".output").textContent = "";
    det.querySelector(".toolcalls").innerHTML = "";
    setCaseStatus(c.id, { status: "idle" });
  }
  $("#eval-score").textContent = "";
}

/* ---------- runs ---------- */

function harnessConfig() {
  return {
    prompt: editor().value,
    label: currentLabel(),
    model: $("#model").value.trim() || state.defaults.model,
    effort: $("#effort").value,
    tools_enabled: $("#tools-enabled").checked,
    stop_sequences: $("#stop-seqs").value.split(",").map(s => s.trim()).filter(Boolean),
  };
}

async function startRun(caseIds) {
  $("#run-all").disabled = true;
  try {
    const body = harnessConfig();
    if (caseIds) body.case_ids = caseIds;
    else resetCases();
    const { run_id } = await api("/api/run", { body });
    state.currentRunId = run_id;
    for (const c of state.cases)
      if (!caseIds || caseIds.includes(c.id))
        setCaseStatus(c.id, { status: "queued" });
    pollRun();
  } catch (e) {
    console.error(e);
    $("#run-all").disabled = false;
  }
}

async function pollRun() {
  clearTimeout(state.pollTimer);
  try {
    const run = await api(`/api/run/${state.currentRunId}`);
    for (const [cid, r] of Object.entries(run.results)) setCaseStatus(cid, r);
    updateScore(run);
    if (run.status === "running") {
      state.pollTimer = setTimeout(pollRun, 1500);
    } else {
      $("#run-all").disabled = false;
      refreshRuns();
    }
  } catch (e) {
    console.error(e);
    $("#run-all").disabled = false;
  }
}

function updateScore(run) {
  const el = $("#eval-score");
  const done = run.passed + run.failed;
  el.textContent = `${run.passed}/${run.total} passing` + (done < run.total ? ` (${done} graded)` : "");
  el.className = "badge " + (run.status === "done" ? (run.failed === 0 ? "ok" : "bad") : "");
}

async function refreshRuns() {
  const runs = await api("/api/runs");
  const tb = $("#run-rows");
  tb.innerHTML = runs.slice().reverse().map(r => `
    <tr data-run="${r.id}">
      <td>${r.id}</td><td>${escapeHtml(r.label)}</td>
      <td>${escapeHtml(r.model)} · ${escapeHtml(r.effort)}</td>
      <td class="${r.failed === 0 && r.status === "done" ? "result-pass" : "result-fail"}">
        ${r.status === "done" ? `${r.passed}/${r.total}` : `${r.passed + r.failed}/${r.total}…`}
      </td>
      <td>${r.tokens ? r.tokens.toLocaleString() : "—"}</td>
      <td title="estimate at OpenAI API list prices">${r.cost_usd != null ? "$" + r.cost_usd.toFixed(4) : "—"}</td>
      <td>${r.elapsed_s != null ? r.elapsed_s.toFixed(1) + "s" : "—"}</td>
      <td>${r.status}</td>
      <td>${new Date(r.created_at * 1000).toLocaleTimeString()}</td>
    </tr>`).join("");
  tb.querySelectorAll("tr").forEach(tr =>
    tr.addEventListener("click", async () => {
      const run = await api(`/api/run/${tr.dataset.run}`);
      for (const [cid, r] of Object.entries(run.results)) setCaseStatus(cid, r);
      updateScore(run);
    }));
}

init().catch(e => {
  document.body.insertAdjacentHTML("afterbegin",
    `<div style="padding:12px;background:#5b2020;color:#fff">Failed to load: ${e.message}</div>`);
});
