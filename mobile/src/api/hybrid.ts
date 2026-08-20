import * as FileSystem from 'expo-file-system';

import { api, transcribeMedia, ProjectAnalysisResult, ProjectSettingsPayload } from './client';

/** Matches the backend cap in backend/app/services/project_limits.py. */
export const MAX_CLIPS_PER_PROJECT = 10;
export const MAX_TOTAL_DURATION_SECONDS = 15 * 60;

export type LocalHybridClip = {
  uri: string;
  name: string;
  mimeType?: string;
  lastModified?: number | null;
  recordedAt?: string | null;
  durationSeconds?: number | null;
};

export type ClientTranscriptionPayload = {
  text: string;
  language: string;
  words: Array<{ word?: string; text?: string; start: number; end: number; confidence?: number | null }>;
  segments: Array<{
    start: number;
    end: number;
    text: string;
    words?: Array<{ word?: string; text?: string; start: number; end: number; confidence?: number | null }>;
  }>;
};

export type HybridTrackPayload = {
  filename: string;
  duration?: number;
  recorded_at?: string | null;
  source_reference?: string | null;
  metadata: Record<string, unknown>;
  transcription: ClientTranscriptionPayload;
};

function deriveRecordedAt(file: LocalHybridClip, modificationTime?: number | null): string | null {
  if (file.recordedAt) return file.recordedAt;
  if (typeof file.lastModified === 'number' && Number.isFinite(file.lastModified)) {
    return new Date(file.lastModified).toISOString();
  }
  if (typeof modificationTime === 'number' && Number.isFinite(modificationTime)) {
    return new Date(modificationTime * 1000).toISOString();
  }
  return null;
}

function normalizeTranscription(raw: any): ClientTranscriptionPayload {
  const segments = Array.isArray(raw?.segments) ? raw.segments : [];
  const topLevelWords = Array.isArray(raw?.words) ? raw.words : [];
  const words =
    topLevelWords.length > 0
      ? topLevelWords
      : segments.flatMap((segment: any) => (Array.isArray(segment?.words) ? segment.words : []));

  return {
    text: String(raw?.text || raw?.transcript || ''),
    language: String(raw?.language || 'en'),
    words: words.map((word: any) => ({
      word: typeof word?.word === 'string' ? word.word : undefined,
      text: typeof word?.text === 'string' ? word.text : undefined,
      start: Number(word?.start || 0),
      end: Number(word?.end || 0),
      confidence: typeof word?.confidence === 'number' ? word.confidence : null,
    })),
    segments: segments.map((segment: any) => ({
      start: Number(segment?.start || 0),
      end: Number(segment?.end || 0),
      text: String(segment?.text || ''),
      words: Array.isArray(segment?.words)
        ? segment.words.map((word: any) => ({
            word: typeof word?.word === 'string' ? word.word : undefined,
            text: typeof word?.text === 'string' ? word.text : undefined,
            start: Number(word?.start || 0),
            end: Number(word?.end || 0),
            confidence: typeof word?.confidence === 'number' ? word.confidence : null,
          }))
        : undefined,
    })),
  };
}

function estimateDurationSeconds(payload: ClientTranscriptionPayload): number | undefined {
  const segmentEnds = payload.segments.map((segment) => segment.end).filter((value) => Number.isFinite(value));
  const wordEnds = payload.words.map((word) => word.end).filter((value) => Number.isFinite(value));
  const max = Math.max(0, ...segmentEnds, ...wordEnds);
  return max > 0 ? max : undefined;
}

export async function transcribeClip(token: string, file: LocalHybridClip): Promise<ClientTranscriptionPayload> {
  return normalizeTranscription(
    await transcribeMedia(token, { uri: file.uri, name: file.name, mimeType: file.mimeType }),
  );
}

export async function buildHybridTrackPayload(token: string, file: LocalHybridClip): Promise<HybridTrackPayload> {
  const info = await FileSystem.getInfoAsync(file.uri);
  const transcription = await transcribeClip(token, file);

  return {
    filename: file.name,
    duration: file.durationSeconds ?? estimateDurationSeconds(transcription),
    recorded_at: deriveRecordedAt(file, (info as any)?.modificationTime ?? null),
    source_reference: file.uri,
    metadata: {
      hybrid_prepared_on_device: true,
      mime_type: file.mimeType || null,
      size_bytes: (info as any)?.exists ? ((info as any)?.size ?? null) : null,
      local_uri_hint: file.uri,
    },
    transcription,
  };
}

/**
 * Checks the same limits the backend enforces, so the user gets an
 * immediate answer instead of an upload that fails at the end.
 * Returns an error message, or null when the selection is acceptable.
 */
export function validateClipSelection(files: LocalHybridClip[]): string | null {
  if (files.length === 0) {
    return 'Select at least one clip.';
  }
  if (files.length > MAX_CLIPS_PER_PROJECT) {
    return `Select at most ${MAX_CLIPS_PER_PROJECT} clips. You picked ${files.length}.`;
  }

  const known = files.map((file) => file.durationSeconds).filter((value): value is number => Number.isFinite(value as number));
  if (known.length === files.length) {
    const total = known.reduce((sum, value) => sum + value, 0);
    if (total > MAX_TOTAL_DURATION_SECONDS) {
      const minutes = Math.round(total / 60);
      return `Your clips add up to about ${minutes} minutes. The limit is ${MAX_TOTAL_DURATION_SECONDS / 60} minutes.`;
    }
  }
  return null;
}

export async function analyzeHybridProjectOnBackend(payload: {
  token: string;
  name: string;
  files: LocalHybridClip[];
  settings?: Partial<ProjectSettingsPayload>;
  onProgress?: (done: number, total: number) => void;
}): Promise<ProjectAnalysisResult> {
  const problem = validateClipSelection(payload.files);
  if (problem) throw new Error(problem);

  const tracks: HybridTrackPayload[] = [];
  for (const file of payload.files) {
    tracks.push(await buildHybridTrackPayload(payload.token, file));
    payload.onProgress?.(tracks.length, payload.files.length);
  }

  return api.analyzeHybridProject(payload.token, {
    name: payload.name,
    tracks,
    settings: payload.settings,
    render_strategy: 'on_device',
  });
}
