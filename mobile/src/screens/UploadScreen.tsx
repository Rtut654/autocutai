import React, { useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import {
  createProjectFromClips,
  validateSelection,
  totalDuration,
  totalBytes,
  DEFAULT_EDIT_OPTIONS,
  EditOptions,
  MAX_CLIPS_PER_PROJECT,
  MAX_TOTAL_DURATION_SECONDS,
  PickedClip,
} from '../api/projects';
import EditOptionsPanel from '../components/EditOptionsPanel';

type Props = {
  token: string;
  onProjectCreated: (projectId: string) => void;
};

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

function formatSize(bytes: number): string {
  if (bytes <= 0) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

function toPickedClip(asset: ImagePicker.ImagePickerAsset, index: number): PickedClip {
  return {
    uri: asset.uri,
    fileName: asset.fileName || `clip-${index + 1}.mov`,
    // expo-image-picker reports duration in milliseconds.
    durationSeconds: typeof asset.duration === 'number' ? asset.duration / 1000 : null,
    recordedAt: typeof asset.exif?.DateTimeOriginal === 'string' ? asset.exif.DateTimeOriginal : null,
    fileSizeBytes: typeof asset.fileSize === 'number' ? asset.fileSize : null,
    mimeType: asset.mimeType,
  };
}

export default function UploadScreen({ token, onProjectCreated }: Props) {
  const [clips, setClips] = useState<PickedClip[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<{ clip: number; of: number; fraction: number } | null>(null);
  const [options, setOptions] = useState<EditOptions>(DEFAULT_EDIT_OPTIONS);

  const pickClips = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert(
        'Photo access needed',
        'AutoCutAI needs access to your photo library to read the clips you want to edit. You can enable it in Settings.',
      );
      return;
    }

    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['videos'],
        allowsMultipleSelection: true,
        selectionLimit: MAX_CLIPS_PER_PROJECT,
        exif: true,
        quality: 1,
      });
      if (result.canceled) return;

      const picked = result.assets.map(toPickedClip);
      const problem = validateSelection(picked);
      if (problem) {
        Alert.alert('Too much footage', problem);
        return;
      }
      setClips(picked);
    } catch (error: any) {
      Alert.alert('Could not open your library', error?.message || 'Unknown error');
    }
  };

  const upload = async () => {
    if (clips.length === 0) return;
    setUploading(true);
    setProgress({ clip: 1, of: clips.length, fraction: 0 });
    try {
      const project = await createProjectFromClips(
        token,
        `Trip ${new Date().toLocaleDateString()}`,
        clips,
        ({ clipIndex, clipCount, fraction }) =>
          setProgress({ clip: clipIndex + 1, of: clipCount, fraction }),
        options,
      );
      setClips([]);
      onProjectCreated(project.id);
    } catch (error: any) {
      Alert.alert('Upload failed', error?.message || 'Unknown error');
    } finally {
      setUploading(false);
      setProgress(null);
    }
  };

  const duration = totalDuration(clips);
  const bytes = totalBytes(clips);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>New edit</Text>
      <Text style={styles.subtitle}>
        Pick up to {MAX_CLIPS_PER_PROJECT} clips, {MAX_TOTAL_DURATION_SECONDS / 60} minutes total. AutoCutAI
        transcribes them, removes filler words and long pauses, and gives you a first cut to adjust.
      </Text>

      <ScrollView style={styles.card} contentContainerStyle={styles.cardContent}>
        <Pressable style={styles.primaryButton} onPress={pickClips} disabled={uploading}>
          <MaterialCommunityIcons name="image-multiple-outline" size={18} color="#fff" />
          <Text style={styles.primaryButtonText}>
            {clips.length > 0 ? 'Choose different clips' : 'Choose clips from library'}
          </Text>
        </Pressable>

        {clips.length > 0 ? (
          <>
            <View style={styles.summaryRow}>
              <Text style={styles.summary}>
                {clips.length} {clips.length === 1 ? 'clip' : 'clips'} · {formatDuration(duration)}
              </Text>
              {bytes > 0 ? <Text style={styles.summaryMuted}>{formatSize(bytes)} to upload</Text> : null}
            </View>

            <View style={styles.list}>
              {clips.map((item, index) => (
                <View key={`${item.uri}-${index}`} style={styles.clipRow}>
                  <Text style={styles.clipIndex}>{index + 1}</Text>
                  <Text style={styles.clipName} numberOfLines={1}>
                    {item.fileName}
                  </Text>
                  <Text style={styles.clipMeta}>
                    {item.durationSeconds ? formatDuration(item.durationSeconds) : '—'}
                  </Text>
                </View>
              ))}
            </View>

            <View style={styles.divider} />
            <Text style={styles.sectionTitle}>Look of the edit</Text>
            <EditOptionsPanel value={options} onChange={setOptions} disabled={uploading} />
            <View style={styles.divider} />

            {uploading ? (
              <View style={styles.progressBlock}>
                <ActivityIndicator size="large" color="#031b33" />
                {progress ? (
                  <>
                    <Text style={styles.summary}>
                      Uploading clip {progress.clip} of {progress.of}
                    </Text>
                    <View style={styles.progressTrack}>
                      <View style={[styles.progressFill, { width: `${Math.round(progress.fraction * 100)}%` }]} />
                    </View>
                  </>
                ) : null}
              </View>
            ) : (
              <Pressable style={styles.processButton} onPress={upload}>
                <Text style={styles.primaryButtonText}>Upload and analyse</Text>
              </Pressable>
            )}
          </>
        ) : (
          <View style={styles.empty}>
            <MaterialCommunityIcons name="movie-open-outline" size={34} color="#9fb2c6" />
            <Text style={styles.emptyText}>No clips selected yet.</Text>
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, gap: 6 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { color: '#4c6077', marginBottom: 10, lineHeight: 20 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#cfdded',
    flex: 1,
  },
  cardContent: { padding: 14, gap: 12, flexGrow: 1 },
  divider: { height: 1, backgroundColor: '#eef2f7', marginVertical: 2 },
  sectionTitle: { color: '#031b33', fontWeight: '700', fontSize: 16 },
  primaryButton: {
    backgroundColor: '#031b33',
    borderRadius: 10,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  processButton: {
    backgroundColor: '#185FA5',
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  primaryButtonText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  summaryRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  summary: { color: '#0b2845', fontWeight: '600', fontSize: 14 },
  summaryMuted: { color: '#60748a', fontSize: 13 },
  list: { gap: 2 },
  clipRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 },
  clipIndex: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: '#eef5ff',
    color: '#185FA5',
    textAlign: 'center',
    lineHeight: 22,
    fontSize: 12,
    fontWeight: '700',
    overflow: 'hidden',
  },
  clipName: { flex: 1, color: '#0b2845' },
  clipMeta: { color: '#60748a', fontSize: 13 },
  progressBlock: { alignItems: 'center', gap: 10, paddingVertical: 8 },
  progressTrack: {
    height: 6,
    width: '100%',
    borderRadius: 3,
    backgroundColor: '#e3ecf7',
    overflow: 'hidden',
  },
  progressFill: { height: 6, backgroundColor: '#185FA5' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, paddingVertical: 60 },
  emptyText: { color: '#60748a' },
});
