const $ = (id) => document.getElementById(id);
const API_ROOT = "/api/absidekick";
const REVIEW_PROVIDERS = [
  "audible",
  "google",
  "openlibrary",
  "itunes",
  "audible.ca",
  "audible.uk",
  "audible.au",
  "audible.fr",
  "audible.de",
  "audible.jp",
  "audible.it",
  "audible.in",
  "audible.es",
  "fantlab",
];
const MATCH_POLICY_PRESETS = {
  balanced: {
    threshold: 80,
    reviewFloor: 65,
    minimumTitleScore: 86,
    minimumAuthorScore: 78,
    minimumWinnerMargin: 6,
    minimumStrongSignals: 2,
    strictAutoMatch: true,
  },
  cautious: {
    threshold: 90,
    reviewFloor: 70,
    minimumTitleScore: 92,
    minimumAuthorScore: 85,
    minimumWinnerMargin: 8,
    minimumStrongSignals: 2,
    strictAutoMatch: true,
  },
  flexible: {
    threshold: 80,
    reviewFloor: 60,
    minimumTitleScore: 84,
    minimumAuthorScore: 72,
    minimumWinnerMargin: 3,
    minimumStrongSignals: 1,
    strictAutoMatch: true,
  },
};
const MATCH_POLICY_FIELDS = [
  "threshold",
  "reviewFloor",
  "minimumTitleScore",
  "minimumAuthorScore",
  "minimumWinnerMargin",
  "minimumStrongSignals",
];

let appState = {
  settings: null,
  libraries: [],
  filterData: null,
  job: null,
  poll: null,
  reviewRows: [],
  logs: [],
  logPage: 1,
  activity: {
    sequence: 0,
    activeId: 0,
    startedAt: 0,
    timer: null,
    poll: null,
    status: "idle",
  },
};

const ACTION_CANCELLED = Symbol("action-cancelled");

const checkboxIds = [
  "rememberConnection",
  "missingMetadataOnly",
  "missingCoverOnly",
  "noAsinOnly",
  "noIsbnOnly",
  "skipMissingItems",
  "skipInvalidItems",
  "overwriteMetadata",
  "repairSeries",
  "adaptiveSearch",
  "automaticFallbackProviders",
  "strictAutoMatch",
  "quickMatchFirstResultOnly",
  "requireAuthor",
  "requireTitleToken",
  "useEmbeddedFileMetadata",
  "sortDesc",
  "tagMatched",
  "tagUnmatched",
  "tagReview",
  "clearUnmatchedOnMatch",
  "clearReviewOnMatch",
  "clearMatchedOnUnmatched",
  "dryRun",
  "stopOnError",
  "rejectAddsUnmatchedTag",
  "rejectClearsReviewTag",
];

function showToast(message, timeout = 3200) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), timeout);
}

function elapsedLabel(startedAt) {
  if (!startedAt) return "Idle";
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = String(elapsedSeconds % 60).padStart(2, "0");
  return minutes ? `${minutes}m ${seconds}s elapsed` : `${elapsedSeconds}s elapsed`;
}

function renderActivity(activity) {
  const panel = $("activityPanel");
  const progress = $("activityProgress");
  const status = activity.status || "idle";
  const current = Number(activity.current || 0);
  const total = Number(activity.total || 0);
  const startedAt = activity.startedAt
    ? typeof activity.startedAt === "number"
      ? activity.startedAt
      : Date.parse(activity.startedAt)
    : appState.activity.startedAt;

  panel.className = `activity-panel ${status}`;
  panel.setAttribute("aria-busy", status === "running" ? "true" : "false");
  $("activityTitle").textContent = activity.title || "Ready";
  $("activityDetail").textContent = activity.detail || "No ABSidekick operation is currently running.";
  $("activityCount").textContent = total > 0 ? `${Math.min(current, total)} / ${total}` : status === "running" ? "Working…" : "—";
  $("activityElapsed").textContent = startedAt
    ? status === "running"
      ? elapsedLabel(startedAt)
      : `${elapsedLabel(startedAt).replace(" elapsed", "")} total`
    : status === "success"
      ? "Completed"
      : status === "error"
        ? "Stopped with error"
        : "Idle";

  if (status === "running" && total <= 0) {
    progress.removeAttribute("value");
    progress.max = 1;
  } else {
    progress.max = Math.max(1, total);
    progress.value = total > 0 ? Math.min(current, total) : status === "success" ? 1 : 0;
  }
}

function setButtonBusy(button, busy, busyText = "Working…") {
  if (!button) return;
  if (busy) {
    button.dataset.activityLabel = button.textContent;
    button.dataset.activityWasDisabled = button.disabled ? "true" : "false";
    button.disabled = true;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    button.textContent = busyText;
    return;
  }
  button.disabled = button.dataset.activityWasDisabled === "true";
  button.classList.remove("is-busy");
  button.removeAttribute("aria-busy");
  if (button.dataset.activityLabel) button.textContent = button.dataset.activityLabel;
  delete button.dataset.activityLabel;
  delete button.dataset.activityWasDisabled;
}

function beginVisibleActivity(button, options) {
  const activity = appState.activity;
  activity.sequence += 1;
  activity.activeId = activity.sequence;
  activity.startedAt = Date.now();
  activity.status = "running";
  activity.source = "action";
  window.clearInterval(activity.timer);
  window.clearInterval(activity.poll);
  setButtonBusy(button, true, options.busyText);
  renderActivity({
    status: "running",
    title: options.title,
    detail: options.detail,
    startedAt: activity.startedAt,
  });
  activity.timer = window.setInterval(() => {
    if (activity.status === "running") {
      $("activityElapsed").textContent = elapsedLabel(activity.startedAt);
    }
  }, 500);
  return activity.activeId;
}

async function refreshBackendActivity(activityId) {
  if (appState.activity.activeId !== activityId) return;
  try {
    const payload = await api("/api/activity");
    const activity = payload.activity || {};
    if (activity.status !== "running") {
      if (appState.activity.source === "server") {
        appState.activity.status = activity.status || "idle";
        window.clearInterval(appState.activity.timer);
        window.clearInterval(appState.activity.poll);
        renderActivity(activity);
      }
      return;
    }
    appState.activity.status = "running";
    renderActivity(activity);
  } catch (_error) {
    // The initiating request still owns final success/error handling.
  }
}

function resumeBackendActivity(activity) {
  if (!activity || !activity.status || activity.status === "idle") return;
  if (appState.activity.status === "running" && appState.activity.source !== "server") return;
  appState.activity.sequence += 1;
  appState.activity.activeId = appState.activity.sequence;
  appState.activity.source = "server";
  appState.activity.status = activity.status;
  appState.activity.startedAt = Date.parse(activity.startedAt || "") || Date.now();
  renderActivity(activity);
  if (activity.status !== "running") return;
  const activityId = appState.activity.activeId;
  window.clearInterval(appState.activity.timer);
  window.clearInterval(appState.activity.poll);
  appState.activity.timer = window.setInterval(() => {
    $("activityElapsed").textContent = elapsedLabel(appState.activity.startedAt);
  }, 500);
  appState.activity.poll = window.setInterval(() => refreshBackendActivity(activityId), 450);
}

function finishVisibleActivity(activityId, status, title, detail) {
  if (appState.activity.activeId !== activityId) return;
  appState.activity.status = status;
  window.clearInterval(appState.activity.timer);
  window.clearInterval(appState.activity.poll);
  renderActivity({
    status,
    title,
    detail,
    startedAt: appState.activity.startedAt,
    current: 0,
    total: 0,
  });
}

async function runVisibleAction(button, options, action) {
  const activityId = beginVisibleActivity(button, options);
  if (options.pollBackend) {
    appState.activity.poll = window.setInterval(() => refreshBackendActivity(activityId), 450);
    window.setTimeout(() => refreshBackendActivity(activityId), 80);
  }
  try {
    const result = await action();
    if (result === ACTION_CANCELLED) {
      finishVisibleActivity(activityId, "idle", "Action cancelled", "Nothing was changed.");
      return result;
    }
    const successDetail = typeof options.success === "function" ? options.success(result) : options.success;
    finishVisibleActivity(activityId, "success", options.successTitle || `${options.title} complete`, successDetail || "Operation completed successfully.");
    return result;
  } catch (error) {
    finishVisibleActivity(activityId, "error", `${options.title} failed`, error.message);
    showToast(error.message, 5200);
    return null;
  } finally {
    setButtonBusy(button, false);
  }
}

function reportInstantActivity(title, detail) {
  if (appState.activity.status === "running") return;
  appState.activity.sequence += 1;
  appState.activity.activeId = appState.activity.sequence;
  appState.activity.startedAt = Date.now();
  appState.activity.source = "instant";
  finishVisibleActivity(appState.activity.activeId, "success", title, detail);
}

