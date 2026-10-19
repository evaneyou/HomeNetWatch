import { useMemo } from "react";
import { isAnonymized } from "../anonymized";
import type { AsChip, PathTrace } from "./NetworkPathView";

function sameNetwork(a: AsChip, b: AsChip): boolean {
  if (isAnonymized(a.asn) || isAnonymized(b.asn)) return Boolean(a.anon_key) && a.anon_key === b.anon_key;
  return a.asn != null && a.asn === b.asn;
}

function isOrigin(item: AsChip, trace: PathTrace): boolean {
  if (isAnonymized(item.asn)) return Boolean(item.anon_key) && item.anon_key === trace.bgp.anon_key;
  return trace.bgp.origin_asn != null && item.asn === trace.bgp.origin_asn;
}

/** The part of an AS name people recognise: "COMCAST-7922 - Comcast Cable Communications, LLC" becomes the second half. */
function niceName(name: string): string {
  const split = name.indexOf(" - ");
  return split > 0 && split < name.length - 3 ? name.slice(split + 3) : name;
}

type Row = {
  key: string;
  item: AsChip;
  /** Position along the traceroute, from 1, or null when it was not on it. */
  seen: number | null;
  /** Position along the announced BGP path, from 1, or null when it is not on it. */
  announced: number | null;
  origin: boolean;
};

/**
 * One list of every network on either path, in the order from you to the destination. A network
 * on both paths is one row. Networks that appear on one path only keep their place between the
 * matches either side of them.
 */
export function mergePaths(trace: PathTrace): Row[] {
  const observed = trace.as_path_observed;
  const announced = trace.bgp.as_path_announced;
  const rows: Row[] = [];
  let next = 0;
  observed.forEach((item, index) => {
    let partner = -1;
    for (let at = next; at < announced.length; at += 1) {
      if (sameNetwork(item, announced[at])) {
        partner = at;
        break;
      }
    }
    if (partner >= 0) {
      for (let at = next; at < partner; at += 1) {
        rows.push({ key: `a${at}`, item: announced[at], seen: null, announced: at + 1, origin: isOrigin(announced[at], trace) });
      }
      rows.push({ key: `m${index}`, item: announced[partner], seen: index + 1, announced: partner + 1, origin: isOrigin(announced[partner], trace) });
      next = partner + 1;
    } else {
      rows.push({ key: `o${index}`, item, seen: index + 1, announced: null, origin: false });
    }
  });
  for (let at = next; at < announced.length; at += 1) {
    rows.push({ key: `a${at}`, item: announced[at], seen: null, announced: at + 1, origin: isOrigin(announced[at], trace) });
  }
  return rows;
}

export type BgpVerdict = { tone: "ok" | "warn"; title: string; body: string };

/** What the two paths add up to, in a sentence. Null when there is not enough to say. */
export function bgpVerdict(trace: PathTrace): BgpVerdict | null {
  const observed = trace.as_path_observed;
  const originChip = trace.bgp.as_path_announced.find((item) => isOrigin(item, trace));
  const last = observed.length ? observed[observed.length - 1] : undefined;
  if (!originChip || !last) return null;
  const reached = sameNetwork(last, originChip);
  if (!reached) {
    return {
      tone: "warn",
      title: "Traffic ends in a different network than BGP expects",
      body: `BGP says ${originChip.label} owns this prefix, but the trace ends in ${last.label}. The trace may not have finished.`,
    };
  }
  if (trace.bgp.path_match) {
    return {
      tone: "ok",
      title: "Traffic reaches the right network",
      body: "The two paths match. An exchange point does not appear in BGP, which is expected.",
    };
  }
  return {
    tone: "ok",
    title: "Traffic reaches the right network",
    body: `Your packets end in ${originChip.label}, the network BGP says owns this prefix. The networks in between differ, which is common: BGP shows the path as seen from elsewhere on the internet.`,
  };
}

function note(row: Row): string {
  if (row.origin) return "Origin network";
  if (row.seen != null && row.announced != null) return "On both paths";
  if (row.seen != null) return row.item.type === "ixp" ? "Exchange point, not part of BGP" : "Not in the BGP path";
  return "Not seen in your trace";
}

function Step({ n, what }: { n: number | null; what: string }) {
  return n == null ? (
    <span className="bx-step none" role="img" aria-label={`Not in ${what}`}>
      <i />
    </span>
  ) : (
    <span className="bx-step" role="img" aria-label={`Step ${n} in ${what}`}>
      {n}
    </span>
  );
}

/**
 * The traceroute's networks and the announced BGP path as one checklist, read from you at the top to
 * the destination at the bottom. The numbers are each network's place along that path.
 */
export default function BgpPath({ trace }: { trace: PathTrace }) {
  const rows = useMemo(() => mergePaths(trace), [trace]);
  if (rows.length === 0) return null;
  return (
    <div className="bx">
      <div className="bx-head" aria-hidden="true">
        <span />
        <span>Networks, from you to the destination</span>
        <span>Your trace</span>
        <span>BGP path</span>
      </div>
      <ol className="bx-list">
        <li className="bx-cap">
          <span className="bx-gut">
            <i className="bx-end" />
          </span>
          <span className="bx-cap-text">Start: {trace.agent.name}</span>
        </li>
        {rows.map((row) => (
          <li className="bx-row" key={row.key} style={{ ["--c" as string]: `var(--role-${row.item.role})` }}>
            <span className="bx-gut">
              <i className="bx-node" />
            </span>
            <span className="bx-who" title={`${row.item.label} ${row.item.name}`}>
              <span className="bx-l1">
                <span className="bx-as">{row.item.label}</span>
                <b>{niceName(row.item.name)}</b>
              </span>
              <span className={`bx-note${row.origin ? " origin" : ""}`}>{note(row)}</span>
            </span>
            <Step n={row.seen} what="your trace" />
            <Step n={row.announced} what="the BGP path" />
          </li>
        ))}
        <li className="bx-cap last">
          <span className="bx-gut">
            <i className="bx-end" />
          </span>
          <span className="bx-cap-text">Destination: {trace.target.name}</span>
        </li>
      </ol>
      <p className="bx-foot">Numbers show the order along each path. A dash means the network is not on that path.</p>
    </div>
  );
}
