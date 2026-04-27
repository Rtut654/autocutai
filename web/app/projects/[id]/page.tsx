"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import { getStoredSession, setLastProjectId } from "../../../lib/session";
import type { ProjectDetail, ProjectTrack, SpeechFilterArtifact, SpeechFilterCut, TranscriptSegment, VisualPlanArtifact, VisualPlanPart, ZoomPreviewBeat } from "../../../lib/types";

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

function formatOrientation(value?: ProjectTrack["orientation"]): string | null {
  if (!value || value === "unknown") return null;
  if (value === "horizontal") return "Horizontal";
  if (value === "vertical") return "Vertical";
  if (value === "square") return "Square";
  return null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundToMillis(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function easeOutCubic(value: number): number {
  const clamped = clamp(value, 0, 1);
  return 1 - Math.pow(1 - clamped, 3);
}

function easeInOutCubic(value: number): number {
  const clamped = clamp(value, 0, 1);
  return clamped < 0.5
    ? 4 * clamped * clamped * clamped
    : 1 - Math.pow(-2 * clamped + 2, 3) / 2;
}

function filenameNaturalKey(value: string): Array<string | number> {
  return value
    .toLowerCase()
    .split(/(\d+)/)
    .filter(Boolean)
    .map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

function formatSpeechFilterReason(value: string): string {
  return value
    .split("+")
    .flatMap((part) => part.split("_"))
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" + ");
}

const STOP_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "how",
  "i", "if", "in", "into", "is", "it", "its", "not", "of", "on", "or", "so", "that", "the",
  "their", "then", "there", "they", "this", "to", "up", "was", "we", "what", "when", "which",
  "with", "you", "your", "once", "usually", "again",
]);

function compactSentence(text: string, maxWords = 8): string {
  const words = text
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean);
  if (words.length <= maxWords) return words.join(" ");
  return `${words.slice(0, maxWords).join(" ")}...`;
}

function extractKeywords(text: string, maxKeywords = 3): string[] {
  const counts = new Map<string, number>();
  const ordered: string[] = [];
  text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 4 && !STOP_WORDS.has(token))
    .forEach((token) => {
      if (!counts.has(token)) ordered.push(token);
      counts.set(token, (counts.get(token) || 0) + 1);
    });
  return ordered
    .sort((left, right) => {
      const scoreDelta = (counts.get(right) || 0) - (counts.get(left) || 0);
      if (scoreDelta !== 0) return scoreDelta;
      return right.length - left.length;
    })
    .slice(0, maxKeywords)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1));
}

type VisualAnimationSpec = {
  theme: "problem" | "solution" | "steps" | "insight";
  kicker: string;
  headline: string;
  chips: string[];
  align: "left" | "right";
  motif: "conversation" | "steps" | "path" | "spark";
};

function buildAnimationSpec(part: VisualPlanPart, partIndex: number): VisualAnimationSpec {
  const text = (part.text || "").trim();
  const prompt = (part.prompt || "").trim();
  const source = `${prompt} ${text}`.toLowerCase();
  const chips = extractKeywords(text || prompt, 3);
  const headline = (chips.slice(0, 2).join(" / ") || compactSentence(text || prompt, 3)).replace(/\.\.\.$/, "");

  if (source.includes("conversation") || source.includes("listen") || source.includes("speaker")) {
    return {
      theme: "insight",
      kicker: "Conversation",
      headline,
      chips: chips.length ? chips.slice(0, 2) : ["Listen", "Reply"],
      align: partIndex % 2 === 0 ? "left" : "right",
      motif: "conversation",
    };
  }

  if (source.includes("first") || source.includes("step") || source.includes("then")) {
    return {
      theme: "steps",
      kicker: "Sequence",
      headline,
      chips: chips.length ? chips.slice(0, 2) : ["Step", "Next"],
      align: partIndex % 2 === 0 ? "left" : "right",
      motif: "steps",
    };
  }

  if (source.includes("problem") && source.includes("solution")) {
    return {
      theme: "problem",
      kicker: "Problem -> fix",
      headline,
      chips: chips.length ? chips.slice(0, 2) : ["Problem", "Solution"],
      align: partIndex % 2 === 0 ? "left" : "right",
      motif: "path",
    };
  }

  if (source.includes("solution") || source.includes("strategy") || source.includes("important")) {
    return {
      theme: "solution",
      kicker: "Key takeaway",
      headline,
      chips: chips.length ? chips.slice(0, 2) : ["Strategy", "Action"],
      align: partIndex % 2 === 0 ? "right" : "left",
      motif: "path",
    };
  }

  return {
    theme: "insight",
    kicker: "Visual emphasis",
    headline,
    chips: chips.length ? chips.slice(0, 2) : ["Focus", "Point"],
    align: partIndex % 2 === 0 ? "left" : "right",
    motif: "spark",
  };
}

