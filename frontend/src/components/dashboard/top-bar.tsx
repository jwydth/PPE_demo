import { Factory, type LucideIcon } from "lucide-react";
import { navigation } from "./data";

export type DashboardView = "feeds" | "violations" | "factory3d" | "analytics";

const navViewByLabel: Record<string, DashboardView> = {
  "Camera Feeds": "feeds",
  "Live Incident Panel": "violations",
  "3D Map": "factory3d",
  "Analytics": "analytics",
};

export function TopBar({
  activeView,
  onViewChange,
}: {
  activeView: DashboardView;
  onViewChange: (view: DashboardView) => void;
}) {
  const tabs = navigation.map((item) => ({
    label: item.label,
    icon: item.icon,
    view: navViewByLabel[item.label] ?? "feeds",
  }));

  return (
    <header className="sticky top-0 z-30 border-b border-slate-800 bg-slate-950 text-white shadow-sm">
      <div className="flex h-16 items-center gap-4 px-4 lg:px-6">
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

        {/* The tabs sit next to the brand rather than floating in the middle:
            with the three dead action buttons gone, `justify-between` would
            have parked them at an arbitrary point that moves with the width of
            whatever ends up on the right. */}
        <span className="hidden h-8 w-px shrink-0 bg-slate-800 lg:block" aria-hidden="true" />

        <nav className="hidden h-full min-w-0 flex-1 items-center gap-1 lg:flex" aria-label="Main">
          {tabs.map((tab) => (
            <NavTab
              key={tab.label}
              label={tab.label}
              icon={tab.icon}
              isActive={activeView === tab.view}
              onClick={() => onViewChange(tab.view)}
              className="h-full"
            />
          ))}
        </nav>

        {/* Identity only. This was a menu button with a chevron and no menu
            behind it; the chevron promised something that never opened. */}
        <div className="ml-auto flex shrink-0 items-center gap-2 rounded-md border border-white/10 bg-white/5 py-1.5 pl-1.5 pr-2.5">
          <span
            className="flex size-7 items-center justify-center rounded bg-lime-200 text-xs font-bold text-green-950"
            aria-hidden="true"
          >
            DH
          </span>
          <span className="hidden text-sm font-medium text-slate-300 sm:inline">
            Safety team
          </span>
        </div>
      </div>

      {/* Below lg the inline nav used to disappear entirely, leaving no way to
          change view on a tablet or phone. It becomes a scrollable strip under
          the brand instead — labels kept, since an icon-only nav is a guessing
          game. */}
      <nav
        className="flex overflow-x-auto border-t border-slate-800 lg:hidden"
        aria-label="Main"
      >
        {tabs.map((tab) => (
          <NavTab
            key={tab.label}
            label={tab.label}
            icon={tab.icon}
            isActive={activeView === tab.view}
            onClick={() => onViewChange(tab.view)}
            className="py-3.5"
          />
        ))}
      </nav>
    </header>
  );
}

function NavTab({
  label,
  icon: Icon,
  isActive,
  onClick,
  className,
}: {
  label: string;
  icon: LucideIcon;
  isActive: boolean;
  onClick: () => void;
  className: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      // aria-current, not colour alone: the active tab was signalled only by
      // lime text plus an underline, which a screen reader never sees.
      aria-current={isActive ? "page" : undefined}
      className={`flex shrink-0 cursor-pointer items-center gap-2 whitespace-nowrap border-b-2 px-4 text-xs font-semibold uppercase tracking-wide transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-lime-200 ${
        isActive
          ? "border-lime-200 text-lime-200"
          : "border-transparent text-slate-400 hover:border-slate-700 hover:text-slate-100"
      } ${className}`}
    >
      <Icon className="size-4" aria-hidden="true" />
      {label}
    </button>
  );
}
