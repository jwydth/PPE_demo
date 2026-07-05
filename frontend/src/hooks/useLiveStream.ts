import { useEffect, useRef, useState } from "react";
import { API_URL, toAbsoluteUrl } from "@/lib/ppe-api";
import { TrackingOverlay, ViolationReport } from "@/types/detection";
import { PPESuggestion, ZoneSuggestion, ZoneViolation } from "@/types/zone";
import { AnalysisPhase } from "./camera-panel-types";

export type StreamData = {
  summary: any | null;
  reports: ViolationReport[];
  zone_violations: ZoneViolation[];
  tracking_overlay: TrackingOverlay;
  live_frame: string | null;
};

export const emptyStreamData = (): StreamData => ({
  summary: null,
  reports: [],
  zone_violations: [],
  tracking_overlay: {
    fps: 30,
    stride: 1,
    frame_width: 1000,
    frame_height: 1000,
    frames: [],
  },
  live_frame: null,
});

export function useLiveStream({
  ppeEnabled,
  zoneEnabled,
  setPhase,
  setError,
  setStatus,
}: {
  ppeEnabled: boolean;
  zoneEnabled: boolean;
  setPhase: (phase: AnalysisPhase) => void;
  setError: (message: string) => void;
  setStatus: (status: string) => void;
}) {
  const [streamData, setStreamData] = useState<StreamData>(emptyStreamData());
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLive, setIsLive] = useState(false);
  const [liveUrl, setLiveUrl] = useState("rtsp://127.0.0.1:8554/mystream");
  const [zoneSuggestions, setZoneSuggestions] = useState<Record<string, ZoneSuggestion>>({});
  const [ppeSuggestions, setPpeSuggestions] = useState<Record<string, PPESuggestion>>({});
  const wsRef = useRef<WebSocket | null>(null);

  const [surfaceVideoTime, setCurrentVideoTime] = useState(0);
  const [videoElement, setVideoElement] = useState<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          event: "update_settings",
          data: {
            enable_ppe: ppeEnabled,
            enable_zone: zoneEnabled,
          },
        }),
      );
    }
  }, [ppeEnabled, zoneEnabled]);

  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  const sendMessage = (payload: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  };

  // Full reset used when discarding the current file/stream entirely
  // (closes the socket, unlike a plain streamData reset on file selection).
  const resetStream = () => {
    setStreamData(emptyStreamData());
    setCurrentVideoTime(0);
    if (wsRef.current) wsRef.current.close();
  };

  const startStreaming = async (videoName: string, isAutoLive = false) => {
    try {
      setPhase("loading");
      setStatus(isAutoLive ? "Connecting to live stream..." : "Initializing real-time stream...");

      const wsUrlBase = API_URL.replace(/^http/, "ws");
      const wsUrl = `${wsUrlBase}/ws/stream?video_name=${encodeURIComponent(
        videoName,
      )}&enable_ppe=${ppeEnabled}&enable_zone=${zoneEnabled}`;

      if (wsRef.current) wsRef.current.close();
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      setStreamData(emptyStreamData());
      setZoneSuggestions({});
      setIsStreaming(true);
      setIsLive(isAutoLive);

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        const { event: eventType, data, frame_index, image_base64 } = msg;

        if (eventType === "start") {
          setStreamData((prev) => ({
            ...prev,
            tracking_overlay: { ...prev.tracking_overlay, fps: data.fps },
          }));
          setStatus(isAutoLive ? "Stream connected." : "Buffering inference…");
        } else if (eventType === "frame") {
          setStreamData((prev) => {
            const nextOverlay = {
              ...prev.tracking_overlay,
              frames: isAutoLive ? data.frames : [...prev.tracking_overlay.frames, ...data.frames],
              frame_width: data.frame_width || prev.tracking_overlay.frame_width,
              frame_height: data.frame_height || prev.tracking_overlay.frame_height,
            };

            if (isAutoLive) {
              setCurrentVideoTime(frame_index / (nextOverlay.fps || 30));
            }

            return {
              ...prev,
              live_frame: image_base64 ? `data:image/jpeg;base64,${image_base64}` : prev.live_frame,
              tracking_overlay: nextOverlay,
            };
          });
        } else if (eventType === "violation") {
          setStreamData((prev) => ({
            ...prev,
            reports: [
              ...prev.reports,
              {
                ...data,
                snapshot_url: toAbsoluteUrl(data.snapshot_url),
              },
            ],
          }));
        } else if (eventType === "zone_violation") {
          setStreamData((prev) => ({
            ...prev,
            zone_violations: [
              ...prev.zone_violations,
              {
                ...data,
                snapshot_path: toAbsoluteUrl(data.snapshot_path),
              },
            ],
          }));
        } else if (eventType === "zone_suggestion") {
          setZoneSuggestions((prev) => ({ ...prev, [data.suggestion_id]: data as ZoneSuggestion }));
        } else if (eventType === "ppe_suggestion") {
          setPpeSuggestions((prev) => ({ ...prev, [data.suggestion_id]: data as PPESuggestion }));
        } else if (eventType === "summary") {
          setStreamData((prev) => ({ ...prev, summary: data }));
          setPhase("done");
        } else if (eventType === "error") {
          setError(data.message);
          setPhase("error");
        } else if (eventType === "end") {
          setStatus("Stream completed.");
        }
      };

      ws.onclose = (event) => {
        setIsStreaming(false);
        console.log("WebSocket closed:", event.code, event.reason);
        if (!event.wasClean) {
          setError(`Stream disconnected unexpectedly (Code: ${event.code})`);
        }
      };
      ws.onerror = (event) => {
        console.error("WebSocket error:", event);
        setError("WebSocket connection failed. Check browser console for security/CORS errors.");
        setPhase("error");
      };
    } catch (err) {
      setError(err instanceof Error ? err.message : "Streaming failed");
      setPhase("error");
    }
  };

  const togglePlayback = () => {
    if (!videoElement) return;
    if (isPlaying) {
      videoElement.pause();
    } else {
      videoElement.play();
    }
  };

  const handleVideoPlay = () => {
    setIsPlaying(true);
    setCurrentVideoTime(videoElement?.currentTime ?? 0);
  };
  const handleVideoPause = () => {
    setIsPlaying(false);
    setCurrentVideoTime(videoElement?.currentTime ?? 0);
  };
  const handleVideoTimeSync = () => {
    setCurrentVideoTime(videoElement?.currentTime ?? 0);
  };

  useEffect(() => {
    if (!isPlaying) return;
    let frameId = 0;
    const syncTime = () => {
      setCurrentVideoTime(videoElement?.currentTime ?? 0);
      frameId = requestAnimationFrame(syncTime);
    };
    frameId = requestAnimationFrame(syncTime);
    return () => cancelAnimationFrame(frameId);
  }, [isPlaying, videoElement]);

  // Buffer-then-sync: wait until inference has a lead before starting playback,
  // then keep the video paused whenever it gets more than 15 frames ahead of inference.
  useEffect(() => {
    if (!isStreaming || !videoElement) return;

    const overlay = streamData.tracking_overlay;
    const fps = overlay.fps || 30;
    const stride = overlay.stride || 1;
    const frames = overlay.frames;
    if (frames.length === 0) return;

    const latestProcessedFrame = frames[frames.length - 1].frame_index;
    const currentFrame = Math.round(surfaceVideoTime * fps);
    const lag = currentFrame - latestProcessedFrame; // positive = video ahead of inference

    const video = videoElement;

    // Phase 1 – buffering: don't play until inference has processed at least 2 s worth of frames
    const bufferFrames = Math.ceil((fps * 2) / stride);
    if (latestProcessedFrame < bufferFrames) {
      setStatus(`Buffering… ${Math.round((latestProcessedFrame / bufferFrames) * 100)}%`);
      return;
    }

    // Phase 2 – playing: start (once) and keep video within 15 frames of inference
    if (video.paused && isPlaying) {
      setStatus("Streaming active.");
      void video.play();
    }
    if (lag > 15 && !video.paused) {
      video.pause();
    } else if (lag <= 5 && video.paused && isPlaying) {
      void video.play();
    }
  }, [streamData.tracking_overlay.frames, surfaceVideoTime, isStreaming, isPlaying, streamData.tracking_overlay, videoElement]);

  return {
    streamData,
    setStreamData,
    isStreaming,
    isLive,
    liveUrl,
    setLiveUrl,
    zoneSuggestions,
    setZoneSuggestions,
    ppeSuggestions,
    setPpeSuggestions,
    startStreaming,
    sendMessage,
    resetStream,
    setVideoElement,
    isPlaying,
    setIsPlaying,
    currentVideoTime: surfaceVideoTime,
    setCurrentVideoTime,
    togglePlayback,
    handleVideoPlay,
    handleVideoPause,
    handleVideoTimeSync,
  };
}
