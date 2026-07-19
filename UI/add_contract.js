"use strict";

(() => {
  let customers = [];
  let products = [];
  let nextContractId = "";
  let nextOrderId = "";

  const customerFieldIds = [
    "new_customer_name",
    "new_customer_type",
    "new_province",
    "new_payment_reliability",
    "new_strategic_value",
    "new_industry",
    "new_revenue_model",
  ];

  async function initAddContractModule() {
    try {
      const response = await fetch("/UI/add_contract.html", { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      document.body.insertAdjacentHTML("beforeend", await response.text());

      byId("addContractButton")?.addEventListener("click", openAddContractModal);
      byId("sideNavAddBtn")?.addEventListener("click", openAddContractModal);
      byId("addContractClose").addEventListener("click", closeAddContractModal);
      byId("btnCancelNewContract").addEventListener("click", closeAddContractModal);
      byId("contractResultClose").addEventListener("click", closeContractResult);
      byId("new_customer_id").addEventListener("change", handleCustomerChange);
      byId("newContractForm").addEventListener("submit", handleNewContractSubmit);
      byId("addContractModal").addEventListener("click", (event) => {
        if (event.target === byId("addContractModal")) closeAddContractModal();
      });
      byId("contractResultModal").addEventListener("click", (event) => {
        if (event.target === byId("contractResultModal")) closeContractResult();
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          if (!byId("contractResultModal").hidden) closeContractResult();
          else if (!byId("addContractModal").hidden) closeAddContractModal();
        }
      });

      await loadFormOptions();
    } catch (error) {
      console.error("Không khởi tạo được form thêm hợp đồng:", error);
      showToast(`Không tải được form thêm hợp đồng: ${error.message}`, true);
    }
  }

  async function loadFormOptions() {
    const response = await requestJson("/api/new-contract/options");
    customers = Array.isArray(response.customers) ? response.customers : [];
    products = Array.isArray(response.products) ? response.products : [];
    nextContractId = response.next_contract_id || "";
    nextOrderId = response.next_order_id || "";
    const writeReady = Boolean(response.database_write_ready);
    byId("databaseWriteNotice").hidden = writeReady;
    byId("databaseWriteNotice").textContent = writeReady
      ? ""
      : "Database đang ở chế độ chỉ đọc: backend chưa có SUPABASE_SECRET_KEY hoặc SUPABASE_SERVICE_ROLE_KEY.";

    byId("new_customer_id").innerHTML = [
      '<option value="">-- Chọn Customer --</option>',
      '<option value="__new__">＋ Thêm Customer mới</option>',
      ...customers.map((customer) => (
        `<option value="${escapeText(customer.customer_id)}">${escapeText(customer.customer_id)} — ${escapeText(customer.customer_name)}</option>`
      )),
    ].join("");

    byId("new_service_id").innerHTML = [
      '<option value="">-- Không chọn --</option>',
      ...products.map((product) => (
        `<option value="${escapeText(product.service_id)}">${escapeText(product.service_id)} — ${escapeText(product.service_name)}</option>`
      )),
    ].join("");
    byId("new_contract_id").value = nextContractId;
    byId("new_order_id").value = nextOrderId;
  }

  function setCustomerFieldsEnabled(enabled) {
    customerFieldIds.forEach((id) => {
      byId(id).disabled = !enabled;
    });
  }

  function clearCustomerFields() {
    customerFieldIds.forEach((id) => {
      byId(id).value = "";
    });
  }

  function fillCustomerFields(customer) {
    byId("new_customer_name").value = customer.customer_name || "";
    byId("new_customer_type").value = customer.customer_type || "";
    byId("new_province").value = customer.province || "";
    byId("new_payment_reliability").value = customer.payment_reliability ?? "";
    byId("new_strategic_value").value = customer.strategic_value || "";
    byId("new_industry").value = customer.industry || "";
    byId("new_revenue_model").value = customer.revenue_model || "";
  }

  function handleCustomerChange() {
    const customerId = byId("new_customer_id").value;
    const isNew = customerId === "__new__";
    const customer = customers.find((item) => item.customer_id === customerId);

    clearCustomerFields();
    setCustomerFieldsEnabled(isNew);

    if (isNew) {
      byId("customerModeHint").textContent = "Customer ID sẽ được backend tự sinh khi lưu.";
      byId("new_customer_name").focus();
    } else if (customer) {
      fillCustomerFields(customer);
      byId("customerModeHint").textContent = `Đang dùng dữ liệu của ${customer.customer_id}; các trường được khóa để tránh sửa nhầm.`;
    } else {
      byId("customerModeHint").textContent = "Chọn mã có sẵn để tự động hiển thị thông tin Customer.";
    }
  }

  function openAddContractModal() {
    const form = byId("newContractForm");
    form.reset();
    byId("new_contract_id").value = nextContractId;
    byId("new_order_id").value = nextOrderId;
    handleCustomerChange();
    byId("addContractModal").hidden = false;
    document.body.classList.add("modal-open");
    byId("new_customer_id").focus();
  }

  function closeAddContractModal() {
    byId("addContractModal").hidden = true;
    document.body.classList.remove("modal-open");
  }

  function showContractResult(success, message) {
    const modal = byId("contractResultModal");
    modal.classList.toggle("result-success", success);
    modal.classList.toggle("result-error", !success);
    byId("contractResultIcon").textContent = success ? "✓" : "!";
    byId("contractResultTitle").textContent = success
      ? "Thêm hợp đồng thành công"
      : "Thêm hợp đồng thất bại";
    byId("contractResultMessage").textContent = message;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    byId("contractResultClose").focus();
  }

  function closeContractResult() {
    byId("contractResultModal").hidden = true;
    if (byId("addContractModal").hidden) document.body.classList.remove("modal-open");
  }

  function optionalText(id) {
    const value = byId(id).value.trim();
    return value || null;
  }

  function optionalNumber(id) {
    const value = byId(id).value.trim();
    return value === "" ? null : Number(value);
  }

  function buildPayload() {
    const customerId = byId("new_customer_id").value;
    const isNewCustomer = customerId === "__new__";

    return {
      customer_id: isNewCustomer ? null : customerId,
      new_customer: isNewCustomer ? {
        customer_name: byId("new_customer_name").value.trim(),
        customer_type: byId("new_customer_type").value,
        province: byId("new_province").value.trim(),
        payment_reliability: optionalNumber("new_payment_reliability"),
        strategic_value: optionalText("new_strategic_value"),
        industry: optionalText("new_industry"),
        revenue_model: optionalText("new_revenue_model"),
      } : null,
      contract: {
        contract_value: Number(byId("new_contract_value").value),
        gross_margin: optionalNumber("new_gross_margin"),
        start_date: byId("new_start_date").value,
        end_date: byId("new_end_date").value,
        status: byId("new_status").value,
        payment_terms: byId("new_payment_terms").value,
        description: optionalText("new_description"),
      },
      order: {
        order_revenue: Number(byId("new_order_revenue").value),
        estimated_cost: Number(byId("new_estimated_cost").value),
        due_date: byId("new_due_date").value,
        service_id: optionalText("new_service_id"),
        delivery_note: optionalText("new_delivery_note"),
      },
    };
  }

  async function handleNewContractSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    if (byId("new_end_date").value < byId("new_start_date").value) {
      showToast("Ngày kết thúc không được trước ngày bắt đầu.", true);
      byId("new_end_date").focus();
      return;
    }

    const submitButton = byId("btnSubmitNewContract");
    const originalText = submitButton.textContent;
    submitButton.disabled = true;
    submitButton.textContent = "Đang lưu...";

    try {
      const response = await requestJson("/api/contracts", {
        method: "POST",
        body: JSON.stringify(buildPayload()),
      });
      const createdContract = response.data?.contract || {};
      const createdCustomer = response.data?.customer || {};
      const createdOrder = response.data?.order || {};

      closeAddContractModal();
      showContractResult(
        true,
        `${response.message || "Dữ liệu đã được lưu vào database."}\nCustomer: ${createdCustomer.customer_id || "—"}\nContract: ${createdContract.contract_id || "—"}\nOrder: ${createdOrder.order_id || "—"}`
      );

      try {
        await Promise.all([loadContracts(), loadFormOptions()]);
        if (createdContract.contract_id) {
          byId("contractSelect").value = createdContract.contract_id;
          state.contractId = createdContract.contract_id;
        }
        byId("customerName").value = createdCustomer.customer_name || createdCustomer.customer_id || "";
      } catch (refreshError) {
        console.error("Đã lưu nhưng không làm mới được dashboard:", refreshError);
        showToast("Đã lưu database nhưng chưa làm mới được danh sách trên giao diện.", true);
      }
    } catch (error) {
      showContractResult(false, `Không lưu được dữ liệu vào database.\n${error.message}`);
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = originalText;
    }
  }

  document.addEventListener("DOMContentLoaded", initAddContractModule);
})();
