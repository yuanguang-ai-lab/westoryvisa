(function initializeBillingPage(global) {
  "use strict";

  const page = document.body.dataset.billingPage;
  if (!page) return;
  const api = global.DocFlowApi;
  const notice = document.querySelector("#billingNotice");

  function endpoint(path) {
    return `${api?.apiBaseUrl || "/api"}${path}`;
  }

  function showNotice(message, tone = "info") {
    if (!notice) return;
    notice.hidden = !message;
    notice.textContent = message || "";
    notice.dataset.tone = tone;
  }

  async function request(path, options) {
    if (global.location.protocol === "file:") {
      throw new Error("本地文件模式不能连接支付后端，请使用域名网站。");
    }
    const response = await api.request(endpoint(path), Object.assign({
      headers: { "Content-Type": "application/json" },
    }, options || {}));
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
    return payload;
  }

  function money(amount, currency) {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency", currency: String(currency || "cny").toUpperCase(),
    }).format(Number(amount || 0) / 100);
  }

  function dateTime(value) {
    if (!value) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "medium", timeStyle: "short",
    }).format(new Date(value));
  }

  function statusLabel(status) {
    const labels = {
      creating: "创建中", pending: "待支付", paid: "已支付",
      failed: "失败", expired: "已过期", refunded: "已退款",
      partially_refunded: "部分退款", succeeded: "已成功",
      active: "有效", inactive: "未开通",
    };
    return labels[status] || status || "未知";
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[character]);
  }

  async function loadMembership() {
    const title = document.querySelector("#membershipStatusTitle");
    const detail = document.querySelector("#membershipStatusDetail");
    const badge = document.querySelector("#membershipStatusBadge");
    try {
      const session = await request("/session");
      if (!session.user) {
        title.textContent = "请先登录机构账号";
        detail.textContent = "登录后才能创建订单，会员权益会绑定到当前机构。";
        badge.textContent = "尚未登录";
        document.querySelectorAll(".billing-checkout").forEach((button) => {
          button.textContent = "登录后购买";
          button.addEventListener("click", () => { global.location.href = "/workspace"; });
        });
        return;
      }
      const accountAction = document.querySelector("#membershipAccountAction");
      if (accountAction) {
        accountAction.textContent = "退出账号";
        accountAction.href = "#";
        accountAction.addEventListener("click", async (event) => {
          event.preventDefault();
          accountAction.setAttribute("aria-disabled", "true");
          accountAction.textContent = "正在退出…";
          try {
            await request("/logout", { method: "POST", body: "{}" });
            global.location.replace("/workspace");
          } catch (error) {
            accountAction.removeAttribute("aria-disabled");
            accountAction.textContent = "退出账号";
            showNotice(error.message || "退出失败，请稍后重试。", "error");
          }
        }, { once: true });
      }
      const billing = await request("/billing");
      const membership = billing.membership || {};
      const trial = billing.trial || {};
      const trialDetail = document.querySelector("#freeTrialDetail");
      const trialBadge = document.querySelector("#freeTrialBadge");
      const trialWorkspaceLink = document.querySelector("#freeTrialWorkspaceLink");
      const legalAcceptance = document.querySelector("#legalAcceptance");
      const checkoutButtons = [...document.querySelectorAll(".billing-checkout")];
      checkoutButtons.forEach((button) => { button.dataset.defaultText ||= button.textContent; });
      const syncCheckoutAvailability = () => {
        const legalReady = Boolean(global.WESTORY_LEGAL?.configured);
        const accepted = Boolean(legalAcceptance?.checked);
        checkoutButtons.forEach((button) => {
          button.disabled = !billing.gateway.configured || !legalReady || !accepted;
          if (!billing.gateway.configured) {
            button.textContent = "支付通道接入中";
            button.title = billing.gateway.message || "支付通道接通后可购买";
          } else if (!legalReady) {
            button.textContent = button.dataset.defaultText;
            button.title = "运营主体资料配置完成后可购买";
          } else if (!accepted) {
            button.textContent = button.dataset.defaultText;
            button.title = "请先阅读并同意购买页面下方的法律条款";
          } else {
            button.textContent = button.dataset.defaultText;
            button.title = "";
          }
        });
      };
      legalAcceptance?.addEventListener("change", syncCheckoutAvailability);
      document.addEventListener("westory:merchant-profile", syncCheckoutAvailability, { once: true });
      if (membership.active) {
        title.textContent = "会员权益有效";
        detail.textContent = `当前有效期至 ${dateTime(membership.currentPeriodEnd)}。新购买的时长会接在现有有效期之后。`;
        badge.textContent = "已开通";
        document.querySelector("#membershipWorkspaceLink").hidden = false;
        trialDetail.textContent = `当前正在使用付费会员权益，创建案件不会扣减免费试验次数。免费试验记录：已使用 ${trial.used || 0} / ${trial.limit || 3} 次。`;
        trialBadge.textContent = "会员优先";
      } else if (trial.active) {
        title.textContent = trial.remaining > 0 ? "免费试验可用" : "免费试验次数已用完";
        detail.textContent = trial.remaining > 0
          ? `注册后 30 天内可创建 3 个免费试验案件；当前剩余 ${trial.remaining} 次。`
          : "3 次免费试验案件已经全部创建；到期前仍可查看和完善已有案件，创建新案件需购买会员。";
        badge.textContent = trial.remaining > 0 ? `剩余 ${trial.remaining} 次` : "0 次剩余";
        document.querySelector("#membershipWorkspaceLink").hidden = false;
        trialDetail.textContent = `已使用 ${trial.used || 0} / ${trial.limit || 3} 次，有效至 ${dateTime(trial.expiresAt)}。每创建一个新客户案件计为 1 次。`;
        trialBadge.textContent = trial.remaining > 0 ? `剩余 ${trial.remaining} 次` : "次数已用完";
        trialWorkspaceLink.hidden = false;
      } else {
        title.textContent = "当前未开通有效会员";
        detail.textContent = "30 天免费试用期已结束，请在下方选择月度或年度会员并购买。";
        badge.textContent = "未开通";
        trialDetail.textContent = `免费试验已结束；此前已使用 ${trial.used || 0} / ${trial.limit || 3} 次。购买会员后可继续创建和处理案件。`;
        trialBadge.textContent = "已结束";
      }
      if (!membership.active && !trial.active && !billing.gateway.configured) {
        showNotice(billing.gateway.message || "支付通道正在接入。", "warning");
      }
      syncCheckoutAvailability();
      checkoutButtons.forEach((button) => {
        button.addEventListener("click", async () => {
          if (!legalAcceptance?.checked || !global.WESTORY_LEGAL?.configured) {
            showNotice("请先阅读并同意服务条款、隐私政策和退款政策。", "warning");
            return;
          }
          button.disabled = true;
          const original = button.textContent;
          button.textContent = "正在创建真实订单…";
          try {
            const result = await request("/billing/checkout", {
              method: "POST", body: JSON.stringify({
                productId: button.dataset.productId,
                legalAcceptance: {
                  accepted: true,
                  termsVersion: global.WESTORY_LEGAL.termsVersion,
                },
              }),
            });
            if (!result.order?.checkoutUrl) throw new Error("支付网关没有返回收银台地址");
            global.location.assign(result.order.checkoutUrl);
          } catch (error) {
            showNotice(error.message, "error");
            button.textContent = original;
            syncCheckoutAvailability();
          }
        });
      });
      const params = new URLSearchParams(global.location.search);
      if (params.get("auth") === "registered") {
        showNotice("账号已创建，30 天内的 3 次免费试验已自动开通。", "success");
      } else if (params.get("auth") === "logged-in" || params.get("access") === "required") {
        if (!membership.active && !trial.active) {
          showNotice("当前账号尚未开通会员，请选择月付或年付方案。", "warning");
        }
      } else if (params.get("checkout") === "success") {
        showNotice("购买已提交。支付确认到账后，会员状态会在本页自动更新。", "success");
      } else if (params.get("checkout") === "cancelled") {
        showNotice("本次支付未完成，可以继续在本页重新选择方案。", "warning");
      }
    } catch (error) {
      title.textContent = "暂时无法读取会员状态";
      detail.textContent = error.message;
      badge.textContent = "连接失败";
      showNotice(error.message, "error");
    }
  }

  function renderConsole(billing) {
    const gateway = billing.gateway || {};
    document.querySelector("#paymentConsoleContent").hidden = false;
    document.querySelector("#billingGatewayBadge").textContent = gateway.configured ? "已接通" : "未接通";
    document.querySelector("#billingGatewayMessage").textContent = gateway.message || "支付通道状态未知";
    document.querySelector("#billingOrganizationCount").textContent = String(billing.totals?.organizations || 0);
    document.querySelector("#billingActiveMembershipCount").textContent = String(billing.totals?.activeMemberships || 0);
    document.querySelector("#billingOrderCount").textContent = String(billing.orders.length);
    document.querySelector("#billingRefundCount").textContent = String(billing.refunds.length);
    document.querySelector("#billingProductRows").innerHTML = billing.products.map((product) => `
      <tr><td><strong>${escapeHtml(product.name)}</strong><small>${escapeHtml(product.id)}</small></td><td>${money(product.amount, product.currency)}</td><td>${product.durationDays} 天</td><td>${product.active ? "可售" : "下架"}</td></tr>
    `).join("") || '<tr><td colspan="4">暂无商品</td></tr>';
    document.querySelector("#billingOrderRows").innerHTML = billing.orders.map((order) => `
      <tr><td><strong>${escapeHtml(order.organizationName || "未知机构")}</strong><small>${escapeHtml(order.userEmail || "")}</small></td><td><strong>${escapeHtml(order.id)}</strong><small>${escapeHtml(order.providerCheckoutId || "尚未生成网关单号")}</small></td><td>${money(order.amount, order.currency)}</td><td><span class="billing-status" data-status="${escapeHtml(order.status)}">${statusLabel(order.status)}</span></td><td>${dateTime(order.createdAt)}</td><td>${order.status === "paid" || order.status === "partially_refunded" ? `<button class="btn secondary billing-refund" type="button" data-order-id="${escapeHtml(order.id)}">申请退款</button>` : order.status === "pending" ? `<button class="btn secondary billing-refresh" type="button" data-order-id="${escapeHtml(order.id)}">向网关查单</button>` : "—"}</td></tr>
    `).join("") || '<tr><td colspan="6">暂无订单</td></tr>';
    document.querySelector("#billingRefundRows").innerHTML = billing.refunds.map((refund) => `
      <tr><td><strong>${escapeHtml(refund.organizationName || "未知机构")}</strong></td><td><strong>${escapeHtml(refund.id)}</strong><small>${escapeHtml(refund.providerRefundId || "处理中")}</small></td><td>${escapeHtml(refund.orderId)}</td><td>${money(refund.amount, refund.currency)}</td><td>${statusLabel(refund.status)}</td><td>${dateTime(refund.createdAt)}</td></tr>
    `).join("") || '<tr><td colspan="6">暂无退款记录</td></tr>';
    document.querySelectorAll(".billing-refund").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!global.confirm("确认向支付网关申请该订单的全额退款？")) return;
        button.disabled = true;
        button.textContent = "退款处理中…";
        try {
          await request(`/admin/billing/orders/${encodeURIComponent(button.dataset.orderId)}/refunds`, {
            method: "POST", body: JSON.stringify({ reason: "requested_by_customer" }),
          });
          await loadConsole();
          showNotice("退款请求已提交，最终结果以支付网关状态为准。", "success");
        } catch (error) {
          showNotice(error.message, "error");
          button.disabled = false;
          button.textContent = "申请退款";
        }
      });
    });
    document.querySelectorAll(".billing-refresh").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "查单中…";
        try {
          await request(`/admin/billing/orders/${encodeURIComponent(button.dataset.orderId)}/refresh`, {
            method: "POST", body: "{}",
          });
          await loadConsole();
          showNotice("已从支付网关刷新订单状态。", "success");
        } catch (error) {
          showNotice(error.message, "error");
          button.disabled = false;
          button.textContent = "向网关查单";
        }
      });
    });
  }

  async function loadConsole() {
    try {
      const session = await request("/session");
      if (!session.user) {
        showNotice("请先登录平台管理员账号。", "warning");
        document.querySelector("#billingGatewayBadge").textContent = "未登录";
        document.querySelector("#billingGatewayMessage").textContent = "支付后台不会向顾问账号开放。";
        return;
      }
      if (!session.user.platformAdmin) {
        showNotice("当前账号不是平台管理员，不能查看支付后台。", "error");
        document.querySelector("#billingGatewayBadge").textContent = "无权限";
        document.querySelector("#billingGatewayMessage").textContent = "顾问账号只能使用会员中心和工作台。";
        return;
      }
      const billing = await request("/admin/billing");
      renderConsole(billing);
      if (!billing.gateway.configured) showNotice(billing.gateway.message, "warning");
    } catch (error) {
      showNotice(error.message, "error");
    }
  }

  if (page === "membership") loadMembership();
  if (page === "console") loadConsole();
})(window);
