import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  NodeToolbar,
  Position,
  ReactFlow,
  ViewportPortal,
  getBezierPath,
  useNodes,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo, useState } from "react";
import { isAnonymized, shownAnon } from "../anonymized";
import { EDGE_IDS, NODE, SCAN_EDGE, edgeId, hostOf, hostsOf, pathsFor, type FlowLink, type FlowTest } from "../flowPaths";
import type { MonitorAdmin, MonitorDetail, RfOverview } from "../types";

type Tone = "wired" | "wifi" | "neutral";
type Fact = [label: string, value: string];
type Through = { flow: FlowTest; role: "data" | "control" };
type NodeInfo = {
  facts: Fact[];
  through: Through[];
  side: Position.Top | Position.Bottom;
  align: "start" | "center" | "end";
  passive: boolean;
};
type FlowNodeData = {
  title: string;
  lines: string[];
  tone: Tone;
  active: boolean;
  dim: boolean;
  /** Part of the picture but never on a flow's path: the radio and the networks it hears. */
  passive: boolean;
  info: NodeInfo;
};
type FlowNodeType = Node<FlowNodeData, "box">;

type Pulse = { role: "data" | "control"; color: string };
type RouteEdgeData = { label: string; pulses: Pulse[]; dim: boolean; scan?: boolean };

type FrameData = { label: string; kind: "zone" | "agent" };
type FrameNodeType = Node<FrameData, "frame">;
type RouteEdgeType = Edge<RouteEdgeData, "route">;

const COLOR: Record<FlowLink, string> = { wired: "#4fd1b5", wifi: "#ffc24d" };
const QUIET = "#2a3550";
const SHARED = "#8fa3d1";
const RETURN_COLOR = "#e8ecf8";

/** How fast a pulse travels, in diagram pixels per second, the same on every stretch of a route. */
const SPEED = 230;
/** A pause when it reaches the far end, and a longer one before it sets off again. */
const TURN = 0.18;
const REST = 0.7;

const STATUS_LABEL: Record<FlowTest["status"], string> = {
  ok: "Passing",
  degraded: "Degraded",
  fail: "Failing",
  idle: "No recent result",
};

