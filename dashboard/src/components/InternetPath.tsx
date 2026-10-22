import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { MonitorDetail } from "../types";
import ConnectionDiagramLink from "./ConnectionDiagramLink";
import { HealthBar, InternetDetail, RangeTabs, TimeAxis } from "./InternetDetail";
import {
  APPS,
  RANGES,
  type ChartRange,
  type DetailKey,
  type StripRange,
  STRIP_RANGES,
  type TestRow,
  fmt,
  isDetail,
  isSaasProbe,
  liveOf,
  logProbeForDetail,
  speed,
  statusStrip,
  stripGradient,
  useAppStrips,
  useSeries,
} from "./pathSeries";
import StatusMark from "./StatusMark";

export type { ChartRange, StripRange };
export { isSaasProbe, logProbeForDetail, RangeTabs, TimeAxis };

export default function InternetPath({
  tests,
  isp,
  monitorId,
  stripRange = "1h",
  chartRange,
  onChartRangeChange,
}: {
  tests: MonitorDetail["tests"];
  isp: string | null;
  monitorId?: string;
  stripRange?: StripRange;
  chartRange?: ChartRange;
  onChartRangeChange?: (range: ChartRange) => void;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("detail");
  const detail = isDetail(requested) ? requested : null;
  const link = searchParams.get("link");
  const wired = tests.filter((test) => test.network_slug === "wired");
  const wireless = tests.filter((test) => test.network_slug === "wifi");
  const wan = tests.filter((test) => test.network_slug === "wan");

  function openDetail(key: DetailKey, nextLink?: string) {
    const next = new URLSearchParams(searchParams);
    next.set("detail", key);
    next.delete("check");
    next.delete("view");
    if (nextLink === "wireless") {
      const wifiId = wireless[0]?.network_id;
      if (wifiId) next.set("network", wifiId);
      next.set("link", "wireless");
    } else if (nextLink === "wired") {
      const wiredId = wired[0]?.network_id;
      if (wiredId) next.set("network", wiredId);
      next.set("link", "wired");
    } else if (nextLink) {
      next.set("link", nextLink);
    } else {
      next.delete("link");
    }
    setSearchParams(next);
  }

  function openCharts(linkName: "wired" | "wireless") {
    const next = new URLSearchParams(searchParams);
    next.set("detail", "isp");
    next.set("link", linkName);
    next.set("check", "librespeed");
    next.delete("view");
    const id = (linkName === "wireless" ? wireless : wired)[0]?.network_id;
    if (id) next.set("network", id);
    setSearchParams(next);
  }

  if (requested === "connection") {
    return (
      <InternetConnection
        wired={wired}
        wireless={wireless}
        onOpen={openCharts}
        range={chartRange}
        onRangeChange={onChartRangeChange}
        monitorId={monitorId}
      />
    );
  }

  if (detail) {
    const networkId = searchParams.get("network");
    const selectedSlug = tests.find((test) => test.network_id === networkId)?.network_slug ?? "";
    const resolved =
      link === "wireless"
        ? "wireless"
        : link === "wired"
          ? "wired"
          : selectedSlug === "wifi"
            ? "wireless"
            : selectedSlug === "wired" || wired.length > 0
              ? "wired"
              : "";
    // The old Internet-network copies stop updating once a wired or wireless link is reporting.
    const pool = resolved === "wireless" ? wireless : resolved === "wired" ? wired : wan;
    return (
      <>
        <InternetDetail
          tests={pool.length > 0 ? pool : tests}
          detail={detail}
          isp={isp}
          monitorId={monitorId}
          linkLabel={resolved === "wired" ? "Wired" : resolved === "wireless" ? "Wi-Fi" : null}
          range={chartRange}
          onRangeChange={onChartRangeChange}
        />
      </>
    );
  }
  if (wired.length > 0 && wireless.length > 0) {
    return (
      <ServiceCompare
        wired={wired}
        wireless={wireless}
        stripRange={stripRange}
        onOpen={(key, linkName) => openDetail(key, linkName)}
      />
    );
  }
  return (
    <InternetDashboard tests={wan.length > 0 ? wan : tests} onOpen={openDetail} />
  );
}

export function StripRangeTabs({ range, onChange }: { range: StripRange; onChange: (range: StripRange) => void }) {
  return (
    <div className="range-row">
      {STRIP_RANGES.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`tab ${range === item.id ? "active" : ""}`}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function ServiceSide({
  label,
  status,
  detail,
  strip,
  stripLabel,
  onOpen,
}: {
  label: string;
  status: string;
  detail: string | null;
  strip: string[];
  stripLabel: string;
  onOpen: () => void;
}) {
  return (
    <button className="saas-tile" type="button" onClick={onOpen}>
      <span className="saas-tile-head">
        <strong>{label}</strong>
        <StatusMark status={status} detail={detail} focusable={false} />
      </span>
      <span className="isp-strip" title={stripLabel} style={{ background: stripGradient(strip) }} />
    </button>
  );
}

function ServiceCompare({
  wired,
  wireless,
  stripRange,
  onOpen,
}: {
  wired: TestRow[];
  wireless: TestRow[];
  stripRange: StripRange;
  onOpen: (key: DetailKey, link: "wired" | "wireless") => void;
}) {
  const wiredView = useAppStrips(wired, stripRange);
  const wirelessView = useAppStrips(wireless, stripRange);

  const wiredBad = APPS.filter((app) => {
    const s = wiredView.statusFor(app.key);
    return s === "fail" || s === "degraded";
  }).length;
  const wifiBad = APPS.filter((app) => {
    const s = wirelessView.statusFor(app.key);
    return s === "fail" || s === "degraded";
  }).length;

  let diagnosisBanner: { title: string; summary: string } | null = null;
  if (wifiBad >= 2 && wiredBad === 0) {
    diagnosisBanner = {
      title: "Wi-Fi link degradation",
      summary: "Multiple Web services are experiencing slowdowns or failures on Wi-Fi while passing on Wired Ethernet. This indicates a local wireless network issue rather than a Web service outage.",
    };
  } else if (wiredBad >= 2 && wifiBad === 0) {
    diagnosisBanner = {
      title: "Wired Ethernet link degradation",
      summary: "Multiple Web services are experiencing slowdowns or failures on Wired Ethernet while passing on Wi-Fi. This indicates a local wired connection issue rather than a Web service outage.",
    };
  } else if (wiredBad >= 4 && wifiBad >= 4) {
    diagnosisBanner = {
      title: "Upstream ISP, DNS, or remote agent issue",
      summary: "Multiple Web services are failing across both Wired and Wi-Fi simultaneously, pointing to an upstream ISP, router, or DNS issue rather than individual Web services.",
    };
  }

  return (
    <div className="service-compare">
      {diagnosisBanner ? (
        <div
          style={{
            padding: "10px 14px",
            marginBottom: 12,
            borderRadius: 8,
            border: "1px solid var(--degraded)",
            background: "color-mix(in srgb, var(--degraded) 10%, var(--card))",
            fontSize: "0.9rem",
          }}
        >
          <strong>Root cause · {diagnosisBanner.title}:</strong> {diagnosisBanner.summary}
        </div>
      ) : null}
      <div className="svc-table">
        <div className="svc-head" aria-hidden="true">
          <span>Service</span>
          <span>Wired</span>
          <span>Wi-Fi</span>
        </div>
        {APPS.map((app) => (
          <div className="svc-row" key={app.key}>
            <h2 className="svc-name">{app.label}</h2>
            <ServiceCell
              link="Wired"
              app={app.label}
              status={wiredView.statusFor(app.key)}
              detail={wiredView.detailFor(app.key)}
              strip={wiredView.appStrips.get(app.key) ?? []}
              stripLabel={wiredView.stripLabel}
              onOpen={() => onOpen(app.key, "wired")}
            />
            <ServiceCell
              link="Wi-Fi"
              app={app.label}
              status={wirelessView.statusFor(app.key)}
              detail={wirelessView.detailFor(app.key)}
              strip={wirelessView.appStrips.get(app.key) ?? []}
              stripLabel={wirelessView.stripLabel}
              onOpen={() => onOpen(app.key, "wireless")}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/** One link of one service: the column heading names it on wide screens, the
    inline label does below 700px where the columns stack. */
function ServiceCell({
  link,
  app,
  status,
  detail,
  strip,
  stripLabel,
  onOpen,
}: {
  link: string;
  app: string;
  status: string;
  detail: string | null;
  strip: string[];
  stripLabel: string;
  onOpen: () => void;
}) {
  return (
    <button className="svc-cell" type="button" onClick={onOpen} aria-label={`${app} over ${link}`}>
      <span className="svc-link">{link}</span>
      <StatusMark status={status} detail={detail} focusable={false} />
      <span className="isp-strip" title={stripLabel} style={{ background: stripGradient(strip) }} />
    </button>
  );
}

function InternetDashboard({
  tests,
  onOpen,
}: {
  tests: TestRow[];
  onOpen: (key: DetailKey) => void;
}) {
  const [stripRange, setStripRange] = useState<StripRange>("1h");
  const { appStrips, statusFor, detailFor, stripLabel } = useAppStrips(tests, stripRange);

  return (
    <>
      <StripRangeTabs range={stripRange} onChange={setStripRange} />
      <div className="panel">
      <h3 className="wifi-heading">Web Services</h3>
      <div className="saas-grid">
        {APPS.map((app) => (
          <ServiceSide
            key={app.key}
            label={app.label}
            status={statusFor(app.key)}
            detail={detailFor(app.key)}
            strip={appStrips.get(app.key) ?? []}
            stripLabel={stripLabel}
            onOpen={() => onOpen(app.key)}
          />
        ))}
      </div>
    </div>
    </>
  );
}

function InternetConnection({
  wired,
  wireless,
  onOpen,
  range: rangeProp,
  onRangeChange,
  monitorId,
}: {
  wired: TestRow[];
  wireless: TestRow[];
  onOpen: (link: "wired" | "wireless") => void;
  range?: ChartRange;
  onRangeChange?: (range: ChartRange) => void;
  monitorId?: string;
}) {
  const [localRange, setLocalRange] = useState<ChartRange>("1h");
  const range = rangeProp ?? localRange;
  const setRange = onRangeChange ?? setLocalRange;
  const wiredSpeed = useSeries(
    wired.filter((test) => test.slug === "librespeed"),
    range,
  );
  const wirelessSpeed = useSeries(
    wireless.filter((test) => test.slug === "librespeed"),
    range,
  );
  return (
    <section className="panel network-health">
      <h2 style={{ marginTop: 0, fontSize: "1rem" }}>Internet Connection</h2>
      <ConnectionDiagramLink
        monitorId={monitorId}
        wired={liveOf(wiredSpeed.points.get("librespeed") ?? [])}
        wireless={liveOf(wirelessSpeed.points.get("librespeed") ?? [])}
      />
      {onRangeChange ? null : <RangeTabs range={range} onChange={setRange} />}
      <div className="health-stack">
        <SpeedSummary label="Wired Internet Connection" query={wiredSpeed} range={range} onOpen={() => onOpen("wired")} />
        <SpeedSummary label="Wireless Internet Connection" query={wirelessSpeed} range={range} onOpen={() => onOpen("wireless")} />
      </div>
    </section>
  );
}

function SpeedSummary({
  label,
  query,
  range,
  onOpen,
}: {
  label: string;
  query: ReturnType<typeof useSeries>;
  range: (typeof RANGES)[number];
  onOpen: () => void;
}) {
  const series = query.points.get("librespeed") ?? [];
  const latest = series.at(-1);
  const strip = statusStrip(series, range);
  return (
    <div className="connection-summary">
      {query.loading ? (
        <div className="muted">Loading internet checks…</div>
      ) : query.failed ? (
        <div className="muted">Could not load internet checks.</div>
      ) : (
        <HealthBar
          title={label}
          detail={`${fmt(speed(latest).down)} Mbps down · ${fmt(speed(latest).up)} Mbps up · ping ${fmt(latest?.rtt_ms)} ms · jitter ${fmt(latest?.jitter_ms)} ms`}
          strip={strip}
          empty={series.length === 0}
          onOpen={onOpen}
        />
      )}
    </div>
  );
}
