(() => {
  const stateNode = document.querySelector("#mam-spender-state");
  if (!stateNode) return;

  let state = JSON.parse(stateNode.textContent || "{}");
  let page = 1;
  let chartMode = "pie";
  let formDirty = false;
  const number = new Intl.NumberFormat();

  const one = (selector) => document.querySelector(selector);
  const all = (selector) => [...document.querySelectorAll(selector)];
  const formatNumber = (value, fallback = "N/A") =>
    value === null || value === undefined || value === ""
      ? fallback
      : number.format(Number(value));
  const formatTime = (value) => {
    if (!value) return "N/A";
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
  };
  const formatCountdown = (seconds) => {
    if (seconds === null || seconds === undefined) return "Paused";
    const safe = Math.max(0, Number(seconds));
    const days = Math.floor(safe / 86400);
    const hours = Math.floor((safe % 86400) / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const secs = Math.floor(safe % 60);
    return days
      ? `${days}d ${hours}h ${minutes}m`
      : `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };

  function setText(selector, value) {
    const target = one(selector);
    if (target) target.textContent = value;
  }

  function showNotice(message, kind = "success") {
    const notice = one("[data-spender-notice]");
    if (!notice) return;
    notice.textContent = message;
    notice.className = `spender-notice ${kind}`;
    notice.hidden = false;
    window.setTimeout(() => {
      notice.hidden = true;
    }, 7000);
  }

  async function api(path, body = {}) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      throw new Error(`The suite returned HTTP ${response.status} without JSON.`);
    }
    if (!response.ok) throw new Error(payload.detail || payload.error || "Request failed.");
    state = payload;
    render();
    return payload;
  }

  function renderStatus() {
    const status = one("[data-spender-status]");
    if (status) {
      status.textContent = state.automation_running
        ? "Running"
        : state.scheduler_enabled
          ? "Scheduled"
          : "Paused";
      status.className = `status ${state.automation_running ? "running" : state.scheduler_enabled ? "ok" : "waiting"}`;
    }
    setText("[data-spender-field='last_scan_points']", formatNumber(state.last_scan_points));
    setText(
      "[data-spender-field='points_per_hour']",
      state.points_per_min === null || state.points_per_min === undefined
        ? "N/A"
        : formatNumber(Math.round(Number(state.points_per_min) * 60)),
    );
    setText("[data-spender-field='cumulative_upload_gb']", `${formatNumber(state.totals?.cumulative_upload_gb, "0")} GiB`);
    setText("[data-spender-field='cumulative_freeleech_wedges']", formatNumber(state.totals?.cumulative_freeleech_wedges, "0"));
    setText("[data-spender-field='cumulative_points_spent']", formatNumber(state.totals?.cumulative_points_spent, "0"));
    setText("[data-spender-field='cumulative_vip_purchases']", formatNumber(state.totals?.cumulative_vip_purchases, "0"));
    setText("[data-spender-field='cumulative_freeleech_points_spent']", formatNumber(state.totals?.cumulative_freeleech_points_spent, "0"));
    setText("[data-spender-field='next_run']", state.scheduler_enabled ? formatCountdown(state.next_run_seconds) : "Paused");
    ["username", "vip_expires", "uploaded", "downloaded", "ratio"].forEach((key) => {
      setText(`[data-spender-field='${key}']`, state.user?.[key] || "N/A");
    });
    const mode = state.settings?.fl_only
      ? "Wedge-only"
      : state.settings?.alternate_fl_upload
        ? `Alternating · next ${String(state.settings.alternate_next_purchase || "").replaceAll("_", " ")}`
        : state.settings?.buy_upload_credit
          ? "Upload credit enabled"
          : "Purchases limited to VIP renewal";
    setText("[data-spender-mode-summary]", `Current mode: ${mode}`);
    setText("[data-spender-alternate]", `Next alternating target: ${String(state.settings?.alternate_next_purchase || "freeleech_wedge").replaceAll("_", " ")}.`);
  }

  function renderLog() {
    const target = one("[data-spender-log]");
    if (!target) return;
    const rows = state.logs || [];
    target.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.textContent = "No MAM-Spender activity yet.";
      target.append(empty);
      return;
    }
    rows.forEach((row) => {
      const line = document.createElement("p");
      line.className = row.level || "info";
      const time = document.createElement("time");
      time.textContent = formatTime(row.created_at);
      const message = document.createElement("span");
      message.textContent = row.message || "";
      line.append(time, " ", message);
      if (row.context && Object.keys(row.context).length) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "context";
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(row.context, null, 2);
        details.append(summary, pre);
        line.append(details);
      }
      target.append(line);
    });
    target.scrollTop = target.scrollHeight;
  }

  function row(cells) {
    const item = document.createElement("tr");
    cells.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      item.append(cell);
    });
    return item;
  }

  function emptyRow(columns, message) {
    const item = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = columns;
    cell.textContent = message;
    item.append(cell);
    return item;
  }

  function renderHistory() {
    const target = one("[data-spender-history]");
    if (target) {
      const history = state.history || [];
      target.replaceChildren(
        ...(history.length
          ? history.map((entry) => row([
            formatTime(entry.started_at || entry.created_at),
            entry.result || entry.kind || "N/A",
            formatNumber(entry.points_spent, "0"),
            entry.upload_gb ? `${formatNumber(entry.upload_gb)} GiB` : "—",
            entry.freeleech_wedges ? formatNumber(entry.freeleech_wedges) : "—",
            entry.vip_purchased ? "Yes" : "—",
          ]))
          : [emptyRow(6, "No automation history yet.")]),
      );
    }
    renderBonusHistory();
  }

  function renderBonusHistory() {
    const target = one("[data-spender-bonus-history]");
    if (!target) return;
    const history = state.bonus_history || [];
    const pageSize = Number(one("[data-spender-page-size]")?.value || 10);
    const pages = Math.max(1, Math.ceil(history.length / pageSize));
    page = Math.max(1, Math.min(page, pages));
    const start = (page - 1) * pageSize;
    const visible = history.slice(start, start + pageSize);
    target.replaceChildren(
      ...(visible.length
        ? visible.map((entry) => row([
          formatTime(entry.timestamp),
          entry.type || "N/A",
          formatNumber(entry.amount, "0"),
          entry.title || "N/A",
          entry.other_name || "N/A",
        ]))
        : [emptyRow(5, "No MaM bonus history loaded yet.")]),
    );
    setText(
      "[data-spender-page-status]",
      history.length
        ? `Showing ${start + 1}–${start + visible.length} of ${history.length} · page ${page} of ${pages}`
        : "Page 0 of 0",
    );
    const previous = one("[data-spender-prev]");
    const next = one("[data-spender-next]");
    if (previous) previous.disabled = page <= 1;
    if (next) next.disabled = page >= pages || !history.length;
    setText(
      "[data-spender-bonus-status]",
      state.bonus_history_error
        ? `Refresh failed: ${state.bonus_history_error}`
        : state.bonus_history_fetched_at
          ? `Last refreshed ${formatTime(state.bonus_history_fetched_at)} · ${history.length} cached records.`
          : "Up to 500 returned point and wedge records.",
    );
  }

  function renderMamData() {
    const data = state.mam_user_data || {};
    all("[data-mam-field]").forEach((element) => {
      element.textContent = data[element.dataset.mamField] || "N/A";
    });
    setText(
      "[data-spender-account-status]",
      state.mam_user_error
        ? `Refresh failed: ${state.mam_user_error}`
        : state.mam_user_fetched_at
          ? `Last refreshed ${formatTime(state.mam_user_fetched_at)}.`
          : "Refresh to load the latest account snapshot.",
    );
    const target = one("[data-spender-notifications]");
    if (target) {
      target.replaceChildren();
      (data.notifications || []).forEach((message) => {
        const item = document.createElement("p");
        item.textContent = message;
        target.append(item);
      });
    }
  }

  function renderEvents() {
    const target = one("[data-spender-events]");
    if (!target) return;
    const events = [...(state.spend_events || [])].reverse();
    target.replaceChildren(
      ...(events.length
        ? events.map((event) => row([
          formatTime(event.created_at),
          event.label || event.category || "N/A",
          formatNumber(event.points_spent, "0"),
          event.units ? `${formatNumber(event.units)} ${event.unit_label || ""}` : "—",
          formatNumber(event.balance_after),
        ]))
        : [emptyRow(5, "No confirmed spending events yet.")]),
    );
  }

  const categoryLabel = {
    upload_credit: "Upload Credit",
    freeleech_wedge: "Freeleech Wedge",
    vip: "VIP Renewal",
  };
  const categoryColor = {
    upload_credit: "#62ff96",
    freeleech_wedge: "#d7ff64",
    vip: "#63d8ff",
  };

  function drawChart() {
    const canvas = one("[data-spender-chart]");
    if (!canvas) return;
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    const events = (state.spend_events || []).filter((event) => Number(event.points_spent || 0) > 0);
    const totals = {};
    events.forEach((event) => {
      const category = event.category || "other";
      totals[category] = (totals[category] || 0) + Number(event.points_spent || 0);
    });
    const entries = Object.entries(totals);
    if (!entries.length) {
      context.fillStyle = "#69a77a";
      context.font = "22px ui-monospace, monospace";
      context.fillText("No confirmed spending data yet.", 48, height / 2);
      return;
    }
    if (chartMode === "pie") drawPie(context, entries, width, height);
    if (chartMode === "bar") drawBars(context, entries, width, height);
    if (chartMode === "timeline") drawTimeline(context, events, width, height);
    setText("[data-spender-chart-title]", chartMode === "timeline" ? "Cumulative spending timeline" : chartMode === "bar" ? "Spending by category" : "Spending share by category");
    const legend = one("[data-spender-chart-legend]");
    if (legend) {
      legend.replaceChildren();
      entries.forEach(([category, points]) => {
        const item = document.createElement("span");
        item.style.setProperty("--legend", categoryColor[category] || "#a7c7af");
        item.textContent = `${categoryLabel[category] || category}: ${formatNumber(points)} pts`;
        legend.append(item);
      });
    }
  }

  function drawPie(context, entries, width, height) {
    const total = entries.reduce((sum, [, value]) => sum + value, 0);
    const centerX = width * 0.36;
    const centerY = height / 2;
    const radius = Math.min(height * 0.37, width * 0.2);
    let angle = -Math.PI / 2;
    entries.forEach(([category, points]) => {
      const next = angle + (points / total) * Math.PI * 2;
      context.beginPath();
      context.moveTo(centerX, centerY);
      context.arc(centerX, centerY, radius, angle, next);
      context.closePath();
      context.fillStyle = categoryColor[category] || "#a7c7af";
      context.fill();
      angle = next;
    });
    context.beginPath();
    context.arc(centerX, centerY, radius * 0.55, 0, Math.PI * 2);
    context.fillStyle = "#06100a";
    context.fill();
    context.fillStyle = "#dbffe5";
    context.font = "bold 24px ui-monospace, monospace";
    context.textAlign = "center";
    context.fillText(formatNumber(total), centerX, centerY);
    context.font = "15px ui-monospace, monospace";
    context.fillStyle = "#69a77a";
    context.fillText("points", centerX, centerY + 25);
    context.textAlign = "start";
  }

  function drawBars(context, entries, width, height) {
    const max = Math.max(...entries.map(([, value]) => value), 1);
    entries.forEach(([category, points], index) => {
      const y = 65 + index * 100;
      const barWidth = (points / max) * (width - 380);
      context.fillStyle = categoryColor[category] || "#a7c7af";
      context.fillRect(230, y, barWidth, 44);
      context.fillStyle = "#dbffe5";
      context.font = "18px ui-monospace, monospace";
      context.fillText(categoryLabel[category] || category, 35, y + 28);
      context.fillText(`${formatNumber(points)} pts`, 245 + barWidth, y + 28);
    });
  }

  function drawTimeline(context, events, width, height) {
    const ordered = [...events].sort((left, right) => new Date(left.created_at) - new Date(right.created_at));
    let cumulative = 0;
    const points = ordered.map((event) => ({
      value: (cumulative += Number(event.points_spent || 0)),
      category: event.category,
    }));
    const max = Math.max(...points.map((point) => point.value), 1);
    const left = 70;
    const top = 45;
    const right = width - 45;
    const bottom = height - 60;
    context.strokeStyle = "rgba(98,255,150,.25)";
    context.strokeRect(left, top, right - left, bottom - top);
    context.beginPath();
    points.forEach((point, index) => {
      const x = left + (points.length === 1 ? 0.5 : index / (points.length - 1)) * (right - left);
      const y = bottom - (point.value / max) * (bottom - top);
      if (!index) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.strokeStyle = "#62ff96";
    context.lineWidth = 4;
    context.stroke();
    context.fillStyle = "#dbffe5";
    context.font = "17px ui-monospace, monospace";
    context.fillText(`${formatNumber(max)} cumulative points`, left, 28);
  }

  function render() {
    document.body.dataset.spenderTheme = state.settings?.theme || "ember";
    renderStatus();
    renderLog();
    renderHistory();
    renderMamData();
    renderEvents();
    drawChart();
  }

  function nextWeekdayUtc(day, hour) {
    const current = new Date();
    const result = new Date(current);
    result.setUTCMinutes(0, 0, 0);
    result.setUTCHours(hour);
    result.setUTCDate(result.getUTCDate() + ((day - result.getUTCDay() + 7) % 7));
    if (result <= current) result.setUTCDate(result.getUTCDate() + 7);
    return result;
  }

  function renderMarquee() {
    const target = one("[data-spender-marquee]");
    if (!target) return;
    const current = new Date();
    const vault = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate() + 1));
    const lottoReset = nextWeekdayUtc(1, 0);
    const lottoDrawing = nextWeekdayUtc(1, 9);
    const until = (date) => formatCountdown(Math.floor((date - Date.now()) / 1000));
    target.textContent = [
      `LOCAL ${current.toLocaleString()}`,
      `MAM UTC ${current.toLocaleString([], { timeZone: "UTC", timeZoneName: "short" })}`,
      `VAULT RESET ${until(vault)}`,
      `LOTTO RESET ${until(lottoReset)}`,
      `LOTTO DRAW ${until(lottoDrawing)}`,
    ].join("  //  ");
  }

  all("[data-spender-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.spenderAction;
      const paths = {
        start: "start",
        pause: "pause",
        run: "run",
        "run-fl": "run",
        "refresh-account": "refresh-account",
        "refresh-bonus-history": "refresh-bonus-history",
      };
      button.disabled = true;
      try {
        await api(`/api/mam-spender/${paths[action]}`, {
          fl_only_override: action === "run-fl",
        });
        showNotice(action.startsWith("refresh") ? "Fresh MaM data loaded." : "MAM-Spender action accepted.");
      } catch (error) {
        showNotice(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  });

  const settings = one("[data-spender-settings]");
  if (settings) {
    settings.addEventListener("input", () => { formDirty = true; });
    settings.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(settings);
      const body = {
        buy_vip: data.has("buy_vip"),
        buy_upload_credit: data.has("buy_upload_credit"),
        fl_only: data.has("fl_only"),
        alternate_fl_upload: data.has("alternate_fl_upload"),
        theme: data.get("theme"),
        points_buffer: Number(data.get("points_buffer")),
        next_run_delay_minutes: Number(data.get("next_run_delay_minutes")),
      };
      try {
        await api("/api/mam-spender/settings", body);
        formDirty = false;
        showNotice("Purchase settings saved and applied immediately.");
      } catch (error) {
        showNotice(error.message, "error");
      }
    });
  }

  one("[data-spender-reset]")?.addEventListener("click", async () => {
    if (!window.confirm("Reset cumulative MAM-Spender totals? Purchase history remains intact.")) return;
    try {
      await api("/api/mam-spender/reset-totals");
      showNotice("Cumulative totals reset. History was preserved.");
    } catch (error) {
      showNotice(error.message, "error");
    }
  });

  one("[data-spender-save-session]")?.addEventListener("click", async () => {
    const input = one("[data-spender-session]");
    if (!input?.value.trim()) return showNotice("Paste a Session_ID or cookie export first.", "error");
    try {
      await api("/api/mam-spender/session", { value: input.value });
      input.value = "";
      showNotice("Shared MaM API session saved. The cookie was not echoed back.");
    } catch (error) {
      showNotice(error.message, "error");
    }
  });

  one("[data-spender-session-file]")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    const input = one("[data-spender-session]");
    if (!file || !input) return;
    try {
      input.value = await file.text();
      showNotice(`Loaded ${file.name}. Review it, then save the Session_ID.`);
    } catch {
      showNotice(`Could not read ${file.name}.`, "error");
    }
  });

  one("[data-spender-import-button]")?.addEventListener("click", async () => {
    const input = one("[data-spender-import]");
    if (!input?.value.trim()) return showNotice("Paste the old config.json first.", "error");
    if (!window.confirm("Import standalone MAM-Spender settings and history into this module?")) return;
    try {
      await api("/api/mam-spender/import", { config: input.value });
      input.value = "";
      showNotice("Web Edition data imported.");
    } catch (error) {
      showNotice(error.message, "error");
    }
  });

  one("[data-spender-page-size]")?.addEventListener("change", () => {
    page = 1;
    renderBonusHistory();
  });
  one("[data-spender-prev]")?.addEventListener("click", () => {
    page -= 1;
    renderBonusHistory();
  });
  one("[data-spender-next]")?.addEventListener("click", () => {
    page += 1;
    renderBonusHistory();
  });
  all("[data-chart-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      chartMode = button.dataset.chartMode;
      all("[data-chart-mode]").forEach((item) => item.classList.toggle("active", item === button));
      drawChart();
    });
  });

  async function refreshState() {
    if (formDirty) return;
    try {
      const response = await fetch("/api/mam-spender/state", { cache: "no-store" });
      if (!response.ok) return;
      state = await response.json();
      render();
    } catch {
      // Keep the most recent state visible while the server reconnects.
    }
  }

  render();
  renderMarquee();
  window.setInterval(renderMarquee, 1000);
  window.setInterval(refreshState, 2000);
})();
