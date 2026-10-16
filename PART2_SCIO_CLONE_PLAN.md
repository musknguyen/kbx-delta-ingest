# SCIO Clone — Build Plan

> Draft to hand to the team before the first line of code. Every number below has a "why"; the assumptions section lists what would change them.

---

## 0. TL;DR

| | Decision |
|---|---|
| **What** | A multi-tenant web portal + one cross-platform player that lets a small business pair screens, upload media, build playlists / split-screen layouts, schedule them, and trust that the screen keeps playing when the internet drops. |
| **Team** | 4 engineers: 1 tech lead (backend/architecture), 1 backend/infra, 1 frontend (portal), 1 player/frontend. Part-time designer (≈ 0.5) and QA from week 10. |
| **Timeline** | **16 weeks to a paid-beta MVP**, 5 phases, each ending with something demoable on a real TV. ~64 engineer-weeks + 20 % buffer already inside the phase estimates. |
| **Platforms** | Portal: web (desktop browsers). Player: **one web player** shipped as (a) Android APK wrapper (Fire TV / Android TV / sticks), (b) Electron for Windows/Linux mini-PCs, (c) plain URL for any browser/kiosk. No Tizen/webOS/BrightSign/Chrome OS native builds in v1. |
| **Biggest bet** | The player is the product. A portal bug is an annoyance; a black screen in a restaurant is a churned customer. Phase 1 therefore ships a working offline-capable player before any "nice" portal UI. |

---

## 1. What SCIO actually is (from using it as a customer)

Walking through the trial as a customer (pair a screen → upload → playlist → schedule → split screen), the product breaks into five surfaces:

1. **Screens / Devices** – pair by code shown on the TV, group into folders/locations, see online/offline, orientation, remote restart/reload, screenshot, per-screen "what is playing now".
2. **Files / Assets** – images, videos, PDFs/PowerPoint (converted to pages), web pages, and **Apps** (YouTube, weather, clock, RSS, Google Slides, dashboards, social feeds… > 100 of them). Apps are configured assets, not code the user writes.
3. **Playlists** – ordered list of assets with per-item duration, nested playlists, shuffle.
4. **Split Screen / Designer** – layout with zones, each zone plays an asset or playlist; Designer adds a canvas editor with widgets and data sources.
5. **Schedules** – dayparting rules ("breakfast menu 6–11 weekdays") with a default fallback, assigned to screens or groups; plus ad-hoc **Push/Takeover** of content to many screens at once.

Around them: team members & roles, billing per screen, white-label/API (enterprise), Engage (touch/QR/sensor interactivity), proof-of-play reports.

---

## 2. The thing I did not expect — and what it changed

**What I expected:** a screen plays *a playlist*; a schedule swaps one playlist for another. A 3-table model `screen → playlist → file`, and the player asks the server "what should I play?" every few seconds.

**What I found:** content is **recursive and polymorphic**, and assignment is **layered**:
- Anything can be assigned to a screen: a single file, an app, a playlist, a split-screen, a schedule. A schedule slot can point to a split-screen, whose zone points to a playlist, which contains an app and *another playlist*.
- On top of whatever is scheduled, an operator can **push/take over** a set of screens immediately, and the screen returns to its schedule afterwards.
- Apps are not files: a weather app or a YouTube link is a *configured asset* that renders live on the device.
- Help-center articles assume the screen keeps playing cached content offline — reliability is a product promise, not an implementation detail.

**What it changed in the plan:**

| Area | Before | After |
|---|---|---|
| Data model | `screens`, `playlists`, `files` | One **`content_node`** table with `type ∈ {file, app, playlist, layout, schedule}` + typed child tables; every "slot" (playlist item, layout zone, schedule rule, screen assignment) references a `content_node`. Cycle detection on save. |
| Player protocol | Player polls `/screens/:id/playlist` | Server **compiles a flat, versioned manifest per screen** (resolved schedule windows → layouts → zones → item lists, every asset URL + SHA-256). Player downloads assets, verifies checksums, swaps atomically, and evaluates schedule windows **locally** in the screen's timezone. Works fully offline. |
| Push / Takeover | Not considered | Modelled as a **priority overlay** in the manifest (`priority`, `starts_at`, `expires_at`). Override is just a higher-priority layer, so restoring the schedule is free. |
| Sequencing | Portal CRUD first, player last | **Player + manifest first** (Phase 1), because every other feature is "produce a different manifest". |
| Estimate | ~10 weeks | 16 weeks: the compiler, invalidation (which screens are affected when a nested playlist changes) and offline player are where the time goes. |

---

## 3. Scope

