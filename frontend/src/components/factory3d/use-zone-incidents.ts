"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getSafetyEvents } from "@/lib/ppe-api";
import { ViolationReport } from "@/types/detection";
import { ZONES, type ZoneDef, type ZoneId } from "./factory-layout";

export type AnyIncident = Awaited<ReturnType<typeof getSafetyEvents>>[number];

export interface ZoneAggregate {
  zoneId: ZoneId;
  total: number;
  ppeCount: number;
  zoneCount: number;
  fallCount: number;
  /** incident count in the most recent 24h minus the previous 24h */
  trendDelta: number;
  incidents: AnyIncident[];
}

function isPpeIncident(event: AnyIncident): event is ViolationReport {
  return "violation_type" in event;
}

function isBehaviorIncident(event: AnyIncident): boolean {
  return "behavior_type" in event;
}

function isZoneViolation(event: AnyIncident): boolean {
  return !isPpeIncident(event) && !isBehaviorIncident(event);
}

function sourceKeyOf(event: AnyIncident): string | undefined {
  return "video_name" in event && event.video_name ? event.video_name : undefined;
}

// Zone assignment order (single-camera routing):
//  1. Direct zone_id/zone_name match on a ZoneViolation.
//  2. camera_id match (BehaviorIncident) against a ZoneDef's cameraIds.
//  3. video_name/source_key substring match against a ZoneDef's videoNameIncludes.
//  4. Fall back to the single active zone so nothing is lost while only one
//     camera exists (assignUnmatchedToActiveZone, default true).
function resolveZoneId(
  event: AnyIncident,
  zones: ZoneDef[],
  assignUnmatchedToActiveZone: boolean,
): ZoneId | null {
  if (isZoneViolation(event)) {
    const zoneEvent = event as { zone_id?: number; zone_name?: string };
    if (zoneEvent.zone_id != null || zoneEvent.zone_name) {
      const direct = zones.find(
        (z) =>
          z.id === zoneEvent.zone_name ||
          z.label.toLowerCase() === (zoneEvent.zone_name ?? "").toLowerCase(),
      );
      if (direct) return direct.id;
    }
  }

  if (isBehaviorIncident(event)) {
    const cameraId = (event as { camera_id?: number | null }).camera_id;
    if (cameraId != null) {
      const byCamera = zones.find((z) => z.sources.cameraIds?.includes(cameraId));
      if (byCamera) return byCamera.id;
    }
  }

  const sourceKey = sourceKeyOf(event);
  if (sourceKey) {
    const byVideoName = zones.find((z) =>
      z.sources.videoNameIncludes?.some((token) => sourceKey.includes(token)),
    );
    if (byVideoName) return byVideoName.id;
  }

  return assignUnmatchedToActiveZone ? zones.find((z) => z.active)?.id ?? null : null;
}

function computeAggregate(zoneId: ZoneId, incidents: AnyIncident[]): ZoneAggregate {
  const ppeIncidents = incidents.filter(isPpeIncident);
  const zoneIncidents = incidents.filter(isZoneViolation);
  const fallIncidents = incidents.filter(isBehaviorIncident);

  const now = Date.now();
  const oneDayMs = 24 * 60 * 60 * 1000;
  let recent = 0;
  let previous = 0;
  for (const incident of incidents) {
    const age = now - new Date(incident.timestamp).getTime();
    if (age >= 0 && age < oneDayMs) recent += 1;
    else if (age >= oneDayMs && age < 2 * oneDayMs) previous += 1;
  }

  return {
    zoneId,
    total: incidents.length,
    ppeCount: ppeIncidents.length,
    zoneCount: zoneIncidents.length,
    fallCount: fallIncidents.length,
    trendDelta: recent - previous,
    incidents,
  };
}

export function useZoneIncidents(options?: {
  assignUnmatchedToActiveZone?: boolean;
  zones?: ZoneDef[];
}): {
  aggregates: Record<ZoneId, ZoneAggregate>;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const assignUnmatchedToActiveZone = options?.assignUnmatchedToActiveZone ?? true;
  const activeZones = options?.zones ?? ZONES;
  const [events, setEvents] = useState<AnyIncident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setEvents(await getSafetyEvents());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load incidents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const aggregates = useMemo(() => {
    const grouped = new Map<ZoneId, AnyIncident[]>(activeZones.map((z) => [z.id, []]));
    for (const event of events) {
      const zoneId = resolveZoneId(event, activeZones, assignUnmatchedToActiveZone);
      if (zoneId) grouped.get(zoneId)?.push(event);
    }
    return Object.fromEntries(
      activeZones.map((z) => [z.id, computeAggregate(z.id, grouped.get(z.id) ?? [])]),
    ) as Record<ZoneId, ZoneAggregate>;
  }, [events, activeZones, assignUnmatchedToActiveZone]);

  return { aggregates, loading, error, refresh: () => void load() };
}
