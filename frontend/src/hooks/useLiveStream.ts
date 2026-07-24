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

  // Video names whose *next* close event is expected (we asked for it, e.g.
  // deactivating a camera or resetting) — so onclose knows not to reconnect.
  // Any close that isn't in this set is treated as unexpected and retried;
  // this is what makes a connection survive React StrictMode's dev-mode
  // double-invoke of mount effects (mount -> cleanup -> mount), which closes
  // every open socket once immediately after opening it with no dependent
  // effect left to reopen it.
  const intentionalCloseRef = useRef<Set<string>>(new Set());
  const reconnectAttemptsRef = useRef<Record<string, number>>({});
  const MAX_RECONNECT_ATTEMPTS = 5;

  // Object URL for the most recent binary JPEG frame received per stream (see
  // ws.onmessage below — the server sends the frame as a raw binary WS message
  // immediately before the "frame" JSON envelope that references it via
  // has_image, instead of embedding it as base64 inside the JSON). Object URLs
  // must be explicitly revoked or they leak for the life of the tab.
  const pendingImageUrlRef = useRef<Record<string, string>>({});

  const revokePendingImage = (videoName: string) => {
    const url = pendingImageUrlRef.current[videoName];
    if (url) {
      URL.revokeObjectURL(url);
      delete pendingImageUrlRef.current[videoName];
    }
  };

  const closeSocket = (videoName: string) => {
    const ws = wsRefs.current[videoName];
    if (ws) {
      intentionalCloseRef.current.add(videoName);
      ws.close();
    }
  };

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
    // Deliberately NOT marked via closeSocket/intentionalCloseRef: this cleanup
    // also fires on React StrictMode's simulated dev-mode unmount, and we want
    // that specific close to be treated as unexpected so onclose reconnects it.
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
    setStreamData((prev) => {
      if (prev.live_frame) URL.revokeObjectURL(prev.live_frame);
      return emptyStreamData();
    });
    Object.keys(pendingImageUrlRef.current).forEach(revokePendingImage);
    setCurrentVideoTime(0);
    Object.keys(wsRefs.current).forEach(closeSocket);
    wsRefs.current = {};
    setViewedVideoName(null);
    viewedVideoNameRef.current = null;
  };

  const setViewedCamera = (url: string) => {
    setViewedVideoName(url);
    viewedVideoNameRef.current = url;
    setLiveUrl(url);

    // Clear the current live frame so we don't display the stale frame of the previous stream
    setStreamData((prev) => {
      if (prev.live_frame) URL.revokeObjectURL(prev.live_frame);
      return { ...prev, live_frame: null };
    });

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
      ws.binaryType = "blob";
      wsRefs.current[videoName] = ws;

      setIsStreaming(true);
      setIsLive(isAutoLive);

      ws.onmessage = (event) => {
        if (event.data instanceof Blob) {
          // Raw JPEG for the "frame" event that follows on this same
          // connection (see routers/streaming.py) — stash it as an object URL
          // for the "frame" handler below to pick up via has_image. Deliberately
          // NOT revoking the previous pending URL here: it may still be the one
          // rendered as live_frame, and revoking it while the <img> element is
          // showing it can blank the frame in some browsers. The frame handler
          // below revokes it once it's confirmed superseded in state.
          pendingImageUrlRef.current[videoName] = URL.createObjectURL(event.data);
          return;
        }

        const msg = JSON.parse(event.data);
        const { event: eventType, data, frame_index, has_image } = msg;
        const isCurrent = videoName === viewedVideoNameRef.current;

        if (eventType === "start") {
          // NOT where we clear the reconnect-attempt count — "start" fires
          // before the backend has actually tried to open the video source
          // (see real_video_pipeline in video_pipeline/__init__.py), so a
          // camera whose source never opens still gets a "start" on every
          // attempt right before it errors out. Resetting here made a
          // permanently-broken camera retry forever, once every ~500ms,
          // since the counter never got a chance to reach
          // MAX_RECONNECT_ATTEMPTS. The "frame" branch below resets it
          // instead — actual frame data is real proof the connection works.
          if (isCurrent) {
            setStreamData((prev) => ({
              ...prev,
              tracking_overlay: { ...prev.tracking_overlay, fps: data.fps },
            }));
            setStatus(isAutoLive ? "Stream connected." : "Buffering inference…");
          }
        } else if (eventType === "frame") {
          // The first successfully processed frame is real proof this
          // connection is healthy — clear any reconnect-attempt count so a
          // later drop gets the full retry budget again.
          delete reconnectAttemptsRef.current[videoName];
          if (isCurrent) {
            setStreamData((prev) => {
              const nextOverlay = {
                ...prev.tracking_overlay,
                frames: isAutoLive ? data.frames : [...prev.tracking_overlay.frames, ...data.frames],
                frame_width: data.frame_width || prev.tracking_overlay.frame_width,
                frame_height: data.frame_height || prev.tracking_overlay.frame_height,
              };

              setCurrentVideoTime(frame_index / (nextOverlay.fps || 30));

              const nextLiveFrame = has_image
                ? (pendingImageUrlRef.current[videoName] ?? prev.live_frame)
                : prev.live_frame;
              if (nextLiveFrame !== prev.live_frame && prev.live_frame) {
                URL.revokeObjectURL(prev.live_frame);
              }

              return {
                ...prev,
                live_frame: nextLiveFrame,
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
        } else if (eventType === "ping") {
          // Server-side liveness probe (routers/streaming.py) — sent when the
          // connection has been idle a while, just to confirm the socket is
          // still alive. No client-side action needed; merely receiving it
          // (or failing to) is what the server cares about.
        }
      };

      ws.onclose = (event) => {
        // Revoke a binary frame that arrived but whose paired "frame" JSON
        // envelope never did (connection dropped between the two messages).
        revokePendingImage(videoName);
        const wasIntentional = intentionalCloseRef.current.delete(videoName);
        // The backend uses this code when a *newer* connection takes over this
        // same camera (see streaming.py's cancel_event/supersede logic, used
        // for page reloads and duplicate tabs/mounts). Reconnecting here would
        // just cancel that newer connection right back — an infinite ping-pong
        // between the two sessions — so treat it like an intentional close.
        const wasSuperseded = event.code === 4001;
        delete wsRefs.current[videoName];
        if (Object.keys(wsRefs.current).length === 0) {
          setIsStreaming(false);
        }
        console.log(
          `WebSocket closed for ${videoName}:`,
          event.code,
          event.reason,
          wasIntentional ? "(intentional)" : wasSuperseded ? "(superseded)" : "(unexpected)",
        );
        if (videoName === viewedVideoNameRef.current && !event.wasClean) {
          setError(`Stream disconnected unexpectedly (Code: ${event.code})`);
        }

        if (!wasIntentional && !wasSuperseded) {
          const attempts = reconnectAttemptsRef.current[videoName] ?? 0;
          if (attempts < MAX_RECONNECT_ATTEMPTS) {
            reconnectAttemptsRef.current[videoName] = attempts + 1;
            // Short delay covers both a genuine transient drop and React
            // StrictMode's dev-mode double-invoke (mount -> cleanup -> mount),
            // which closes this socket moments after opening it with nothing
            // else left to reopen it — by the time this fires, that cycle has
            // long finished, so the reconnect sticks.
            setTimeout(() => {
              if (!wsRefs.current[videoName]) {
                void startStreaming(videoName, isAutoLive);
              }
            }, 500);
          }
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
    Object.keys(wsRefs.current).forEach((url) => {
      if (!activeUrls.has(url)) {
        closeSocket(url);
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
