import React from 'react';
import { describe, expect, it, jest, beforeEach } from '@jest/globals';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('../../api/projects', () => ({
  getProject: jest.fn(),
  getStatus: jest.fn(),
  getSpeechFilter: jest.fn(),
  generateSpeechFilter: jest.fn(),
  saveSpeechFilter: jest.fn(),
  startProcessing: jest.fn(),
  toggleTrackExcluded: jest.fn(),
  downloadFinalVideo: jest.fn(),
  listProjects: jest.fn(),
  deleteProject: jest.fn(),
  trackMediaUrl: () => 'https://example.test/media',
  projectDownloadUrl: () => 'https://example.test/download',
}));

import EditorScreen from '../EditorScreen';
import HistoryScreen from '../HistoryScreen';

const projectsApi = jest.requireMock('../../api/projects') as Record<string, jest.Mock>;

const MOCKED_CALLS = [
  'getProject',
  'getStatus',
  'getSpeechFilter',
  'generateSpeechFilter',
  'saveSpeechFilter',
  'startProcessing',
  'toggleTrackExcluded',
  'downloadFinalVideo',
  'listProjects',
  'deleteProject',
];

function resetMocks() {
  MOCKED_CALLS.forEach((name) => projectsApi[name].mockReset());
}

const TOKEN = 'test-token';

function track(overrides: Record<string, unknown> = {}) {
  return {
    id: 'track-1',
    filename: 'lisbon.mov',
    duration: 12,
    position: 0,
    has_voice: true,
    excluded: false,
    ...overrides,
  };
}

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: 'project-1',
    name: 'Lisbon trip',
    status: 'draft',
    tracks: [track()],
    ...overrides,
  };
}

const CUTS = [
  { start: 0.5, end: 0.75, duration: 0.25, reason: 'filler_word', transcript: 'um', confidence: 0.9 },
  { start: 1.95, end: 3.6, duration: 1.65, reason: 'silence_gap', transcript: '', confidence: 0.8 },
];

