/**
 * Project upload, editing and export.
 *
 * The clips themselves are uploaded to the backend, which does the
 * transcription, cutting and rendering. The device only picks media,
 * shows the result and lets the user adjust it.
 */

import { File, Paths, UploadType } from 'expo-file-system';

import { API_BASE_URL, authorizedRequest } from './client';

/** Mirrors backend/app/services/project_limits.py. */
export const MAX_CLIPS_PER_PROJECT = 10;
export const MAX_TOTAL_DURATION_SECONDS = 15 * 60;

export type PickedClip = {
  uri: string;
  fileName: string;
  durationSeconds: number | null;
  /** iOS capture time, when the picker exposes it. */
  recordedAt: string | null;
  fileSizeBytes: number | null;
  mimeType?: string;
};

export type SpeechFilterCut = {
  start: number;
  end: number;
  duration: number;
  reason: string;
  transcript: string;
  confidence: number;
};

export type SpeechFilterArtifact = {
  project_id: string;
  track_id: string;
  filename: string;
  status: 'completed' | 'error';
  summary: string;
  cuts: SpeechFilterCut[];
  model: string;
};

export type ProjectTrack = {
  id: string;
  filename: string;
  duration: number;
  position: number;
  has_voice: boolean;
  excluded: boolean;
  width?: number | null;
  height?: number | null;
  transcription?: { text?: string } | null;
  metadata?: Record<string, unknown>;
};

export type CaptionStyle = 'bold' | 'boxed' | 'clean' | 'none';
export type FillMode = 'blur' | 'crop' | 'black';

/** How the finished edit looks. Mirrors ProjectSettings on the backend. */
export type EditOptions = {
  caption_style: CaptionStyle;
  fill_mode: FillMode;
  audio_cleanup: boolean;
  broll_max_seconds: number;
};

/** The defaults the backend applies; chosen from creator research. */
export const DEFAULT_EDIT_OPTIONS: EditOptions = {
  caption_style: 'bold',
  fill_mode: 'blur',
  audio_cleanup: true,
  broll_max_seconds: 6,
};

export type Project = {
  id: string;
  name: string;
  status: 'draft' | 'processing' | 'completed' | 'error';
  settings?: Partial<EditOptions> & { aspect_ratio?: 'vertical' | 'horizontal' };
  output_path?: string | null;
  error_message?: string | null;
  created_at?: string;
  updated_at?: string;
  tracks: ProjectTrack[];
};

export type ProcessingStatus = {
  project_id: string;
  status: string;
  progress: number;
  current_step: string;
  estimated_time_remaining: number | null;
  error_message: string | null;
};

export type UploadProgress = {
  sentBytes: number;
  totalBytes: number;
  fraction: number;
};

/**
 * Checks the same limits the backend enforces, so the user finds out before
 * spending minutes on an upload. Returns an error message, or null when fine.
 */
export function validateSelection(clips: PickedClip[]): string | null {
  if (clips.length === 0) return 'Select at least one clip.';
  if (clips.length > MAX_CLIPS_PER_PROJECT) {
    return `Select at most ${MAX_CLIPS_PER_PROJECT} clips. You picked ${clips.length}.`;
  }

  const durations = clips.map((clip) => clip.durationSeconds).filter((d): d is number => typeof d === 'number');
  if (durations.length === clips.length) {
    const total = durations.reduce((sum, value) => sum + value, 0);
    if (total > MAX_TOTAL_DURATION_SECONDS) {
      return `Your clips add up to ${Math.round(total / 60)} minutes. The limit is ${
        MAX_TOTAL_DURATION_SECONDS / 60
      } minutes.`;
    }
  }
  return null;
}

export function totalDuration(clips: PickedClip[]): number {
  return clips.reduce((sum, clip) => sum + (clip.durationSeconds || 0), 0);
}

export function totalBytes(clips: PickedClip[]): number {
  return clips.reduce((sum, clip) => sum + (clip.fileSizeBytes || 0), 0);
}