### In scope for the MVP (v1)
| # | Feature | Notes |
|---|---|---|
| 1 | Accounts, workspaces, team members, roles (Owner/Admin/Editor/Viewer) | Email+password and Google sign-in. |
| 2 | Screen pairing & management | 6-char code, folders/tags, online/offline (heartbeat), orientation, remote reload/restart, remote screenshot, timezone per screen. |
| 3 | Assets | Images, video (transcoded to H.264/AAC 1080p), PDF → pages, web page URL. Folders, search, usage ("where is this used?"). |
| 4 | Apps (top 6) | YouTube, Weather, Clock, Web page, Text/Announcement + QR, RSS ticker. Same App SDK used for all — adding the 7th app is a config file + renderer. |
| 5 | Playlists | Ordering via drag & drop, per-item duration, nested playlists, shuffle, preview. |
| 6 | Split-screen layouts | Templates + custom zones (% based), each zone → any content node, ticker zone. |
| 7 | Schedules | Weekly dayparting rules + date ranges + default content, screen-local time. |
| 8 | Push / Takeover | Push content to screens/tags for N minutes or until cancelled. |
| 9 | Offline-first player | Android wrapper, Electron, browser. Gapless playback, cache eviction, watchdog. |
| 10 | Basic billing | Stripe, price per screen/month, trial. |
| 11 | Proof-of-play (basic) | Player logs what played; per-screen CSV export. |

### Deliberately out of v1 (and why)
| Cut | Why |
|---|---|
| 100+ long-tail apps (Canva, Power BI, social feeds…) | Each needs OAuth + upkeep. Top 6 cover the core restaurant/retail use case; the App SDK makes the rest incremental. |
| Designer (canvas editor) & data sources | It is a product in itself (≈ 8–10 weeks alone). Layouts + Text app cover v1. |
| Native Tizen / webOS / BrightSign | Certification cycles and per-vendor quirks; Android sticks are cheap and cover the market test. |
| Engage (touch, QR interactivity, sensors, cameras) | Needs hardware integration; upsell, not table stakes. |
| SSO/SAML, SCIM, approval workflows, white-label, public API | Enterprise; revisit once there are enterprise leads. |
| Video walls / sync playback across screens | Requires clock sync between devices; niche. |
| Mobile app | Portal is responsive enough for checking screen status. |

---

## 4. Architecture

```
 Portal (React SPA) ──HTTPS──> API (NestJS) ──> PostgreSQL
                                   │   └──> Redis (presence, pub/sub, BullMQ)
                                   │
              upload (presigned) ──┼──> Object storage (R2/S3) ──> CDN
                                   │
                    Media workers (FFmpeg, PDF→PNG)  [BullMQ]
                                   │
                    Manifest compiler  [BullMQ, debounced per screen]
                                   │
 Player (web app in Android/Electron) <──WebSocket── Gateway (presence, "manifest vN ready", commands)
          └── downloads manifest + assets from CDN, caches locally, plays offline
```

| Layer | Choice | Why |
|---|---|---|
| Portal | React + TypeScript, TanStack Query, dnd-kit, a calendar component | Team knows it; drag-and-drop & calendar are solved problems. |
| API | Node.js + NestJS (TypeScript) | **Shared TypeScript types for the manifest** between compiler and player — the contract that matters most. |
| DB | PostgreSQL | Relational content graph, recursive CTEs for "which screens use this node", JSONB for app config. |
| Queue/presence | Redis + BullMQ | Transcode jobs, manifest compilation, heartbeat TTL keys, WS fan-out. |
| Storage/CDN | Cloudflare R2 + CDN (or S3+CloudFront) | Video egress dominates cost; R2 has no egress fees. |
| Player | One web app (TypeScript) + thin shells: Android WebView/ExoPlayer bridge, Electron | One codebase, three distributions. Video through native bridge on Android for reliability on cheap sticks. |
| Infra | Containers on a managed platform (ECS/Fly/Railway), Terraform, Sentry, OpenTelemetry | Small team, no k8s on day one. |

**Key design rules**
1. **Manifest is the contract.** Versioned JSON schema, contract-tested on both sides.
2. **Compile on write, not on read.** Editing a node enqueues recompilation for affected screens (reverse lookup via recursive CTE), debounced 2 s.
3. **Player never needs the server to keep playing.** Last good manifest + cached assets survive reboot; schedule evaluated locally.
4. **Everything idempotent** (uploads, pairing, manifest delivery) — devices retry a lot.

---

## 5. Roadmap — what, in what order, how long, and why

Estimates are in engineer-weeks (ew). Capacity: 4 engineers × 16 weeks = 64 ew.

### Phase 1 — Walking skeleton: pair a TV and play one image offline (weeks 1–3, 12 ew)
| Work | ew | Why this estimate |
|---|---|---|
| Repo, CI/CD, envs, auth, workspaces, roles | 3 | Boilerplate, but auth/tenancy mistakes are expensive to fix later. |
| Pairing flow (code on TV → claim in portal), device tokens, heartbeat/presence | 3 | WebSocket gateway + Redis TTL + reconnection edge cases. |
| Manifest v0 schema + compiler for "single asset" + player that caches & plays it offline | 4 | The core risk; worth over-investing now. |
| Direct-to-storage image upload, asset list | 2 | Presigned URLs, thumbnails. |

