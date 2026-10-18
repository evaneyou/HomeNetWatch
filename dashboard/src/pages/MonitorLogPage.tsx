import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import SelectMenu from "../components/SelectMenu";
import StatusMark from "../components/StatusMark";
import { checkLabel, networkLabel } from "../labels";
import type { MonitorDetail, Result, ResultPage } from "../types";

const PAGE = 100;
const INTERNET_SLUGS = ["http-path", "icmp-path", "mtr-path"];

type ProbeOption = { value: string; label: string };
type ProbeGroup = { label: string; options: ProbeOption[] };

function metric(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(1);
}

function probeGroups(tests: MonitorDetail["tests"], probes: { slug: string; name: string }[]): ProbeGroup[] {
  const internet = INTERNET_SLUGS.flatMap((slug) => {
    const test = tests.find((item) => item.slug === slug);
    return test ? [{ value: `slug:${slug}`, label: checkLabel(test) }] : [];
  });
  const web = new Map<string, string>();
  const other: ProbeOption[] = [];
  for (const test of tests) {
    if (INTERNET_SLUGS.includes(test.slug)) continue;
    const match = /^(http|icmp|mtr)-([a-z0-9-]+)$/.exec(test.slug);
    if (match) {
      const label = checkLabel(test).replace(/ (HTTP|ICMP|MTR)$/, "");
      if (!web.has(match[2])) web.set(match[2], label);
      continue;
    }
    other.push({ value: `slug:${test.slug}`, label: checkLabel(test) });
  }
  const groups: ProbeGroup[] = [];
  if (internet.length > 0) {
    groups.push({
      label: "Internet Service",
      options: [{ value: "internet", label: "All Internet Service checks" }, ...internet],
    });
  }
  const webOptions = [...web.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([key, label]) => ({ value: `svc:${key}`, label }));
  if (webOptions.length > 0) {
    groups.push({
      label: "Web Services",
      options: [{ value: "web", label: "All Web Services" }, ...webOptions],
    });
  }
  if (other.length > 0) {
    groups.push({
      label: "Other probes",
      options: other.sort((a, b) => a.label.localeCompare(b.label)),
    });
  }
  if (probes.length > 0) {
    groups.push({
      label: "Real-Time Application Health",
      options: [
        { value: "rtnp", label: "All Real-Time Application Health" },
        ...probes.map((probe) => ({ value: `rtnp:${probe.slug}`, label: probe.name })),
      ],
    });
  }
  groups.push({
    label: "Airwaves",
    options: [{ value: "airwave", label: "All Airwave notifications & alerts" }],
  });
  groups.push({
    label: "System & Agent",
    options: [{ value: "system", label: "All System & Agent logs" }],
  });
  return groups;
}

export default function MonitorLogPage() {
  const { id = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const offset = Math.max(0, Number(searchParams.get("offset") ?? "0") || 0);
  const probe = searchParams.get("probe") || "all";
  const monitorQ = useQuery({
    queryKey: ["monitor", id],
    queryFn: () => api.monitor(id) as Promise<MonitorDetail>,
    enabled: !!id,
  });
  const logQ = useQuery({
    queryKey: ["monitor-log", id, offset, probe],
    queryFn: () => api.monitorLog(id, offset, PAGE, probe) as Promise<ResultPage>,
    enabled: !!id,
  });

  if (monitorQ.isLoading || logQ.isLoading) return <div className="state-box">Loading log…</div>;
  if (monitorQ.isError || !monitorQ.data) return <div className="state-box">Remote agent not found.</div>;
  if (logQ.isError || !logQ.data) return <div className="state-box">Could not load the log.</div>;

  const monitor = monitorQ.data;
  const page = logQ.data;
  const groups = probeGroups(monitor.tests, monitor.probes ?? []);
  const from = page.total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + page.items.length, page.total);

  function go(next: number) {
    const params = new URLSearchParams(searchParams);
    if (next <= 0) params.delete("offset");
    else params.set("offset", String(next));
    setSearchParams(params);
  }

  function setProbe(next: string) {
    const params = new URLSearchParams(searchParams);
    params.delete("offset");
    if (!next || next === "all") params.delete("probe");
    else params.set("probe", next);
    setSearchParams(params);
  }

  return (
    <div>
      <h1 className="page-title">Log of recent results</h1>
      <p className="page-sub">
        <Link to={`/monitors/${monitor.id}`}>{monitor.name}</Link>
        {" · "}
        every result stored for this remote agent
      </p>
      <div className="panel">
        <div className="log-filter">
          <label htmlFor="log-probe">Show</label>
          <SelectMenu id="log-probe" value={probe} onChange={(event) => setProbe(event.target.value)}>
            <option value="all">All</option>
            {groups.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </SelectMenu>
        </div>
        <div className="log-pager">
          <span className="muted">
            {from}–{to} of {page.total.toLocaleString()}
          </span>
          <div className="log-pager-actions">
            <button className="btn" type="button" disabled={offset <= 0} onClick={() => go(offset - PAGE)}>
              Newer
            </button>
            <button
              className="btn"
              type="button"
              disabled={offset + PAGE >= page.total}
              onClick={() => go(offset + PAGE)}
            >
              Older
            </button>
          </div>
        </div>
        {page.items.length === 0 ? (
          <div className="muted">No results for this filter.</div>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Service</th>
                  <th>Network</th>
                  <th>Status</th>
                  <th>RTT</th>
                  <th>Loss</th>
                  <th>Down</th>
                  <th>Up</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <LogRow key={row.id} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function LogRow({ row }: { row: Result }) {
  const metrics = row.metrics ?? {};
  const href =
    row.test_kind === "airwave"
      ? `/airwaves?agent=${row.monitor_id}`
      : row.test_kind === "rtnp"
        ? `/monitors/${row.monitor_id}?detail=isp&link=${row.network_slug === "wifi" ? "wireless" : "wired"}&check=rtnp:${row.test_slug}`
        : `/tests/${row.test_id}`;
  const isNotice = row.status === "info";
  return (
    <tr className={isNotice ? "muted" : undefined}>
      <td className="mono">{new Date(row.started_at).toLocaleString()}</td>
      <td>
        <Link to={href} className={isNotice ? "muted" : undefined}>
          {checkLabel({ slug: row.test_slug, name: row.test_name })}
        </Link>
        {row.test_kind === "airwave" && row.message ? (
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            {row.message}
          </div>
        ) : null}
      </td>
      <td>{networkLabel({ slug: row.network_slug, name: row.network_name })}</td>
      <td>
        <StatusMark status={row.status} detail={row.status_reason} />
      </td>
      <td className="mono">{metrics.rtt_ms == null ? "—" : `${metric(metrics.rtt_ms)} ms`}</td>
      <td className="mono">{metrics.loss_pct == null ? "—" : `${metric(metrics.loss_pct)}%`}</td>
      <td className="mono">{metrics.down_mbps == null ? "—" : `${metric(metrics.down_mbps)} Mbps`}</td>
      <td className="mono">{metrics.up_mbps == null ? "—" : `${metric(metrics.up_mbps)} Mbps`}</td>
    </tr>
  );
}
