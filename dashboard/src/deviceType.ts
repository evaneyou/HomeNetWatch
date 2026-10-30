/**
 * What kind of remote agent a device is. The device says so in its signed check-in, so the dashboard never asks.
 * An Android agent (an NVIDIA Shield or another Android TV) only runs the Wi-Fi voice stream and the Speed Test,
 * so its pages leave out the Pi's wired link, internet checks and device vitals.
 */
export type Platform = "pi" | "android";

export function isAndroid(platform: string | null | undefined): boolean {
  return platform === "android";
}

export function deviceTypeLabel(platform: string | null | undefined): string {
  return isAndroid(platform) ? "Android TV" : "Raspberry Pi";
}
