# ALACarrte Chrome Extension

Send the YouTube page (or whole channel) you're looking at to your ALACarrte instance, and export your YouTube cookies for age-restricted downloads.

## Install

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `extension/` folder.
4. Pin the ALACarrte icon if you like.

## Configure your instance

The extension defaults to `http://localhost:8080`. If your ALACarrte server lives somewhere else:

1. Right-click the extension icon → **Options**.
2. Enter your instance's base URL (no trailing slash), e.g. `https://alacarrte.example.com`.
3. **Save**. The first time you use the extension it will ask for permission to reach that origin — accept it.

## Use

- On a **video** page: the popup shows the track(s) → **Download All as ALAC** starts the job and opens the status tab.
- On an **album/playlist** page: shows the track list → same download button.
- On a **channel** page: lists the releases → **Open in Downloader** jumps to the web UI.
- **Send YouTube Cookies to Server**: exports your logged-in YouTube cookies (Netscape format) to the server, enabling age-restricted or member-only downloads. Only runs when you click it.

## Notes

- The extension reads cookies for `youtube.com` only.
- No data leaves your browser except to the ALACarrte instance you configured.
- The `options` page stores only your instance URL (in `chrome.storage.sync`).
