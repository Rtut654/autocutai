import { describe, expect, it, jest, beforeEach } from '@jest/globals';

const mockUploadAsync = jest.fn();
const mockCreateUploadTask = jest.fn(() => ({ uploadAsync: mockUploadAsync }));
const mockDownloadAsync = jest.fn();

jest.mock('expo-file-system', () => ({
  cacheDirectory: 'file:///cache/',
  FileSystemUploadType: { MULTIPART: 'multipart' },
  createUploadTask: (...args: any[]) => (mockCreateUploadTask as any)(...args),
  createDownloadResumable: () => ({ downloadAsync: mockDownloadAsync }),
}));

import {
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
      expect(call[2].headers.Authorization).toBe(`Bearer ${TOKEN}`);
    }
  });

  it('requests a vertical edit with pause cutting and subtitles on', async () => {
    mockUploadAsync.mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')]);

    const options = (mockCreateUploadTask.mock.calls[0] as any[])[2];
    expect(options.parameters.aspect_ratio).toBe('vertical');
    expect(options.parameters.smart_pause_cutter).toBe('true');
    expect(options.parameters.generate_subtitles).toBe('true');
    expect(options.uploadType).toBe('multipart');
    expect(options.fieldName).toBe('files');
  });

  it('passes each clip capture time through so ordering is chronological', async () => {
    mockUploadAsync.mockResolvedValueOnce(uploadResponse('project-1') as never);

    await createProjectFromClips(TOKEN, 'Trip', [clip('a.mov')]);

    const options = (mockCreateUploadTask.mock.calls[0] as any[])[2];
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
      call[3]({ totalBytesSent: 50, totalBytesExpectedToSend: 100 });
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
});

describe('downloadFinalVideo', () => {
  beforeEach(() => {
    mockDownloadAsync.mockReset();
  });

  it('returns the local uri of the downloaded file', async () => {
    mockDownloadAsync.mockResolvedValueOnce({ status: 200, uri: 'file:///cache/autocut-p1.mp4' } as never);

    await expect(downloadFinalVideo(TOKEN, 'p1')).resolves.toBe('file:///cache/autocut-p1.mp4');
  });

  it('throws when the download fails', async () => {
    mockDownloadAsync.mockResolvedValueOnce({ status: 404, uri: '' } as never);

    await expect(downloadFinalVideo(TOKEN, 'p1')).rejects.toThrow(/could not download/i);
  });
});
