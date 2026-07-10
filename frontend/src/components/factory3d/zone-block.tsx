"use client";

import { useMemo } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { Html, RoundedBox, Text } from "@react-three/drei";
import { Color } from "three";
import type { ZoneDef, ZoneId } from "./factory-layout";
import type { ZoneAggregate } from "./use-zone-incidents";

const WARNING_COLOR = new Color("#ef4444");
const INACTIVE_COLOR = new Color("#64748b");

interface ZoneBlockProps {
  zone: ZoneDef;
  aggregate: ZoneAggregate;
  selected: boolean;
  hovered: boolean;
  onHover: (zoneId: ZoneId | null) => void;
  onSelect: (zoneId: ZoneId) => void;
}

export function ZoneBlock({ zone, aggregate, selected, hovered, onHover, onSelect }: ZoneBlockProps) {
  const heatColor = useMemo(() => {
    if (!zone.active) return INACTIVE_COLOR;
    const base = new Color(zone.color);
    const t = Math.min(aggregate.total / 15, 1);
    return base.clone().lerp(WARNING_COLOR, t);
  }, [zone.active, zone.color, aggregate.total]);

  const baseY = 0.25;
  const liftY = zone.active && hovered ? baseY + 0.08 : baseY;
  const opacity = zone.active ? (selected ? 1 : 0.9) : 0.35;

  const tooltipText = zone.active
    ? `${zone.label} — ${aggregate.total} incident${aggregate.total === 1 ? "" : "s"}\n${aggregate.insight}`
    : "Camera not connected — monitoring planned";

  const handlePointerOver = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    onHover(zone.id);
  };
  const handlePointerOut = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    onHover(null);
  };
  const handleClick = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    if (zone.active) onSelect(zone.id);
  };

  return (
    <group
      position={[zone.position[0], 0, zone.position[1]]}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
      onClick={handleClick}
    >
      <RoundedBox
        args={[zone.size[0], 0.5, zone.size[1]]}
        radius={0.12}
        smoothness={4}
        position={[0, liftY, 0]}
      >
        <meshStandardMaterial
          color={heatColor}
          transparent
          opacity={opacity}
          emissive={selected ? heatColor : "#000000"}
          emissiveIntensity={selected ? 0.35 : 0}
        />
      </RoundedBox>
      <Text
        position={[0, liftY + 0.35, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        fontSize={0.4}
        color="#f8fafc"
        anchorX="center"
        anchorY="middle"
      >
        {`${zone.id} · ${zone.label}`}
      </Text>
      {hovered ? (
        <Html position={[0, liftY + 0.7, 0]} center distanceFactor={12} zIndexRange={[100, 0]} occlude={false}>
          <div className="pointer-events-none w-max max-w-56 whitespace-pre-line rounded-md border border-slate-700 bg-slate-950/95 px-2.5 py-1.5 text-xs text-slate-100 shadow-lg">
            {tooltipText}
          </div>
        </Html>
      ) : null}
    </group>
  );
}
