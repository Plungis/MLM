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
        openCandidateModal(series, volume, title);
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
  async function openCandidateModal(seriesName, volume, title) {
    const query = new URLSearchParams({
      series_name: seriesName,
      volume: volume,
      title: title
    });
    let modal = document.getElementById("nc-candidate-modal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "nc-candidate-modal";
      modal.style.position = "fixed";
      modal.style.top = "0";
      modal.style.left = "0";
      modal.style.width = "100%";
      modal.style.height = "100%";
      modal.style.backgroundColor = "rgba(0,0,0,0.75)";
      modal.style.display = "flex";
      modal.style.alignItems = "center";
      modal.style.justifyContent = "center";
      modal.style.zIndex = "9999";
      modal.innerHTML = `
        <div style="background: #1e293b; border: 1px solid #334155; border-radius: 8px; width: 90%; max-width: 650px; max-height: 80vh; display: flex; flex-direction: column; overflow: hidden; padding: 1.5rem;">
          <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 0.75rem; margin-bottom: 1rem;">
            <h3 style="margin: 0; color: #f8fafc;" id="nc-modal-title">Search Results</h3>
            <button id="nc-modal-close" style="background: transparent; border: none; color: #94a3b8; font-size: 1.25rem; cursor: pointer;">&times;</button>
          </div>
          <div id="nc-modal-body" style="overflow-y: auto; flex-grow: 1;">
            <p style="color: #94a3b8;">Searching MyAnonamouse for matching releases...</p>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
      document.getElementById("nc-modal-close").addEventListener("click", () => {
        modal.style.display = "none";
      });
    }

    modal.style.display = "flex";
    document.getElementById("nc-modal-title").innerText = `MAM Releases: ${seriesName} #${volume}`;
    const body = document.getElementById("nc-modal-body");
    body.innerHTML = '<p style="color: #94a3b8;"><span class="spin">⟳</span> Searching MyAnonamouse...</p>';

    try {
      const res = await fetch(`/api/nextchapter/search-mam?${query.toString()}`);
      const candidates = await res.json();
      if (!candidates || candidates.length === 0) {
        body.innerHTML = `<p style="color: #94a3b8;">No matching releases found on MAM for "${seriesName} ${volume}".</p>`;
        return;
      }

      let html = '<div style="display: flex; flex-direction: column; gap: 0.75rem;">';
      candidates.forEach(c => {
        const badges = [];
        if (c.vip) badges.push('<span class="chip" style="background: #8b5cf6; color: white;">VIP</span>');
        if (c.free || c.personal_freeleech) badges.push('<span class="chip" style="background: #10b981; color: white;">Free</span>');
        badges.push(`<span class="chip">${c.media_type}</span>`);
        badges.push(`<span class="chip">${c.size}</span>`);
        badges.push(`<span class="chip" style="color: #38bdf8;">▲ ${c.seeders}</span>`);

        html += `
          <div style="background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 0.75rem; display: flex; justify-content: space-between; align-items: center; gap: 1rem;">
            <div style="flex-grow: 1;">
              <strong style="color: #f1f5f9; display: block; margin-bottom: 0.35rem; font-size: 0.9rem;">${c.title}</strong>
              <div style="display: flex; gap: 0.4rem; flex-wrap: wrap;">${badges.join("")}</div>
            </div>
            <button class="btn btn-sm btn-primary-nc" data-modal-queue="${c.mam_id}" data-series="${seriesName}" data-vol="${volume}" style="white-space: nowrap;">Queue</button>
          </div>
        `;
      });
      html += '</div>';
      body.innerHTML = html;

      body.querySelectorAll("[data-modal-queue]").forEach(qBtn => {
        qBtn.addEventListener("click", async () => {
          const id = qBtn.getAttribute("data-modal-queue");
          qBtn.disabled = true;
          qBtn.innerText = "Queuing...";
          const qRes = await fetch("/api/nextchapter/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mam_id: parseInt(id, 10), series_name: seriesName, volume: volume })
          });
          const qData = await qRes.json();
          if (qData.ok) {
            qBtn.innerText = "✓ Queued";
            showToast(`Queued #${id} successfully!`);
            setTimeout(() => { modal.style.display = "none"; }, 800);
          } else {
            alert("Error: " + (qData.error || "Failed to queue"));
            qBtn.disabled = false;
            qBtn.innerText = "Queue";
          }
        });
      });
    } catch (err) {
      body.innerHTML = `<p style="color: #fb7185;">Search error: ${err.message}</p>`;
    }
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
