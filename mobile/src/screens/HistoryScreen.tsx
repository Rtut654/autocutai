import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { api, downloadOutput, ProjectSummary } from '../api/client';

type Props = {
  token: string;
};

const STATUS_LABEL: Record<string, string> = {
  draft: 'Draft',
  processing: 'Processing',
  completed: 'Ready',
  error: 'Failed',
};

const STATUS_COLOR: Record<string, string> = {
  draft: '#60748a',
  processing: '#b07818',
  completed: '#1b6b50',
  error: '#a83229',
};

function totalDurationSeconds(project: ProjectSummary): number {
  const tracks = Array.isArray(project.tracks) ? project.tracks : [];
  return tracks.reduce((sum, track) => sum + (Number(track?.duration) || 0), 0);
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

function formatDate(value?: string | null): string {
  if (!value) return 'Unknown date';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown date';
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function HistoryScreen({ token }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await api.listProjects(token, 100, 0);
      setProjects(Array.isArray(data?.projects) ? data.projects : []);
    } catch (err: any) {
      setError(err?.message || 'Could not load your projects.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const exportProject = async (project: ProjectSummary) => {
    if (project.status !== 'completed') {
      Alert.alert('Not ready yet', 'This project has to finish processing before you can export it.');
      return;
    }
    setBusyId(project.id);
    try {
      const target = `${FileSystem.cacheDirectory}${project.id}.mp4`;
      const result = await FileSystem.downloadAsync(downloadOutput(project.id), target, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (result.status !== 200) {
        throw new Error(`Download failed with status ${result.status}`);
      }
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(result.uri);
      } else {
        Alert.alert('Saved', `Video saved to ${result.uri}`);
      }
    } catch (err: any) {
      Alert.alert('Export failed', err?.message || 'Could not export this video.');
    } finally {
      setBusyId(null);
    }
  };

  const renderProject = ({ item }: { item: ProjectSummary }) => {
    const status = String(item.status || 'draft');
    const clipCount = Array.isArray(item.tracks) ? item.tracks.length : 0;

    return (
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <Text style={styles.cardTitle} numberOfLines={1}>
            {item.name || 'Untitled project'}
          </Text>
          <View style={[styles.badge, { backgroundColor: `${STATUS_COLOR[status] || '#60748a'}1a` }]}>
            <Text style={[styles.badgeText, { color: STATUS_COLOR[status] || '#60748a' }]}>
              {STATUS_LABEL[status] || status}
            </Text>
          </View>
        </View>

        <View style={styles.metaRow}>
          <View style={styles.metaItem}>
            <MaterialCommunityIcons name="movie-outline" size={15} color="#60748a" />
            <Text style={styles.metaText}>{clipCount} {clipCount === 1 ? 'clip' : 'clips'}</Text>
          </View>
          <View style={styles.metaItem}>
            <MaterialCommunityIcons name="clock-outline" size={15} color="#60748a" />
            <Text style={styles.metaText}>{formatDuration(totalDurationSeconds(item))}</Text>
          </View>
          <View style={styles.metaItem}>
            <MaterialCommunityIcons name="calendar-blank-outline" size={15} color="#60748a" />
            <Text style={styles.metaText}>{formatDate(item.updated_at || item.created_at)}</Text>
          </View>
        </View>

        <Pressable
          style={[styles.action, (busyId === item.id || status !== 'completed') && styles.actionDisabled]}
          onPress={() => exportProject(item)}
          disabled={busyId === item.id || status !== 'completed'}
        >
          {busyId === item.id ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <Text style={styles.actionText}>Export video</Text>
          )}
        </Pressable>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>History</Text>
      <Text style={styles.subtitle}>Every edit you have made.</Text>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#031b33" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.action} onPress={load}>
            <Text style={styles.actionText}>Try again</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={projects}
          keyExtractor={(item) => item.id}
          renderItem={renderProject}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            <View style={styles.center}>
              <Text style={styles.emptyText}>No projects yet. Upload some clips to get started.</Text>
            </View>
          }
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, gap: 4 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { color: '#4c6077', marginBottom: 12 },
  list: { gap: 12, paddingBottom: 24 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, paddingTop: 60 },
  emptyText: { color: '#4c6077', textAlign: 'center' },
  errorText: { color: '#a83229', textAlign: 'center' },
  card: {
    backgroundColor: '#fff',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#cfdded',
    padding: 14,
    gap: 12,
  },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  cardTitle: { flex: 1, fontWeight: '700', fontSize: 16, color: '#0b2845' },
  badge: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  badgeText: { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 14 },
  metaItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  metaText: { color: '#60748a', fontSize: 13 },
  action: {
    backgroundColor: '#031b33',
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 18,
    alignItems: 'center',
  },
  actionDisabled: { opacity: 0.45 },
  actionText: { color: '#fff', fontWeight: '700' },
});
