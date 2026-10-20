import {
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getStraightPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  useNodesInitialized,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type RefObject } from "react";

export type SpeedPhase = "idle" | "waiting" | "ping" | "download" | "upload" | "done";

type StageData = { label: string; sub: string; icon: "pi" | "router" | "isp" | "internet" | "server"; index: number };
type StageNode = Node<StageData, "stage">;
type FlowEdge = Edge<Record<string, never>, "flow">;
type Live = { speed: number; phase: SpeedPhase };

const LiveCtx = createContext<RefObject<Live> | null>(null);
const SHORT = ["Pi", "Router", "ISP", "Net", "Server"];

const NODE_WIDTH = 112;
const GAP = 50;
const COLORS = { download: "#4fd1b5", upload: "#6aa8ff", idle: "#5b6788" };

function Icon({ name }: { name: StageData["icon"] }) {
  const common = { width: 26, height: 26, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (name === "pi") {
    return (
      <svg {...common} aria-hidden="true">
        <rect x="5" y="5" width="14" height="14" rx="2.5" />
        <path d="M9 2.5v2.5M15 2.5v2.5M9 19v2.5M15 19v2.5M2.5 9H5M2.5 15H5M19 9h2.5M19 15h2.5" />
        <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
      </svg>
    );
  }
  if (name === "router") {
    return (
      <svg {...common} aria-hidden="true">
        <rect x="3" y="13" width="18" height="6" rx="2" />
        <path d="M7 16h.01M11 16h.01M8 13V9M16 13V9M5.5 6.5a4.5 4.5 0 0 1 13 0" />
      </svg>
    );
  }
  if (name === "isp") {
    return (
      <svg {...common} aria-hidden="true">
        <path d="M12 21V9M12 9 8 21M12 9l4 12M9.5 15h5" />
        <path d="M7.5 6a6 6 0 0 1 9 0M5 3.5a9.5 9.5 0 0 1 14 0" />
      </svg>
    );
  }
  if (name === "internet") {
    return (
      <svg {...common} aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9S14.500 18.500 12 21c-2.500-2.500-3.800-5.500-3.800-9S9.500 5.500 12 3z" />
      </svg>
    );
  }
  return (
    <svg {...common} aria-hidden="true">
      <rect x="4" y="4" width="16" height="6" rx="1.5" />
      <rect x="4" y="14" width="16" height="6" rx="1.5" />
      <path d="M8 7h.01M8 17h.01" />
    </svg>
  );
}

function NodeCard({ data, handles = false, compact = false }: { data: StageData; handles?: boolean; compact?: boolean }) {
  return (
    <div className="sf-node" title={compact ? `${data.label}, ${data.sub}` : undefined}>
      {handles ? <Handle type="target" position={Position.Left} className="sf-handle" isConnectable={false} /> : null}
      <span className="sf-icon" style={{ animationDelay: `${data.index * 0.16}s` }}>
        <Icon name={data.icon} />
      </span>
      <b>{compact ? SHORT[data.index] : data.label}</b>
      {compact ? null : <small>{data.sub}</small>}
      {handles ? <Handle type="source" position={Position.Right} className="sf-handle" isConnectable={false} /> : null}
    </div>
  );
}

function Stage({ data }: NodeProps<StageNode>) {
  return <NodeCard data={data} handles />;
}

const PACKET_COUNT = 8;

/** Moves packets along a path from the live speed. The elements stay mounted, so the motion does not restart on each sample. */
function startPackets(live: RefObject<Live>, path: SVGPathElement, line: SVGPathElement, glow: SVGPathElement, dots: SVGCircleElement[], scale: number) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const place = dots.map((_, index) => index / dots.length);
  let shown = 0;
  let offset = 0;
  let last = performance.now();
  let frame = 0;
  const tick = (now: number) => {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    const { speed, phase } = live.current;
    const active = phase === "download" || phase === "upload";
    const target = active ? Math.max(0, speed) : 0;
    shown += (target - shown) * (1 - Math.exp(-dt * 3));
    const len = Math.max(1, path.getTotalLength());
    const dir = phase === "download" ? -1 : 1;
    const pace = reduced ? 0 : 32 + Math.min(460, shown * 2.2);
    const color = phase === "upload" ? COLORS.upload : phase === "download" ? COLORS.download : COLORS.idle;
    offset -= dir * pace * dt;
    const dash = active && !reduced ? 4 + Math.min(9, shown / 45) : 0;
    line.style.stroke = color;
      line.style.strokeWidth = String((active ? 2.2 + Math.min(2.4, shown / 160) : 2) * scale);
    line.style.strokeOpacity = active ? "0.92" : "0.38";
    line.style.strokeDasharray = dash ? `${dash} ${Math.round(dash * 1.3)}` : "none";
    line.style.strokeDashoffset = String(offset);
    glow.style.stroke = color;
      glow.style.strokeWidth = String((8 + Math.min(10, shown / 35)) * scale);
    glow.style.strokeOpacity = active ? String(0.1 + Math.min(0.42, shown / 280)) : "0";
    for (let index = 0; index < dots.length; index += 1) {
      if (!reduced) {
        place[index] += (dir * pace * dt) / len;
        place[index] %= 1;
        if (place[index] < 0) place[index] += 1;
      }
      const point = path.getPointAtLength(place[index] * len);
      const lead = index === 0;
      const breathe = active ? 0.8 + 0.2 * Math.sin(now / 200 + index) : 1;
      const dot = dots[index];
      dot.setAttribute("cx", point.x.toFixed(2));
      dot.setAttribute("cy", point.y.toFixed(2));
      dot.setAttribute("r", ((lead ? 3.3 + Math.min(2.6, shown / 140) : 1.6 + (index % 3) * 0.5) * breathe * scale).toFixed(2));
      dot.setAttribute("fill", active && lead ? "#ffffff" : color);
      dot.setAttribute("opacity", active ? (lead ? "1" : (0.38 + (index % 4) * 0.12).toFixed(2)) : "0.3");
    }
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(frame);
}

function PacketLine({ path, live, compact = false }: { path: string; live: RefObject<Live>; compact?: boolean }) {
  const root = useRef<SVGGElement>(null);
  useEffect(() => {
    const rootEl = root.current;
    if (!rootEl) return undefined;
    const line = rootEl.querySelector("[data-sf='line']") as SVGPathElement | null;
    const glow = rootEl.querySelector("[data-sf='glow']") as SVGPathElement | null;
    const dots = Array.from(rootEl.querySelectorAll("circle"));
    if (!line || !glow || dots.length === 0) return undefined;
    return startPackets(live, line, line, glow, dots, compact ? 0.62 : 1);
  }, [path, live, compact]);
  const count = compact ? 4 : PACKET_COUNT;
  return (
    <g ref={root}>
      <path data-sf="glow" d={path} fill="none" stroke={COLORS.idle} strokeWidth={compact ? 5 : 8} strokeLinecap="round" />
      <path data-sf="line" d={path} fill="none" stroke={COLORS.idle} strokeWidth={compact ? 1.6 : 2} strokeLinecap="round" />
      {Array.from({ length: count }, (_, index) => (
        <circle key={index} r={compact ? 1.4 : 2} fill={COLORS.idle} />
      ))}
    </g>
  );
}

/** Dots travel the line. Download runs toward the Pi, upload runs toward the server, and the pace follows the speed. */
function Flow({ sourceX, sourceY, targetX, targetY }: EdgeProps<FlowEdge>) {
  const [path] = getStraightPath({ sourceX, sourceY, targetX, targetY });
  const live = useContext(LiveCtx);
  if (!live) return null;
  return <PacketLine path={path} live={live} />;
}

const nodeTypes = { stage: Stage };
const edgeTypes = { flow: Flow };

/** Refit after the nodes have a real size, and again when the box changes width. */
function Fit() {
  const { fitView } = useReactFlow();
  const ready = useNodesInitialized();
  useEffect(() => {
    if (!ready) return undefined;
    const fit = () => fitView({ padding: 0.14, duration: 0 });
    const frame = requestAnimationFrame(fit);
    return () => cancelAnimationFrame(frame);
  }, [ready, fitView]);
  return null;
}

function stagesFor(link: "wired" | "wireless"): Omit<StageData, "index">[] {
  return [
    { label: "Raspberry Pi", sub: link === "wired" ? "Wired" : "Wi-Fi", icon: "pi" },
    { label: "Home router", sub: "Your network", icon: "router" },
    { label: "Internet provider", sub: "Your ISP", icon: "isp" },
    { label: "Internet", sub: "The path out", icon: "internet" },
    { label: "Speed server", sub: "LibreSpeed", icon: "server" },
  ];
}

function Diagram({ link }: { link: "wired" | "wireless" }) {
  const { nodes, edges } = useMemo(() => {
    const stages = stagesFor(link);
    const built: StageNode[] = stages.map((stage, index) => ({
      id: String(index),
      type: "stage",
      position: { x: index * (NODE_WIDTH + GAP), y: 0 },
      data: { ...stage, index },
      draggable: false,
      selectable: false,
    }));
    const lines: FlowEdge[] = stages.slice(1).map((_stage, index) => ({
      id: `e${index}`,
      type: "flow",
      source: String(index),
      target: String(index + 1),
      data: {},
    }));
    return { nodes: built, edges: lines };
  }, [link]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitView
      fitViewOptions={{ padding: 0.14 }}
      minZoom={0.2}
      maxZoom={1.2}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag={false}
      zoomOnScroll={false}
      zoomOnPinch={false}
      zoomOnDoubleClick={false}
      preventScrolling={false}
      proOptions={{ hideAttribution: true }}
      colorMode="dark"
    >
      <Fit />
    </ReactFlow>
  );
}

/** The phone path: the same five stops, drawn small enough to fit, with the same moving packets. */
function Mini({ link }: { link: "wired" | "wireless" }) {
  const live = useContext(LiveCtx);
  if (!live) return null;
  return (
    <div className="sf-mini">
      {stagesFor(link).map((stage, index) => (
        <div className="sf-step" key={stage.label}>
          {index > 0 ? <Lane live={live} /> : null}
          <NodeCard data={{ ...stage, index }} compact />
        </div>
      ))}
    </div>
  );
}

function Lane({ live }: { live: RefObject<Live> }) {
  return (
    <svg className="sf-lane" viewBox="0 0 28 16" aria-hidden="true">
      <PacketLine path="M1 8 H27" live={live} compact />
    </svg>
  );
}

/** A small picture of the connection: Pi to router to provider to the internet to the speed server. */
export default function SpeedFlow({ link, phase, mbps }: { link: "wired" | "wireless"; phase: SpeedPhase; mbps: number }) {
  const box = useRef<HTMLDivElement>(null);
  const live = useRef<Live>({ speed: mbps, phase });
  live.current = { speed: mbps, phase };
  const [compact, setCompact] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 1100px)").matches);
  const running = phase === "download" || phase === "upload";
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const update = () => setCompact(el.clientWidth < 780);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return (
    <LiveCtx.Provider value={live}>
      <div className={`sf${compact ? " is-compact" : ""}${running ? " is-live" : ""}${phase === "upload" ? " is-up" : ""}`} ref={box} aria-label="Path of the speed test traffic" role="img">
        {compact ? (
          <Mini link={link} />
        ) : (
          <ReactFlowProvider>
            <Diagram link={link} />
          </ReactFlowProvider>
        )}
      </div>
    </LiveCtx.Provider>
  );
}
