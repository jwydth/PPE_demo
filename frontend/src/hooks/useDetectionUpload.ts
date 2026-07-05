import { useEffect, useMemo, useState } from "react";
import { analyzeImage } from "@/lib/ppe-api";
import { DetectionResponse, VideoProcessingResponse } from "@/types/detection";

export function useDetectionUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [imageResult, setImageResult] = useState<DetectionResponse | null>(null);
  const [videoResult, setVideoResult] = useState<VideoProcessingResponse | null>(null);

  const videoUrl = useMemo(
    () => (file?.type.startsWith("video/") ? URL.createObjectURL(file) : ""),
    [file],
  );
  const isVideo = !!file?.type.startsWith("video/");

  useEffect(() => {
    if (!videoUrl) return;
    return () => URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  const analyzeUploadedImage = async (): Promise<DetectionResponse> => {
    if (!file) throw new Error("No file selected");
    return analyzeImage(file);
  };

  return {
    file,
    setFile,
    imageResult,
    setImageResult,
    videoResult,
    setVideoResult,
    videoUrl,
    isVideo,
    analyzeUploadedImage,
  };
}
