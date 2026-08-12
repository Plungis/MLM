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

let appState = {
  settings: null,
  libraries: [],
  filterData: null,
  job: null,
  poll: null,
  reviewRows: [],
  logs: [],
  logPage: 1,
};

const checkboxIds = [
  "rememberConnection",
  "missingMetadataOnly",
  "missingCoverOnly",
  "noAsinOnly",
  "noIsbnOnly",
  "skipMissingItems",
  "skipInvalidItems",
  "overwriteMetadata",
  "quickMatchFirstResultOnly",
  "requireAuthor",
  "requireTitleToken",
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
  const run = settings.run || {};
  const targeting = settings.targeting || {};
  const matching = settings.matching || {};
  const weights = settings.weights || {};
  const tags = settings.tags || {};
  const review = settings.review || {};

  $("baseUrl").value = connection.baseUrl || "";
  $("provider").value = connection.provider || "audible";
  $("rememberConnection").checked = Boolean(connection.rememberConnection);
  $("libraryId").value = connection.libraryId || "";

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
  $("applyMode").value = matching.applyMode || "metadata_patch";
  $("coverMode").value = matching.coverMode || "if_missing";

  $("sort").value = run.sort || "media.metadata.title";
  $("limit").value = run.limit ?? 0;
  $("pageSize").value = run.pageSize ?? 100;
  $("requestDelayMs").value = run.requestDelayMs ?? 150;
  $("maxRetries").value = run.maxRetries ?? 2;

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
      timeoutSeconds: 30,
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
      applyMode: $("applyMode").value,
      overwriteMetadata: $("overwriteMetadata").checked,
      coverMode: $("coverMode").value,
      quickMatchFirstResultOnly: $("quickMatchFirstResultOnly").checked,
      requireAuthor: $("requireAuthor").checked,
      requireTitleToken: $("requireTitleToken").checked,
      durationToleranceMinutes: Number($("durationToleranceMinutes").value || 7),
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
    const haystack = [entry.message, entry.title, entry.author, entry.candidate, entry.level, entry.score]
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
    const score = entry.score !== undefined && entry.score !== null ? ` Score ${entry.score}` : "";
    row.innerHTML = `
      <strong>${entry.level || "info"}${score}</strong>
      <div>${escapeHtml(entry.message || "")}</div>
      <div class="meta">${escapeHtml(entry.time || "")}${entry.candidate ? ` | Candidate: ${escapeHtml(entry.candidate)}` : ""}</div>
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
        return `
          <div class="preview-row">
            <div><strong>${escapeHtml(row.title)}</strong> <span class="meta">${escapeHtml(row.author || "")}</span></div>
            ${
              best
                ? `<div><span class="score">${best.score}</span> | ${escapeHtml(candidate.title || "Untitled")} | ${escapeHtml(candidate.author || "")}</div>`
                : "<div>No candidates returned</div>"
            }
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
  if (Array.isArray(series)) return series.map((entry) => entry.name || entry).join(", ");
  if (series && typeof series === "object") return series.name || "";
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

function renderCurrentBookCard(item) {
  return `
    <article class="compare-card source-card" ${cssCoverStyle(item.coverUrl, "source")}>
      <div class="card-label">ABS ITEM</div>
      <div class="candidate-title">${escapeHtml(item.title || "Untitled")}</div>
      <div class="meta">${escapeHtml(item.author || "Unknown author")}</div>
      <div class="source-score">Current Metadata</div>
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

function renderCandidateCard(scored, rowIndex, candidateIndex) {
  const candidate = scored.candidate || {};
  const checked = candidateIndex === 0 ? "checked" : "";
  const meta = [candidateSeries(candidate), candidateNarrator(candidate), candidate.publishedYear].filter(Boolean).join(" | ");
  const author = candidateAuthor(candidate) || "Unknown author";
  const authorInfo = authorMatchInfo(scored);
  return `
    <article class="compare-card candidate-card ${authorInfo.matches ? "author-match-card" : ""}" ${cssCoverStyle(candidate.cover)}>
      <label class="candidate-pick">
        <input type="radio" name="review-${rowIndex}" value="${candidateIndex}" ${checked} />
        <span>Use This</span>
      </label>
      ${scored.searchSource === "manual" ? `<div class="candidate-origin">Manual search · ${escapeHtml(scored.searchProvider || "provider")}</div>` : ""}
      <div class="candidate-title">${escapeHtml(candidate.title || "Untitled")}</div>
      <div class="candidate-author ${authorInfo.matches ? "author-match" : ""}">
        <span>${escapeHtml(author)}</span>
        ${authorInfo.matches ? `<strong>Author match ${Math.round(authorInfo.score)}</strong>` : `<em>Author ${Math.round(authorInfo.score)}</em>`}
      </div>
      <div class="match-score"><span class="score">${scored.score}</span> match score</div>
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

function reviewSearchState(row) {
  if (!row.manualSearch) {
    row.manualSearch = {
      title: row.item?.title || "",
      author: row.item?.author || "",
      provider: $("provider")?.value || appState.settings?.connection?.provider || "audible",
      limit: 20,
      open: false,
      loading: false,
      message: "",
      error: "",
    };
  }
  return row.manualSearch;
}

function renderReviewSearch(row, rowIndex) {
  const search = reviewSearchState(row);
  const options = REVIEW_PROVIDERS.map(
    (provider) => `<option value="${provider}"${provider === search.provider ? " selected" : ""}>${provider}</option>`,
  ).join("");
  const feedback = search.error
    ? `<div class="review-search-feedback error">${escapeHtml(search.error)}</div>`
    : search.message
      ? `<div class="review-search-feedback success">${escapeHtml(search.message)}</div>`
      : "";
  return `
    <details class="review-search"${search.open ? " open" : ""}>
      <summary>Search for another match</summary>
      <p class="meta">Simplify the title, clear the author, or switch providers to broaden the Audiobookshelf metadata search. New results are added ahead of the original suggestions.</p>
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
        <button type="submit"${search.loading ? " disabled" : ""}>${search.loading ? "Searching…" : "Search ABS"}</button>
      </form>
      ${feedback}
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

function renderReviewDesk() {
  const root = $("reviewDesk");
  const rows = appState.reviewRows || [];
  if (!rows.length) {
    root.innerHTML = '<div class="note">No review items loaded. Scan review tags or run automatch to build this queue.</div>';
    return;
  }
  root.innerHTML = rows
    .map((row, rowIndex) => {
      const item = row.item || {};
      const candidates = row.candidates || [];
      return `
        <article class="review-item">
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
          ${renderReviewSearch(row, rowIndex)}
          <div class="compare-grid">
            ${renderCurrentBookCard(item)}
            ${
              candidates.length
                ? candidates.map((scored, candidateIndex) => renderCandidateCard(scored, rowIndex, candidateIndex)).join("")
                : '<div class="note">No candidates came back for this item. Reject it or adjust search settings and scan again.</div>'
            }
          </div>
        </article>
      `;
    })
    .join("");
}

async function scanReview() {
  const settings = getSettingsFromForm();
  const payload = await api("/api/review/scan", {
    method: "POST",
    body: JSON.stringify({ settings, limit: settings.review.scanLimit, ...tokenPayload() }),
  });
  appState.reviewRows = payload.review.rows || [];
  renderReviewDesk();
  showToast(`Loaded ${appState.reviewRows.length} review item(s)`);
}

function loadJobReviewQueue() {
  const rows = appState.job?.reviewQueue || [];
  appState.reviewRows = rows;
  renderReviewDesk();
  showToast(`Loaded ${rows.length} item(s) from the current job queue`);
}

async function approveReview(rowIndex) {
  const row = appState.reviewRows[rowIndex];
  if (!row) return;
  const selected = document.querySelector(`input[name="review-${rowIndex}"]:checked`);
  const candidateIndex = Number(selected?.value || 0);
  const candidate = row.candidates?.[candidateIndex];
  if (!candidate) {
    showToast("No candidate selected");
    return;
  }
  const confirmed = window.confirm(`Approve this match for "${row.item.title}"? This writes to Audiobookshelf.`);
  if (!confirmed) return;
  appState.reviewRows.splice(rowIndex, 1);
  renderReviewDesk();
  try {
    await api("/api/review/approve", {
      method: "POST",
      body: JSON.stringify({ settings: getSettingsFromForm(), itemId: row.item.id, candidate, row, ...tokenPayload() }),
    });
    showToast("Review match approved");
  } catch (error) {
    appState.reviewRows.splice(rowIndex, 0, row);
    renderReviewDesk();
    throw error;
  }
}

async function rejectReview(rowIndex) {
  const row = appState.reviewRows[rowIndex];
  if (!row) return;
  appState.reviewRows.splice(rowIndex, 1);
  renderReviewDesk();
  try {
    await api("/api/review/reject", {
      method: "POST",
      body: JSON.stringify({ settings: getSettingsFromForm(), itemId: row.item.id, row, ...tokenPayload() }),
    });
    showToast("Review item rejected");
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
  search.limit = Number(formData.get("limit") || 20);
  search.open = true;
  search.loading = true;
  search.message = "";
  search.error = "";
  renderReviewDesk();
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
    search.message = candidates.length
      ? `Added ${candidates.length} result(s) from ${search.provider}. Select a card below, then approve it.`
      : `No results from ${search.provider}. Try fewer title words, clear the author, or choose another provider.`;
    showToast(candidates.length ? `Found ${candidates.length} additional candidate(s)` : "No additional candidates found");
  } catch (error) {
    search.error = `${error.message}. Try broader terms or another provider.`;
    throw error;
  } finally {
    search.loading = false;
    renderReviewDesk();
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
}

async function saveSettings() {
  const payload = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ settings: getSettingsFromForm(), ...tokenPayload() }),
  });
  setForm(payload.settings);
  showToast("Settings saved");
}

async function loadFilterData() {
  const libraryId = $("libraryId").value;
  if (!libraryId) {
    showToast("Select a library first");
    return;
  }
  const payload = await api(`/api/filter-data?libraryId=${encodeURIComponent(libraryId)}`);
  populateFilterData(payload.filterData);
}

async function preview() {
  const payload = await api("/api/preview", {
    method: "POST",
    body: JSON.stringify({ settings: getSettingsFromForm(), limit: 10, ...tokenPayload() }),
  });
  renderPreview(payload.preview);
  showToast("Preview loaded");
}

async function startJob() {
  const settings = getSettingsFromForm();
  if (!settings.run.dryRun) {
    const confirmed = window.confirm("This run will write metadata/tags to Audiobookshelf. Start anyway?");
    if (!confirmed) return;
  }
  const payload = await api("/api/job/start", {
    method: "POST",
    body: JSON.stringify({ settings, ...tokenPayload() }),
  });
  renderJob(payload.job);
  startPolling();
  showToast("Job started");
}

async function jobAction(action) {
  const payload = await api(`/api/job/${action}`, { method: "POST", body: "{}" });
  renderJob(payload.job);
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
    return;
  }
  const blob = new Blob([JSON.stringify(job, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `absidekick-job-${job.id}.json`;
  link.click();
  URL.revokeObjectURL(url);
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
  $("connectBtn").addEventListener("click", () => connect().catch((error) => showToast(error.message)));
  $("saveSettingsBtn").addEventListener("click", () => saveSettings().catch((error) => showToast(error.message)));
  $("loadFiltersBtn").addEventListener("click", () => loadFilterData().catch((error) => showToast(error.message)));
  $("previewBtn").addEventListener("click", () => preview().catch((error) => showToast(error.message)));
  $("startBtn").addEventListener("click", () => startJob().catch((error) => showToast(error.message)));
  $("pauseBtn").addEventListener("click", () => jobAction("pause").catch((error) => showToast(error.message)));
  $("resumeBtn").addEventListener("click", () => jobAction("resume").catch((error) => showToast(error.message)));
  $("cancelBtn").addEventListener("click", () => jobAction("cancel").catch((error) => showToast(error.message)));
  $("exportBtn").addEventListener("click", exportLog);
  $("scanReviewBtn").addEventListener("click", () => scanReview().catch((error) => showToast(error.message)));
  $("loadJobReviewBtn").addEventListener("click", loadJobReviewQueue);
  $("reviewDesk").addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-action]");
    if (!button) return;
    const row = Number(button.dataset.row);
    if (button.dataset.reviewAction === "approve") {
      approveReview(row).catch((error) => showToast(error.message));
    }
    if (button.dataset.reviewAction === "reject") {
      rejectReview(row).catch((error) => showToast(error.message));
    }
  });
  $("reviewDesk").addEventListener("submit", (event) => {
    const form = event.target.closest("[data-review-search-form]");
    if (!form) return;
    event.preventDefault();
    searchReview(Number(form.dataset.row), form).catch((error) => showToast(error.message));
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
