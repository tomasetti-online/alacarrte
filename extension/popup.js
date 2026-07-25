const API = "https://alacarrte.tomasetti.online";
const statusEl = document.getElementById("status");
const tracksEl = document.getElementById("tracks");
const dlBtn = document.getElementById("dl-btn");
const spinner = document.getElementById("spinner");

let currentUrl = "";

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

  if (!currentUrl.match(/youtu\.?be/)) {
    statusEl.textContent = "Navigate to a YouTube page first.";
    return;
  }

  statusEl.textContent = "Fetching...";
  const fd = new FormData();
  fd.append("url", currentUrl);

  try {
    if (isChannel(currentUrl)) {
      const data = await jsonFetch(`${API}/api/channel`, fd);
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
      dlBtn.onclick = () => { window.open(`${API}/?url=${encodeURIComponent(currentUrl)}`, "_blank"); };
    } else {
      const data = await jsonFetch(`${API}/api/info`, fd);
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
  dlBtn.disabled = true;
  dlBtn.textContent = "Starting...";
  spinner.classList.remove("hidden");

  const fd = new FormData();
  fd.append("url", url);

  try {
    const data = await jsonFetch(`${API}/api/download`, fd);
    window.open(`${API}/?url=${encodeURIComponent(currentUrl)}`, "_blank");
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
