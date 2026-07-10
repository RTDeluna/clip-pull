document.addEventListener("DOMContentLoaded", async () => {

  // ── DOM refs ────────────────────────────────────────────────
  const statusEl    = document.getElementById("status");
  const spinner     = document.getElementById("spinner");
  const downloadBtn = document.getElementById("downloadBtn");
  const preview     = document.getElementById("preview");
  const thumbnail   = document.getElementById("thumbnail");

  // ── Detection state (set by runDetection, read by sendToClipPull) ────
  let detectedLink  = null;
  let videoSource   = null;
  let lessonTitle   = null;
  let courseName    = null;

  // ============================================================
  // HELPER FUNCTIONS
  // ============================================================
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Only trust http(s) URLs before assigning them to element src attributes.
  // Blocks javascript:, data:, blob: and other injection vectors that reach
  // this privileged popup from page-controlled data (thumbnails, embeds).
  function isHttpUrl(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url.trim());
  }

  function offerRestart(label) {
    document.getElementById("enhancedDownloadBtn")?.remove();
    const btn = document.createElement("button");
    btn.id = "enhancedDownloadBtn";
    btn.textContent = label;
    btn.style.cssText = "margin-top:14px;padding:12px 20px;width:100%;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;border-radius:10px;cursor:pointer;font-weight:700;font-size:14px;box-shadow:0 4px 14px rgba(102,126,234,0.42);transition:all 0.2s ease;font-family:inherit;";
    btn.addEventListener("click", () => {
      btn.style.display = "none";
      sendToClipPull();
    });
    statusEl.insertAdjacentElement("afterend", btn);
  }

  function showManualLoomInstructions(loomUrl) {
    statusEl.innerHTML = `
      <div style="text-align:center;padding:10px;">
        <div style="margin-bottom:12px;color:#e67e22;font-weight:700;font-size:13px;">
          ⚠️ Manual Download Required
        </div>
        <div style="font-size:12px;margin-bottom:14px;color:#555;line-height:1.5;">
          This Loom video has extra protection. Use the fast manual method:
        </div>
        <div style="background:#f0f0f0;padding:12px;border-radius:8px;margin-bottom:14px;
                    text-align:left;font-size:11px;line-height:1.8;color:#444;
                    box-shadow:inset 2px 2px 4px #d8d8d8,inset -2px -2px 4px #fff;">
          1. Click "Fast Manual Download" below<br>
          2. On the Loom page: click <strong>⋯</strong> → <strong>Download</strong><br>
          3. Save the file to your computer
        </div>
        <button id="fastManualDownloadBtn" style="
          padding:11px 20px;width:100%;
          background:linear-gradient(135deg,#FF6B6B,#FF8E53);
          color:white;border:none;border-radius:10px;cursor:pointer;
          font-weight:700;font-size:13px;font-family:inherit;
          box-shadow:0 4px 14px rgba(255,107,107,0.38);
          transition:all 0.18s ease;
        ">🚀 Fast Manual Download</button>
        <div style="font-size:10px;color:#aaa;margin-top:8px;">
          Opens video + instructions in new tabs
        </div>
      </div>
    `;
    setTimeout(() => {
      document.getElementById("fastManualDownloadBtn")?.addEventListener("click", () => {
        chrome.tabs.create({ url: loomUrl, active: true });
        chrome.tabs.create({
          url: "https://skoolvideodownloader.com/loom-download-workaround",
          active: false
        });
      });
    }, 50);
    spinner.style.display = "none";
    downloadBtn.style.display = "none";
    document.getElementById("enhancedDownloadBtn")?.remove();
  }

  function showGenericError(message, allowRetry = true) {
    statusEl.innerHTML = `
      <div style="text-align:center;color:#e74c3c;padding:8px;">
        <div style="margin-bottom:8px;font-weight:700;font-size:13px;">⚠️ Could Not Send</div>
        <div style="font-size:12px;color:#666;line-height:1.4;">${escapeHtml(message)}</div>
      </div>
    `;
    spinner.style.display = "none";
    if (allowRetry && videoSource !== "loom") {
      offerRestart("🔄 Try Again");
    }
  }

  // ── Video preview URL per provider ─────────────────────────
  function getPreviewEmbedUrl(link, source) {
    try {
      switch (source) {
        case "youtube": {
          const m = link.match(/v=([a-zA-Z0-9_-]+)/);
          return m ? `https://www.youtube.com/embed/${m[1]}?autoplay=1&mute=1&rel=0` : null;
        }
        case "vimeo": {
          const m = link.match(/video\/(\d+)/);
          return m ? `https://player.vimeo.com/video/${m[1]}?autoplay=1&muted=1` : null;
        }
        case "loom": {
          const m = link.match(/share\/([a-zA-Z0-9]+)/);
          return m ? `https://www.loom.com/embed/${m[1]}?autoplay=true&hide_title=true` : null;
        }
        case "wistia": {
          const m = link.match(/medias\/([a-z0-9]+)/) || link.match(/iframe\/([a-z0-9]+)/);
          return m ? `https://fast.wistia.net/embed/iframe/${m[1]}?autoplay=1` : null;
        }
        case "bunny":
          return link + (link.includes("?") ? "&" : "?") + "autoplay=true";
        case "mux":
        case "direct":
          return link; // native <video> element used in preview
        default:
          return null;
      }
    } catch { return null; }
  }

  // ── No-video UI (retry + manual URL fallback) ───────────────
  function showNoVideoUI(isError = false) {
    downloadBtn.style.display = "none";
    if (!isError) statusEl.textContent = "❌ No video found on this page.";
    document.getElementById("retrySection")?.remove();

    const retrySection = document.createElement("div");
    retrySection.id            = "retrySection";
    retrySection.style.marginTop = "14px";

    // Retry button
    const retryBtn       = document.createElement("button");
    retryBtn.textContent = "🔄 Retry Detection";
    retryBtn.style.cssText = "width:100%;padding:10px;background:#e2e2e2;color:#555;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:13px;box-shadow:3px 3px 6px #c4c4c4,-3px -3px 6px #fff;margin-bottom:8px;transition:all 0.15s ease;font-family:inherit;";
    retryBtn.addEventListener("click", async () => {
      retryBtn.textContent = "⏳ Waiting for video to load...";
      retryBtn.disabled    = true;
      await new Promise(r => setTimeout(r, 2500));
      runDetection(true);
    });

    // Divider
    const divider = document.createElement("div");
    divider.style.cssText = "text-align:center;color:#bbb;font-size:11px;margin:10px 0 8px;";
    divider.textContent   = "— or paste a video URL manually —";

    // Hint
    const hint = document.createElement("div");
    hint.style.cssText = "font-size:10px;color:#aaa;margin-bottom:5px;text-align:left;";
    hint.textContent   = "Supports: Vimeo, Wistia, direct MP4 links";

    // Manual URL input
    const manualInput = document.createElement("input");
    manualInput.type        = "url";
    manualInput.placeholder = "https://player.vimeo.com/video/...";
    manualInput.style.cssText = "width:100%;padding:9px 12px;border:none;border-radius:8px;font-size:12px;background:#ebebeb;box-shadow:inset 3px 3px 5px #d0d0d0,inset -3px -3px 5px #fff;color:#333;margin-bottom:8px;outline:none;font-family:inherit;";

    // Manual send button
    const manualBtn       = document.createElement("button");
    manualBtn.textContent = "📤 Send to Clip.Pull";
    manualBtn.style.cssText = "width:100%;padding:10px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:13px;box-shadow:0 4px 12px rgba(102,126,234,0.35);transition:all 0.15s ease;font-family:inherit;";

    const triggerManual = () => {
      const url = manualInput.value.trim();
      if (!url || !url.startsWith("http")) {
        manualInput.style.boxShadow = "inset 3px 3px 5px #ffaaaa,inset -3px -3px 5px #fff";
        setTimeout(() => { manualInput.style.boxShadow = "inset 3px 3px 5px #d0d0d0,inset -3px -3px 5px #fff"; }, 1500);
        return;
      }
      detectedLink = url;
      videoSource  = "manual";
      retrySection.remove();
      statusEl.textContent = "📤 Sending to Clip.Pull...";
      sendToClipPull();
    };

    manualBtn.addEventListener("click", triggerManual);
    manualInput.addEventListener("keydown", e => { if (e.key === "Enter") triggerManual(); });

    retrySection.append(retryBtn, divider, hint, manualInput, manualBtn);
    statusEl.insertAdjacentElement("afterend", retrySection);
  }

  // ============================================================
  // STEP 1 — VIDEO DETECTION
  // ============================================================
  async function runDetection(isRetry = false) {
    ["retrySection", "enhancedDownloadBtn", "copyLinkBtn", "previewBtn", "videoPreviewContainer"]
      .forEach(id => document.getElementById(id)?.remove());

    preview.style.display = "none";
    thumbnail.src         = "";
    detectedLink = videoSource = lessonTitle = courseName = null;

    statusEl.textContent = isRetry ? "🔄 Scanning for video..." : "🔍 Detecting video...";

    try {
      const [tab]  = await chrome.tabs.query({ active: true, currentWindow: true });
      const result = await chrome.tabs.sendMessage(tab.id, { action: "getVideoLink" });

      if (result?.link) {
        detectedLink = result.link;
        videoSource  = result.source;
        lessonTitle  = result.title || null;
        courseName   = result.courseName || null;

        const SOURCE_LABELS = {
          mux: "SKOOL NATIVE", bunny: "BUNNY STREAM",
          direct: "DIRECT MP4", manual: "MANUAL URL"
        };
        const label = SOURCE_LABELS[videoSource] || videoSource?.toUpperCase() || "VIDEO";
        statusEl.textContent = `✅ ${label} Detected!`;

        if (isHttpUrl(result.thumbnail)) {
          thumbnail.referrerPolicy = "no-referrer";
          thumbnail.src            = result.thumbnail;
          preview.style.display    = "block";
        }

        // ── Send button ──────────────────────────
        const dlBtn      = document.createElement("button");
        dlBtn.id         = "enhancedDownloadBtn";
        dlBtn.textContent = "📤 Send to Clip.Pull";
        dlBtn.style.cssText = "margin-top:14px;padding:12px 20px;width:100%;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;border-radius:10px;cursor:pointer;font-weight:700;font-size:14px;box-shadow:0 4px 14px rgba(102,126,234,0.42);transition:all 0.2s ease;font-family:inherit;";
        dlBtn.addEventListener("mouseover", () => {
          dlBtn.style.transform = "translateY(-2px)";
          dlBtn.style.boxShadow = "0 8px 20px rgba(102,126,234,0.52)";
        });
        dlBtn.addEventListener("mouseout", () => {
          dlBtn.style.transform = "";
          dlBtn.style.boxShadow = "0 4px 14px rgba(102,126,234,0.42)";
        });
        dlBtn.addEventListener("click", () => { dlBtn.style.display = "none"; sendToClipPull(); });

        // ── Copy link button ─────────────────────────
        const cpBtn      = document.createElement("button");
        cpBtn.id         = "copyLinkBtn";
        cpBtn.textContent = "🔗 Copy Video Link";
        cpBtn.style.cssText = "margin-top:7px;padding:8px 14px;width:100%;background:#e2e2e2;color:#555;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:12px;box-shadow:3px 3px 6px #c4c4c4,-3px -3px 6px #fff;transition:all 0.15s ease;font-family:inherit;";
        cpBtn.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(detectedLink);
            cpBtn.textContent = "✅ Copied!";
          } catch {
            cpBtn.textContent = "❌ Copy failed";
          }
          setTimeout(() => { cpBtn.textContent = "🔗 Copy Video Link"; }, 2000);
        });

        // ── Watch preview button ─────────────────────
        // Only offer a preview when the embed URL is a safe http(s) URL —
        // prevents page-supplied data:/javascript: URLs from loading in the popup.
        const embedUrl = getPreviewEmbedUrl(detectedLink, videoSource);
        const pvBtn    = isHttpUrl(embedUrl) ? document.createElement("button") : null;
        if (pvBtn) {
          pvBtn.id          = "previewBtn";
          pvBtn.textContent = "▶ Watch Preview";
          pvBtn.style.cssText = "margin-top:7px;padding:8px 14px;width:100%;background:#e2e2e2;color:#444;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-size:12px;box-shadow:3px 3px 6px #c4c4c4,-3px -3px 6px #fff;transition:all 0.15s ease;font-family:inherit;";

          let previewOpen = false;
          pvBtn.addEventListener("click", () => {
            const existing = document.getElementById("videoPreviewContainer");
            if (previewOpen && existing) {
              existing.remove();
              pvBtn.textContent = "▶ Watch Preview";
              previewOpen       = false;
              return;
            }
            const container   = document.createElement("div");
            container.id      = "videoPreviewContainer";
            container.style.cssText = "margin-top:8px;border-radius:10px;overflow:hidden;box-shadow:3px 3px 8px #bbb,-3px -3px 8px #fff;";

            if (videoSource === "mux" || videoSource === "direct") {
              const vid     = document.createElement("video");
              vid.src       = embedUrl;
              vid.controls  = true;
              vid.muted     = true;
              vid.autoplay  = true;
              vid.style.cssText = "width:100%;height:160px;display:block;background:#000;";
              container.appendChild(vid);
            } else {
              const iframe  = document.createElement("iframe");
              iframe.src    = embedUrl;
              iframe.style.cssText = "width:100%;height:160px;border:none;display:block;";
              iframe.allow  = "autoplay; fullscreen; picture-in-picture";
              iframe.allowFullscreen = true;
              container.appendChild(iframe);
            }
            pvBtn.insertAdjacentElement("afterend", container);
            pvBtn.textContent = "✕ Close Preview";
            previewOpen       = true;
          });
        }

        // Insert all buttons
        const anchor = preview.style.display !== "none" ? preview : statusEl;
        anchor.insertAdjacentElement("afterend", dlBtn);
        dlBtn.insertAdjacentElement("afterend", cpBtn);
        if (pvBtn) cpBtn.insertAdjacentElement("afterend", pvBtn);

        downloadBtn.style.display = "none";

      } else {
        showNoVideoUI();
      }
    } catch (err) {
      console.error("Detection error:", err);
      // This exact message means the content script in this tab is stale —
      // usually because the extension was reloaded (e.g. after an update)
      // while this tab was already open. Retrying the message won't help;
      // only reloading the page re-injects a fresh content script.
      if (String(err?.message || err).includes("Receiving end does not exist")) {
        statusEl.textContent = "⚠️ Please refresh this page (F5) and try again — the extension was just reloaded.";
      } else {
        statusEl.textContent = "⚠️ Could not scan this page.";
      }
      showNoVideoUI(true);
    }
  }

  // ============================================================
  // STEP 2 — HAND OFF TO CLIP.PULL (single round trip, no polling)
  // ============================================================
  async function sendToClipPull() {
    if (!detectedLink) return;

    ["retrySection", "enhancedDownloadBtn", "copyLinkBtn", "previewBtn", "videoPreviewContainer"]
      .forEach(id => document.getElementById(id)?.remove());

    spinner.style.display = "block";
    statusEl.textContent  = "📤 Sending to Clip.Pull...";

    const resp = await chrome.runtime.sendMessage({
      type: "SEND_TO_CLIPPULL",
      url: detectedLink,
      referer: "https://www.skool.com/",
      subfolder: courseName || null,
    });

    spinner.style.display = "none";

    if (resp?.ok) {
      statusEl.textContent = "✅ Sent! Check Clip.Pull's Queue tab for progress.";
      offerRestart("📤 Send Another");
      return;
    }

    if (videoSource === "loom" && resp?.error === "server_error") {
      showManualLoomInstructions(detectedLink);
      return;
    }

    const MESSAGES = {
      not_running: "Clip.Pull isn't running. Open the desktop app and try again.",
      no_output_folder: "Open Clip.Pull → Settings and set a default output folder first.",
      server_error: "Clip.Pull rejected the request." + (resp?.detail ? ` (${resp.detail})` : ""),
    };
    showGenericError(MESSAGES[resp?.error] || resp?.detail || "Could not reach Clip.Pull.");
  }

  // ============================================================
  // STARTUP
  // ============================================================
  downloadBtn.addEventListener("click", sendToClipPull);
  runDetection();
});
