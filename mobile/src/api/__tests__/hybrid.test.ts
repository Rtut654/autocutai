import { describe, expect, it, jest, beforeEach } from '@jest/globals';

jest.mock('expo-file-system', () => ({
  getInfoAsync: jest.fn(async () => ({
    exists: true,
    size: 4096,
    modificationTime: 1711900800,
  })),
}));

const mockAnalyzeHybridProject = jest.fn(async (_token: string, payload: any) => ({
  id: 'project-123',
  name: payload.name,
  status: 'completed',
  pipeline: {
    combined_transcript: 'hello world',
    gap_ranges: [],
    insertion_suggestions: [],
    render_plan: {},
  },
  tracks: [],
}));

const mockTranscribeMedia = jest.fn(async () => ({
  text: 'hello world',
  language: 'en-US',
  words: [
    { word: 'hello', start: 0.1, end: 0.5, confidence: 0.9 },
    { word: 'world', start: 0.6, end: 1.2, confidence: 0.9 },
  ],
  segments: [
    {
      start: 0.1,
      end: 1.2,
      text: 'hello world',
      words: [
        { word: 'hello', start: 0.1, end: 0.5 },
        { word: 'world', start: 0.6, end: 1.2 },
      ],
    },
  ],
}));

jest.mock('../client', () => ({
  api: {
    analyzeHybridProject: (token: string, payload: any) => mockAnalyzeHybridProject(token, payload),
  },
  transcribeMedia: (...args: any[]) => (mockTranscribeMedia as any)(...args),
}));

import {
  analyzeHybridProjectOnBackend,
  buildHybridTrackPayload,
  validateClipSelection,
  MAX_CLIPS_PER_PROJECT,
} from '../hybrid';

const TOKEN = 'test-token';

function clip(name: string, durationSeconds?: number) {
  return { uri: `file:///tmp/${name}`, name, mimeType: 'video/mp4', durationSeconds };
}

describe('clip selection limits', () => {
  it('accepts a selection inside the limits', () => {
    expect(validateClipSelection([clip('a.mp4', 60), clip('b.mp4', 60)])).toBeNull();
  });

  it('rejects an empty selection', () => {
    expect(validateClipSelection([])).toMatch(/at least one clip/i);
  });

  it('rejects more clips than the backend allows', () => {
    const tooMany = Array.from({ length: MAX_CLIPS_PER_PROJECT + 1 }, (_, i) => clip(`c${i}.mp4`, 10));
    expect(validateClipSelection(tooMany)).toMatch(/at most 10 clips/i);
  });

  it('rejects a selection over the total duration cap', () => {
    const long = [clip('a.mp4', 600), clip('b.mp4', 600)];
    expect(validateClipSelection(long)).toMatch(/15 minutes/i);
  });

  it('allows the selection through when durations are unknown', () => {
    // The backend re-checks once ffprobe has read the real durations.
    expect(validateClipSelection([clip('a.mp4'), clip('b.mp4')])).toBeNull();
  });
});

describe('hybrid track payload', () => {
  beforeEach(() => {
    mockAnalyzeHybridProject.mockClear();
    mockTranscribeMedia.mockClear();
  });

  it('transcribes through the backend rather than a third-party endpoint', async () => {
    await buildHybridTrackPayload(TOKEN, clip('a.mp4'));

    expect(mockTranscribeMedia).toHaveBeenCalledTimes(1);
    expect((mockTranscribeMedia as any).mock.calls[0][0]).toBe(TOKEN);
  });

  it('keeps the local file reference so the source never leaves the device', async () => {
    const payload = await buildHybridTrackPayload(TOKEN, clip('a.mp4'));

    expect(payload.source_reference).toBe('file:///tmp/a.mp4');
    expect(payload.metadata.hybrid_prepared_on_device).toBe(true);
  });

  it('prefers a known duration over one estimated from the transcript', async () => {
    const payload = await buildHybridTrackPayload(TOKEN, clip('a.mp4', 42));

    expect(payload.duration).toBe(42);
  });

  it('falls back to the transcript end when duration is unknown', async () => {
    const payload = await buildHybridTrackPayload(TOKEN, clip('a.mp4'));

    expect(payload.duration).toBe(1.2);
  });

  it('derives recorded_at from the file modification time', async () => {
    const payload = await buildHybridTrackPayload(TOKEN, clip('a.mp4'));

    expect(payload.recorded_at).toBe(new Date(1711900800 * 1000).toISOString());
  });
});

describe('analyzeHybridProjectOnBackend', () => {
  beforeEach(() => {
    mockAnalyzeHybridProject.mockClear();
    mockTranscribeMedia.mockClear();
  });

  it('sends the auth token and one track per clip', async () => {
    const result = await analyzeHybridProjectOnBackend({
      token: TOKEN,
      name: 'Trip',
      files: [clip('a.mp4', 30), clip('b.mp4', 30)],
    });

    expect(result.id).toBe('project-123');
    const [token, payload] = mockAnalyzeHybridProject.mock.calls[0] as any[];
    expect(token).toBe(TOKEN);
    expect(payload.tracks).toHaveLength(2);
    expect(payload.render_strategy).toBe('on_device');
  });

  it('reports progress as each clip is transcribed', async () => {
    const seen: Array<[number, number]> = [];

    await analyzeHybridProjectOnBackend({
      token: TOKEN,
      name: 'Trip',
      files: [clip('a.mp4', 30), clip('b.mp4', 30)],
      onProgress: (done, total) => seen.push([done, total]),
    });

    expect(seen).toEqual([
      [1, 2],
      [2, 2],
    ]);
  });

  it('refuses an oversized selection before uploading anything', async () => {
    await expect(
      analyzeHybridProjectOnBackend({
        token: TOKEN,
        name: 'Trip',
        files: [clip('a.mp4', 600), clip('b.mp4', 600)],
      }),
    ).rejects.toThrow(/15 minutes/i);

    expect(mockTranscribeMedia).not.toHaveBeenCalled();
    expect(mockAnalyzeHybridProject).not.toHaveBeenCalled();
  });
});
