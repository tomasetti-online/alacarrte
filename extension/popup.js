const statusEl = document.getElementById("status");
const tracksEl = document.getElementById("tracks");
const dlBtn = document.getElementById("dl-btn");
const cookieBtn = document.getElementById("cookie-btn");
const spinner = document.getElementById("spinner");
const statusView = document.getElementById("status-view");
const svAlbum = document.getElementById("sv-album");
const svProgress = document.getElementById("sv-progress");
const svTracks = document.getElementById("sv-tracks");

let currentUrl = "";
let API = null;
let pollTimer = null;
let pollStopped = false;

// Resolve the ALACarrte instance URL (set in the options page) and make sure
// this extension is allowed to reach it.
async function initApi() {
  if (API) return API;
  const { apiUrl } = await chrome.storage.sync.get({ apiUrl: "http://localhost:8080" });
  API = apiUrl.replace(/\/+$/, "");
  const origin = new URL(API).origin;
  const has = await chrome.permissions.contains({ origins: [origin + "/*"] });
  if (!has) {
    const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
    if (!granted) throw new Error("Permission to reach " + origin + " is required (set the URL in options)");
  }
  return API;
}

function isChannel(u) {
  return /youtube\.com\/(@[^\/]+|channel\/[^\/]+|c\/[^\/]+)/.test(u) && !/\/watch\b/.test(u);
}

async function jsonFetch(url, fd) {
  const resp = await fetch(url, { method: "POST", body: fd });
  const text = await resp.text();
  try { return JSON.parse(text); } catch(e) { throw new Error("Server error: empty response"); }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ---- Poll-to-status view ------------------------------------------------
// Lets the user watch a running download (and its per-track errors) right in
// the popup, without keeping the full status tab open.

function showStatusView(tid, data) {
  svTid = tid;
  dlBtn.style.display = "none";
  cookieBtn.style.display = "none";
  statusView.classList.remove("hidden");
  svAlbum.textContent = data.album || "";
  renderStatusProgress(data);
  renderStatusTracks(data.tracks);
}

function renderStatusProgress(data) {
  const [done, total, title] = data.progress || [0, 0, ""];
  let pct = 0;
  if (total > 0) {
    const term = data.tracks.filter(t => t.done || t.error).length;
    pct = Math.round(term / total * 100);
  }
  const label = (title ? `${done}/${total} ${title}` : `${done}/${total}`) + " · " + pct + "%";
  svProgress.textContent = label;
  const bar = svTracks.querySelector(".sv-progress-fill");
  if (bar) bar.style.width = pct + "%";
}

function renderStatusTracks(tracks) {
  let html = '<div class="sv-progress-bar"><div class="sv-progress-fill" style="width:0%"></div></div>';
  tracks.forEach((t, i) => {
    let badge;
    let retry = "";
    if (t.done) {
      badge = '<span class="sv-ok">Downloaded</span>';
    } else if (t.error) {
      badge = `<span class="sv-err" title="${esc(t.error)}">Failed: ${esc(t.error)}</span>`;
      retry = `<button class="sv-retry" data-tid="${esc(svTid)}" data-idx="${i}">Retry</button>`;
    } else {
      badge = '<span class="sv-pending">Queued</span>';
    }
    const name = (t.artist && t.artist !== "Unknown Artist" ? t.artist + " - " : "") + t.title;
    html += `<div class="sv-track"><span class="num">${i+1}</span><span class="sv-name" title="${esc(name)}">${esc(name)}</span>${badge}${retry}</div>`;
  });
  svTracks.innerHTML = html;
}

function pollStatus(tid) {
  pollStopped = false;
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (pollStopped) return;
    try {
      const resp = await fetch(`${API}/api/status/${tid}`);
      if (!resp.ok) throw new Error("status " + resp.status);
      const data = await resp.json();
      showStatusView(tid, data);
      if (data.status === "done" || data.status === "error") {
        stopPolling();
        chrome.storage.session.remove("alacarrteTid").catch(() => {});
        svAlbum.textContent = (data.album || "") + " — Complete";
      }
    } catch (err) {
      statusEl.textContent = "Status error: " + err.message;
    }
  }, 1500);
}

function stopPolling() {
  pollStopped = true;
  clearInterval(pollTimer);
}

async function resumeStatusView() {
  try {
    const { alacarrteTid } = await chrome.storage.session.get("alacarrteTid");
    if (!alacarrteTid) return false;
    const resp = await fetch(`${API}/api/status/${alacarrteTid}`);
    if (!resp.ok) { await chrome.storage.session.remove("alacarrteTid").catch(() => {}); return false; }
    const data = await resp.json();
    statusEl.textContent = "Watching existing download…";
    showStatusView(alacarrteTid, data);
    if (data.status !== "done" && data.status !== "error") {
      pollStatus(alacarrteTid);
    } else {
      await chrome.storage.session.remove("alacarrteTid").catch(() => {});
    }
    return true;
  } catch (err) {
    return false;
  }
}

let svTid = "";

