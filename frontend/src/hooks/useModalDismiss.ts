import { useEffect, useRef } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * The keyboard contract every modal in the app owes its users: Escape closes
 * it, focus starts inside it, Tab cannot walk out of it, and focus returns to
 * whatever opened it.
 *
 * Written once because the two dialogs here kept getting it half-right —
 * `role="dialog"` and `aria-modal` were set, but with no Escape handler and no
 * focus management the only way out was clicking the backdrop, which strands
 * anyone working by keyboard while Tab quietly walks the page behind the
 * overlay.
 *
 * Returns a ref to attach to the dialog's container element.
 */
export function useModalDismiss<T extends HTMLElement>({
  onDismiss,
  enabled = true,
}: {
  onDismiss: () => void;
  /** Set false to hold the modal open — e.g. while a delete is in flight. */
  enabled?: boolean;
}) {
  const containerRef = useRef<T>(null);
  // Kept in a ref so changing the handler doesn't tear down the listener and
  // lose the captured `previouslyFocused` element mid-interaction. Written in
  // an effect rather than during render — a ref write in the render body is
  // not safe under concurrent rendering.
  const onDismissRef = useRef(onDismiss);
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const container = containerRef.current;

    // Prefer the first genuinely focusable control; fall back to the container
    // itself so focus never stays behind on the page underneath.
    const first = container?.querySelector<HTMLElement>(FOCUSABLE);
    if (first) {
      first.focus();
    } else if (container) {
      container.tabIndex = -1;
      container.focus();
    }

    return () => {
      // The opener can be gone by now (deleted row, unmounted tab) — only
      // restore when it's still in the document.
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);

  useEffect(() => {
    if (!enabled) return;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onDismissRef.current();
        return;
      }
      if (e.key !== "Tab") return;

      const container = containerRef.current;
      if (!container) return;
      const items = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (items.length === 0) return;

      const first = items[0];
      const last = items[items.length - 1];
      // Wrap at both ends so Tab and Shift+Tab stay inside the dialog.
      if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!container.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [enabled]);

  return containerRef;
}
