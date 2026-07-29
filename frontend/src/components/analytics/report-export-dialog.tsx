"use client";

import { FileDown, Loader2, Mail, X } from "lucide-react";
import { useEffect, useState } from "react";
import { downloadIncidentReportPdf, emailIncidentReport, getReportPreview } from "@/lib/ppe-api";
import { AnalyticsRangeParam } from "@/types/analytics";
import { ReportPreview } from "@/types/report";

interface ReportExportDialogProps {
  open: boolean;
  onClose: () => void;
  range: AnalyticsRangeParam;
  zoneId: number | null;
}

type SendState = "idle" | "sending" | "success" | "error";

export function ReportExportDialog({ open, onClose, range, zoneId }: ReportExportDialogProps) {
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [includeSnapshots, setIncludeSnapshots] = useState(true);

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");

  const [recipientsInput, setRecipientsInput] = useState("");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [sendState, setSendState] = useState<SendState>("idle");
  const [sendError, setSendError] = useState("");
  const [sentTo, setSentTo] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getReportPreview(range, zoneId)
      .then((result) => {
        if (cancelled) return;
        setPreview(result);
        setPreviewError("");
      })
      .catch((err) => {
        if (cancelled) return;
        setPreview(null);
        setPreviewError(err instanceof Error ? err.message : "Could not load preview");
      });
    return () => {
      cancelled = true;
    };
  }, [open, range, zoneId]);

  if (!open) return null;

  const handleClose = () => {
    setPreview(null);
    setPreviewError("");
    setRecipientsInput("");
    setRecipients([]);
    setSubject("");
    setMessage("");
    setSendState("idle");
    setSendError("");
    setDownloadError("");
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

  const handleDownload = async () => {
    setDownloading(true);
    setDownloadError("");
    try {
      await downloadIncidentReportPdf(range, zoneId, includeSnapshots);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Could not generate the PDF report");
    } finally {
      setDownloading(false);
    }
  };

  const handleSend = async () => {
    const finalRecipients = addPendingRecipient(recipients);
    setRecipients(finalRecipients);
    setRecipientsInput("");

    if (finalRecipients.length === 0) {
      setSendError("Add at least one recipient.");
      setSendState("error");
      return;
    }

    setSendState("sending");
    setSendError("");
    try {
      const result = await emailIncidentReport({
        recipients: finalRecipients,
        range,
        zone_id: zoneId,
        subject: subject.trim() || null,
        message: message.trim() || null,
        include_snapshots: includeSnapshots,
      });
      setSentTo(result.recipients);
      setSendState("success");
      setRecipients([]);
      setSubject("");
      setMessage("");
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "Could not send the report email");
      setSendState("error");
    }
  };

  const zoneScopeLabel = preview ? preview.zone_scope_label : zoneId != null ? `Zone #${zoneId}` : "All zones";

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
              {range} · {zoneScopeLabel}
            </p>
            <h2 className="text-sm font-semibold text-slate-950">Export report</h2>
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
          {previewError ? (
            <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{previewError}</p>
          ) : !preview ? (
            <p className="text-sm text-slate-500">Loading preview…</p>
          ) : (
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {preview.grand_total} incident{preview.grand_total === 1 ? "" : "s"} · generated{" "}
                {preview.generated_at_local}
              </p>
              {preview.data_caveats.length > 0 ? (
                <ul className="mt-2 grid list-disc gap-1 pl-4 text-xs text-amber-700">
                  {preview.data_caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              ) : null}
              <ul className="mt-2 grid gap-1 text-xs text-slate-700">
                {preview.insights.slice(0, 4).map((insight) => (
                  <li key={insight}>• {insight.replace(/\*\*/g, "")}</li>
                ))}
              </ul>
            </div>
          )}

          <label className="flex items-center gap-2 text-xs font-medium text-slate-700">
            <input
              type="checkbox"
              checked={includeSnapshots}
              onChange={(e) => setIncludeSnapshots(e.target.checked)}
              className="size-3.5 rounded border-slate-300"
            />
            Include evidence snapshots (Critical/High incidents)
          </label>

          <div className="border-t border-slate-200 pt-3">
            <button
              type="button"
              onClick={() => void handleDownload()}
              disabled={downloading}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {downloading ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <FileDown className="size-4" aria-hidden="true" />
              )}
              {downloading ? "Generating…" : "Download PDF"}
            </button>
            {downloadError ? <p className="mt-2 text-xs text-red-700">{downloadError}</p> : null}
          </div>

          <div className="border-t border-slate-200 pt-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Email report</p>

            <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded-md border border-slate-200 p-2">
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

            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject (optional)"
              className="mb-2 w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-slate-400"
            />
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Message (optional)"
              rows={2}
              className="mb-3 w-full rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-slate-400"
            />

            <button
              type="button"
              onClick={() => void handleSend()}
              disabled={sendState === "sending"}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-3 py-2 text-xs font-semibold text-lime-200 shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sendState === "sending" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Mail className="size-4" aria-hidden="true" />
              )}
              {sendState === "sending" ? "Sending…" : "Send"}
            </button>

            {sendState === "success" ? (
              <p className="mt-2 rounded-md border border-green-200 bg-green-50 p-2 text-xs text-green-700">
                Report sent to {sentTo.join(", ")}.
              </p>
            ) : null}
            {sendState === "error" && sendError ? (
              <p className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">{sendError}</p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
