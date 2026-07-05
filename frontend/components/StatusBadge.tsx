interface StatusBadgeProps {
  violation: boolean;
}

export function StatusBadge({ violation }: StatusBadgeProps) {
  if (violation) {
    return (
      <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-full px-4 py-2 w-fit">
        <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
        <span className="font-mono text-sm font-bold text-red-400 tracking-widest">
          VIOLATION DETECTED
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/30 rounded-full px-4 py-2 w-fit">
      <span className="w-2 h-2 rounded-full bg-green-500" />
      <span className="font-mono text-sm font-bold text-green-400 tracking-widest">
        COMPLIANT
      </span>
    </div>
  );
}
