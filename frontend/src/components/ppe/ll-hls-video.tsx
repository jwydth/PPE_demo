"use client";

import Hls from "hls.js";
import { Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { rtspToHlsUrl, type StreamTimeline } from "@/lib/stream-timeline";

export function LlHlsVideo({
  source,
  className,
  controls = false,
  annotated = false,
  aspectRatio = 16 / 9,
  children,
  onTimeline,
  onVideoElement,
  onSurfaceElement,
  onPlaybackMetrics,
}: {
  source: string;
  className?: string;
  controls?: boolean;
  annotated?: boolean;
  aspectRatio?: number;
  children?: ReactNode;
  onTimeline?: (timeline: StreamTimeline) => void;
  onVideoElement?: (element: HTMLVideoElement | null) => void;
  onSurfaceElement?: (element: HTMLDivElement | null) => void;
  onPlaybackMetrics?: (metrics: {
    liveDelayMs: number | null;
    sourceTimeMs: number | null;
    rebufferCount: number;
    droppedVideoFrames: number;
  }) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const playerRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const callbackRef = useRef(onTimeline);
  const elementCallbackRef = useRef(onVideoElement);
  const metricsCallbackRef = useRef(onPlaybackMetrics);
  const normalizedAspectRatio =
    Number.isFinite(aspectRatio) && aspectRatio > 0 ? aspectRatio : 16 / 9;
  const fullscreenStageStyle = isFullscreen
    ? {
        width: `min(100vw, ${normalizedAspectRatio * 100}vh)`,
        height: `min(100vh, ${100 / normalizedAspectRatio}vw)`,
      }
    : undefined;

  useEffect(() => {
    callbackRef.current = onTimeline;
    elementCallbackRef.current = onVideoElement;
    metricsCallbackRef.current = onPlaybackMetrics;
  }, [onPlaybackMetrics, onTimeline, onVideoElement]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === playerRef.current);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () =>
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    elementCallbackRef.current?.(video);
    const playlist = rtspToHlsUrl(source, annotated);
    let retryTimer = 0;
    let resumeTimer = 0;
    let disposed = false;
    const canPlayHere = () =>
      !document.hidden &&
      (!document.fullscreenElement ||
        document.fullscreenElement === playerRef.current);
    const resumePlayback = () => {
      window.clearTimeout(resumeTimer);
      resumeTimer = window.setTimeout(() => {
        if (!disposed && canPlayHere() && video.paused) {
          playSafely(video);
        }
      }, 100);
    };

    if (Hls.isSupported()) {
      const hls = new Hls({
        lowLatencyMode: true,
        liveSyncDuration: 2,
        liveMaxLatencyDuration: 3,
        maxLiveSyncPlaybackRate: 1.05,
        backBufferLength: 10,
      });
      hlsRef.current = hls;
      hls.attachMedia(video);
      hls.on(Hls.Events.MEDIA_ATTACHED, () => hls.loadSource(playlist));
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        window.clearTimeout(retryTimer);
        resumePlayback();
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          window.clearTimeout(retryTimer);
          retryTimer = window.setTimeout(() => {
            if (disposed) return;
            hls.loadSource(playlist);
            hls.startLoad();
          }, 750);
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError();
        } else {
          hls.destroy();
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = playlist;
      resumePlayback();
    }

    let frameRequest = 0;
    let rebufferCount = 0;
    const countRebuffer = () => {
      rebufferCount += 1;
    };
    video.addEventListener("waiting", countRebuffer);
    video.addEventListener("stalled", countRebuffer);
    video.addEventListener("canplay", resumePlayback);
    document.addEventListener("visibilitychange", resumePlayback);
    document.addEventListener("fullscreenchange", resumePlayback);
    const metricsTimer = window.setInterval(() => {
      const playingDate = hlsRef.current?.playingDate ?? null;
      const quality = video.getVideoPlaybackQuality?.();
      metricsCallbackRef.current?.({
        liveDelayMs: Number.isFinite(hlsRef.current?.latency)
          ? (hlsRef.current?.latency ?? 0) * 1000
          : null,
        sourceTimeMs: playingDate?.getTime() ?? null,
        rebufferCount,
        droppedVideoFrames: quality?.droppedVideoFrames ?? 0,
      });
    }, 1000);
    const updateTimeline: VideoFrameRequestCallback = (_now, metadata) => {
      const playingDate = hlsRef.current?.playingDate ?? null;
      callbackRef.current?.({
        streamEpoch: null,
        sourceTimeMs: playingDate?.getTime() ?? null,
        mediaTimeSeconds: metadata.mediaTime,
      });
      frameRequest = video.requestVideoFrameCallback(updateTimeline);
    };
    if ("requestVideoFrameCallback" in video) {
      frameRequest = video.requestVideoFrameCallback(updateTimeline);
    }

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      window.clearTimeout(resumeTimer);
      if (frameRequest) video.cancelVideoFrameCallback(frameRequest);
      window.clearInterval(metricsTimer);
      video.removeEventListener("waiting", countRebuffer);
      video.removeEventListener("stalled", countRebuffer);
      video.removeEventListener("canplay", resumePlayback);
      document.removeEventListener("visibilitychange", resumePlayback);
      document.removeEventListener("fullscreenchange", resumePlayback);
      hlsRef.current?.destroy();
      hlsRef.current = null;
      video.removeAttribute("src");
      video.load();
      elementCallbackRef.current?.(null);
    };
  }, [annotated, source]);

  const toggleFullscreen = async () => {
    const player = playerRef.current;
    if (!player) return;
    if (document.fullscreenElement === player) {
      await document.exitFullscreen();
    } else {
      await player.requestFullscreen();
    }
  };

  return (
    <div
      ref={playerRef}
      className="group/video absolute inset-0 flex items-center justify-center bg-black"
    >
      <div
        ref={onSurfaceElement}
        className={`relative overflow-hidden bg-black ${isFullscreen ? "" : "h-full w-full"}`}
        style={fullscreenStageStyle}
      >
        <video
          ref={videoRef}
          className={className}
          controls={controls}
          muted
          autoPlay
          playsInline
        />
        {children}
        <button
          type="button"
          title={isFullscreen ? "Thu nhỏ video" : "Xem toàn màn hình"}
          aria-label={isFullscreen ? "Thu nhỏ video" : "Xem toàn màn hình"}
          onClick={(event) => {
            event.stopPropagation();
            void toggleFullscreen();
          }}
          className="absolute bottom-3 right-3 z-30 rounded-md border border-white/20 bg-slate-950/75 p-2 text-white shadow-lg backdrop-blur transition hover:bg-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lime-400"
        >
          {isFullscreen ? (
            <Minimize2 className="size-4" />
          ) : (
            <Maximize2 className="size-4" />
          )}
        </button>
      </div>
    </div>
  );
}

function playSafely(video: HTMLVideoElement): void {
  void video.play().catch((error: unknown) => {
    const name =
      error instanceof DOMException
        ? error.name
        : typeof error === "object" && error !== null && "name" in error
          ? String(error.name)
          : "";
    if (name === "AbortError" || name === "NotAllowedError") return;
    console.error("Video playback failed", error);
  });
}
