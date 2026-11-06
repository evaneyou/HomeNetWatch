# Dashboard

React + TypeScript single-page app for HomeNetWatch. It reads the server's REST API and
never talks to a Pi directly.

## Stack

React 19, Vite 7, TypeScript, TanStack Query, React Router, Recharts, Leaflet.

## Develop

```bash
npm ci
npm run dev          # http://127.0.0.1:3847
```

The API address comes from `VITE_API_BASE_URL` (default `http://127.0.0.1:8847`). Start the server
first; the root [README](../README.md) shows how.

## Build

```bash
npm run build        # type-check (tsc -b) then bundle to dist/
npm run preview      # serve the production build on port 3847
```

The Docker image builds the app and serves `dist/` with `serve`. `VITE_API_BASE_URL` is baked in
at build time, so rebuild the image if it changes.

## Screens

| Route | Screen |
|-------|--------|
| `/` | Home: open-issue banner and one card per remote agent (Pi or Android TV) with its Web, Wired, Wi-Fi and Apps tiles |
| `/monitors/:id` | Remote agent detail: device health, networks, test status (`?network=…&detail=…&link=…` views) |
| `/monitors/:id/connection` | Network path diagram for the agent's tests |
| `/monitors/:id/log` | Result log for a remote agent |
| `/tests/:id` | Test detail: stage waterfall, pass/fail strip, latency and loss charts |
| `/path` | Path View: traceroute grouped into networks, latency by hop, BGP view, trace any host |
| `/speed-test` | Live Speed Test (Wired or Wi-Fi; links the device lacks are greyed out) |
| `/network-test` | Network Test: multi-session stress runs |
| `/kpi` | KPI Dashboard: availability, quality and speed reports, daily history, Determine Root Cause |
| `/airwaves` (`networks`, `clients`, `channels`, `security`) | Wi-Fi scans, clients, channel graph, and the Security tab (WIDS alerts, trust, LAN scans) |
| `/issues`, `/issues/:id` | Open and resolved issues |
| `/server-health`, `/server-health/software` | Server Health (admin and guest) and the software and vulnerability list |
| `/settings/onboarding` | Remote Agent Onboarding: installer codes, waiting devices, approval |
| `/settings/agents/:id` | One remote agent's settings |
| `/settings/internet-service` | Internet service checks and Bandwidth Test |
| `/settings/synthetic` | Real-Time Application Health profiles |
| `/settings/web-services` | Web-service HTTP checks |
| `/settings/telegram` | Telegram alerts |
| `/settings/gemini` | Google Gemini API key and model |
| `/settings/guest-visits` | Guest sign-ins and where they came from |
| `/about`, `/about/history` | About and version history |

Old `/sensors/:id` links redirect to `/monitors/:id`.

Roles are enforced by the server. A viewer gets a read-only UI with write controls hidden and no Settings
or Server Health. A guest can open every page, including Settings, read-only: the server refuses its
changes, and the dashboard blurs the fields the server marks as identifying. A guest session ends after
3 minutes idle.

## Layout

```
src/pages/        Route-level screens
src/components/   Shared UI (charts, map, dialogs, device panel)
src/api.ts        API client
src/types.ts      API types
src/anonymized.tsx  Guest blur for fields the server marks
src/deviceType.ts   Pi or Android platform, and which links a device has
public/           Logo and icons
```