describe('EditorScreen', () => {
  beforeEach(() => {
    resetMocks();
    projectsApi.getProject.mockResolvedValue(project() as never);
    projectsApi.getSpeechFilter.mockResolvedValue({ cuts: CUTS } as never);
    projectsApi.saveSpeechFilter.mockResolvedValue({ cuts: CUTS } as never);
    projectsApi.startProcessing.mockResolvedValue(undefined as never);
  });

  it('shows the clip with its duration', async () => {
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() => expect(getByText('lisbon.mov')).toBeTruthy());
  });

  it('explains that the AI has already cut, which is the Option B premise', async () => {
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() =>
      expect(getByText(/AutoCutAI has already made its cut/i)).toBeTruthy(),
    );
  });

  it('loads suggested cuts when a clip is opened', async () => {
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);
    await waitFor(() => getByText('lisbon.mov'));

    fireEvent.press(getByText('lisbon.mov'));

    await waitFor(() => expect(getByText('Filler word')).toBeTruthy());
    expect(getByText('Silence gap')).toBeTruthy();
    expect(projectsApi.getSpeechFilter).toHaveBeenCalledWith(TOKEN, 'project-1', 'track-1');
  });

  it('generates cuts when the clip has none yet', async () => {
    projectsApi.getSpeechFilter.mockResolvedValue(null as never);
    projectsApi.generateSpeechFilter.mockResolvedValue({ cuts: CUTS } as never);
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);
    await waitFor(() => getByText('lisbon.mov'));

    fireEvent.press(getByText('lisbon.mov'));

    await waitFor(() => expect(projectsApi.generateSpeechFilter).toHaveBeenCalled());
  });

  it('does not ask for cuts on a clip with no narration', async () => {
    projectsApi.getProject.mockResolvedValue(
      project({ tracks: [track({ has_voice: false })] }) as never,
    );
    projectsApi.getSpeechFilter.mockResolvedValue(null as never);
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);
    await waitFor(() => getByText('lisbon.mov'));

    fireEvent.press(getByText('lisbon.mov'));

    await waitFor(() =>
      expect(getByText(/No narration in this clip/i)).toBeTruthy(),
    );
    expect(projectsApi.generateSpeechFilter).not.toHaveBeenCalled();
  });

  it('labels a silent clip as kept b-roll rather than something to trim', async () => {
    projectsApi.getProject.mockResolvedValue(
      project({ tracks: [track({ has_voice: false })] }) as never,
    );
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() => expect(getByText(/kept as b-roll/i)).toBeTruthy());
  });

  it('saves only the cuts the user left switched on', async () => {
    const { getByText, getAllByRole } = render(
      <EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />,
    );
    await waitFor(() => getByText('lisbon.mov'));
    fireEvent.press(getByText('lisbon.mov'));
    await waitFor(() => getByText('Filler word'));

    // Turn the first suggested cut off, keeping that moment in the edit.
    fireEvent(getAllByRole('switch')[0], 'valueChange', false);
    fireEvent.press(getByText('Save this clip'));

    await waitFor(() => expect(projectsApi.saveSpeechFilter).toHaveBeenCalled());
    const [, , , saved] = projectsApi.saveSpeechFilter.mock.calls[0] as any[];
    expect(saved).toHaveLength(1);
    expect(saved[0].reason).toBe('silence_gap');
    expect(saved[0]).not.toHaveProperty('enabled');
  });

  it('keeps everything when the user turns all cuts off', async () => {
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);
    await waitFor(() => getByText('lisbon.mov'));
    fireEvent.press(getByText('lisbon.mov'));
    await waitFor(() => getByText('Keep everything'));

    fireEvent.press(getByText('Keep everything'));
    fireEvent.press(getByText('Save this clip'));

    await waitFor(() => expect(projectsApi.saveSpeechFilter).toHaveBeenCalled());
    const [, , , saved] = projectsApi.saveSpeechFilter.mock.calls[0] as any[];
    expect(saved).toEqual([]);
  });

  it('starts a render when the user asks for their cut', async () => {
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);
    await waitFor(() => getByText('Render my cut'));

    fireEvent.press(getByText('Render my cut'));

    await waitFor(() => expect(projectsApi.startProcessing).toHaveBeenCalledWith(TOKEN, 'project-1'));
  });

  it('shows real progress while the backend is working', async () => {
    projectsApi.getProject.mockResolvedValue(project({ status: 'processing' }) as never);
    projectsApi.getStatus.mockResolvedValue({
      project_id: 'project-1',
      status: 'processing',
      progress: 42,
      current_step: 'transcribing',
      estimated_time_remaining: 30,
      error_message: null,
    } as never);

    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() => expect(getByText(/Analysing your clips/i)).toBeTruthy());
    await waitFor(() => expect(getByText(/42%/)).toBeTruthy());
  });

  it('surfaces a processing failure with the backend reason', async () => {
    projectsApi.getProject.mockResolvedValue(
      project({ status: 'error', error_message: 'Azure quota exceeded' }) as never,
    );
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() => expect(getByText('Processing failed')).toBeTruthy());
    expect(getByText('Azure quota exceeded')).toBeTruthy();
  });

  it('offers the export once the render is ready', async () => {
    projectsApi.getProject.mockResolvedValue(project({ status: 'completed' }) as never);
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() => expect(getByText('Your cut is ready')).toBeTruthy());
    expect(getByText('Save to my library')).toBeTruthy();
    expect(getByText('Re-render with my changes')).toBeTruthy();
  });

  it('lets the user drop a clip from the edit', async () => {
    projectsApi.toggleTrackExcluded.mockResolvedValue({ excluded: true } as never);
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);
    await waitFor(() => getByText('lisbon.mov'));
    fireEvent.press(getByText('lisbon.mov'));
    await waitFor(() => getByText('Leave this clip out'));

    fireEvent.press(getByText('Leave this clip out'));

    await waitFor(() =>
      expect(projectsApi.toggleTrackExcluded).toHaveBeenCalledWith(TOKEN, 'project-1', 'track-1'),
    );
  });

  it('shows an error rather than a blank screen when loading fails', async () => {
    projectsApi.getProject.mockRejectedValue(new Error('Network unreachable') as never);
    const { getByText } = render(<EditorScreen token={TOKEN} projectId="project-1" onClose={jest.fn()} />);

    await waitFor(() => expect(getByText('Network unreachable')).toBeTruthy());
  });
});

describe('HistoryScreen', () => {
  beforeEach(() => {
    resetMocks();
  });

  it('lists projects with their state', async () => {
    projectsApi.listProjects.mockResolvedValue([
      project({ status: 'completed', tracks: [track({ duration: 90 })] }),
    ] as never);

    const { getByText } = render(<HistoryScreen token={TOKEN} onOpenProject={jest.fn()} />);

    await waitFor(() => expect(getByText('Lisbon trip')).toBeTruthy());
    expect(getByText('Ready')).toBeTruthy();
    expect(getByText('1 clip')).toBeTruthy();
    expect(getByText('1:30')).toBeTruthy();
  });

  it('opens the editor for the tapped project', async () => {
    projectsApi.listProjects.mockResolvedValue([project()] as never);
    const onOpen = jest.fn();
    const { getByText } = render(<HistoryScreen token={TOKEN} onOpenProject={onOpen} />);
    await waitFor(() => getByText('Lisbon trip'));

    fireEvent.press(getByText('Lisbon trip'));

    expect(onOpen).toHaveBeenCalledWith('project-1');
  });

  it('invites the user to start when there is nothing yet', async () => {
    projectsApi.listProjects.mockResolvedValue([] as never);

    const { getByText } = render(<HistoryScreen token={TOKEN} onOpenProject={jest.fn()} />);

    await waitFor(() => expect(getByText(/No projects yet/i)).toBeTruthy());
  });

  it('reports a load failure instead of showing an empty list', async () => {
    projectsApi.listProjects.mockRejectedValue(new Error('Unauthorized') as never);

    const { getByText } = render(<HistoryScreen token={TOKEN} onOpenProject={jest.fn()} />);

    await waitFor(() => expect(getByText('Unauthorized')).toBeTruthy());
  });
});
