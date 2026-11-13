import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { chartTicks, chartWindow, formatChartTick, formatChartTime, tickStep } from "./src/chartTime.ts";

const HOUR = 60 * 60 * 1000;
const RANGES = ["1h", "3h", "6h", "8h", "12h", "24h", "7d", "30d"];

describe("chart time axis", () => {
  it("never prints the same label twice on any range, at any minute of the day", () => {
    const base = new Date(2026, 10, 13, 0, 0, 0).getTime();
    for (const range of RANGES) {
      for (let minute = 0; minute < 24 * 60; minute += 7) {
        const domain = chartWindow(range, base + minute * 60 * 1000);
        const ticks = chartTicks(domain) ?? [];
        const labels = ticks.map((tick) => formatChartTick(tick, domain[1] - domain[0]));
        assert.equal(new Set(labels).size, labels.length, `${range} at minute ${minute}: ${labels.join(" | ")}`);
      }
    }
  });

  it("puts between 3 and 8 ticks inside every range", () => {
    for (const range of RANGES) {
      const domain = chartWindow(range, new Date(2026, 10, 13, 14, 42).getTime());
      const ticks = chartTicks(domain) ?? [];
      assert.ok(ticks.length >= 3 && ticks.length <= 8, `${range}: ${ticks.length} ticks`);
      for (const tick of ticks) assert.ok(tick >= domain[0] && tick <= domain[1], `${range}: tick outside the window`);
    }
  });

  it("spaces a 6-hour window one hour apart on whole hours", () => {
    const domain = chartWindow("6h", new Date(2026, 10, 13, 14, 42).getTime());
    assert.equal(tickStep(domain[1] - domain[0]), HOUR);
    for (const tick of chartTicks(domain) ?? []) {
      const date = new Date(tick);
      assert.equal(date.getMinutes(), 0);
    }
  });

  it("keeps hour ticks on whole local hours through a daylight-saving change", () => {
    const end = new Date(2026, 10, 1, 12, 0).getTime();
    for (const tick of chartTicks([end - 24 * HOUR, end]) ?? []) assert.equal(new Date(tick).getMinutes(), 0);
  });

  it("shows the minute in a tooltip", () => {
    const moment = new Date(2026, 10, 13, 14, 37).getTime();
    assert.match(formatChartTime(moment), /37/);
  });
});
