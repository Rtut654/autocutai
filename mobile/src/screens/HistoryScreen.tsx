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
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { Project, deleteProject, listProjects } from '../api/projects';

type Props = {
  token: string;
  onOpenProject: (projectId: string) => void;
};

const STATUS_LABEL: Record<string, string> = {
  draft: 'Ready to edit',
  processing: 'Processing',
  completed: 'Ready',
  error: 'Failed',
};

const STATUS_COLOR: Record<string, string> = {
  draft: '#60748a',
  processing: '#96601a',
  completed: '#1b6b50',
  error: '#a83229',
};

function totalSeconds(project: Project): number {
  return (project.tracks || []).reduce((sum, track) => sum + (Number(track?.duration) || 0), 0);
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

function formatDate(value?: string | null): string {
  if (!value) return 'Unknown date';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown date';
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function HistoryScreen({ token, onOpenProject }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setProjects(await listProjects(token));
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

  const remove = (project: Project) => {
    Alert.alert('Delete project', `Delete “${project.name}” and its clips?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            await deleteProject(token, project.id);
            setProjects((current) => current.filter((item) => item.id !== project.id));
          } catch (err: any) {
            Alert.alert('Could not delete', err?.message || 'Unknown error');
          }
        },
      },
    ]);
  };

  const renderProject = ({ item }: { item: Project }) => {
    const status = String(item.status || 'draft');
    const clipCount = (item.tracks || []).length;

    return (
      <Pressable style={styles.card} onPress={() => onOpenProject(item.id)}>
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
            <Text style={styles.metaText}>
              {clipCount} {clipCount === 1 ? 'clip' : 'clips'}
            </Text>
          </View>
          <View style={styles.metaItem}>
            <MaterialCommunityIcons name="clock-outline" size={15} color="#60748a" />
            <Text style={styles.metaText}>{formatDuration(totalSeconds(item))}</Text>
          </View>
          <View style={styles.metaItem}>
            <MaterialCommunityIcons name="calendar-blank-outline" size={15} color="#60748a" />
            <Text style={styles.metaText}>{formatDate(item.updated_at || item.created_at)}</Text>
          </View>
        </View>

        <View style={styles.actions}>
          <Text style={styles.openHint}>Tap to open the editor</Text>
          <Pressable hitSlop={10} onPress={() => remove(item)}>
            <MaterialCommunityIcons name="trash-can-outline" size={19} color="#a83229" />
          </Pressable>
        </View>
      </Pressable>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Projects</Text>
      <Text style={styles.subtitle}>Every edit you have made.</Text>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#031b33" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retry} onPress={load}>
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={projects}
          keyExtractor={(item) => item.id}
          renderItem={renderProject}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
            />
          }
          ListEmptyComponent={
            <View style={styles.center}>
              <MaterialCommunityIcons name="movie-open-outline" size={34} color="#9fb2c6" />
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
  retry: { backgroundColor: '#031b33', borderRadius: 10, paddingVertical: 12, paddingHorizontal: 22 },
  retryText: { color: '#fff', fontWeight: '700' },
  card: { backgroundColor: '#fff', borderRadius: 14, borderWidth: 1, borderColor: '#cfdded', padding: 14, gap: 10 },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  cardTitle: { flex: 1, fontWeight: '700', fontSize: 16, color: '#0b2845' },
  badge: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  badgeText: { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 14 },
  metaItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  metaText: { color: '#60748a', fontSize: 13 },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderTopWidth: 1,
    borderTopColor: '#eef2f7',
    paddingTop: 10,
  },
  openHint: { color: '#185FA5', fontWeight: '600', fontSize: 13 },
});
