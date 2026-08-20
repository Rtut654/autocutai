import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import * as MediaLibrary from 'expo-media-library';
import { useVideoPlayer, VideoView } from 'expo-video';

import {
  Project,
  ProjectTrack,
  SpeechFilterArtifact,
  SpeechFilterCut,
  downloadFinalVideo,
  generateSpeechFilter,
  getProject,
  getSpeechFilter,
  getStatus,
  projectDownloadUrl,
  saveSpeechFilter,
  startProcessing,
  toggleTrackExcluded,
  trackMediaUrl,
} from '../api/projects';

type Props = {
  token: string;
  projectId: string;
  onClose: () => void;
};

/** A cut plus whether the user currently wants it applied. */
type EditableCut = SpeechFilterCut & { enabled: boolean };

const POLL_INTERVAL_MS = 2500;

function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${minutes}:${rest.toFixed(1).padStart(4, '0')}`;
}

function formatReason(reason: string): string {
  const label = reason.replace(/_/g, ' ').replace(/\+/g, ' and ');
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function keptDuration(track: ProjectTrack, cuts: EditableCut[]): number {
  const removed = cuts.filter((cut) => cut.enabled).reduce((sum, cut) => sum + (cut.end - cut.start), 0);
  return Math.max(0, (track.duration || 0) - removed);
}

export default function EditorScreen({ token, projectId, onClose }: Props) {
  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState<{ progress: number; step: string; eta: number | null } | null>(null);
  const [cutsByTrack, setCutsByTrack] = useState<Record<string, EditableCut[]>>({});
  const [loadingTrackId, setLoadingTrackId] = useState<string | null>(null);
  const [savingTrackId, setSavingTrackId] = useState<string | null>(null);
  const [openTrackId, setOpenTrackId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exportedUri, setExportedUri] = useState<string | null>(null);

  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const fresh = await getProject(token, projectId);
      setProject(fresh);
      return fresh;
    } catch (err: any) {
      setError(err?.message || 'Could not load this project.');
      return null;
    }
  }, [token, projectId]);

  useEffect(() => {
    load();
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [load]);

  // While the backend is working, poll for real progress.
  useEffect(() => {
    if (!project || project.status !== 'processing') return;

    let cancelled = false;
    const tick = async () => {
      try {
        const next = await getStatus(token, projectId);
        if (cancelled) return;
        setStatus({ progress: next.progress, step: next.current_step, eta: next.estimated_time_remaining });
        if (next.status === 'completed' || next.status === 'error') {
          await load();
          return;
        }
      } catch {
        // Transient; the next tick retries.
      }
      if (!cancelled) pollTimer.current = setTimeout(tick, POLL_INTERVAL_MS);
    };

    tick();
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [project?.status, token, projectId, load]);

  const visibleTracks = useMemo(
    () => (project?.tracks || []).slice().sort((a, b) => a.position - b.position),
    [project],
  );

  const openTrack = async (track: ProjectTrack) => {
    if (openTrackId === track.id) {
      setOpenTrackId(null);
      return;
    }
    setOpenTrackId(track.id);
    if (cutsByTrack[track.id]) return;

    setLoadingTrackId(track.id);
    try {
      let artifact: SpeechFilterArtifact | null = await getSpeechFilter(token, projectId, track.id);
      if (!artifact && track.has_voice) {
        artifact = await generateSpeechFilter(token, projectId, track.id);
      }
      setCutsByTrack((current) => ({
        ...current,
        [track.id]: (artifact?.cuts || []).map((cut) => ({ ...cut, enabled: true })),
      }));
    } catch (err: any) {
      setError(err?.message || 'Could not load suggested cuts for this clip.');
      setCutsByTrack((current) => ({ ...current, [track.id]: [] }));
    } finally {
      setLoadingTrackId(null);
    }
  };

  const toggleCut = (trackId: string, index: number) => {
    setCutsByTrack((current) => {
      const cuts = current[trackId] || [];
      const next = cuts.map((cut, position) => (position === index ? { ...cut, enabled: !cut.enabled } : cut));
      return { ...current, [trackId]: next };
    });
  };

  const setAllCuts = (trackId: string, enabled: boolean) => {
    setCutsByTrack((current) => ({
      ...current,
      [trackId]: (current[trackId] || []).map((cut) => ({ ...cut, enabled })),
    }));
  };

  const saveTrack = async (track: ProjectTrack) => {
    const cuts = cutsByTrack[track.id] || [];
    setSavingTrackId(track.id);
    try {
      // Only enabled cuts are sent: the backend treats the saved list as the
      // complete set of ranges to remove from this clip.
      const enabled = cuts
        .filter((cut) => cut.enabled)
        .map(({ enabled: _enabled, ...cut }) => cut);
      await saveSpeechFilter(token, projectId, track.id, enabled);
      setError(null);
    } catch (err: any) {
      setError(err?.message || 'Could not save your changes.');
    } finally {
      setSavingTrackId(null);
    }
  };

  const toggleExcluded = async (track: ProjectTrack) => {
    try {
      await toggleTrackExcluded(token, projectId, track.id);
      await load();
    } catch (err: any) {
      setError(err?.message || 'Could not update this clip.');
    }
  };

  const renderFinal = async () => {
    setBusy('Starting render');
    try {
      // Persist every clip the user opened before rendering.
      for (const track of visibleTracks) {
        if (cutsByTrack[track.id]) await saveTrack(track);
      }
      await startProcessing(token, projectId);
      setExportedUri(null);
      await load();
    } catch (err: any) {
      setError(err?.message || 'Could not start the render.');
    } finally {
      setBusy(null);
    }
  };

  const saveToLibrary = async () => {
    setBusy('Saving to your library');
    try {
      const permission = await MediaLibrary.requestPermissionsAsync();
      if (!permission.granted) {
        Alert.alert('Photo access needed', 'AutoCutAI needs permission to save the finished video.');
        return;
      }
      const localUri = await downloadFinalVideo(token, projectId);
      await MediaLibrary.saveToLibraryAsync(localUri);
      setExportedUri(localUri);
      Alert.alert('Saved', 'Your edit is in your photo library.');
    } catch (err: any) {
      Alert.alert('Export failed', err?.message || 'Could not save the video.');
    } finally {
      setBusy(null);
    }
  };

  if (!project) {
    return (
      <SafeAreaView style={styles.container} edges={['top']}>
        <View style={styles.center}>
          {error ? <Text style={styles.errorText}>{error}</Text> : <ActivityIndicator size="large" color="#031b33" />}
          <Pressable style={styles.ghostButton} onPress={onClose}>
            <Text style={styles.ghostButtonText}>Back</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  const isProcessing = project.status === 'processing';
  const isReady = project.status === 'completed';

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Pressable onPress={onClose} hitSlop={12}>
          <MaterialCommunityIcons name="chevron-left" size={28} color="#031b33" />
        </Pressable>
        <Text style={styles.title} numberOfLines={1}>
          {project.name}
        </Text>
      </View>

      {error ? <Text style={styles.errorBanner}>{error}</Text> : null}

      <ScrollView contentContainerStyle={styles.scroll}>
        {isProcessing ? (
          <View style={styles.statusCard}>
            <ActivityIndicator color="#185FA5" />
            <View style={{ flex: 1 }}>
              <Text style={styles.statusTitle}>
                {status?.step === 'rendering' ? 'Rendering your cut' : 'Analysing your clips'}
              </Text>
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: `${Math.round(status?.progress ?? 5)}%` }]} />
              </View>
              <Text style={styles.statusMeta}>
                {Math.round(status?.progress ?? 0)}%
                {status?.eta ? ` · about ${status.eta}s left` : ''}
              </Text>
            </View>
          </View>
        ) : null}

        {project.status === 'error' ? (
          <View style={styles.errorCard}>
            <Text style={styles.statusTitle}>Processing failed</Text>
            <Text style={styles.statusMeta}>{project.error_message || 'Unknown error'}</Text>
          </View>
        ) : null}

        {isReady ? (
          <FinalPreview
            token={token}
            projectId={projectId}
            onSave={saveToLibrary}
            busy={busy}
            exported={Boolean(exportedUri)}
          />
        ) : null}

        <Text style={styles.sectionTitle}>Clips</Text>
        <Text style={styles.sectionHint}>
          AutoCutAI has already made its cut. Open a clip to see what it removed and turn anything back on.
        </Text>

        {visibleTracks.map((track) => {
          const cuts = cutsByTrack[track.id] || [];
          const isOpen = openTrackId === track.id;
          const enabledCount = cuts.filter((cut) => cut.enabled).length;

          return (
            <View key={track.id} style={[styles.trackCard, track.excluded && styles.trackCardExcluded]}>
              <Pressable style={styles.trackHead} onPress={() => openTrack(track)}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.trackName} numberOfLines={1}>
                    {track.filename}
                  </Text>
                  <Text style={styles.trackMeta}>
                    {formatTime(track.duration)}
                    {cuts.length > 0
                      ? ` → ${formatTime(keptDuration(track, cuts))} · ${enabledCount} of ${cuts.length} cuts on`
                      : track.has_voice
                        ? ' · narration detected'
                        : ' · no narration, kept as b-roll'}
                  </Text>
                </View>
                <MaterialCommunityIcons
                  name={isOpen ? 'chevron-up' : 'chevron-down'}
                  size={22}
                  color="#60748a"
                />
              </Pressable>

              {isOpen ? (
                <View style={styles.trackBody}>
                  <TrackPreview projectId={projectId} trackId={track.id} token={token} />

                  {loadingTrackId === track.id ? (
                    <ActivityIndicator color="#185FA5" />
                  ) : cuts.length === 0 ? (
                    <Text style={styles.trackMeta}>
                      {track.has_voice
                        ? 'Nothing to trim in this clip.'
                        : 'No narration in this clip, so nothing is being cut.'}
                    </Text>
                  ) : (
                    <>
                      <View style={styles.bulkRow}>
                        <Pressable onPress={() => setAllCuts(track.id, true)}>
                          <Text style={styles.link}>Apply all</Text>
                        </Pressable>
                        <Pressable onPress={() => setAllCuts(track.id, false)}>
                          <Text style={styles.link}>Keep everything</Text>
                        </Pressable>
                      </View>

                      {cuts.map((cut, index) => (
                        <View key={`${cut.start}-${cut.end}-${index}`} style={styles.cutRow}>
                          <View style={{ flex: 1 }}>
                            <Text style={styles.cutReason}>{formatReason(cut.reason)}</Text>
                            <Text style={styles.cutMeta}>
                              {formatTime(cut.start)}–{formatTime(cut.end)} · {(cut.end - cut.start).toFixed(1)}s
                            </Text>
                            {cut.transcript ? (
                              <Text style={styles.cutTranscript} numberOfLines={2}>
                                “{cut.transcript}”
                              </Text>
                            ) : null}
                          </View>
                          <Switch
                            value={cut.enabled}
                            onValueChange={() => toggleCut(track.id, index)}
                            trackColor={{ true: '#185FA5', false: '#cfdded' }}
                          />
                        </View>
                      ))}

                      <Pressable
                        style={[styles.saveButton, savingTrackId === track.id && styles.disabled]}
                        onPress={() => saveTrack(track)}
                        disabled={savingTrackId === track.id}
                      >
                        {savingTrackId === track.id ? (
                          <ActivityIndicator color="#fff" size="small" />
                        ) : (
                          <Text style={styles.saveButtonText}>Save this clip</Text>
                        )}
                      </Pressable>
                    </>
                  )}

                  <Pressable style={styles.excludeRow} onPress={() => toggleExcluded(track)}>
                    <MaterialCommunityIcons
                      name={track.excluded ? 'eye-off-outline' : 'eye-outline'}
                      size={17}
                      color="#60748a"
                    />
                    <Text style={styles.link}>
                      {track.excluded ? 'Bring this clip back' : 'Leave this clip out'}
                    </Text>
                  </Pressable>
                </View>
              ) : null}
            </View>
          );
        })}

        <Pressable
          style={[styles.renderButton, (isProcessing || busy !== null) && styles.disabled]}
          onPress={renderFinal}
          disabled={isProcessing || busy !== null}
        >
          {busy ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <Text style={styles.saveButtonText}>
              {isReady ? 'Re-render with my changes' : 'Render my cut'}
            </Text>
          )}
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function TrackPreview({ projectId, trackId, token }: { projectId: string; trackId: string; token: string }) {
  const source = useMemo(
    () => ({ uri: trackMediaUrl(projectId, trackId), headers: { Authorization: `Bearer ${token}` } }),
    [projectId, trackId, token],
  );
  const player = useVideoPlayer(source, (instance) => {
    instance.loop = false;
  });

  return <VideoView style={styles.preview} player={player} contentFit="contain" nativeControls />;
}

function FinalPreview({
  token,
  projectId,
  onSave,
  busy,
  exported,
}: {
  token: string;
  projectId: string;
  onSave: () => void;
  busy: string | null;
  exported: boolean;
}) {
  const source = useMemo(
    () => ({ uri: projectDownloadUrl(projectId), headers: { Authorization: `Bearer ${token}` } }),
    [projectId, token],
  );
  const player = useVideoPlayer(source, (instance) => {
    instance.loop = false;
  });

  return (
    <View style={styles.finalCard}>
      <Text style={styles.statusTitle}>Your cut is ready</Text>
      <VideoView style={styles.finalPreview} player={player} contentFit="contain" nativeControls />
      <Pressable style={[styles.saveButton, busy !== null && styles.disabled]} onPress={onSave} disabled={busy !== null}>
        {busy ? (
          <ActivityIndicator color="#fff" size="small" />
        ) : (
          <Text style={styles.saveButtonText}>{exported ? 'Save again' : 'Save to my library'}</Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff' },
  header: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 14, paddingVertical: 10 },
  title: { flex: 1, fontSize: 22, fontWeight: '700', color: '#031b33' },
  scroll: { padding: 16, paddingTop: 4, gap: 12, paddingBottom: 40 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 14 },
  errorText: { color: '#a83229', textAlign: 'center', paddingHorizontal: 24 },
  errorBanner: {
    marginHorizontal: 16,
    marginBottom: 6,
    color: '#a83229',
    backgroundColor: '#f8e7e5',
    borderRadius: 8,
    padding: 10,
    fontSize: 13,
  },
  statusCard: {
    flexDirection: 'row',
    gap: 12,
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#cfdded',
    padding: 14,
  },
  errorCard: {
    backgroundColor: '#f8e7e5',
    borderRadius: 12,
    padding: 14,
    gap: 4,
  },
  statusTitle: { fontWeight: '700', color: '#0b2845', fontSize: 15 },
  statusMeta: { color: '#60748a', fontSize: 13, marginTop: 4 },
  progressTrack: { height: 6, borderRadius: 3, backgroundColor: '#e3ecf7', marginTop: 8, overflow: 'hidden' },
  progressFill: { height: 6, backgroundColor: '#185FA5' },
  sectionTitle: { fontSize: 18, fontWeight: '700', color: '#031b33', marginTop: 6 },
  sectionHint: { color: '#60748a', fontSize: 13, lineHeight: 19, marginBottom: 2 },
  trackCard: { backgroundColor: '#fff', borderRadius: 12, borderWidth: 1, borderColor: '#cfdded', overflow: 'hidden' },
  trackCardExcluded: { opacity: 0.5 },
  trackHead: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14 },
  trackName: { fontWeight: '600', color: '#0b2845', fontSize: 15 },
  trackMeta: { color: '#60748a', fontSize: 13, marginTop: 3 },
  trackBody: { paddingHorizontal: 14, paddingBottom: 14, gap: 12, borderTopWidth: 1, borderTopColor: '#eef2f7' },
  preview: { width: '100%', height: 190, borderRadius: 10, backgroundColor: '#0b1a2b', marginTop: 12 },
  finalCard: { backgroundColor: '#fff', borderRadius: 12, borderWidth: 1, borderColor: '#cfdded', padding: 14, gap: 12 },
  finalPreview: { width: '100%', height: 260, borderRadius: 10, backgroundColor: '#0b1a2b' },
  bulkRow: { flexDirection: 'row', gap: 18 },
  link: { color: '#185FA5', fontWeight: '600', fontSize: 13 },
  cutRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 8, borderTopWidth: 1, borderTopColor: '#eef2f7' },
  cutReason: { color: '#0b2845', fontWeight: '600', fontSize: 14 },
  cutMeta: { color: '#60748a', fontSize: 12, marginTop: 2 },
  cutTranscript: { color: '#4c6077', fontSize: 13, fontStyle: 'italic', marginTop: 4 },
  saveButton: { backgroundColor: '#185FA5', borderRadius: 10, paddingVertical: 12, alignItems: 'center' },
  saveButtonText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  renderButton: { backgroundColor: '#031b33', borderRadius: 10, paddingVertical: 15, alignItems: 'center', marginTop: 8 },
  excludeRow: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingTop: 4 },
  ghostButton: { paddingHorizontal: 18, paddingVertical: 10 },
  ghostButtonText: { color: '#185FA5', fontWeight: '600' },
  disabled: { opacity: 0.5 },
});
