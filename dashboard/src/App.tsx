import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "./api";
import { GuestGate, useGuestIdle } from "./guestGate";
import IssueDetailPage from "./pages/IssueDetailPage";
import IssuesPage from "./pages/IssuesPage";
import MonitorOnboardingPage from "./pages/MonitorOnboardingPage";
import RemoteAgentPage from "./pages/RemoteAgentPage";
import AboutPage, { VersionHistoryPage } from "./pages/AboutPage";
import OverviewPage from "./pages/OverviewPage";
import MonitorLogPage from "./pages/MonitorLogPage";
import MonitorPage from "./pages/MonitorPage";
import SettingsLayout from "./pages/SettingsLayout";
import PathPage from "./pages/PathPage";
import AirwavesLayout from "./pages/airwaves/shared";
import AirwavesOverview from "./pages/airwaves/OverviewPage";
import AirwavesNetworks from "./pages/airwaves/NetworksPage";
import AirwavesChannels from "./pages/airwaves/ChannelsPage";
import AirwavesClients from "./pages/airwaves/ClientsPage";
import GuestVisitsPage from "./pages/GuestVisitsPage";
import InternetServicePage from "./pages/InternetServicePage";
import SyntheticPage from "./pages/SyntheticPage";
import TelegramPage from "./pages/TelegramPage";
import WebServicesPage from "./pages/WebServicesPage";
import ConnectionDiagramPage from "./pages/ConnectionDiagramPage";
import TestPage from "./pages/TestPage";
import AppFooter from "./components/AppFooter";
import KpiPage from "./pages/KpiPage";
import NetworkTestPage from "./pages/NetworkTestPage";
import SpeedTestPage from "./pages/SpeedTestPage";
import type { User } from "./types";

function useDarkTheme() {
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "dark");
    document.documentElement.style.colorScheme = "dark";
    localStorage.removeItem("hns-theme");
  }, []);
}

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async () => {
      await api.login(username, password);
      try {
        return (await api.me()) as User;
      } catch {
        throw new Error("The password was accepted, but the browser did not keep the session. Refresh and try again.");
      }
    },
    onSuccess: () => onSuccess(),
    onError: (e: Error) => setError(e.message || "Login failed"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    mutation.mutate();
  }

  return (
    <div className="login-wrap ui-compact">
      <form className="login-card" onSubmit={submit}>
        <h1>HomeNetWatch</h1>
        <p>Sign in to view remote agent health and staged test results.</p>
        {error && <div className="error">{error}</div>}
        <div className="field">
          <label htmlFor="username">Username</label>
          <input id="username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <button className="btn btn-primary" type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <AppFooter />
    </div>
  );
}

function LegacyMonitorRedirect({ log = false }: { log?: boolean }) {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const query = params.toString();
  const path = `/monitors/${id}${log ? "/log" : ""}`;
  return <Navigate to={query ? `${path}?${query}` : path} replace />;
}

