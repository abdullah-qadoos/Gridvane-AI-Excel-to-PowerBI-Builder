"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="sheetforge-token"]').content;
let project = null, run = null, profile = null, cleaned = {}, busy = false;
const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
async function api(path, body, options = {}) {
  const headers = { "X-SheetForge-Token": token, ...options.headers };
  if (body !== undefined && !(body instanceof File)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const response = await fetch(path, { method: body === undefined ? "GET" : "POST", body, ...options, headers });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error((data.detail || "The request failed.") + (data.errors ? " " + data.errors.map(error => error.msg).join(" ") : "")); }
  return response;
}
async function json(path, body) { return (await api(path, body)).json(); }
function notice(text, error = false) { $("notice").hidden = !text; $("notice").textContent = text; $("notice").classList.toggle("error", error); }
function controls() {
  for (const button of document.querySelectorAll("button,input,select,textarea")) button.disabled = busy;
  $("apply-plan").disabled = busy || !project || !$("cleaning-json").value;
  $("make-model").disabled = busy || !run || !["applied", "modeled"].includes(run.status);
  $("review-model").disabled = busy || !run || !["applied", "modeled"].includes(run.status) || !$("model-json").value;
  $("generate").disabled = busy || run?.status !== "modeled";
  $("download").disabled = busy || run?.status !== "generated";
  $("run-status").textContent = run?.status || "Uploaded";
  document.body.setAttribute("aria-busy", String(busy));
}
async function task(message, work) { if (busy) return; busy = true; controls(); notice(message); try { await work(); notice(""); } catch(error) { notice(error.message, true); } finally { busy = false; controls(); } }
function table(target, columns, rows) {
  const node = el("table"), head = el("thead"), tr = el("tr");
  columns.forEach(column => tr.append(el("th", column))); head.append(tr); node.append(head);
  const body = el("tbody"); for (const row of rows) { const item = el("tr"); for (const value of row) item.append(el("td", value == null ? "—" : value)); body.append(item); } node.append(body); $(target).replaceChildren(node);
}
function before() {
  const name = $("sheet").value || Object.keys(profile?.sheets || {})[0]; const sheet = profile?.sheets[name]; if (!sheet) return;
  $("profile-stats").replaceChildren(el("span", sheet.shape[0] + " rows"), el("span", sheet.shape[1] + " columns"), el("span", sheet.blocks.length + " tables detected"));
  const sample = sheet.sample.slice(0, 20); table("before", ["Row", ...Array.from({length: sheet.shape[1]}, (_, i) => "Column " + (i + 1))], sample.map(row => [row.row + 1, ...row.values]));
}
function after(data) {
  cleaned = data.tables || {}; $("table-select").replaceChildren(...Object.keys(cleaned).map(name => new Option(name, name))); renderAfter();
  $("audit").replaceChildren(...(data.steps || []).map(step => el("li", step.table + " · " + step.description)));
  $("warnings").textContent = (data.warnings || []).join(" ");
}
function renderAfter() {
  const data = cleaned[$("table-select").value];
  if (!data) { $("after").replaceChildren(el("p", "Apply a cleaning plan to preview the result.", "helper")); $("after-stats").replaceChildren(); return; }
  $("after-stats").replaceChildren(el("span", data.row_count + " cleaned records"), el("span", data.columns.length + " columns"), el("span", "Preview: first 30 rows"));
  table("after", data.columns, data.rows.map(row => data.columns.map(column => row[column])));
}
async function refreshProjects() {
  const data = await json("/projects"); $("project-list").replaceChildren();
  for (const item of data.projects) { const button = el("button", null, "project-item" + (project?.id === item.id ? " selected" : "")); button.append(el("span", item.name), el("small", new Date(item.created_at).toLocaleDateString())); button.addEventListener("click", () => task("Opening project…", () => openProject(item))); $("project-list").append(button); }
  return data.projects;
}
async function refreshRuns() {
  const data = await json("/runs?project_id=" + project.id); $("run-select").replaceChildren(...data.runs.map(item => new Option(new Date(item.created_at).toLocaleString() + " · " + item.status, item.id))); if(run) $("run-select").value = run.id; return data.runs;
}
async function setRun(item) {
  run = item; $("cleaning-json").value = item?.cleaning_plan_json ? JSON.stringify(JSON.parse(item.cleaning_plan_json), null, 2) : ""; $("model-json").value = item?.model_plan_json ? JSON.stringify(JSON.parse(item.model_plan_json), null, 2) : "";
  after(item ? await json("/preview?run_id=" + item.id) : {});
}
async function openProject(item) {
  project = item; profile = await json("/profile?project_id=" + item.id); $("empty").hidden = true; $("workspace").hidden = false; $("project-name").textContent = item.name;
  $("source-description").textContent = "Refresh source: " + item.source_path;
  $("sheet").replaceChildren(new Option("All sheets", ""), ...Object.keys(profile.sheets).map(name => new Option(name, name))); $("sheet").value = ""; before();
  const runs = await refreshRuns(); await setRun(runs[0] || null); await refreshProjects();
}
$("choose-file").addEventListener("click", () => $("upload").click());
$("upload").addEventListener("change", event => { const file = event.target.files[0]; event.target.value = ""; if(file) task("Profiling workbook…", async () => { const result = await json("/upload?filename=" + encodeURIComponent(file.name), file); await openProject(result.project); }); });
$("path-form").addEventListener("submit", event => { event.preventDefault(); task("Reading local workbook…", async () => { const result = await json("/upload", { source_path: $("source-path").value }); await openProject(result.project); }); });
$("sheet").addEventListener("change", before);
$("table-select").addEventListener("change", renderAfter);
$("cleaning-json").addEventListener("input", controls);
$("model-json").addEventListener("input", controls);
$("make-plan").addEventListener("click", () => task("Inspecting layout and proposing a cleaning plan…", async () => { const result = await json("/plan", { project_id: project.id, run_id: run?.id || null, sheet: $("sheet").value || null }); await setRun(result.run); await refreshRuns(); }));
$("apply-plan").addEventListener("click", () => task("Applying the reviewed plan with pandas…", async () => { const proposed = JSON.parse($("cleaning-json").value); const planned = await json("/plan", { project_id: project.id, run_id: run?.id || null, cleaning_plan: proposed }); run = planned.run; const result = await json("/apply", { project_id: project.id, run_id: run.id }); run = result.run; after(result); $("model-json").value = ""; await refreshRuns(); }));
$("make-model").addEventListener("click", () => task("Planning measures and report pages…", async () => { const result = await json("/model", { project_id: project.id, run_id: run.id }); run = result.run; $("model-json").value = JSON.stringify(result.model_plan, null, 2); await refreshRuns(); }));
$("review-model").addEventListener("click", () => task("Validating the edited semantic model…", async () => { const result = await json("/model", { project_id: project.id, run_id: run.id, model_plan: JSON.parse($("model-json").value) }); run = result.run; await refreshRuns(); }));
$("generate").addEventListener("click", () => task("Generating TMDL, native visuals, and a validated PBIP folder…", async () => { const result = await json("/generate", { project_id: project.id, run_id: run.id }); run = result.run; await refreshRuns(); }));
$("download").addEventListener("click", () => task("Preparing your download…", async () => { const response = await api("/download?run_id=" + run.id); const url = URL.createObjectURL(await response.blob()); const link = el("a"); link.href = url; link.download = "SheetForge-powerbi.zip"; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); }));
$("run-select").addEventListener("change", () => task("Loading saved run…", async () => { const items = await json("/runs?project_id=" + project.id); await setRun(items.runs.find(item => item.id === $("run-select").value)); }));
async function modelMode() { const settings = await json("/settings"); $("mode").textContent = settings.provider === "none" ? "No-AI · offline" : settings.provider + " · " + settings.model; return settings; }
$("settings-open").addEventListener("click", () => task("Loading model settings…", async () => { const data = await modelMode(); $("provider").value = data.provider; $("model-id").value = data.model; $("base-url").value = data.base_url || ""; $("api-version").value = data.api_version || ""; $("api-key").value = ""; $("settings-notice").textContent = data.has_key ? "A saved key is available." : "No key is required for no-AI mode or local Ollama."; $("settings").showModal(); }));
$("settings").addEventListener("close", () => { $("api-key").value = ""; });
$("provider").addEventListener("change", () => { if($("provider").value === "ollama") task("Looking for local Ollama models…", async () => { const result = await json("/settings/ollama"); $("ollama-models").replaceChildren(...result.models.map(name => new Option(name, name))); $("ollama-status").textContent = result.available ? "Available local models are listed in the model field." : "Ollama was not detected at localhost:11434. Start Ollama and try again."; $("base-url").value = "http://localhost:11434"; }); });
$("settings-form").addEventListener("submit", event => { event.preventDefault(); task("Saving settings…", async () => { await json("/settings", { provider: $("provider").value, model: $("model-id").value, base_url: $("base-url").value || null, api_version: $("api-version").value || null, api_key: $("api-key").value || null }); $("api-key").value = ""; await modelMode(); $("settings-notice").textContent = "Settings saved. Keys are stored only in the credential manager."; }); });
$("test-model").addEventListener("click", () => task("Testing the saved connection…", async () => { const result = await json("/settings/test", {}); $("settings-notice").textContent = result.message; }));
task("Opening SheetForge…", async () => { await modelMode(); const items = await refreshProjects(); if(items.length) await openProject(items[0]); });
