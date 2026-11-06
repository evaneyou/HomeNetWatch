import type { ReactNode } from "react";

/** A word joiner the server puts in front of a value the dashboard should blur. */
export const BLUR_MARK = "\u2060";

/** A guest value the server marked for blurring, or an older value made of #. */
export function isAnonymized(value: unknown): boolean {
  if (typeof value !== "string") return false;
  if (value.startsWith(BLUR_MARK)) return true;
  return value.includes("#") && !/[A-Za-z0-9]/.test(value);
}

/** The text to show inside a blur. Older hashed values stay as they are. */
export function shownAnon(value: string): string {
  return value.startsWith(BLUR_MARK) ? value.slice(BLUR_MARK.length) : value;
}

export function coordinate(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Join place parts. A marked city stays marked so it can be blurred on its own. */
export function joinAnon(parts: Array<string | null | undefined>, separator = ", "): string {
  return parts.filter((part): part is string => Boolean(part && part.trim())).join(separator);
}

export function stableId(row: { anon_key?: string | null }, fallback: string): string {
  return row.anon_key || fallback;
}

export function AnonText({
  value,
  fallback = "—",
  className,
}: {
  value: string | null | undefined;
  fallback?: string;
  className?: string;
}): ReactNode {
  if (!value) return <span className={className}>{fallback}</span>;
  if (isAnonymized(value)) {
    return (
      <span className={className ? `anon-mark ${className}` : "anon-mark"} aria-hidden="true">
        {shownAnon(value)}
      </span>
    );
  }
  return <span className={className}>{value}</span>;
}

/** City blurred, state and country readable. */
export function PlaceText({
  city,
  region,
  country,
  fallback = "No location saved",
}: {
  city?: string | null;
  region?: string | null;
  country?: string | null;
  fallback?: string;
}): ReactNode {
  const parts = [city, region, country].map((part) => part?.trim() || "").filter(Boolean);
  if (parts.length === 0) return <span>{fallback}</span>;
  return (
    <span>
      {parts.map((part, index) => (
        <span key={`${index}-${shownAnon(part)}`}>
          {index > 0 ? ", " : null}
          {isAnonymized(part) ? (
            <span className="anon-mark" aria-hidden="true">
              {shownAnon(part)}
            </span>
          ) : (
            part
          )}
        </span>
      ))}
    </span>
  );
}

export function AnonPlate({ value }: { value?: string | null }) {
  return (
    <div className="anon-plate" aria-label="Anonymized">
      <span className="anon-mark" aria-hidden="true">
        {value ? shownAnon(value) : "Hidden"}
      </span>
    </div>
  );
}
