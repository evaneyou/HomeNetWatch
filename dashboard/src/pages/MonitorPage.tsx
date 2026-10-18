import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import StatusMark from "../components/StatusMark";
import DevicePanel from "../components/DevicePanel";
import InternetPath, {
  logProbeForDetail,
  RangeTabs,
  StripRangeTabs,
  type ChartRange,
  type StripRange,
} from "../components/InternetPath";
import MonitorStatusDialog from "../components/MonitorStatusDialog";
import MonitorTabs, { monitorNetworks } from "../components/MonitorTabs";
import RealtimeHealth from "../components/RealtimeHealth";
import WifiScans from "../components/WifiScans";
import type { MonitorDetail } from "../types";

const MONITOR_ZONE = "America/New_York";

function formatLastSeen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    timeZone: MONITOR_ZONE,
    timeZoneName: "short",
  });
}

/** Time alone when the probe was seen today, otherwise a short date and time. */
function shortLastSeen(iso: string): string {
  const seen = new Date(iso);
  const time = seen.toLocaleTimeString(undefined, {
    timeZone: MONITOR_ZONE,
    hour: "numeric",
    minute: "2-digit",
  });
  const dayOf = (date: Date) => date.toLocaleDateString(undefined, { timeZone: MONITOR_ZONE });
  if (dayOf(seen) === dayOf(new Date())) return time;
  const day = seen.toLocaleDateString(undefined, {
    timeZone: MONITOR_ZONE,
    month: "short",
    day: "numeric",
  });
  return `${day}, ${time}`;
}

