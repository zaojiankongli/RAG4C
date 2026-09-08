# Stage 23 Content Recovery Center Visual Acceptance

- Run: stage23-2026-08-29T16-00-36-551Z
- Date: 2026-08-29T16:01:34.519Z
- Revision: 0033_enterprise_content_recovery
- Frontend: http://127.0.0.1:5197
- Fulfilled API: http://127.0.0.1:5198
- Gate status: passed
- Production actions: false
- External network: false

## Matrix

12/12 cases passed.

- direct-light-1440: passed; viewport 1440x900; screenshot direct-light-1440-content-recovery.png
- direct-light-375: passed; viewport 375x812; screenshot direct-light-375-content-recovery.png
- direct-light-280: passed; viewport 280x720; screenshot direct-light-280-content-recovery.png
- direct-dark-1440: passed; viewport 1440x900; screenshot direct-dark-1440-content-recovery.png
- direct-dark-375: passed; viewport 375x812; screenshot direct-dark-375-content-recovery.png
- direct-dark-280: passed; viewport 280x720; screenshot direct-dark-280-content-recovery.png
- hash-light-1440: passed; viewport 1440x900; screenshot hash-light-1440-content-recovery.png
- hash-light-375: passed; viewport 375x812; screenshot hash-light-375-content-recovery.png
- hash-light-280: passed; viewport 280x720; screenshot hash-light-280-content-recovery.png
- hash-dark-1440: passed; viewport 1440x900; screenshot hash-dark-1440-content-recovery.png
- hash-dark-375: passed; viewport 375x812; screenshot hash-dark-375-content-recovery.png
- hash-dark-280: passed; viewport 280x720; screenshot hash-dark-280-content-recovery.png

## Scenarios

15/15 cases passed.

- baseline: passed; screenshot scenario-baseline.png
- restore: passed; screenshot scenario-restore.png
- legal-hold: passed; screenshot scenario-legal-hold.png
- legal-hold-release: passed; screenshot scenario-legal-hold-release.png
- retention-warning: passed; screenshot scenario-retention-warning.png
- purge-time-blocked: passed; screenshot scenario-purge-time-blocked.png
- purge-hold-blocked: passed; screenshot scenario-purge-hold-blocked.png
- approval-handoff: passed; screenshot scenario-approval-handoff.png
- partial: passed; screenshot scenario-partial.png
- unavailable: passed; screenshot scenario-unavailable.png
- empty: passed; screenshot scenario-empty.png
- read-only: passed; screenshot scenario-read-only.png
- scope-switch: passed; screenshot scenario-scope-switch.png
- documents-recycle: passed; screenshot scenario-documents-recycle.png
- keyboard: passed; screenshot scenario-keyboard.png

## Runtime safety

- Controlled loopback: true
- Unknown requests: 0
- Request failures: 0
- Console/page errors: 0
- Sensitive leak: false
- Horizontal overflow detected: false
- Source unchanged: true

## Gate failures

- none

## Fresh artifacts

- direct-dark-1440-content-recovery.png
- direct-dark-280-content-recovery.png
- direct-dark-375-content-recovery.png
- direct-light-1440-content-recovery.png
- direct-light-280-content-recovery.png
- direct-light-375-content-recovery.png
- hash-dark-1440-content-recovery.png
- hash-dark-280-content-recovery.png
- hash-dark-375-content-recovery.png
- hash-light-1440-content-recovery.png
- hash-light-280-content-recovery.png
- hash-light-375-content-recovery.png
- scenario-approval-handoff.png
- scenario-baseline.png
- scenario-documents-recycle.png
- scenario-empty.png
- scenario-keyboard.png
- scenario-legal-hold-release.png
- scenario-legal-hold.png
- scenario-partial.png
- scenario-purge-hold-blocked.png
- scenario-purge-time-blocked.png
- scenario-read-only.png
- scenario-restore.png
- scenario-retention-warning.png
- scenario-scope-switch.png
- scenario-unavailable.png
