export const CATEGORY_ORDER = ["voice", "video", "enterprise"] as const;

export const CATEGORY_NAMES: Record<(typeof CATEGORY_ORDER)[number], string> = {
  voice: "Voice",
  video: "Video",
  enterprise: "Enterprise apps",
};

export const FIELD_LIMITS = {
  report_interval_s: [1, 300],
  max_rtt_ms: [1, 10000],
  max_jitter_ms: [0, 1000],
  max_loss_pct: [0, 100],
  ptime_ms: [10, 200],
  duration_seconds: [5, 3600],
} as const;

export type PresetGroup<T> = {
  category: (typeof CATEGORY_ORDER)[number];
  name: string;
  items: T[];
};

export function groupPresets<T extends { category?: string | null }>(presets: T[]): PresetGroup<T>[] {
  return CATEGORY_ORDER.map((category) => ({
    category,
    name: CATEGORY_NAMES[category],
    items: presets.filter((item) => item.category === category),
  })).filter((group) => group.items.length > 0);
}

/** stored === null means this session has not chosen yet, so every preset starts closed. */
export function initialOpenIds(ids: string[], stored: string[] | null): string[] {
  if (!stored) return [];
  return stored.filter((id) => ids.includes(id));
}

export function toggleOpen(open: string[], id: string): string[] {
  return open.includes(id) ? open.filter((item) => item !== id) : [...open, id];
}

export function expandAll(ids: string[]): string[] {
  return [...ids];
}

export function collapseAll(): string[] {
  return [];
}

export function rangeError(value: number, min: number, max: number): string {
  if (Number.isFinite(value) && value >= min && value <= max) return "";
  return `Enter a number from ${min} to ${max}.`;
}