export default function MonitorPage() {
  const { id = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [statusOpen, setStatusOpen] = useState(false);
  const [stripRange, setStripRange] = useState<StripRange>("1h");
  const [chartRange, setChartRange] = useState<ChartRange>("1h");
  const monitorQ = useQuery({
    queryKey: ["monitor", id],
    queryFn: () => api.monitor(id) as Promise<MonitorDetail>,
    enabled: !!id,
  });

  const networks = useMemo(() => monitorNetworks(monitorQ.data?.tests), [monitorQ.data]);

  if (searchParams.get("view") === "path") {
    const next = new URLSearchParams();
    next.set("agent", id);
    next.set("link", searchParams.get("link") === "wireless" ? "wireless" : "wired");
    return <Navigate to={`/path?${next.toString()}`} replace />;
  }

  const requested = searchParams.get("network");
  const link = searchParams.get("link");
  const detailParam = searchParams.get("detail");
  const checkParam = searchParams.get("check");
  const onApps = detailParam === "apps";
  const openLink = (link === "wired" || link === "wireless") && Boolean(detailParam) && !onApps;
  const wanId = networks.find((network) => network.slug === "wan")?.id ?? null;
  const testsSoFar = monitorQ.data?.tests ?? [];
  const wiredId = testsSoFar.find((test) => test.network_slug === "wired")?.network_id ?? null;
  const wirelessId = testsSoFar.find((test) => test.network_slug === "wifi")?.network_id ?? null;
  const onConnection = detailParam === "connection";
  const onWired = link === "wired" && !onConnection && !onApps;
  const onWireless = link === "wireless" && !onConnection && !onApps;
  const activeTab =
    (openLink || onApps) && wanId
      ? wanId
      : requested && networks.some((n) => n.id === requested)
        ? requested
        : (networks[0]?.id ?? null);

  if (monitorQ.isLoading) return <div className="state-box">Loading monitor…</div>;
  if (monitorQ.isError || !monitorQ.data) {
    return <div className="state-box">Remote agent not found.</div>;
  }

  const monitor = monitorQ.data;
  const activeNetwork = networks.find((n) => n.id === activeTab);
  const tests =
    activeNetwork?.slug === "wan"
      ? monitor.tests.filter((test) => ["wan", "wired", "wifi"].includes(test.network_slug))
      : monitor.tests.filter((test) => !activeTab || test.network_id === activeTab);

  return (
    <div>
      <div className="monitor-head">
        <h1 className="page-title">
          <ProbeIcon />
          <span>{monitor.name}</span>
        </h1>
        <button
          className="monitor-status-box"
          type="button"
          onClick={() => setStatusOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={statusOpen}
          aria-label={
            monitor.last_seen_at
              ? `Remote Probe Status. Last seen ${formatLastSeen(monitor.last_seen_at)}`
              : "Remote Probe Status. Not seen yet"
          }
        >
          <StatusMark status={monitor.status} focusable={false} />
          <span className="monitor-status-label">Remote Probe Status</span>
          <span className="monitor-status-seen">
            {monitor.last_seen_at
              ? `Last seen ${shortLastSeen(monitor.last_seen_at)}`
              : "Not seen yet"}
          </span>
        </button>
      </div>
      <div className="page-tabs">
        <MonitorTabs
          tests={monitor.tests}
          activeTab={activeTab}
          onWired={onWired}
          onWireless={onWireless}
          onApps={onApps}
          onConnection={onConnection}
          link={link}
          onSelect={setSearchParams}
        />
          {activeNetwork?.slug === "wan" && !detailParam && !link && wiredId && wirelessId ? (
            <StripRangeTabs range={stripRange} onChange={setStripRange} />
          ) : onApps || onConnection || ((onWired || onWireless) && (checkParam || detailParam)) ? (
            <RangeTabs range={chartRange} onChange={setChartRange} />
          ) : null}
        </div>
      {statusOpen ? <MonitorStatusDialog monitor={monitor} onClose={() => setStatusOpen(false)} /> : null}

      {activeNetwork?.slug === "wan" || statusOpen ? null : <DevicePanel monitorId={monitor.id} />}

      {activeNetwork?.slug === "wifi-main" ? <WifiScans monitorId={monitor.id} /> : null}
      {onApps ? (
        <AppsDashboard
          monitorId={monitor.id}
          range={chartRange}
          link={link === "wired" ? "wired" : link === "wireless" ? "wireless" : null}
          slug={checkParam?.startsWith("rtnp:") ? checkParam.slice(5) : null}
          onOpen={(selectedLink, slug) => {
            const next = new URLSearchParams(searchParams);
            next.set("detail", "apps");
            next.set("link", selectedLink);
            next.set("check", `rtnp:${slug}`);
            setSearchParams(next);
          }}
        />
      ) : activeNetwork?.slug === "wan" ? (
        <InternetPath
          tests={tests}
          isp={monitor.isp}
          monitorId={monitor.id}
          stripRange={stripRange}
          chartRange={chartRange}
          onChartRangeChange={setChartRange}
        />
      ) : null}

      <div className="panel">
        <Link
          className="btn log-link"
          to={logHref(monitor.id, activeNetwork?.slug, searchParams.get("detail"), searchParams.get("check"))}
        >
          Log of recent results
        </Link>
      </div>
    </div>
  );
}

function AppsDashboard({
  monitorId,
  range,
  link,
  slug,
  onOpen,
}: {
  monitorId: string;
  range: ChartRange;
  link: "wired" | "wireless" | null;
  slug: string | null;
  onOpen: (link: "wired" | "wireless", slug: string) => void;
}) {
  if (slug && link) {
    return <RealtimeHealth monitorId={monitorId} link={link} range={range} slug={slug} />;
  }
  return (
    <>
      <section className="panel network-health">
        <RealtimeHealth
          monitorId={monitorId}
          link="wireless"
          range={range}
          title="Wi-Fi · Real-Time Application Health"
          onOpen={(s) => onOpen("wireless", s)}
        />
      </section>
      <section className="panel network-health">
        <RealtimeHealth
          monitorId={monitorId}
          link="wired"
          range={range}
          title="Wired · Real-Time Application Health"
          onOpen={(s) => onOpen("wired", s)}
        />
      </section>
    </>
  );
}

function logHref(
  monitorId: string,
  networkSlug: string | undefined,
  detail: string | null,
  check: string | null,
): string {
  const probe = networkSlug === "wan" ? logProbeForDetail(detail, check) : null;
  if (!probe) return `/monitors/${monitorId}/log`;
  return `/monitors/${monitorId}/log?probe=${encodeURIComponent(probe)}`;
}

function ProbeIcon() {
  return (
    <svg className="probe-icon" viewBox="0 0 24 24" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
        <path d="M5.4 4.7a12.2 12.2 0 0 1 13.2 0" />
        <path d="M7.7 7.2a8.4 8.4 0 0 1 8.6 0" />
        <path d="M10 9.6a4.4 4.4 0 0 1 4 0" />
      </g>
      <path fill="currentColor" d="M11.15 10.15h1.7V13h-1.7z" />
      <path
        fill="currentColor"
        d="M3.1 13.15h17.8c.9 0 1.6.7 1.6 1.6v4.5c0 .9-.7 1.6-1.6 1.6H3.1c-.9 0-1.6-.7-1.6-1.6v-4.5c0-.9.7-1.6 1.6-1.6z"
      />
      <circle cx="6.15" cy="17.2" r="1.05" className="probe-icon-hole" />
      <circle cx="9.15" cy="17.2" r="1.05" className="probe-icon-hole" />
    </svg>
  );
}
