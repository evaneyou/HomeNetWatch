import {
  BaseEdge,
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
import { useEffect, useMemo, useRef, useState } from "react";

export type SpeedPhase = "idle" | "waiting" | "ping" | "download" | "upload" | "done";

type StageData = { label: string; sub: string; icon: "pi" | "router" | "isp" | "internet" | "server"; active: boolean; pulse: number; tone: string };
type StageNode = Node<StageData, "stage">;
type FlowData = { speed: number; phase: SpeedPhase };
type FlowEdge = Edge<FlowData, "flow">;

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

function NodeCard({ data, handles = false }: { data: StageData; handles?: boolean }) {
  return (
    <div className={data.active ? "sf-node is-active" : "sf-node"} style={{ ["--pulse" as string]: `${data.pulse}s`, ["--tone" as string]: data.tone }}>
      {handles ? <Handle type="target" position={Position.Left} className="sf-handle" isConnectable={false} /> : null}
      <span className="sf-icon">
        <Icon name={data.icon} />
      </span>
      <b>{data.label}</b>
      <small>{data.sub}</small>
      {handles ? <Handle type="source" position={Position.Right} className="sf-handle" isConnectable={false} /> : null}
    </div>
  );
}

function Stage({ data }: NodeProps<StageNode>) {
  return <NodeCard data={data} handles />;
}

/** Dots travel the line. The faster the test, the more of them and the quicker they go. */
function Flow({ sourceX, sourceY, targetX, targetY, data }: EdgeProps<FlowEdge>) {
  const [path] = getStraightPath({ sourceX, sourceY, targetX, targetY });
  const phase = data?.phase ?? "idle";
  const active = phase === "download" || phase === "upload";
  const speed = active ? Math.max(0, data?.speed ?? 0) : 0;
  const count = active ? Math.min(20, Math.max(3, Math.round(3 + speed / 22))) : 2;
  const duration = active ? Math.round(Math.min(2.4, Math.max(0.22, 2.4 / (1 + speed / 45))) * 20) / 20 : 4.5;
  const color = phase === "download" ? COLORS.download : phase === "upload" ? COLORS.upload : COLORS.idle;
  const width = 2 + Math.min(4, speed / 120);
  // A download comes from the right (the server) toward the Pi; an upload goes the other way.
  const keyPoints = phase === "download" ? "1;0" : "0;1";
  return (
    <>
      {active ? <path d={path} fill="none" stroke={color} strokeOpacity={0.18} strokeWidth={width + 7} strokeLinecap="round" /> : null}
      <BaseEdge path={path} style={{ stroke: color, strokeWidth: width, strokeOpacity: active ? 0.75 : 0.35 }} />
      {Array.from({ length: count }, (_unused, index) => (
        <circle key={`${count}-${duration}-${phase}-${index}`} r={active ? 3.2 + Math.min(2, speed / 300) : 2.4} fill={active ? "#ffffff" : color} opacity={active ? 0.95 : 0.4}>
          <animateMotion dur={`${duration}s`} begin={`-${((index / count) * duration).toFixed(3)}s`} repeatCount="indefinite" path={path} calcMode="linear" keyPoints={keyPoints} keyTimes="0;1" />
        </circle>
      ))}
    </>
  );
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

function stagesFor(link: "wired" | "wireless"): Omit<StageData, "active" | "pulse" | "tone">[] {
  return [
    { label: "Raspberry Pi", sub: link === "wired" ? "Wired" : "Wi-Fi", icon: "pi" },
    { label: "Home router", sub: "Your network", icon: "router" },
    { label: "Internet provider", sub: "Your ISP", icon: "isp" },
    { label: "Internet", sub: "The path out", icon: "internet" },
    { label: "Speed server", sub: "LibreSpeed", icon: "server" },
  ];
}

function Diagram({ link, phase, mbps }: { link: "wired" | "wireless"; phase: SpeedPhase; mbps: number }) {
  const active = phase === "download" || phase === "upload";
  const tone = phase === "upload" ? COLORS.upload : COLORS.download;
  const pulse = active ? Math.round(Math.min(1.6, Math.max(0.35, 1.6 / (1 + mbps / 80))) * 20) / 20 : 3;
  const { nodes, edges } = useMemo(() => {
    const stages = stagesFor(link);
    const built: StageNode[] = stages.map((stage, index) => ({
      id: String(index),
      type: "stage",
      position: { x: index * (NODE_WIDTH + GAP), y: 0 },
      data: { ...stage, active, pulse, tone },
      draggable: false,
      selectable: false,
    }));
    const lines: FlowEdge[] = stages.slice(1).map((_stage, index) => ({
      id: `e${index}`,
      type: "flow",
      source: String(index),
      target: String(index + 1),
      data: { speed: mbps, phase },
    }));
    return { nodes: built, edges: lines };
  }, [link, active, pulse, tone, mbps, phase]);

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
      colorMode="dark"
    >
      <Fit />
    </ReactFlow>
  );
}

/** Five full-size cards in a row. A narrow screen scrolls them instead of slicing the icons. */
function Strip({ link, phase, mbps }: { link: "wired" | "wireless"; phase: SpeedPhase; mbps: number }) {
  const active = phase === "download" || phase === "upload";
  const tone = phase === "upload" ? COLORS.upload : COLORS.download;
  const pulse = active ? Math.round(Math.min(1.6, Math.max(0.35, 1.6 / (1 + mbps / 80))) * 20) / 20 : 3;
  return (
    <div className="sf-strip">
      {stagesFor(link).map((stage, index) => (
        <div className="sf-step" key={stage.label}>
          {index > 0 ? <span className="sf-link" aria-hidden="true" /> : null}
          <NodeCard data={{ ...stage, active, pulse, tone }} />
        </div>
      ))}
    </div>
  );
}

/** A small picture of the connection: Pi to router to provider to the internet to the speed server. */
export default function SpeedFlow({ link, phase, mbps }: { link: "wired" | "wireless"; phase: SpeedPhase; mbps: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 1100px)").matches);
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const update = () => setScroll(el.clientWidth < 780);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return (
    <div className={scroll ? "sf is-scroll" : "sf"} ref={box} aria-label="Path of the speed test traffic" role="img">
      {scroll ? (
        <Strip link={link} phase={phase} mbps={mbps} />
      ) : (
        <ReactFlowProvider>
          <Diagram link={link} phase={phase} mbps={mbps} />
        </ReactFlowProvider>
      )}
    </div>
  );
}
