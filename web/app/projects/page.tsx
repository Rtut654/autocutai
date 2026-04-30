"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { getStoredSession } from "../../lib/session";
import type { ProjectSummary, ProjectTrack } from "../../lib/types";

const MEDIA_BLOB_CACHE_NAME = "bestshotai-track-media-v1";
const mediaObjectUrlCache = new Map<string, string>();
const mediaObjectUrlPromiseCache = new Map<string, Promise<string>>();

function formatDate(value?: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatTime(value?: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusColor(status: string): string {
  if (status === "completed") return "#16a34a";
  if (status === "processing") return "#d97706";
  if (status === "error") return "#dc2626";
  return "#64748b";
}

function trackCount(project: ProjectSummary): number {
  return project.tracks?.length || 0;
}

function totalDuration(project: ProjectSummary): string {
  const total = (project.tracks || []).reduce((s, t) => s + (t.duration || 0), 0);
  if (total <= 0) return "";
  const m = Math.floor(total / 60);
  const s = Math.round(total % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
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

function ProjectMaterialThumb({
  projectId,
  track,
  token,
}: {
  projectId: string;
  track: ProjectTrack;
  token: string | undefined;
}) {
  const rawUrl = api.getTrackMediaUrl(projectId, track.id);
  const { blobUrl } = useAuthedBlobUrl(rawUrl, token);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !blobUrl) return;
    setReady(false);
    const onLoaded = () => {
      try {
        video.currentTime = Math.min(0.35, Math.max(0.05, (track.duration || 0) * 0.08));
      } catch {
        // ignore
      }
    };
    const onSeeked = () => setReady(true);
    video.addEventListener("loadedmetadata", onLoaded);
    video.addEventListener("seeked", onSeeked);
    return () => {
      video.removeEventListener("loadedmetadata", onLoaded);
      video.removeEventListener("seeked", onSeeked);
    };
  }, [blobUrl, track.duration]);

  return (
    <div className="projectMaterialsCell">
      {blobUrl ? (
        <video
          ref={videoRef}
          className="projectMaterialsVideo"
          src={blobUrl}
          preload="metadata"
          playsInline
          muted
        />
      ) : null}
      {!ready ? (
        <div className="projectMaterialsFallback">
          <span>{track.filename.slice(0, 2).toUpperCase()}</span>
        </div>
      ) : null}
    </div>
  );
}

function ProjectMaterialsPreview({
  project,
  token,
}: {
  project: ProjectSummary;
  token: string | undefined;
}) {
  const tracks = (project.tracks || [])
    .filter((track) => track.type === "video" && !track.excluded && track.status !== "hidden")
    .slice(0, 4);
  if (tracks.length === 0) {
    return <div className="projectMaterialsEmpty">No media yet</div>;
  }
  return (
    <div className="projectMaterialsGrid" aria-hidden="true">
      {tracks.map((track) => (
        <ProjectMaterialThumb
          key={track.id}
          projectId={project.id}
          track={track}
          token={token}
        />
      ))}
    </div>
  );
}

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionToken, setSessionToken] = useState<string>();

  useEffect(() => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.replace("/login?next=/projects");
      return;
    }
    setSessionToken(session.access_token);
    api.listProjects(100, 0, session.access_token)
      .then((data) => {
        const sorted = (data.projects || []).sort((a, b) => {
          const da = new Date(a.updated_at || a.created_at || 0).getTime();
          const db = new Date(b.updated_at || b.created_at || 0).getTime();
          return db - da;
        });
        setProjects(sorted);
      })
      .catch((error) => setMessage(String((error as Error).message || error)))
      .finally(() => setLoading(false));
  }, [router]);

  return (
    <section className="projectsPageShell">
      <div className="projectsPageHead">
        <div className="projectsPageTitleBlock">
          <span className="projectsPageEyebrow">Workspace</span>
          <h1 className="projectsPageTitle">Projects</h1>
          <p className="projectsPageSummary">
            {loading ? "Loading your edits..." : `${projects.length} project${projects.length === 1 ? "" : "s"} ready`}
          </p>
        </div>
        <Link href="/projects/new" className="btn">New Project</Link>
      </div>

      {message ? <div className="notice">{message}</div> : null}

      {loading ? (
        <div className="projectsPageState">
          <p className="muted">Loading projects...</p>
        </div>
      ) : projects.length === 0 ? (
        <div className="projectsPageState">
          <p className="projectsPageEmptyTitle">No projects yet</p>
          <p className="muted">Create a project to start cutting clips, subtitles, and visuals.</p>
          <Link href="/projects/new" className="btn">Create your first project</Link>
        </div>
      ) : (
        <div className="projectList">
          {projects.map((project) => (
            <Link key={project.id} href={`/projects/${project.id}`} className="projectListItem">
              <ProjectMaterialsPreview project={project} token={sessionToken} />
              <div className="projectListInfo">
                <strong className="projectListName">{project.name}</strong>
                <div className="projectListMeta">
                  <span>{formatDate(project.updated_at || project.created_at)}</span>
                  {formatTime(project.updated_at || project.created_at) && (
                    <span>{formatTime(project.updated_at || project.created_at)}</span>
                  )}
                  {trackCount(project) > 0 && (
                    <span>{trackCount(project)} clip{trackCount(project) !== 1 ? "s" : ""}</span>
                  )}
                  {totalDuration(project) && <span>{totalDuration(project)}</span>}
                </div>
              </div>
              <span
                className="projectListStatus"
                style={{
                  color: statusColor(project.status),
                  borderColor: `${statusColor(project.status)}22`,
                  backgroundColor: `${statusColor(project.status)}12`,
                }}
              >
                {project.status}
              </span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
