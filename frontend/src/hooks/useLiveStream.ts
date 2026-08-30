import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, toAbsoluteUrl } from "@/lib/ppe-api";
import { TrackingOverlay, TrackingOverlayFrame, ViolationReport } from "@/types/detection";
import { BehaviorIncident, FallLiveDetection, FallLiveSummary } from "@/types/behavior";
import { PPESuggestion, ZoneSuggestion, ZoneViolation } from "@/types/zone";
import { AnalysisPhase } from "./camera-panel-types";
import {
  appendOverlayRing,
  OVERLAY_BUFFER_MS,
  type OverlaySelection,
  type StreamTimeline,
} from "@/lib/stream-timeline";

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
    frame_width: 16,
    frame_height: 9,
    frames: [],
  },
  live_frame: null,
});

/** Suggestion dictionary key. The backend's suggestion_id carries no camera,
 * so it alone is not unique across cameras — see ZoneSuggestion.source_key. */
export function suggestionKey(sourceKey: string, suggestionId: string): string {
  return `${sourceKey}::${suggestionId}`;
}

export function useLiveStream({
  ppeEnabled,
  zoneEnabled,
  fallEnabled,
  setPhase,
  setError,
  setStatus,
  viewMode,
  selectedCameraSources,
  cameras,
  cameraFeatureMap,
}: {
  ppeEnabled: boolean;
  zoneEnabled: boolean;
  fallEnabled: boolean;
  setPhase: (phase: AnalysisPhase) => void;
  setError: (message: string) => void;
  setStatus: (status: string) => void;
  viewMode?: "single" | "matrix";
  selectedCameraSources?: string[];
  cameras?: { id: number; rtspUrl: string }[];
  cameraFeatureMap?: Record<number, Record<string, boolean>>;
}) {
  const [streamData, setStreamData] = useState<StreamData>(emptyStreamData());
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLive, setIsLive] = useState(false);
  const [liveUrl, setLiveUrl] = useState("rtsp://127.0.0.1:8554/mystream");
  const [zoneSuggestions, setZoneSuggestions] = useState<Record<string, ZoneSuggestion>>({});
  const [ppeSuggestions, setPpeSuggestions] = useState<Record<string, PPESuggestion>>({});
  const [liveFrames, setLiveFrames] = useState<Record<string, string>>({});
  const [cameraOverlays, setCameraOverlays] = useState<Record<string, {
    frames: any[];
    frameWidth: number;
    frameHeight: number;
    fallDetections: any[];
  }>>({});
  const [hlsSourceTimes, setHlsSourceTimes] = useState<Record<string, number | null>>({});
  const [currentSourceTimeMs, setCurrentSourceTimeMs] = useState<number | null>(null);
  
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

  // Mirrors of the latest props/state, kept current every render so
  // long-lived callbacks (ws.onopen fires whenever the browser decides the
  // handshake is done, not on a React render) can read fresh values instead
  // of whatever was captured in their closure at connect time.
  const camerasRef = useRef(cameras);
  const cameraFeatureMapRef = useRef(cameraFeatureMap);
  const ppeEnabledRef = useRef(ppeEnabled);
  const zoneEnabledRef = useRef(zoneEnabled);
  const fallEnabledRef = useRef(fallEnabled);
  const viewModeRef = useRef(viewMode);
  const selectedCameraSourcesRef = useRef(selectedCameraSources);
  useEffect(() => {
    camerasRef.current = cameras;
    cameraFeatureMapRef.current = cameraFeatureMap;
    ppeEnabledRef.current = ppeEnabled;
    zoneEnabledRef.current = zoneEnabled;
    fallEnabledRef.current = fallEnabled;
    viewModeRef.current = viewMode;
    selectedCameraSourcesRef.current = selectedCameraSources;
  });

  // Single source of truth for what a connection's feature flags should be
  // right now, from the per-camera config once it has loaded, falling back
  // to the generic (non-per-camera) toggles until then.
  const buildSettingsMessage = (videoName: string) => {
    const cams = camerasRef.current;
    const featureMap = cameraFeatureMapRef.current;

    let isViewing = false;
    if (viewModeRef.current === "matrix" && selectedCameraSourcesRef.current) {
      isViewing = selectedCameraSourcesRef.current.includes(videoName);
    } else {
      isViewing = videoName === viewedVideoNameRef.current;
    }

    const cam = cams?.find((c) => c.rtspUrl === videoName);
    const featureDict = cam && featureMap ? featureMap[cam.id] : null;

    const ppe = featureDict ? featureDict["ppe_detection"] : ppeEnabledRef.current;
    const zone = featureDict ? featureDict["zone_monitoring"] : zoneEnabledRef.current;
    const fall = featureDict
      ? (featureDict["behavior_detection"] ?? featureDict["fall_detection"])
      : fallEnabledRef.current;

    return {
      event: "update_settings",
      data: {
        features: {
          ppe_detection: ppe !== undefined ? ppe : true,
          zone_monitoring: zone !== undefined ? zone : false,
          behavior_detection: fall !== undefined ? fall : false,
        },
        viewing: isViewing,
      },
    };
  };

  // Object URL for the most recent binary JPEG frame received per stream (see
  // ws.onmessage below — the server sends the frame as a raw binary WS message
  // immediately before the "frame" JSON envelope that references it via
  // has_image, instead of embedding it as base64 inside the JSON). Object URLs
  // must be explicitly revoked or they leak for the life of the tab.
  const pendingImageUrlRef = useRef<Record<string, string>>({});
  const renderedOverlayRef = useRef<Record<string, OverlaySelection>>({});

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

  // Synchronize settings across all open connections. This only reaches
  // sockets that are already OPEN — a socket that's still mid-handshake when
  // this fires (e.g. per-camera feature config resolves before the WS
  // handshake completes) won't be caught here, since nothing re-runs this
  // effect purely because a socket later transitions to OPEN. That gap is
  // closed by ws.onopen in startStreaming below, which sends the same
  // freshly-computed settings the instant the connection becomes ready —
  // whichever of the two "finishes last" ends up delivering the correct
  // settings, so the connection can never get stuck on stale defaults.
  useEffect(() => {
    Object.entries(wsRefs.current).forEach(([vidName, ws]) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(buildSettingsMessage(vidName)));
      }
    });
  }, [fallEnabled, ppeEnabled, zoneEnabled, viewedVideoName, viewMode, selectedCameraSources, cameras, cameraFeatureMap]);

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
      Object.values(pendingImageUrlRef.current).forEach((url) => URL.revokeObjectURL(url));
      setLiveFrames((prev) => {
        Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
        return {};
      });
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
    setLiveFrames((prev) => {
      Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
      return {};
    });
    setCameraOverlays({});
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
        ws.send(JSON.stringify(buildSettingsMessage(vidName)));
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

      const cam = cameras?.find((c) => c.rtspUrl === videoName);
      const featureDict = cam && cameraFeatureMap ? cameraFeatureMap[cam.id] : null;

      const ppe = featureDict ? featureDict["ppe_detection"] : ppeEnabled;
      const zone = featureDict ? featureDict["zone_monitoring"] : zoneEnabled;
      const fall = featureDict ? (featureDict["behavior_detection"] ?? featureDict["fall_detection"]) : fallEnabled;

      const wsUrlBase = API_URL.replace(/^http/, "ws");
      const wsUrl = `${wsUrlBase}/ws/stream?video_name=${encodeURIComponent(
        videoName,
      )}&enable_ppe=${ppe !== undefined ? ppe : true}&enable_zone=${zone !== undefined ? zone : false}&enable_fall=${fall !== undefined ? fall : false}&metadata_only=${isAutoLive}`;

      const ws = new WebSocket(wsUrl);
      ws.binaryType = "blob";
      wsRefs.current[videoName] = ws;

      // The URL query params above reflect whatever cameraFeatureMap held at
      // call time, which can still be pre-fetch (this connection is often
      // opened by syncCameraConnections before the per-camera feature GET
      // resolves) — see buildSettingsMessage's doc comment for why the
      // "Synchronize settings" effect alone can't be relied on to correct
      // that. Sending fresh settings the instant the handshake completes
      // guarantees this connection is never left running with stale
      // ppe/zone/fall flags, regardless of which resolves first.
      ws.onopen = () => {
        ws.send(JSON.stringify(buildSettingsMessage(videoName)));
      };

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
          const url = URL.createObjectURL(event.data);
          pendingImageUrlRef.current[videoName] = url;
          setLiveFrames((prev) => {
            const oldUrl = prev[videoName];
            if (oldUrl) URL.revokeObjectURL(oldUrl);
            return { ...prev, [videoName]: url };
          });
          return;
        }

        const msg = JSON.parse(event.data);
        const {
          event: eventType,
          data,
          frame_index,
          has_image,
          stream_epoch,
          media_pts_ms,
          source_time_ms,
          inference_completed_ms,
          discontinuity_sequence,
        } = msg;
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

          const timeline = {
            stream_epoch,
            media_pts_ms,
            source_time_ms,
            inference_completed_ms,
            discontinuity_sequence,
          };
          const timestampedFrames = (data.frames || []).map((frame: TrackingOverlayFrame) => ({
            ...frame,
            ...timeline,
          }));
          const behaviorTimeline = data.behavior_timeline ?? timeline;
          const timestampedBehavior = (data.fall_detections || []).map((detection: FallLiveDetection) => ({
            ...detection,
            ...behaviorTimeline,
          }));
          setCameraOverlays((prev) => ({
            ...prev,
            [videoName]: {
              frames: appendOverlayRing(prev[videoName]?.frames ?? [], timestampedFrames),
              frameWidth: data.frame_width || 16,
              frameHeight: data.frame_height || 9,
              fallDetections: appendBehaviorRing(
                prev[videoName]?.fallDetections ?? [],
                timestampedBehavior,
              ),
            },
          }));

          if (isCurrent) {
            setStreamData((prev) => {
              const nextOverlay = {
                ...prev.tracking_overlay,
                frames: isAutoLive
                  ? appendOverlayRing(prev.tracking_overlay.frames, timestampedFrames)
                  : [...prev.tracking_overlay.frames, ...timestampedFrames],
                frame_width: data.frame_width || prev.tracking_overlay.frame_width,
                frame_height: data.frame_height || prev.tracking_overlay.frame_height,
              };

              if (!isAutoLive) {
                setCurrentVideoTime(frame_index / (nextOverlay.fps || 30));
              }

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
                fall_detections: isAutoLive
                  ? appendBehaviorRing(prev.fall_detections, timestampedBehavior)
                  : timestampedBehavior,
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
            // Keyed and stamped per camera. These dictionaries are never
            // cleared on a camera switch, so keying them by suggestion_id
            // alone left one camera's "Must Wear Hard Hat" banner sitting over
            // the next camera's feed — and since the backend builds that id
            // from sign class + grid position with no camera in it, two
            // cameras could also overwrite each other's suggestion.
            setZoneSuggestions((prev) => ({
              ...prev,
              [suggestionKey(videoName, data.suggestion_id)]: {
                ...(data as ZoneSuggestion),
                source_key: videoName,
              },
            }));
          }
        } else if (eventType === "ppe_suggestion") {
          if (isCurrent) {
            setPpeSuggestions((prev) => ({
              ...prev,
              [suggestionKey(videoName, data.suggestion_id)]: {
                ...(data as PPESuggestion),
                source_key: videoName,
              },
            }));
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
        // Browser error events intentionally expose no useful cause. The
        // following onclose event contains the close code/reason and is the
        // single source of truth for UI error state and reconnect behavior.
        console.error(`WebSocket transport error for ${videoName} (state=${ws.readyState}):`, event);
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
        setLiveFrames((prev) => {
          const next = { ...prev };
          if (next[url]) {
            URL.revokeObjectURL(next[url]);
            delete next[url];
          }
          return next;
        });
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
    if (!isStreaming || !videoElement || isLive) return;

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

  const handleHlsTimeline = useCallback((videoName: string, timeline: StreamTimeline) => {
    setHlsSourceTimes((current) => ({ ...current, [videoName]: timeline.sourceTimeMs }));
    if (videoName === viewedVideoNameRef.current) {
      setCurrentSourceTimeMs(timeline.sourceTimeMs);
      setCurrentVideoTime(timeline.mediaTimeSeconds);
    }
  }, []);

  const handleOverlayPresentation = useCallback((
    videoName: string,
    selection: OverlaySelection,
  ) => {
    renderedOverlayRef.current[videoName] = selection;
  }, []);

  const handlePlaybackMetrics = useCallback((videoName: string, metrics: {
    liveDelayMs: number | null;
    sourceTimeMs: number | null;
    rebufferCount: number;
    droppedVideoFrames: number;
  }) => {
    const socket = wsRefs.current[videoName];
    if (socket?.readyState !== WebSocket.OPEN) return;
    const renderedOverlay = renderedOverlayRef.current[videoName];
    const signedOverlaySkewMs = renderedOverlay?.signedSkewMs ?? null;
    socket.send(JSON.stringify({
      event: "playback_metrics",
      data: {
        live_delay_ms: metrics.liveDelayMs,
        overlay_skew_ms: signedOverlaySkewMs === null ? null : Math.abs(signedOverlaySkewMs),
        rendered_overlay_signed_skew_ms: signedOverlaySkewMs,
        overlay_selection_mode: renderedOverlay?.mode ?? "missing",
        rebuffer_count: metrics.rebufferCount,
        dropped_video_frames: metrics.droppedVideoFrames,
      },
    }));
  }, []);

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
    liveFrames,
    cameraOverlays,
    hlsSourceTimes,
    currentSourceTimeMs,
    handleHlsTimeline,
    handleOverlayPresentation,
    handlePlaybackMetrics,
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

function appendBehaviorRing<T extends { source_time_ms?: number }>(current: T[], incoming: T[]): T[] {
  const combined = [...current, ...incoming];
  const newest = Math.max(...combined.map((item) => item.source_time_ms ?? 0), 0);
  if (newest <= 0) return combined.slice(-240);
  return combined.filter((item) => (item.source_time_ms ?? newest) >= newest - OVERLAY_BUFFER_MS);
}
