import { useEffect, useRef, useState } from "react";
import { API_URL, toAbsoluteUrl } from "@/lib/ppe-api";
import { TrackingOverlay, ViolationReport } from "@/types/detection";
import { BehaviorIncident, FallLiveDetection, FallLiveSummary } from "@/types/behavior";
import { PPESuggestion, ZoneSuggestion, ZoneViolation } from "@/types/zone";
import { AnalysisPhase } from "./camera-panel-types";

export type StreamData = {
  summary: any | null;
  reports: ViolationReport[];
  zone_violations: ZoneViolation[];
  behavior_incidents: BehaviorIncident[];
  fall_summary: FallLiveSummary | null;
  fall_detections: FallLiveDetection[];
  fall_unavailable: string | null;
  tracking_overlay: TrackingOverlay;
  live_frame: string | null;
};

export const emptyStreamData = (): StreamData => ({
  summary: null,
  reports: [],
  zone_violations: [],
  behavior_incidents: [],
  fall_summary: null,
  fall_detections: [],
  fall_unavailable: null,
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
  fallEnabled,
  setPhase,
  setError,
  setStatus,
}: {
  ppeEnabled: boolean;
  zoneEnabled: boolean;
  fallEnabled: boolean;
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
  
  // Manage concurrent connections keyed by RTSP URL
  const wsRefs = useRef<Record<string, WebSocket>>({});
  const [viewedVideoName, setViewedVideoName] = useState<string | null>(null);
  const viewedVideoNameRef = useRef<string | null>(null);

  const [surfaceVideoTime, setCurrentVideoTime] = useState(0);
  const [videoElement, setVideoElement] = useState<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  // Synchronize settings across all open connections
  useEffect(() => {
    Object.entries(wsRefs.current).forEach(([vidName, ws]) => {
      if (ws.readyState === WebSocket.OPEN) {
        const isCurrentlyViewed = vidName === viewedVideoNameRef.current;
        ws.send(
          JSON.stringify({
            event: "update_settings",
            data: {
              enable_ppe: ppeEnabled,
              enable_zone: zoneEnabled,
              enable_fall: fallEnabled,
              viewing: isCurrentlyViewed,
            },
          }),
        );
      }
    });
  }, [fallEnabled, ppeEnabled, zoneEnabled, viewedVideoName]);

  useEffect(() => {
    if (fallEnabled) return;
    setStreamData((prev) => ({
      ...prev,
      fall_summary: null,
      fall_detections: [],
      fall_unavailable: null,
    }));
  }, [fallEnabled]);

  useEffect(() => {
    return () => {
      Object.values(wsRefs.current).forEach((ws) => ws.close());
    };
  }, []);

  const sendMessage = (payload: object) => {
    const activeWs = viewedVideoName ? wsRefs.current[viewedVideoName] : null;
    if (activeWs?.readyState === WebSocket.OPEN) {
      activeWs.send(JSON.stringify(payload));
    }
  };

  const resetStream = () => {
    setStreamData(emptyStreamData());
    setCurrentVideoTime(0);
    Object.values(wsRefs.current).forEach((ws) => ws.close());
    wsRefs.current = {};
    setViewedVideoName(null);
    viewedVideoNameRef.current = null;
  };

  const setViewedCamera = (url: string) => {
    setViewedVideoName(url);
    viewedVideoNameRef.current = url;
    setLiveUrl(url);
    
    // Clear the current live frame so we don't display the stale frame of the previous stream
    setStreamData((prev) => ({
      ...prev,
      live_frame: null,
    }));

    // Instantly toggle viewed stream frames on the backend WebSockets
    Object.entries(wsRefs.current).forEach(([vidName, ws]) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            event: "update_settings",
            data: {
              enable_ppe: ppeEnabled,
              enable_zone: zoneEnabled,
              enable_fall: fallEnabled,
              viewing: vidName === url,
            },
          }),
        );
      }
    });
  };

  const startStreaming = async (videoName: string, isAutoLive = false) => {
    try {
      if (wsRefs.current[videoName]) {
        return;
      }

      const isCurrentlyViewed = viewedVideoNameRef.current === null || viewedVideoNameRef.current === videoName;
      if (isCurrentlyViewed) {
        setViewedVideoName(videoName);
        viewedVideoNameRef.current = videoName;
        setPhase("loading");
        setStatus(isAutoLive ? "Connecting to live stream..." : "Initializing real-time stream...");
      }

      const wsUrlBase = API_URL.replace(/^http/, "ws");
      const wsUrl = `${wsUrlBase}/ws/stream?video_name=${encodeURIComponent(
        videoName,
      )}&enable_ppe=${ppeEnabled}&enable_zone=${zoneEnabled}&enable_fall=${fallEnabled}`;

      const ws = new WebSocket(wsUrl);
      wsRefs.current[videoName] = ws;

      setIsStreaming(true);
      setIsLive(isAutoLive);

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        const { event: eventType, data, frame_index, image_base64 } = msg;
        const isCurrent = videoName === viewedVideoNameRef.current;

        if (eventType === "start") {
          if (isCurrent) {
            setStreamData((prev) => ({
              ...prev,
              tracking_overlay: { ...prev.tracking_overlay, fps: data.fps },
            }));
            setStatus(isAutoLive ? "Stream connected." : "Buffering inference…");
          }
        } else if (eventType === "frame") {
          if (isCurrent) {
            setStreamData((prev) => {
              const nextOverlay = {
                ...prev.tracking_overlay,
                frames: isAutoLive ? data.frames : [...prev.tracking_overlay.frames, ...data.frames],
                frame_width: data.frame_width || prev.tracking_overlay.frame_width,
                frame_height: data.frame_height || prev.tracking_overlay.frame_height,
              };

              setCurrentVideoTime(frame_index / (nextOverlay.fps || 30));

              return {
                ...prev,
                live_frame: image_base64 ? `data:image/jpeg;base64,${image_base64}` : prev.live_frame,
                fall_summary: data.fall_summary ?? prev.fall_summary,
                fall_detections: data.fall_detections ?? prev.fall_detections,
                fall_unavailable: data.fall_unavailable ?? prev.fall_unavailable,
                tracking_overlay: nextOverlay,
              };
            });
          }
        } else if (eventType === "behavior_incident") {
          setStreamData((prev) => ({
            ...prev,
            behavior_incidents: [
              ...prev.behavior_incidents,
              {
                ...data,
                snapshot_url: toAbsoluteUrl(data.snapshot_url),
                evidence: (data.evidence ?? []).map((item: any) => ({
                  ...item,
                  file_url: toAbsoluteUrl(item.file_url),
                })),
              },
            ],
          }));
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
          if (isCurrent) {
            setZoneSuggestions((prev) => ({ ...prev, [data.suggestion_id]: data as ZoneSuggestion }));
          }
        } else if (eventType === "ppe_suggestion") {
          if (isCurrent) {
            setPpeSuggestions((prev) => ({ ...prev, [data.suggestion_id]: data as PPESuggestion }));
          }
        } else if (eventType === "summary") {
          if (isCurrent) {
            setStreamData((prev) => ({ ...prev, summary: data }));
            setPhase("done");
          }
        } else if (eventType === "error") {
          if (isCurrent) {
            setError(data.message);
            setPhase("error");
          }
        } else if (eventType === "end") {
          if (isCurrent) {
            setStatus("Stream completed.");
          }
        }
      };

      ws.onclose = (event) => {
        delete wsRefs.current[videoName];
        if (Object.keys(wsRefs.current).length === 0) {
          setIsStreaming(false);
        }
        console.log(`WebSocket closed for ${videoName}:`, event.code, event.reason);
        if (videoName === viewedVideoNameRef.current && !event.wasClean) {
          setError(`Stream disconnected unexpectedly (Code: ${event.code})`);
        }
      };
      ws.onerror = (event) => {
        console.error(`WebSocket error for ${videoName}:`, event);
        if (videoName === viewedVideoNameRef.current) {
          setError("WebSocket connection failed. Check browser console for security/CORS errors.");
          setPhase("error");
        }
      };
    } catch (err) {
      if (videoName === viewedVideoNameRef.current) {
        setError(err instanceof Error ? err.message : "Streaming failed");
        setPhase("error");
      }
    }
  };

  const syncCameraConnections = (cameras: any[]) => {
    const activeUrls = new Set(
      cameras.filter((c) => c.active && c.rtspUrl).map((c) => c.rtspUrl)
    );

    // 1. Close connections that are no longer active/needed
    Object.entries(wsRefs.current).forEach(([url, ws]) => {
      if (!activeUrls.has(url)) {
        ws.close();
        delete wsRefs.current[url];
      }
    });

    // 2. Start connections for active cameras not yet connected
    cameras.forEach((c) => {
      if (c.active && c.rtspUrl && !wsRefs.current[c.rtspUrl]) {
        void startStreaming(c.rtspUrl, true);
      }
    });
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

  useEffect(() => {
    if (!isStreaming || !videoElement) return;

    const overlay = streamData.tracking_overlay;
    const fps = overlay.fps || 30;
    const stride = overlay.stride || 1;
    const frames = overlay.frames;
    if (frames.length === 0) return;

    const latestProcessedFrame = frames[frames.length - 1].frame_index;
    const currentFrame = Math.round(surfaceVideoTime * fps);
    const lag = currentFrame - latestProcessedFrame;

    const video = videoElement;

    const bufferFrames = Math.ceil((fps * 2) / stride);
    if (latestProcessedFrame < bufferFrames) {
      setStatus(`Buffering… ${Math.round((latestProcessedFrame / bufferFrames) * 100)}%`);
      return;
    }

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
    setViewedCamera,
    syncCameraConnections,
  };
}
