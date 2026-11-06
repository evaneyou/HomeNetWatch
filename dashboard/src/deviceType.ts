/**
 * What kind of remote agent a device is. The device says so in its signed check-in, so the dashboard never asks.
 * An Android agent (an NVIDIA Shield or another Android TV) only runs the Wi-Fi voice stream and the Speed Test,
 * so its pages leave out the Pi's wired link, internet checks and device vitals.
 */
export type Platform = "pi" | "android";

export type AgentLinkCapabilitySource = {
  platform?: string | null;
  wired_iface?: string | null;
  wifi_link_iface?: string | null;
  tunnel_state?: "never_connected" | "connected" | "disconnected" | null;
  wireless_tunnel_state?: "never_connected" | "connected" | "disconnected" | null;
  tunnel_address?: string | null;
  wireless_tunnel_address?: string | null;
};

export function isAndroid(platform: string | null | undefined): boolean {
  return platform === "android";
}

export function deviceTypeLabel(platform: string | null | undefined): string {
  return isAndroid(platform) ? "Android TV" : "Raspberry Pi";
}

/** The links an agent can test. An Android agent is on Wi-Fi only; the server refuses Wired for it too. */
export function agentLinks(
  agentOrPlatform: string | AgentLinkCapabilitySource | null | undefined,
): ("wired" | "wireless")[] {
  if (!agentOrPlatform) {
    return ["wired", "wireless"];
  }
  if (typeof agentOrPlatform === "string") {
    return isAndroid(agentOrPlatform) ? ["wireless"] : ["wired", "wireless"];
  }
  const {
    platform,
    wired_iface,
    wifi_link_iface,
    tunnel_state,
    wireless_tunnel_state,
    tunnel_address,
    wireless_tunnel_address,
  } = agentOrPlatform;
  if (isAndroid(platform)) {
    if (wired_iface && !wifi_link_iface) return ["wired"];
    return ["wireless"];
  }
  const hasReportedIfaces = Boolean(wired_iface) || Boolean(wifi_link_iface);
  if (hasReportedIfaces) {
    const out: ("wired" | "wireless")[] = [];
    if (wired_iface && tunnel_state !== "never_connected") out.push("wired");
    if (wifi_link_iface && wireless_tunnel_state !== "never_connected") out.push("wireless");
    if (out.length > 0) return out;
  }
  const hasConnectedTunnel =
    tunnel_state === "connected" || wireless_tunnel_state === "connected";
  if (hasConnectedTunnel) {
    const out: ("wired" | "wireless")[] = [];
    if (tunnel_state !== "never_connected" && (tunnel_address || tunnel_state === "connected")) {
      out.push("wired");
    }
    if (
      wireless_tunnel_state !== "never_connected" &&
      (wireless_tunnel_address || wireless_tunnel_state === "connected")
    ) {
      out.push("wireless");
    }
    if (out.length > 0) return out;
  }
  return ["wired", "wireless"];
}