function AnimatedMotif({
  spec,
  progress,
}: {
  spec: VisualAnimationSpec;
  progress: number;
}) {
  const orbit = easeInOutCubic(clamp(progress, 0, 1));
  const pulse = 0.96 + Math.sin(progress * Math.PI * 2) * 0.035;
  const draw = easeOutCubic(clamp(progress / 0.7, 0, 1));

  if (spec.motif === "conversation") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <g transform={`translate(${12 * (1 - orbit)} ${18 * (1 - orbit)})`} opacity={0.92}>
          <rect x="34" y="22" width="112" height="56" rx="18" fill="rgba(125,177,255,0.18)" stroke="rgba(156,195,255,0.72)" strokeWidth="3" />
          <path d="M74 78 L64 98 L96 82" fill="rgba(125,177,255,0.18)" stroke="rgba(156,195,255,0.72)" strokeWidth="3" strokeLinejoin="round" />
        </g>
        <g transform={`translate(${220 - 14 * (1 - orbit)} ${58 + 16 * (1 - orbit)})`} opacity={0.9}>
          <rect x="-84" y="0" width="102" height="48" rx="16" fill="rgba(113,255,211,0.16)" stroke="rgba(131,255,218,0.78)" strokeWidth="3" />
          <path d="M-20 48 L-2 68 L-28 56" fill="rgba(113,255,211,0.16)" stroke="rgba(131,255,218,0.78)" strokeWidth="3" strokeLinejoin="round" />
        </g>
        <g transform={`translate(72 126) scale(${pulse.toFixed(4)})`}>
          <circle cx="0" cy="0" r="22" fill="rgba(255,255,255,0.9)" />
          <rect x="-18" y="26" width="52" height="34" rx="17" fill="rgba(255,255,255,0.9)" />
        </g>
        <g transform={`translate(238 126) scale(${(1.02 - (pulse - 0.96)).toFixed(4)})`}>
          <circle cx="0" cy="0" r="22" fill="rgba(255,255,255,0.9)" />
          <rect x="-34" y="26" width="52" height="34" rx="17" fill="rgba(255,255,255,0.9)" />
        </g>
        <path
          d="M120 132 C146 110, 174 110, 202 132"
          fill="none"
          stroke="rgba(255,255,255,0.85)"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray="120"
          strokeDashoffset={120 - 120 * draw}
        />
      </svg>
    );
  }

  if (spec.motif === "steps") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        {[0, 1, 2].map((index) => {
          const local = easeOutCubic(clamp((progress - index * 0.12) / 0.28, 0, 1));
          return (
            <g key={index} transform={`translate(${44 + index * 82} ${118 - index * 24}) scale(${(0.8 + local * 0.2).toFixed(4)})`} opacity={local}>
              <rect x="0" y="-22" width="56" height="56" rx="18" fill="rgba(255,255,255,0.92)" />
              <circle cx="28" cy="6" r="10" fill="rgba(65,116,240,0.9)" />
            </g>
          );
        })}
        <path
          d="M70 118 C98 98, 116 92, 146 92 S196 72, 228 52"
          fill="none"
          stroke="rgba(150,193,255,0.92)"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray="210"
          strokeDashoffset={210 - 210 * draw}
        />
      </svg>
    );
  }

  if (spec.motif === "path") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <circle cx="68" cy="108" r="30" fill="rgba(255,108,133,0.18)" stroke="rgba(255,126,147,0.84)" strokeWidth="4" />
        <path d="M54 94 L82 122 M82 94 L54 122" stroke="rgba(255,255,255,0.9)" strokeWidth="7" strokeLinecap="round" />
        <circle cx="252" cy="64" r="34" fill="rgba(98,227,182,0.18)" stroke="rgba(126,244,203,0.84)" strokeWidth="4" />
        <path d="M236 65 L248 78 L270 50" stroke="rgba(255,255,255,0.9)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <path
          d="M98 108 C132 118, 154 118, 180 92 S218 66, 220 66"
          fill="none"
          stroke="rgba(255,255,255,0.9)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray="170"
          strokeDashoffset={170 - 170 * draw}
        />
        <circle cx={98 + 122 * draw} cy={108 - 42 * draw} r="8" fill="rgba(157,197,255,1)" />
      </svg>
    );
  }

  return (
    <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((index) => {
        const local = easeOutCubic(clamp((progress - index * 0.06) / 0.24, 0, 1));
        const x = [70, 124, 160, 206, 252][index];
        const y = [110, 78, 120, 70, 110][index];
        const r = [18, 11, 22, 10, 16][index];
        return (
          <g key={index} transform={`translate(${x} ${y}) scale(${(0.5 + local * 0.5).toFixed(4)})`} opacity={local}>
            <circle cx="0" cy="0" r={r} fill={index % 2 === 0 ? "rgba(255,255,255,0.92)" : "rgba(151,193,255,0.95)"} />
          </g>
        );
      })}
      <path
        d="M84 114 C112 88, 138 88, 160 114 S206 140, 236 100"
        fill="none"
        stroke="rgba(255,255,255,0.72)"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray="180"
        strokeDashoffset={180 - 180 * draw}
      />
    </svg>
  );
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

