"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import { getStoredSession, setLastProjectId } from "../../../lib/session";
import type { ProjectDetail, ProjectTrack, SpeechFilterArtifact, SpeechFilterCut, TranscriptSegment } from "../../../lib/types";

type TranscriptStatus = "pending" | "processing" | "completed" | "error" | "not_applicable";
const MEDIA_BLOB_CACHE_NAME = "bestshotai-track-media-v1";
const mediaObjectUrlCache = new Map<string, string>();
const mediaObjectUrlPromiseCache = new Map<string, Promise<string>>();

function formatDate(value?: string | null): string {
  if (!value) return "Unknown date";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDayLabel(value?: string | null): string {
  if (!value) return "Unknown day";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function getDayGroupKey(value?: string | null): string {
  if (!value) return "unknown-day";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "unknown-day";
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function formatDuration(seconds: number): string {
  return formatTime(seconds || 0);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundToMillis(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function formatSpeechFilterReason(value: string): string {
  return value
    .split("+")
    .flatMap((part) => part.split("_"))
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" + ");
}

function normalizeTranscriptBackfillError(error: unknown): string {
  const text = String((error as Error).message || error || "").trim();
  if (text === "Not Found") {
    return "Transcript backfill endpoint is unavailable. Restart the backend so the latest /transcribe-missing route is loaded.";
  }
  return text || "Transcript processing request failed.";
}

function getTranscriptText(track: ProjectTrack): string | null {
  const text = track.transcription?.text;
  if (typeof text !== "string") return null;
  const normalized = text.trim();
  return normalized.length > 0 ? normalized : null;
}

function getTranscriptSegments(track: ProjectTrack): TranscriptSegment[] {
  return Array.isArray(track.transcription?.segments) ? track.transcription?.segments || [] : [];
}

function hasTranscript(track: ProjectTrack): boolean {
  if (getTranscriptText(track)) return true;
  return Array.isArray(track.transcription?.words) && track.transcription!.words!.length > 0;
}

function getTrackTranscriptStatus(track: ProjectTrack): TranscriptStatus | null {
  if (hasTranscript(track)) return "completed";
  const status = track.metadata?.transcript_status;
  if (
    status === "pending" ||
    status === "processing" ||
    status === "completed" ||
    status === "error" ||
    status === "not_applicable"
  ) {
    return status;
  }
  return null;
}

function isTranscriptProcessing(track: ProjectTrack): boolean {
  const status = getTrackTranscriptStatus(track);
  return status === "pending" || status === "processing";
}

function getSpeechFilterStatus(track: ProjectTrack): string | null {
  const status = track.metadata?.speech_filter_status;
  return typeof status === "string" && status.trim() ? status : null;
}

function normalizeEditableCuts(cuts: SpeechFilterCut[]): SpeechFilterCut[] {
  return [...cuts]
    .map((cut) => ({
      ...cut,
      start: roundToMillis(Math.max(0, cut.start)),
      end: roundToMillis(Math.max(cut.start, cut.end)),
      duration: roundToMillis(Math.max(0, cut.end - cut.start)),
    }))
    .sort((left, right) => left.start - right.start);
}

function isTrackTranscribable(track: ProjectTrack): boolean {
  return track.type === "video" || track.type === "audio";
}

function projectHasProcessingTranscripts(project: ProjectDetail | null): boolean {
  return Boolean(project?.tracks.some((track) => {
    const status = getTrackTranscriptStatus(track);
    return status === "pending" || status === "processing";
  }));
}

function shouldRequestTranscriptBackfill(project: ProjectDetail | null): boolean {
  if (!project) return false;
  return project.tracks.some((track) => {
    if (!isTrackTranscribable(track)) return false;
    const status = getTrackTranscriptStatus(track);
    if (status === "completed" || status === "processing" || status === "not_applicable") {
      return false;
    }
    return !hasTranscript(track);
  });
}

function sortChronologically(tracks: ProjectTrack[]): ProjectTrack[] {
  return [...tracks].sort((left, right) => {
    const leftTime = left.recorded_at ? new Date(left.recorded_at).getTime() : Number.POSITIVE_INFINITY;
    const rightTime = right.recorded_at ? new Date(right.recorded_at).getTime() : Number.POSITIVE_INFINITY;
    if (leftTime !== rightTime) return leftTime - rightTime;
    return left.position - right.position;
  });
}

function isTrackHidden(track: ProjectTrack): boolean {
  return track.status === "hidden" || track.excluded === true;
}

function getTrackSize(track: ProjectTrack): number | null {
  const raw = track.metadata?.size;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function isDuplicateSelectedFile(file: File, tracks: ProjectTrack[]): boolean {
  const normalizedName = file.name.trim().toLowerCase();
  return tracks.some((track) => {
    const trackName = String(track.filename || "").trim().toLowerCase();
    const trackSize = getTrackSize(track);
    return trackName === normalizedName && trackSize === file.size;
  });
}

async function getCachedMediaResponse(url: string): Promise<Response | null> {
  if (typeof window === "undefined" || !("caches" in window)) return null;
  const cache = await window.caches.open(MEDIA_BLOB_CACHE_NAME);
  return cache.match(url);
}

async function putCachedMediaResponse(url: string, response: Response): Promise<void> {
  if (typeof window === "undefined" || !("caches" in window)) return;
  const cache = await window.caches.open(MEDIA_BLOB_CACHE_NAME);
  await cache.put(url, response);
}

async function loadAuthedMediaObjectUrl(url: string, token: string): Promise<string> {
  const existing = mediaObjectUrlCache.get(url);
  if (existing) return existing;

  const pending = mediaObjectUrlPromiseCache.get(url);
  if (pending) return pending;

  const task = (async () => {
    const cachedResponse = await getCachedMediaResponse(url);
    if (cachedResponse?.ok) {
      const cachedObjectUrl = URL.createObjectURL(await cachedResponse.blob());
      mediaObjectUrlCache.set(url, cachedObjectUrl);
      return cachedObjectUrl;
    }

    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    });
    if (!response.ok) throw new Error(`Media ${response.status}`);

    await putCachedMediaResponse(url, response.clone());
    const objectUrl = URL.createObjectURL(await response.blob());
    mediaObjectUrlCache.set(url, objectUrl);
    return objectUrl;
  })();

  mediaObjectUrlPromiseCache.set(url, task);
  try {
    return await task;
  } finally {
    mediaObjectUrlPromiseCache.delete(url);
  }
}

/**
 * Fetch the URL with the auth Bearer token and return an object URL
 * that can be assigned to a <video src>. Uses Cache Storage to persist
 * previously fetched previews across page reloads.
 */
function useAuthedBlobUrl(
  url: string,
  token: string | undefined,
): { blobUrl: string | null; loading: boolean; error: string | null } {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url || !token) {
      setBlobUrl(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    loadAuthedMediaObjectUrl(url, token)
      .then((objectUrl) => {
        if (cancelled) return;
        setBlobUrl(objectUrl);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setBlobUrl(null);
          setLoading(false);
          setError(err instanceof Error ? err.message : "Unable to load media");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [url, token]);

  return { blobUrl, loading, error };
}

function TranscriptPanel({
  track,
  speechFilter,
  speechFilterLoading,
  speechFilterError,
  onGenerateSpeechFilter,
}: {
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
}) {
  const transcriptStatus = getTrackTranscriptStatus(track);

  if (transcriptStatus === "pending" || transcriptStatus === "processing") {
    return (
      <span className="clipTag clipTagProcessing">
        <span className="spinner" aria-hidden="true" />
        transcript processing
      </span>
    );
  }

  if (transcriptStatus === "error") {
    return <span className="clipTag clipTagMuted">transcript failed</span>;
  }

  if (!hasTranscript(track)) {
    return <span className="clipTag clipTagMuted">no transcript</span>;
  }

  const transcriptText = getTranscriptText(track);
  const segments = getTranscriptSegments(track);
  const speechFilterStatus = getSpeechFilterStatus(track);
  const hasSpeechFilter = Boolean(speechFilter);

  return (
    <div className="clipTranscriptStack">
      <details className="clipTranscriptDetails">
        <summary className="clipTag clipTagActive">transcript</summary>
        <div className="clipTranscriptPanel">
          {segments.length > 0 ? (
            <div className="clipTranscriptList">
              {segments.map((segment, index) => (
                <div key={`${segment.start}-${segment.end}-${index}`} className="clipTranscriptRow">
                  <span className="clipTranscriptTime">
                    {formatTime(segment.start)} - {formatTime(segment.end)}
                  </span>
                  <span>{segment.text}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted clipTranscriptText">{transcriptText}</p>
          )}
        </div>
      </details>

      <div className="clipSpeechFilter">
        <button
          type="button"
          className={`clipTag clipTagButton ${speechFilterLoading ? "clipTagProcessing" : "clipTagActive"}`}
          disabled={speechFilterLoading}
          onClick={() => onGenerateSpeechFilter(track)}
        >
          {speechFilterLoading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              filtering speech
            </>
          ) : hasSpeechFilter ? (
            "rerun filter speech"
          ) : speechFilterStatus === "completed" ? (
            "load speech cuts"
          ) : (
            "filter speech"
          )}
        </button>
        {speechFilterError ? <p className="clipSpeechFilterError">{speechFilterError}</p> : null}
        {speechFilter ? (
          <details className="clipSpeechFilterDetails">
            <summary className={`clipTag ${speechFilter.cuts.length ? "clipTagWarn" : "clipTagMuted"}`}>
              {speechFilter.cuts.length ? `suggested cuts (${speechFilter.cuts.length})` : "no cuts suggested"}
            </summary>
            <div className="clipSpeechFilterPanel">
              <p className="muted clipSpeechFilterSummary">{speechFilter.summary}</p>
              {speechFilter.cuts.length > 0 ? (
                <div className="clipSpeechFilterList">
                  {speechFilter.cuts.map((cut: SpeechFilterCut, index: number) => (
                    <div key={`${cut.start}-${cut.end}-${index}`} className="clipSpeechFilterRow">
                      <div className="clipSpeechFilterHeader">
                        <strong>{formatTime(cut.start)} - {formatTime(cut.end)}</strong>
                        <span className="muted">{formatSpeechFilterReason(cut.reason)}</span>
                      </div>
                      {cut.transcript ? <span className="clipSpeechFilterSnippet">“{cut.transcript}”</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function ClipCard({
  projectId,
  projectCreatedAt,
  token,
  track,
  speechFilter,
  speechFilterLoading,
  speechFilterError,
  onGenerateSpeechFilter,
  onOpen,
  onHide,
  hiding,
}: {
  projectId: string;
  projectCreatedAt?: string;
  token: string | undefined;
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onOpen: (track: ProjectTrack) => void;
  onHide: (trackId: string) => void;
  hiding: boolean;
}) {
  const rawUrl = api.getTrackMediaUrl(projectId, track.id);
  const { blobUrl, loading, error } = useAuthedBlobUrl(rawUrl, token);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [posterReady, setPosterReady] = useState(false);
  const clipCreatedAt = track.recorded_at || projectCreatedAt;

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !blobUrl) return;
    setPosterReady(false);
    const onLoaded = () => {
      try {
        v.currentTime = 0.5;
      } catch {
        // ignore
      }
    };
    const onSeeked = () => setPosterReady(true);
    v.addEventListener("loadedmetadata", onLoaded);
    v.addEventListener("seeked", onSeeked);
    return () => {
      v.removeEventListener("loadedmetadata", onLoaded);
      v.removeEventListener("seeked", onSeeked);
    };
  }, [blobUrl]);

  return (
    <article className="clipCard">
      <div className="clipMediaWrap">
        <button
          type="button"
          className="clipHideButton"
          aria-label={`Remove ${track.filename} from project`}
          disabled={hiding}
          onClick={(event) => {
            event.stopPropagation();
            onHide(track.id);
          }}
        >
          ×
        </button>
        <button type="button" className="clipPreviewButton" onClick={() => onOpen(track)}>
          {blobUrl ? (
            <video
              ref={videoRef}
              className="clipMedia"
              src={blobUrl}
              preload="metadata"
              playsInline
              muted
            />
          ) : (
            <div className="clipMedia clipMediaLoading" />
          )}
          {!blobUrl && error ? (
            <span className="clipPreviewPlaceholder">ERR</span>
          ) : !posterReady && (
            <span className="clipPreviewPlaceholder">{track.filename.slice(0, 2).toUpperCase()}</span>
          )}
          <span className="clipPreviewPlay">{error ? "Unavailable" : loading ? "Loading" : "▶ Play"}</span>
        </button>
      </div>
      <div className="clipMeta">
        <div className="stack" style={{ gap: 4 }}>
          <strong className="clipFileName" title={track.filename}>{track.filename}</strong>
          <span className="muted">{formatDate(clipCreatedAt)}</span>
          <span className="muted">Duration: {formatDuration(track.duration)}</span>
        </div>
        <TranscriptPanel
          track={track}
          speechFilter={speechFilter}
          speechFilterLoading={speechFilterLoading}
          speechFilterError={speechFilterError}
          onGenerateSpeechFilter={onGenerateSpeechFilter}
        />
      </div>
    </article>
  );
}

function SpeechFilterEditor({
  src,
  track,
  artifact,
  saving,
  saveError,
  onSave,
}: {
  src: string;
  track: ProjectTrack;
  artifact: SpeechFilterArtifact;
  saving: boolean;
  saveError?: string | null;
  onSave: (cuts: SpeechFilterCut[]) => Promise<void>;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [editableCuts, setEditableCuts] = useState<SpeechFilterCut[]>(() => normalizeEditableCuts(artifact.cuts));
  const [selectedCutIndex, setSelectedCutIndex] = useState(0);
  const [dragState, setDragState] = useState<{ cutIndex: number; edge: "start" | "end" } | null>(null);
  const skipInFlightRef = useRef(false);
  const duration = Math.max(track.duration || 0, 0.1);

  useEffect(() => {
    setEditableCuts(normalizeEditableCuts(artifact.cuts));
    setSelectedCutIndex(0);
  }, [artifact]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const syncTime = () => setCurrentTime(video.currentTime || 0);
    const syncPlay = () => setIsPlaying(true);
    const syncPause = () => setIsPlaying(false);

    video.addEventListener("timeupdate", syncTime);
    video.addEventListener("play", syncPlay);
    video.addEventListener("pause", syncPause);
    video.addEventListener("loadedmetadata", syncTime);
    return () => {
      video.removeEventListener("timeupdate", syncTime);
      video.removeEventListener("play", syncPlay);
      video.removeEventListener("pause", syncPause);
      video.removeEventListener("loadedmetadata", syncTime);
    };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const maybeSkipCut = () => {
      if (video.paused || dragState || skipInFlightRef.current) return;
      const current = video.currentTime || 0;
      const activeCut = editableCuts.find((cut) => current >= cut.start && current < cut.end - 0.01);
      if (!activeCut) return;

      skipInFlightRef.current = true;
      const targetTime = clamp(activeCut.end + 0.01, 0, duration);
      video.currentTime = targetTime;
      setCurrentTime(targetTime);
      window.setTimeout(() => {
        skipInFlightRef.current = false;
      }, 0);
    };

    video.addEventListener("timeupdate", maybeSkipCut);
    video.addEventListener("play", maybeSkipCut);
    return () => {
      video.removeEventListener("timeupdate", maybeSkipCut);
      video.removeEventListener("play", maybeSkipCut);
    };
  }, [dragState, duration, editableCuts]);

  useEffect(() => {
    if (!dragState) return;

    const handlePointerMove = (event: PointerEvent) => {
      const rect = timelineRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0) return;

      const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      const nextTime = roundToMillis(ratio * duration);
      setEditableCuts((current) => {
        const next = current.map((cut) => ({ ...cut }));
        const cut = next[dragState.cutIndex];
        if (!cut) return current;

        if (dragState.edge === "start") {
          const previousEnd = dragState.cutIndex > 0 ? next[dragState.cutIndex - 1].end : 0;
          cut.start = roundToMillis(clamp(nextTime, previousEnd, cut.end - 0.08));
        } else {
          const nextStart = dragState.cutIndex < next.length - 1 ? next[dragState.cutIndex + 1].start : duration;
          cut.end = roundToMillis(clamp(nextTime, cut.start + 0.08, nextStart));
        }
        cut.duration = roundToMillis(cut.end - cut.start);
        return next;
      });
    };

    const handlePointerUp = () => setDragState(null);

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    document.body.style.cursor = "ew-resize";
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      document.body.style.cursor = "";
    };
  }, [dragState, duration]);

  const activeCut = editableCuts.find((cut) => currentTime >= cut.start && currentTime <= cut.end) || null;

  const seekTo = (time: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = clamp(time, 0, duration);
    setCurrentTime(video.currentTime);
  };

  const togglePlayback = async () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      await video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  };

  const handleTimelineClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!timelineRef.current) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    seekTo(ratio * duration);
  };

  return (
    <div className="speechEditor">
      <div className="speechEditorVideoShell">
        <video
          ref={videoRef}
          className="speechEditorVideo"
          src={src}
          preload="metadata"
          playsInline
        />
        {activeCut ? (
          <div className="speechEditorVideoNotice">
            Suggested cut: {formatSpeechFilterReason(activeCut.reason)}
          </div>
        ) : null}
      </div>

      <div className="speechEditorControls">
        <button type="button" className="btn secondary" onClick={togglePlayback}>
          {isPlaying ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          className="btn secondary"
          onClick={() => {
            const selected = editableCuts[selectedCutIndex];
            if (selected) seekTo(selected.start);
          }}
          disabled={!editableCuts[selectedCutIndex]}
        >
          Jump To Cut
        </button>
        <div className="speechEditorTime">
          {formatTime(currentTime)} / {formatDuration(duration)}
        </div>
        <button type="button" className="btn" disabled={saving} onClick={() => onSave(editableCuts)}>
          {saving ? "Saving..." : "Save Cuts"}
        </button>
      </div>

      <div className="speechEditorTimelineWrap">
        <div ref={timelineRef} className="speechEditorTimeline" onClick={handleTimelineClick}>
          <div className="speechEditorTimelineBase" />
          <div className="speechEditorTimelinePlayed" style={{ width: `${(currentTime / duration) * 100}%` }} />
          {editableCuts.map((cut, index) => {
            const left = (cut.start / duration) * 100;
            const width = ((cut.end - cut.start) / duration) * 100;
            const selected = index === selectedCutIndex;
            return (
              <button
                key={`${cut.start}-${cut.end}-${index}`}
                type="button"
                className={`speechEditorCut ${selected ? "speechEditorCutSelected" : ""}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedCutIndex(index);
                  seekTo(cut.start);
                }}
              >
                <span
                  className="speechEditorHandle speechEditorHandleStart"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setSelectedCutIndex(index);
                    setDragState({ cutIndex: index, edge: "start" });
                  }}
                />
                <span className="speechEditorCutLabel">{index + 1}</span>
                <span
                  className="speechEditorHandle speechEditorHandleEnd"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setSelectedCutIndex(index);
                    setDragState({ cutIndex: index, edge: "end" });
                  }}
                />
              </button>
            );
          })}
          <div className="speechEditorPlayhead" style={{ left: `${(currentTime / duration) * 100}%` }} />
        </div>
      </div>

      <div className="speechEditorCutsPanel">
        <div className="speechEditorSummary">
          <strong>{artifact.summary}</strong>
          <span className="muted">Drag the left or right edge of each red block to adjust the cut.</span>
          {saveError ? <span className="speechEditorError">{saveError}</span> : null}
        </div>
        <div className="speechEditorCutList">
          {editableCuts.map((cut, index) => (
            <button
              key={`${cut.start}-${cut.end}-${index}-row`}
              type="button"
              className={`speechEditorCutRow ${index === selectedCutIndex ? "speechEditorCutRowSelected" : ""}`}
              onClick={() => {
                setSelectedCutIndex(index);
                seekTo(cut.start);
              }}
            >
              <strong>{formatTime(cut.start)} - {formatTime(cut.end)}</strong>
              <span>{formatSpeechFilterReason(cut.reason)}</span>
              {cut.transcript ? <span className="muted">“{cut.transcript}”</span> : null}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function PreviewModal({
  projectId,
  token,
  projectCreatedAt,
  track,
  speechFilter,
  speechFilterLoading,
  speechFilterSaving,
  speechFilterError,
  onGenerateSpeechFilter,
  onSaveSpeechFilter,
  onClose,
}: {
  projectId: string;
  token: string | undefined;
  projectCreatedAt?: string;
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterSaving: boolean;
  speechFilterError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onSaveSpeechFilter: (track: ProjectTrack, cuts: SpeechFilterCut[]) => Promise<void>;
  onClose: () => void;
}) {
  const { blobUrl, loading, error } = useAuthedBlobUrl(api.getTrackMediaUrl(projectId, track.id), token);

  return (
    <div className="previewModalBackdrop" onClick={onClose}>
      <div className="previewModal" onClick={(event) => event.stopPropagation()}>
        <div className="previewModalHead">
          <div className="stack" style={{ gap: 4 }}>
            <strong className="clipFileName">{track.filename}</strong>
            <span className="muted">
              {formatDate(track.recorded_at || projectCreatedAt)} • {formatDuration(track.duration)}
            </span>
          </div>
          <button type="button" className="previewModalClose" onClick={onClose}>×</button>
        </div>
        {blobUrl ? (
          speechFilter ? (
            <SpeechFilterEditor
              src={blobUrl}
              track={track}
              artifact={speechFilter}
              saving={speechFilterSaving}
              saveError={speechFilterError}
              onSave={(cuts) => onSaveSpeechFilter(track, cuts)}
            />
          ) : (
            <video
              className="previewModalVideo"
              src={blobUrl}
              controls
              autoPlay
              preload="metadata"
              playsInline
            />
          )
        ) : (
          <div className="previewModalVideo clipMediaLoading">
            <span className="muted">{loading ? "Loading preview..." : error || "Preview unavailable"}</span>
          </div>
        )}
        <TranscriptPanel
          track={track}
          speechFilter={speechFilter}
          speechFilterLoading={speechFilterLoading}
          speechFilterError={speechFilterError}
          onGenerateSpeechFilter={onGenerateSpeechFilter}
        />
      </div>
    </div>
  );
}

function FinalVideo({ projectId, token }: { projectId: string; token: string | undefined }) {
  const { blobUrl, loading, error } = useAuthedBlobUrl(api.getOutputUrl(projectId), token);
  if (!blobUrl) {
    return (
      <div className="videoPlayerEmpty">
        <span className="muted">{loading ? "Loading final video..." : error || "Final video unavailable."}</span>
      </div>
    );
  }
  return (
    <video
      className="projectFinalVideo"
      src={blobUrl}
      controls
      preload="metadata"
      playsInline
    />
  );
}

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = String(params?.id || "");
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [addingTracks, setAddingTracks] = useState(false);
  const [groupByDay, setGroupByDay] = useState(false);
  const [hidingTrackId, setHidingTrackId] = useState<string | null>(null);
  const [activeTrack, setActiveTrack] = useState<ProjectTrack | null>(null);
  const [speechFilters, setSpeechFilters] = useState<Record<string, SpeechFilterArtifact | null>>({});
  const [speechFilterLoadingIds, setSpeechFilterLoadingIds] = useState<Record<string, boolean>>({});
  const [speechFilterSavingIds, setSpeechFilterSavingIds] = useState<Record<string, boolean>>({});
  const [speechFilterErrors, setSpeechFilterErrors] = useState<Record<string, string | null>>({});
  const [token, setToken] = useState<string | undefined>(undefined);
  const requestedTranscriptBackfill = useRef(false);
  const addTracksInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.replace(`/login?next=/projects/${projectId}`);
      return;
    }
    setToken(session.access_token);
    if (!projectId) return;
    setLastProjectId(projectId);

    api.getProject(projectId, session.access_token)
      .then((response) => {
        setProject(response.project);
        setMessage(null);
      })
      .catch((error) => {
        const text = String((error as Error).message || error);
        setMessage(text);
        if (text.toLowerCase().includes("unauthorized")) {
          router.replace(`/login?next=/projects/${projectId}`);
        }
      })
      .finally(() => setLoading(false));
  }, [projectId, router]);

  useEffect(() => {
    requestedTranscriptBackfill.current = false;
    setSpeechFilters({});
    setSpeechFilterLoadingIds({});
    setSpeechFilterSavingIds({});
    setSpeechFilterErrors({});
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !token || !project || !shouldRequestTranscriptBackfill(project)) return;
    if (requestedTranscriptBackfill.current) return;

    requestedTranscriptBackfill.current = true;
    api.requestMissingTranscripts(projectId, token)
      .then((response) => {
        setProject(response.project);
        setMessage(null);
      })
      .catch((error) => {
        requestedTranscriptBackfill.current = false;
        setMessage(normalizeTranscriptBackfillError(error));
      });
  }, [project, projectId, token]);

  useEffect(() => {
    if (!projectId || !token || !projectHasProcessingTranscripts(project)) return;

    const intervalId = window.setInterval(() => {
      api.getProject(projectId, token)
        .then((response) => {
          setProject(response.project);
          setMessage(null);
        })
        .catch((error) => setMessage(String((error as Error).message || error)));
    }, 3000);

    return () => window.clearInterval(intervalId);
  }, [project, projectId, token]);

  useEffect(() => {
    if (!activeTrack || !project || !token) return;
    if (speechFilters[activeTrack.id]) return;
    if (getSpeechFilterStatus(activeTrack) !== "completed") return;
    if (speechFilterLoadingIds[activeTrack.id]) return;
    if (speechFilterErrors[activeTrack.id]) return;

    setSpeechFilterLoadingIds((current) => ({ ...current, [activeTrack.id]: true }));
    api.getTrackSpeechFilter(project.id, activeTrack.id, token)
      .then((artifact) => {
        setSpeechFilters((current) => ({ ...current, [activeTrack.id]: artifact }));
      })
      .catch((error) => {
        setSpeechFilterErrors((current) => ({ ...current, [activeTrack.id]: String((error as Error).message || error) }));
      })
      .finally(() => {
        setSpeechFilterLoadingIds((current) => ({ ...current, [activeTrack.id]: false }));
      });
  }, [activeTrack, project, speechFilters, speechFilterErrors, speechFilterLoadingIds, token]);

  const handleAddTracks = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files || []);
    event.target.value = "";
    if (selectedFiles.length === 0 || !token || !project) return;

    const duplicateFiles = selectedFiles.filter((file) => isDuplicateSelectedFile(file, project.tracks));
    const nextFiles = selectedFiles.filter((file) => !isDuplicateSelectedFile(file, project.tracks));
    if (nextFiles.length === 0) {
      setMessage("All selected clips are already in this project.");
      return;
    }

    try {
      setAddingTracks(true);
      setMessage(
        duplicateFiles.length
          ? `${duplicateFiles.length} duplicate clip${duplicateFiles.length === 1 ? "" : "s"} skipped.`
          : null
      );
      const response = await api.addTracksToProject(projectId, nextFiles, token);
      requestedTranscriptBackfill.current = false;
      setProject(response.project);
      if (response.message) {
        setMessage(response.message);
      }
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setAddingTracks(false);
    }
  };

  const handleHideTrack = async (trackId: string) => {
    if (!token || !project) return;
    try {
      setHidingTrackId(trackId);
      setMessage(null);
      await api.excludeTrack(project.id, trackId, token);
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      if (activeTrack?.id === trackId) {
        setActiveTrack(null);
      }
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setHidingTrackId(null);
    }
  };

  const handleGenerateSpeechFilter = async (track: ProjectTrack) => {
    if (!token || !project) return;

    setSpeechFilterLoadingIds((current) => ({ ...current, [track.id]: true }));
    setSpeechFilterErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const existingStatus = getSpeechFilterStatus(track);
      let artifact: SpeechFilterArtifact;
      if (existingStatus === "completed" && !speechFilters[track.id]) {
        try {
          artifact = await api.getTrackSpeechFilter(project.id, track.id, token);
        } catch (error) {
          const text = String((error as Error).message || error).toLowerCase();
          if (!text.includes("not found")) {
            throw error;
          }
          artifact = await api.generateTrackSpeechFilter(project.id, track.id, token);
        }
      } else {
        artifact = await api.generateTrackSpeechFilter(project.id, track.id, token);
      }
      setSpeechFilters((current) => ({ ...current, [track.id]: artifact }));
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setMessage(null);
    } catch (error) {
      const text = String((error as Error).message || error);
      setSpeechFilterErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
    } finally {
      setSpeechFilterLoadingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleSaveSpeechFilter = async (track: ProjectTrack, cuts: SpeechFilterCut[]) => {
    if (!token || !project) return;
    setSpeechFilterSavingIds((current) => ({ ...current, [track.id]: true }));
    setSpeechFilterErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const artifact = await api.updateTrackSpeechFilter(project.id, track.id, cuts, token);
      setSpeechFilters((current) => ({ ...current, [track.id]: artifact }));
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setMessage("Speech cuts saved.");
    } catch (error) {
      const text = String((error as Error).message || error);
      setSpeechFilterErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
    } finally {
      setSpeechFilterSavingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const sortedTracks = useMemo(
    () => sortChronologically((project?.tracks || []).filter((track) => !isTrackHidden(track))),
    [project?.tracks],
  );
  const dayGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; tracks: ProjectTrack[] }> = [];
    for (const track of sortedTracks) {
      const sourceTime = track.recorded_at || project?.created_at || null;
      const key = getDayGroupKey(sourceTime);
      const label = formatDayLabel(sourceTime);
      const existing = groups[groups.length - 1];
      if (existing && existing.key === key) {
        existing.tracks.push(track);
      } else {
        groups.push({ key, label, tracks: [track] });
      }
    }
    return groups;
  }, [project?.created_at, sortedTracks]);

  if (loading) {
    return (
      <section className="card">
        <p className="muted" style={{ padding: 24 }}>Loading project...</p>
      </section>
    );
  }

  if (!project) {
    return (
      <section className="card stack">
        <h1>Project</h1>
        <div className="notice">{message || "Project not found."}</div>
      </section>
    );
  }

  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card stack" style={{ gap: 16 }}>
        <div className="projectOverviewHead">
          <div className="stack" style={{ gap: 6 }}>
            <h1>{project.name}</h1>
            <span className="muted">Created {formatDate(project.created_at)}</span>
          </div>
          <span className="badge">{project.status}</span>
        </div>

        <div className="projectFinalPreview">
          <div className="stack" style={{ gap: 6 }}>
            <h2>Final Version</h2>
            <span className="muted">Play the latest rendered version of the project.</span>
          </div>
          {project.status === "completed" && project.output_path ? (
            <FinalVideo projectId={project.id} token={token} />
          ) : project.status === "error" ? (
            <div className="videoPlayerEmpty">
              <span className="muted">{project.error_message || "Rendering failed."}</span>
            </div>
          ) : (
            <div className="videoPlayerEmpty">
              <span className="muted">Final video is not ready yet.</span>
            </div>
          )}
        </div>
      </section>

      {message ? <div className="notice">{message}</div> : null}

      <section className="card stack" style={{ gap: 16 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div className="stack" style={{ gap: 4 }}>
            <h2>Clips ({sortedTracks.length})</h2>
            <span className="muted">
              Ordered chronologically from the earliest clip to the latest.
            </span>
            <div className="clipViewControls">
              <button
                type="button"
                className={`clipViewToggle ${groupByDay ? "clipViewToggleActive" : ""}`}
                onClick={() => setGroupByDay((current) => !current)}
              >
                {groupByDay ? "Grouped by day" : "Split by day"}
              </button>
            </div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <input
              ref={addTracksInputRef}
              type="file"
              accept="video/*"
              multiple
              hidden
              onChange={handleAddTracks}
            />
            <button
              type="button"
              className="btn secondary"
              disabled={addingTracks}
              onClick={() => addTracksInputRef.current?.click()}
            >
              {addingTracks ? "Adding..." : "Add Clips"}
            </button>
          </div>
        </div>

        {sortedTracks.length === 0 ? (
          <div className="notice">No clips in this project yet.</div>
        ) : groupByDay ? (
          <div className="clipDayGroups">
            {dayGroups.map((group) => (
              <section key={group.key} className="clipDaySection">
                <div className="clipDayHeader">
                  <strong>{group.label}</strong>
                  <span className="muted">{group.tracks.length} clip{group.tracks.length === 1 ? "" : "s"}</span>
                </div>
                <div className="clipGrid">
                  {group.tracks.map((track) => (
                    <ClipCard
                      key={track.id}
                      projectId={project.id}
                      projectCreatedAt={project.created_at}
                      token={token}
                      track={track}
                      speechFilter={speechFilters[track.id]}
                      speechFilterLoading={Boolean(speechFilterLoadingIds[track.id])}
                      speechFilterError={speechFilterErrors[track.id]}
                      onGenerateSpeechFilter={handleGenerateSpeechFilter}
                      onOpen={setActiveTrack}
                      onHide={handleHideTrack}
                      hiding={hidingTrackId === track.id}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <div className="clipGrid">
            {sortedTracks.map((track) => (
              <ClipCard
                key={track.id}
                projectId={project.id}
                projectCreatedAt={project.created_at}
                token={token}
                track={track}
                speechFilter={speechFilters[track.id]}
                speechFilterLoading={Boolean(speechFilterLoadingIds[track.id])}
                speechFilterError={speechFilterErrors[track.id]}
                onGenerateSpeechFilter={handleGenerateSpeechFilter}
                onOpen={setActiveTrack}
                onHide={handleHideTrack}
                hiding={hidingTrackId === track.id}
              />
            ))}
          </div>
        )}
      </section>

      {activeTrack ? (
        <PreviewModal
          projectId={project.id}
          token={token}
          projectCreatedAt={project.created_at}
          track={activeTrack}
          speechFilter={speechFilters[activeTrack.id]}
          speechFilterLoading={Boolean(speechFilterLoadingIds[activeTrack.id])}
          speechFilterSaving={Boolean(speechFilterSavingIds[activeTrack.id])}
          speechFilterError={speechFilterErrors[activeTrack.id]}
          onGenerateSpeechFilter={handleGenerateSpeechFilter}
          onSaveSpeechFilter={handleSaveSpeechFilter}
          onClose={() => setActiveTrack(null)}
        />
      ) : null}
    </div>
  );
}
