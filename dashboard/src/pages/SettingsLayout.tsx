import { NavLink, Outlet } from "react-router-dom";

const PAGES = [
  { to: "/settings/onboarding", label: "Remote Agent Onboarding" },
  { to: "/settings/internet-service", label: "Internet Service" },
  { to: "/settings/synthetic", label: "Real-Time Application Health" },
  { to: "/settings/web-services", label: "Web Services" },
  { to: "/settings/telegram", label: "Configure Telegram" },
  { to: "/settings/gemini", label: "Configure Gemini AI" },
];

export default function SettingsLayout() {
  return (
    <div className="settings-layout">
      <aside>
        <h1 className="page-title">Settings</h1>
        <nav className="settings-nav" aria-label="Configuration">
          {PAGES.map((page) => (
            <NavLink key={page.to} to={page.to}>
              {page.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="settings-content">
        <Outlet />
      </div>
    </div>
  );
}
