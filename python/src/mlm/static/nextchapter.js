// NextChapter Frontend Controller

document.addEventListener("DOMContentLoaded", () => {
  // 1. Filter buttons in Series Hub
  const filterBtns = document.querySelectorAll("[data-filter-series]");
  filterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      filterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const filter = btn.getAttribute("data-filter-series");
      const cards = document.querySelectorAll(".series-card");
      cards.forEach(card => {
        const status = card.getAttribute("data-status");
        if (filter === "all" || status === filter) {
          card.style.display = "block";
        } else {
          card.style.display = "none";
        }
      });
    });
  });

  // 2. Scan Series Button
  const scanBtn = document.querySelector("[data-nc-action='scan']");
  if (scanBtn) {
    scanBtn.addEventListener("click", async () => {
      const originalText = scanBtn.innerHTML;
      scanBtn.disabled = true;
      scanBtn.innerHTML = '<span class="spin">⟳</span> Scanning Library...';
      try {
        const res = await fetch("/api/nextchapter/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force: true })
        });
        const data = await res.json();
        if (data.ok || data.series) {
          window.location.reload();
        } else {
          alert("Scan completed with warning: " + (data.error || "Unknown error"));
          window.location.reload();
        }
      } catch (err) {
        alert("Scan failed: " + err.message);
        scanBtn.disabled = false;
        scanBtn.innerHTML = originalText;
      }
    });
  }

  // 3. Refresh Recommendations Button
  const refreshRecsBtn = document.querySelector("[data-nc-action='refresh-recs']");
  if (refreshRecsBtn) {
    refreshRecsBtn.addEventListener("click", async () => {
      const originalText = refreshRecsBtn.innerHTML;
      refreshRecsBtn.disabled = true;
      refreshRecsBtn.innerHTML = '<span class="spin">⟳</span> Consulting AI...';
      try {
        const res = await fetch("/api/nextchapter/recommendations/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force: true })
        });
        const data = await res.json();
        if (data.ok || Array.isArray(data)) {
          window.location.reload();
        } else {
          alert("Refresh response: " + (data.error || "No recommendations generated."));
          window.location.reload();
        }
      } catch (err) {
        alert("Failed to refresh recommendations: " + err.message);
        refreshRecsBtn.disabled = false;
        refreshRecsBtn.innerHTML = originalText;
      }
    });
  }

  // 4. Single Download Queue Trigger or Open MAM Browser Modal
  document.querySelectorAll("[data-nc-download]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const mamId = btn.getAttribute("data-mam-id");
      const series = btn.getAttribute("data-series") || "";
      const volume = btn.getAttribute("data-volume") || "";

      if (!mamId) {
        const title = btn.getAttribute("data-title") || "";
        const author = btn.getAttribute("data-author") || "";
        openCandidateModal(series, volume, title, author, "", "smart");
        return;
      }

      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.innerHTML = '<span class="spin">⟳</span> Queuing...';

      try {
        const res = await fetch("/api/nextchapter/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mam_id: parseInt(mamId, 10), series_name: series, volume: volume })
        });
        const data = await res.json();
        if (data.ok) {
          btn.innerHTML = "✓ Queued";
          btn.classList.remove("btn-primary-nc");
          btn.classList.add("btn-outline-nc");
          showToast(`Queued #${mamId} for download: ${data.title || series}`);
        } else {
          alert("Error queuing download: " + (data.error || "Unknown"));
          btn.disabled = false;
          btn.innerHTML = originalText;
        }
      } catch (err) {
        alert("Queue failed: " + err.message);
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  });

  // 4b. Browse Series / Author on MAM Buttons
  document.querySelectorAll("[data-nc-browse-mam]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const series = btn.getAttribute("data-series") || "";
      const author = btn.getAttribute("data-author") || "";
      const mode = btn.getAttribute("data-mode") || "series";
      openCandidateModal(series, "", "", author, "", mode);
    });
  });

  // 5. Batch Download All Missing for a Series
  document.querySelectorAll("[data-nc-queue-all]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const series = btn.getAttribute("data-series");
      if (!confirm(`Queue all detected missing books for '${series}' from MAM?`)) return;

      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.innerHTML = '<span class="spin">⟳</span> Queuing all...';

      try {
        const res = await fetch("/api/nextchapter/series/download-all", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ series_name: series })
        });
        const data = await res.json();
        if (data.ok) {
          btn.innerHTML = `✓ Queued ${data.queued_count} Books`;
          showToast(`Successfully queued ${data.queued_count} missing book(s) for '${series}'!`);
        } else {
          alert("Queue all failed: " + (data.error || "Unknown error"));
          btn.disabled = false;
          btn.innerHTML = originalText;
        }
      } catch (err) {
        alert("Failed to queue series: " + err.message);
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  });

  // Modal helper for candidates with Series, Author, and Smart search modes
  async function openCandidateModal(seriesName, volume, title, author, customQ, initialMode = "smart") {
    let modal = document.getElementById("nc-candidate-modal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "nc-candidate-modal";
      modal.className = "nc-modal-overlay";
      modal.innerHTML = `
        <div class="nc-modal-container" style="max-width: 860px;">
          <div class="nc-modal-header">
            <h3 class="nc-modal-title" id="nc-modal-title"><span>📚</span> Available MAM Releases</h3>
            <button id="nc-modal-close" class="nc-modal-close" title="Close">&times;</button>
          </div>
          <div id="nc-modal-search-box" style="padding: 0.85rem 1.25rem; border-bottom: 1px solid rgba(255, 255, 255, 0.08); background: rgba(15, 23, 42, 0.65);">
            <form id="nc-modal-search-form" style="display: flex; gap: 0.5rem; margin: 0 0 0.7rem 0;">
              <input type="text" id="nc-modal-search-input" placeholder="Search MAM by title, author, series, or #volume..." style="flex: 1; padding: 0.55rem 0.85rem; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #f8fafc; font-size: 0.9rem;" />
              <button type="submit" class="button btn-primary-nc" style="padding: 0.5rem 1.1rem; white-space: nowrap; font-size: 0.875rem;">Search MAM</button>
            </form>
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
              <div id="nc-modal-mode-tabs" style="display: flex; gap: 0.4rem; flex-wrap: wrap;">
                <button type="button" class="btn btn-sm btn-outline-nc" data-nc-modal-mode="smart">🎯 Best Matches</button>
                <button type="button" class="btn btn-sm btn-outline-nc" data-nc-modal-mode="series">📚 All in Series</button>
                <button type="button" class="btn btn-sm btn-outline-nc" data-nc-modal-mode="author">✍️ All by Author</button>
              </div>
              <div id="nc-modal-media-pills" style="display: flex; gap: 0.35rem; align-items: center;">
                <span style="font-size: 0.75rem; color: #94a3b8; margin-right: 0.15rem;">Format:</span>
                <button type="button" class="btn btn-sm btn-outline-nc" data-nc-modal-media="" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;">All</button>
                <button type="button" class="btn btn-sm btn-outline-nc" data-nc-modal-media="audiobook" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;">🎧 Audio</button>
                <button type="button" class="btn btn-sm btn-outline-nc" data-nc-modal-media="ebook" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;">📖 eBook</button>
              </div>
            </div>
          </div>
          <div id="nc-modal-body" class="nc-modal-body">
            <p style="color: #94a3b8;">Searching MyAnonamouse for matching releases...</p>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
      document.getElementById("nc-modal-close").addEventListener("click", () => {
        modal.style.display = "none";
      });
      modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.style.display = "none";
      });
    }

    let state = {
      seriesName: seriesName || "",
      volume: volume || "",
      title: title || "",
      author: author || "",
      mode: initialMode || "smart",
      mediaFilter: "",
      q: customQ || ""
    };

    function buildDefaultQuery() {
      if (state.mode === "series") {
        return state.seriesName || state.title || state.author;
      }
      if (state.mode === "author") {
        return state.author || state.seriesName || state.title;
      }
      if (state.title) {
        return state.author ? `${state.author} ${state.title}` : state.title;
      }
      if (state.seriesName && state.volume) {
        return `${state.seriesName} #${state.volume}`;
      }
      return state.seriesName || state.author || "";
    }

    if (!state.q) {
      state.q = buildDefaultQuery();
    }

    modal.style.display = "flex";
    const searchInput = document.getElementById("nc-modal-search-input");
    searchInput.value = state.q;

    function syncControls() {
      let headerLabel = "";
      if (state.mode === "series") {
        headerLabel = `All in Series: ${state.seriesName || state.q}`;
      } else if (state.mode === "author") {
        headerLabel = `All by Author: ${state.author || state.seriesName || state.q}`;
      } else if (state.mode === "custom") {
        headerLabel = `Custom Search: ${state.q}`;
      } else {
        const volPart = state.volume ? ` #${state.volume}` : "";
        const titlePart = state.title ? ` — ${state.title}` : "";
        headerLabel = `${state.seriesName || state.author}${volPart}${titlePart}`;
      }
      document.getElementById("nc-modal-title").innerHTML = `<span>📚</span> MAM Releases: <span style="color: #818cf8; margin-left: 4px;">${escapeHtml(headerLabel)}</span>`;

      document.querySelectorAll("[data-nc-modal-mode]").forEach(btn => {
        const m = btn.getAttribute("data-nc-modal-mode");
        const isActive = (m === state.mode) || (m === "smart" && state.mode === "custom");
        btn.classList.toggle("btn-primary-nc", isActive);
        btn.classList.toggle("btn-outline-nc", !isActive);
        if (m === "series") {
          btn.innerHTML = state.seriesName ? `📚 All in Series (${escapeHtml(truncateText(state.seriesName, 22))})` : "📚 All in Series";
        } else if (m === "author") {
          const authLabel = state.author || state.seriesName;
          btn.innerHTML = authLabel ? `✍️ All by Author (${escapeHtml(truncateText(authLabel, 22))})` : "✍️ All by Author";
        }
      });

      document.querySelectorAll("[data-nc-modal-media]").forEach(btn => {
        const mf = btn.getAttribute("data-nc-modal-media") || "";
        const isActive = mf === state.mediaFilter;
        btn.classList.toggle("btn-primary-nc", isActive);
        btn.classList.toggle("btn-outline-nc", !isActive);
      });
    }

    document.querySelectorAll("[data-nc-modal-mode]").forEach(btn => {
      btn.onclick = () => {
        const nextMode = btn.getAttribute("data-nc-modal-mode") || "smart";
        state.mode = nextMode;
        state.q = buildDefaultQuery();
        searchInput.value = state.q;
        syncControls();
        runSearch();
      };
    });

    document.querySelectorAll("[data-nc-modal-media]").forEach(btn => {
      btn.onclick = () => {
        state.mediaFilter = btn.getAttribute("data-nc-modal-media") || "";
        syncControls();
        runSearch();
      };
    });

    const searchForm = document.getElementById("nc-modal-search-form");
    searchForm.onsubmit = (e) => {
      e.preventDefault();
      state.q = searchInput.value.trim();
      state.mode = "custom";
      syncControls();
      runSearch();
    };

    syncControls();
    await runSearch();

    async function runSearch() {
      const body = document.getElementById("nc-modal-body");
      const modeDesc = state.mode === "series"
        ? `Fetching all volumes in series "${state.seriesName || state.q}" on MyAnonamouse...`
        : state.mode === "author"
          ? `Fetching full bibliography for author "${state.author || state.seriesName || state.q}" on MyAnonamouse...`
          : `Searching MyAnonamouse for "${state.q}"...`;

      body.innerHTML = `
        <div style="text-align: center; padding: 2.5rem 1rem;">
          <p style="color: #94a3b8; font-size: 0.95rem; margin-bottom: 0.5rem;"><span class="spin">⟳</span> ${escapeHtml(modeDesc)}</p>
          <small style="color: #64748b;">Querying title, author, and series indexes on MAM &bull; Matching canonical editions</small>
        </div>
      `;

      const params = new URLSearchParams({
        series_name: state.seriesName || "",
        volume: state.volume || "",
        title: state.title || "",
        author: state.author || "",
        q: state.q || "",
        mode: state.mode || "smart",
        media_filter: state.mediaFilter || "",
        limit: "50"
      });

      try {
        const res = await fetch(`/api/nextchapter/search-mam?${params.toString()}`);
        const candidates = await res.json();
        if (!candidates || candidates.length === 0) {
          body.innerHTML = `
            <div style="text-align: center; padding: 2.5rem 1rem;">
              <p style="color: #f8fafc; font-weight: 600; font-size: 1rem; margin-bottom: 0.5rem;">No matching releases found for this view</p>
              <p style="color: #94a3b8; font-size: 0.85rem; max-width: 520px; margin: 0 auto 1.25rem;">
                We searched for "${escapeHtml(state.q)}". Try switching to <strong>📚 All in Series</strong> or <strong>✍️ All by Author</strong> below to inspect every related torrent on MAM:
              </p>
              <div style="display: flex; gap: 0.6rem; justify-content: center; flex-wrap: wrap;">
                ${state.seriesName ? `<button type="button" class="button btn-primary-nc" data-nc-empty-mode="series">📚 Browse All in Series (${escapeHtml(state.seriesName)})</button>` : ""}
                ${(state.author || state.seriesName) ? `<button type="button" class="button btn-outline-nc" data-nc-empty-mode="author">✍️ Browse All by Author (${escapeHtml(state.author || state.seriesName)})</button>` : ""}
                <a href="https://www.myanonamouse.net/tor/browse.php?tor[text]=${encodeURIComponent(state.q)}" target="_blank" rel="noreferrer" class="button btn-outline-nc" style="display: inline-flex;">Open on MAM ↗</a>
              </div>
            </div>
          `;
          body.querySelectorAll("[data-nc-empty-mode]").forEach(fbBtn => {
            fbBtn.addEventListener("click", () => {
              state.mode = fbBtn.getAttribute("data-nc-empty-mode") || "author";
              state.q = buildDefaultQuery();
              searchInput.value = state.q;
              syncControls();
              runSearch();
            });
          });
          return;
        }

        let html = `
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; font-size: 0.8rem; color: #94a3b8;">
            <span>Showing <strong>${candidates.length}</strong> release${candidates.length === 1 ? "" : "s"} on MyAnonamouse ${state.mode === "series" ? "(sorted by series volume)" : state.mode === "author" ? "(grouped by series &amp; volume)" : "(ranked by match relevance)"}</span>
            <span>Click any <strong>Author</strong> or <strong>MAM Series</strong> to pivot</span>
          </div>
          <div style="display: flex; flex-direction: column; gap: 0.9rem;">
        `;
        candidates.forEach(c => {
          const authorsList = (c.authors && c.authors.length) ? c.authors : (state.author ? [state.author] : []);
          const primaryAuthor = authorsList.length ? authorsList[0] : "";
          const authorText = authorsList.length ? authorsList.join(", ") : "Unknown Author";
          const narratorsList = c.narrators || [];
          const narratorText = narratorsList.length ? narratorsList.join(", ") : "";

          const filetypesList = c.filetypes || [];
          const formatBadges = filetypesList.length
            ? filetypesList.map(ft => `<span class="format-badge ${escapeHtml(ft.toLowerCase())}">${escapeHtml(ft)}</span>`).join(" ")
            : `<span class="format-badge unknown">${escapeHtml((c.media_type || "UNKNOWN").toUpperCase())}</span>`;

          const isAudio = (c.media_type || "").toLowerCase().includes("audio");
          const mediaLabel = isAudio ? "🎧 Audiobook" : "📖 eBook";

          const costPills = [];
          if (c.free || c.personal_freeleech) {
            costPills.push('<span class="status-pill free">FREELEECH</span>');
          } else if (c.vip) {
            costPills.push('<span class="status-pill vip">VIP</span>');
          } else {
            costPills.push('<span class="status-pill ratio">RATIO</span>');
          }

          const matchReasons = (c.match_reasons || []).map(reason =>
            `<span class="chip" style="background: rgba(56, 189, 248, 0.14); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); font-size: 0.72rem; font-weight: 600;">${escapeHtml(reason)}</span>`
          ).join(" ");

          const inLibraryBadge = c.in_library
            ? `<span class="chip" style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.45); font-weight: 600;">✓ In Library / Queue</span>`
            : "";

          const seedersNum = parseInt(c.seeders, 10) || 0;
          const leechersNum = parseInt(c.leechers, 10) || 0;
          const seedColor = seedersNum > 5 ? "#34d399" : (seedersNum > 0 ? "#38bdf8" : "#fb7185");
          const mamSeriesPrimary = c.series_name_mam || "";

          html += `
            <div class="candidate-card" style="${c.in_library ? 'border-color: rgba(16, 185, 129, 0.35);' : ''}">
              <div class="candidate-header-row" style="display: flex; justify-content: space-between; align-items: flex-start; gap: 0.5rem; flex-wrap: wrap;">
                <a href="https://www.myanonamouse.net/t/${c.mam_id}" target="_blank" rel="noreferrer" class="candidate-title" title="Open release #${c.mam_id} on MyAnonamouse">
                  ${escapeHtml(c.title)} <span style="font-size: 0.8rem; opacity: 0.7;">↗</span>
                </a>
                <div style="display: flex; gap: 0.35rem; flex-wrap: wrap;">
                  ${inLibraryBadge}
                  ${matchReasons}
                </div>
              </div>

              <div class="candidate-meta-grid">
                <div class="candidate-meta-item">
                  <span class="candidate-meta-label">Author:</span>
                  ${primaryAuthor ? `
                    <button type="button" data-nc-pivot-author="${escapeHtml(primaryAuthor)}" style="background: none; border: none; padding: 0; color: #38bdf8; font-weight: 600; cursor: pointer; text-decoration: underline; font-size: 0.85rem; text-align: left;" title="Browse all torrents by ${escapeHtml(primaryAuthor)} on MAM">
                      ✍️ ${escapeHtml(authorText)}
                    </button>
                  ` : `<span class="candidate-meta-value" style="color: #38bdf8;">${escapeHtml(authorText)}</span>`}
                </div>
                <div class="candidate-meta-item">
                  <span class="candidate-meta-label">Format:</span>
                  <div>${formatBadges}</div>
                </div>
                <div class="candidate-meta-item">
                  <span class="candidate-meta-label">Size:</span>
                  <span class="candidate-meta-value">${escapeHtml(c.size || "Unknown")}</span>
                  ${c.num_files ? `<span style="color: #64748b; font-size: 0.75rem;">(${c.num_files} files)</span>` : ""}
                </div>
                <div class="candidate-meta-item">
                  <span class="candidate-meta-label">Language:</span>
                  <span class="candidate-meta-value">${escapeHtml(c.language || "English")}</span>
                </div>
                ${c.series_display ? `
                <div class="candidate-meta-item" style="grid-column: 1 / -1;">
                  <span class="candidate-meta-label">MAM Series:</span>
                  <button type="button" data-nc-pivot-series="${escapeHtml(mamSeriesPrimary || c.series_display)}" style="background: none; border: none; padding: 0; color: #a5b4fc; font-weight: 600; cursor: pointer; text-decoration: underline; font-size: 0.85rem; text-align: left;" title="Browse all torrents in series '${escapeHtml(mamSeriesPrimary || c.series_display)}' on MAM">
                    📚 ${escapeHtml(c.series_display)} (Browse Series)
                  </button>
                </div>
                ` : `
                <div class="candidate-meta-item" style="grid-column: 1 / -1;">
                  <span class="candidate-meta-label">MAM Series:</span>
                  <span class="candidate-meta-value" style="color: #64748b; font-style: italic;">None tagged on MAM</span>
                </div>
                `}
                ${narratorText ? `
                <div class="candidate-meta-item" style="grid-column: 1 / -1;">
                  <span class="candidate-meta-label">Narrator:</span>
                  <span class="candidate-meta-value" style="color: #c084fc;">${escapeHtml(narratorText)}</span>
                </div>
                ` : ""}
              </div>

              <div class="candidate-pills-row">
                <div class="candidate-badges">
                  <span class="chip" style="font-weight: 600;">${mediaLabel}</span>
                  ${costPills.join(" ")}
                  <span class="chip" style="color: ${seedColor}; font-weight: 600;">▲ ${seedersNum} seeders</span>
                  <span class="chip" style="color: #94a3b8;">▼ ${leechersNum} leechers</span>
                  ${c.series_display ? `
                    <button type="button" data-nc-pivot-series="${escapeHtml(mamSeriesPrimary || c.series_display)}" class="chip" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc; border: 1px solid rgba(99, 102, 241, 0.4); font-weight: 600; cursor: pointer;" title="Click to view all torrents in this MAM series">
                      📚 ${escapeHtml(c.series_display)}
                    </button>
                  ` : ""}
                </div>
                <button class="btn btn-sm ${c.in_library ? 'btn-outline-nc' : 'btn-primary-nc'}" data-modal-queue="${c.mam_id}" data-series="${escapeHtml(mamSeriesPrimary || state.seriesName)}" data-vol="${escapeHtml(c.series_volume || state.volume)}" style="padding: 0.45rem 1rem; font-weight: 600;">
                  ${c.in_library ? "Queue Again" : "1-Click Queue"}
                </button>
              </div>
            </div>
          `;
        });
        html += `</div>`;
        body.innerHTML = html;

        // Wire Author Pivot buttons inside cards
        body.querySelectorAll("[data-nc-pivot-author]").forEach(aBtn => {
          aBtn.addEventListener("click", () => {
            const targetAuthor = aBtn.getAttribute("data-nc-pivot-author") || "";
            if (!targetAuthor) return;
            state.author = targetAuthor;
            state.mode = "author";
            state.q = targetAuthor;
            searchInput.value = state.q;
            syncControls();
            runSearch();
          });
        });

        // Wire Series Pivot buttons inside cards
        body.querySelectorAll("[data-nc-pivot-series]").forEach(sBtn => {
          sBtn.addEventListener("click", () => {
            let targetSeries = sBtn.getAttribute("data-nc-pivot-series") || "";
            targetSeries = targetSeries.replace(/\s*#[\d.]+.*$/, "").trim();
            if (!targetSeries) return;
            state.seriesName = targetSeries;
            state.mode = "series";
            state.q = targetSeries;
            searchInput.value = state.q;
            syncControls();
            runSearch();
          });
        });

        // Wire Queue buttons inside cards
        body.querySelectorAll("[data-modal-queue]").forEach(qBtn => {
          qBtn.addEventListener("click", async () => {
            const id = qBtn.getAttribute("data-modal-queue");
            const qSeries = qBtn.getAttribute("data-series") || state.seriesName;
            const qVol = qBtn.getAttribute("data-vol") || state.volume;
            qBtn.disabled = true;
            qBtn.innerHTML = '<span class="spin">⟳</span> Queuing...';
            const qRes = await fetch("/api/nextchapter/download", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ mam_id: parseInt(id, 10), series_name: qSeries, volume: qVol })
            });
            const qData = await qRes.json();
            if (qData.ok) {
              qBtn.innerHTML = "✓ Queued";
              qBtn.classList.remove("btn-primary-nc");
              qBtn.classList.add("btn-outline-nc");
              showToast(`Queued #${id} successfully!`);
            } else {
              alert("Error: " + (qData.error || "Failed to queue"));
              qBtn.disabled = false;
              qBtn.innerHTML = "1-Click Queue";
            }
          });
        });
      } catch (err) {
        body.innerHTML = `<p style="color: #fb7185;">Search error: ${escapeHtml(err.message)}</p>`;
      }
    }
  }

  function truncateText(str, maxLen) {
    if (!str) return "";
    const s = String(str);
    return s.length > maxLen ? s.slice(0, maxLen - 1) + "…" : s;
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function showToast(message) {
    let toast = document.getElementById("nc-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "nc-toast";
      toast.style.position = "fixed";
      toast.style.bottom = "20px";
      toast.style.right = "20px";
      toast.style.backgroundColor = "#0284c7";
      toast.style.color = "#ffffff";
      toast.style.padding = "12px 20px";
      toast.style.borderRadius = "6px";
      toast.style.boxShadow = "0 10px 15px -3px rgba(0, 0, 0, 0.5)";
      toast.style.zIndex = "10000";
      toast.style.fontWeight = "600";
      toast.style.transition = "opacity 0.3s ease";
      document.body.appendChild(toast);
    }
    toast.innerText = message;
    toast.style.opacity = "1";
    toast.style.display = "block";
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => { toast.style.display = "none"; }, 300);
    }, 3500);
  }
});
