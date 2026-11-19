# Changelog

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