/**
 * Uploads clips one at a time so progress is meaningful and a failure part-way
 * through does not discard everything that already landed.
 */
export async function createProjectFromClips(
  token: string,
  name: string,
  clips: PickedClip[],
  onProgress?: (progress: UploadProgress & { clipIndex: number; clipCount: number }) => void,
  options: EditOptions = DEFAULT_EDIT_OPTIONS,
): Promise<Project> {
  const problem = validateSelection(clips);
  if (problem) throw new Error(problem);

  let projectId: string | null = null;
  let project: Project | null = null;

  for (let index = 0; index < clips.length; index += 1) {
    const clip = clips[index];
    const path = projectId ? `/api/projects/${projectId}/tracks` : '/api/projects/';

    const parameters: Record<string, string> = projectId
      ? {
          capture_times_json: JSON.stringify([clip.recordedAt]),
          metadata_json: JSON.stringify([{ source: 'ios_photo_library' }]),
        }
      : {
          name,
          aspect_ratio: 'vertical',
          smart_pause_cutter: 'true',
          insert_suggestions: 'false',
          caption_style: options.caption_style,
          generate_subtitles: options.caption_style === 'none' ? 'false' : 'true',
          fill_mode: options.fill_mode,
          audio_cleanup: String(options.audio_cleanup),
          broll_max_seconds: String(options.broll_max_seconds),
          capture_times_json: JSON.stringify([clip.recordedAt]),
          metadata_json: JSON.stringify([{ source: 'ios_photo_library' }]),
        };

    // Background session (the iOS default) lets a large upload keep going if
    // the user switches apps, which matters for multi-gigabyte 4K footage.
    const task = new File(clip.uri).createUploadTask(`${API_BASE_URL}${path}`, {
      httpMethod: 'POST',
      uploadType: UploadType.MULTIPART,
      fieldName: 'files',
      mimeType: clip.mimeType || 'video/quicktime',
      parameters,
      headers: { Authorization: `Bearer ${token}` },
      onProgress: ({ bytesSent, totalBytes }) => {
        onProgress?.({
          sentBytes: bytesSent,
          totalBytes,
          fraction: totalBytes > 0 ? Math.min(1, bytesSent / totalBytes) : 0,
          clipIndex: index,
          clipCount: clips.length,
        });
      },
    });

    let result: { status: number; body: string } | undefined;
    try {
      result = await task.uploadAsync();
    } catch (error: any) {
      throw new Error(`Upload of ${clip.fileName} failed: ${error?.message || 'network error'}`);
    }
    if (!result || result.status < 200 || result.status >= 300) {
      throw new Error(readUploadError(result?.body) || `Upload failed (${result?.status ?? 'no response'})`);
    }

    project = (JSON.parse(result.body) as { project: Project }).project;
    projectId = project.id;
  }

  if (!project) throw new Error('Upload produced no project.');
  return project;
}

function readUploadError(body?: string): string | null {
  if (!body) return null;
  try {
    const parsed = JSON.parse(body) as { detail?: string };
    return typeof parsed.detail === 'string' ? parsed.detail : null;
  } catch {
    return body.slice(0, 200);
  }
}

export async function getProject(token: string, projectId: string): Promise<Project> {
  const response = await authorizedRequest<{ project: Project }>(`/api/projects/${projectId}`, token);
  return response.project;
}

export async function listProjects(token: string): Promise<Project[]> {
  const response = await authorizedRequest<{ projects: Project[] }>('/api/projects/?limit=100&offset=0', token);
  return response.projects;
}

