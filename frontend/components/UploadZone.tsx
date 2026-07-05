"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";

interface UploadZoneProps {
  onFileSelect: (file: File) => void;
  disabled?: boolean;
}

const ACCEPTED_MIME: Record<string, string[]> = {
  "image/jpeg": [".jpg", ".jpeg"],
  "image/png": [".png"],
  "image/webp": [".webp"],
  "image/bmp": [".bmp"],
  "video/mp4": [".mp4"],
  "video/mpeg": [".mpeg", ".mpg"],
  "video/quicktime": [".mov"],
  "video/x-msvideo": [".avi"],
  "video/x-matroska": [".mkv"],
  "video/webm": [".webm"],
};

export function UploadZone({ onFileSelect, disabled }: UploadZoneProps) {
  const [preview, setPreview] = useState<string | null>(null);
  const [previewKind, setPreviewKind] = useState<"image" | "video">("image");

  const onDrop = useCallback(
    (accepted: File[]) => {
      const file = accepted[0];
      if (!file) return;
      if (preview) URL.revokeObjectURL(preview);

      setPreview(URL.createObjectURL(file));
      setPreviewKind(file.type.startsWith("video/") ? "video" : "image");
      onFileSelect(file);
    },
    [onFileSelect, preview],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_MIME,
    multiple: false,
    disabled,
  });

  return (
    <div
      {...getRootProps()}
      className={[
        "relative border-2 border-dashed rounded-lg transition-all duration-200",
        "flex flex-col items-center justify-center min-h-[260px] overflow-hidden",
        isDragActive ? "border-orange-500 bg-orange-500/5" : "border-zinc-700 hover:border-zinc-500",
        disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
      ].join(" ")}
    >
      <input {...getInputProps()} />

      {preview && previewKind === "video" ? (
        <video src={preview} className="max-h-[260px] max-w-full rounded" controls muted />
      ) : preview ? (
        <img
          src={preview}
          alt="Upload preview"
          className="max-h-[260px] max-w-full object-contain rounded"
        />
      ) : (
        <div className="flex flex-col items-center gap-3 p-8 text-center select-none">
          <svg
            className="w-12 h-12 text-zinc-600"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
            />
          </svg>
          <p className="text-zinc-400 font-mono text-sm">
            {isDragActive ? "DROP FILE HERE" : "DRAG & DROP OR CLICK TO UPLOAD"}
          </p>
          <p className="text-zinc-600 font-mono text-xs">
            JPG / PNG / WEBP / BMP / MP4 / MOV / AVI / MKV / WEBM
          </p>
        </div>
      )}
    </div>
  );
}
