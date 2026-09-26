const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const resp = await fetch(path, opts);
  const data = await resp.json();
  if (!resp.ok || data.ok === false) throw new Error(data.error || `HTTP ${resp.status}`);
  return data.data;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function badge(status) {
  const map = {
    pending: "待提交", submitting: "提交中", completed: "已完成", partial: "部分完成",
    timeout: "超时可重试", retrying: "重试中", rejected: "业务驳回",
    imported: "已导入", validated: "已校验", submitted: "已提交", invalid: "校验异常",
  };
  return `<span class="badge badge-${esc(status)}">${map[status] || esc(status)}</span>`;
}
function errType(t) {
  return { validation_error: "字段校验", duplicate: "重复记录", business_reject: "业务驳回" }[t] || t;
}
function showOutput(el, data) {
  el.textContent = JSON.stringify(data, null, 2);
}

// Tabs
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "batches") loadBatches();
    if (btn.dataset.tab === "exceptions") loadExceptions();
    if (btn.dataset.tab === "report") loadReport();
  });
});

// Import workflow
$("btn-import").onclick = async () => {
  const f = $("file").files[0];
  if (!f) return alert("请先选择 CSV 或 JSON 文件");
  const out = $("import-output");
  out.textContent = "上传中…";
  try {
    const body = new FormData();
    body.append("file", f);
    const d = await api("/api/import", { method: "POST", body });
    showOutput(out, { 导入结果: d });
  } catch (e) { out.textContent = "❌ " + e.message; }
};
$("btn-validate").onclick = async () => {
  const out = $("import-output"); out.textContent = "分批校验中…";
  try { showOutput(out, { 分批校验: await api("/api/batch/validate", { method: "POST" }) }); }
  catch (e) { out.textContent = "❌ " + e.message; }
};
$("btn-process").onclick = async () => {
  const out = $("import-output"); out.textContent = "提交监管接口中（超时会自动重试）…";
  try { showOutput(out, { 提交结果: await api("/api/process", { method: "POST" }) }); }
  catch (e) { out.textContent = "❌ " + e.message; }
};
$("btn-retry").onclick = async () => {
  const out = $("import-output"); out.textContent = "重试超时批次中…";
  try { showOutput(out, { 重试结果: await api("/api/retry", { method: "POST" }) }); }
  catch (e) { out.textContent = "❌ " + e.message; }
};
$("btn-save-report").onclick = async () => {
  const out = $("import-output"); out.textContent = "生成报告快照…";
  try { showOutput(out, { 报告快照: await api("/api/report/generate", { method: "POST" }) }); }
  catch (e) { out.textContent = "❌ " + e.message; }
};

// Batches
async function loadBatches() {
  const rows = await api("/api/batches");
  $("batches-body").innerHTML = rows.map(b => `
    <tr>
      <td>${b.id}</td><td>${esc(b.batch_no)}</td><td>${badge(b.status)}</td>
      <td>${b.total}</td><td>${b.accepted}</td><td>${b.rejected}</td>
      <td>${b.attempts}</td>
      <td>${esc((b.error_message || "").slice(0, 60))}</td>
      <td>${esc(b.updated_at)}</td>
    </tr>`).join("") || `<tr><td colspan="9" style="text-align:center;color:#9ca3af">暂无批次</td></tr>`;
}
$("btn-refresh-batches").onclick = loadBatches;

// Exceptions
async function loadExceptions() {
  const t = $("exc-filter").value;
  const rows = await api("/api/exceptions" + (t ? `?error_type=${t}` : ""));
  $("exc-body").innerHTML = rows.map(e => `
    <tr>
      <td>${e.id}</td><td>${esc(errType(e.error_type))}</td>
      <td>${esc(e.plate_no)}</td><td>${esc(e.vin)}</td>
      <td>${esc(e.organization)}</td><td>${esc(e.inspect_date)}</td>
      <td>${esc(e.error_message)}</td><td>${e.source_row ?? ""}</td>
    </tr>`).join("") || `<tr><td colspan="8" style="text-align:center;color:#9ca3af">暂无异常</td></tr>`;
}
$("exc-filter").onchange = loadExceptions;

// Report
function statCard(num, label, cls = "") {
  return `<div class="stat ${cls}"><div class="num">${num}</div><div class="label">${label}</div></div>`;
}
async function loadReport() {
  const r = await api("/api/report");
  $("report-cards").innerHTML =
    statCard(r.total_imported, "累计导入") +
    statCard(r.total_submitted, "成功提交", "ok") +
    statCard(r.total_rejected, "记录驳回", "warn") +
    statCard(r.total_exceptions, "异常总数", "warn") +
    statCard(r.pending_batches, "待处理批次") +
    statCard(r.timeout_batches, "超时批次", "warn");
  $("report-status").textContent = JSON.stringify(r.records_by_status, null, 2);
  $("report-result").textContent = JSON.stringify(r.by_result, null, 2);
  $("report-error").textContent = JSON.stringify(r.by_error_type, null, 2);
  $("report-org").textContent = JSON.stringify(r.by_organization, null, 2);

  const snaps = await api("/api/reports/snapshots");
  $("snap-body").innerHTML = snaps.map(s => `
    <tr><td>${s.id}</td><td>${esc(s.generated_at)}</td>
    <td>${s.total_imported}</td><td>${s.total_submitted}</td>
    <td>${s.total_rejected}</td><td>${s.total_exceptions}</td>
    <td>${s.timeout_batches}</td></tr>`).join("")
    || `<tr><td colspan="7" style="text-align:center;color:#9ca3af">暂无快照，点击“生成快照”写入数据库</td></tr>`;
}
$("btn-refresh-report").onclick = loadReport;
$("btn-gen-report").onclick = async () => {
  await api("/api/report/generate", { method: "POST" });
  loadReport();
};

loadBatches();
