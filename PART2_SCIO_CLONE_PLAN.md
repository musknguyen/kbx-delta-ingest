# SCIO Digital Signage Cloud — Clean-Room Architecture & Execution Plan

## 1. Executive Summary & Architectural Thesis

Building a digital signage management portal like SCIO (`app.optisigns.com`) looks like a standard CRUD web application on the surface, but in practice it is a **distributed state synchronization and offline-first media delivery system**.

The core architectural principle of this plan is **Event-Driven Desired-State Reconciliation**:
1. **The Web Portal (SCIO)** mutates the *Desired State* of a Screen, Playlist, Schedule, or Layout Zone in PostgreSQL.
2. **A Manifest Compiler** computes a deterministic dependency graph (JSON Manifest) containing every asset URL, SHA-256 checksum, layout coordinate, and cron window required by affected screens.
3. **A Real-Time Push Broker (WebSockets/MQTT)** notifies online players of a new manifest version, while offline players reconcile state automatically upon reconnection.
4. **The Player Client** downloads missing assets into local persistent storage (`IndexedDB` / filesystem), verifies checksums, and swaps playback atomically—guaranteeing **zero black-screen transitions** even if the internet drops mid-playback.

---

## 2. The "Unexpected Discovery" & How It Changed This Plan

### What I Found
While testing `app.optisigns.com` as an end customer—specifically inside the **Split-Screen Layout Designer** and **Playlist Assignment flow**—I initially expected screens to play flat sequences of media files, and schedules to simply swap one playlist for another.

Instead, I discovered **Polymorphic, Recursive Content Nesting combined with Priority Override Layers**:
1. **Recursive Composition:** A Screen can be assigned a *Schedule*, which points to a *Split-Screen Layout*, where *Zone A* plays a *Playlist* (containing both uploaded MP4s and live dynamic "Apps" like Weather/YouTube/Canva), *Zone B* plays another *Nested Playlist*, and *Zone C* renders a live *Scrolling Ticker*.
2. **Ephemeral Interrupts ("Takeover" / Quick Push):** On top of this scheduled hierarchy, an operator can trigger an immediate screen takeover or emergency broadcast that temporarily preempts the running schedule and cleanly restores the prior state afterward.

### What It Changed in My Plan
* **Before Exploring the Product (Naive Plan):** I planned to model `Screens -> Playlists -> MediaFiles` as a simple 3-table relational hierarchy and have the player query `/api/screens/:id/playlist` to loop through URLs.
* **After Exploring the Product (Revised Plan):**
  1. **Unified Polymorphic `PlayableNode` Data Model:** Everything playable (`ASSET`, `APP_INSTANCE`, `PLAYLIST`, `LAYOUT`) is abstracted behind a unified polymorphic reference so Layout Zones, Playlist Slots, and Schedule Windows can reference any content type without schema rewrites.
  2. **Server-Side Manifest Flattening (`ResolvedScreenManifest`):** Moving recursive resolution to the edge player would cause race conditions and complex client queries. Whenever a user edits a Playlist, Layout, or Schedule in SCIO, the backend pre-compiles a **flattened, self-contained JSON Manifest** for each affected screen. The player remains a deterministic state machine that reconciles local storage against `manifest.required_assets[]` and renders the active time slot.

---

## 3. Scope Definition: What We Build vs. What We Cut

* **Team Size:** **4 Engineers** (1 Tech Lead/Architect, 1 Backend/Infra Engineer, 2 Frontend/Player Engineers).
* **Target Timeline:** **16 Weeks (4 Months)** to Production MVP.
* **Target Player Platform:** **1 Offline-First PWA / Electron Reference Player** compatible with browsers, Windows/Linux mini-PCs, and Android WebView wrappers.

