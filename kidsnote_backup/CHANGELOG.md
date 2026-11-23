# Changelog

## 0.4.1

- Shortened restart startup stagger from up to 30 minutes to 10–120 seconds (keeping the 0–30 minute spread for recurring 24-hour intervals).
- Updated the initial backup notice with a more realistic 2–5+ hour estimate for multi-year archives under standard pacing.
- Gracefully handled unavailable optional menus (schedules, medications, return-home, temperatures returning HTTP 403/404 or empty) without recording errors, and showed a clear empty-state explanation in the web UI.

## 0.4.0

- Added backup and viewer support (with new tabs and calendar integration) for 4 additional Kidsnote menus, all filtered from the earliest report date onward:
  - **일정표 (Schedules)** (`/service/schedule`)
  - **투약의뢰서 (Medication requests)** (`/service/medication`)
  - **귀가동의서 (Return-home consents)** (`/service/return-home`)
  - **체온기록 (Temperature logs)** (`/service/temperature/child`)
- Fixed catalog slowness / timeouts on large notice archives by lazily extracting PDF/HWP/DOCX text only when a search query is entered, and expanded the document text cache.
- Clarified progress logs when date filtering (`since`) is active on center collections.
- Restored standard server-friendly request pacing (`1s` API / `2s` media) after live verification.

## 0.3.13

- Temporarily reduced request pacing intervals (`0.1s`) for faster end-to-end backup testing (while keeping HTTP 429/503 cooldown protection active).

## 0.3.12

- Started the very first backup within 5–30 seconds after installation instead of waiting up to 30 minutes, while keeping the full 0–30 minute startup stagger for subsequent restarts after the initial backup succeeds.

## 0.3.11

- Added resumable backup checkpointing (`checkpoint.json`) so stopping and restarting the app mid-backup skips already completed stages and verified local records, resumes right where it stopped without missing any data, and starts within seconds instead of waiting for a full startup stagger.

## 0.3.10

- Fixed misleading progress counts like `25/1 (100%)` caused by Kidsnote endpoints whose `count` field is smaller than the actual number of records, and localized collection progress logs into clear Korean (`알림장`, `앨범`, `공지`, `식단`).

## 0.3.9

- Displayed the scheduled KST start time for the initial backup stagger in both the app logs and the web UI status bar / toast.

## 0.3.8

- Logged an initial backup notice and rough estimated duration range (from each collection's total `count`) plus live measured remaining time (ETA) in both the app logs and web UI header so users know how long the first backup will take.

## 0.3.7

- Added the Kidsnote child ID lookup URL (`https://www.kidsnote.com/api/v1/me/children/`) and JSON example directly to the `child_ids` configuration description (Korean & English).

## 0.3.6

- Kept the manual "지금 백업" button inactive until the first backup completes, and showed an immediate status toast when clicked while a backup is in progress or waiting for the initial run.

## 0.3.5

- Added detailed per-collection backup logs (start/finish, fetched, saved, unchanged, skipped-older, elapsed time, and detected center count) and live phase/count progress in the web UI header.
- Expanded center ID extraction to support dict enrollments, top-level center fields, and fallback recovery from report metadata, logging a warning if no center ID is found.
- Preserved earliest report dates even when previously backed-up reports are skipped as unchanged on restart.

## 0.3.4

- Limited center notice and menu backups to dates on or after the earliest report date of the enrolled children, skipping pre-enrollment records and stopping pagination early.

## 0.3.3

- Hardened viewer and scheduler concurrency, security, and edge cases:
  - Restored saved server cooldown before starting the viewer server and blocked duplicate manual backup triggers while a wake request is pending.
  - Made UI settings updates atomic under the state lock, enforced JSON content type on all POST endpoints, and capped POST body size to 16 KB.
  - Filtered report-sourced calendar events by selected child owner and normalized uppercase media extensions during delivery.
  - Ensured temporary ZIP buffers are always closed on error and validated cached client settings against allowlists.

## 0.3.2

- Fixed overly rounded corners (`999px`) in the Cute theme clipping video player controls, gallery photos, document previews, calendar day cards, and theme cards by introducing a dedicated `--radius-media` token (`1rem`).

## 0.3.1

- Added a "🎬 동영상만 보기" (videos only) checkbox filter to reports, albums, and notices, plus a video badge on cards containing video attachments.

## 0.3.0

- Added a settings modal (⚙) to customize the archive title, choose from 7 themes (System Auto, White, Black, Blue, Ivory, Cute, Futuristic), and select the default landing tab, synced across devices.
- Upgraded the calendar tab to show dated events alongside archived reports, albums, menus, and notices per day with category indicators and a child filter.
- Added a persistent checklist for supply requests with a remaining count and hide-completed toggle.
- Added an in-app photo lightbox with keyboard and touch-swipe navigation, one-click ZIP download for all post attachments, and print/PDF styling.
- Indexed attached document text (PDF, HWP, HWPX, PPTX, DOCX) in archive search.
- Added a manual "Back up now" trigger with live progress status (respecting server cooldowns) and optional Home Assistant `kidsnote_backup_completed` event notifications (`notify_ha`).

## 0.2.7

- Replaced the logo shown on the Home Assistant app page.

## 0.2.6

- Added the Kidsnote HA Backup icon and logo to the Home Assistant app store and app page.
- Changed the Home Assistant app stage to stable.

## 0.2.5

- Pace all login, API and attachment requests. Wait at least one second after API requests and two seconds after attachment requests, including retries.
- Pause after HTTP 429 or 503, honor Retry-After, and keep the cooldown across restarts.
- Spread backup start times and avoid repeated detail and comment requests for recently checked, unchanged older records.

## 0.2.4

- Fixed pagination for Kidsnote reports, albums, notices, menus and comments.
- Increased the number of requests the archive viewer can handle concurrently.

## 0.2.3

- Fixed the child ID input so the Home Assistant configuration form submits a list.
- Preserved support for existing numeric lists and documented the YAML syntax.

## 0.2.2

- Renamed the app and sidebar entry to Kidsnote HA Backup and moved the GitHub address to redchupa/kidsnote-ha-backup.
- Existing installations can update from their currently registered repository to keep using their archive.

## 0.2.1

- Added a numeric child ID example and step-by-step instructions for copying the Kidsnote session cookie.
- Displayed child IDs alongside names in the archive filter.

## 0.2.0

- Added a private in-app archive with search, date and child filters, and readable report details, comments and attachments.
- Added local supply requests and a calendar sourced from backed-up posts, teacher comments and readable PDF, HWP, HWPX, PPTX and DOCX files.
- Included locally downloadable comment attachments in subsequent backup runs.

## 0.1.0

- Initial Home Assistant app for recurring local Kidsnote backups.
- Includes multiple children, report details, comments, media, albums, notices, and menus.
- Reuses completed downloads, refreshes record metadata, and reports partial runs.
