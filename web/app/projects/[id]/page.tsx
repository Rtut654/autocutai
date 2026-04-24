"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import { getStoredSession, setLastProjectId } from "../../../lib/session";
import type { ProjectDetail, ProjectTrack, TranscriptSegment } from "../../../lib/types";

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

function sortChronologically(tracks: ProjectTrack[]): ProjectTrack[] {
  return [...tracks].sort((left, right) => {
    const leftTime = left.recorded_at ? new Date(left.recorded_at).getTime() : Number.POSITIVE_INFINITY;
    const rightTime = right.recorded_at ? new Date(right.recorded_at).getTime() : Number.POSITIVE_INFINITY;
    if (leftTime !== rightTime) return leftTime - rightTime;
    return left.position - right.position;
  });
}

/**
 * Fetch the URL with the auth Bearer token and return an object URL
 * that can be assigned to a <video src>. Revokes on unmount.
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
    let created: string | null = null;
    setLoading(true);
    setError(null);

    fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Media ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setBlobUrl(created);
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
      if (created) URL.revokeObjectURL(created);
    };
  }, [url, token]);

  return { blobUrl, loading, error };
}

function TranscriptPanel({ track }: { track: ProjectTrack }) {
  if (!hasTranscript(track)) {
    return <span className="clipTag clipTagMuted">no transcript</span>;
  }

  const transcriptText = getTranscriptText(track);
  const segments = getTranscriptSegments(track);

  return (
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
  );
}

function ClipCard({
  projectId,
  projectCreatedAt,
  token,
  track,
  onOpen,
}: {
  projectId: string;
  projectCreatedAt?: string;
  token: string | undefined;
  track: ProjectTrack;
  onOpen: (track: ProjectTrack) => void;
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
        <TranscriptPanel track={track} />
      </div>
    </article>
  );
}

function PreviewModal({
  projectId,
  token,
  projectCreatedAt,
  track,
  onClose,
}: {
  projectId: string;
  token: string | undefined;
  projectCreatedAt?: string;
  track: ProjectTrack;
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
          <video
            className="previewModalVideo"
            src={blobUrl}
            controls
            autoPlay
            preload="metadata"
            playsInline
          />
        ) : (
          <div className="previewModalVideo clipMediaLoading">
            <span className="muted">{loading ? "Loading preview..." : error || "Preview unavailable"}</span>
          </div>
        )}
        <TranscriptPanel track={track} />
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
  const [activeTrack, setActiveTrack] = useState<ProjectTrack | null>(null);
  const [token, setToken] = useState<string | undefined>(undefined);

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

  const sortedTracks = useMemo(() => sortChronologically(project?.tracks || []), [project?.tracks]);

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
        <div className="stack" style={{ gap: 4 }}>
          <h2>Clips ({sortedTracks.length})</h2>
          <span className="muted">
            Ordered chronologically from the earliest clip to the latest.
          </span>
        </div>

        {sortedTracks.length === 0 ? (
          <div className="notice">No clips in this project yet.</div>
        ) : (
          <div className="clipGrid">
            {sortedTracks.map((track) => (
              <ClipCard
                key={track.id}
                projectId={project.id}
                projectCreatedAt={project.created_at}
                token={token}
                track={track}
                onOpen={setActiveTrack}
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
          onClose={() => setActiveTrack(null)}
        />
      ) : null}
    </div>
  );
}
