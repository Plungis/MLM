// NextChapter Frontend Controller

document.addEventListener("DOMContentLoaded", () => {
  let qbitPollTimer = null;

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

  // 1b. Filter buttons & search input in Missing Volumes view
  const missingFilterBtns = document.querySelectorAll("[data-filter-missing]");
  const missingSearchInput = document.getElementById("nc-missing-filter-input");
  let activeMissingFilter = "all";

  function applyMissingFilters() {
    const q = (missingSearchInput ? missingSearchInput.value : "").trim().toLowerCase();
    document.querySelectorAll("#nc-missing-grid [data-vol-card]").forEach(card => {
      const st = card.getAttribute("data-status") || "missing";
      const text = (
        (card.getAttribute("data-series") || "") + " " +
        (card.getAttribute("data-title") || "") + " " +
        (card.getAttribute("data-author") || "") + " #" +
        (card.getAttribute("data-volume") || "")
      ).toLowerCase();
      const statusMatch = activeMissingFilter === "all" || st === activeMissingFilter;
      const textMatch = !q || text.includes(q);
      card.style.display = (statusMatch && textMatch) ? "flex" : "none";
    });
  }

  missingFilterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      missingFilterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeMissingFilter = btn.getAttribute("data-filter-missing") || "all";
      applyMissingFilters();
    });
  });
  if (missingSearchInput) {
    missingSearchInput.addEventListener("input", applyMissingFilters);
  }

  // 1c. Filter buttons & search input in Revisit & Watchlist view
  const revisitFilterBtns = document.querySelectorAll("[data-filter-revisit]");
  const revisitSearchInput = document.getElementById("nc-revisit-filter-input");
  let activeRevisitFilter = "all";
  revisitFilterBtns.forEach(btn => {
    if (btn.classList.contains("active")) {
      activeRevisitFilter = btn.getAttribute("data-filter-revisit") || "all";
    }
  });

  function applyRevisitFilters() {
    const q = (revisitSearchInput ? revisitSearchInput.value : "").trim().toLowerCase();
    document.querySelectorAll("[data-revisit-card]").forEach(card => {
      const st = card.getAttribute("data-status") || "";
      const text = (
        (card.getAttribute("data-series") || "") + " " +
        (card.getAttribute("data-title") || "") + " " +
        (card.getAttribute("data-author") || "") + " " +
        (card.getAttribute("data-reason") || "") + " " +
        (card.getAttribute("data-notes") || "")
      ).toLowerCase();
      const statusMatch = activeRevisitFilter === "all" || st === activeRevisitFilter;
      const textMatch = !q || text.includes(q);
      card.style.display = (statusMatch && textMatch) ? "flex" : "none";
    });
  }

  revisitFilterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      revisitFilterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeRevisitFilter = btn.getAttribute("data-filter-revisit") || "all";
      applyRevisitFilters();
    });
  });
  if (revisitSearchInput) {
    revisitSearchInput.addEventListener("input", applyRevisitFilters);
  }

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

  // 2b. Sync qBittorrent Button
  const syncQbitBtn = document.querySelector("[data-nc-action='refresh-qbit']");
  if (syncQbitBtn) {
    syncQbitBtn.addEventListener("click", async () => {
      const originalText = syncQbitBtn.innerHTML;
      syncQbitBtn.disabled = true;
      syncQbitBtn.innerHTML = '<span class="spin">⟳</span> Syncing qBittorrent...';
      await syncQbitStatus(true);
      syncQbitBtn.disabled = false;
      syncQbitBtn.innerHTML = originalText;
    });
  }

  // 2c. Re-Check All Watchlist on MAM Button
  document.querySelectorAll("[data-nc-action='recheck-all']").forEach(btn => {
    btn.addEventListener("click", async () => {
      const originalText = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spin">⟳</span> Checking MAM Availability...';
      try {
        const res = await fetch("/api/nextchapter/dispositions/recheck", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.ok) {
          showToast(
            data.found_count > 0
              ? `✨ Found MAM releases for ${data.found_count} of ${data.checked_count} watchlist book(s)!`
              : `Checked ${data.checked_count} book(s) on MAM — no new exact matches yet.`
          );
          setTimeout(() => window.location.reload(), 700);
        } else {
          btn.disabled = false;
          btn.innerHTML = originalText;
        }
      } catch (err) {
        alert("Failed to re-check MAM: " + err.message);
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  });

  // 2d. Single Re-Check on MAM Button in Revisit Hub
  document.querySelectorAll("[data-nc-recheck-one]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const dKey = btn.getAttribute("data-nc-recheck-one") || "";
      const originalText = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spin">⟳</span> Checking...';
      try {
        const res = await fetch("/api/nextchapter/dispositions/recheck", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ disposition_key: dKey })
        });
        const data = await res.json();
        if (data.ok) {
          if (data.found_count > 0) {
            showToast("✨ Matching release(s) found on MAM!");
            setTimeout(() => window.location.reload(), 500);
          } else {
            btn.innerHTML = "0 Matches";
            showToast("Checked MAM — still 0 exact matches for this volume.");
            setTimeout(() => {
              btn.disabled = false;
              btn.innerHTML = originalText;
            }, 2000);
          }
        }
      } catch (err) {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  });

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
      const title = btn.getAttribute("data-title") || "";
      const author = btn.getAttribute("data-author") || "";

      if (!mamId) {
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
          body: JSON.stringify({
            mam_id: parseInt(mamId, 10),
            series_name: series,
            volume: volume,
            title: title
          })
        });
        const data = await res.json();
        if (data.ok) {
          btn.innerHTML = "✓ Queued in qBittorrent";
          btn.classList.remove("btn-primary-nc");
          btn.classList.add("btn-queued-nc");
          markCardQueuedInDOM({
            disposition_key: data.disposition_key || "",
            series_name: series,
            volume_number: volume,
            title: title,
            mam_id: parseInt(mamId, 10),
            queued_title: data.title || title || series,
            qbit_state_label: "Queued • Sending to qBittorrent...",
            qbit_progress: 0
          });
          if (data.counts) updatePageCounts(data.counts);
          showToast(`✓ Queued #${mamId} for download: ${data.title || series}`);
          startQbitPolling();
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
          (data.items || []).forEach(it => {
            markCardQueuedInDOM({
              disposition_key: it.disposition_key || "",
              series_name: series,
              volume_number: it.volume || "",
              title: it.title || "",
              mam_id: it.mam_id,
              queued_title: it.title || "",
              qbit_state_label: "Queued • Sending to qBittorrent...",
              qbit_progress: 0
            });
          });
          showToast(`✓ Queued ${data.queued_count} missing book(s) for '${series}'!`);
          startQbitPolling();
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

  // 6. Triage Buttons: Mark Volume as Not Wanted or Unable to Find
  document.querySelectorAll("[data-nc-mark]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const status = btn.getAttribute("data-nc-mark") || "not_wanted";
      const dKey = btn.getAttribute("data-disp-key") || "";
      const series = btn.getAttribute("data-series") || "";
      const volume = btn.getAttribute("data-volume") || "";
      const title = btn.getAttribute("data-title") || "";
      const author = btn.getAttribute("data-author") || "";
      openDispositionModal({
        disposition_key: dKey,
        series_name: series,
        volume_number: volume,
        title: title,
        author: author,
        status: status,
        reason: "",
        notes: ""
      });
    });
  });

  // 6b. Edit Disposition Button in Revisit Hub
  document.querySelectorAll("[data-nc-edit-disp]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const card = btn.closest("[data-revisit-card]");
      if (!card) return;
      openDispositionModal({
        disposition_key: card.getAttribute("data-disp-key") || "",
        series_name: card.getAttribute("data-series") || "",
        volume_number: card.getAttribute("data-volume") || "",
        title: card.getAttribute("data-title") || "",
        author: card.getAttribute("data-author") || "",
        status: card.getAttribute("data-status") || "unable_to_find",
        reason: card.getAttribute("data-reason") || "",
        notes: card.getAttribute("data-notes") || "",
        reloadOnSave: true
      });
    });
  });

  // 6c. Restore Volume back to Active Missing
  document.querySelectorAll("[data-nc-restore]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const dKey = btn.getAttribute("data-disp-key") || "";
      const series = btn.getAttribute("data-series") || "";
      const volume = btn.getAttribute("data-volume") || "";
      const title = btn.getAttribute("data-title") || "";
      btn.disabled = true;
      btn.innerHTML = "Restoring...";
      try {
        const res = await fetch("/api/nextchapter/dispositions/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            disposition_key: dKey,
            series_name: series,
            volume_number: volume,
            title: title
          })
        });
        const data = await res.json();
        if (data.ok) {
          if (data.counts) updatePageCounts(data.counts);
          const revisitCard = btn.closest("[data-revisit-card]");
          const miniRow = btn.closest(".dismissed-mini-row");
          if (revisitCard) {
            revisitCard.style.opacity = "0.25";
            setTimeout(() => revisitCard.remove(), 220);
            showToast(`↩ Restored '${title || series}' to Active Missing!`);
          } else if (miniRow) {
            window.location.reload();
          }
        }
      } catch (err) {
        alert("Failed to restore volume: " + err.message);
        btn.disabled = false;
        btn.innerHTML = "↩ Restore";
      }
    });
  });

  // 6d. Skip All Remaining Missing Volumes in Series
  document.querySelectorAll("[data-nc-skip-series]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const series = btn.getAttribute("data-series") || "";
      if (!series) return;
      if (!confirm(`Mark all remaining unqueued missing volumes in '${series}' as Not Wanted?\n\n(You can restore them anytime in Revisit & Watchlist.)`)) {
        return;
      }
      btn.disabled = true;
      btn.innerHTML = "Skipping...";
      try {
        const res = await fetch("/api/nextchapter/dispositions/series", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            series_name: series,
            status: "not_wanted",
            reason: "Skipped remaining in series"
          })
        });
        const data = await res.json();
        if (data.ok) {
          showToast(`🚫 Marked ${data.updated_count} volume(s) in '${series}' as Not Wanted.`);
          setTimeout(() => window.location.reload(), 450);
        } else {
          btn.disabled = false;
          btn.innerHTML = "🚫 Skip Remaining";
        }
      } catch (err) {
        btn.disabled = false;
        btn.innerHTML = "🚫 Skip Remaining";
      }
    });
  });

  // Update DOM cards in-place when a volume is queued
  function markCardQueuedInDOM(info) {
    const dKey = (info.disposition_key || "").toLowerCase();
    const sNorm = (info.series_name || "").trim().toLowerCase();
    const vNorm = String(info.volume_number || "").trim().toLowerCase();

    document.querySelectorAll("[data-vol-card]").forEach(card => {
      const cardKey = (card.getAttribute("data-disp-key") || "").toLowerCase();
      const cardSeries = (card.getAttribute("data-series") || "").trim().toLowerCase();
      const cardVol = (card.getAttribute("data-volume") || "").trim().toLowerCase();

      const matched = (dKey && cardKey && dKey === cardKey) ||
        (sNorm && vNorm && cardSeries === sNorm && cardVol === vNorm);
      if (!matched) return;

      card.classList.add("is-queued");
      card.setAttribute("data-status", "queued");

      const banner = card.querySelector("[data-vol-banner]");
      if (banner) {
        banner.style.display = "flex";
        const labelEl = banner.querySelector("[data-qbit-label]");
        if (labelEl && info.qbit_state_label) {
          labelEl.textContent = info.qbit_state_label;
        }
        const linkEl = banner.querySelector("[data-qbit-mam-link]");
        if (linkEl && info.mam_id) {
          linkEl.href = `https://www.myanonamouse.net/t/${info.mam_id}`;
          linkEl.textContent = `#${info.mam_id} ↗`;
          linkEl.style.display = "inline";
        }
      }

      const tracker = card.querySelector("[data-qbit-tracker]");
      if (tracker) {
        tracker.style.display = "block";
        const barEl = tracker.querySelector("[data-qbit-bar]");
        if (barEl && info.qbit_progress !== undefined) {
          barEl.style.width = `${Math.min(100, Math.max(0, Number(info.qbit_progress) || 0))}%`;
        }
        const fileEl = tracker.querySelector("[data-qbit-file]");
        const displayFile = info.qbit_file || info.queued_title || info.title || "Waiting for qBittorrent...";
        if (fileEl) {
          fileEl.textContent = `📄 ${displayFile}`;
          fileEl.title = info.qbit_save_path || displayFile;
        }
        const speedEl = tracker.querySelector("[data-qbit-speed]");
        if (speedEl) {
          speedEl.textContent = info.qbit_dlspeed || "";
        }
      }

      const dlBtn = card.querySelector("[data-nc-download]");
      if (dlBtn) {
        dlBtn.innerHTML = "✓ Queued (View / Change Release)";
        dlBtn.classList.remove("btn-primary-nc", "btn-outline-nc");
        dlBtn.classList.add("btn-queued-nc");
      }
    });
  }

  // Live qBittorrent polling & card updater
  async function syncQbitStatus(showFeedback = false) {
    try {
      const res = await fetch("/api/nextchapter/queue-status");
      const data = await res.json();
      if (!data || !data.ok) return;
      if (data.counts) updatePageCounts(data.counts);
      const list = data.queued_volumes || [];
      const byKey = data.by_key || {};

      Object.values(byKey).forEach(qv => {
        markCardQueuedInDOM(qv);
      });
      list.forEach(qv => {
        markCardQueuedInDOM(qv);
      });

      if (showFeedback) {
        showToast(`🔄 Synced ${Object.keys(byKey).length} tracked torrent(s) with qBittorrent.`);
      }
    } catch (err) {
      // Ignore transient polling errors
    }
  }

  function startQbitPolling() {
    syncQbitStatus(false);
    if (qbitPollTimer) return;
    qbitPollTimer = setInterval(() => {
      const hasQueuedOnPage = document.querySelector(".missing-vol-card.is-queued");
      if (hasQueuedOnPage) {
        syncQbitStatus(false);
      }
    }, 4500);
  }

  if (document.querySelector(".missing-vol-card.is-queued")) {
    startQbitPolling();
  }

  function updatePageCounts(counts) {
    if (!counts) return;
    document.querySelectorAll("[data-nc-count='missing']").forEach(el => {
      el.textContent = counts.missing_count ?? 0;
    });
    document.querySelectorAll("[data-nc-count='dismissed']").forEach(el => {
      el.textContent = counts.dismissed_count ?? 0;
    });
    document.querySelectorAll("[data-nc-metric='missing']").forEach(el => {
      el.textContent = counts.missing_count ?? 0;
      el.style.color = (counts.missing_count > 0) ? "#fb7185" : "#34d399";
    });
    document.querySelectorAll("[data-nc-metric='complete']").forEach(el => {
      el.textContent = counts.complete_count ?? 0;
    });
    document.querySelectorAll("[data-nc-metric='dismissed']").forEach(el => {
      el.textContent = counts.dismissed_count ?? 0;
    });
    document.querySelectorAll("[data-nc-metric='unable']").forEach(el => {
      el.textContent = counts.unable_to_find_count ?? 0;
    });
    document.querySelectorAll("[data-nc-metric='not_wanted']").forEach(el => {
      el.textContent = counts.not_wanted_count ?? 0;
    });
    document.querySelectorAll("[data-nc-metric='queued']").forEach(el => {
      if (counts.queued_count > 0) {
        el.textContent = `✓ ${counts.queued_count} queued in qBittorrent`;
        el.style.color = "#38bdf8";
        el.style.fontWeight = "600";
      }
    });
  }

  // Quick Disposition & Reason Modal ("Unable to Find" / "Not Wanted")
  function openDispositionModal(opts) {
    let modal = document.getElementById("nc-disp-modal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "nc-disp-modal";
      modal.className = "nc-modal-overlay";
      modal.innerHTML = `
        <div class="nc-modal-container" style="max-width: 520px;">
          <div class="nc-modal-header">
            <h3 class="nc-modal-title" id="nc-disp-modal-title">Triage Missing Volume</h3>
            <button id="nc-disp-modal-close" class="nc-modal-close" title="Close">&times;</button>
          </div>
          <div class="nc-modal-body" id="nc-disp-modal-body" style="padding: 1.25rem 1.5rem;"></div>
        </div>
      `;
      document.body.appendChild(modal);
      document.getElementById("nc-disp-modal-close").addEventListener("click", () => {
        modal.style.display = "none";
      });
      modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.style.display = "none";
      });
    }

    let currentStatus = opts.status || "unable_to_find";
    let currentReason = opts.reason || "";
    const presets = {
      unable_to_find: [
        "Not on MAM yet",
        "Wrong format (e.g. no Audiobook)",
        "No seeders / Dead torrent",
        "Unreleased / Upcoming book",
        "Abridged or foreign language only"
      ],
      not_wanted: [
        "Not needed / Skipped",
        "Already read / Own elsewhere",
        "Novella / Side story",
        "Covered by omnibus / box set",
        "Dropped series"
      ]
    };

    function renderDispBody() {
      const volLabel = opts.volume_number ? ` #${opts.volume_number}` : "";
      const bookHeading = `${opts.series_name}${volLabel}${opts.title ? " — " + opts.title : ""}`;
      document.getElementById("nc-disp-modal-title").innerHTML =
        currentStatus === "unable_to_find"
          ? `<span>🕵️</span> Mark as Unable to Find`
          : `<span>🚫</span> Mark as Not Wanted`;

      const pillList = presets[currentStatus] || presets.not_wanted;
      if (!currentReason || !(presets[currentStatus] || []).includes(currentReason)) {
        currentReason = opts.reason && opts.status === currentStatus ? opts.reason : pillList[0];
      }

      const body = document.getElementById("nc-disp-modal-body");
      body.innerHTML = `
        <div style="margin-bottom: 0.85rem;">
          <div style="font-size: 0.95rem; font-weight: 700; color: #f8fafc;">${escapeHtml(bookHeading)}</div>
          ${opts.author ? `<div style="font-size: 0.82rem; color: #38bdf8;">by ${escapeHtml(opts.author)}</div>` : ""}
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; margin-bottom: 1rem;">
          <button type="button" data-disp-type="unable_to_find" class="btn btn-sm ${currentStatus === 'unable_to_find' ? 'btn-primary-nc' : 'btn-outline-nc'}" style="padding: 0.55rem;">
            🕵️ Unable to Find (Watchlist)
          </button>
          <button type="button" data-disp-type="not_wanted" class="btn btn-sm ${currentStatus === 'not_wanted' ? 'btn-primary-nc' : 'btn-outline-nc'}" style="padding: 0.55rem;">
            🚫 Not Wanted (Skip)
          </button>
        </div>

        <div>
          <label style="font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: #94a3b8; font-weight: 600;">
            Quick Reason (Click any pill to save immediately, or add a note below):
          </label>
          <div class="nc-reason-pills">
            ${pillList.map(r => `
              <button type="button" class="nc-reason-pill ${r === currentReason ? 'active' : ''}" data-reason-pill="${escapeHtml(r)}">
                ${escapeHtml(r)}
              </button>
            `).join("")}
          </div>
        </div>

        <div style="margin-bottom: 1.1rem;">
          <label style="display: block; font-size: 0.8rem; color: #cbd5e1; margin-bottom: 0.35rem; font-weight: 600;">
            Optional Note (e.g., narrator preference, release date, or edition note):
          </label>
          <input type="text" id="nc-disp-note-input" value="${escapeHtml(opts.notes || '')}" placeholder="Add a note for when you revisit this book..." style="width: 100%; padding: 0.6rem 0.75rem; background: #0b1120; border: 1px solid #334155; border-radius: 6px; color: #f8fafc; font-size: 0.88rem;" />
        </div>

        <div style="display: flex; justify-content: flex-end; gap: 0.6rem;">
          <button type="button" id="nc-disp-cancel-btn" class="btn btn-sm btn-outline-nc" style="padding: 0.5rem 1rem;">Cancel</button>
          <button type="button" id="nc-disp-save-btn" class="btn btn-sm btn-primary-nc" style="padding: 0.5rem 1.25rem;">
            Save to ${currentStatus === "unable_to_find" ? "Watchlist" : "Skipped"}
          </button>
        </div>
      `;

      body.querySelectorAll("[data-disp-type]").forEach(tBtn => {
        tBtn.addEventListener("click", () => {
          currentStatus = tBtn.getAttribute("data-disp-type") || "not_wanted";
          currentReason = "";
          renderDispBody();
        });
      });

      body.querySelectorAll("[data-reason-pill]").forEach(pBtn => {
        pBtn.addEventListener("click", async () => {
          currentReason = pBtn.getAttribute("data-reason-pill") || "";
          const noteVal = (document.getElementById("nc-disp-note-input")?.value || "").trim();
          await submitDisposition(currentStatus, currentReason, noteVal);
        });
      });

      document.getElementById("nc-disp-cancel-btn").addEventListener("click", () => {
        modal.style.display = "none";
      });

      document.getElementById("nc-disp-save-btn").addEventListener("click", async () => {
        const noteVal = (document.getElementById("nc-disp-note-input")?.value || "").trim();
        await submitDisposition(currentStatus, currentReason, noteVal);
      });
    }

    async function submitDisposition(statusVal, reasonVal, notesVal) {
      const saveBtn = document.getElementById("nc-disp-save-btn");
      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spin">⟳</span> Saving...';
      }
      try {
        const res = await fetch("/api/nextchapter/dispositions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            disposition_key: opts.disposition_key || "",
            series_name: opts.series_name || "",
            volume_number: opts.volume_number || "",
            title: opts.title || "",
            author: opts.author || "",
            status: statusVal,
            reason: reasonVal,
            notes: notesVal
          })
        });
        const data = await res.json();
        if (data.ok) {
          modal.style.display = "none";
          if (opts.onSuccess) opts.onSuccess(data);
          if (opts.reloadOnSave) {
            window.location.reload();
            return;
          }
          if (data.counts) updatePageCounts(data.counts);

          // Remove matching active missing card(s) from DOM
          const savedKey = (data.disposition?.disposition_key || opts.disposition_key || "").toLowerCase();
          const sNorm = (opts.series_name || "").trim().toLowerCase();
          const vNorm = String(opts.volume_number || "").trim().toLowerCase();

          document.querySelectorAll("[data-vol-card]").forEach(card => {
            const cKey = (card.getAttribute("data-disp-key") || "").toLowerCase();
            const cSer = (card.getAttribute("data-series") || "").trim().toLowerCase();
            const cVol = (card.getAttribute("data-volume") || "").trim().toLowerCase();
            if ((savedKey && cKey === savedKey) || (sNorm && vNorm && cSer === sNorm && cVol === vNorm)) {
              card.style.transition = "opacity 0.2s ease, transform 0.2s ease";
              card.style.opacity = "0";
              card.style.transform = "scale(0.95)";
              setTimeout(() => card.remove(), 220);
            }
          });

          const label = statusVal === "unable_to_find" ? "🕵️ Added to Watchlist" : "🚫 Marked Not Wanted";
          showUndoToast(
            `${label}: ${opts.title || (opts.series_name + " #" + opts.volume_number)} (${reasonVal})`,
            async () => {
              await fetch("/api/nextchapter/dispositions/restore", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  disposition_key: data.disposition?.disposition_key || opts.disposition_key || "",
                  series_name: opts.series_name || "",
                  volume_number: opts.volume_number || "",
                  title: opts.title || ""
                })
              });
              window.location.reload();
            }
          );
        }
      } catch (err) {
        alert("Failed to save disposition: " + err.message);
      }
    }

    renderDispBody();
    modal.style.display = "flex";
  }

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
            <div id="nc-modal-triage-bar" style="display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.65rem; padding-top: 0.6rem; border-top: 1px solid rgba(255, 255, 255, 0.06);">
              <span style="font-size: 0.76rem; color: #94a3b8;">Can't find the right edition or don't need this volume?</span>
              <div style="display: flex; gap: 0.4rem;">
                <button type="button" class="btn-triage unable" id="nc-modal-mark-unable">🕵️ Mark Unable to Find</button>
                <button type="button" class="btn-triage not-wanted" id="nc-modal-mark-notwanted">🚫 Mark Not Wanted</button>
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

    const triageBar = document.getElementById("nc-modal-triage-bar");
    if (triageBar) {
      triageBar.style.display = (state.seriesName && (state.volume || state.title)) ? "flex" : "none";
    }
    const markUnableBtn = document.getElementById("nc-modal-mark-unable");
    if (markUnableBtn) {
      markUnableBtn.onclick = () => {
        modal.style.display = "none";
        openDispositionModal({
          series_name: state.seriesName,
          volume_number: state.volume,
          title: state.title,
          author: state.author,
          status: "unable_to_find"
        });
      };
    }
    const markNotWantedBtn = document.getElementById("nc-modal-mark-notwanted");
    if (markNotWantedBtn) {
      markNotWantedBtn.onclick = () => {
        modal.style.display = "none";
        openDispositionModal({
          series_name: state.seriesName,
          volume_number: state.volume,
          title: state.title,
          author: state.author,
          status: "not_wanted"
        });
      };
    }

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
              <p style="color: #94a3b8; font-size: 0.85rem; max-width: 540px; margin: 0 auto 1.25rem;">
                We searched for "${escapeHtml(state.q)}". You can mark this volume as <strong>Unable to Find</strong> to revisit later, or browse the entire series/author below:
              </p>
              <div style="display: flex; gap: 0.6rem; justify-content: center; flex-wrap: wrap;">
                ${(state.seriesName && (state.volume || state.title)) ? `<button type="button" class="button btn-primary-nc" id="nc-empty-mark-unable">🕵️ Mark Unable to Find (Add to Watchlist)</button>` : ""}
                ${state.seriesName ? `<button type="button" class="button btn-outline-nc" data-nc-empty-mode="series">📚 Browse All in Series (${escapeHtml(state.seriesName)})</button>` : ""}
                ${(state.author || state.seriesName) ? `<button type="button" class="button btn-outline-nc" data-nc-empty-mode="author">✍️ Browse All by Author (${escapeHtml(state.author || state.seriesName)})</button>` : ""}
                <a href="https://www.myanonamouse.net/tor/browse.php?tor[text]=${encodeURIComponent(state.q)}" target="_blank" rel="noreferrer" class="button btn-outline-nc" style="display: inline-flex;">Open on MAM ↗</a>
              </div>
            </div>
          `;
          const emptyUnable = document.getElementById("nc-empty-mark-unable");
          if (emptyUnable) {
            emptyUnable.addEventListener("click", () => {
              modal.style.display = "none";
              openDispositionModal({
                series_name: state.seriesName,
                volume_number: state.volume,
                title: state.title,
                author: state.author,
                status: "unable_to_find",
                reason: "Not on MAM yet"
              });
            });
          }
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

          const inLibraryOnDisk = Boolean(c.in_library_on_disk);
          const inQueueOnly = Boolean(c.in_queue && !inLibraryOnDisk);
          const inLibraryBadge = inLibraryOnDisk
            ? `<span class="chip" style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.45); font-weight: 600;">✓ In Library</span>`
            : (inQueueOnly
              ? `<span class="chip" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.45); font-weight: 600;">⏳ In Queue / qBittorrent</span>`
              : (c.in_library
                ? `<span class="chip" style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.45); font-weight: 600;">✓ In Library / Queue</span>`
                : ""));

          const seedersNum = parseInt(c.seeders, 10) || 0;
          const leechersNum = parseInt(c.leechers, 10) || 0;
          const seedColor = seedersNum > 5 ? "#34d399" : (seedersNum > 0 ? "#38bdf8" : "#fb7185");
          const mamSeriesPrimary = c.series_name_primary || c.series_name_mam || "";

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
                <button class="btn btn-sm ${c.in_library ? 'btn-queued-nc' : 'btn-primary-nc'}" data-modal-queue="${c.mam_id}" data-series="${escapeHtml(state.seriesName || mamSeriesPrimary)}" data-vol="${escapeHtml(state.volume || c.series_volume)}" data-title="${escapeHtml(state.title || c.title)}" style="padding: 0.45rem 1rem; font-weight: 600;">
                  ${inQueueOnly ? "↻ Queue Again & Assign Category" : (c.in_library ? "✓ In Library / Queue Again" : "1-Click Queue")}
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
            const qTitle = qBtn.getAttribute("data-title") || state.title;
            qBtn.disabled = true;
            qBtn.innerHTML = '<span class="spin">⟳</span> Queuing...';
            const qRes = await fetch("/api/nextchapter/download", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                mam_id: parseInt(id, 10),
                series_name: qSeries,
                volume: qVol,
                title: qTitle
              })
            });
            const qData = await qRes.json();
            if (qData.ok) {
              const catInfo = qData.category ? ` [${escapeHtml(qData.category)}]` : "";
              qBtn.disabled = false;
              qBtn.innerHTML = `✓ Queued${catInfo} • Queue Again`;
              qBtn.classList.remove("btn-primary-nc");
              qBtn.classList.add("btn-queued-nc");
              const qItem = qData.queued_item || {};
              markCardQueuedInDOM({
                disposition_key: qData.disposition_key || "",
                series_name: qSeries,
                volume_number: qVol,
                title: qTitle,
                mam_id: parseInt(id, 10),
                queued_title: qData.title || qTitle,
                qbit_state_label: qItem.qbit_state_label || `Queued${qData.category ? " [" + qData.category + "]" : ""} • Sending to qBittorrent...`,
                qbit_progress: 0
              });
              if (qData.counts) updatePageCounts(qData.counts);
              showToast(`✓ Queued #${id}${qData.category ? " [" + qData.category + "]" : ""}: ${qData.title || qSeries}`);
              startQbitPolling();
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
      toast.style.borderRadius = "8px";
      toast.style.boxShadow = "0 10px 20px -3px rgba(0, 0, 0, 0.6)";
      toast.style.zIndex = "10000";
      toast.style.fontWeight = "600";
      toast.style.transition = "opacity 0.3s ease";
      document.body.appendChild(toast);
    }
    toast.innerHTML = escapeHtml(message);
    toast.style.opacity = "1";
    toast.style.display = "block";
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => { toast.style.display = "none"; }, 300);
    }, 3500);
  }

  function showUndoToast(message, onUndo) {
    let toast = document.getElementById("nc-toast");
    if (!toast) {
      showToast(message);
      toast = document.getElementById("nc-toast");
    }
    toast.innerHTML = `
      <div style="display: flex; align-items: center; gap: 12px;">
        <span>${escapeHtml(message)}</span>
        <button type="button" id="nc-toast-undo-btn" style="background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: #fff; padding: 3px 10px; border-radius: 5px; font-weight: 700; cursor: pointer; font-size: 0.8rem;">
          ↩ Undo
        </button>
      </div>
    `;
    toast.style.opacity = "1";
    toast.style.display = "block";
    const undoBtn = document.getElementById("nc-toast-undo-btn");
    if (undoBtn && onUndo) {
      undoBtn.onclick = async () => {
        toast.style.display = "none";
        await onUndo();
      };
    }
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => { toast.style.display = "none"; }, 300);
    }, 5500);
  }
});
