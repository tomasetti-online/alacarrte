const input = document.getElementById("api");
const status = document.getElementById("status");

chrome.storage.sync.get({ apiUrl: "http://localhost:8080" }, (opts) => {
  input.value = opts.apiUrl;
});

document.getElementById("save").addEventListener("click", async () => {
  let url = input.value.trim().replace(/\/+$/, "");
  status.className = "";
  if (!/^https?:\/\//.test(url)) {
    status.textContent = "Enter a full URL like http://localhost:8080";
    status.className = "err";
    return;
  }
  try { new URL(url); } catch (e) {
    status.textContent = "That does not look like a valid URL";
    status.className = "err";
    return;
  }
  await chrome.storage.sync.set({ apiUrl: url });
  status.textContent = "Saved. The popup will use this instance.";
  status.className = "ok";
  setTimeout(() => { status.textContent = ""; }, 3000);
});
