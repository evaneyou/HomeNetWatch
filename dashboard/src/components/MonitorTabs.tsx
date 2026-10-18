import { networkLabel, networkOrder } from "../labels";
import type { MonitorDetail } from "../types";

type Network = { id: string; slug: string; name: string };

/**
 * The networks a monitor has checks on, other than the two links, in tab order. Wired and Wi-Fi get
 * their own tabs, which sit after the WAN one, so they are not in this list.
 */
export function monitorNetworks(tests: MonitorDetail["tests"] | undefined): Network[] {
  const map = new Map<string, Network>();
  for (const test of tests ?? []) {
    map.set(test.network_id, { id: test.network_id, slug: test.network_slug, name: test.network_name });
  }
  return [...map.values()]
    .filter((network) => network.slug !== "wired" && network.slug !== "wifi")
    .sort((a, b) => networkOrder(a) - networkOrder(b));
}

export type TabParams = Record<string, string>;

/**
 * The tab bar at the top of a monitor's pages: Web, Wired, Wi-Fi, and Internet Connection while that
 * page, or the diagram under it, is open. It only says where to go; each page decides how, so the
 * monitor page changes its own URL and the diagram page navigates to the monitor page.
 */
export default function MonitorTabs({
  tests,
  activeTab,
  onWired,
  onWireless,
  onApps = false,
  onConnection,
  link,
  onSelect,
}: {
  tests: MonitorDetail["tests"] | undefined;
  /** The network whose tab is lit when neither link nor the connection page is. */
  activeTab: string | null;
  onWired: boolean;
  onWireless: boolean;
  onApps?: boolean;
  onConnection: boolean;
  link: string | null;
  onSelect: (params: TabParams) => void;
}) {
  const networks = monitorNetworks(tests);
  const found = tests ?? [];
  const wiredId = found.find((test) => test.network_slug === "wired")?.network_id ?? null;
  const wirelessId = found.find((test) => test.network_slug === "wifi")?.network_id ?? null;

  return (
    <div className="tabs">
      {networks.flatMap((network) => {
        const buttons = [
          <button
            key={network.id}
            type="button"
            className={`tab ${!onWired && !onWireless && !onApps && !onConnection && activeTab === network.id ? "active" : ""}`}
            onClick={() => onSelect({ network: network.id })}
          >
            {networkLabel(network)}
          </button>,
        ];
        if (network.slug === "wan") {
          buttons.push(
            <button
              key="apps"
              type="button"
              className={`tab ${onApps ? "active" : ""}`}
              onClick={() => onSelect({ detail: "apps" })}
            >
              Apps
            </button>,
          );
        }
        if (network.slug === "wan" && wiredId) {
          buttons.push(
            <button
              key="wired"
              type="button"
              className={`tab ${onWired ? "active" : ""}`}
              onClick={() => onSelect({ network: wiredId, detail: "isp", link: "wired" })}
            >
              Wired
            </button>,
          );
        }
        if (network.slug === "wan" && wirelessId) {
          buttons.push(
            <button
              key="wireless"
              type="button"
              className={`tab ${onWireless ? "active" : ""}`}
              onClick={() => onSelect({ network: wirelessId, detail: "isp", link: "wireless" })}
            >
              Wi-Fi
            </button>,
          );
        }
        if (network.slug === "wan" && onConnection && (link === "wired" || link === "wireless")) {
          buttons.push(
            <button
              key="connection"
              type="button"
              className="tab active"
              onClick={() =>
                onSelect({ network: network.id, detail: "connection", link: link === "wireless" ? "wireless" : "wired" })
              }
            >
              Internet Connection
            </button>,
          );
        }
        return buttons;
      })}
    </div>
  );
}