function getVisualWorkerStatus(track: ProjectTrack): string | null {
  const status = track.metadata?.visual_worker_status;
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

function buildZoomPreviewBeats(track: ProjectTrack): Array<ZoomPreviewBeat & { id: string; label: string }> {
  const segments = getTranscriptSegments(track)
    .map((segment) => ({
      start: Math.max(0, segment.start || 0),
      end: Math.max(segment.start || 0, segment.end || 0),
      text: String(segment.text || "").trim(),
    }))
    .filter((segment) => segment.text && segment.end - segment.start >= 0.8)
    .sort((left, right) => left.start - right.start);

  if (segments.length === 0) {
    return [];
  }

  const merged: ZoomPreviewBeat[] = [];
  let currentStart = segments[0].start;
  let currentEnd = segments[0].end;
  let currentText = segments[0].text;

  const flush = () => {
    const duration = roundToMillis(Math.max(0, currentEnd - currentStart));
    if (duration < 1.2) return;
    const index = merged.length;
    const normalizedLabel = currentText.replace(/\s+/g, " ").trim();
    merged.push({
      start: roundToMillis(currentStart),
      end: roundToMillis(currentEnd),
      duration,
      text: normalizedLabel,
      scale: index % 3 === 1 ? 1.18 : 1.12,
      enabled: index % 2 === 0,
    });
  };

  for (let index = 1; index < segments.length; index += 1) {
    const segment = segments[index];
    const nextDuration = segment.end - currentStart;
    const gap = segment.start - currentEnd;
    if (nextDuration <= 4.3 && gap <= 0.55) {
      currentEnd = segment.end;
      currentText = `${currentText} ${segment.text}`.trim();
      continue;
    }
    flush();
    currentStart = segment.start;
    currentEnd = segment.end;
    currentText = segment.text;
  }
  flush();
  return merged.map((beat, index) => ({
    ...beat,
    id: `zoom-${index + 1}`,
    label: beat.text.length > 88 ? `${beat.text.slice(0, 85).trim()}...` : beat.text,
  }));
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

function sortTracksForProject(tracks: ProjectTrack[]): ProjectTrack[] {
  return [...tracks].sort((left, right) => {
    if (left.position !== right.position) return left.position - right.position;
    const leftKey = filenameNaturalKey(left.filename || "");
    const rightKey = filenameNaturalKey(right.filename || "");
    const maxLength = Math.max(leftKey.length, rightKey.length);
    for (let index = 0; index < maxLength; index += 1) {
      const a = leftKey[index];
      const b = rightKey[index];
      if (a === undefined) return -1;
      if (b === undefined) return 1;
      if (typeof a === "number" && typeof b === "number") {
        if (a !== b) return a - b;
      } else {
        const leftPart = String(a);
        const rightPart = String(b);
        if (leftPart !== rightPart) return leftPart.localeCompare(rightPart);
      }
    }
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
  visualPlan,
  visualPlanLoading,
  visualPlanError,
  onGenerateSpeechFilter,
  onGenerateVisualPlan,
}: {
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterError?: string | null;
  visualPlan?: VisualPlanArtifact | null;
  visualPlanLoading: boolean;
  visualPlanError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onGenerateVisualPlan: (track: ProjectTrack) => void;
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
  const visualPlanStatus = getVisualWorkerStatus(track);
  const hasVisualPlan = Boolean(visualPlan);

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

      <div className="clipSpeechFilter">
        <button
          type="button"
          className={`clipTag clipTagButton ${visualPlanLoading ? "clipTagProcessing" : "clipTagActive"}`}
          disabled={visualPlanLoading}
          onClick={() => onGenerateVisualPlan(track)}
        >
          {visualPlanLoading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              generating visuals
            </>
          ) : hasVisualPlan ? (
            "rerun visuals"
          ) : visualPlanStatus === "completed" ? (
            "load visuals"
          ) : (
            "generate visuals"
          )}
        </button>
        {visualPlanError ? <p className="clipSpeechFilterError">{visualPlanError}</p> : null}
        {visualPlan ? (
          <details className="clipSpeechFilterDetails">
            <summary className={`clipTag ${visualPlan.parts.length ? "clipTagWarn" : "clipTagMuted"}`}>
              {visualPlan.parts.length ? `visual parts (${visualPlan.parts.length})` : "no visuals suggested"}
            </summary>
            <div className="clipSpeechFilterPanel">
              <p className="muted clipSpeechFilterSummary">{visualPlan.summary}</p>
              {visualPlan.parts.length > 0 ? (
                <div className="clipSpeechFilterList">
                  {visualPlan.parts.map((part: VisualPlanPart, index: number) => (
                    <div key={`${part.start}-${part.end}-${index}`} className="clipSpeechFilterRow">
                      <div className="clipSpeechFilterHeader">
                        <strong>{formatTime(part.start)} - {formatTime(part.end)}</strong>
                        <span className="muted">{part.visual_type === "web_image" ? "Web Image" : "Animation"}</span>
                      </div>
                      <span>{part.prompt}</span>
                      {part.text ? <span className="clipSpeechFilterSnippet">“{part.text}”</span> : null}
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
  visualPlan,
  visualPlanLoading,
  visualPlanError,
  onGenerateSpeechFilter,
  onGenerateVisualPlan,
  onOpen,
  onHide,
  hiding,
  draggable,
  dragActive,
  dragTarget,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: {
  projectId: string;
  projectCreatedAt?: string;
  token: string | undefined;
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterError?: string | null;
  visualPlan?: VisualPlanArtifact | null;
  visualPlanLoading: boolean;
  visualPlanError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onGenerateVisualPlan: (track: ProjectTrack) => void;
  onOpen: (track: ProjectTrack) => void;
  onHide: (trackId: string) => void;
  hiding: boolean;
  draggable: boolean;
  dragActive: boolean;
  dragTarget: boolean;
  onDragStart: (track: ProjectTrack) => void;
  onDragOver: (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => void;
  onDrop: (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
}) {
  const rawUrl = api.getTrackMediaUrl(projectId, track.id);
  const { blobUrl, loading, error } = useAuthedBlobUrl(rawUrl, token);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [posterReady, setPosterReady] = useState(false);
  const clipCreatedAt = track.recorded_at || projectCreatedAt;
  const orientationLabel = formatOrientation(track.orientation);

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
    <article
      className={`clipCard ${dragActive ? "clipCardDragging" : ""} ${dragTarget ? "clipCardDropTarget" : ""}`}
      draggable={draggable}
      onDragStart={() => onDragStart(track)}
      onDragOver={(event) => onDragOver(track, event)}
      onDrop={(event) => onDrop(track, event)}
      onDragEnd={onDragEnd}
    >
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
          {orientationLabel ? (
            <span className="muted">
              {orientationLabel}
              {track.width && track.height ? ` • ${track.width}×${track.height}` : ""}
            </span>
          ) : null}
        </div>
        <TranscriptPanel
          track={track}
          speechFilter={speechFilter}
          speechFilterLoading={speechFilterLoading}
          speechFilterError={speechFilterError}
          visualPlan={visualPlan}
          visualPlanLoading={visualPlanLoading}
          visualPlanError={visualPlanError}
          onGenerateSpeechFilter={onGenerateSpeechFilter}
          onGenerateVisualPlan={onGenerateVisualPlan}
        />
      </div>
    </article>
  );
}

function VisualOverlayPreview({
  projectId,
  trackId,
  token,
  track,
  part,
  partIndex,
  progress,
}: {
  projectId: string;
  trackId: string;
  token: string | undefined;
  track: ProjectTrack;
  part: VisualPlanPart;
  partIndex: number;
  progress: number;
}) {
  const shouldLoadImage = part.visual_type === "web_image" && part.asset_status === "ready";
  const imageUrl = shouldLoadImage ? api.getTrackVisualAssetUrl(projectId, trackId, partIndex) : "";
  const { blobUrl } = useAuthedBlobUrl(imageUrl, shouldLoadImage ? token : undefined);
  const intro = easeOutCubic(clamp(progress / 0.22, 0, 1));
  const outro = easeOutCubic(clamp((1 - progress) / 0.18, 0, 1));
  const envelope = Math.min(intro, outro);

  if (part.visual_type === "web_image" && blobUrl) {
    return (
      <div
        className={`visualOverlay visualOverlayImage ${track.orientation === "vertical" ? "visualOverlayVertical" : ""}`}
        style={{
          opacity: clamp(envelope, 0, 1),
          transform: `translateY(${(1 - envelope) * 24}px) scale(${(0.94 + envelope * 0.06).toFixed(4)})`,
        }}
      >
        <img src={blobUrl} alt={part.prompt} className="visualOverlayAsset" />
        <div className="visualOverlayCaption">{part.text || part.prompt}</div>
      </div>
    );
  }

  const spec = buildAnimationSpec(part, partIndex);
  const cardLift = (1 - envelope) * 28;
  const titleProgress = easeOutCubic(clamp((progress - 0.04) / 0.28, 0, 1));
  const chipProgresses = spec.chips.map((_, index) => easeOutCubic(clamp((progress - 0.18 - index * 0.08) / 0.24, 0, 1)));

  return (
    <div
      className={`visualOverlay visualOverlayAnimation ${track.orientation === "vertical" ? "visualOverlayVertical" : ""} visualOverlayAnimation${spec.align === "right" ? "Right" : "Left"}`}
      style={{
        opacity: clamp(envelope, 0, 1),
      }}
    >
      <div
        className={`visualOverlayFloat visualOverlayFloat${spec.theme.charAt(0).toUpperCase()}${spec.theme.slice(1)}`}
        style={{
          transform: `translateY(${cardLift}px) scale(${(0.92 + envelope * 0.08).toFixed(4)})`,
        }}
      >
        <AnimatedMotif spec={spec} progress={progress} />
        <div className="visualOverlayLabels">
          <div
            className="visualOverlayKicker"
            style={{
              opacity: titleProgress,
              transform: `translateY(${(1 - titleProgress) * 14}px)`,
            }}
          >
            {spec.kicker}
          </div>
          <div
            className="visualOverlayPrompt"
            style={{
              opacity: titleProgress,
              transform: `translateY(${(1 - titleProgress) * 12}px)`,
            }}
          >
            {spec.headline}
          </div>
          <div className="visualOverlayChipRow">
            {spec.chips.map((chip, index) => (
              <span
                key={`${chip}-${index}`}
                className="visualOverlayChip"
                style={{
                  opacity: chipProgresses[index],
                  transform: `translateY(${(1 - chipProgresses[index]) * 10}px) scale(${(0.92 + chipProgresses[index] * 0.08).toFixed(4)})`,
                }}
              >
                {chip}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SpeechFilterEditor({
  src,
  track,
  artifact,
  visualPlan,
  projectId,
  token,
  saving,
  saveError,
  onSave,
}: {
  src: string;
  track: ProjectTrack;
  artifact: SpeechFilterArtifact;
  visualPlan?: VisualPlanArtifact | null;
  projectId: string;
  token: string | undefined;
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
  const [zoomPreviewEnabled, setZoomPreviewEnabled] = useState(true);
  const skipInFlightRef = useRef(false);
  const animationFrameRef = useRef<number | null>(null);
  const duration = Math.max(track.duration || 0, 0.1);
  const stageAspectRatio = useMemo(() => {
    if ((track.width || 0) > 0 && (track.height || 0) > 0) {
      return `${track.width} / ${track.height}`;
    }
    if (track.orientation === "vertical") return "9 / 16";
    if (track.orientation === "square") return "1 / 1";
    return "16 / 9";
  }, [track.height, track.orientation, track.width]);
  const zoomBeats = useMemo(() => {
    if (artifact.zoom_beats?.length) {
      return artifact.zoom_beats.map((beat, index) => ({
        ...beat,
        id: `zoom-${index + 1}`,
        label: beat.text.length > 88 ? `${beat.text.slice(0, 85).trim()}...` : beat.text,
      }));
    }
    return buildZoomPreviewBeats(track);
  }, [artifact.zoom_beats, track]);
  const activeZoomBeat = useMemo(() => {
    if (!zoomPreviewEnabled) return null;
    return zoomBeats.find((beat) => beat.enabled && currentTime >= beat.start && currentTime <= beat.end) || null;
  }, [currentTime, zoomBeats, zoomPreviewEnabled]);
  const videoTransform = useMemo(() => {
    if (!activeZoomBeat) return "scale(1)";
    const progress = clamp((currentTime - activeZoomBeat.start) / Math.max(activeZoomBeat.duration, 0.001), 0, 1);
    const eased = easeOutCubic(progress);
    const scale = 1 + (activeZoomBeat.scale - 1) * eased;
    return `scale(${scale.toFixed(4)})`;
  }, [activeZoomBeat, currentTime]);

  useEffect(() => {
    setEditableCuts(normalizeEditableCuts(artifact.cuts));
    setSelectedCutIndex(0);
  }, [artifact]);

  useEffect(() => {
    setZoomPreviewEnabled(zoomBeats.some((beat) => beat.enabled));
  }, [zoomBeats]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const syncTime = () => setCurrentTime(video.currentTime || 0);
    const tick = () => {
      syncTime();
      if (!video.paused && !video.ended) {
        animationFrameRef.current = window.requestAnimationFrame(tick);
      } else {
        animationFrameRef.current = null;
      }
    };
    const startTick = () => {
      if (animationFrameRef.current !== null) return;
      animationFrameRef.current = window.requestAnimationFrame(tick);
    };
    const stopTick = () => {
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
    };
    const syncPlay = () => setIsPlaying(true);
    const syncPause = () => setIsPlaying(false);

    video.addEventListener("timeupdate", syncTime);
    video.addEventListener("play", syncPlay);
    video.addEventListener("play", startTick);
    video.addEventListener("pause", syncPause);
    video.addEventListener("pause", stopTick);
    video.addEventListener("ended", stopTick);
    video.addEventListener("loadedmetadata", syncTime);
    return () => {
      stopTick();
      video.removeEventListener("timeupdate", syncTime);
      video.removeEventListener("play", syncPlay);
      video.removeEventListener("play", startTick);
      video.removeEventListener("pause", syncPause);
      video.removeEventListener("pause", stopTick);
      video.removeEventListener("ended", stopTick);
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
  const activeVisualPart = visualPlan?.parts.find((part) => currentTime >= part.start && currentTime <= part.end) || null;
  const activeVisualPartIndex = activeVisualPart ? visualPlan?.parts.findIndex((part) => part === activeVisualPart) ?? -1 : -1;
  const activeVisualProgress = activeVisualPart
    ? clamp(
        (currentTime - activeVisualPart.start) / Math.max(activeVisualPart.end - activeVisualPart.start, 0.001),
        0,
        1,
      )
    : 0;

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
        <div className="speechEditorStage" style={{ ["--stage-ratio" as string]: stageAspectRatio }}>
          <video
            ref={videoRef}
            className="speechEditorVideo"
            src={src}
            preload="metadata"
            playsInline
            style={{ transform: videoTransform }}
          />
          {activeCut ? (
            <div className="speechEditorVideoNotice">
              Suggested cut: {formatSpeechFilterReason(activeCut.reason)}
            </div>
          ) : null}
          {activeZoomBeat ? (
            <div className="speechEditorZoomNotice">
              Zoom beat: {formatTime(activeZoomBeat.start)} - {formatTime(activeZoomBeat.end)}
            </div>
          ) : null}
          {activeVisualPart && activeVisualPartIndex >= 0 ? (
            <VisualOverlayPreview
              projectId={projectId}
              trackId={track.id}
              token={token}
              track={track}
              part={activeVisualPart}
              partIndex={activeVisualPartIndex}
              progress={activeVisualProgress}
            />
          ) : null}
        </div>
      </div>

      <div className="speechEditorControls">
        <button type="button" className="btn secondary" onClick={togglePlayback}>
          {isPlaying ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          className={`btn secondary ${zoomPreviewEnabled ? "speechEditorToggleActive" : ""}`}
          onClick={() => setZoomPreviewEnabled((current) => !current)}
          disabled={zoomBeats.length === 0}
        >
          {zoomPreviewEnabled ? "Zoom Preview On" : "Zoom Preview Off"}
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
          {zoomBeats.map((beat) => {
            const left = (beat.start / duration) * 100;
            const width = ((beat.end - beat.start) / duration) * 100;
            return (
              <div
                key={beat.id}
                className={`speechEditorZoomBeat ${beat.enabled ? "speechEditorZoomBeatEnabled" : ""}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                title={`${formatTime(beat.start)} - ${formatTime(beat.end)} ${beat.enabled ? "center zoom" : "no zoom"}`}
              />
            );
          })}
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
          {zoomBeats.length > 0 ? (
            <span className="muted">
              Center zoom preview is generated from transcript beats. Blue blocks mark beats, darker blue means zoom-in.
            </span>
          ) : null}
          {saveError ? <span className="speechEditorError">{saveError}</span> : null}
        </div>
        {zoomBeats.length > 0 ? (
          <div className="speechEditorZoomList">
            {zoomBeats.map((beat) => (
              <div
                key={`${beat.id}-row`}
                className={`speechEditorZoomRow ${beat.enabled ? "speechEditorZoomRowEnabled" : ""} ${activeZoomBeat?.id === beat.id ? "speechEditorZoomRowActive" : ""}`}
              >
                <strong>{formatTime(beat.start)} - {formatTime(beat.end)}</strong>
                <span>{beat.enabled ? `Center zoom ${beat.scale.toFixed(2)}x` : "Normal framing"}</span>
                <span className="muted">{beat.label}</span>
              </div>
            ))}
          </div>
        ) : null}
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
  visualPlan,
  visualPlanLoading,
  visualPlanError,
  onGenerateSpeechFilter,
  onGenerateVisualPlan,
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
  visualPlan?: VisualPlanArtifact | null;
  visualPlanLoading: boolean;
  visualPlanError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onGenerateVisualPlan: (track: ProjectTrack) => void;
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
              {formatOrientation(track.orientation)
                ? ` • ${formatOrientation(track.orientation)}${track.width && track.height ? ` ${track.width}×${track.height}` : ""}`
                : ""}
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
              visualPlan={visualPlan}
              projectId={projectId}
              token={token}
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
          visualPlan={visualPlan}
          visualPlanLoading={visualPlanLoading}
          visualPlanError={visualPlanError}
          onGenerateSpeechFilter={onGenerateSpeechFilter}
          onGenerateVisualPlan={onGenerateVisualPlan}
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
  const [visualPlans, setVisualPlans] = useState<Record<string, VisualPlanArtifact | null>>({});
  const [visualPlanLoadingIds, setVisualPlanLoadingIds] = useState<Record<string, boolean>>({});
  const [visualPlanErrors, setVisualPlanErrors] = useState<Record<string, string | null>>({});
  const [token, setToken] = useState<string | undefined>(undefined);
  const [draggingTrackId, setDraggingTrackId] = useState<string | null>(null);
  const [dragTargetTrackId, setDragTargetTrackId] = useState<string | null>(null);
  const [reorderingTracks, setReorderingTracks] = useState(false);
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
    setVisualPlans({});
    setVisualPlanLoadingIds({});
    setVisualPlanErrors({});
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

  useEffect(() => {
    if (!activeTrack || !project || !token) return;
    if (visualPlans[activeTrack.id]) return;
    if (getVisualWorkerStatus(activeTrack) !== "completed") return;
    if (visualPlanLoadingIds[activeTrack.id]) return;
    if (visualPlanErrors[activeTrack.id]) return;

    setVisualPlanLoadingIds((current) => ({ ...current, [activeTrack.id]: true }));
    api.getTrackVisualPlan(project.id, activeTrack.id, token)
      .then((artifact) => {
        setVisualPlans((current) => ({ ...current, [activeTrack.id]: artifact }));
      })
      .catch((error) => {
        setVisualPlanErrors((current) => ({ ...current, [activeTrack.id]: String((error as Error).message || error) }));
      })
      .finally(() => {
        setVisualPlanLoadingIds((current) => ({ ...current, [activeTrack.id]: false }));
      });
  }, [activeTrack, project, token, visualPlans, visualPlanLoadingIds, visualPlanErrors]);

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

  const persistTrackOrder = async (orderedVisibleTracks: ProjectTrack[]) => {
    if (!token || !project) return;
    setReorderingTracks(true);
    try {
      const hiddenTracks = (project.tracks || []).filter((track) => isTrackHidden(track));
      const orderedTrackIds = [...orderedVisibleTracks, ...hiddenTracks].map((track) => track.id);
      const response = await api.reorderProjectTracks(
        project.id,
        orderedTrackIds,
        token,
      );
      setProject(response.project);
      setMessage(null);
    } catch (error) {
      const text = String((error as Error).message || error);
      setMessage(text);
      const refreshed = await api.getProject(project.id, token).catch(() => null);
      if (refreshed) setProject(refreshed.project);
    } finally {
      setReorderingTracks(false);
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
    }
  };

  const handleDragStart = (track: ProjectTrack) => {
    if (groupByDay || reorderingTracks) return;
    setDraggingTrackId(track.id);
    setDragTargetTrackId(track.id);
  };

  const handleDragOver = (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => {
    if (!draggingTrackId || draggingTrackId === track.id || groupByDay) return;
    event.preventDefault();
    setDragTargetTrackId(track.id);
  };

  const handleDrop = async (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    if (!draggingTrackId || draggingTrackId === track.id || !project || groupByDay) {
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
      return;
    }

    const visibleTracks = sortTracksForProject((project.tracks || []).filter((item) => !isTrackHidden(item)));
    const fromIndex = visibleTracks.findIndex((item) => item.id === draggingTrackId);
    const toIndex = visibleTracks.findIndex((item) => item.id === track.id);
    if (fromIndex < 0 || toIndex < 0) {
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
      return;
    }

    const nextVisible = [...visibleTracks];
    const [moved] = nextVisible.splice(fromIndex, 1);
    nextVisible.splice(toIndex, 0, moved);

    setProject((current) => {
      if (!current) return current;
      const hiddenTracks = current.tracks.filter((item) => isTrackHidden(item));
      return {
        ...current,
        tracks: [...nextVisible, ...hiddenTracks].map((item, index) => ({ ...item, position: index })),
      };
    });
    await persistTrackOrder(nextVisible);
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

  const handleGenerateVisualPlan = async (track: ProjectTrack) => {
    if (!token || !project) return;

    setVisualPlanLoadingIds((current) => ({ ...current, [track.id]: true }));
    setVisualPlanErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const existingStatus = getVisualWorkerStatus(track);
      let artifact: VisualPlanArtifact;
      if (existingStatus === "completed" && !visualPlans[track.id]) {
        try {
          artifact = await api.getTrackVisualPlan(project.id, track.id, token);
        } catch (error) {
          const text = String((error as Error).message || error).toLowerCase();
          if (!text.includes("not found")) throw error;
          artifact = await api.generateTrackVisualPlan(project.id, track.id, token);
        }
      } else {
        artifact = await api.generateTrackVisualPlan(project.id, track.id, token);
      }
      setVisualPlans((current) => ({ ...current, [track.id]: artifact }));
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setMessage(null);
    } catch (error) {
      const text = String((error as Error).message || error);
      setVisualPlanErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
    } finally {
      setVisualPlanLoadingIds((current) => ({ ...current, [track.id]: false }));
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
    () => sortTracksForProject((project?.tracks || []).filter((track) => !isTrackHidden(track))),
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
              {!groupByDay ? (
                <span className="muted">{reorderingTracks ? "Saving clip order..." : "Drag clips to reorder"}</span>
              ) : null}
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
                      visualPlan={visualPlans[track.id]}
                      visualPlanLoading={Boolean(visualPlanLoadingIds[track.id])}
                      visualPlanError={visualPlanErrors[track.id]}
                      onGenerateSpeechFilter={handleGenerateSpeechFilter}
                      onGenerateVisualPlan={handleGenerateVisualPlan}
                      onOpen={setActiveTrack}
                      onHide={handleHideTrack}
                      hiding={hidingTrackId === track.id}
                      draggable={!groupByDay && !reorderingTracks}
                      dragActive={draggingTrackId === track.id}
                      dragTarget={dragTargetTrackId === track.id && draggingTrackId !== track.id}
                      onDragStart={handleDragStart}
                      onDragOver={handleDragOver}
                      onDrop={handleDrop}
                      onDragEnd={() => {
                        setDraggingTrackId(null);
                        setDragTargetTrackId(null);
                      }}
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
                visualPlan={visualPlans[track.id]}
                visualPlanLoading={Boolean(visualPlanLoadingIds[track.id])}
                visualPlanError={visualPlanErrors[track.id]}
                onGenerateSpeechFilter={handleGenerateSpeechFilter}
                onGenerateVisualPlan={handleGenerateVisualPlan}
                onOpen={setActiveTrack}
                onHide={handleHideTrack}
                hiding={hidingTrackId === track.id}
                draggable={!groupByDay && !reorderingTracks}
                dragActive={draggingTrackId === track.id}
                dragTarget={dragTargetTrackId === track.id && draggingTrackId !== track.id}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onDragEnd={() => {
                  setDraggingTrackId(null);
                  setDragTargetTrackId(null);
                }}
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
          visualPlan={visualPlans[activeTrack.id]}
          visualPlanLoading={Boolean(visualPlanLoadingIds[activeTrack.id])}
          visualPlanError={visualPlanErrors[activeTrack.id]}
          onGenerateSpeechFilter={handleGenerateSpeechFilter}
          onGenerateVisualPlan={handleGenerateVisualPlan}
          onSaveSpeechFilter={handleSaveSpeechFilter}
          onClose={() => setActiveTrack(null)}
        />
      ) : null}
    </div>
  );
}