### In-Scope (Core Value Proposition)
1. **Multi-Tenant Workspace & Folder Hierarchy:** Organization accounts, basic RBAC (Admin, Editor), folder organization for assets and screens.
2. **Screen Pairing & Device Management:** 6-digit alphanumeric PIN pairing flow, online/offline heartbeat monitoring, remote screenshot capture, remote reload command, screen orientation (Landscape/Portrait).
3. **Asset Management Pipeline:** Direct-to-R2 multipart upload for images (`JPG`, `PNG`, `WEBP`), videos (`MP4` transcoding to H.264 1080p), and documents (`PDF` converted server-side into slide images).
4. **Playlist Engine:** Sequential ordering, per-item duration overrides, drag-and-drop reordering, sub-playlist nesting.
5. **Split-Screen Layout Designer:** Visual multi-zone editor (pre-built templates + custom % coordinates), independent zone playlists, and bottom scrolling text ticker.
6. **Core Built-in Apps (Top 5):** YouTube, Weather, Clock/Date, Website URL (iframe), and Announcements/QR Code generator.
7. **Calendar Scheduler & Dayparting:** Recurring weekly/daily schedules, default fallback content, and instant "Takeover / Interrupt" override.

### Deliberately Out-of-Scope for Phase 1
* **140+ Long-Tail Third-Party Apps:** Shipping the top 5 Apps covers 85% of initial retail/restaurant use cases without bogging down the team in OAuth maintenance.
* **Native Tizen (Samsung) & webOS (LG) SoC Builds:** Commercial B2B TV SDKs have strict certification cycles; a browser/Android/Electron player validates the market first.
* **AI Camera Audience Analytics & Hardware Sensor Triggers:** Enterprise upsells, not table stakes for core signage.
* **Enterprise SSO (SAML/SCIM) & Approval Workflows:** Deferred until moving upmarket.

---

## 4. System Architecture & Technical Stack

* **Frontend (SCIO Portal & Player UI):** `React 18`, `TypeScript`, `TailwindCSS`, `Zustand`, `@dnd-kit` (playlists), `Konva.js` / DOM Canvas (Layout Designer), `FullCalendar` (Schedule view).
* **Backend Control Plane:** `Node.js` + `NestJS` (TypeScript) for shared manifest interfaces between server and player.
* **Database & Queue:** `PostgreSQL 16` + `Redis` (device heartbeat TTL tracking, WebSocket pub/sub adapter, and `BullMQ` media transcoding queues).
* **Media Storage & Delivery:** `Cloudflare R2` + `Cloudflare CDN` ($0 egress fees for heavy video downloads).
* **Media Processing Worker:** `FFmpeg` (normalizing videos to H.264 MP4 `yuv420p`) and `Poppler/pdf2image` (converting PDFs into static slide arrays).

---

## 5. Sequence & Execution Roadmap (16 Weeks / 5 Phases)

| Phase | Milestone | Duration | Eng-Weeks | Why Sequenced Here & Key Mitigation |
| :--- | :--- | :---: | :---: | :--- |
| **Phase 1** | Foundation, PIN Pairing & Single Push | Weeks 1–3 (3 Wks) | 12 | Proves the end-to-end vertical slice (Web -> API -> WS -> Player offline cache) immediately. |
| **Phase 2** | Media Transcoding & Gapless Playlists | Weeks 4–6 (3 Wks) | 12 | Normalizes customer uploads via FFmpeg and implements dual `<video>` pre-buffering to eliminate black flashes. |
| **Phase 3** | Multi-Zone Layouts & Core 5 Apps | Weeks 7–10 (4 Wks) | 16 | Delivers commercial signage differentiation with sandboxed iframe Apps and recursive manifest compilation. |
| **Phase 4** | Dayparting Scheduler & Takeovers | Weeks 11–13 (3 Wks) | 12 | Automates time-of-day transitions in Screen Local Time and adds <2s emergency screen interrupts. |
| **Phase 5** | Fleet Telemetry, Watchdog & QA | Weeks 14–16 (3 Wks) | 12 | Adds Redis heartbeat alerts, remote screenshot verification, player watchdog timers, and 5,000-screen load tests. |
| **TOTAL** | **Production-Ready SCIO Clone MVP** | **16 Wks** | **64 Eng-Wks** | **4 Full-Time Engineers over 4 Months** |
