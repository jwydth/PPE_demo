"use client";

import { Calendar, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { getReportSchedule, updateReportSchedule } from "@/lib/ppe-api";
import { ReportScheduleRequest, ReportScheduleResponse, ScheduleFrequency } from "@/types/report";

interface ScheduleReportDialogProps {
  open: boolean;
  onClose: () => void;
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const DAYS_OF_MONTH = Array.from({ length: 28 }, (_, i) => i + 1);

type SaveState = "idle" | "saving" | "success" | "error";

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export function ScheduleReportDialog({ open, onClose }: ScheduleReportDialogProps) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [current, setCurrent] = useState<ReportScheduleResponse | null>(null);

  const [frequency, setFrequency] = useState<ScheduleFrequency>("off");
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [time, setTime] = useState("07:00");
  const [recipientsInput, setRecipientsInput] = useState("");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [includeSnapshots, setIncludeSnapshots] = useState(true);

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getReportSchedule()
      .then((schedule) => {
        if (cancelled) return;
        setCurrent(schedule);
        setFrequency(schedule.frequency);
        setDayOfWeek(schedule.day_of_week ?? 0);
        setDayOfMonth(schedule.day_of_month ?? 1);
        setTime(`${pad(schedule.hour)}:${pad(schedule.minute)}`);
        setRecipients(schedule.recipients);
        setIncludeSnapshots(schedule.include_snapshots);
        setLoadError("");
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Could not load schedule");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const handleClose = () => {
    setSaveState("idle");
    setSaveError("");
    setRecipientsInput("");
    onClose();
  };

  const addPendingRecipient = (recipientList: string[]): string[] => {
    const value = recipientsInput.trim().replace(/,$/, "");
    if (!value || recipientList.includes(value)) return recipientList;
    return [...recipientList, value];
  };

  const commitRecipientInput = () => {
    setRecipients((prev) => addPendingRecipient(prev));
    setRecipientsInput("");
  };

  const removeRecipient = (value: string) => {
    setRecipients((prev) => prev.filter((r) => r !== value));
  };

  const handleSave = async () => {
    const finalRecipients = addPendingRecipient(recipients);
    setRecipients(finalRecipients);
    setRecipientsInput("");

    const [hourStr, minuteStr] = time.split(":");
    const payload: ReportScheduleRequest = {
      frequency,
      day_of_week: frequency === "weekly" ? dayOfWeek : null,
      day_of_month: frequency === "monthly" ? dayOfMonth : null,
      hour: Number(hourStr) || 0,
      minute: Number(minuteStr) || 0,
      recipients: finalRecipients,
      include_snapshots: includeSnapshots,
    };

    setSaveState("saving");
    setSaveError("");
    try {
      const result = await updateReportSchedule(payload);
      setCurrent(result);
      setSaveState("success");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Could not save the report schedule");
      setSaveState("error");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={handleClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-md border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Automatic delivery
            </p>
            <h2 className="text-sm font-semibold text-slate-950">Schedule report</h2>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close"
            className="rounded-md p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="grid gap-4 p-4">
          {loadError ? (
            <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{loadError}</p>
          ) : loading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : (
            <>
              {current && (current.last_sent_at || current.next_run_at) ? (
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                  {current.next_run_at ? (
                    <p>
                      <span className="font-semibold text-slate-800">Next send:</span>{" "}
                      {formatDateTime(current.next_run_at)} ({current.timezone_label})
                    </p>
                  ) : null}
                  {current.last_sent_at ? (
                    <p className="mt-1">
                      <span className="font-semibold text-slate-800">Last sent:</span>{" "}
                      {formatDateTime(current.last_sent_at)}
                    </p>
                  ) : null}
                </div>
              ) : null}

              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Frequency
                </p>
                <div className="inline-flex rounded-md border border-slate-200 bg-white p-1 shadow-sm">
                  {(["off", "weekly", "monthly"] as const).map((f) => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => setFrequency(f)}
                      className={`rounded px-3 py-1.5 text-xs font-semibold capitalize transition ${
                        frequency === f ? "bg-slate-950 text-lime-200" : "text-slate-500 hover:bg-slate-100"
                      }`}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              </div>

              {frequency !== "off" ? (
                <div className="grid grid-cols-2 gap-3">
                  {frequency === "weekly" ? (
                    <div>
                      <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Day of week
                      </label>
                      <select
                        value={dayOfWeek}
                        onChange={(e) => setDayOfWeek(Number(e.target.value))}
                        className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-slate-400"
                      >
                        {WEEKDAYS.map((label, idx) => (
                          <option key={label} value={idx}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : (
                    <div>
                      <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Day of month
                      </label>
                      <select
                        value={dayOfMonth}
                        onChange={(e) => setDayOfMonth(Number(e.target.value))}
                        className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-slate-400"
                      >
                        {DAYS_OF_MONTH.map((d) => (
                          <option key={d} value={d}>
                            {d}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div>
                    <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Time {current ? `(${current.timezone_label})` : ""}
                    </label>
                    <input
                      type="time"
                      value={time}
                      onChange={(e) => setTime(e.target.value)}
                      className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-slate-400"
                    />
                  </div>
                </div>
              ) : (
                <p className="text-xs text-slate-500">
                  Automatic delivery is off. Choose Weekly or Monthly to send this report on a
                  recurring schedule.
                </p>
              )}

              {frequency !== "off" ? (
                <>
                  <div>
                    <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Recipients
                    </label>
                    <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-slate-200 p-2">
                      {recipients.map((recipient) => (
                        <span
                          key={recipient}
                          className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-1 text-xs text-slate-700"
                        >
                          {recipient}
                          <button
                            type="button"
                            onClick={() => removeRecipient(recipient)}
                            aria-label={`Remove ${recipient}`}
                          >
                            <X className="size-3" />
                          </button>
                        </span>
                      ))}
                      <input
                        type="text"
                        value={recipientsInput}
                        onChange={(e) => setRecipientsInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
                            if (recipientsInput.trim()) {
                              e.preventDefault();
                              commitRecipientInput();
                            }
                          }
                        }}
                        onBlur={commitRecipientInput}
                        placeholder="name@company.com"
                        className="min-w-[10rem] flex-1 border-none text-xs outline-none"
                      />
                    </div>
                  </div>

                  <label className="flex items-center gap-2 text-xs font-medium text-slate-700">
                    <input
                      type="checkbox"
                      checked={includeSnapshots}
                      onChange={(e) => setIncludeSnapshots(e.target.checked)}
                      className="size-3.5 rounded border-slate-300"
                    />
                    Include evidence snapshots (Critical/High incidents)
                  </label>
                </>
              ) : null}

              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={saveState === "saving"}
                className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-3 py-2 text-xs font-semibold text-lime-200 shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saveState === "saving" ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Calendar className="size-4" aria-hidden="true" />
                )}
                {saveState === "saving" ? "Saving…" : "Save schedule"}
              </button>

              {saveState === "success" ? (
                <p className="rounded-md border border-green-200 bg-green-50 p-2 text-xs text-green-700">
                  Schedule saved.
                </p>
              ) : null}
              {saveState === "error" && saveError ? (
                <p className="rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
                  {saveError}
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
