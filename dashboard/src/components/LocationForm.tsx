import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import { AnonPlate, AnonText, coordinate, isAnonymized, shownAnon } from "../anonymized";
import { useGuestGate } from "../guestGate";
import { cityPlace, lookupCities, type CityMatch } from "../geocode";
import { regionsFor } from "../regions";
import type { DeviceReport, User } from "../types";
import CityMap from "./CityMap";
import ConfirmDialog from "./ConfirmDialog";
import SelectMenu from "./SelectMenu";

const COUNTRIES = ["United States", "Canada"] as const;

function errorText(error: Error, fallback: string): string {
  try {
    const body = JSON.parse(error.message) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    return error.message || fallback;
  }
  return fallback;
}

export default function LocationForm({ monitorId }: { monitorId: string }) {
  const qc = useQueryClient();
  const run = useGuestGate();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() as Promise<User> });
  const deviceQ = useQuery({
    queryKey: ["monitor-device", monitorId],
    queryFn: () => api.monitorDevice(monitorId) as Promise<DeviceReport>,
  });
  const report = deviceQ.data;
  const [location, setLocation] = useState("");
  const [region, setRegion] = useState("");
  const [country, setCountry] = useState("");
  const [syncedLocation, setSyncedLocation] = useState("");
  const [locationError, setLocationError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<CityMatch[]>([]);
  const [suggestionNote, setSuggestionNote] = useState("");
  const [pendingPlace, setPendingPlace] = useState<CityMatch | null>(null);
  const [checkingLocation, setCheckingLocation] = useState(false);
  const [confirmingLocation, setConfirmingLocation] = useState(false);

  const locationKey = report
    ? [report.location_label ?? "", report.region ?? "", report.country ?? ""].join("|")
    : "";

  useEffect(() => {
    if (!report || syncedLocation === locationKey) return;
    setSyncedLocation(locationKey);
    setLocation(report.location_label ?? "");
    setRegion(report.region ?? "");
    setCountry(report.country ?? "");
  }, [report, locationKey, syncedLocation]);

  const saveLocation = useMutation({
    mutationFn: () => {
      const csrf = me.data?.csrf_token;
      if (!csrf) throw new Error("The session is missing a CSRF token. Reload the page.");
      if (!pendingPlace) {
        return api.updateMonitorLocation(monitorId, {
          location_label: null,
          region: null,
          country: null,
          latitude: null,
          longitude: null,
        }, csrf);
      }
      return api.updateMonitorLocation(monitorId, {
        location_label: pendingPlace.name,
        region: pendingPlace.region || region.trim() || null,
        country: country.trim() || null,
        latitude: pendingPlace.latitude,
        longitude: pendingPlace.longitude,
      }, csrf);
    },
    onSuccess: async () => {
      setLocationError(null);
      setSuggestions([]);
      setSuggestionNote("");
      setPendingPlace(null);
      setConfirmingLocation(false);
      await qc.invalidateQueries({ queryKey: ["monitor-device", monitorId] });
      await qc.invalidateQueries({ queryKey: ["monitor", monitorId] });
      await qc.invalidateQueries({ queryKey: ["monitors"] });
    },
    onError: (error: Error) => {
      setConfirmingLocation(false);
      setLocationError(errorText(error, "Could not save location"));
    },
  });

  function chooseCountry(value: string) {
    setCountry(value);
    if (!regionsFor(value).includes(region)) setRegion("");
  }

  function chooseCity(match: CityMatch) {
    run(() => applyCity(match));
  }

  function applyCity(match: CityMatch) {
    setLocation(match.name);
    if (match.region) setRegion(match.region);
    setPendingPlace(match);
    setSuggestions([]);
    setSuggestionNote("");
    setLocationError(null);
    setConfirmingLocation(true);
  }

  async function submitLocation(event: FormEvent) {
    event.preventDefault();
    run(() => {
      void saveLocationChoice();
    });
  }

  async function saveLocationChoice() {
    const city = location.trim();
    const regionName = region.trim();
    const countryName = country.trim();
    if ((city || regionName) && !countryName) {
      setLocationError("Choose a country.");
      setSuggestions([]);
      return;
    }
    if (city && !regionName) {
      setLocationError("Choose a state or province.");
      setSuggestions([]);
      return;
    }
    if (!city && (regionName || countryName)) {
      setLocationError("Enter a city.");
      setSuggestions([]);
      return;
    }
    setLocationError(null);
    setSuggestions([]);
    setSuggestionNote("");
    if (!city && !regionName && !countryName) {
      setPendingPlace(null);
      setConfirmingLocation(true);
      return;
    }
    setCheckingLocation(true);
    try {
      const matches = await lookupCities(city, countryName, regionName);
      const exact = matches.filter((item) => item.name.toLowerCase() === city.toLowerCase());
      if (exact.length === 1) {
        setPendingPlace(exact[0]);
        setConfirmingLocation(true);
        return;
      }
      const options = exact.length > 1 ? exact : matches.slice(0, 5);
      if (options.length === 0) {
        setLocationError(`No city named “${city}” in ${regionName}.`);
        return;
      }
      setSuggestions(options);
      setSuggestionNote(
        exact.length > 1
          ? `More than one city is named “${city}”.`
          : `“${city}” was not found. Choose a suggestion.`,
      );
    } catch (error) {
      setLocationError(error instanceof Error ? error.message : "Could not look up that city.");
    } finally {
      setCheckingLocation(false);
    }
  }

  if (deviceQ.isLoading) return <p className="muted">Loading location…</p>;
  if (!report) return <p className="muted">Could not load location.</p>;

  const latitude = coordinate(report.latitude);
  const longitude = coordinate(report.longitude);
  const savedPin = latitude != null && longitude != null ? { latitude, longitude } : null;
  const anonymized =
    isAnonymized(report.location_label) ||
    isAnonymized(report.region) ||
    isAnonymized(report.country) ||
    isAnonymized(report.latitude) ||
    isAnonymized(report.longitude);

  if (anonymized) {
    return (
      <div className="device-form">
        <div className="field">
          <label>Country</label>
          <AnonText value={report.country} />
        </div>
        <div className="field">
          <label>State/Province</label>
          <AnonText value={report.region} />
        </div>
        <div className="field">
          <label>City</label>
          <AnonText value={report.location_label} />
        </div>
        {savedPin ? (
          <CityMap
            className="location-map"
            latitude={savedPin.latitude}
            longitude={savedPin.longitude}
            label={[report.region, report.country].filter(Boolean).join(", ") || report.name}
            zoomControl={false}
          />
        ) : (
          <AnonPlate
            value={[report.latitude, report.longitude].filter(isAnonymized).map((item) => shownAnon(String(item))).join("  ") || undefined}
          />
        )}
      </div>
    );
  }
  const locationDirty =
    syncedLocation === locationKey &&
    (location.trim() !== (report.location_label ?? "") ||
      region !== (report.region ?? "") ||
      country !== (report.country ?? ""));

  return (
    <>
      <form className="device-form" onSubmit={submitLocation}>
        <div className="field">
          <label htmlFor="agent-country">Country</label>
          <SelectMenu id="agent-country" value={country} onChange={(event) => chooseCountry(event.target.value)}>
            <option value="">Select</option>
            {COUNTRIES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </SelectMenu>
        </div>
        <div className="field field-region">
          <label htmlFor="agent-region">State/Province</label>
          <SelectMenu id="agent-region" value={region} onChange={(event) => setRegion(event.target.value)}>
            <option value="">Select</option>
            {regionsFor(country).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </SelectMenu>
        </div>
        <div className="field">
          <label htmlFor="agent-location">City</label>
          <input
            id="agent-location"
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder="City"
            autoComplete="address-level2"
          />
        </div>
        <div className="form-actions">
          <button
            className={`btn btn-primary${locationDirty ? " is-dirty" : ""}`}
            type="submit"
            disabled={saveLocation.isPending || checkingLocation}
          >
            {checkingLocation ? "Checking…" : "Save location"}
          </button>
        </div>
        {savedPin ? (
          <CityMap
            className="location-map"
            latitude={savedPin.latitude}
            longitude={savedPin.longitude}
            label={[report.location_label, report.region, report.country].filter(Boolean).join(", ") || report.name}
            zoomControl={false}
          />
        ) : null}
        {suggestions.length > 0 ? (
          <div className="city-suggestions">
            <p>{suggestionNote}</p>
            {suggestions.map((item) => (
              <button key={`${item.name}|${item.region}`} className="btn" type="button" onClick={() => chooseCity(item)}>
                {cityPlace(item)}
              </button>
            ))}
          </div>
        ) : null}
        {locationError ? <div className="error">{locationError}</div> : null}
      </form>
      {confirmingLocation ? (
        <ConfirmDialog
          title="Save location"
          confirmLabel="Save"
          busy={saveLocation.isPending}
          onConfirm={() => saveLocation.mutate()}
          onCancel={() => {
            if (!saveLocation.isPending) setConfirmingLocation(false);
          }}
        >
          {pendingPlace
            ? `Save ${cityPlace(pendingPlace)} for “${report.name}”?`
            : `Clear the location for “${report.name}”?`}
        </ConfirmDialog>
      ) : null}
    </>
  );
}
