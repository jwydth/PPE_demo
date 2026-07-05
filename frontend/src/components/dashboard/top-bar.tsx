import { ChevronDown, Factory } from "lucide-react";
import { appActions, navigation } from "./data";
import { IconButton } from "./icon-button";

export type DashboardView = "feeds" | "violations";

const navViewByLabel: Record<string, DashboardView> = {
  "Camera Feeds": "feeds",
  "Violations Log": "violations",
};

export function TopBar({
  activeView,
  onViewChange,
}: {
  activeView: DashboardView;
  onViewChange: (view: DashboardView) => void;
}) {
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-800 bg-slate-950 px-4 text-white shadow-sm lg:px-6">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-lime-200 text-green-950">
          <Factory className="size-5" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold uppercase tracking-wide text-lime-200">
            De Heus LLC
          </p>
          <h1 className="truncate text-base font-semibold text-slate-50 sm:text-lg">
            Smart Factory Safety Monitoring
          </h1>
        </div>
      </div>

      <nav className="hidden h-full items-center gap-1 lg:flex" aria-label="Main">
        {navigation.map((item) => (
          <button
            key={item.label}
            onClick={() => onViewChange(navViewByLabel[item.label] ?? "feeds")}
            className={`flex h-full items-center gap-2 border-b-2 px-4 text-xs font-semibold uppercase tracking-wide transition ${
              activeView === (navViewByLabel[item.label] ?? "feeds")
                ? "border-lime-200 text-lime-200"
                : "border-transparent text-slate-400 hover:text-slate-100"
            }`}
            type="button"
          >
            <item.icon className="size-4" aria-hidden="true" />
            {item.label}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-2">
        <div className="hidden items-center gap-1 md:flex">
          {appActions.map((action) => (
            <IconButton key={action.label} label={action.label} icon={action.icon} />
          ))}
        </div>
        <button
          className="flex items-center gap-2 rounded-md border border-white/10 bg-white/5 py-1.5 pl-1.5 pr-2 text-sm text-slate-100 transition hover:bg-white/10"
          type="button"
        >
          <span className="flex size-7 items-center justify-center rounded bg-lime-200 text-xs font-bold text-green-950">
            DH
          </span>
          <ChevronDown className="size-4 text-slate-400" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