function MenuIcon({ name }: { name: "airwaves" | "issues" | "settings" | "path" | "speed" | "network" | "kpi" | "about" | "logout" }) {
  return (
    <svg className="menu-icon" viewBox="0 0 24 24" aria-hidden="true">
      {name === "airwaves" ? (
        // the waves are short and wide, so they are drawn larger to match the weight of the other icons
        <g transform="translate(12 12) scale(1.25) translate(-12 -12.75)" strokeWidth={1.4}>
          <path d="M4.5 16a7.5 7.5 0 0 1 15 0" />
          <path d="M8 16a4 4 0 0 1 8 0" />
          <circle className="menu-icon-dot" cx="12" cy="16" r="1.15" />
        </g>
      ) : null}
      {name === "issues" ? (
        <>
          <circle cx="12" cy="12" r="7.5" />
          <path d="M12 8v5" />
          <circle className="menu-icon-dot" cx="12" cy="16" r="0.9" />
        </>
      ) : null}
      {name === "settings" ? (
        <>
          <path d="M4 7h16M4 12h16M4 17h16" />
          <circle cx="9" cy="7" r="2" />
          <circle cx="15" cy="12" r="2" />
          <circle cx="8" cy="17" r="2" />
        </>
      ) : null}
      {name === "kpi" ? (
        <>
          <rect x="3.5" y="13" width="4.5" height="7.5" rx="1.2" fill="currentColor" stroke="none" />
          <rect x="9.75" y="4" width="4.5" height="16.5" rx="1.2" fill="currentColor" stroke="none" />
          <rect x="16" y="9" width="4.5" height="11.5" rx="1.2" fill="currentColor" stroke="none" />
        </>
      ) : null}
      {name === "path" ? (
        <>
          <circle cx="6" cy="16.5" r="2" />
          <circle cx="12" cy="7.5" r="2" />
          <circle cx="18" cy="14.5" r="2" />
          <path d="M7.6 15.2 10.4 9M13.6 8.6 16.5 13.2" />
        </>
      ) : null}
      {name === "speed" ? (
        <g transform="translate(12 12) scale(1.15) translate(-12 -11.5)" strokeWidth={1.5}>
          <path d="M6 16.5a6 6 0 1 1 12 0" />
          <path d="M12 16.5 16.2 11" />
          <circle className="menu-icon-dot" cx="12" cy="16.5" r="1" />
        </g>
      ) : null}
      {name === "network" ? (
        <>
          <circle cx="12" cy="12" r="7.5" />
          <path d="M4.5 12h15" />
          <path d="M12 4.5c2.4 2 3.6 4.6 3.6 7.5s-1.2 5.5-3.6 7.5c-2.4-2-3.6-4.6-3.6-7.5s1.2-5.5 3.6-7.5" />
        </>
      ) : null}
      {name === "about" ? (
        <>
          <circle cx="12" cy="12" r="7.5" />
          <path d="M12 11v5" />
          <circle className="menu-icon-dot" cx="12" cy="8" r="0.9" />
        </>
      ) : null}
      {name === "logout" ? (
        <>
          <path d="M9 7V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2v-1" />
          <path d="M4 12h10" />
          <path d="m11 8 4 4-4 4" />
        </>
      ) : null}
    </svg>
  );
}

