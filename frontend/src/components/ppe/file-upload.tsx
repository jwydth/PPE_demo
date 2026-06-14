"use client";

import { UploadCloud } from "lucide-react";
import { DragEvent, useRef, useState } from "react";

const accept = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/bmp",
  "video/mp4",
  "video/mpeg",
  "video/quicktime",
  "video/x-msvideo",
  "video/x-matroska",
  "video/webm",
].join(",");

export function FileUpload({
  label,
  helper,
  multiple = false,
  disabled = false,
  imagesOnly = false,
  videosOnly = false,
  onFiles,
}: {
  label: string;
  helper: string;
  multiple?: boolean;
  disabled?: boolean;
  imagesOnly?: boolean;
  videosOnly?: boolean;
  onFiles: (files: File[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const commitFiles = (files: FileList | null) => {
    if (!files || disabled) return;
    const next = Array.from(files).filter((file) => {
      if (imagesOnly) return file.type.startsWith("image/");
      if (videosOnly) return file.type.startsWith("video/");
      return true;
    });
    if (next.length > 0) onFiles(multiple ? next : [next[0]]);
  };

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDragging(false);
    commitFiles(event.dataTransfer.files);
  };

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => inputRef.current?.click()}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={[
        "flex min-h-44 w-full flex-col items-center justify-center rounded-md border-2 border-dashed p-6 text-center transition",
        dragging
          ? "border-green-800 bg-lime-50 text-green-950"
          : "border-slate-300 bg-white text-slate-600 hover:border-slate-400 hover:bg-slate-50",
        disabled ? "cursor-not-allowed opacity-60" : "",
      ].join(" ")}
    >
      <input
        ref={inputRef}
        type="file"
        accept={
          imagesOnly
            ? "image/jpeg,image/png,image/webp,image/bmp"
            : videosOnly
              ? "video/mp4,video/mpeg,video/quicktime,video/x-msvideo,video/x-matroska,video/webm"
              : accept
        }
        multiple={multiple}
        className="hidden"
        onChange={(event) => {
          commitFiles(event.target.files);
          event.target.value = "";
        }}
      />
      <UploadCloud className="size-9 text-green-900" aria-hidden="true" />
      <span className="mt-3 text-sm font-semibold text-slate-950">{label}</span>
      <span className="mt-1 max-w-md text-sm leading-6 text-slate-500">{helper}</span>
    </button>
  );
}
