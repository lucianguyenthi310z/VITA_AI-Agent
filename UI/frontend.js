"use strict";

const state = {
  contractId: "CON-004",
  latestPayload: null,
  chartRows: [],
  riskAdjustment: 0,
};

const byId = (id) => document.getElementById(id);
const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null && value !== "");

function parseJsonMaybe(value) {
  if (typeof value !== "string") return value ?? {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function numberValue(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeRatio(value) {
  const number = numberValue(value, NaN);
  if (!Number.isFinite(number)) return null;
  return Math.abs(number) <= 1 ? number : number / 100;
}

function booleanValue(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "y"].includes(normalized)) return true;
    if (["false", "0", "no", "n", ""].includes(normalized)) return false;
  }
  if (typeof value === "number") return value !== 0;
  return value == null ? fallback : Boolean(value);
}

function formatPercent(value, digits = 0) {
  const ratio = normalizeRatio(value);
  if (ratio === null) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

function formatMoney(value) {
  const amount = numberValue(value, NaN);
  if (!Number.isFinite(amount)) return "—";
  const abs = Math.abs(amount);
  if (abs >= 1_000_000_000) return `${(amount / 1_000_000_000).toFixed(abs % 1_000_000_000 === 0 ? 0 : 1)} tỷ`;
  if (abs >= 1_000_000) return `${(amount / 1_000_000).toFixed(abs % 1_000_000 === 0 ? 0 : 1)} triệu`;
  return new Intl.NumberFormat("vi-VN").format(amount);
}

function formatFullMoney(value) {
  const amount = numberValue(value, NaN);
  return Number.isFinite(amount)
    ? `${new Intl.NumberFormat("vi-VN").format(amount)} VND`
    : "—";
}

function escapeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  const rawBody = await response.text();
  let body = {};
  try {
    body = rawBody ? JSON.parse(rawBody) : {};
  } catch {
    body = { detail: rawBody || `HTTP ${response.status}` };
  }

  if (!response.ok) {
    const message = body.detail || body.message || `HTTP ${response.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }

  return body;
}

function setLoading(isLoading) {
  byId("loadingOverlay").hidden = !isLoading;
  byId("analyzeButton").disabled = isLoading;
  if (isLoading) {
    byId("workflowStatus").className = "status-pill status-running";
    byId("workflowStatus").textContent = "Đang chạy";
    byId("agentState").textContent = "Đang xử lý";
  }
}

let toastTimer;
function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.className = "toast";
  }, 4200);
}

function setProgress(prefix, confidence) {
  const ratio = normalizeRatio(confidence);
  const percent = ratio === null ? 0 : Math.max(0, Math.min(100, ratio * 100));
  byId(`${prefix}Confidence`).textContent = ratio === null ? "—" : `${Math.round(percent)}%`;
  byId(`${prefix}Progress`).style.width = `${percent}%`;
}

function unionFlags(...groups) {
  const values = groups.flatMap((group) => Array.isArray(group) ? group : []);
  return [...new Set(values.filter(Boolean))];
}

function normalizePayload(payload) {
  const outputs = payload.outputs
    || payload.data?.outputs
    || payload.dify_response?.data?.outputs
    || {};

  const decision = parseJsonMaybe(outputs.decision);
  const finance = parseJsonMaybe(outputs.finance_result);
  
  // Tự động map dữ liệu khách hàng vào hợp đồng để lấy Tỉnh, Loại, Điểm tin cậy
  const baseContract = payload.contract || payload.case_data?.contract || {};
  const customers = payload.case_data?.related_data?.customers || [];
  const baseCId = String(baseContract.customer_id || "").trim();
  const matchedCustomer = customers.find(c => String(c.customer_id || "").trim() === baseCId) || customers[0] || {};
  
  const contract = { ...matchedCustomer, ...baseContract };

  const financialSummary = finance.financial_summary || {};
  const cashflowSummary = finance.cashflow_summary || {};
  const summary = decision.summary || {};

  const chartRows = firstDefined(
    cashflowSummary.monthly_summary,
    outputs.monthly_summary,
    payload.case_data?.related_data?.cashflow_forecasts,
    []
  );

  const flags = unionFlags(
    outputs.financial_flags,
    decision.financial_flags,
    finance.financial_flags
  );

  const computedMargin = firstDefined(
    outputs.computed_margin,
    summary.computed_margin,
    financialSummary.computed_margin,
    contract.gross_margin
  );

  const targetMargin = firstDefined(
    outputs.target_margin,
    summary.target_margin,
    financialSummary.target_margin,
    contract.target_margin,
    0.28
  );

  const computedRatio = normalizeRatio(computedMargin);
  const targetRatio = normalizeRatio(targetMargin);
  const marginGap = firstDefined(
    outputs.margin_gap,
    summary.margin_gap,
    financialSummary.margin_gap,
    computedRatio !== null && targetRatio !== null
      ? computedRatio - targetRatio
      : null
  );

  const reserveMinimum = firstDefined(
    chartRows?.[0]?.cash_reserve_minimum,
    contract.cash_reserve_minimum,
    contract.reserve_minimum
  );

  const workflowRunId = payload.dify_response?.workflow_run_id
    || payload.dify_response?.data?.id
    || payload.workflow_run_id
    || null;

  const missingFields = unionFlags(
    outputs.missing_fields,
    decision.missing_fields,
    decision.external_reasons
  );

  const contractValue = firstDefined(
    contract.contract_value,
    contract.value,
    contract.total_value
  );

  const fundingNeed = firstDefined(
    outputs.maximum_funding_need,
    summary.maximum_funding_need,
    finance.funding_need,
    cashflowSummary.maximum_funding_need
  );

  const monthsBelowReserve = firstDefined(
    outputs.months_below_reserve,
    summary.months_below_reserve,
    finance.months_below_reserve,
    cashflowSummary.months_below_reserve,
    []
  );

  const cashflowRisk = firstDefined(finance.cashflow_risk_level, outputs.risk_level, "UNKNOWN");
  const riskLevel = String(cashflowRisk).toUpperCase();
  const approvalRequired = booleanValue(firstDefined(
    outputs.approval_required,
    decision.approval_required,
    contractValue != null ? numberValue(contractValue) > 300_000_000 : false
  ));

  return {
    payload,
    outputs,
    decision,
    finance,
    contract,
    chartRows: Array.isArray(chartRows) ? chartRows : [],
    flags,
    missingFields,
    computedMargin,
    targetMargin,
    marginGap,
    reserveMinimum,
    fundingNeed,
    monthsBelowReserve: Array.isArray(monthsBelowReserve) ? monthsBelowReserve : [],
    contractValue,
    workflowRunId,
    riskLevel,
    riskScore: firstDefined(outputs.risk_score, decision.risk_score, finance.risk_score),
    requestedAmount: firstDefined(outputs.requested_amount, decision.requested_amount, finance.requested_amount, contract.requested_amount, fundingNeed, 0),
    approvalRequired,
    openInvoiceAmount: firstDefined(outputs.open_invoice_amount, summary.open_invoice_amount),
    totalRevenue: firstDefined(outputs.total_order_revenue, summary.total_order_revenue, financialSummary.total_order_revenue),
    totalCost: firstDefined(outputs.total_estimated_cost, summary.total_estimated_cost, financialSummary.total_estimated_cost),
    agentDecision: firstDefined(outputs.agent_decision, decision.agent_decision, "UNKNOWN"),
    status: firstDefined(outputs.status, decision.status, finance.status, payload.dify_response?.data?.status, "unknown"),
    message: firstDefined(outputs.message, decision.message, finance.message, ""),
  };
}

function renderChecks(data) {
  const checks = [
    { status: "ok", text: `Hợp đồng ${data.contract.contract_id || state.contractId}` },
    {
      status: data.openInvoiceAmount > 0 ? "ok" : "warning",
      text: data.openInvoiceAmount > 0
        ? `Hóa đơn mở: ${formatFullMoney(data.openInvoiceAmount)}`
        : "Chưa phát hiện hóa đơn mở",
    },
    {
      status: data.chartRows.length ? "ok" : "warning",
      text: data.chartRows.length
        ? `Dự báo dòng tiền ${data.chartRows.length} tháng`
        : "Chưa có dữ liệu dự báo dòng tiền",
    },
    {
      status: data.flags.length ? "ok" : "warning",
      text: data.flags.length
        ? `${data.flags.length} cờ tài chính/rủi ro được kích hoạt`
        : "Chưa có cờ rủi ro",
    },
    ...data.missingFields.map((field) => ({
      status: "error",
      text: `Thiếu hoặc cần nguồn ngoài: ${field}`,
    })),
  ];

  byId("dataChecks").innerHTML = checks.map((item) => `
    <li><span class="dot dot-${item.status}"></span>${escapeText(item.text)}</li>
  `).join("");
}

function recommendationList(data) {
  const items = [];
  const marginGap = normalizeRatio(data.marginGap);

  if (marginGap !== null && marginGap < 0) {
    items.push("Rà soát pricing, chi phí và biên lợi nhuận trước khi ký.");
  }
  if (numberValue(data.fundingNeed) > 0) {
    items.push(`Chuẩn bị phương án vốn lưu động tối đa ${formatMoney(data.fundingNeed)}.`);
  }
  if (data.missingFields.length) {
    items.push(`Bổ sung dữ liệu: ${data.missingFields.join(", ")}.`);
  }
  if (data.approvalRequired) {
    items.push("Chuyển Founder phê duyệt do vượt ngưỡng giá trị hợp đồng.");
  }
  if (!items.length) items.push("Có thể tiếp tục quy trình phê duyệt thông thường.");
  return items;
}

function renderDashboard(payload) {
  const data = normalizePayload(payload);
  state.latestPayload = payload;
  state.chartRows = data.chartRows;
  state.contractId = data.contract.contract_id || state.contractId;

  byId("customerName").value = firstDefined(
    data.contract.customer_name,
    data.contract.customer,
    data.contract.customer_id,
    "Không có tên khách hàng"
  );
  byId("contractType").textContent = firstDefined(data.contract.type, data.contract.contract_type, data.contract.customer_type, "—");
  byId("contractCity").textContent = firstDefined(data.contract.city, data.contract.province, data.contract.location, "—");
  byId("paymentReliability").textContent = formatPercent(firstDefined(data.contract.payment_reliability, data.contract.reliability_score), 0);
  byId("strategicValue").textContent = firstDefined(data.contract.strategic_value, "—");
  byId("grossMargin").textContent = formatPercent(data.computedMargin, 0);
  byId("contractValue").textContent = formatMoney(data.contractValue);
  byId("fundingNeed").textContent = formatMoney(data.fundingNeed);
  byId("reserveMinimum").textContent = formatMoney(data.reserveMinimum);

  const status = String(data.status).toLowerCase();
  byId("workflowStatus").textContent = status === "partial" ? "Partial" : status === "succeeded" ? "Succeeded" : status;
  byId("workflowStatus").className = `status-pill ${status === "partial" ? "status-partial" : status === "succeeded" ? "status-success" : "status-idle"}`;
  byId("agentState").textContent = status === "partial" ? "Cần bổ sung dữ liệu" : "Đã hoàn thành";

  renderChecks(data);

  const marginGapRatio = normalizeRatio(data.marginGap);
  const findings = [
    `Doanh thu ${formatMoney(data.totalRevenue)}, chi phí ${formatMoney(data.totalCost)}.`,
    `Biên lợi nhuận ${formatPercent(data.computedMargin)} so với mục tiêu ${formatPercent(data.targetMargin)}; chênh lệch ${marginGapRatio === null ? "—" : `${(marginGapRatio * 100).toFixed(1)} điểm %`}.`,
    `Nhu cầu vốn tối đa ${formatMoney(data.fundingNeed)}; ${data.monthsBelowReserve.length} tháng thấp hơn mức dự trữ.`,
  ];
  byId("keyFindings").innerHTML = findings.map((text) => `<li>${escapeText(text)}</li>`).join("");

  const recommendations = recommendationList(data);
  byId("recommendations").innerHTML = recommendations.map((text) => `<li>${escapeText(text)}</li>`).join("");

  const riskLabel = data.riskLevel === "HIGH" || data.riskLevel === "CRITICAL" ? "Cao" : data.riskLevel === "MEDIUM" ? "Trung bình" : data.riskLevel === "LOW" ? "Thấp" : "Chưa rõ";
  const adjustedRiskScore = numberValue(data.riskScore, NaN) + state.riskAdjustment;
  byId("riskLevel").textContent = Number.isFinite(adjustedRiskScore)
    ? `${riskLabel} (${adjustedRiskScore} điểm${state.riskAdjustment ? ", +2 do bỏ qua dữ liệu" : ""})`
    : riskLabel;
  byId("confidenceScore").textContent = formatPercent(firstDefined(data.outputs.confidence_score, data.finance.confidence_score), 0);
  byId("anomalyCount").textContent = String(data.flags.length + data.missingFields.length);

  const financeConfidence = firstDefined(data.outputs.finance_confidence, data.finance.confidence_score, data.outputs.confidence_score);
  const riskConfidence = firstDefined(data.outputs.risk_confidence, data.outputs.confidence_score);
  const decisionConfidence = firstDefined(data.outputs.decision_confidence, data.decision.confidence_score, data.outputs.confidence_score);
  setProgress("finance", financeConfidence);
  setProgress("risk", riskConfidence);
  setProgress("decision", decisionConfidence);

  byId("financeAgentIcon").textContent = "✓";
  byId("riskAgentIcon").textContent = data.riskLevel === "HIGH" || data.riskLevel === "CRITICAL" ? "⚠" : "✓";
  byId("decisionAgentIcon").textContent = "✓";
  byId("financeAgentText").textContent = data.message || `Đã tính toán tài chính cho ${state.contractId}.`;
  byId("riskAgentText").textContent = `${data.riskLevel} risk; ${data.monthsBelowReserve.length} tháng dưới mức dự trữ.`;
  byId("decisionAgentText").textContent = `Quyết định: ${data.agentDecision}.`;

  byId("financeFlags").innerHTML = data.flags.slice(0, 5).map((flag) => `<span class="tag">${escapeText(flag)}</span>`).join("");
  byId("riskTags").innerHTML = [
    data.riskLevel !== "UNKNOWN" ? `Rủi ro ${data.riskLevel}` : null,
    data.monthsBelowReserve.length ? `Thiếu quỹ ${data.monthsBelowReserve.length} tháng` : null,
  ].filter(Boolean).map((tag) => `<span class="tag">${escapeText(tag)}</span>`).join("");

  byId("approvalState").textContent = data.approvalRequired ? "Cần phê duyệt" : "Không bắt buộc";
  byId("approvalText").textContent = data.approvalRequired
    ? `${formatMoney(data.contractValue)} > 300 triệu — cần Founder phê duyệt.`
    : "Giá trị hợp đồng không vượt ngưỡng phê duyệt tự động.";

  byId("cashflowViolation").textContent = data.monthsBelowReserve.length
    ? `⚠ Vi phạm RR-002 — ${data.monthsBelowReserve.join(", ")}`
    : "Không phát hiện tháng dưới mức dự trữ.";

  byId("rawOutput").textContent = JSON.stringify(data.outputs, null, 2);
  byId("workflowRunId").textContent = `Workflow run: ${data.workflowRunId || "—"}`;
  byId("lastRun").textContent = `Thực thi gần nhất: ${new Date().toLocaleTimeString("vi-VN")}`;

  drawCashflowChart(data.chartRows);
}

function compactAxis(value) {
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(0)}M`;
  return String(Math.round(value));
}

function drawCashflowChart(rows) {
  const canvas = byId("cashflowChart");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(320, Math.round(rect.width * ratio));
  canvas.height = Math.max(220, Math.round(rect.height * ratio));

  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const width = canvas.width / ratio;
  const height = canvas.height / ratio;
  ctx.clearRect(0, 0, width, height);

  if (!Array.isArray(rows) || !rows.length) {
    ctx.fillStyle = "#6c788a";
    ctx.font = "14px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("Chưa có monthly_summary để vẽ biểu đồ", width / 2, height / 2);
    return;
  }

  const normalized = rows.map((row, index) => ({
    month: row.month || row.period || `T${index + 1}`,
    cashIn: numberValue(firstDefined(row.expected_cash_in, row.cash_in, row.inflow)),
    cashOut: numberValue(firstDefined(row.expected_cash_out, row.cash_out, row.outflow)),
    closing: numberValue(firstDefined(row.projected_closing_cash, row.closing_cash, row.balance)),
  }));

  const values = normalized.flatMap((row) => [row.cashIn, -row.cashOut, row.closing]);
  let minValue = Math.min(0, ...values);
  let maxValue = Math.max(0, ...values);
  if (minValue === maxValue) maxValue = minValue + 1;
  const range = maxValue - minValue;
  minValue -= range * 0.12;
  maxValue += range * 0.12;

  const margin = { top: 18, right: 18, bottom: 42, left: 58 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const y = (value) => margin.top + ((maxValue - value) / (maxValue - minValue)) * plotHeight;
  const zeroY = y(0);

  ctx.font = "11px system-ui";
  ctx.strokeStyle = "#e3e7ed";
  ctx.fillStyle = "#657084";
  ctx.lineWidth = 1;
  ctx.textAlign = "right";

  for (let i = 0; i <= 5; i += 1) {
    const value = maxValue - ((maxValue - minValue) * i / 5);
    const lineY = y(value);
    ctx.beginPath();
    ctx.moveTo(margin.left, lineY);
    ctx.lineTo(width - margin.right, lineY);
    ctx.stroke();
    ctx.fillText(compactAxis(value), margin.left - 8, lineY + 4);
  }

  ctx.strokeStyle = "#7d8590";
  ctx.beginPath();
  ctx.moveTo(margin.left, zeroY);
  ctx.lineTo(width - margin.right, zeroY);
  ctx.stroke();

  const slot = plotWidth / normalized.length;
  const barWidth = Math.min(28, slot * 0.25);

  normalized.forEach((row, index) => {
    const centerX = margin.left + slot * (index + 0.5);

    ctx.fillStyle = "#54d88d";
    const inTop = y(row.cashIn);
    ctx.fillRect(centerX - barWidth - 2, inTop, barWidth, Math.max(1, zeroY - inTop));

    ctx.fillStyle = "#ef7070";
    const outBottom = y(-row.cashOut);
    ctx.fillRect(centerX + 2, zeroY, barWidth, Math.max(1, outBottom - zeroY));

    ctx.fillStyle = "#5d6674";
    ctx.textAlign = "center";
    ctx.fillText(String(row.month).replace("2026-", "T"), centerX, height - 15);
  });

  ctx.strokeStyle = "#5d6470";
  ctx.fillStyle = "#5d6470";
  ctx.lineWidth = 2;
  ctx.beginPath();
  normalized.forEach((row, index) => {
    const centerX = margin.left + slot * (index + 0.5);
    const lineY = y(row.closing);
    if (index === 0) ctx.moveTo(centerX, lineY);
    else ctx.lineTo(centerX, lineY);
  });
  ctx.stroke();

  normalized.forEach((row, index) => {
    const centerX = margin.left + slot * (index + 0.5);
    const lineY = y(row.closing);
    ctx.beginPath();
    ctx.arc(centerX, lineY, 3.5, 0, Math.PI * 2);
    ctx.fill();
  });
}

async function loadContracts() {
  try {
    const response = await requestJson("/api/contracts");
    const contracts = Array.isArray(response.data) ? response.data : [];
    if (!contracts.length) return;

    const select = byId("contractSelect");
    select.innerHTML = contracts.map((contract) => {
      const id = contract.contract_id || contract.id;
      const customer = contract.customer_name || contract.customer_id || "";
      return `<option value="${escapeText(id)}" data-customer="${escapeText(customer)}">${escapeText(id)}</option>`;
    }).join("");

    if (contracts.some((contract) => (contract.contract_id || contract.id) === state.contractId)) {
      select.value = state.contractId;
    } else {
      state.contractId = select.value;
    }
  } catch (error) {
    showToast(`Không tải được danh sách hợp đồng: ${error.message}`, true);
  }
}

async function analyzeSelectedContract() {
  const contractId = byId("contractSelect").value.trim().toUpperCase();
  if (!contractId) return showToast("Hãy chọn mã hợp đồng.", true);

  state.contractId = contractId;
  setLoading(true);

  try {
    const payload = await requestJson(`/api/agent/analyze/${encodeURIComponent(contractId)}`, {
      method: "POST",
    });
    renderDashboard(payload);
    showToast(`Đã tải dữ liệu hợp đồng ${contractId}.`);
  } catch (error) {
    byId("workflowStatus").className = "status-pill status-error";
    byId("workflowStatus").textContent = "Lỗi";
    byId("agentState").textContent = "Thất bại";
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

function openModal({ step, title, message, fields = "", actions }) {
  byId("modalStep").textContent = step;
  byId("modalTitle").textContent = title;
  byId("modalMessage").textContent = message;
  byId("modalFields").innerHTML = fields;
  byId("modalActions").innerHTML = actions.map((action) =>
    `<button type="button" class="button ${action.className || "button-outline"}" data-modal-action="${action.value}">${escapeText(action.label)}</button>`
  ).join("");
  byId("workflowModal").hidden = false;
  return new Promise((resolve) => {
    const finish = (value) => {
      byId("workflowModal").hidden = true;
      byId("modalActions").onclick = null;
      byId("modalClose").onclick = null;
      resolve(value);
    };
    byId("modalActions").onclick = (event) => {
      const button = event.target.closest("[data-modal-action]");
      if (button) finish(button.dataset.modalAction);
    };
    byId("modalClose").onclick = () => finish(null);
  });
}

async function rerunAgent1(body) {
  setLoading(true);
  try {
    const payload = await requestJson(`/api/agent/analyze/${encodeURIComponent(state.contractId)}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    renderDashboard(payload);
    return payload;
  } finally {
    setLoading(false);
  }
}

async function handleMissingData(missingFields) {
  const choice = await openModal({
    step: "Popup 1 · Dữ liệu còn thiếu",
    title: "Cần bổ sung dữ liệu",
    message: `Các trường còn thiếu: ${missingFields.join(", ")}.\nNếu bỏ qua, điểm rủi ro hiển thị sẽ tăng thêm 2 điểm.`,
    actions: [
      { value: "supplement", label: "Bổ sung", className: "button-primary" },
      { value: "skip", label: "Bỏ qua", className: "button-cancel" },
    ],
  });
  if (choice === "supplement") {
    const fields = missingFields.map((field, index) => `
      <label>${escapeText(field)}<input data-missing-field="${escapeText(field)}" id="missing-${index}" required></label>
    `).join("");
    const submit = await openModal({
      step: "Popup 4 · Bổ sung dữ liệu",
      title: "Nhập dữ liệu còn thiếu",
      message: "Dữ liệu này sẽ được gửi lại cho Agent 1 để phân tích lại.",
      fields,
      actions: [
        { value: "submit", label: "Gửi và chạy lại", className: "button-primary" },
        { value: "cancel", label: "Hủy", className: "button-cancel" },
      ],
    });
    if (submit !== "submit") return false;
    const supplementalData = {};
    document.querySelectorAll("[data-missing-field]").forEach((input) => {
      supplementalData[input.dataset.missingField] = input.value.trim();
    });
    if (Object.values(supplementalData).some((value) => !value)) {
      showToast("Vui lòng nhập đầy đủ dữ liệu cần bổ sung.", true);
      return false;
    }
    state.riskAdjustment = 0;
    await rerunAgent1({ supplemental_data: supplementalData });
    return false;
  }
  if (choice === "skip") {
    state.riskAdjustment = 2;
    await rerunAgent1({ skip_missing_data: true });
    return true;
  }
  return false;
}

async function callAgent2(founderDecision, externalSendConfirmation = null) {
  setLoading(true);
  try {
    const payload = { founder_decision: founderDecision };
    if (externalSendConfirmation) payload.external_send_confirmation = externalSendConfirmation;
    const response = await requestJson(`/api/agent/founder-decision/${encodeURIComponent(state.contractId)}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    byId("rawOutput").textContent = JSON.stringify(response.outputs, null, 2);
    byId("decisionAgentText").textContent = `Founder đã chọn: ${founderDecision}.`;
    return response;
  } finally {
    setLoading(false);
  }
}

async function handleAcceptFlow() {
  if (!state.latestPayload) return showToast("Hãy chạy phân tích trước khi duyệt hợp đồng.", true);
  let data = normalizePayload(state.latestPayload);
  if (data.missingFields.length) {
    const skipped = await handleMissingData(data.missingFields);
    if (!skipped) return;
    data = normalizePayload(state.latestPayload);
  }

  const founderDecision = await openModal({
    step: "Popup 2 · Quyết định Founder",
    title: "Chọn quyết định cho hợp đồng",
    message: `Hợp đồng ${state.contractId} · Số tiền đề nghị ${formatFullMoney(data.requestedAmount)}.`,
    actions: [
      { value: "approve", label: "Duyệt", className: "button-accept" },
      { value: "request_more_info", label: "Yêu cầu thêm thông tin", className: "button-more" },
      { value: "reject", label: "Từ chối", className: "button-reject" },
    ],
  });
  if (!founderDecision) return;
  if (founderDecision !== "approve") {
    await callAgent2(founderDecision);
    showToast("Đã gửi quyết định của Founder tới Agent 2.");
    return;
  }

  const aboveThreshold = numberValue(data.requestedAmount) > 300_000_000;
  const confirmation = await openModal({
    step: "Popup 3 · Xác nhận gửi ngoài",
    title: aboveThreshold ? "Xác nhận gửi hồ sơ ngân hàng" : "Xác nhận duyệt hợp đồng",
    message: aboveThreshold
      ? "Số tiền đề nghị trên 300 triệu. Bạn có xác nhận gửi hồ sơ tới ngân hàng không?"
      : "Số tiền đề nghị dưới 300 triệu. Bạn có xác nhận duyệt không? Hồ sơ sẽ được tự động gửi tới ngân hàng.",
    actions: [
      { value: "confirm", label: "Xác nhận", className: "button-accept" },
      { value: "cancel", label: "Hủy", className: "button-cancel" },
    ],
  });
  if (!confirmation) return;
  await callAgent2("approve", confirmation);
  if (confirmation === "confirm") showToast("Đã duyệt và tự động gửi hồ sơ đến ngân hàng.");
  else showToast("Đã ghi nhận duyệt nhưng hủy gửi hồ sơ đến ngân hàng.");
}

async function submitDecision(decision) {
  if (!state.latestPayload) {
    return showToast("Hãy chạy phân tích trước khi lưu quyết định.", true);
  }

  const workflowRunId = state.latestPayload.dify_response?.workflow_run_id
    || state.latestPayload.dify_response?.data?.id
    || null;

  try {
    await requestJson(`/api/contracts/${encodeURIComponent(state.contractId)}/decision`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        workflow_run_id: workflowRunId,
        decided_at: new Date().toISOString(),
        source: "opc-web-dashboard",
      }),
    });
    showToast(`Đã lưu quyết định ${decision} cho ${state.contractId}.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function bindEvents() {
  byId("analyzeButton").addEventListener("click", analyzeSelectedContract);
  byId("contractSelect").addEventListener("change", () => {
    state.contractId = byId("contractSelect").value;
    const option = byId("contractSelect").selectedOptions[0];
    if (option?.dataset.customer) byId("customerName").value = option.dataset.customer;
  });
  byId("addContractButton").addEventListener("click", () => {
    showToast("Hãy thêm hợp đồng trong Supabase hoặc bổ sung endpoint POST /api/contracts.");
  });
  document.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => button.dataset.decision === "ACCEPT"
      ? handleAcceptFlow().catch((error) => showToast(error.message, true))
      : submitDecision(button.dataset.decision));
  });
  window.addEventListener("resize", () => drawCashflowChart(state.chartRows));
}

async function init() {
  bindEvents();
  drawCashflowChart([]);
  await loadContracts();
}

document.addEventListener("DOMContentLoaded", init);
