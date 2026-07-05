import { Shield } from "lucide-react";
import { zones } from "./data";
import { ZoneButton } from "./zone-button";

export function ZoneSidebar() {
  return (
    <aside className="border-b border-slate-200 bg-slate-50 lg:sticky lg:top-16 lg:h-[calc(100vh-4rem)] lg:w-72 lg:shrink-0 lg:border-b-0 lg:border-r">
      <div className="flex h-full flex-col gap-4 p-4">
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex items-center gap-3">
            <div className="flex size-11 items-center justify-center rounded-md bg-green-950 text-lime-200">
              <Shield className="size-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-lg font-semibold text-slate-950">Factory Zones</p>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Live access
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-2">
          {zones.map((zone) => (
            <ZoneButton key={zone.name} zone={zone} active={zone.name === "Packaging Line 1"} />
          ))}
        </div>
      </div>
    </aside>
  );
}
