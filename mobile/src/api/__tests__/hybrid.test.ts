import { describe, expect, it, jest, beforeEach } from '@jest/globals';

jest.mock('expo-file-system', () => ({
  getInfoAsync: jest.fn(async () => ({
    exists: true,
    size: 4096,
    modificationTime: 1711900800,
  })),
}));

const mockAnalyzeHybridProject = jest.fn(async (payload: any) => ({
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

jest.mock('../client', () => ({
  api: {
    analyzeHybridProject: (payload: any) => mockAnalyzeHybridProject(payload),
  },
}));

import { analyzeHybridProjectOnBackend, buildHybridTrackPayload } from '../hybrid';

describe('hybrid mobile analysis', () => {
  beforeEach(() => {
    mockAnalyzeHybridProject.mockClear();
    global.fetch = jest.fn(async () => ({
      ok: true,
      json: async () => ({
        text: 'hello world',
        language: 'en',
        words: [
          { text: 'hello', start: 0.0, end: 0.4 },
          { text: 'world', start: 0.6, end: 1.0 },
        ],
        segments: [
          {
            start: 0.0,
            end: 1.0,
            text: 'hello world',
            words: [
              { text: 'hello', start: 0.0, end: 0.4 },
              { text: 'world', start: 0.6, end: 1.0 },
            ],
          },
        ],
      }),
      text: async () => '',
    })) as any;
  });

  it('builds a hybrid track payload from local clip metadata and direct whisper transcription', async () => {
    const payload = await buildHybridTrackPayload({
      uri: 'file:///clips/clip-a.mov',
      name: 'clip-a.mov',
      mimeType: 'video/quicktime',
      lastModified: 1711900800000,
    });

    expect(payload.filename).toBe('clip-a.mov');
    expect(payload.duration).toBe(1);
    expect(payload.recorded_at).toBe('2024-03-31T16:00:00.000Z');
    expect(payload.metadata.size_bytes).toBe(4096);
    expect(payload.transcription.text).toBe('hello world');
  });

  it('sends only compact track analysis payloads to the backend', async () => {
    const project = await analyzeHybridProjectOnBackend({
      name: 'Hybrid Edit',
      files: [
        {
          uri: 'file:///clips/clip-a.mov',
          name: 'clip-a.mov',
          mimeType: 'video/quicktime',
        },
      ],
    });

    expect(project.id).toBe('project-123');
    expect(mockAnalyzeHybridProject).toHaveBeenCalledTimes(1);
    const request = mockAnalyzeHybridProject.mock.calls[0][0] as any;
    expect(request.render_strategy).toBe('on_device');
    expect(request.tracks).toHaveLength(1);
    expect(request.tracks[0].filename).toBe('clip-a.mov');
    expect(request.tracks[0].source_reference).toBe('file:///clips/clip-a.mov');
    expect(request.tracks[0].transcription.text).toBe('hello world');
  });
});
