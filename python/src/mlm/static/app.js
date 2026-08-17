const menuButton = document.querySelector("[data-menu-toggle]");

if (menuButton) {
  menuButton.addEventListener("click", () => {
    const open = document.body.classList.toggle("nav-open");
    menuButton.setAttribute("aria-expanded", String(open));
  });
}

document.querySelectorAll(".nav-link").forEach((link) => {
  link.addEventListener("click", () => document.body.classList.remove("nav-open"));
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm || "Continue?")) {
      event.preventDefault();
    }
  });
});

function localizeTimes(root = document) {
  root.querySelectorAll("[data-local-time]").forEach((element) => {
    const value = element.getAttribute("data-local-time");
    if (!value) return;
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.valueOf())) {
      element.textContent = parsed.toLocaleString();
      element.title = value;
    }
  });
}

localizeTimes();

const jobMonitor = document.querySelector("[data-job-monitor]");
if (jobMonitor) {
  const focus = jobMonitor.dataset.focusJob || "";
  const title = jobMonitor.querySelector("[data-job-title]");
  const summary = jobMonitor.querySelector("[data-job-summary]");
  const state = jobMonitor.querySelector("[data-job-state]");
  const eventList = jobMonitor.querySelector("[data-job-events]");
  const eventCount = jobMonitor.querySelector("[data-job-event-count]");
  const progressBar = jobMonitor.querySelector("[data-job-progress-bar]");

  const matchingJobs = (jobs) => {
    const entries = Object.entries(jobs);
    if (focus) {
      const matches = entries.filter(
        ([name]) => name === focus || name.startsWith(`${focus}:`),
      );
      if (matches.length) return matches;
    }
    return entries.filter(([, value]) => value.running);
  };

  const eventNode = (jobName, event) => {
    const item = document.createElement("li");
    item.className = event.level || "info";

    const line = document.createElement("div");
    const time = document.createElement("time");
    time.dataset.localTime = event.created_at || "";
    time.textContent = event.created_at || "";
    const job = document.createElement("code");
    job.textContent = jobName;
    const message = document.createElement("span");
    message.textContent = event.message || "Working";
    line.append(time, job, message);
    item.append(line);

    if (event.context && Object.keys(event.context).length) {
      const details = document.createElement("details");
      const detailsSummary = document.createElement("summary");
      detailsSummary.textContent = "details";
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(event.context, null, 2);
      details.append(detailsSummary, pre);
      item.append(details);
    }
    return item;
  };

  const refreshJobs = async () => {
    try {
      const response = await fetch("/api/jobs", {
        headers: { "X-HeavyMLM-Refresh": "jobs" },
        cache: "no-store",
      });
      if (!response.ok) return;
      const payload = await response.json();
      const matches = matchingJobs(payload.jobs || {});
      if (!matches.length) return;

      jobMonitor.hidden = false;
      const running = matches.some(([, value]) => value.running);
      const failed = matches.find(
        ([, value]) => value.last_error || Number(value.last_result?.failed || 0) > 0,
      );
      const events = matches
        .flatMap(([name, value]) =>
          (value.progress || []).map((event) => ({ name, event })),
        )
        .sort((left, right) =>
          String(left.event.created_at).localeCompare(
            String(right.event.created_at),
          ),
        )
        .slice(-40);

      title.textContent =
        matches.length === 1
          ? matches[0][0]
          : `${focus || "Background"} · ${matches.length} workers`;
      state.textContent = failed ? "Error" : running ? "Running" : "Complete";
      state.className = `status ${failed ? "error" : running ? "running" : "ok"}`;
      const latest = events.at(-1)?.event;
      const latestFailure = failed?.[1].last_result?.failures?.at(-1);
      summary.textContent = failed
        ? failed[1].last_error || latestFailure?.error || "Job failed; open Errors for details"
        : latest?.message || (running ? "Working…" : "Job complete");

      const progressEvent = [...events]
        .reverse()
        .find(({ event }) => event.context?.current && event.context?.total);
      const current = Number(progressEvent?.event.context.current || 0);
      const total = Number(progressEvent?.event.context.total || 0);
      progressBar.style.width = running
        ? `${total ? Math.min(100, (current * 100) / total) : 12}%`
        : "100%";

      eventList.replaceChildren(
        ...events.map(({ name, event }) => eventNode(name, event)),
      );
      eventCount.textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
      localizeTimes(eventList);
    } catch {
      summary.textContent = "Live updates temporarily unavailable; the job is still running.";
    }
  };

  refreshJobs();
  window.setInterval(refreshJobs, 1000);
}

const liveDiagnostics = document.querySelector("[data-live-diagnostics]");
if (liveDiagnostics) {
  const refreshDiagnostics = async () => {
    if (document.querySelector(".activity-console details[open]")) return;
    try {
      const response = await fetch(window.location.href, {
        headers: { "X-HeavyMLM-Refresh": "diagnostics" },
      });
      if (!response.ok) return;
      const nextPage = new DOMParser().parseFromString(await response.text(), "text/html");
      [".debug-grid", ".activity-console"].forEach((selector) => {
        const current = document.querySelector(selector);
        const replacement = nextPage.querySelector(selector);
        if (current && replacement) {
          current.innerHTML = replacement.innerHTML;
          localizeTimes(current);
        }
      });
    } catch {
      // Keep the current diagnostic snapshot visible if a refresh is unavailable.
    }
  };
  window.setInterval(refreshDiagnostics, 5000);
}