function PathBox({ data }: NodeProps<FlowNodeType>) {
  const [open, setOpen] = useState(false);
  const classes = [
    "flow-node",
    `tone-${data.tone}`,
    data.active ? "is-active" : "",
    data.dim ? "is-dim" : "",
    data.passive ? "is-passive" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const { facts, through, side, align } = data.info;
  const rows = [...new Set(through.map(({ flow }) => flow.key))].map((key) => {
    const entries = through.filter(({ flow }) => flow.key === key);
    return { flow: entries[0].flow, control: entries.every(({ role }) => role === "control") };
  });
  return (
    <div
      className={classes}
      tabIndex={0}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <strong>{data.title}</strong>
      {data.lines.map((line, index) => (
        <span key={`${index}-${line}`} className={isAnonymized(line) ? "anon-mark" : undefined}>
            {isAnonymized(line) ? shownAnon(line) : line}
        </span>
      ))}
      <NodeToolbar isVisible={open} position={side} align={align} offset={12}>
        <div className="flow-tip" role="tooltip">
          <h4>{data.title}</h4>
          {facts.length > 0 ? (
            <dl>
              {facts.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd className={isAnonymized(value) ? "anon-mark" : undefined}>
                    {isAnonymized(value) ? shownAnon(value) : value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {rows.length > 0 ? (
            <>
              <h5>Flows through here</h5>
              <ul>
                {rows.map(({ flow, control }) => (
                  <li key={flow.key}>
                    <span className={`flow-dot status-${flow.status}`} title={STATUS_LABEL[flow.status]} />
                    <strong>
                      {flow.kindLabel} · {flow.link === "wired" ? "Wired" : "Wi-Fi"}
                      {control ? " (signalling)" : ""}
                    </strong>
                    <span>
                      {[flow.route === "web" && !flow.members ? hostOf(flow.target) : "", flow.detail].filter(Boolean).join(" · ") ||
                        STATUS_LABEL[flow.status]}
                    </span>
                    {flow.status === "degraded" || flow.status === "fail" ? (
                      <em>{flow.reason ?? STATUS_LABEL[flow.status]}</em>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : data.passive ? null : (
            <p>Nothing in the selected service passes through here.</p>
          )}
        </div>
      </NodeToolbar>
    </div>
  );
}

type Box = { x: number; y: number; w: number; h: number };
type Route = { key: string; d: string; length: number; color: string; role: "data" | "control"; offset: number };

function fractions(value: number): string {
  return value.toFixed(4);
}

/** Length of a cubic bezier, by walking along it; close enough to set a speed. */
function bezierLength(p0: [number, number], p1: [number, number], p2: [number, number], p3: [number, number]): number {
  let length = 0;
  let last = p0;
  for (let step = 1; step <= 24; step += 1) {
    const t = step / 24;
    const u = 1 - t;
    const point: [number, number] = [
      u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
      u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    ];
    length += Math.hypot(point[0] - last[0], point[1] - last[1]);
    last = point;
  }
  return length;
}

/**
 * One unbroken path through every box of a route: the same curve each edge draws between two boxes,
 * then straight across the next box to the curve that leaves it. A pulse that rides this path is
 * never off the line, so it crosses the boxes instead of vanishing at one edge and appearing at the next.
 */
function routeGeometry(boxes: Box[]): { d: string; length: number } {
  let d = "";
  let length = 0;
  boxes.forEach((box, index) => {
    const right: [number, number] = [box.x + box.w, box.y + box.h / 2];
    if (index === 0) {
      d = `M ${right[0]} ${right[1]}`;
      return;
    }
    const before = boxes[index - 1];
    const from: [number, number] = [before.x + before.w, before.y + before.h / 2];
    const left: [number, number] = [box.x, box.y + box.h / 2];
    const lean = Math.max(0, (left[0] - from[0]) / 2);
    const c1: [number, number] = [from[0] + lean, from[1]];
    const c2: [number, number] = [left[0] - lean, left[1]];
    d += ` C ${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${left[0]} ${left[1]}`;
    length += bezierLength(from, c1, c2, left);
    if (index < boxes.length - 1) {
      d += ` L ${right[0]} ${right[1]}`;
      length += box.w;
    }
  });
  return { d, length };
}

function cycle(length: number): { out: number; total: number } {
  const out = length / SPEED;
  return { out, total: out * 2 + TURN + REST };
}

/**
 * A dot that rides a route during its own slice of the cycle. Position is a fraction of the path's
 * length, so equal time is equal distance: the same speed everywhere along it.
 */
function Dot({
  route,
  color,
  radius,
  reverse,
  lag,
  fade,
}: {
  route: Route;
  color: string;
  radius: number;
  reverse: boolean;
  lag: number;
  fade: number;
}) {
  const { out, total } = cycle(route.length);
  const start = reverse ? out + TURN : 0;
  const a = start / total;
  const b = (start + out) / total;
  const shift = (route.offset - lag).toFixed(3);
  const from = reverse ? 1 : 0;
  const to = reverse ? 0 : 1;
  return (
    <circle
      r={radius}
      fill={color}
      stroke="#0b0f1a"
      strokeWidth={1.5}
      opacity={0}
      style={{ filter: `drop-shadow(0 0 ${radius}px ${color})` }}
    >
      <animateMotion
        dur={`${total}s`}
        begin={`-${shift}s`}
        repeatCount="indefinite"
        path={route.d}
        calcMode="linear"
        keyPoints={`${from};${from};${to};${to}`}
        keyTimes={`0;${fractions(a)};${fractions(b)};1`}
      />
      <animate
        attributeName="opacity"
        dur={`${total}s`}
        begin={`-${shift}s`}
        repeatCount="indefinite"
        calcMode="discrete"
        values={`0;${fade};0`}
        keyTimes={`0;${fractions(a)};${fractions(b)}`}
      />
    </circle>
  );
}

/** A bright head and a short fading tail, so the direction reads even over a dashed line. */
function Pulse({ route }: { route: Route }) {
  const data = route.role === "data";
  const head = data ? 4.8 : 3.8;
  const tail = [
    { lag: 0.09, radius: head * 0.74, fade: 0.55 },
    { lag: 0.18, radius: head * 0.52, fade: 0.3 },
  ];
  return (
    <g>
      {[false, true].map((reverse) => {
        const color = reverse ? RETURN_COLOR : route.color;
        const strength = data ? 1 : 0.8;
        return (
          <g key={String(reverse)}>
            {tail.map((part) => (
              <Dot key={part.lag} route={route} color={color} radius={part.radius} reverse={reverse} lag={part.lag} fade={part.fade * strength} />
            ))}
            <Dot route={route} color={color} radius={head} reverse={reverse} lag={0} fade={strength} />
          </g>
        );
      })}
    </g>
  );
}

/**
 * Draws every selected flow's pulses over the diagram, in the diagram's own coordinates so they follow
 * pan and zoom. Each flow gets its own place in the cycle, so flows that share a stretch do not overlap.
 */
function FlowPulses({ flows }: { flows: FlowTest[] }) {
  const nodes = useNodes();
  const routes = useMemo(() => {
    const boxes = new Map<string, Box>();
    for (const node of nodes) {
      boxes.set(node.id, {
        x: node.position.x,
        y: node.position.y,
        w: node.measured?.width ?? 190,
        h: node.measured?.height ?? 80,
      });
    }
    const built: Route[] = [];
    flows.forEach((flow, index) => {
      for (const path of pathsFor(flow)) {
        const known = path.nodes.map((id) => boxes.get(id));
        if (known.some((box) => !box)) continue;
        const { d, length } = routeGeometry(known as Box[]);
        built.push({
          key: `${flow.key}:${path.role}`,
          d,
          length,
          color: COLOR[flow.link],
          role: path.role,
          offset: (index / flows.length) * cycle(length).total,
        });
      }
    });
    return built;
  }, [flows, nodes]);
  if (routes.length === 0) return null;
  return (
    <ViewportPortal>
      <svg className="flow-pulses" width={1} height={1} aria-hidden="true">
        {routes.map((route) => (
          <Pulse key={route.key} route={route} />
        ))}
      </svg>
    </ViewportPortal>
  );
}

function RouteEdge(props: EdgeProps<RouteEdgeType>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const pulses = data?.pulses ?? [];
  const active = pulses.length > 0;
  const controlOnly = active && pulses.every((pulse) => pulse.role === "control");
  const colors = new Set(pulses.map((pulse) => pulse.color));
  const color = colors.size === 1 ? [...colors][0] : SHARED;
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: active ? color : data?.scan ? "#6d7895" : QUIET,
          strokeWidth: active ? 2.5 : 1.5,
          strokeLinecap: "round",
          strokeDasharray: data?.scan ? "2 6" : controlOnly ? "5 5" : undefined,
          opacity: active || !data?.dim ? 1 : 0.45,
        }}
      />
      {controlOnly ? (
        <BaseEdge
          id={`${id}-track`}
          path={path}
          interactionWidth={0}
          style={{ stroke: color, strokeWidth: 3, strokeOpacity: 0.22, strokeLinecap: "round" }}
        />
      ) : null}
      {data?.label ? (
        <EdgeLabelRenderer>
          <div
            className={`flow-edge-label nodrag nopan${active ? " is-active" : ""}`}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

function FrameBox({ data }: NodeProps<FrameNodeType>) {
  return (
    <div className={`flow-frame kind-${data.kind}`}>
      <span>{data.label}</span>
    </div>
  );
}

const nodeTypes = { box: PathBox, frame: FrameBox };
const edgeTypes = { route: RouteEdge };

const EDGE_LABEL: Record<string, string> = {
  [edgeId(NODE.piWired, NODE.router)]: "Ethernet",
  [edgeId(NODE.piWifi, NODE.router)]: "Wi-Fi",
  [edgeId(NODE.router, NODE.internet)]: "WAN",
  [edgeId(NODE.internet, NODE.serverDirect)]: "Public address",
  [edgeId(NODE.internet, NODE.tunnelWired)]: "Tunnel 1",
  [edgeId(NODE.internet, NODE.tunnelWifi)]: "Tunnel 2",
  [edgeId(NODE.tunnelWired, NODE.serverDirect)]: "Signalling",
  [edgeId(NODE.tunnelWifi, NODE.serverDirect)]: "Signalling",
  [SCAN_EDGE]: "Scans",
};

type Spec = { id: string; x: number; y: number; tone: Tone; title: string; lines: string[]; passive?: boolean };

function specs(monitor: MonitorDetail | undefined, rf: RfOverview | null | undefined): Spec[] {
  const name = monitor?.name || "Pi";
  const clean = (lines: (string | null | undefined)[]) => lines.filter((line): line is string => Boolean(line));
  return [
    {
      id: NODE.piWired,
      x: 0,
      y: 10,
      tone: "wired",
      title: `${name} · Wired`,
      lines: clean([monitor?.wired_iface ? `Ethernet ${monitor.wired_iface}` : "Ethernet", monitor?.tunnel_address]),
    },
    {
      id: NODE.piWifi,
      x: 0,
      y: 230,
      tone: "wifi",
      title: `${name} · Wi-Fi`,
      lines: clean([
        monitor?.wifi_link_iface ? `Wi-Fi client ${monitor.wifi_link_iface}` : "Wi-Fi client",
        monitor?.wireless_tunnel_address,
      ]),
    },
    {
      id: NODE.piRadio,
      x: 0,
      y: 350,
      tone: "neutral",
      passive: true,
      title: `${name} · Monitor radio`,
      lines: clean([monitor?.wifi_radio_iface ? `Monitor mode ${monitor.wifi_radio_iface}` : "Monitor mode", "Wi-Fi scans"]),
    },
    {
      id: NODE.nearby,
      x: 250,
      y: 350,
      tone: "neutral",
      passive: true,
      title: "Nearby Wi-Fi networks",
      lines: clean([
        rf ? `${rf.bands.reduce((total, band) => total + band.ap_count, 0)} networks heard` : "",
        "Scanned, never joined",
      ]),
    },
    { id: NODE.router, x: 250, y: 120, tone: "neutral", title: "Home router", lines: ["LAN switch and Wi-Fi access point"] },
    {
      id: NODE.internet,
      x: 570,
      y: 120,
      tone: "neutral",
      title: "ISP",
      lines: clean([monitor?.isp ?? "ISP", monitor?.public_ip]),
    },
    { id: NODE.web, x: 1170, y: -20, tone: "neutral", title: "Web services", lines: ["Public sites", "Not through a tunnel"] },
    {
      id: NODE.serverDirect,
      x: 1170,
      y: 105,
      tone: "neutral",
      title: "Measurement service",
      lines: ["Payload: UDP 8998, public", "Signalling: TCP 8999, tunnel"],
    },
    { id: NODE.tunnelWired, x: 830, y: 235, tone: "wired", title: "WireGuard · wired", lines: ["10.7.0.1 · UDP 51820"] },
    { id: NODE.tunnelWifi, x: 830, y: 345, tone: "wifi", title: "WireGuard · Wi-Fi", lines: ["10.7.1.1 · UDP 51821"] },
    { id: NODE.services, x: 1170, y: 290, tone: "neutral", title: "Server services", lines: ["API and Bandwidth Test", "Inside the tunnel"] },
  ];
}

const TUNNEL_STATE: Record<string, string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  never_connected: "Not connected yet",
};

const RADIO_MODE: Record<string, string> = { kismet: "Kismet capture", off: "Off" };

function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "";
}

/** What is known about one hop, whatever flows pass through it. */
function factsFor(
  id: string,
  monitor: MonitorDetail | undefined,
  agent: MonitorAdmin | null | undefined,
  flows: FlowTest[],
  rf: RfOverview | null | undefined,
): Fact[] {
  const facts: Fact[] = [];
  const add = (label: string, value: string | null | undefined) => {
    if (value) facts.push([label, value]);
  };
  // The agent works its "LAN IP" out from the route to the server, which now runs through the
  // tunnel, so it can report the tunnel address. Showing that as a LAN address would be wrong.
  const reported = agent?.lan_ip ?? monitor?.lan_ip;
  const sameAsTunnel =
    !isAnonymized(reported) &&
    (reported === monitor?.tunnel_address || reported === monitor?.wireless_tunnel_address);
  const lan = reported && !sameAsTunnel ? reported : null;
  switch (id) {
    case NODE.piWired:
      add("Status", monitor?.status);
      add("Last seen", when(monitor?.last_seen_at));
      add("Adapter", monitor?.wired_iface);
      add("LAN address", lan);
      add("Tunnel address", monitor?.tunnel_address);
      add("Tunnel", agent?.tunnel_state ? (TUNNEL_STATE[agent.tunnel_state] ?? agent.tunnel_state) : "");
      add("Last handshake", when(agent?.last_handshake_at));
      add("Agent version", agent?.agent_version);
      break;
    case NODE.piWifi:
      add("Wi-Fi client", monitor?.wifi_link_iface);
      add("Monitor radio", monitor?.wifi_radio_iface);
      add("Tunnel address", monitor?.wireless_tunnel_address);
      add(
        "Tunnel",
        agent?.wireless_tunnel_state ? (TUNNEL_STATE[agent.wireless_tunnel_state] ?? agent.wireless_tunnel_state) : "",
      );
      break;
    case NODE.piRadio:
      add("Adapter", monitor?.wifi_radio_iface);
      add("Mode", monitor?.airwave_mode ? (RADIO_MODE[monitor.airwave_mode] ?? monitor.airwave_mode) : "");
      add("Purpose", "Listens to nearby Wi-Fi; never joins a network");
      add("Last scan", when(rf?.last_scan_at));
      break;
    case NODE.nearby:
      for (const band of rf?.bands ?? []) {
        add(`${/^[\d.]+$/.test(band.band) ? `${band.band} GHz` : band.band} networks`, String(band.ap_count));
      }
      add("Last scan", when(rf?.last_scan_at));
      add("Joined", "No: heard, never joined");
      break;
    case NODE.router:
      add("ISP", monitor?.isp);
      add("Pi LAN address", lan);
      add("Carries", "Ethernet and Wi-Fi");
      break;
    case NODE.internet:
      add("Provider", monitor?.isp);
      add("Public address", monitor?.public_ip);
      break;
    case NODE.web: {
      const hosts = [...new Set(flows.filter((flow) => flow.route === "web").flatMap(hostsOf))].sort();
      add(hosts.length > 1 ? "Destinations" : "Destination", hosts.join(", "));
      add("Through a tunnel", "No");
      break;
    }
    case NODE.serverDirect:
      add("Payload", "Direct over the internet to the public address, UDP 8998");
      add("Signalling", "Through the tunnel to the tunnel address, TCP 8999");
      add("Also reached by", "MTR traces to the public address");
      break;
    case NODE.tunnelWired:
      add("Address", "10.7.0.1 · UDP 51820");
      add("Tunnel", agent?.tunnel_state ? (TUNNEL_STATE[agent.tunnel_state] ?? agent.tunnel_state) : "");
      add("Last handshake", when(agent?.last_handshake_at));
      break;
    case NODE.tunnelWifi:
      add("Address", "10.7.1.1 · UDP 51821");
      add(
        "Tunnel",
        agent?.wireless_tunnel_state ? (TUNNEL_STATE[agent.wireless_tunnel_state] ?? agent.wireless_tunnel_state) : "",
      );
      break;
    case NODE.services:
      add("Serves", "API and Bandwidth Test");
      add("Reached", "Only inside a tunnel");
      break;
  }
  return facts;
}

const NODE_W = 190;
// A box is as tall as its lines; this is a little over the tallest, so a frame always clears them.
const NODE_H = 88;

type Rect = { x: number; y: number; w: number; h: number };

function around(rects: Rect[], pad: { left: number; right: number; top: number; bottom: number }): Rect {
  const left = Math.min(...rects.map((rect) => rect.x)) - pad.left;
  const top = Math.min(...rects.map((rect) => rect.y)) - pad.top;
  const right = Math.max(...rects.map((rect) => rect.x + rect.w)) + pad.right;
  const bottom = Math.max(...rects.map((rect) => rect.y + rect.h)) + pad.bottom;
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/**
 * The Pi's three interfaces in one solid box, because they are one device, and three dotted
 * zones: your home, the internet (the ISP and the tunnels that cross it) and the cloud (the
 * websites and the server). Each is sized from the boxes it holds, so moving a box moves its frame.
 */
function frames(list: Spec[], name: string): FrameNodeType[] {
  const at = (ids: string[]): Rect[] =>
    list.filter((spec) => ids.includes(spec.id)).map((spec) => ({ x: spec.x, y: spec.y, w: NODE_W, h: NODE_H }));
  const agent = around(at([NODE.piWired, NODE.piWifi, NODE.piRadio]), { left: 20, right: 20, top: 46, bottom: 20 });
  const zone = { left: 30, right: 30, top: 54, bottom: 30 };
  const home = around([agent, ...at([NODE.router, NODE.nearby])], zone);
  const internet = around(at([NODE.internet, NODE.tunnelWired, NODE.tunnelWifi]), zone);
  const cloud = around(at([NODE.web, NODE.serverDirect, NODE.services]), zone);
  const frame = (id: string, rect: Rect, data: FrameData): FrameNodeType => ({
    id,
    type: "frame",
    position: { x: rect.x, y: rect.y },
    data,
    draggable: false,
    connectable: false,
    selectable: false,
    focusable: false,
    style: { width: rect.w, height: rect.h },
  });
  return [
    frame("frame-home", home, { label: "Your home", kind: "zone" }),
    frame("frame-internet", internet, { label: "Internet", kind: "zone" }),
    frame("frame-cloud", cloud, { label: "Cloud services", kind: "zone" }),
    frame("frame-agent", agent, { label: `${name} · remote agent`, kind: "agent" }),
  ];
}

export default function FlowDiagram({
  flows,
  monitor,
  agent,
  rf,
}: {
  flows: FlowTest[];
  monitor?: MonitorDetail;
  agent?: MonitorAdmin | null;
  rf?: RfOverview | null;
}) {
  const { nodes, edges } = useMemo(() => {
    const active = flows.length > 0;
    const lit = new Set<string>();
    const pulses = new Map<string, Pulse[]>();
    const through = new Map<string, Through[]>();
    flows.forEach((flow) => {
      for (const path of pathsFor(flow)) {
        path.nodes.forEach((node) => {
          lit.add(node);
          through.set(node, [...(through.get(node) ?? []), { flow, role: path.role }]);
        });
        for (let hop = 0; hop < path.nodes.length - 1; hop += 1) {
          const id = edgeId(path.nodes[hop], path.nodes[hop + 1]);
          pulses.set(id, [...(pulses.get(id) ?? []), { role: path.role, color: COLOR[flow.link] }]);
        }
      }
    });
    const list = specs(monitor, rf);
    const built: FlowNodeType[] = list.map((spec) => ({
      id: spec.id,
      type: "box",
      position: { x: spec.x, y: spec.y },
      data: { title: spec.title, lines: spec.lines, tone: spec.tone, active: lit.has(spec.id), dim: active && !lit.has(spec.id) && !spec.passive,
        passive: Boolean(spec.passive),
        // The top row opens its card downwards so it is not cut off by the edge of the canvas.
        info: {
          facts: factsFor(spec.id, monitor, agent, flows, rf),
          passive: Boolean(spec.passive),
          through: through.get(spec.id) ?? [],
          side: spec.y < 150 ? Position.Bottom : Position.Top,
          // The outer columns open their card inwards, so it is not cut off by the side of the canvas.
          align: spec.x === 0 ? "start" : spec.x >= 750 ? "end" : "center",
        },
      },
      draggable: false,
      connectable: false,
      selectable: false,
      style: { width: 190 },
    }));
    const wires: RouteEdgeType[] = EDGE_IDS.map((id) => {
      const [source, target] = id.split(">");
      return {
        id,
        type: "route",
        source,
        target,
        selectable: false,
        data: { label: EDGE_LABEL[id] ?? "", pulses: pulses.get(id) ?? [], dim: active },
      };
    });
    wires.push({
      id: SCAN_EDGE,
      type: "route",
      source: NODE.piRadio,
      target: NODE.nearby,
      selectable: false,
      data: { label: EDGE_LABEL[SCAN_EDGE], pulses: [], dim: false, scan: true },
    });
    return { nodes: [...frames(list, monitor?.name || "Pi"), ...built], edges: wires };
  }, [flows, monitor, agent, rf]);

  return (
    <div className="flow-canvas" aria-label="Animated paths of the selected service">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        minZoom={0.35}
        maxZoom={2.5}
        // A plain wheel scrolls the page; zooming takes Ctrl or Cmd so the diagram never traps it.
        zoomOnScroll={false}
        zoomActivationKeyCode={["Control", "Meta"]}
        zoomOnPinch
        zoomOnDoubleClick
        panOnDrag
        preventScrolling={false}
        colorMode="dark"
      >
        <Background gap={24} size={1} color="#1a2238" />
        <FlowPulses flows={flows} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}
