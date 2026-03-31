// Pipeline API client - shared between mobile and web

import { API_BASE_URL } from "./client";

export interface ClipDecision {
  clip_id: string;
  source_file: string;
  in_point: string;
  out_point: string;
  reason: string;
  transition_in?: { type: string; duration_ms: number } | null;
  transition_out?: { type: string; duration_ms: number } | null;
}

export interface MusicCue {
  start: string;
  end: string;
  mood: string;
  bpm_target: number;
  suggested_track: string;
  fade_in_ms: number;
  fade_out_ms: number;
}

export interface RemovedClip {
  clip_id: string;
  source_file: string;
  reason: string;
}

export interface EditPlan {
  output_duration_estimate: string;
  clips: ClipDecision[];
  music_cues: MusicCue[];
  cuts_removed: RemovedClip[];
}

export interface ProcessingStatus {
  job_id: string;
  stage: "ordering" | "demux" | "analysis" | "edit_plan" | "rendering" | "done";
  progress: number;
  message: string;
  edit_plan?: EditPlan;
  output_url?: string;
}

export async function uploadClips(
  files: { uri: string; name: string; mimeType?: string }[]
): Promise<string> {
  const form = new FormData();
  files.forEach((f) => {
    form.append("files", {
      uri: f.uri,
      name: f.name,
      type: f.mimeType || "video/mp4",
    } as any);
  });

  const res = await fetch(`${API_BASE_URL}/upload`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Upload failed: ${res.status}`);
  }

  const { job_id } = await res.json();
  return job_id;
}

export function subscribeToStatus(
  jobId: string,
  onUpdate: (status: ProcessingStatus) => void
): () => void {
  const es = new EventSource(`${API_BASE_URL}/status/${jobId}`);
  es.onmessage = (e) => onUpdate(JSON.parse(e.data));
  es.onerror = () => es.close();
  return () => es.close();
}

export async function pollStatus(jobId: string): Promise<ProcessingStatus> {
  const res = await fetch(`${API_BASE_URL}/status/${jobId}/poll`);
  if (!res.ok) throw new Error(`Status poll failed: ${res.status}`);
  return res.json();
}

export function getOutputUrl(jobId: string): string {
  return `${API_BASE_URL}/outputs/${jobId}/output.mp4`;
}

export async function retryJob(jobId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/retry/${jobId}`, { method: "POST" });
  if (!res.ok) throw new Error(`Retry failed: ${res.status}`);
}