async function api(path, options = {}) {
  const resolvedPath = path.startsWith("/api/") ? `${API_ROOT}${path.slice(4)}` : path;
  const response = await fetch(resolvedPath, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function csvToArray(value) {
  return String(value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function arrayToCsv(values) {
  return (values || []).join(", ");
}

function selectedValues(select) {
  return Array.from(select.selectedOptions).map((option) => option.value);
}

function setSelectedValues(select, values) {
  const selected = new Set(values || []);
  Array.from(select.options).forEach((option) => {
    option.selected = selected.has(option.value);
  });
}

function matchingPolicyValues() {
  return {
    threshold: Number($("threshold").value || 80),
    reviewFloor: Number($("reviewFloor").value || 65),
    minimumTitleScore: Number($("minimumTitleScore").value || 86),
    minimumAuthorScore: Number($("minimumAuthorScore").value || 78),
    minimumWinnerMargin: Number($("minimumWinnerMargin").value || 6),
    minimumStrongSignals: Number($("minimumStrongSignals").value || 2),
    strictAutoMatch: $("strictAutoMatch").checked,
  };
}

function detectMatchingPolicyPreset() {
  const current = matchingPolicyValues();
  return Object.entries(MATCH_POLICY_PRESETS).find(([, preset]) =>
    Object.entries(preset).every(([key, value]) => current[key] === value),
  )?.[0] || "custom";
}

function renderPolicySummary() {
  const values = matchingPolicyValues();
  const preset = detectMatchingPolicyPreset();
  $("matchPolicyPreset").value = preset;
  const scoreRange = `Similarity ${values.threshold}+ can auto-match; scores from ${values.reviewFloor} up to just under ${values.threshold} go to Review.`;
  $("policySummary").innerHTML = values.strictAutoMatch
    ? `<strong>${preset === "custom" ? "Custom safety policy" : `${$("matchPolicyPreset").selectedOptions[0].textContent} policy`}</strong><span>${scoreRange} Automatic approval also requires title ${values.minimumTitleScore}+, author ${values.minimumAuthorScore}+ when both sides provide one, ${values.minimumStrongSignals} independent signal${values.minimumStrongSignals === 1 ? "" : "s"}, a ${values.minimumWinnerMargin}-point lead over a meaningfully different result, and no work-identity conflicts. With an exact ASIN or ISBN, a different secondary identifier or stored duration is a non-blocking edition note. Duplicate listings of the same work do not count as competitors.</span><span class="policy-google">If ABS does not pass all of that, a tested Google key is searched second and Open Library is searched third. The full provider path appears in the log.</span>`
    : `<strong>Similarity-only approval</strong><span>${scoreRange} The title, author, signal, margin, and conflict safety checks are displayed but do not block automatic approval. This is less accurate.</span><span class="policy-google">If ABS does not reach the score, Google is searched second and Open Library third.</span>`;
}

function applyMatchingPolicyPreset(name) {
  const preset = MATCH_POLICY_PRESETS[name];
  if (!preset) {
    renderPolicySummary();
    return;
  }
  MATCH_POLICY_FIELDS.forEach((id) => {
    $(id).value = preset[id];
  });
  $("strictAutoMatch").checked = preset.strictAutoMatch;
  renderPolicySummary();
}

function normalizeLibraryList(payload) {
  const raw = payload?.libraries;
  if (Array.isArray(raw)) return raw;
  if (Array.isArray(raw?.libraries)) return raw.libraries;
  if (Array.isArray(raw?.items)) return raw.items;
  if (Array.isArray(raw?.results)) return raw.results;
  return [];
}

function populateLibraries(libraries) {
  appState.libraries = libraries || [];
  const select = $("libraryId");
  const current = select.value || appState.settings?.connection?.libraryId || "";
  select.innerHTML = '<option value="">Select a library</option>';
  appState.libraries.forEach((library) => {
    const option = document.createElement("option");
    option.value = library.id;
    option.textContent = `${library.name || library.id}${library.mediaType ? ` (${library.mediaType})` : ""}`;
    select.appendChild(option);
  });
  if (current) select.value = current;
}

function populateFilterData(filterData) {
  appState.filterData = filterData || {};
  const authors = filterData?.authors || [];
  ["includeAuthors", "excludeAuthors"].forEach((id) => {
    const select = $(id);
    const current = selectedValues(select);
    select.innerHTML = "";
    authors.forEach((author) => {
      const option = document.createElement("option");
      option.value = author.name || author.id;
      option.textContent = author.name || author.id;
      select.appendChild(option);
    });
    setSelectedValues(select, current);
  });
  if (filterData?.tags?.length) {
    $("connectionNote").textContent = `Loaded ${authors.length} author(s), ${filterData.tags.length} tag(s), ${filterData.series?.length || 0} series.`;
  } else {
    $("connectionNote").textContent = `Loaded ${authors.length} author(s). No tags returned for this library yet.`;
  }
}

function setForm(settings) {
  appState.settings = settings;
  const connection = settings.connection || {};
  const providers = settings.providers || {};
  const run = settings.run || {};
  const targeting = settings.targeting || {};
  const matching = settings.matching || {};
  const weights = settings.weights || {};
  const tags = settings.tags || {};
  const review = settings.review || {};

  $("baseUrl").value = connection.baseUrl || "";
  $("provider").value = connection.provider || "audible";
  $("rememberConnection").checked = connection.rememberConnection !== false;
  $("libraryId").value = connection.libraryId || "";
  const hasToken = Boolean(connection.hasToken);
  const tokenSaved = hasToken && connection.rememberConnection !== false;
  const tokenStatus = $("absTokenStatus");
  tokenStatus.className = `provider-state ${tokenSaved ? "ready" : hasToken ? "untested" : "missing"}`;
  tokenStatus.textContent = tokenSaved
    ? "API token saved"
    : hasToken
      ? "Token available this run only"
      : "Token not saved";
  $("token").value = "";
  $("token").placeholder = hasToken
    ? "Stored token is hidden — paste only to replace it"
    : "Paste token once; it will be saved privately";
  $("connectionNote").textContent = tokenSaved
    ? "The saved API token is loaded on the server. The blank token field is intentional; you do not need to enter it again."
    : hasToken
      ? "This token is available only until MyAnonaSuite restarts. Enable saving to keep it."
      : "Paste the Audiobookshelf API token and connect. Saving is enabled by default and the stored token is never returned to the browser.";
  $("googleBooksApiKey").value = "";
  renderGoogleBooksStatus(providers);
  $("openLibraryEnabled").checked = providers.openLibraryEnabled !== false;
  $("openLibraryContactEmail").value = providers.openLibraryContactEmail || "";
  renderOpenLibraryStatus();

  $("targetMode").value = targeting.mode || "unprocessed";
  $("titleContains").value = targeting.titleContains || "";
  $("pathContains").value = targeting.pathContains || "";
  $("includeTags").value = arrayToCsv(targeting.includeTags);
  $("excludeTags").value = arrayToCsv(targeting.excludeTags);
  $("includeTagMode").value = targeting.includeTagMode || "any";
  setSelectedValues($("includeAuthors"), targeting.includeAuthors || []);
  setSelectedValues($("excludeAuthors"), targeting.excludeAuthors || []);

  $("threshold").value = matching.threshold ?? 80;
  $("reviewFloor").value = matching.reviewFloor ?? 65;
  $("candidateLimit").value = matching.candidateLimit ?? 8;
  $("durationToleranceMinutes").value = matching.durationToleranceMinutes ?? 7;
  $("minimumTitleScore").value = matching.minimumTitleScore ?? 86;
  $("minimumAuthorScore").value = matching.minimumAuthorScore ?? 78;
  $("minimumWinnerMargin").value = matching.minimumWinnerMargin ?? 6;
  $("minimumStrongSignals").value = matching.minimumStrongSignals ?? 2;
  $("fallbackProviders").value = arrayToCsv(
    (matching.fallbackProviders || []).filter(
      (provider) => !["google", "openlibrary"].includes(provider),
    ),
  );
  $("applyMode").value = matching.applyMode || "metadata_patch";
  $("coverMode").value = matching.coverMode || "if_missing";

  $("sort").value = run.sort || "media.metadata.title";
  $("limit").value = run.limit ?? 0;
  $("pageSize").value = run.pageSize ?? 100;
  $("requestDelayMs").value = run.requestDelayMs ?? 150;
  $("maxRetries").value = run.maxRetries ?? 2;
  $("timeoutSeconds").value = run.timeoutSeconds ?? 30;
  $("searchTimeoutSeconds").value = run.searchTimeoutSeconds ?? 12;

  $("weightTitle").value = weights.title ?? 50;
  $("weightAuthor").value = weights.author ?? 25;
  $("weightSeries").value = weights.series ?? 8;
  $("weightNarrator").value = weights.narrator ?? 6;
  $("weightYear").value = weights.year ?? 6;
  $("weightDuration").value = weights.duration ?? 5;

  $("matchedTag").value = tags.matchedTag || "ABSidekick: AutoMatched";
  $("unmatchedTag").value = tags.unmatchedTag || "ABSidekick: AutoMatch Unmatched";
  $("reviewTag").value = tags.reviewTag || "ABSidekick: Needs Review";
  $("reviewScanLimit").value = review.scanLimit ?? 25;
  $("reviewCandidateLimit").value = review.candidateLimit ?? 6;

  checkboxIds.forEach((id) => {
    if ($(id) && id in targeting) $(id).checked = Boolean(targeting[id]);
    if ($(id) && id in matching) $(id).checked = Boolean(matching[id]);
    if ($(id) && id in run) $(id).checked = Boolean(run[id]);
    if ($(id) && id in tags) $(id).checked = Boolean(tags[id]);
    if ($(id) && id in review) $(id).checked = Boolean(review[id]);
  });
  renderPolicySummary();
}

function getSettingsFromForm() {
  return {
    connection: {
      baseUrl: $("baseUrl").value.trim(),
      libraryId: $("libraryId").value,
      provider: $("provider").value,
      rememberConnection: $("rememberConnection").checked,
    },
    run: {
      dryRun: $("dryRun").checked,
      limit: Number($("limit").value || 0),
      pageSize: Number($("pageSize").value || 100),
      sort: $("sort").value,
      sortDesc: $("sortDesc").checked,
      requestDelayMs: Number($("requestDelayMs").value || 0),
      timeoutSeconds: Number($("timeoutSeconds").value || 30),
      searchTimeoutSeconds: Number($("searchTimeoutSeconds").value || 12),
      maxRetries: Number($("maxRetries").value || 0),
      stopOnError: $("stopOnError").checked,
    },
    targeting: {
      mode: $("targetMode").value,
      includeAuthors: selectedValues($("includeAuthors")),
      excludeAuthors: selectedValues($("excludeAuthors")),
      includeTags: csvToArray($("includeTags").value),
      excludeTags: csvToArray($("excludeTags").value),
      includeTagMode: $("includeTagMode").value,
      titleContains: $("titleContains").value.trim(),
      pathContains: $("pathContains").value.trim(),
      missingMetadataOnly: $("missingMetadataOnly").checked,
      missingCoverOnly: $("missingCoverOnly").checked,
      noAsinOnly: $("noAsinOnly").checked,
      noIsbnOnly: $("noIsbnOnly").checked,
      skipMissingItems: $("skipMissingItems").checked,
      skipInvalidItems: $("skipInvalidItems").checked,
    },
    matching: {
      threshold: Number($("threshold").value || 80),
      reviewFloor: Number($("reviewFloor").value || 65),
      candidateLimit: Number($("candidateLimit").value || 8),
      adaptiveSearch: $("adaptiveSearch").checked,
      automaticFallbackProviders: $("automaticFallbackProviders").checked,
      fallbackProviders: csvToArray($("fallbackProviders").value),
      strictAutoMatch: $("strictAutoMatch").checked,
      minimumTitleScore: Number($("minimumTitleScore").value || 86),
      minimumAuthorScore: Number($("minimumAuthorScore").value || 78),
      minimumWinnerMargin: Number($("minimumWinnerMargin").value || 6),
      minimumStrongSignals: Number($("minimumStrongSignals").value || 2),
      applyMode: $("applyMode").value,
      overwriteMetadata: $("overwriteMetadata").checked,
      repairSeries: $("repairSeries").checked,
      coverMode: $("coverMode").value,
      quickMatchFirstResultOnly: $("quickMatchFirstResultOnly").checked,
      requireAuthor: $("requireAuthor").checked,
      requireTitleToken: $("requireTitleToken").checked,
      durationToleranceMinutes: Number($("durationToleranceMinutes").value || 7),
      useEmbeddedFileMetadata: $("useEmbeddedFileMetadata").checked,
    },
    weights: {
      title: Number($("weightTitle").value || 0),
      author: Number($("weightAuthor").value || 0),
      series: Number($("weightSeries").value || 0),
      narrator: Number($("weightNarrator").value || 0),
      year: Number($("weightYear").value || 0),
      duration: Number($("weightDuration").value || 0),
    },
    tags: {
      matchedTag: $("matchedTag").value.trim(),
      unmatchedTag: $("unmatchedTag").value.trim(),
      reviewTag: $("reviewTag").value.trim(),
      tagMatched: $("tagMatched").checked,
      tagUnmatched: $("tagUnmatched").checked,
      tagReview: $("tagReview").checked,
      clearUnmatchedOnMatch: $("clearUnmatchedOnMatch").checked,
      clearReviewOnMatch: $("clearReviewOnMatch").checked,
      clearMatchedOnUnmatched: $("clearMatchedOnUnmatched").checked,
    },
    review: {
      scanLimit: Number($("reviewScanLimit").value || 25),
      candidateLimit: Number($("reviewCandidateLimit").value || 6),
      rejectAddsUnmatchedTag: $("rejectAddsUnmatchedTag").checked,
      rejectClearsReviewTag: $("rejectClearsReviewTag").checked,
    },
  };
}

function tokenPayload() {
  const token = $("token").value.trim();
  return token ? { token } : {};
}

function renderJobActivity(job) {
  if (!job) return;
  if (appState.activity.status === "running" && appState.activity.source !== "job") return;
  const stats = job.stats || {};
  const status = job.status || "idle";
  const running = ["queued", "running", "paused"].includes(status);
  const failed = status === "failed";
  const cancelled = status === "cancelled";
  const latest = job.latest?.title ? `Current item: ${job.latest.title}` : "Preparing the Audiobookshelf match queue…";
  appState.activity.source = "job";
  appState.activity.status = running ? "running" : failed ? "error" : cancelled ? "idle" : "success";
  appState.activity.startedAt = Date.parse(job.startedAt || "") || appState.activity.startedAt || Date.now();
  renderActivity({
    status: appState.activity.status,
    title: running ? `Matching job ${status}` : failed ? "Matching job failed" : cancelled ? "Matching job cancelled" : "Matching job complete",
    detail: running ? latest : `Matched ${stats.matched || 0}; review ${stats.review || 0}; errors ${stats.errors || 0}.`,
    current: stats.processed || 0,
    total: stats.total || 0,
    startedAt: appState.activity.startedAt,
  });
}

function renderJob(job) {
  appState.job = job;
  const stats = job?.stats || {};
  $("jobStatus").textContent = job?.status || "Idle";
  $("processedMetric").textContent = `${stats.processed || 0} / ${stats.total || 0}`;
  $("matchedMetric").textContent = stats.matched || 0;
  $("unmatchedMetric").textContent = stats.unmatched || 0;
  $("reviewMetric").textContent = stats.review || 0;
  $("latestItem").textContent = job?.latest?.title ? `Latest: ${job.latest.title}` : "";
  appState.logs = job?.logs || [];
  renderLogs();
  renderJobActivity(job);
  if (!appState.reviewRows.length && job?.reviewQueue?.length) {
    appState.reviewRows = job.reviewQueue;
    renderReviewDesk();
  }
}

function logComparable(entry, sortKey) {
  if (sortKey === "score") return Number(entry.score ?? -1);
  if (sortKey === "level") return String(entry.level || "");
  if (sortKey === "title") return String(entry.title || entry.message || "").toLowerCase();
  return Date.parse(entry.time || "") || 0;
}

function filteredLogs() {
  const level = $("logLevel")?.value || "all";
  const search = String($("logSearch")?.value || "").toLowerCase().trim();
  const sortKey = $("logSort")?.value || "time";
  const direction = $("logDirection")?.value || "desc";
  const rows = appState.logs.filter((entry) => {
    if (level !== "all" && entry.level !== level) return false;
    if (!search) return true;
    const haystack = [
      entry.message,
      entry.title,
      entry.author,
      entry.candidate,
      entry.level,
      entry.score,
      JSON.stringify(entry.reasons || []),
      JSON.stringify(entry.searchAttempts || []),
      JSON.stringify(entry.decision || {}),
    ]
      .filter((value) => value !== undefined && value !== null)
      .join(" ")
      .toLowerCase();
    return haystack.includes(search);
  });
  rows.sort((a, b) => {
    const left = logComparable(a, sortKey);
    const right = logComparable(b, sortKey);
    if (left < right) return direction === "asc" ? -1 : 1;
    if (left > right) return direction === "asc" ? 1 : -1;
    return 0;
  });
  return rows;
}

function providerLabel(provider) {
  if (provider === "embedded") return "File metadata";
  if (provider === "google") return "Google Books";
  if (provider === "openlibrary") return "Open Library";
  return `ABS ${provider || "provider"}`;
}

function renderSearchAttempts(attempts) {
  if (!attempts?.length) return "";
  const steps = attempts.map((attempt) => {
    const count = Number(attempt.resultCount || 0);
    const status = attempt.status === "results"
      ? `${count} result${count === 1 ? "" : "s"}`
      : attempt.status === "evidence"
        ? `${count} tagged file${count === 1 ? "" : "s"} used`
        : attempt.status === "no_metadata"
          ? "no usable tags"
      : attempt.status === "no_results"
        ? "no results"
        : attempt.status === "skipped"
          ? "skipped"
          : attempt.status === "disabled"
            ? "disabled"
            : "error";
    const query = attempt.provider === "embedded" || !attempt.queryTitle
      ? ""
      : ` · “${escapeHtml(attempt.queryTitle)}”${attempt.queryAuthor ? ` by ${escapeHtml(attempt.queryAuthor)}` : " · title only"}`;
    const detail = [
      attempt.strategy,
      attempt.queryTitle ? `Title: ${attempt.queryTitle}` : "",
      attempt.queryAuthor ? `Author: ${attempt.queryAuthor}` : "",
      attempt.message,
      attempt.error,
    ].filter(Boolean).join(" | ");
    return `<span class="search-attempt ${escapeHtml(attempt.status || "")}" title="${escapeHtml(detail)}"><b>${escapeHtml(providerLabel(attempt.provider))}</b> ${escapeHtml(status)}${query}</span>`;
  });
  return `<div class="search-trace"><span class="trace-label">Search path</span>${steps.join('<span class="trace-arrow">&rarr;</span>')}</div>`;
}

function renderLogDecision(decision) {
  if (!decision) return "";
  const policy = decision.policy || {};
  const reasons = decision.reasons || [];
  const advisories = decision.advisories || [];
  const scoreGate = decision.scorePassed
    ? `Similarity ${decision.score ?? "-"} passed the ${policy.autoThreshold ?? "-"} auto threshold.`
    : `Similarity ${decision.score ?? "-"} did not pass the ${policy.autoThreshold ?? "-"} auto threshold.`;
  const duplicateNote = Number(decision.equivalentCandidateCount || 0)
    ? ` ${decision.equivalentCandidateCount} duplicate-equivalent result${decision.equivalentCandidateCount === 1 ? " was" : "s were"} ignored for the margin check.`
    : "";
  const evidence = policy.strict
    ? `${decision.strongSignalCount ?? 0}/${policy.minimumStrongSignals ?? "-"} strong signals; different-result lead ${decision.margin ?? 0}/${policy.minimumWinnerMargin ?? "-"}.${duplicateNote}`
    : "Safety gates are not required by the current policy.";
  return `
    <div class="log-decision ${escapeHtml(decision.action || "")}">
      <strong>${escapeHtml(decision.action === "auto" ? "Automatically approved" : decision.action === "review" ? "Why it needs review" : "Why it was not matched")}</strong>
      <span>${escapeHtml(scoreGate)} ${escapeHtml(evidence)}</span>
      ${reasons.length ? `<span class="decision-reasons">${escapeHtml(reasons.join("; "))}</span>` : ""}
      ${advisories.length ? `<span class="decision-advisories">Match note: ${escapeHtml(advisories.join("; "))}. This did not block approval.</span>` : ""}
    </div>
  `;
}

function renderLogs() {
  const root = $("log");
  const logs = filteredLogs();
  const pageSize = Number($("logPageSize")?.value || 10);
  const pageCount = Math.max(1, Math.ceil(logs.length / pageSize));
  appState.logPage = Math.min(Math.max(1, appState.logPage), pageCount);
  const start = (appState.logPage - 1) * pageSize;
  const pageRows = logs.slice(start, start + pageSize);
  $("logPageInfo").textContent = logs.length ? `Page ${appState.logPage} / ${pageCount} (${logs.length} log lines)` : "Page 0 / 0";
  $("logPrevBtn").disabled = appState.logPage <= 1;
  $("logNextBtn").disabled = appState.logPage >= pageCount || !logs.length;
  root.innerHTML = "";
  if (!pageRows.length) {
    root.innerHTML = '<div class="note">No run logs yet.</div>';
    return;
  }
  pageRows.forEach((entry) => {
    const row = document.createElement("div");
    row.className = `log-entry ${entry.level || ""}`;
    const score = entry.score !== undefined && entry.score !== null ? ` Similarity ${entry.score}` : "";
    const selectedProvider = entry.search?.provider
      ? `<div class="meta">Selected from ${escapeHtml(providerLabel(entry.search.provider))}${entry.search.strategy ? ` via ${escapeHtml(entry.search.strategy)}` : ""}</div>`
      : "";
    row.innerHTML = `
      <strong>${entry.level || "info"}${score}</strong>
      <div>${escapeHtml(entry.message || "")}</div>
      <div class="meta">${escapeHtml(entry.time || "")}${entry.candidate ? ` | Candidate: ${escapeHtml(entry.candidate)}` : ""}</div>
      ${renderSearchAttempts(entry.searchAttempts)}
      ${selectedProvider}
      ${renderLogDecision(entry.decision)}
    `;
    root.appendChild(row);
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderPreview(preview) {
  const panel = $("previewPanel");
  panel.classList.remove("hidden");
  const rows = preview.rows || [];
  if (!rows.length) {
    panel.innerHTML = "<strong>No eligible items found for these filters.</strong>";
    return;
  }
  panel.innerHTML = `
    <strong>${preview.totalEligible} eligible item(s), previewing ${rows.length}</strong>
    ${rows
      .map((row) => {
        const best = row.best;
        const candidate = best?.candidate || {};
        const decision = row.decision || {};
        return `
          <div class="preview-row">
            <div><strong>${escapeHtml(row.title)}</strong> <span class="meta">${escapeHtml(row.author || "")}</span></div>
            ${
              best
                ? `<div><span class="score">${best.score}</span> | ${escapeHtml(candidate.title || "Untitled")} | ${escapeHtml(candidate.author || "")}</div>`
                : "<div>No candidates returned</div>"
            }
            <div class="confidence-line ${escapeHtml(decision.confidence || "none")}">${escapeHtml(decision.action || "unmatched")} · margin ${escapeHtml(decision.margin ?? 0)}${decision.reasons?.length ? ` · ${escapeHtml(decision.reasons.join("; "))}` : ""}</div>
            ${decision.advisories?.length ? `<div class="evidence-line">Match note: ${escapeHtml(decision.advisories.join("; "))}. This did not block approval.</div>` : ""}
            ${renderEmbeddedMetadata(row.embeddedMetadata)}
            ${renderSearchAttempts(row.searchAttempts)}
            ${row.searchDiagnostics?.length ? `<div class="conflict-line">Provider warning: ${escapeHtml(row.searchDiagnostics.map((entry) => entry.message).join("; "))}</div>` : ""}
            ${best?.parts ? `<div class="meta">Title ${best.parts.title} | Author ${best.parts.author} | Year ${best.parts.year} | Duration ${best.parts.duration}</div>` : ""}
          </div>
        `;
      })
      .join("")}
  `;
}

function candidateAuthor(candidate) {
  const author = candidate.author || candidate.authorName || candidate.authors || "";
  if (Array.isArray(author)) {
    return author.map((entry) => entry.name || entry).join(", ");
  }
  return String(author || "");
}

function candidateSeries(candidate) {
  const series = candidate.series;
  const label = (entry) => {
    if (!entry || typeof entry !== "object") return String(entry || "");
    const name = entry.name || entry.series || entry.title || "";
    const sequence = entry.sequence || entry.seq || entry.number || "";
    return sequence ? `${name} #${sequence}` : name;
  };
  if (Array.isArray(series)) return series.map(label).filter(Boolean).join(", ");
  if (series && typeof series === "object") return label(series);
  return series || "";
}

function candidateNarrator(candidate) {
  const narrator = candidate.narrator || candidate.narrators || "";
  return Array.isArray(narrator) ? narrator.join(", ") : String(narrator || "");
}

function cssCoverStyle(url, tone = "candidate") {
  if (!url) return "";
  const clean = String(url).replace(/['"\\\n\r]/g, "");
  const overlay =
    tone === "source"
      ? "linear-gradient(90deg, rgba(5, 32, 48, 0.94), rgba(8, 54, 70, 0.76))"
      : "linear-gradient(90deg, rgba(3, 16, 8, 0.92), rgba(3, 16, 8, 0.7))";
  return `style="background-image: ${overlay}, url('${escapeHtml(clean)}')"`;
}

function renderEmbeddedMetadata(metadata) {
  if (!metadata || !metadata.status) return "";
  if (metadata.status === "error") {
    return `<div class="provider-warning"><strong>Embedded file metadata failed:</strong> ${escapeHtml(metadata.error || "unknown error")}</div>`;
  }
  const fileCount = Number(metadata.fileCount || 0);
  const taggedCount = Number(metadata.taggedFileCount || 0);
  if (metadata.status !== "found") {
    return `<div class="meta">Embedded file metadata: no usable tags or filename titles in ${fileCount} associated audio file${fileCount === 1 ? "" : "s"}.</div>`;
  }
  const filenameTitles = (metadata.fileTitleCandidates || [])
    .map((candidate) => candidate?.title || candidate)
    .filter(Boolean);
  const evidence = [
    metadata.title ? `Title: ${metadata.title}` : "",
    filenameTitles.length ? `Filename title: ${filenameTitles.join(", ")}` : "",
    metadata.author ? `Author: ${metadata.author}` : "",
    metadata.series ? `Series: ${metadata.series}${metadata.seriesSequence ? ` #${metadata.seriesSequence}` : ""}` : "",
    metadata.narrator ? `Narrator: ${metadata.narrator}` : "",
    metadata.publishedYear ? `Year: ${metadata.publishedYear}` : "",
    metadata.asin ? `ASIN: ${metadata.asin}` : "",
    metadata.isbn ? `ISBN: ${metadata.isbn}` : "",
  ].filter(Boolean);
  return `<div class="evidence-line"><strong>File evidence (${taggedCount}/${fileCount} tagged):</strong> ${escapeHtml(evidence.join(" | ") || "usable filename evidence found")}</div>`;
}

function renderCurrentBookCard(item) {
  return `
    <article class="compare-card source-card" ${cssCoverStyle(item.coverUrl, "source")}>
      <div class="card-label">ABS ITEM</div>
      <div class="candidate-title">${escapeHtml(item.title || "Untitled")}</div>
      <div class="meta">${escapeHtml(item.author || "Unknown author")}</div>
      <div class="source-score">Current Metadata</div>
      ${renderEmbeddedMetadata(item.embeddedMetadata)}
      <details>
        <summary>More current info</summary>
        <div class="detail-grid">
          <span>Series</span><strong>${escapeHtml(item.series || "-")}</strong>
          <span>Narrator</span><strong>${escapeHtml(item.narrator || "-")}</strong>
          <span>Year</span><strong>${escapeHtml(item.year || "-")}</strong>
          <span>ASIN/ISBN</span><strong>${escapeHtml([item.asin, item.isbn].filter(Boolean).join(" / ") || "-")}</strong>
          <span>Tags</span><strong>${escapeHtml((item.tags || []).join(", ") || "-")}</strong>
          <span>Path</span><strong>${escapeHtml(item.path || "-")}</strong>
        </div>
        ${item.description ? `<p>${escapeHtml(String(item.description).slice(0, 320))}${String(item.description).length > 320 ? "..." : ""}</p>` : ""}
      </details>
    </article>
  `;
}

function authorMatchInfo(scored) {
  const authorScore = Number(scored.parts?.author ?? 0);
  return {
    score: authorScore,
    matches: authorScore >= 75,
  };
}

function renderCandidateCard(scored, rowIndex, candidateIndex, selectedIndex = 0) {
  const candidate = scored.candidate || {};
  const checked = candidateIndex === selectedIndex ? "checked" : "";
  const series = candidateSeries(candidate);
  const meta = [series, candidateNarrator(candidate), candidate.publishedYear].filter(Boolean).join(" | ");
  const author = candidateAuthor(candidate) || "Unknown author";
  const authorInfo = authorMatchInfo(scored);
  const searchOrigin = scored.searchSource === "manual"
    ? `Manual search · ${scored.searchProvider || "provider"}`
    : scored.search?.strategy
      ? `Automated search · ${scored.search.strategy}`
      : "";
  return `
    <article class="compare-card candidate-card ${authorInfo.matches ? "author-match-card" : ""}" ${cssCoverStyle(candidate.cover)}>
      <label class="candidate-pick">
        <input type="radio" name="review-${rowIndex}" value="${candidateIndex}" ${checked} />
        <span>Use This</span>
      </label>
      ${searchOrigin ? `<div class="candidate-origin">${escapeHtml(searchOrigin)}</div>` : ""}
      <div class="candidate-title">${escapeHtml(candidate.title || "Untitled")}</div>
      <div class="candidate-author ${authorInfo.matches ? "author-match" : ""}">
        <span>${escapeHtml(author)}</span>
        ${authorInfo.matches ? `<strong>Author match ${Math.round(authorInfo.score)}</strong>` : `<em>Author ${Math.round(authorInfo.score)}</em>`}
      </div>
      <div class="match-score"><span class="score">${scored.score}</span> match score</div>
      ${series ? `<div class="evidence-line">Series to write: ${escapeHtml(series)}${candidate._absidekickSeriesSource ? ` · ${escapeHtml(candidate._absidekickSeriesSource)}` : ""}</div>` : ""}
      ${scored.strongSignals?.length ? `<div class="evidence-line">Strong: ${escapeHtml(scored.strongSignals.join(", "))}</div>` : ""}
      ${scored.conflicts?.length ? `<div class="conflict-line">Check: ${escapeHtml(scored.conflicts.join("; "))}</div>` : ""}
      ${scored.advisories?.length ? `<div class="evidence-line">Note: ${escapeHtml(scored.advisories.join("; "))}</div>` : ""}
      <details>
        <summary>More match info</summary>
        ${meta ? `<div class="meta">${escapeHtml(meta)}</div>` : ""}
        ${renderScoreParts(scored.parts)}
        <div class="detail-grid">
          <span>Publisher</span><strong>${escapeHtml(candidate.publisher || "-")}</strong>
          <span>ASIN/ISBN</span><strong>${escapeHtml([candidate.asin, candidate.isbn].filter(Boolean).join(" / ") || "-")}</strong>
          <span>Provider Rank</span><strong>${Number(scored.index ?? 0) + 1}</strong>
        </div>
        ${candidate.description ? `<p>${escapeHtml(String(candidate.description).slice(0, 320))}${String(candidate.description).length > 320 ? "..." : ""}</p>` : ""}
      </details>
    </article>
  `;
}

function renderScoreParts(parts) {
  if (!parts) return "";
  const authorScore = Number(parts.author ?? 0);
  return `
    <div class="score-parts">
      <span>Title ${parts.title ?? "-"}</span>
      <span class="${authorScore >= 75 ? "author-chip-match" : ""}">Author ${parts.author ?? "-"}</span>
      <span>Series ${parts.series ?? "-"}</span>
      <span>Narrator ${parts.narrator ?? "-"}</span>
      <span>Year ${parts.year ?? "-"}</span>
      <span>Duration ${parts.duration ?? "-"}</span>
    </div>
  `;
}

function renderMatchDecision(decision) {
  if (!decision) return "";
  const reasons = decision.reasons || [];
  const advisories = decision.advisories || [];
  const policy = decision.policy || {};
  const scoreStatus = decision.scorePassed
    ? `Similarity ${decision.score ?? "-"} passed ${policy.autoThreshold ?? "-"}`
    : `Similarity ${decision.score ?? "-"} is below ${policy.autoThreshold ?? "-"}`;
  const duplicateNote = Number(decision.equivalentCandidateCount || 0)
    ? `; ${decision.equivalentCandidateCount} duplicate-equivalent result${decision.equivalentCandidateCount === 1 ? " ignored" : "s ignored"}`
    : "";
  const evidenceStatus = policy.strict
    ? `${decision.strongSignalCount ?? 0}/${policy.minimumStrongSignals ?? "-"} strong signals; different-result lead ${decision.margin ?? 0}/${policy.minimumWinnerMargin ?? "-"}${duplicateNote}`
    : "Safety checks are not blocking in this policy";
  return `
    <div class="match-decision ${escapeHtml(decision.confidence || "none")}">
      <strong>${decision.action === "auto" ? "High-confidence match" : decision.action === "review" ? "Human review required" : "Insufficient evidence"}</strong>
      <span>${escapeHtml(scoreStatus)} | ${escapeHtml(evidenceStatus)}</span>
      ${reasons.length ? `<span>${escapeHtml(reasons.join(" · "))}</span>` : ""}
      ${advisories.length ? `<span>Match note: ${escapeHtml(advisories.join(" · "))}. This did not block approval.</span>` : ""}
    </div>
  `;
}

function reviewSearchState(row) {
  if (!row.manualSearch) {
    row.manualSearch = {
      title: row.item?.title || "",
      author: row.item?.author || "",
      provider: $("provider")?.value || appState.settings?.connection?.provider || "audible",
      limit: 20,
      open: false,
      loading: false,
      error: "",
      result: null,
    };
  }
  return row.manualSearch;
}

function renderManualSearchOutcome(row, rowIndex) {
  const search = reviewSearchState(row);
  if (search.loading) {
    return `
      <div class="manual-search-outcome searching" data-manual-search-outcome role="status" aria-live="polite">
        <div>
          <strong><span class="search-pulse" aria-hidden="true"></span>SEARCHING NOW</strong>
          <span>Checking ${escapeHtml(search.provider)} for “${escapeHtml(search.title || "all titles")}”. This review stays open while results load.</span>
        </div>
      </div>
    `;
  }
  if (search.error) {
    return `
      <div class="manual-search-outcome error" data-manual-search-outcome role="alert">
        <div><strong>SEARCH FAILED</strong><span>${escapeHtml(search.error)}</span></div>
        <div class="manual-search-actions"><button type="button" class="danger" data-review-action="reject" data-row="${rowIndex}">Reject Item</button></div>
      </div>
    `;
  }
  const result = search.result;
  if (!result) {
    return `
      <div class="manual-search-outcome idle" data-manual-search-outcome aria-live="polite">
        <div><strong>READY TO RESEARCH</strong><span>The search runs here. This row will remain open and report whether it found a confident match.</span></div>
      </div>
    `;
  }

  const resultCount = Number(result.resultCount || 0);
  const confident = Boolean(result.isConfidentMatch);
  const hasCandidates = resultCount > 0;
  const heading = confident ? "MATCH FOUND" : hasCandidates ? "NO CONFIDENT MATCH — REVIEW RESULTS" : "NO MATCH FOUND";
  const className = confident ? "match" : hasCandidates ? "review" : "unmatched";
  const best = result.bestCandidate || null;
  const candidate = best?.candidate || {};
  const bestLine = best
    ? `<span class="manual-best">Best result: <b>${escapeHtml(candidate.title || "Untitled")}</b>${candidateAuthor(candidate) ? ` by ${escapeHtml(candidateAuthor(candidate))}` : ""} · score ${escapeHtml(best.score ?? "-")}</span>`
    : "";
  const reasons = result.decision?.reasons || [];
  return `
    <div class="manual-search-outcome ${className}" data-manual-search-outcome role="status" aria-live="polite">
      <div>
        <strong>${heading}</strong>
        <span>${escapeHtml(result.message || "Search completed.")}</span>
        ${bestLine}
        ${reasons.length ? `<span class="manual-reasons">Policy: ${escapeHtml(reasons.join(" · "))}</span>` : ""}
        <span class="manual-result-meta">${resultCount} result${resultCount === 1 ? "" : "s"} returned by ${escapeHtml(search.provider)}</span>
      </div>
      <div class="manual-search-actions">
        ${hasCandidates ? `<button type="button" data-review-action="approve" data-row="${rowIndex}">Approve Selected Match</button>` : ""}
        <button type="button" class="danger" data-review-action="reject" data-row="${rowIndex}">Reject Item</button>
      </div>
    </div>
  `;
}

function renderReviewSearch(row, rowIndex) {
  const search = reviewSearchState(row);
  const options = REVIEW_PROVIDERS.map(
    (provider) => `<option value="${provider}"${provider === search.provider ? " selected" : ""}>${["google", "openlibrary"].includes(provider) ? `${provider} (native)` : provider}</option>`,
  ).join("");
  const keepOpen = search.open || search.loading || search.result || search.error;
  return `
    <details class="review-search"${keepOpen ? " open" : ""}>
      <summary>Research this match manually</summary>
      <p class="meta">Edit the fields and search now. Results appear here without closing the review; a confident result is identified, while uncertain results remain available for your decision.</p>
      <form class="review-search-form" data-review-search-form data-row="${rowIndex}">
        <label class="field">
          <span>Title</span>
          <input type="search" name="title" value="${escapeHtml(search.title)}" placeholder="Book title or distinctive words" maxlength="300" />
        </label>
        <label class="field">
          <span>Author <em>optional</em></span>
          <input type="search" name="author" value="${escapeHtml(search.author)}" placeholder="Clear this to broaden results" maxlength="200" />
        </label>
        <label class="field">
          <span>Provider</span>
          <select name="provider">${options}</select>
        </label>
        <label class="field compact">
          <span>Results</span>
          <input type="number" name="limit" value="${Number(search.limit) || 20}" min="1" max="30" />
        </label>
        <button type="submit"${search.loading ? " disabled" : ""}>${search.loading ? "Searching…" : "Search Now"}</button>
      </form>
      ${renderManualSearchOutcome(row, rowIndex)}
    </details>
  `;
}

function candidateIdentity(scored) {
  const candidate = scored?.candidate || {};
  const identifier = candidate.asin || candidate.isbn || candidate.id || candidate.bookId;
  if (identifier) return `id:${String(identifier).toLowerCase()}`;
  return [candidate.title, candidateAuthor(candidate), candidate.publishedYear]
    .map((value) => String(value || "").toLowerCase().trim())
    .join("|");
}

function mergeReviewCandidates(manualCandidates, existingCandidates) {
  const seen = new Set();
  return [...manualCandidates, ...existingCandidates].filter((scored) => {
    const identity = candidateIdentity(scored);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function renderGoogleBooksStatus(providers = {}) {
  const status = $("googleBooksStatus");
  const result = $("googleBooksResult");
  const input = $("googleBooksApiKey");
  const clearButton = $("clearGoogleBooksBtn");
  const hasKey = Boolean(providers.hasGoogleBooksApiKey);
  const ready = Boolean(providers.googleBooksReady);
  const lastError = String(providers.googleBooksLastError || "").trim();

  status.className = `provider-state ${ready ? "ready" : hasKey ? "untested" : "missing"}`;
  status.textContent = ready
    ? lastError ? "Saved · last test interrupted" : "API key saved & enabled"
    : hasKey ? "Saved · test required" : "Not configured";
  input.placeholder = hasKey
    ? "Stored key is hidden — paste only to replace it"
    : "Paste a new key, then test it";
  clearButton.disabled = !hasKey;

  result.className = "provider-test-result";
  if (ready) {
    const when = providers.googleBooksApiKeyValidatedAt
      ? new Date(providers.googleBooksApiKeyValidatedAt).toLocaleString()
      : "recently";
    result.classList.add(lastError ? "warning" : "success");
    result.textContent = lastError
      ? `The key remains enabled because it passed validation ${when}. The latest test was interrupted by a temporary provider error: ${lastError}`
      : `Saved privately on this server; you do not need to enter the key again. Live Google Books test passed ${when}. Automatic runs try ABS first, native Google second, and Open Library third.`;
  } else if (hasKey) {
    result.classList.add("warning");
    result.textContent = providers.googleBooksLastError || "The saved key must pass a live test before Google searches are enabled.";
  } else {
    result.textContent = "Google second pass is disabled. MyAnonaSuite will skip Google and continue to Open Library until a key passes the live test.";
  }
}

function renderOpenLibraryStatus() {
  const enabled = $("openLibraryEnabled").checked;
  const contact = $("openLibraryContactEmail").value.trim();
  const status = $("openLibraryStatus");
  const result = $("openLibraryResult");
  status.className = `provider-state ${enabled ? "ready" : "missing"}`;
  status.textContent = enabled ? (contact ? "Identified & enabled" : "Enabled") : "Disabled";
  result.className = `provider-test-result ${enabled ? "success" : "warning"}`;
  result.textContent = enabled
    ? contact
      ? "Third-stage and manual searches are enabled at the identified-client limit of 3 requests/second. Results are cached during each run."
      : "Third-stage and manual searches are enabled at the anonymous limit of 1 request/second. Add a contact email for Open Library's identified-client limit."
    : "Open Library automatic and Review Desk searches are disabled.";
}

function googleKeyPayload() {
  const key = $("googleBooksApiKey").value.trim();
  return key ? { googleBooksApiKey: key } : {};
}

function openLibraryPayload() {
  return {
    openLibraryEnabled: $("openLibraryEnabled").checked,
    openLibraryContactEmail: $("openLibraryContactEmail").value.trim(),
  };
}

function ensureProviderReady(provider) {
  if (provider === "google" && !appState.settings?.providers?.googleBooksReady) {
    throw new Error(
      "Google Books is disabled. Open ABSidekick Config, add an API key, and select Test & Enable first. No Google request was sent.",
    );
  }
  if (provider === "openlibrary" && !$("openLibraryEnabled").checked) {
    throw new Error("Open Library is disabled. Enable it in ABSidekick Config before selecting it as the primary or manual provider.");
  }
}

function selectedCandidateIndex(row) {
  const candidates = row.candidates || [];
  if (!row.selectedCandidateIdentity) return 0;
  const index = candidates.findIndex((candidate) => candidateIdentity(candidate) === row.selectedCandidateIdentity);
  return index >= 0 ? index : 0;
}

function renderReviewDesk({ preserveRow = null } = {}) {
  const root = $("reviewDesk");
  const oldRow = preserveRow === null ? null : root.querySelector(`[data-review-row="${preserveRow}"]`);
  const oldTop = oldRow?.getBoundingClientRect().top;
  const rows = appState.reviewRows || [];
  if (!rows.length) {
    root.innerHTML = '<div class="note">No review items loaded. Scan review tags or run automatch to build this queue.</div>';
    return;
  }
  root.innerHTML = rows
    .map((row, rowIndex) => {
      const item = row.item || {};
      const candidates = row.candidates || [];
      const selectedIndex = selectedCandidateIndex(row);
      return `
        <article class="review-item" data-review-row="${rowIndex}">
          <div class="review-item-head">
            <div>
              <p class="eyebrow">Review #${rowIndex + 1}</p>
              <h3>${escapeHtml(item.title || "Untitled")}</h3>
              <div class="meta">${escapeHtml(item.author || "")}${item.path ? ` | ${escapeHtml(item.path)}` : ""}</div>
            </div>
            <div class="button-row">
              <button type="button" class="ghost" data-review-action="approve" data-row="${rowIndex}">Approve Selected</button>
              <button type="button" class="danger" data-review-action="reject" data-row="${rowIndex}">Reject</button>
            </div>
          </div>
          ${renderMatchDecision(row.decision)}
          ${renderSearchAttempts(row.searchAttempts)}
          ${row.searchDiagnostics?.length ? `<div class="provider-warning">${escapeHtml(row.searchDiagnostics.map((entry) => `${entry.provider}: ${entry.error}`).join(" · "))}</div>` : ""}
          ${renderReviewSearch(row, rowIndex)}
          <div class="compare-grid">
            ${renderCurrentBookCard(item)}
            ${
              candidates.length
                ? candidates.map((scored, candidateIndex) => renderCandidateCard(scored, rowIndex, candidateIndex, selectedIndex)).join("")
                : '<div class="note">No candidates are available yet. Use the manual research fields above now, or reject this item.</div>'
            }
          </div>
        </article>
      `;
    })
    .join("");
  if (oldTop !== undefined) {
    const newRow = root.querySelector(`[data-review-row="${preserveRow}"]`);
    if (newRow) window.scrollBy(0, newRow.getBoundingClientRect().top - oldTop);
  }
}

async function scanReview() {
  const settings = getSettingsFromForm();
  ensureProviderReady(settings.connection.provider);
  const payload = await api("/api/review/scan", {
    method: "POST",
    body: JSON.stringify({ settings, limit: settings.review.scanLimit, ...tokenPayload() }),
  });
  appState.reviewRows = payload.review.rows || [];
  renderReviewDesk();
  showToast(`Loaded ${appState.reviewRows.length} review item(s)`);
  return payload;
}

function loadJobReviewQueue() {
  const rows = appState.job?.reviewQueue || [];
  appState.reviewRows = rows;
  renderReviewDesk();
  showToast(`Loaded ${rows.length} item(s) from the current job queue`);
  reportInstantActivity("Job review queue loaded", `${rows.length} review item(s) are now displayed.`);
}

async function approveReview(rowIndex) {
  const row = appState.reviewRows[rowIndex];
  if (!row) return ACTION_CANCELLED;
  const selected = document.querySelector(`input[name="review-${rowIndex}"]:checked`);
  const candidateIndex = Number(selected?.value || 0);
  const candidate = row.candidates?.[candidateIndex];
  if (!candidate) {
    showToast("No candidate selected");
    return ACTION_CANCELLED;
  }
  const confirmed = window.confirm(`Approve this match for "${row.item.title}"? This writes to Audiobookshelf.`);
  if (!confirmed) return ACTION_CANCELLED;
  appState.reviewRows.splice(rowIndex, 1);
  renderReviewDesk();
  try {
    const payload = await api("/api/review/approve", {
      method: "POST",
      body: JSON.stringify({ settings: getSettingsFromForm(), itemId: row.item.id, candidate, row, ...tokenPayload() }),
    });
    showToast("Review match approved");
    return payload;
  } catch (error) {
    appState.reviewRows.splice(rowIndex, 0, row);
    renderReviewDesk();
    throw error;
  }
}

async function rejectReview(rowIndex) {
  const row = appState.reviewRows[rowIndex];
  if (!row) return ACTION_CANCELLED;
  appState.reviewRows.splice(rowIndex, 1);
  renderReviewDesk();
  try {
    const payload = await api("/api/review/reject", {
      method: "POST",
      body: JSON.stringify({ settings: getSettingsFromForm(), itemId: row.item.id, row, ...tokenPayload() }),
    });
    showToast("Review item rejected");
    return payload;
  } catch (error) {
    appState.reviewRows.splice(rowIndex, 0, row);
    renderReviewDesk();
    throw error;
  }
}

async function loadState() {
  const payload = await api("/api/state");
  setForm(payload.settings);
  renderJob(payload.job);
  resumeBackendActivity(payload.activity);
  renderReviewDesk();
}

async function searchReview(rowIndex, form) {
  const row = appState.reviewRows[rowIndex];
  if (!row) return;
  const formData = new FormData(form);
  const search = reviewSearchState(row);
  search.title = String(formData.get("title") || "").trim();
  search.author = String(formData.get("author") || "").trim();
  search.provider = String(formData.get("provider") || "audible");
  ensureProviderReady(search.provider);
  search.limit = Number(formData.get("limit") || 20);
  search.open = true;
  search.loading = true;
  search.error = "";
  search.result = null;
  const searchPanel = form.closest(".review-search");
  if (searchPanel) {
    searchPanel.open = true;
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Searching…";
    }
    const outcome = searchPanel.querySelector("[data-manual-search-outcome]");
    if (outcome) outcome.outerHTML = renderManualSearchOutcome(row, rowIndex);
  }
  try {
    const payload = await api("/api/review/search", {
      method: "POST",
      body: JSON.stringify({
        settings: getSettingsFromForm(),
        itemId: row.item.id,
        query: {
          title: search.title,
          author: search.author,
          provider: search.provider,
          limit: search.limit,
        },
        ...tokenPayload(),
      }),
    });
    const candidates = payload.candidates || [];
    row.item = payload.item || row.item;
    row.candidates = mergeReviewCandidates(candidates, row.candidates || []);
    row.decision = payload.decision || row.decision;
    if (candidates.length) {
      row.selectedCandidateIdentity = candidateIdentity(candidates[0]);
    }
    search.result = {
      ...(payload.manualMatch || {}),
      decision: payload.decision || null,
      resultCount: Number(payload.resultCount ?? candidates.length),
      bestCandidate: payload.manualMatch?.bestCandidate || candidates[0] || null,
    };
    const verdict = search.result.isConfidentMatch
      ? "Confident match found"
      : candidates.length
        ? "Results need your review"
        : "No match found";
    showToast(verdict);
    return payload;
  } catch (error) {
    search.error = `${error.message}. Try broader terms or another provider.`;
    search.result = null;
    throw error;
  } finally {
    search.loading = false;
    renderReviewDesk({ preserveRow: rowIndex });
  }
}

async function connect() {
  const payload = await api("/api/connect", {
    method: "POST",
    body: JSON.stringify({ settings: getSettingsFromForm(), ...tokenPayload() }),
  });
  setForm(payload.settings);
  populateLibraries(normalizeLibraryList(payload));
  showToast(payload.message || "Connected");
  return payload;
}

async function saveSettings() {
  const payload = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      settings: getSettingsFromForm(),
      ...tokenPayload(),
      ...googleKeyPayload(),
      ...openLibraryPayload(),
    }),
  });
  setForm(payload.settings);
  showToast("Settings saved");
  return payload;
}

async function testGoogleBooks() {
  const button = $("testGoogleBooksBtn");
  const result = $("googleBooksResult");
  button.disabled = true;
  button.textContent = "Testing live API…";
  result.className = "provider-test-result testing";
  result.textContent = "Sending one test query to the official Google Books API…";
  try {
    const payload = await api("/api/provider/google/test", {
      method: "POST",
      body: JSON.stringify({
        settings: getSettingsFromForm(),
        ...tokenPayload(),
        ...googleKeyPayload(),
      }),
    });
    setForm(payload.settings);
    showToast(payload.message || "Google Books API key tested and enabled");
    return payload;
  } catch (error) {
    try {
      await loadState();
    } catch (_stateError) {
      // Keep the provider's actionable test error visible even if state reload fails.
    }
    result.className = "provider-test-result error";
    result.textContent = `${error.message} Google searches remain disabled.`;
    throw error;
  } finally {
    button.disabled = false;
    button.textContent = "Test & Enable";
  }
}

async function clearGoogleBooks() {
  if (!window.confirm("Remove the saved Google Books API key and disable all Google searches?")) return ACTION_CANCELLED;
  const payload = await api("/api/provider/google/clear", {
    method: "POST",
    body: "{}",
  });
  setForm(payload.settings);
  showToast(payload.message || "Google Books API key removed");
  return payload;
}

async function loadFilterData() {
  const libraryId = $("libraryId").value;
  if (!libraryId) {
    throw new Error("Select a library first");
  }
  const payload = await api(`/api/filter-data?libraryId=${encodeURIComponent(libraryId)}`);
  populateFilterData(payload.filterData);
  return payload;
}

async function preview() {
  ensureProviderReady($("provider").value);
  const payload = await api("/api/preview", {
    method: "POST",
    body: JSON.stringify({ settings: getSettingsFromForm(), limit: 10, ...tokenPayload() }),
  });
  renderPreview(payload.preview);
  showToast("Preview loaded");
  return payload;
}

async function startJob() {
  const settings = getSettingsFromForm();
  ensureProviderReady(settings.connection.provider);
  if (!settings.run.dryRun) {
    const confirmed = window.confirm("This run will write metadata/tags to Audiobookshelf. Start anyway?");
    if (!confirmed) return ACTION_CANCELLED;
  }
  const payload = await api("/api/job/start", {
    method: "POST",
    body: JSON.stringify({ settings, ...tokenPayload() }),
  });
  renderJob(payload.job);
  startPolling();
  showToast("Job started");
  return payload;
}

async function jobAction(action) {
  const payload = await api(`/api/job/${action}`, { method: "POST", body: "{}" });
  renderJob(payload.job);
  return payload;
}

async function pollJob() {
  try {
    const payload = await api("/api/job");
    renderJob(payload.job);
    const status = payload.job?.status;
    if (!["queued", "running", "paused"].includes(status)) {
      stopPolling();
    }
  } catch (error) {
    console.error(error);
  }
}

function startPolling() {
  stopPolling();
  appState.poll = window.setInterval(pollJob, 1500);
}

function stopPolling() {
  if (appState.poll) window.clearInterval(appState.poll);
  appState.poll = null;
}

function exportLog() {
  const job = appState.job;
  if (!job) {
    showToast("No job log to export");
    reportInstantActivity("Nothing to export", "Run a matching job before exporting its log.");
    return;
  }
  const blob = new Blob([JSON.stringify(job, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `absidekick-job-${job.id}.json`;
  link.click();
  URL.revokeObjectURL(url);
  reportInstantActivity("Job log exported", "The JSON log download was created.");
}

function wireEvents() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
      button.classList.add("active");
      $(`tab-${button.dataset.tab}`).classList.add("active");
    });
  });
  $("matchPolicyPreset").addEventListener("change", (event) => {
    applyMatchingPolicyPreset(event.target.value);
  });
  [...MATCH_POLICY_FIELDS, "strictAutoMatch"].forEach((id) => {
    $(id).addEventListener("input", renderPolicySummary);
    $(id).addEventListener("change", renderPolicySummary);
  });
  ["openLibraryEnabled", "openLibraryContactEmail"].forEach((id) => {
    $(id).addEventListener("input", renderOpenLibraryStatus);
    $(id).addEventListener("change", renderOpenLibraryStatus);
  });
  $("token").addEventListener("input", () => {
    if ($("token").value.trim()) {
      $("rememberConnection").checked = true;
      $("absTokenStatus").className = "provider-state untested";
      $("absTokenStatus").textContent = "Will save on connect";
      $("connectionNote").textContent = "This new API token will be stored privately when you select Connect or Save Settings.";
    }
  });
  $("connectBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Connecting to Audiobookshelf",
    detail: "Checking the server URL and token, then loading available libraries…",
    busyText: "Connecting…",
    success: (payload) => payload?.message || "Audiobookshelf connection is ready.",
  }, connect));
  $("saveSettingsBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Saving ABSidekick settings",
    detail: "Validating and writing module settings to the private data folder…",
    busyText: "Saving…",
    success: "Settings were saved and will apply to the next action.",
  }, saveSettings));
  $("testGoogleBooksBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Testing Google Books",
    detail: "Sending one validation query to the official Google Books API…",
    busyText: "Testing…",
    success: "The API key passed its live test; native Google searches are enabled.",
  }, testGoogleBooks));
  $("clearGoogleBooksBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Removing Google Books key",
    detail: "Waiting for confirmation, then removing the stored private key…",
    busyText: "Removing…",
    success: "The key was removed and Google searches are disabled.",
  }, clearGoogleBooks));
  $("loadFiltersBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Loading library filters",
    detail: "Requesting authors, tags, and series from the selected ABS library…",
    busyText: "Loading…",
    success: "Library filter choices are ready.",
  }, loadFilterData));
  $("previewBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Building match preview",
    detail: "Loading eligible ABS items and searching the selected metadata provider…",
    busyText: "Previewing…",
    success: "The dry-run match preview is ready.",
  }, preview));
  $("startBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Starting matching job",
    detail: "Validating run policy and creating the background matching queue…",
    busyText: "Starting…",
    success: "The background job started; live item counts will continue here.",
  }, startJob));
  $("pauseBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Pausing matching job",
    detail: "Waiting for the current safe checkpoint…",
    busyText: "Pausing…",
    success: "The matching job is paused.",
  }, () => jobAction("pause")));
  $("resumeBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Resuming matching job",
    detail: "Releasing the paused background worker…",
    busyText: "Resuming…",
    success: "The matching job resumed.",
  }, () => jobAction("resume")));
  $("cancelBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Cancelling matching job",
    detail: "Requesting a stop at the next safe checkpoint…",
    busyText: "Cancelling…",
    success: "Cancellation was requested.",
  }, () => jobAction("cancel")));
  $("exportBtn").addEventListener("click", exportLog);
  $("scanReviewBtn").addEventListener("click", (event) => runVisibleAction(event.currentTarget, {
    title: "Scanning Review Tags",
    successTitle: "Review scan complete",
    detail: "Loading review-tagged ABS items; live item and provider progress will appear here…",
    busyText: "Scanning…",
    pollBackend: true,
    success: () => `Review scan finished with ${appState.reviewRows.length} item(s) ready for review.`,
  }, scanReview));
  $("loadJobReviewBtn").addEventListener("click", loadJobReviewQueue);
  $("reviewDesk").addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-action]");
    if (!button) return;
    const row = Number(button.dataset.row);
    const title = appState.reviewRows[row]?.item?.title || `review item ${row + 1}`;
    if (button.dataset.reviewAction === "approve") {
      runVisibleAction(button, {
        title: `Approving ${title}`,
        detail: "Writing the selected metadata to Audiobookshelf and clearing review state…",
        busyText: "Approving…",
        success: "The selected match was written to Audiobookshelf.",
      }, () => approveReview(row));
    }
    if (button.dataset.reviewAction === "reject") {
      runVisibleAction(button, {
        title: `Rejecting ${title}`,
        detail: "Updating ABS tags and saving the review decision…",
        busyText: "Rejecting…",
        success: "The item was rejected and its review state was saved.",
      }, () => rejectReview(row));
    }
  });
  $("reviewDesk").addEventListener("submit", (event) => {
    const form = event.target.closest("[data-review-search-form]");
    if (!form) return;
    event.preventDefault();
    const row = Number(form.dataset.row);
    const button = form.querySelector('button[type="submit"]');
    const title = appState.reviewRows[row]?.item?.title || `review item ${row + 1}`;
    runVisibleAction(button, {
      title: `Researching ${title}`,
      detail: "Searching the selected provider and rescoring every returned candidate…",
      busyText: "Searching…",
      success: (payload) => `${payload?.resultCount || 0} candidate(s) returned; the review row has been updated.`,
    }, () => searchReview(row, form));
  });
  $("reviewDesk").addEventListener("change", (event) => {
    const radio = event.target.closest('input[type="radio"][name^="review-"]');
    if (!radio) return;
    const rowIndex = Number(radio.name.slice("review-".length));
    const row = appState.reviewRows[rowIndex];
    const candidate = row?.candidates?.[Number(radio.value)];
    if (row && candidate) row.selectedCandidateIdentity = candidateIdentity(candidate);
  });
  ["logSearch", "logLevel", "logSort", "logDirection", "logPageSize"].forEach((id) => {
    $(id).addEventListener("input", () => {
      appState.logPage = 1;
      renderLogs();
    });
    $(id).addEventListener("change", () => {
      appState.logPage = 1;
      renderLogs();
    });
  });
  $("logPrevBtn").addEventListener("click", () => {
    appState.logPage -= 1;
    renderLogs();
  });
  $("logNextBtn").addEventListener("click", () => {
    appState.logPage += 1;
    renderLogs();
  });
}

wireEvents();
const initialView = $("absidekickModule")?.dataset.initialView || "run";
document.querySelectorAll(".absidekick-module .tab").forEach((tab) => {
  tab.classList.toggle("active", tab.dataset.tab === initialView);
});
document.querySelectorAll(".absidekick-module .tab-panel").forEach((panel) => {
  panel.classList.toggle("active", panel.id === `tab-${initialView}`);
});
loadState()
  .then(() => {
    if (appState.job?.status && ["queued", "running", "paused"].includes(appState.job.status)) {
      startPolling();
    }
  })
  .catch((error) => showToast(error.message));