async function main() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  currentUrl = tab.url;

  // Show cookie button on any YouTube page
  if (currentUrl.match(/youtu\.?be/)) {
    cookieBtn.style.display = "";
  }

  let api;
  try {
    api = await initApi();
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
    return;
  }

  // If a download is still running, resume watching it in the popup even if
  // the active tab isn't a YouTube page.
  if (await resumeStatusView()) return;

  if (!currentUrl.match(/youtu\.?be/)) {
    statusEl.textContent = "Navigate to a YouTube page first.";
    return;
  }

  statusEl.textContent = "Fetching...";
  const fd = new FormData();
  fd.append("url", currentUrl);

  try {
    if (isChannel(currentUrl)) {
      const data = await jsonFetch(`${api}/api/channel`, fd);
      const releases = data.releases || [];
      statusEl.textContent = `${data.artist || data.channel} — ${releases.length} release(s)`;
      let html = "";
      releases.slice(0, 12).forEach((r) => {
        html += `<div class="track"><span class="num">&#9834;</span><span class="title">${esc(r.title)}</span> <span style="color:#666;font-size:0.8rem">${r.count} tr.</span></div>`;
      });
      if (releases.length > 12) html += `<div class="track" style="color:#666">… and ${releases.length - 12} more</div>`;
      tracksEl.innerHTML = html;
      dlBtn.style.display = "block";
      dlBtn.textContent = "Open in Downloader";
      dlBtn.onclick = () => { window.open(`${api}/?url=${encodeURIComponent(currentUrl)}`, "_blank"); };
    } else {
      const data = await jsonFetch(`${api}/api/info`, fd);
      const isAlbum = data.playlist !== null;
      statusEl.textContent = isAlbum ? `${data.playlist} (${data.tracks.length} tracks)` : `${data.tracks.length} track(s)`;

      let html = "";
      if (isAlbum) {
        html += `<div class="album-label">Album</div><div class="track" style="margin-bottom:8px">${esc(data.playlist)}</div>`;
      }
      data.tracks.slice(0, 15).forEach((t, i) => {
        html += `<div class="track"><span class="num">${i+1}</span>`;
        if (t.artist !== "Unknown Artist") {
          html += `<span class="artist">${esc(t.artist)}</span> – <span class="title">${esc(t.title)}</span>`;
        } else {
          html += `<span class="title">${esc(t.title)}</span>`;
        }
        html += `</div>`;
      });
      if (data.tracks.length > 15) html += `<div class="track" style="color:#666">… and ${data.tracks.length - 15} more</div>`;
      tracksEl.innerHTML = html;

      dlBtn.style.display = "block";
      dlBtn.onclick = () => startDownload(currentUrl);
    }
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
  }
}

async function startDownload(url) {
  const api = await initApi();
  dlBtn.disabled = true;
  dlBtn.textContent = "Starting...";
  spinner.classList.remove("hidden");

  const fd = new FormData();
  fd.append("url", url);

  try {
    const data = await jsonFetch(`${api}/api/download`, fd);
    const tid = data.tid;
    window.open(`${api}/?url=${encodeURIComponent(currentUrl)}`, "_blank");
    statusEl.textContent = "Download started! Watching here…";
    if (tid) {
      svTid = tid;
      await chrome.storage.session.set({ alacarrteTid: tid }).catch(() => {});
      const st = await fetch(`${api}/api/status/${tid}`);
      if (st.ok) showStatusView(tid, await st.json());
      pollStatus(tid);
    }
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
  } finally {
    dlBtn.disabled = false;
    dlBtn.textContent = "Download All as ALAC";
    spinner.classList.add("hidden");
  }
}

main();

svTracks.addEventListener("click", async (e) => {
  const btn = e.target.closest(".sv-retry");
  if (!btn) return;
  const tid = btn.dataset.tid;
  const idx = btn.dataset.idx;
  try {
    await fetch(`${API}/api/retry-track/${tid}/${idx}`, { method: "POST" });
    btn.textContent = "…";
  } catch (err) {
    btn.textContent = "err";
  }
});

cookieBtn.onclick = sendCookies;

async function sendCookies() {
  const api = await initApi();
  cookieBtn.disabled = true;
  cookieBtn.textContent = "Exporting cookies...";

  try {
    const cookies = await chrome.cookies.getAll({ domain: "youtube.com" });
    const ytCookies = await chrome.cookies.getAll({ domain: ".youtube.com" });
    const all = [...cookies, ...ytCookies];
    const seen = new Set();
    const unique = all.filter(c => {
      const k = c.name + "=" + c.value + c.domain + c.path;
      if (seen.has(k)) return false;
      seen.add(k); return true;
    });

    let lines = ["# Netscape HTTP Cookie File", "# Exported by ALACarrte extension", ""];
    unique.forEach(c => {
      const secure = c.secure ? "TRUE" : "FALSE";
      const httpOnly = c.httpOnly ? "TRUE" : "FALSE";
      const exp = c.expirationDate ? Math.floor(c.expirationDate) : 0;
      lines.push(`${c.domain}\tTRUE\t${c.path}\t${secure}\t${exp}\t${c.name}\t${c.value}`);
    });

    const content = lines.join("\n");
    const fd = new FormData();
    fd.append("content", content);

    const resp = await fetch(`${api}/api/cookies`, { method: "POST", body: fd });
    const result = await resp.json();

    if (resp.ok) {
      cookieBtn.textContent = `Sent ${unique.length} cookies!`;
      statusEl.textContent = `${unique.length} YouTube cookies saved. Age-restricted downloads should work now.`;
      statusEl.style.color = "#6ee7b7";
    } else {
      cookieBtn.textContent = result.error || "Failed";
    }
  } catch (err) {
    cookieBtn.textContent = "Error: " + err.message;
    console.error(err);
  }

  setTimeout(() => {
    cookieBtn.disabled = false;
    cookieBtn.textContent = "Send YouTube Cookies to Server";
  }, 4000);
}
