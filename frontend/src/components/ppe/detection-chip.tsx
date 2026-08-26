import type { LucideIcon } from "lucide-react";

/**
 * Shared visual language for every prompt the vision model surfaces
 * (a sign it read, a PPE recommendation): one tone, one button language.
 * See DESIGN_TOKENS.md §4.
 */
export interface ChipAction {
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  primary?: boolean;
}

const PRIMARY_ACTION_CLASS =
  "inline-flex items-center gap-1 rounded bg-sky-400 px-2 py-1 text-xs font-semibold text-slate-950 transition hover:bg-sky-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300";
const SECONDARY_ACTION_CLASS =
  "inline-flex items-center gap-1 rounded border border-slate-600 px-2 py-1 text-xs font-semibold text-slate-300 transition hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300";

export function ChipActionButton({ action }: { action: ChipAction }) {
  const Icon = action.icon;
  return (
    <button
      type="button"
      onClick={action.onClick}
      className={action.primary ? PRIMARY_ACTION_CLASS : SECONDARY_ACTION_CLASS}
    >
      <Icon className="size-3 shrink-0" aria-hidden="true" />
      {action.label}
    </button>
  );
}

export function DetectionChip({
  icon: Icon,
  title,
  children,
  actions,
  className = "",
}: {
  icon: LucideIcon;
  title: string;
  children?: React.ReactNode;
  actions: ChipAction[];
  className?: string;
}) {
  return (
    <div
      className={`detection-chip-enter pointer-events-auto flex flex-col gap-1.5 rounded-md border border-sky-400/50 bg-slate-950/95 px-2.5 py-2 text-xs shadow-lg shadow-black/40 ${className}`}
    >
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-sky-300" aria-hidden="true" />
        <span className="font-semibold text-white">{title}</span>
      </div>
      {children}
      <div className="flex flex-wrap gap-1.5">
        {actions.map((action) => (
          <ChipActionButton key={action.label} action={action} />
        ))}
      </div>
    </div>
  );
}
