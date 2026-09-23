import React from 'react';
import { describe, expect, it, jest, beforeEach } from '@jest/globals';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('../../api/projects', () => {
  const actual = jest.requireActual('../../api/projects') as Record<string, unknown>;
  return {
    ...actual,
    createProjectFromClips: jest.fn(),
  };
});

import * as ImagePicker from 'expo-image-picker';
import UploadScreen from '../UploadScreen';

const projectsApi = jest.requireMock('../../api/projects') as Record<string, jest.Mock>;
const picker = ImagePicker as unknown as Record<string, jest.Mock>;

function asset(name: string, seconds: number) {
  return { uri: `file:///dcim/${name}`, fileName: name, duration: seconds * 1000, fileSize: 1024, mimeType: 'video/quicktime' };
}

describe('UploadScreen', () => {
  beforeEach(() => {
    projectsApi.createProjectFromClips.mockReset();
    projectsApi.createProjectFromClips.mockResolvedValue({ id: 'project-9' } as never);
    picker.launchImageLibraryAsync.mockReset();
    picker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [asset('lisbon.mov', 42), asset('tram.mov', 18)],
    } as never);
  });

  it('asks for videos only, up to the clip limit', async () => {
    const { getByText } = render(<UploadScreen token="t" onProjectCreated={jest.fn()} />);

    fireEvent.press(getByText('Choose clips from library'));

    await waitFor(() => expect(picker.launchImageLibraryAsync).toHaveBeenCalled());
    const [options] = picker.launchImageLibraryAsync.mock.calls[0] as any[];
    expect(options.mediaTypes).toEqual(['videos']);
    expect(options.allowsMultipleSelection).toBe(true);
    expect(options.selectionLimit).toBe(10);
  });

  it('shows the picked clips with their total length and the look options', async () => {
    const { getByText } = render(<UploadScreen token="t" onProjectCreated={jest.fn()} />);

    fireEvent.press(getByText('Choose clips from library'));

    await waitFor(() => expect(getByText('lisbon.mov')).toBeTruthy());
    expect(getByText(/2 clips · 1:00/)).toBeTruthy();
    expect(getByText('Look of the edit')).toBeTruthy();
  });

  it('uploads with the look the user chose and opens the project', async () => {
    const onCreated = jest.fn();
    const { getByText, getByLabelText } = render(<UploadScreen token="t" onProjectCreated={onCreated} />);
    fireEvent.press(getByText('Choose clips from library'));
    await waitFor(() => getByText('Look of the edit'));

    fireEvent.press(getByLabelText('Captions: Boxed'));
    fireEvent.press(getByLabelText('Longest scenery shot: Full'));
    fireEvent.press(getByText('Upload and analyse'));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('project-9'));
    const [, , clips, , options] = projectsApi.createProjectFromClips.mock.calls[0] as any[];
    expect(clips).toHaveLength(2);
    expect(options).toEqual({ caption_style: 'boxed', fill_mode: 'blur', audio_cleanup: true, broll_max_seconds: 0 });
  });

  it('reads clip durations from the picker in seconds', async () => {
    const { getByText } = render(<UploadScreen token="t" onProjectCreated={jest.fn()} />);
    fireEvent.press(getByText('Choose clips from library'));
    await waitFor(() => getByText('Look of the edit'));

    fireEvent.press(getByText('Upload and analyse'));

    await waitFor(() => expect(projectsApi.createProjectFromClips).toHaveBeenCalled());
    const [, , clips] = projectsApi.createProjectFromClips.mock.calls[0] as any[];
    expect(clips.map((clip: any) => clip.durationSeconds)).toEqual([42, 18]);
  });

  it('refuses a selection over the time limit before uploading', async () => {
    picker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [asset('long1.mov', 600), asset('long2.mov', 600)],
    } as never);
    const { getByText, queryByText } = render(<UploadScreen token="t" onProjectCreated={jest.fn()} />);

    fireEvent.press(getByText('Choose clips from library'));

    await waitFor(() => expect(picker.launchImageLibraryAsync).toHaveBeenCalled());
    expect(queryByText('Upload and analyse')).toBeNull();
    expect(projectsApi.createProjectFromClips).not.toHaveBeenCalled();
  });
});
