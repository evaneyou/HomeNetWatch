import { Background, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ReactElement } from "react";
import { isAnonymized, shownAnon } from "../anonymized";

export type LinkStatus = "ok" | "degraded" | "fail" | "unknown";

/** The newest Bandwidth Test figures for one link. */
export type LinkLive = { status: LinkStatus; down: number | null; up: number | null; ping: number | null };

export type MiniLive = {
  name?: string;
  online?: boolean;
  isp?: string | null;
  wired: LinkLive;
  wireless: LinkLive;
  /** The state of each WireGuard tunnel, as the server reports it. */
  tunnels?: { wired?: string; wireless?: string };
};

type Icon = "house" | "ethernet" | "wifi" | "globe" | "server";
type Tone = "wired" | "wifi" | "neutral";
type MiniData = { title: string; lines: string[]; tone: Tone; icon: Icon; status?: LinkStatus };
type MiniNode = Node<MiniData, "mini">;

const STATUS_LABEL: Record<LinkStatus, string> = {
  ok: "Passing",
  degraded: "Degraded",
  fail: "Failing",
  unknown: "No recent result",
};

const TUNNEL_STATE: Record<string, string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  never_connected: "Not connected yet",
};

const COLOR = { wired: "#4fd1b5", wifi: "#ffc24d", neutral: "#6d7895", fail: "#ff6f91" } as const;

/** Small line icons, drawn with the box's own text colour. */
function Glyph({ name }: { name: Icon }) {
  const paths: Record<Icon, ReactElement> = {
    house: (
      <>
        <path d="M3 11l9-7 9 7" />
        <path d="M5 10v9h14v-9" />
      </>
    ),
    ethernet: (
      <>
        <rect x="6" y="3" width="12" height="9" rx="2" />
        <path d="M9 12v3M12 12v3M15 12v3M12 15v6" />
      </>
    ),
    wifi: (
      <>
        <path d="M2 9a15 15 0 0 1 20 0M5.5 12.5a10 10 0 0 1 13 0M9 16a5 5 0 0 1 6 0" />
        <circle cx="12" cy="19.5" r="1" />
      </>
    ),
    globe: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" />
      </>
    ),
    server: (
      <>
        <rect x="4" y="4" width="16" height="6" rx="1.5" />
        <rect x="4" y="14" width="16" height="6" rx="1.5" />
        <path d="M8 7h.01M8 17h.01" />
      </>
    ),
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function MiniBox({ data }: NodeProps<MiniNode>) {
  return (
    <div className={`mini-card tone-${data.tone}`}>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <span className="mini-icon">
        <Glyph name={data.icon} />
      </span>
      <span className="mini-text">
        <strong>{data.title}</strong>
        {data.lines.map((line, index) => (
          <span key={`${index}-${line}`} className={isAnonymized(line) ? "anon-mark" : undefined}>
            {isAnonymized(line) ? shownAnon(line) : line}
          </span>
        ))}
      </span>
      {data.status ? (
        <span className={`mini-status s-${data.status}`} title={STATUS_LABEL[data.status]} />
      ) : null}
    </div>
  );
}

const nodeTypes = { mini: MiniBox };

function mbps(value: number | null): string {
  if (value == null) return "—";
  return value >= 100 ? value.toFixed(0) : value.toFixed(1);
}

function linkLines(link: LinkLive): string[] {
  if (link.status === "unknown" && link.down == null) return ["No recent result"];
  return [
    `↓ ${mbps(link.down)} · ↑ ${mbps(link.up)} Mbps`,
    link.ping != null ? `ping ${link.ping >= 100 ? link.ping.toFixed(0) : link.ping.toFixed(1)} ms` : "",
  ].filter(Boolean);
}

const node = (id: string, x: number, y: number, data: MiniData): MiniNode => ({
  id,
  type: "mini",
  position: { x, y },
  data,
  draggable: false,
  connectable: false,
  selectable: false,
  focusable: false,
  style: { width: 200 },
});

/**
 * One hop of the picture. A failing link turns red and stops moving; a healthy tunnel carries
 * a moving dash, so the diagram shows at a glance which paths are up.
 */
function line(
  id: string,
  source: string,
  target: string,
  color: string,
  label: string,
  options: { dashed?: boolean; flowing?: boolean } = {},
): Edge {
  return {
    id,
    source,
    target,
    selectable: false,
    focusable: false,
    animated: Boolean(options.flowing),
    label,
    labelStyle: { fill: "#c7cee2", fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: "#0e1320", stroke: color, strokeOpacity: 0.4 },
    labelBgPadding: [7, 3],
    labelBgBorderRadius: 9,
    style: {
      stroke: color,
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeDasharray: options.dashed && !options.flowing ? "6 5" : undefined,
    },
  };
}

/**
 * The overview on the Internet Connection page: home, the two links, the internet and the
 * server, filled in with what the Pi last measured. It is a picture, not a control, so nothing
 * in it moves, zooms or takes the pointer; the link that wraps it opens the full diagram.
 */
export default function ConnectionMini({ live }: { live: MiniLive }) {
  const { wired, wireless } = live;
  const wiredColor = wired.status === "fail" ? COLOR.fail : COLOR.wired;
  const wifiColor = wireless.status === "fail" ? COLOR.fail : COLOR.wifi;
  const tunnel = (state: string | undefined) => (state ? (TUNNEL_STATE[state] ?? state) : "");

  const nodes: MiniNode[] = [
    node("home", 0, 62, {
      title: live.name || "Home",
      lines: ["Remote agent", live.online === false ? "Offline" : "Online"],
      tone: "neutral",
      icon: "house",
      status: live.online === undefined ? undefined : live.online ? "ok" : "fail",
    }),
    node("wired", 262, 0, {
      title: "Wired",
      lines: linkLines(wired),
      tone: "wired",
      icon: "ethernet",
      status: wired.status,
    }),
    node("wifi", 262, 130, {
      title: "Wi-Fi",
      lines: linkLines(wireless),
      tone: "wifi",
      icon: "wifi",
      status: wireless.status,
    }),
    node("internet", 524, 62, {
      title: "Internet",
      lines: [live.isp || "ISP"],
      tone: "neutral",
      icon: "globe",
    }),
    node("server", 786, 62, {
      title: "HomeNetWatch",
      lines: ["Server", ...[tunnel(live.tunnels?.wired) && `Wired tunnel: ${tunnel(live.tunnels?.wired)}`, tunnel(live.tunnels?.wireless) && `Wi-Fi tunnel: ${tunnel(live.tunnels?.wireless)}`].filter((value): value is string => Boolean(value))],
      tone: "neutral",
      icon: "server",
    }),
  ];

  const edges: Edge[] = [
    line("home-wired", "home", "wired", wiredColor, "Ethernet"),
    line("home-wifi", "home", "wifi", wifiColor, "Wi-Fi", { dashed: true }),
    line("wired-internet", "wired", "internet", wiredColor, "Tunnel 1", { dashed: true, flowing: wired.status === "ok" }),
    line("wifi-internet", "wifi", "internet", wifiColor, "Tunnel 2", { dashed: true, flowing: wireless.status === "ok" }),
    line("internet-server", "internet", "server", COLOR.neutral, "Cloud"),
  ];

  return (
    <div className="connection-mini" aria-hidden="true">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: { top: "18%", bottom: "22%", x: "6%" } }}
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        colorMode="dark"
      >
        <Background gap={22} size={1} color="#1a2238" />
      </ReactFlow>
    </div>
  );
}
