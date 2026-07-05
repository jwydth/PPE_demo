import { useZoneDrawing } from "@/hooks/useZoneDrawing";
import { ZoneType } from "@/types/zone";

export function ZoneOverlaySvg({
  zoneDrawing,
  zoneColors,
}: {
  zoneDrawing: ReturnType<typeof useZoneDrawing>;
  zoneColors: Record<ZoneType, string>;
}) {
  return (
    <svg
      onClick={zoneDrawing.handleSurfaceClick}
      className={`absolute inset-0 h-full w-full ${
        zoneDrawing.isDrawing ? "pointer-events-auto" : "pointer-events-none"
      }`}
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
    >
      {zoneDrawing.displayedZones.map((zone) => {
        const pathData = zone.points.length > 0
          ? `M ${zone.points[0].x} ${zone.points[0].y} ` +
            zone.points.map((p, i) => {
              const nextP = zone.points[(i + 1) % zone.points.length];
              if (p.curveControl) {
                return `Q ${p.curveControl.x} ${p.curveControl.y}, ${nextP.x} ${nextP.y}`;
              }
              return `L ${nextP.x} ${nextP.y}`;
            }).join(" ") + " Z"
          : "";

        return (
          <g key={zone.id}>
            <path
              d={pathData}
              fill={`${zoneColors[zone.type]}33`}
              stroke={zoneDrawing.selectedZoneId === zone.id ? "#bef264" : zoneColors[zone.type]}
              strokeWidth={zoneDrawing.selectedZoneId === zone.id ? 0.008 : 0.004}
              className={
                zoneDrawing.configMode === "modify" && zoneDrawing.isDrawing
                  ? "cursor-move pointer-events-auto"
                  : "pointer-events-none"
              }
              onMouseDown={(e) => {
                if (zone.id !== "draft") zoneDrawing.onZoneMouseDown(e, zone.id);
              }}
            />
            {zoneDrawing.selectedZoneId === zone.id &&
              zoneDrawing.isDrawing &&
              zone.points.map((p1, idx) => {
                const p2 = zone.points[(idx + 1) % zone.points.length];
                return (
                  <g key={`${zone.id}-edge-group-${idx}`}>
                    {p1.curveControl ? (
                      <path
                        d={`M ${p1.x} ${p1.y} Q ${p1.curveControl.x} ${p1.curveControl.y}, ${p2.x} ${p2.y}`}
                        fill="none"
                        stroke="transparent"
                        strokeWidth={0.04}
                        className="cursor-move pointer-events-auto"
                        onMouseDown={(e) => zoneDrawing.onCurveEdgeMouseDown(e, zone.id, idx)}
                      />
                    ) : (
                      <line
                        x1={p1.x}
                        y1={p1.y}
                        x2={p2.x}
                        y2={p2.y}
                        stroke="transparent"
                        strokeWidth={0.04}
                        className={
                          zoneDrawing.isAddingPoint || zoneDrawing.isDrawingCurve
                            ? "cursor-crosshair pointer-events-auto"
                            : "pointer-events-none"
                        }
                        onClick={(e) => {
                          if (zoneDrawing.isAddingPoint) zoneDrawing.onEdgeClick(e, zone.id, idx);
                        }}
                        onMouseDown={(e) => {
                          if (zoneDrawing.isDrawingCurve) zoneDrawing.onCurveEdgeMouseDown(e, zone.id, idx);
                        }}
                      />
                    )}
                    {p1.curveControl && (
                      <circle
                        cx={p1.curveControl.x}
                        cy={p1.curveControl.y}
                        r={0.006}
                        fill="#fef08a"
                        className="cursor-move pointer-events-auto"
                        onMouseDown={(e) => zoneDrawing.onCurveEdgeMouseDown(e, zone.id, idx)}
                      />
                    )}
                  </g>
                );
              })}
            {zoneDrawing.selectedZoneId === zone.id &&
              zoneDrawing.isDrawing &&
              zone.points.map((point, idx) => (
                <circle
                  key={`${zone.id}-pt-${idx}`}
                  cx={point.x}
                  cy={point.y}
                  r={0.012}
                  fill="#bef264"
                  className="cursor-pointer pointer-events-auto"
                  onMouseDown={(e) => zoneDrawing.onPointMouseDown(e, zone.id, idx)}
                />
              ))}
          </g>
        );
      })}
      {zoneDrawing.draftPoints.map((point, index) => (
        <circle
          key={`${point.x}-${point.y}-${index}`}
          cx={point.x}
          cy={point.y}
          r={0.008}
          fill="#bef264"
        />
      ))}
    </svg>
  );
}
