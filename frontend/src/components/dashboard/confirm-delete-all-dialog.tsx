import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useModalDismiss } from "@/hooks/useModalDismiss";

/** Typed into the field to arm the delete. Deliberately not localized —
 * matching a fixed literal is what makes the action deliberate. */
const CONFIRM_WORD = "DELETE";

/** Pinned to the UI's own language rather than the visitor's locale. On a
 * Vietnamese machine `toLocaleString()` renders 1251 as "1.251", which in an
 * otherwise-English dialog reads as a decimal — a bad way to state how many
 * records are about to be destroyed. */
const countFormat = new Intl.NumberFormat("en-US");

/**
 * Confirmation for "delete every logged incident".
 *
 * This replaced a bare `window.confirm`, which gave the most destructive
 * action in the product less ceremony than a form submit: it never said how
 * many records were about to go, and the browser puts its default button
 * under the cursor — so a mis-aimed double-click on the neighbouring Refresh
 * button was two clicks from an empty incident store. These records are the
 * compliance evidence trail, so the count is stated and the word must be
 * typed out.
 */
export function ConfirmDeleteAllDialog({
  count,
  deleting,
  onConfirm,
  onCancel,
}: {
  count: number;
  deleting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const armed = typed.trim().toUpperCase() === CONFIRM_WORD && !deleting;
  // Escape, focus trap, and focus restore — disabled mid-delete so the dialog
  // can't be dismissed out from under a request already in flight.
  const dialogRef = useModalDismiss<HTMLDivElement>({
    onDismiss: onCancel,
    enabled: !deleting,
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4"
      // Backdrop click is intentionally NOT wired to close: this dialog is a
      // stop sign, and a stray click behind it shouldn't dismiss the guard.
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-all-title"
      aria-describedby="delete-all-body"
    >
      <div
        ref={dialogRef}
        className="w-full max-w-md rounded-md border border-slate-200 bg-white shadow-xl"
      >
        <div className="flex items-start gap-3 border-b border-slate-200 px-4 py-3">
          <span className="mt-0.5 rounded-md bg-red-50 p-1.5 text-red-700 ring-1 ring-red-200">
            <AlertTriangle className="size-4" aria-hidden="true" />
          </span>
          <div>
            <h2 id="delete-all-title" className="text-sm font-semibold text-slate-950">
              Delete {countFormat.format(count)} incident{count === 1 ? "" : "s"}?
            </h2>
            <p id="delete-all-body" className="mt-1 text-xs text-slate-600">
              This permanently removes every logged PPE, zone, and behavior incident,
              along with their snapshots. It cannot be undone, and these records are
              your compliance history.
            </p>
          </div>
        </div>

        <div className="px-4 py-3">
          <label
            htmlFor="delete-all-confirm"
            className="block text-xs font-medium text-slate-700"
          >
            Type <span className="font-semibold text-slate-950">{CONFIRM_WORD}</span> to confirm
          </label>
          <input
            id="delete-all-confirm"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && armed) onConfirm();
            }}
            disabled={deleting}
            autoComplete="off"
            spellCheck={false}
            className="mt-1.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 shadow-sm transition-colors focus:border-slate-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400 disabled:bg-slate-50"
          />
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!armed}
            className="rounded-md border border-red-600 bg-red-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
          >
            {deleting ? "Deleting…" : `Delete ${countFormat.format(count)}`}
          </button>
        </div>
      </div>
    </div>
  );
}
