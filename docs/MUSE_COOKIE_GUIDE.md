# Muse YouTube Cookie & Media Retrieval Guide

This guide explains how to configure verified YouTube cookies for the Muse worker, how media percentages match Vidzeo App, and how the pipeline handles vision tolerance and fallbacks.

---

## 1. Overview & Architecture

Muse uses the Vidnets documentary rendering engine with external agent reasoning. For YouTube media retrieval:
- **High-Resolution Access**: Authenticated sessions allow yt-dlp to bypass bot-checks and retrieve full 1080p/4K streams.
- **Parity with Vidzeo App**: Media percentages dynamically adapt by topic niche (e.g., Gaming: 80% YouTube / 20% Web; Documentary: 55% YouTube; Sports: 75% YouTube).
- **Dual Cookie Format Support**: The worker natively accepts **JSON cookie arrays** (e.g. from Cookie-Editor) as well as **Netscape `cookies.txt`** files.
- **Resilient Fallback**: If a cookie rotates or expires, the pipeline does not fail; it automatically falls back to unauthenticated public client streams with Node.js challenge solving (`js_runtimes: {'node': {}}`).

---

## 2. Quick Setup: Adding Your Cookies

You can supply your cookies in either **JSON** or **Netscape** format:

### Option A: Project Root (Recommended)
Save your cookie export as `cookies.json` (or `cookies.txt`) directly in the Muse worker directory:
```
vidzeo-muse-worker/
├── cookies.json       <-- Drop here (or cookies.txt)
├── adapters/
├── vidnets/
└── ...
```

### Option B: Vidnets Folder
Alternatively, place `cookies.json` or `cookies.txt` inside `vidnets/`:
```
vidzeo-muse-worker/vidnets/cookies.json
```

### Option C: Environment Variable
You can point to any custom path using an environment variable:
```bash
# In .env or PowerShell:
$env:VIDZEO_YT_COOKIES_PATH = "C:\Path\To\your_cookies.json"
# Or:
$env:VIDZEO_YT_COOKIE_FILE = "C:\Path\To\your_cookies.txt"
```

---

## 3. How to Export Cookies from Your Browser

### Method 1: Cookie-Editor Extension (JSON - Recommended)
1. Install **Cookie-Editor** for Chrome, Edge, or Firefox:
   - [Chrome Web Store: Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor)
2. Open [YouTube](https://www.youtube.com) and ensure you are logged in to your account.
3. Click the **Cookie-Editor** puzzle icon in your browser toolbar.
4. Click **Export** in the bottom right, then select **Export as JSON**.
5. Save or paste the clipboard into a file named `cookies.json` in the root of `vidzeo-muse-worker`.

### Method 2: Netscape Format (`cookies.txt`)
1. Install **Get cookies.txt LOCALLY** extension.
2. Open [YouTube](https://www.youtube.com).
3. Click **Export** to download `cookies.txt`.
4. Copy `cookies.txt` into `vidzeo-muse-worker/cookies.txt`.

---

## 4. Understanding Cookie Rotation (Google Security Notice)

> [!NOTE]
> **Google's Session Ticket (PSIDTS) Rotation**:
> In late 2023, Google introduced automated token binding (`__Secure-1PSIDTS`). If you actively navigate or click around on YouTube in the same browser window after exporting cookies, Google may issue a new session token, invalidating the exported one.
>
> **Best Practice for Muse Operators**:
> - Use a dedicated browser profile or incognito window for exporting cookies.
> - Once exported, avoid browsing actively on YouTube in that specific profile.

---

## 5. Media Retrieval & Percentages (Vidzeo Parity)

Muse matches Vidzeo App's adaptive media planner (`services/mixed_media_planner.py`):

| Content Niche | Base YouTube Clips | Base Web Images | Base Stock Media | Muse External Agent Policy (No Stock) |
| :--- | :---: | :---: | :---: | :---: |
| **Gaming / GTA VI** | **80%** | **15%** | **5%** | **80% YouTube / 20% Web** |
| **Sports / Action** | **75%** | **20%** | **5%** | **75% YouTube / 25% Web** |
| **Royal / Celebrity** | **65%** | **30%** | **5%** | **65% YouTube / 35% Web** |
| **Tech / Business** | **60%** | **35%** | **5%** | **60% YouTube / 40% Web** |
| **Documentary / History** | **55%** | **40%** | **5%** | **55% YouTube / 45% Web** |

### Operator Overrides
If you want to enforce a custom YouTube percentage across all jobs, set:
```bash
$env:MUSE_YOUTUBE_COVERAGE_TARGET = "0.80"  # Enforces 80% YouTube coverage
```
When unset, Muse automatically applies the exact niche profile matching Vidzeo App.

---

## 6. Vision Tolerance & Advisory Scoring

Muse vision operates on the **filter-best** principle just like Vidnets:
- **Advisory by Default**: `visual_required: false` and `strict_retrieval_accuracy: false`. An uncertain model verdict does not cancel the job.
- **Gaming & GTA VI Tolerance**:
  - Official trailer subtitles and captions are safely preserved without trigger rejection (`preserve_source_no_blur`).
  - Corner watermarks and publisher logos (e.g., Rockstar Games emblem) are marked as `watermark_tolerated: true`.
  - Game HUDs and letterboxing are preserved in their native aspect ratio.
- **Guaranteed Delivery**: `global_media_guarantee: true` ensures that every script beat is filled with real media or grounded graphics, preventing pipeline crashes.

---

## 7. Automatic Verification Test

To verify your cookie setup and retrieval at any time, run:
```bash
python "C:\Users\user\.gemini\antigravity\brain\75f847ec-c9c2-4c9b-8838-0c35fbc53218\scratch\test_muse_retrieval_parity.py"
```
Or run the worker test suite:
```bash
npm test
```
