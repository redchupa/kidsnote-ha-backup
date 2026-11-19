# Changelog

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
