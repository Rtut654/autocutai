import { describe, expect, it, jest, beforeEach } from '@jest/globals';

const mockUploadAsync = jest.fn();
const mockCreateUploadTask = jest.fn((_url: string, options: any) => ({
  uploadAsync: mockUploadAsync,
  options,
}));
const mockDownloadFileAsync = jest.fn();

jest.mock('expo-file-system', () => {
  class File {
    uri: string;
    constructor(...parts: any[]) {
      this.uri = parts.map((part) => (typeof part === 'string' ? part : part.uri)).join('/');
    }
    createUploadTask(url: string, options: any) {
      return (mockCreateUploadTask as any)(url, options);
    }
    static downloadFileAsync(...args: any[]) {
      return (mockDownloadFileAsync as any)(...args);
    }
  }
  return {
    File,
    Paths: { cache: { uri: 'file:///cache' } },
    UploadType: { BINARY_CONTENT: 0, MULTIPART: 1 },
  };
});

const mockFetch = jest.fn();
(global as any).fetch = mockFetch;

import {
  DEFAULT_EDIT_OPTIONS,
  editOptionsOf,
  updateEditOptions,
  MAX_CLIPS_PER_PROJECT,
  MAX_TOTAL_DURATION_SECONDS,
  PickedClip,
  createProjectFromClips,
  downloadFinalVideo,
  totalBytes,
  totalDuration,
  validateSelection,
} from '../projects';

const TOKEN = 'test-token';

function clip(name: string, durationSeconds: number | null = 60, fileSizeBytes = 1024): PickedClip {
  return {
    uri: `file:///dcim/${name}`,
    fileName: name,
    durationSeconds,
    recordedAt: '2026-05-01T09:00:00Z',
    fileSizeBytes,
  };
}

function uploadResponse(projectId: string, status = 200) {
  return { status, body: JSON.stringify({ project: { id: projectId, name: 'Trip', status: 'draft', tracks: [] } }) };
}

describe('selection limits', () => {
  it('accepts a selection inside the limits', () => {
    expect(validateSelection([clip('a.mov', 60), clip('b.mov', 60)])).toBeNull();
  });

  it('rejects an empty selection', () => {
    expect(validateSelection([])).toMatch(/at least one clip/i);
  });

  it('rejects more clips than the backend allows', () => {
    const tooMany = Array.from({ length: MAX_CLIPS_PER_PROJECT + 1 }, (_, i) => clip(`c${i}.mov`, 10));
    expect(validateSelection(tooMany)).toMatch(/at most 10 clips/i);
  });

  it('accepts exactly the maximum number of clips', () => {
    const exactly = Array.from({ length: MAX_CLIPS_PER_PROJECT }, (_, i) => clip(`c${i}.mov`, 10));
    expect(validateSelection(exactly)).toBeNull();
  });

  it('rejects a selection over the total duration cap', () => {
    expect(validateSelection([clip('a.mov', 600), clip('b.mov', 600)])).toMatch(/15 minutes/i);
  });

  it('accepts a selection exactly at the duration cap', () => {
    expect(validateSelection([clip('a.mov', MAX_TOTAL_DURATION_SECONDS)])).toBeNull();
  });

  it('lets a selection through when a duration is unknown', () => {
    // The backend re-checks once ffprobe has read the real durations.
    expect(validateSelection([clip('a.mov', null), clip('b.mov', 60)])).toBeNull();
  });

  it('sums durations and sizes for display', () => {
    const clips = [clip('a.mov', 30, 1000), clip('b.mov', 45, 2000)];
    expect(totalDuration(clips)).toBe(75);
    expect(totalBytes(clips)).toBe(3000);
  });
});

describe('createProjectFromClips', () => {
  beforeEach(() => {
    mockCreateUploadTask.mockClear();
    mockUploadAsync.mockReset();
  });

  it('refuses an oversized selection before uploading anything', async () => {
    await expect(
      createProjectFromClips(TOKEN, 'Trip', [clip('a.mov', 600), clip('b.mov', 600)]),
    ).rejects.toThrow(/15 minutes/i);

    expect(mockCreateUploadTask).not.toHaveBeenCalled();
  });

  it('creates the project with the first clip and adds the rest as tracks', async () => {
    mockUploadAsync
      .mockResolvedValueOnce(uploadResponse('project-1') as never)
      .mockResolvedValueOnce(uploadResponse('project-1') as never);

    const project = await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov'), clip('b.mov')]);

    expect(project.id).toBe('project-1');
    const [firstUrl] = mockCreateUploadTask.mock.calls[0] as any[];
    const [secondUrl] = mockCreateUploadTask.mock.calls[1] as any[];
    expect(firstUrl).toMatch(/\/api\/projects\/$/);
    expect(secondUrl).toMatch(/\/api\/projects\/project-1\/tracks$/);
  });

  it('sends the auth token on every upload', async () => {
    mockUploadAsync
      .mockResolvedValueOnce(uploadResponse('project-1') as never)
      .mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov'), clip('b.mov')]);

    for (const call of mockCreateUploadTask.mock.calls as any[]) {
      expect(call[1].headers.Authorization).toBe(`Bearer ${TOKEN}`);
    }
  });

  it('requests a vertical edit with pause cutting and subtitles on', async () => {
    mockUploadAsync.mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')]);

    const options = (mockCreateUploadTask.mock.calls[0] as any[])[1];
    expect(options.parameters.aspect_ratio).toBe('vertical');
    expect(options.parameters.smart_pause_cutter).toBe('true');
    expect(options.parameters.generate_subtitles).toBe('true');
    expect(options.uploadType).toBe(1);
    expect(options.fieldName).toBe('files');
  });

  it('passes each clip capture time through so ordering is chronological', async () => {
    mockUploadAsync.mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')]);

    const options = (mockCreateUploadTask.mock.calls[0] as any[])[1];
    expect(JSON.parse(options.parameters.capture_times_json)).toEqual(['2026-05-01T09:00:00Z']);
  });

  it('reports progress per clip', async () => {
    mockUploadAsync
      .mockResolvedValueOnce(uploadResponse('project-1') as never)
      .mockResolvedValueOnce(uploadResponse('project-1') as never);
    const seen: Array<{ clipIndex: number; fraction: number }> = [];

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov'), clip('b.mov')], (progress) => {
      seen.push({ clipIndex: progress.clipIndex, fraction: progress.fraction });
    });

    // Drive the progress callbacks the upload task was given.
    for (const call of mockCreateUploadTask.mock.calls as any[]) {
      call[1].onProgress({ bytesSent: 50, totalBytes: 100 });
    }
    expect(seen.map((entry) => entry.clipIndex)).toEqual([0, 1]);
    expect(seen.every((entry) => entry.fraction === 0.5)).toBe(true);
  });

  it('surfaces the backend error detail when an upload is rejected', async () => {
    mockUploadAsync.mockResolvedValueOnce({
      status: 400,
      body: JSON.stringify({ detail: 'Select at most 10 clips. You selected 11.' }),
    } as never);

    await expect(createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')])).rejects.toThrow(
      /at most 10 clips/i,
    );
  });

  it('fails clearly when the upload returns nothing', async () => {
    mockUploadAsync.mockResolvedValueOnce(undefined as never);

    await expect(createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')])).rejects.toThrow(/Upload failed/i);
  });

  it('names the clip when the network drops mid-upload', async () => {
    mockUploadAsync.mockRejectedValueOnce(new Error('The network connection was lost.') as never);

    await expect(createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')])).rejects.toThrow(
      /a\.mov failed: The network connection was lost/,
    );
  });
});

