const statusEl = document.getElementById("status");
const tracksEl = document.getElementById("tracks");
const dlBtn = document.getElementById("dl-btn");
const cookieBtn = document.getElementById("cookie-btn");
const spinner = document.getElementById("spinner");

let currentUrl = "";
let API = null;

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

async function main() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  currentUrl = tab.url;

  // Show cookie button on any YouTube page
  if (currentUrl.match(/youtu\.?be/)) {
    cookieBtn.style.display = "";
  }

  if (!currentUrl.match(/youtu\.?be/)) {
    statusEl.textContent = "Navigate to a YouTube page first.";
    return;
  }

  let api;
  try {
    api = await initApi();
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
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
    window.open(`${api}/?url=${encodeURIComponent(currentUrl)}`, "_blank");
    statusEl.textContent = "Download started! Check the tab.";
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
  } finally {
    dlBtn.disabled = false;
    dlBtn.textContent = "Download All as ALAC";
    spinner.classList.add("hidden");
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

main();

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
