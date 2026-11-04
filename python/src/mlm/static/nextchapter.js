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

  // 4. Single Download Queue Trigger
  document.querySelectorAll("[data-nc-download]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const mamId = btn.getAttribute("data-mam-id");
      const series = btn.getAttribute("data-series");
      const volume = btn.getAttribute("data-volume");

      if (!mamId) {
        // Search MAM first
        const title = btn.getAttribute("data-title") || "";
        const author = btn.getAttribute("data-author") || "";
        openCandidateModal(series, volume, title, author);
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

  // Modal helper for candidates
  async function openCandidateModal(seriesName, volume, title, author) {
    const query = new URLSearchParams({
      series_name: seriesName || "",
      volume: volume || "",
      title: title || "",
      author: author || ""
    });
    let modal = document.getElementById("nc-candidate-modal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "nc-candidate-modal";
      modal.className = "nc-modal-overlay";
      modal.innerHTML = `
        <div class="nc-modal-container">
          <div class="nc-modal-header">
            <h3 class="nc-modal-title" id="nc-modal-title"><span>📚</span> Available MAM Releases</h3>
            <button id="nc-modal-close" class="nc-modal-close" title="Close">&times;</button>
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

    modal.style.display = "flex";
    const headerTitle = title ? `${seriesName} #${volume} - ${title}` : `${seriesName} #${volume}`;
    document.getElementById("nc-modal-title").innerHTML = `<span>📚</span> MAM Releases: <span style="color: #818cf8; margin-left: 4px;">${escapeHtml(headerTitle)}</span>`;
    const body = document.getElementById("nc-modal-body");
    body.innerHTML = `
      <div style="text-align: center; padding: 2.5rem 1rem;">
        <p style="color: #94a3b8; font-size: 0.95rem; margin-bottom: 0.5rem;"><span class="spin">⟳</span> Searching MyAnonamouse for matching releases...</p>
        <small style="color: #64748b;">Matching against series, volume, author, and canonical book title</small>
      </div>
    `;

    try {
      const res = await fetch(`/api/nextchapter/search-mam?${query.toString()}`);
      const candidates = await res.json();
      if (!candidates || candidates.length === 0) {
        body.innerHTML = `
          <div style="text-align: center; padding: 2.5rem 1rem;">
            <p style="color: #f8fafc; font-weight: 600; font-size: 1rem; margin-bottom: 0.5rem;">No matching releases found on MAM</p>
            <p style="color: #94a3b8; font-size: 0.85rem; max-width: 480px; margin: 0 auto 1.25rem;">We searched for "${escapeHtml(seriesName)} ${escapeHtml(volume)}" and "${escapeHtml(title)}". You can search manually on MyAnonamouse or check back later.</p>
            <a href="https://www.myanonamouse.net/tor/browse.php?tor[text]=${encodeURIComponent(title || seriesName)}" target="_blank" rel="noreferrer" class="button btn-outline-nc" style="display: inline-flex;">Search Directly on MAM ↗</a>
          </div>
        `;
        return;
      }

      let html = `<div style="display: flex; flex-direction: column; gap: 0.9rem;">`;
      candidates.forEach(c => {
        const authorsList = (c.authors && c.authors.length) ? c.authors : (author ? [author] : []);
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

        const seedersNum = parseInt(c.seeders, 10) || 0;
        const leechersNum = parseInt(c.leechers, 10) || 0;
        const seedColor = seedersNum > 5 ? "#34d399" : (seedersNum > 0 ? "#38bdf8" : "#fb7185");

        html += `
          <div class="candidate-card">
            <div class="candidate-header-row">
              <a href="https://www.myanonamouse.net/t/${c.mam_id}" target="_blank" rel="noreferrer" class="candidate-title" title="Open release #${c.mam_id} on MyAnonamouse">
                ${escapeHtml(c.title)} <span style="font-size: 0.8rem; opacity: 0.7;">↗</span>
              </a>
            </div>

            <div class="candidate-meta-grid">
              <div class="candidate-meta-item">
                <span class="candidate-meta-label">Author:</span>
                <span class="candidate-meta-value" style="color: #38bdf8;">${escapeHtml(authorText)}</span>
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
                <span class="candidate-meta-value" style="color: #a5b4fc; font-weight: 600;">📚 ${escapeHtml(c.series_display)}</span>
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
                ${c.series_display ? `<span class="chip" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc; border: 1px solid rgba(99, 102, 241, 0.4); font-weight: 600;" title="Series registered on MyAnonamouse">📚 ${escapeHtml(c.series_display)}</span>` : ""}
              </div>
              <button class="btn btn-sm btn-primary-nc" data-modal-queue="${c.mam_id}" data-series="${escapeHtml(seriesName)}" data-vol="${escapeHtml(volume)}" style="padding: 0.45rem 1rem; font-weight: 600;">
                1-Click Queue
              </button>
            </div>
          </div>
        `;
      });
      html += `</div>`;
      body.innerHTML = html;

      body.querySelectorAll("[data-modal-queue]").forEach(qBtn => {
        qBtn.addEventListener("click", async () => {
          const id = qBtn.getAttribute("data-modal-queue");
          qBtn.disabled = true;
          qBtn.innerHTML = '<span class="spin">⟳</span> Queuing...';
          const qRes = await fetch("/api/nextchapter/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mam_id: parseInt(id, 10), series_name: seriesName, volume: volume })
          });
          const qData = await qRes.json();
          if (qData.ok) {
            qBtn.innerHTML = "✓ Queued";
            qBtn.classList.remove("btn-primary-nc");
            qBtn.classList.add("btn-outline-nc");
            showToast(`Queued #${id} successfully!`);
            setTimeout(() => { modal.style.display = "none"; }, 1000);
          } else {
            alert("Error: " + (qData.error || "Failed to queue"));
            qBtn.disabled = false;
            qBtn.innerHTML = "1-Click Queue";
          }
        });
      });
    } catch (err) {
      body.innerHTML = `<p style="color: #fb7185;">Search error: ${err.message}</p>`;
    }
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