function Shell({ role, onLogout }: { role: User["role"]; onLogout: () => void }) {
  const loc = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  useDarkTheme();
  useGuestIdle(role, onLogout);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const links = [
    { to: "/airwaves", label: "Airwaves", icon: "airwaves" as const },
    { to: "/issues", label: "Issues", icon: "issues" as const },
    ...(role === "admin" || role === "guest"
      ? [{ to: "/settings/onboarding", label: "Settings", icon: "settings" as const }]
      : []),
  ];

  useEffect(() => {
    setMenuOpen(false);
  }, [loc.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    function onPointer(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const showBack = loc.pathname !== "/";
  const monitorId = loc.pathname.match(/^\/monitors\/([^/]+)$/)?.[1] ?? null;
  const pathLink = params.get("link") === "wireless" ? "wireless" : params.get("link") === "wired" ? "wired" : null;
  const pathQuery = new URLSearchParams();
  if (monitorId) pathQuery.set("agent", monitorId);
  if (pathLink) pathQuery.set("link", pathLink);
  else if (monitorId) pathQuery.set("link", "wired");
  const pathTo = pathQuery.toString() ? `/path?${pathQuery.toString()}` : "/path";

  function goBack() {
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (idx > 0) navigate(-1);
    else navigate("/");
  }

  const seeSettings = role === "admin" || role === "guest";

  return (
    <GuestGate role={role}>
      <div className={loc.pathname === "/" ? "app-shell dash-shell" : "app-shell ui-compact"}>
      <header className="topbar">
        <div className="brand">
          <img src="/homenetwatch.png" alt="HomeNetWatch" />
        </div>
        <div className="topbar-actions">
          {showBack ? (
            <button className="btn icon-button" type="button" aria-label="Go back" onClick={goBack}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M15.5 4 8.5 12l7 8"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span className="icon-tip">Go back</span>
            </button>
          ) : null}
          {showBack ? (
            <Link className="btn icon-button" to="/" aria-label="Home">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M4 11 12 4l8 7"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M7 10.25V20h10V10.25"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span className="icon-tip">Home</span>
            </Link>
          ) : null}
          <div className="menu" ref={menuRef}>
          <button
            className="btn icon-button menu-button"
            type="button"
            aria-label="Show more"
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="4.725" r="1.6" fill="currentColor" />
              <circle cx="12" cy="12" r="1.6" fill="currentColor" />
              <circle cx="12" cy="19.275" r="1.6" fill="currentColor" />
            </svg>
          </button>
          {menuOpen ? (
            <div className="menu-panel" role="menu">
              {links
                .filter((l) => l.to === "/airwaves")
                .map((l) => {
                  const active = loc.pathname.startsWith(l.to);
                  return (
                    <Link key={l.to} to={l.to} role="menuitem" className={active ? "active" : ""}>
                      <MenuIcon name={l.icon} />
                      {l.label}
                    </Link>
                  );
                })}
              <Link to={pathTo} role="menuitem" className={loc.pathname === "/path" ? "active" : ""}>
                <MenuIcon name="path" />
                Path View
              </Link>
              <Link to="/speed-test" role="menuitem" className={loc.pathname === "/speed-test" ? "active" : ""}>
                <MenuIcon name="speed" />
                Speed Test
              </Link>
              <Link to="/network-test" role="menuitem" className={loc.pathname === "/network-test" ? "active" : ""}>
                <MenuIcon name="network" />
                Network Test
              </Link>
              <Link to="/kpi" role="menuitem" className={loc.pathname === "/kpi" ? "active" : ""}>
                <MenuIcon name="kpi" />
                KPI Dashboard
              </Link>
              <div className="menu-rule" role="separator" />
              {links
                .filter((l) => l.to !== "/airwaves")
                .map((l) => {
                  const active = l.to.startsWith("/settings")
                    ? loc.pathname.startsWith("/settings")
                    : loc.pathname.startsWith(l.to);
                  return (
                    <Link key={l.to} to={l.to} role="menuitem" className={active ? "active" : ""}>
                      <MenuIcon name={l.icon} />
                      {l.label}
                    </Link>
                  );
                })}
              <div className="menu-rule" role="separator" />
              <Link to="/about" role="menuitem" className={loc.pathname.startsWith("/about") ? "active" : ""}>
                <MenuIcon name="about" />
                About
              </Link>
              <button className="menu-item" type="button" role="menuitem" onClick={onLogout}>
                <MenuIcon name="logout" />
                Log out
              </button>
            </div>
          ) : null}
          </div>
        </div>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/path" element={<PathPage />} />
          <Route path="/speed-test" element={<SpeedTestPage />} />
          <Route path="/network-test" element={<NetworkTestPage />} />
          <Route path="/kpi" element={<KpiPage />} />
          <Route path="/airwaves" element={<AirwavesLayout />}>
            <Route index element={<AirwavesOverview />} />
            <Route path="networks" element={<AirwavesNetworks />} />
            <Route path="clients" element={<AirwavesClients />} />
            <Route path="channels" element={<AirwavesChannels />} />
          </Route>
          <Route path="/monitors/:id/connection" element={<ConnectionDiagramPage />} />
          <Route path="/monitors/:id" element={<MonitorPage />} />
          <Route path="/monitors/:id/log" element={<MonitorLogPage />} />
          <Route path="/sensors/:id/log" element={<LegacyMonitorRedirect log />} />
          <Route path="/sensors/:id" element={<LegacyMonitorRedirect />} />
          <Route path="/tests/:id" element={<TestPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="/about/history" element={<VersionHistoryPage />} />
          <Route path="/issues" element={<IssuesPage />} />
          <Route path="/issues/:id" element={<IssueDetailPage />} />
          <Route path="/settings" element={seeSettings ? <SettingsLayout /> : <Navigate to="/" replace />}>
            <Route index element={<Navigate to="onboarding" replace />} />
            <Route path="onboarding" element={<MonitorOnboardingPage />} />
            <Route path="internet-service" element={<InternetServicePage />} />
            <Route path="synthetic" element={<SyntheticPage />} />
            <Route path="web-services" element={<WebServicesPage />} />
            <Route path="telegram" element={<TelegramPage />} />
            <Route path="guest-visits" element={<GuestVisitsPage />} />
            <Route path="agents/:id" element={<RemoteAgentPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <AppFooter />
      </div>
    </GuestGate>
  );
}

export default function App() {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me() as Promise<User>,
    retry: false,
    refetchInterval: false,
  });

  if (me.isLoading) {
    return <div className="state-box">Loading session…</div>;
  }

  if (me.isError || !me.data) {
    return (
      <Login
        onSuccess={() => {
          void qc.invalidateQueries({ queryKey: ["me"] });
        }}
      />
    );
  }

  return (
    <Shell
      role={me.data.role}
      onLogout={() => {
        void api.logout().finally(() => {
          qc.setQueryData(["me"], null);
          void qc.invalidateQueries();
        });
      }}
    />
  );
}
