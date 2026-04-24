import * as FileSystem from 'expo-file-system';

import { api, ProjectAnalysisResult, ProjectSettingsPayload } from './client';

export const MOBILE_WHISPER_API_URL = process.env.EXPO_PUBLIC_WHISPER_API_URL || 'https://testsucceed.com/whisper';

export type LocalHybridClip = {
  uri: string;
  name: string;
  mimeType?: string;
  lastModified?: number | null;
  recordedAt?: string | null;
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

export async function transcribeClipDirect(file: LocalHybridClip, language = 'en'): Promise<ClientTranscriptionPayload> {
  const form = new FormData();
  form.append('audio', {
    uri: file.uri,
    name: file.name,
    type: file.mimeType || 'video/mp4',
  } as any);
  form.append('response_format', 'verbose_json');
  form.append('timestamp_granularities', 'word');
  form.append('language', language);

  const res = await fetch(MOBILE_WHISPER_API_URL, {
    method: 'POST',
    body: form,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Whisper request failed: ${res.status}`);
  }

  return normalizeTranscription(await res.json());
}

export async function buildHybridTrackPayload(file: LocalHybridClip): Promise<HybridTrackPayload> {
  const info = await FileSystem.getInfoAsync(file.uri);
  const transcription = await transcribeClipDirect(file);

  return {
    filename: file.name,
    duration: estimateDurationSeconds(transcription),
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

export async function analyzeHybridProjectOnBackend(payload: {
  name: string;
  files: LocalHybridClip[];
  settings?: Partial<ProjectSettingsPayload>;
}): Promise<ProjectAnalysisResult> {
  const tracks: HybridTrackPayload[] = [];
  for (const file of payload.files) {
    tracks.push(await buildHybridTrackPayload(file));
  }

  return api.analyzeHybridProject({
    name: payload.name,
    tracks,
    settings: payload.settings,
    render_strategy: 'on_device',
  });
}
