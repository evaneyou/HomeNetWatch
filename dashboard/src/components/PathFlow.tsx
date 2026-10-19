import { Fragment, useMemo } from "react";
import { useElementWidth } from "../useNarrow";
import type { PathGroup, PathHop, PathTrace } from "./NetworkPathView";

/** A step this big between two networks is worth flagging. */
const BIG_STEP_MS = 15;
/** Room each network and each handoff needs on the wide line; less than this and the path runs top to bottom. */
const NET_MIN = 124;
const HOP_MIN = 46;
const STEP_W = 62;
/** Height of the bars on the wide line, in pixels. */
const BAR_MAX = 46;

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function networkLabel(label: string): string {
  return label === "AS ?" ? "Unknown network" : label;
}

function step(delta: number | null): string {
  return delta == null ? "?" : `${delta >= 0 ? "+" : ""}${fmt(delta)} ms`;
}

type Net = { group: PathGroup; index: number; hops: PathHop[]; big: boolean };

type Props = {
  nets: Net[];
  warn: number[];
  axisMax: number;
  selected: number | null;
  chosen: number | null;
  onSelect: (ttl: number) => void;
  onGroup: (index: number) => void;
};

function hopLabel(hop: PathHop): string {
  return `Hop ${hop.ttl}, ${hop.name}, ${hop.silent ? "no reply" : `${fmt(hop.avg_ms)} milliseconds`}`;
}

function StepPill({ net }: { net: Net }) {
  return <span className={`pm-pill${net.big ? " is-big" : ""}`}>{step(net.group.delta_in_ms)}</span>;
}

/** Wide: one line across the page. Networks are coloured stretches of it, hops rise from it as bars. */
function Line({ nets, warn, axisMax, selected, chosen, onSelect, onGroup }: Props) {
  return (
    <div className="pm-line">
      {nets.map((net, position) => (
        <Fragment key={net.index}>
          {position > 0 ? (
            <div className={`pm-step${net.big ? " is-big" : ""}`} aria-hidden="true">
              <span className="pm-base" />
              <StepPill net={net} />
            </div>
          ) : null}
          <div
            className={`pm-net${chosen === net.index ? " is-chosen" : ""}`}
            style={{ ["--c" as string]: `var(--role-${net.group.role})`, flex: `${net.hops.length} 1 0`, minWidth: Math.max(NET_MIN, net.hops.length * HOP_MIN) }}
          >
            <button
              type="button"
              className="pm-head"
              aria-pressed={chosen === net.index}
              aria-label={`${net.group.name}, ${net.group.role_label}. Show details`}
              onClick={() => onGroup(net.index)}
            >
              <span className="pm-asn">{networkLabel(net.group.asn_label)}</span>
              <b>{net.group.name}</b>
            </button>
            <div className="pm-hops">
              {net.hops.map((hop) => {
                const none = hop.silent || hop.avg_ms == null;
                return (
                  <button
                    key={hop.ttl}
                    type="button"
                    className={`pm-hop${none ? " silent" : ""}${warn.includes(hop.ttl) ? " warn" : ""}${selected === hop.ttl ? " is-selected" : ""}`}
                    style={{ ["--c" as string]: `var(--role-${hop.role})` }}
                    aria-pressed={selected === hop.ttl}
                    aria-label={hopLabel(hop)}
                    onClick={() => onSelect(hop.ttl)}
                  >
                    <span className="pm-bars" aria-hidden="true">
                      <i className={`pm-bar${none ? " none" : ""}`} style={none ? undefined : { height: Math.max(4, Math.round(((hop.avg_ms as number) / axisMax) * BAR_MAX)) }} />
                    </span>
                    <span className="pm-base" aria-hidden="true" />
                    <span className="n">#{hop.ttl}</span>
                    <span className="v">{none ? "* * *" : fmt(hop.avg_ms)}</span>
                    <span className="u">{none ? "no reply" : "ms"}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </Fragment>
      ))}
    </div>
  );
}

/** Narrow: a rail running down the page. Plain HTML, so a thumb on it scrolls the page. */
function Rail({ nets, warn, axisMax, selected, chosen, onSelect, onGroup }: Props) {
  return (
    <div className="pm-rail">
      {nets.map((net, position) => (
        <div className={`pm-rnet${chosen === net.index ? " is-chosen" : ""}`} key={net.index} style={{ ["--c" as string]: `var(--role-${net.group.role})` }}>
          <button
            type="button"
            className="pm-rhead"
            aria-pressed={chosen === net.index}
            aria-label={`${net.group.name}, ${net.group.role_label}. Show details`}
            onClick={() => onGroup(net.index)}
          >
            <span className="pm-rtext">
              <span className="pm-asn">{networkLabel(net.group.asn_label)}</span>
              <b>{net.group.name}</b>
            </span>
            {position > 0 ? <StepPill net={net} /> : null}
          </button>
          {net.hops.map((hop) => {
            const none = hop.silent || hop.avg_ms == null;
            return (
              <button
                key={hop.ttl}
                type="button"
                className={`pm-row${none ? " silent" : ""}${warn.includes(hop.ttl) ? " warn" : ""}${selected === hop.ttl ? " is-selected" : ""}`}
                style={{ ["--c" as string]: `var(--role-${hop.role})` }}
                aria-pressed={selected === hop.ttl}
                aria-label={hopLabel(hop)}
                onClick={() => onSelect(hop.ttl)}
              >
                <span className="n">#{hop.ttl}</span>
                <span className="pm-track" aria-hidden="true">
                  {none ? null : <i style={{ width: `${Math.max(3, Math.min(100, ((hop.avg_ms as number) / axisMax) * 100))}%` }} />}
                </span>
                <span className="v">
                  {none ? (
                    "no reply"
                  ) : (
                    <>
                      {fmt(hop.avg_ms)} <small>ms</small>
                    </>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/**
 * The network path: one line left to right on a wide screen, a rail top to bottom when it will not fit.
 * Each network is a coloured stretch with its hops on it, and the latency each handoff adds sits between them.
 */
export default function PathFlow({
  trace,
  selected,
  onSelect,
  group,
  onGroup,
}: {
  trace: PathTrace;
  selected: number | null;
  onSelect: (ttl: number) => void;
  group: number | null;
  onGroup: (index: number) => void;
}) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const hopsByTtl = useMemo(() => new Map(trace.hops.map((hop) => [hop.ttl, hop])), [trace.hops]);
  const nets = useMemo<Net[]>(
    () =>
      trace.groups.map((item, index) => ({
        group: item,
        index,
        hops: item.hop_ttls.map((ttl) => hopsByTtl.get(ttl)).filter((hop): hop is PathHop => Boolean(hop)),
        big: index > 0 && item.delta_in_ms != null && item.delta_in_ms >= BIG_STEP_MS,
      })),
    [trace.groups, hopsByTtl],
  );
  const slowest = Math.max(0, ...trace.hops.map((hop) => (hop.silent ? 0 : (hop.avg_ms ?? 0))));
  const axisMax = Math.max(15, Math.ceil(slowest / 15) * 15);
  const needed = nets.reduce((sum, net) => sum + Math.max(NET_MIN, net.hops.length * HOP_MIN), 0) + Math.max(0, nets.length - 1) * STEP_W;
  const wide = width > 0 && width >= needed;
  const shared: Props = { nets, warn: trace.warn_ttls, axisMax, selected, chosen: group, onSelect, onGroup };
  return (
    <div ref={ref} className={`pm${wide ? " is-wide" : " is-rail"}`}>
      {width > 0 ? wide ? <Line {...shared} /> : <Rail {...shared} /> : null}
    </div>
  );
}
