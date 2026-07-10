"use client";

import { useEffect, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { RefreshCw } from "lucide-react";
import {
  GATES,
  RESTRICTED_ZONE,
  UTILITY_COLUMN,
  UTILITY_ROOMS,
  WALLS,
  ZONES,
  type ZoneId,
} from "./factory-layout";
import { ZoneBlock } from "./zone-block";
import { ZoneDetailPanel } from "./zone-detail-panel";
import { useZoneIncidents } from "./use-zone-incidents";

const REFRESH_INTERVAL_MS = 20_000;

function FloorPad() {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[1, -0.01, 0]}>
      <planeGeometry args={[14, 8]} />
      <meshStandardMaterial color="#1e293b" />
    </mesh>
  );
}

function BuildingShell() {
  return (
    <>
      {WALLS.map((wall, index) => (
        <mesh key={`wall-${index}`} position={wall.position}>
          <boxGeometry args={wall.size} />
          <meshStandardMaterial color="#334155" />
        </mesh>
      ))}
      {GATES.map((gate, index) => (
        <mesh key={`gate-${index}`} position={gate.position}>
          <boxGeometry args={gate.size} />
          <meshStandardMaterial color="#facc15" />
        </mesh>
      ))}
      <mesh position={UTILITY_COLUMN.position}>
        <boxGeometry args={UTILITY_COLUMN.size} />
        <meshStandardMaterial color="#475569" />
      </mesh>
      {UTILITY_ROOMS.map((room, index) => (
        <mesh
          key={`utility-room-${index}`}
          position={[UTILITY_COLUMN.position[0], UTILITY_COLUMN.position[1] + 0.21, room.centerZ]}
        >
          <boxGeometry args={[UTILITY_COLUMN.size[0] - 0.15, 0.02, room.depthZ - 0.1]} />
          <meshStandardMaterial color="#0f172a" />
        </mesh>
      ))}
      <mesh position={RESTRICTED_ZONE.position}>
        <boxGeometry args={RESTRICTED_ZONE.size} />
        <meshStandardMaterial color="#f97316" transparent opacity={0.5} />
      </mesh>
    </>
  );
}

export function Factory3DView() {
  const { aggregates, loading, error, refresh } = useZoneIncidents();
  const [selectedZoneId, setSelectedZoneId] = useState<ZoneId | null>(
    () => ZONES.find((z) => z.active)?.id ?? null,
  );
  const [hoveredZoneId, setHoveredZoneId] = useState<ZoneId | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => refresh(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const selectedZone = selectedZoneId ? ZONES.find((z) => z.id === selectedZoneId) ?? null : null;
  const selectedAggregate = selectedZoneId ? aggregates[selectedZoneId] ?? null : null;

  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">Factory 3D Map</h2>
          <p className="text-xs text-slate-600">
            Click a zone to filter the incident log. Zones without an active camera are dimmed.
          </p>
        </div>
        <button
          type="button"
          onClick={() => refresh()}
          className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Refresh
        </button>
      </div>

      {/* Non-3D DOM fallback so the feature is usable without WebGL / with a screen reader. */}
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Factory zones">
        {ZONES.map((zone) => {
          const total = aggregates[zone.id]?.total ?? 0;
          return (
            <button
              key={zone.id}
              type="button"
              role="tab"
              aria-selected={selectedZoneId === zone.id}
              disabled={!zone.active}
              title={zone.active ? undefined : "Camera not connected — monitoring planned"}
              onClick={() => zone.active && setSelectedZoneId(zone.id)}
              className={`rounded-md border px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                selectedZoneId === zone.id
                  ? "border-lime-300 bg-lime-100 text-green-950"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
              }`}
            >
              {zone.id} · {zone.label}
              {zone.active ? ` (${total})` : " — offline"}
            </button>
          );
        })}
      </div>

      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm font-medium text-red-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_360px]">
        <div className="min-h-[420px] overflow-hidden rounded-md border border-slate-300 bg-slate-950 shadow-md">
          <Canvas camera={{ position: [6, 9, 11], fov: 45 }}>
            <ambientLight intensity={0.85} />
            <directionalLight position={[6, 12, 8]} intensity={0.55} />
            <FloorPad />
            <BuildingShell />
            {ZONES.map((zone) => (
              <ZoneBlock
                key={zone.id}
                zone={zone}
                aggregate={
                  aggregates[zone.id] ?? {
                    zoneId: zone.id,
                    total: 0,
                    ppeCount: 0,
                    zoneCount: 0,
                    fallCount: 0,
                    insight: "No incidents recorded.",
                    trendDelta: 0,
                    incidents: [],
                  }
                }
                selected={selectedZoneId === zone.id}
                hovered={hoveredZoneId === zone.id}
                onHover={setHoveredZoneId}
                onSelect={setSelectedZoneId}
              />
            ))}
            <OrbitControls
              enablePan={false}
              minPolarAngle={0.3}
              maxPolarAngle={1.4}
              minDistance={9}
              maxDistance={28}
              target={[0.6, 0, 0]}
            />
          </Canvas>
        </div>

        <ZoneDetailPanel zone={selectedZone} aggregate={selectedAggregate} loading={loading} />
      </div>
    </section>
  );
}
