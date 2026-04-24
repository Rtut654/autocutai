"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { getStoredSession, setLastProjectId } from "../../../lib/session";

export default function NewProjectPage() {
  const router = useRouter();
  const [name, setName] = useState("BestShot travel story");
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!getStoredSession()?.access_token) {
      router.replace("/login?next=/projects/new");
    }
  }, [router]);

  const submit = async () => {
    if (!getStoredSession()?.access_token) {
      router.push("/login?next=/projects/new");
      return;
    }
    if (!name.trim()) {
      setMessage("Project name is required.");
      return;
    }
    if (files.length === 0) {
      setMessage("Select at least one file.");
      return;
    }
    try {
      setLoading(true);
      setMessage(null);
      const response = await api.createProject({ name, files }, getStoredSession()?.access_token);
      setLastProjectId(response.project.id);
      router.push(`/projects/${response.project.id}`);
    } catch (e) {
      setMessage(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="card stack">
      <h1>Create Project</h1>
      <p className="muted">Upload clips for the legacy backend path, or use mobile for the newer hybrid flow.</p>

      <div className="stack">
        <label>Project Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <div className="stack">
        <label>Videos (multi-select)</label>
        <input
          type="file"
          multiple
          accept="video/*"
          onChange={(e) => setFiles(Array.from(e.target.files || []))}
        />
      </div>

      {files.length > 0 ? (
        <div className="list">
          {files.map((file) => (
            <div key={`${file.name}-${file.size}`} className="listItem">
              <span>{file.name}</span>
              <span className="muted">{(file.size / (1024 * 1024)).toFixed(1)} MB</span>
            </div>
          ))}
        </div>
      ) : null}

      {message ? <div className="notice">{message}</div> : null}

      <div className="row">
        <button className="btn" disabled={loading} onClick={submit}>
          {loading ? "Creating..." : "Create Project"}
        </button>
        <button className="btn secondary" onClick={() => router.push("/projects")}>Back to Projects</button>
      </div>
    </section>
  );
}
