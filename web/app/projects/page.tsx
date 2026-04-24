"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { getStoredSession } from "../../lib/session";
import type { ProjectSummary } from "../../lib/types";

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

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.replace("/login?next=/projects");
      return;
    }
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
    <section className="card stack" style={{ gap: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ fontSize: 28 }}>Projects</h1>
        </div>
        <Link href="/projects/new" className="btn">New Project</Link>
      </div>

      {message ? <div className="notice">{message}</div> : null}

      {loading ? (
        <p className="muted" style={{ textAlign: "center", padding: 32 }}>Loading projects...</p>
      ) : projects.length === 0 ? (
        <div style={{ textAlign: "center", padding: "48px 0" }}>
          <p className="muted" style={{ marginBottom: 16 }}>No projects yet</p>
          <Link href="/projects/new" className="btn">Create your first project</Link>
        </div>
      ) : (
        <div className="projectList">
          {projects.map((project) => (
            <Link key={project.id} href={`/projects/${project.id}`} className="projectListItem">
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
              <span className="projectListStatus" style={{ color: statusColor(project.status) }}>
                {project.status}
              </span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