describe('downloadFinalVideo', () => {
  beforeEach(() => {
    mockDownloadFileAsync.mockReset();
  });

  it('returns the local uri of the downloaded file', async () => {
    mockDownloadFileAsync.mockResolvedValueOnce({ uri: 'file:///cache/autocut-p1.mp4' } as never);

    await expect(downloadFinalVideo(TOKEN, 'p1')).resolves.toBe('file:///cache/autocut-p1.mp4');
  });

  it('downloads into the cache with auth and overwrites a previous export', async () => {
    mockDownloadFileAsync.mockResolvedValueOnce({ uri: 'file:///cache/autocut-p1.mp4' } as never);

    await downloadFinalVideo(TOKEN, 'p1');

    const [url, destination, options] = mockDownloadFileAsync.mock.calls[0] as any[];
    expect(url).toMatch(/\/api\/projects\/p1\/download$/);
    expect(destination.uri).toBe('file:///cache/autocut-p1.mp4');
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(options.idempotent).toBe(true);
  });

  it('throws a readable error when the download fails', async () => {
    mockDownloadFileAsync.mockRejectedValueOnce(new Error('HTTP 404') as never);

    await expect(downloadFinalVideo(TOKEN, 'p1')).rejects.toThrow(/could not download/i);
  });
});

describe('edit options', () => {
  beforeEach(() => {
    mockCreateUploadTask.mockClear();
    mockUploadAsync.mockReset();
    mockFetch.mockReset();
  });

  it('uploads with the research-backed defaults', async () => {
    mockUploadAsync.mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')]);

    const { parameters } = (mockCreateUploadTask.mock.calls[0] as any[])[1];
    expect(parameters.caption_style).toBe('bold');
    expect(parameters.fill_mode).toBe('blur');
    expect(parameters.audio_cleanup).toBe('true');
    expect(parameters.broll_max_seconds).toBe('6');
    expect(parameters.generate_subtitles).toBe('true');
  });

  it('sends the options the user picked', async () => {
    mockUploadAsync.mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')], undefined, {
      caption_style: 'none',
      fill_mode: 'crop',
      audio_cleanup: false,
      broll_max_seconds: 0,
    });

    const { parameters } = (mockCreateUploadTask.mock.calls[0] as any[])[1];
    expect(parameters.caption_style).toBe('none');
    expect(parameters.generate_subtitles).toBe('false');
    expect(parameters.fill_mode).toBe('crop');
    expect(parameters.audio_cleanup).toBe('false');
    expect(parameters.broll_max_seconds).toBe('0');
  });

  it('only sends options with the first clip; later clips just add tracks', async () => {
    mockUploadAsync
      .mockResolvedValueOnce(uploadResponse('project-1') as never)
      .mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov'), clip('b.mov')]);

    const second = (mockCreateUploadTask.mock.calls[1] as any[])[1];
    expect(second.parameters.caption_style).toBeUndefined();
  });

  it('patches only what changed, and switches subtitles with the caption style', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ project: { id: 'p1', settings: { caption_style: 'none' } } }),
    } as never);

    await updateEditOptions(TOKEN, 'p1', { caption_style: 'none' });

    const [url, init] = mockFetch.mock.calls[0] as any[];
    expect(url).toMatch(/\/api\/projects\/p1\/settings$/);
    expect(init.method).toBe('PATCH');
    expect(init.headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(init.body)).toEqual({ caption_style: 'none', generate_subtitles: false });
  });

  it('fills in defaults for projects created before these options existed', () => {
    expect(editOptionsOf({ id: 'p', name: 'x', status: 'draft', tracks: [] })).toEqual(DEFAULT_EDIT_OPTIONS);
    expect(
      editOptionsOf({ id: 'p', name: 'x', status: 'draft', tracks: [], settings: { caption_style: 'clean' } })
        .caption_style,
    ).toBe('clean');
  });
});
