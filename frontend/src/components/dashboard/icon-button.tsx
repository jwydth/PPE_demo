import { type LucideIcon } from "lucide-react";

export function IconButton({
  label,
  icon: Icon,
}: {
  label: string;
  icon: LucideIcon;
}) {
  return (
    <button
      className="inline-flex size-10 items-center justify-center rounded-md text-slate-300 transition hover:bg-white/10 hover:text-white focus:outline-none focus:ring-2 focus:ring-lime-200"
      type="button"
      aria-label={label}
      title={label}
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}