**Why first:** it de-risks the whole system end to end (portal → API → WS → player → offline). Exit test: unplug the router, reboot the stick, it still plays.

### Phase 2 — Media pipeline & playlists (weeks 4–6, 12 ew)
| Work | ew | Why |
|---|---|---|
| Video transcoding (FFmpeg workers), PDF → pages, progress UI | 4 | Customers upload anything; normalizing once avoids player codec bugs on cheap hardware. |
| Playlists (CRUD, drag & drop, durations, nesting, preview) | 4 | Nesting means the `content_node` model + cycle detection lands here. |
| Player: gapless playback (double-buffered video), cache eviction, checksum verify | 4 | Black frames between videos are the #1 visible quality issue. |

### Phase 3 — Layouts & Apps (weeks 7–10, 16 ew)
| Work | ew | Why |
|---|---|---|
| Split-screen editor (templates + custom zones) | 4 | Visual editor with snapping and % coordinates. |
| Recursive manifest compiler (layouts → zones → playlists → apps) + invalidation | 4 | The discovery from §2 lives here; heavy test coverage. |
| App SDK + 6 apps (YouTube, Weather, Clock, Web, Text/QR, RSS ticker) | 6 | ~1 ew per app incl. config UI + renderer + offline fallback; SDK 1 ew. |
| Player multi-zone renderer | 2 | Each zone is an independent mini-player. |

**Why here:** layouts and apps are what customers compare against competitors, but they only need the manifest to be richer — the pipeline from Phases 1–2 is reused.

### Phase 4 — Scheduling & Push (weeks 11–13, 12 ew)
| Work | ew | Why |
|---|---|---|
| Schedule model + calendar UI (weekly rules, date ranges, default) | 4 | Calendar UX is fiddly; conflicts must be visible. |
| Compiler: schedule windows into manifest; player evaluates locally in screen TZ (DST tests) | 3 | Timezone/DST bugs are classic signage failures. |
| Push/Takeover as priority overlay, bulk push to tags, expiry | 3 | Must reach online screens in < 5 s. |
| Billing (Stripe per screen) + trial limits | 2 | Needed before paid beta. |

### Phase 5 — Fleet operations & hardening (weeks 14–16, 12 ew)
| Work | ew | Why |
|---|---|---|
| Remote screenshot, reload/restart, offline alerts (email), device health | 3 | Support load drops when customers can self-diagnose. |
| Proof-of-play logging + CSV | 2 | Commonly requested by advertisers/franchise owners. |
| Player watchdog, auto-update channel, crash reporting | 3 | Unattended devices must self-heal. |
| Load test (5 000 simulated players), security review, beta onboarding | 4 | Validates WS fan-out and CDN costs before inviting customers. |

**Total: 64 ew.** Each phase includes ~20 % buffer (bug fixing, reviews); if we slip, cuts happen in this order: proof-of-play → RSS app → remote screenshot → custom zones (keep templates only).

### After MVP (rough, not committed)
Designer canvas (8–10 ew), next 15 apps (≈ 15 ew), Tizen/webOS players (≈ 6 ew each), SSO/SAML (3 ew), public API (4 ew), Engage QR interactivity (6 ew).

---

## 6. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Video playback on cheap Android sticks (codecs, memory) | Black screens, crashes | Transcode to one safe profile; native ExoPlayer bridge; device lab (Fire TV Stick, generic Android box, Windows NUC) from week 2. |
| Manifest invalidation misses a screen | Stale content | Reverse-dependency query tested on generated graphs; nightly full recompile as safety net; screen shows manifest version in portal. |
| Timezone / DST | Wrong content at 6 am | Schedule evaluated on device using IANA tz; property-based tests across DST transitions. |
| WebSocket scale | Push latency, server cost | Stateless gateway + Redis pub/sub; load test at 5 000 devices in Phase 5. |
| Storage/egress cost | Margin | R2 (no egress), content-hash dedupe, device-side caching. |
| Scope creep (apps, Designer) | Miss date | App SDK first; explicit cut list in §5. |

---

## 7. Quality & delivery

- **Testing:** unit tests on compiler & schedule evaluator (highest value), contract tests for manifest schema, Playwright for portal golden paths, a physical device lab run before every player release.
- **Release:** portal continuous deploy; player via staged auto-update channels (internal → 10 % → 100 %) with rollback.
- **Observability:** Sentry (portal, API, player), metrics on manifest compile time, push latency, % screens online, cache hit rate.
- **Definition of done per phase:** demo on real TVs + exit test passes + docs for support.

---

## 8. Assumptions that would change the numbers

- Team already fluent in TypeScript/React/Node; adding onboarding to a new stack → +2–3 weeks.
- A designer available ~50 %; without one the portal UI estimates rise ~25 %.
- "MVP" = paid beta for small/medium businesses, not enterprise-ready.
- If Tizen/webOS are mandatory on day one → +6 ew each and push Phase 5 by ~3 weeks.
- If the Designer canvas is in v1 → +8–10 ew (≈ +2.5 weeks with this team).
