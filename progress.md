# Plex Compress — Progress

## Status
Web UI v2 complete and validated. All identified issues fixed.

## Completed Tasks

### Web UI Design System v2
- Three-layer CSS token architecture (Primitive → Semantic → Component)
- Dark theme with glassmorphism sidebar, gradient buttons, glow effects
- Skeleton loaders on first load
- Empty states with icons for all tables
- `prefers-reduced-motion` and `prefers-contrast` support
- Mobile responsive with collapsible sidebar

### Frontend Fixes
1. **Stats grid orphaned card** — `minmax(240px, 1fr)` forces 3+3 layout
2. **Empty charts blank canvas** — `setChartEmpty()` overlay for no-data state
3. **Limit input no label** — Wrapped in `<label>Limit</label>`
4. **kbd tags unstyled** — Added keycap styling with background/border/shadow
5. **Shows empty state too tall** — `.card .empty-state` padding override

### Backend Fixes
6. **`/static/` 404** — Added missing route to `_build_routes()`
7. **`/api/logs` empty** — Reuse `self.log_handler` in `_make_logger()`
8. **`/api/logs?lines=50` 404** — Strip query strings in `_match_route()`
9. **Log duplicate timestamps** — UI handler uses plain `%(message)s` formatter
10. **`get_lines(limit=0)` bug** — Guard against invalid limits
11. **`lines` vs `limit` mismatch** — Support both query param names
12. **`_api_logs` 500 on bad limit** — `try/except` + clamp
13. **`_api_transcode` drops dry_run** — Only override when explicitly provided

### API Validation (All Passing)
- `GET /api/status` → 200, correct structure
- `GET /api/queue` → 200
- `GET /api/recent` → 200
- `GET /api/failed` → 200
- `GET /api/report` → 200, charts + tables
- `GET /api/config` → 200
- `POST /api/config` → 200, persists
- `GET /api/logs` → 200, actual log data
- `GET /api/logs?limit=N` → 200, N lines
- `GET /api/logs?lines=N` → 200, N lines
- `GET /api/logs?limit=abc` → 200, defaults to 100
- `GET /api/extensions` → 200
- `POST /api/health-check` → 200, async job
- `POST /api/scan` → 200, async job
- `POST /api/transcode` → 200, respects dry_run
- `POST /api/stop` → 200
- `GET /api/events` → 200, SSE stream
- `GET /static/style.css` → 200, text/css
- `GET /static/app.js` → 200, application/javascript

### Documentation Updates
- README.md — Added Web UI section with features, shortcuts, design system
- PLAN.md — Updated project structure, added webui/ directory, added design decisions
- AUDIT.md — Added Web UI security table, accessibility gaps, P5 accessibility priority
- TEST_PLAN.md — Added 8 Web UI test cases (WT-01 through WT-08), regression commands

## Files Changed
- `plex_compress/webui/static/style.css`
- `plex_compress/webui/static/index.html`
- `plex_compress/webui/static/app.js`
- `plex_compress/webui/server.py`
- `plex_compress/webui/runner.py`
- `plex_compress/webui/log_buffer.py`
- `README.md`
- `PLAN.md`
- `AUDIT.md`
- `TEST_PLAN.md`
- `progress.md` (this file)

## Deferred (Non-Blocking)
- Config form label `for` attributes (accessibility)
- Command palette ARIA roles + focus trap
- Toast `aria-live` announcements
- Mobile toggle `aria-expanded`
- Navigation `aria-current="page"`
- Reports view tablet breakpoint (2+1 cards)
- Live log SSE events (logs load on visit, not streamed)
- ConfigStore JSON thread safety
- `_make_logger` file descriptor leak on repeated jobs
- Windows path separator in show name extraction

## Next Steps
1. Run Phase 3 real-world validation tests
2. Consider adding auth for multi-user environments
3. Add systemd service file for auto-start
4. Consider nginx reverse proxy setup docs