/** Change how the edit looks. Takes effect on the next render. */
export async function updateEditOptions(
  token: string,
  projectId: string,
  changes: Partial<EditOptions>,
): Promise<Project> {
  const body: Record<string, unknown> = { ...changes };
  if (changes.caption_style) {
    body.generate_subtitles = changes.caption_style !== 'none';
  }
  const response = await authorizedRequest<{ project: Project }>(`/api/projects/${projectId}/settings`, token, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
  return response.project;
}

/** The edit options stored on a project, with defaults for anything missing. */
export function editOptionsOf(project: Project | null | undefined): EditOptions {
  const settings = project?.settings || {};
  return {
    caption_style: settings.caption_style ?? DEFAULT_EDIT_OPTIONS.caption_style,
    fill_mode: settings.fill_mode ?? DEFAULT_EDIT_OPTIONS.fill_mode,
    audio_cleanup: settings.audio_cleanup ?? DEFAULT_EDIT_OPTIONS.audio_cleanup,
    broll_max_seconds: settings.broll_max_seconds ?? DEFAULT_EDIT_OPTIONS.broll_max_seconds,
  };
}

export async function deleteProject(token: string, projectId: string): Promise<void> {
  await authorizedRequest(`/api/projects/${projectId}`, token, { method: 'DELETE' });
}

export async function getStatus(token: string, projectId: string): Promise<ProcessingStatus> {
  return authorizedRequest<ProcessingStatus>(`/api/projects/${projectId}/status`, token);
}

/** Kick off processing in the background; poll getStatus for progress. */
export async function startProcessing(token: string, projectId: string): Promise<void> {
  await authorizedRequest(`/api/projects/${projectId}/process`, token, { method: 'POST' });
}

export async function getSpeechFilter(
  token: string,
  projectId: string,
  trackId: string,
): Promise<SpeechFilterArtifact | null> {
  try {
    return await authorizedRequest<SpeechFilterArtifact>(
      `/api/projects/${projectId}/tracks/${trackId}/speech-filter`,
      token,
    );
  } catch (error: any) {
    if (String(error?.message || '').includes('404')) return null;
    throw error;
  }
}

export async function generateSpeechFilter(
  token: string,
  projectId: string,
  trackId: string,
): Promise<SpeechFilterArtifact> {
  return authorizedRequest<SpeechFilterArtifact>(
    `/api/projects/${projectId}/tracks/${trackId}/speech-filter`,
    token,
    { method: 'POST', body: JSON.stringify({}) },
  );
}

export async function saveSpeechFilter(
  token: string,
  projectId: string,
  trackId: string,
  cuts: SpeechFilterCut[],
): Promise<SpeechFilterArtifact> {
  return authorizedRequest<SpeechFilterArtifact>(
    `/api/projects/${projectId}/tracks/${trackId}/speech-filter`,
    token,
    { method: 'PATCH', body: JSON.stringify({ cuts }) },
  );
}

export async function toggleTrackExcluded(
  token: string,
  projectId: string,
  trackId: string,
): Promise<{ excluded: boolean }> {
  return authorizedRequest<{ excluded: boolean }>(
    `/api/projects/${projectId}/tracks/${trackId}/exclude`,
    token,
    { method: 'PATCH' },
  );
}

export function trackMediaUrl(projectId: string, trackId: string): string {
  return `${API_BASE_URL}/api/projects/${projectId}/tracks/${trackId}/media`;
}

export function projectDownloadUrl(projectId: string): string {
  return `${API_BASE_URL}/api/projects/${projectId}/download`;
}

/** Downloads the finished render to a local file and returns its URI. */
export async function downloadFinalVideo(
  token: string,
  projectId: string,
  onProgress?: (fraction: number) => void,
): Promise<string> {
  const destination = new File(Paths.cache, `autocut-${projectId}.mp4`);
  try {
    const file = await File.downloadFileAsync(projectDownloadUrl(projectId), destination, {
      headers: { Authorization: `Bearer ${token}` },
      // A re-render replaces the previous export for this project.
      idempotent: true,
      onProgress: ({ bytesWritten, totalBytes }) => {
        if (totalBytes > 0) onProgress?.(bytesWritten / totalBytes);
      },
    });
    return file.uri;
  } catch (error: any) {
    throw new Error(`Could not download the video: ${error?.message || 'network error'}`);
  }
}
