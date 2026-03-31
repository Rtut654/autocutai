import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { Linking } from 'react-native';
import MapView, { Polyline, UrlTile } from 'react-native-maps';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { api, downloadOutput } from '../api/client';

const HISTORY_TILE_URL = 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png';

type HistoryItem = {
  id: string;
  route_title: string;
  shape_key: string;
  actual_distance_km: number;
  duration_seconds: number;
  avg_speed_kmh: number;
  elevation_gain_m: number;
  elevation_loss_m: number;
  completed_at: string | null;
  created_at: string | null;
  is_public: boolean;
  route_geojson: string;
  status: string;
};

function hashSeed(value: string) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

function buildLineGeojson(projectId: string, points = 8) {
  const seed = hashSeed(projectId);
  const baseLat = 37.72 + ((seed % 100) / 1000);
  const baseLon = -122.48 + (((seed >> 8) % 100) / 1000);
  const coordinates: number[][] = [];
  for (let i = 0; i < points; i += 1) {
    const a = (i / Math.max(points - 1, 1)) * Math.PI * 2;
    const radiusLat = 0.004 + ((seed % 17) / 10000);
    const radiusLon = 0.006 + (((seed >> 3) % 13) / 10000);
    coordinates.push([
      baseLon + Math.cos(a + i * 0.27) * radiusLon,
      baseLat + Math.sin(a + i * 0.19) * radiusLat,
    ]);
  }

  return JSON.stringify({
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates,
        },
        properties: {},
      },
    ],
  });
}

function parseGeoJsonLine(routeGeojson: string) {
  try {
    const data = JSON.parse(routeGeojson);
    const coords = data.features?.[0]?.geometry?.coordinates || [];
    return coords
      .map((c: any) => ({ longitude: Number(c[0]), latitude: Number(c[1]) }))
      .filter((point: any) => Number.isFinite(point.longitude) && Number.isFinite(point.latitude));
  } catch {
    return [];
  }
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '-';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function formatRunDate(value: string | null) {
  if (!value) return 'Unknown date';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown date';
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: '2-digit' });
}

function formatPace(value: number) {
  const pace = Number(value);
  if (!Number.isFinite(pace) || pace <= 0) return '-';
  const whole = Math.floor(pace);
  const sec = Math.round((pace - whole) * 60);
  const safeSec = sec >= 60 ? 59 : sec;
  return `${String(whole).padStart(2, '0')}:${String(safeSec).padStart(2, '0')} min/km`;
}

function estimateAnimationOutputSeconds(item: HistoryItem | null, speedUp: number) {
  const baseDuration = Number(item?.duration_seconds) > 0
    ? Number(item?.duration_seconds)
    : Math.max(120, Math.round(Math.max(Number(item?.actual_distance_km) || 1, 1) * 240));
  const normalizedSpeed = Math.max(0.25, Math.min(Number(speedUp) || 1, 100));
  return Math.max(6, Math.min(60, baseDuration / normalizedSpeed));
}

function regionFromPolyline(polyline: Array<{ latitude: number; longitude: number }>, options: { paddingFactor?: number; minDelta?: number } = {}) {
  const paddingFactor = Number(options.paddingFactor) > 0 ? Number(options.paddingFactor) : 1.4;
  const minDelta = Number(options.minDelta) > 0 ? Number(options.minDelta) : 0.01;
  if (!polyline.length) {
    return {
      latitude: 0,
      longitude: 0,
      latitudeDelta: 0.06,
      longitudeDelta: 0.06,
    };
  }
  let minLat = polyline[0].latitude;
  let maxLat = polyline[0].latitude;
  let minLon = polyline[0].longitude;
  let maxLon = polyline[0].longitude;
  for (const point of polyline) {
    minLat = Math.min(minLat, point.latitude);
    maxLat = Math.max(maxLat, point.latitude);
    minLon = Math.min(minLon, point.longitude);
    maxLon = Math.max(maxLon, point.longitude);
  }
  const centerLat = (minLat + maxLat) / 2;
  const centerLon = (minLon + maxLon) / 2;
  return {
    latitude: centerLat,
    longitude: centerLon,
    latitudeDelta: Math.max(minDelta, (maxLat - minLat) * paddingFactor),
    longitudeDelta: Math.max(minDelta, (maxLon - minLon) * paddingFactor),
  };
}

function toHistoryItem(project: any): HistoryItem {
  const tracks = Array.isArray(project?.tracks) ? project.tracks : [];
  const durationSeconds = tracks.reduce((sum: number, t: any) => sum + (Number(t?.duration) || 0), 0);
  const distanceKm = Math.max(0.4, Number((durationSeconds / 300).toFixed(2)));
  const avgSpeed = durationSeconds > 0 ? distanceKm / (durationSeconds / 3600) : 0;
  const insertionCount = Number(project?.pipeline?.insertion_suggestions?.length || 0);
  const gain = Math.round(distanceKm * 25 + insertionCount * 7);
  const loss = Math.round(distanceKm * 21 + insertionCount * 6);

  return {
    id: String(project.id),
    shape_key: 'video-edit',
    route_title: project?.name || 'Untitled project',
    actual_distance_km: distanceKm,
    duration_seconds: Math.round(durationSeconds),
    avg_speed_kmh: Number(avgSpeed.toFixed(1)),
    elevation_gain_m: gain,
    elevation_loss_m: loss,
    completed_at: project?.status === 'completed' ? (project?.updated_at || null) : null,
    created_at: project?.created_at || null,
    is_public: false,
    route_geojson: buildLineGeojson(String(project.id), Math.max(6, tracks.length + 5)),
    status: String(project?.status || 'pending'),
  };
}

export default function HistoryScreen() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  const [speedUp, setSpeedUp] = useState(10);
  const [busyAction, setBusyAction] = useState(false);

  const load = async () => {
    setRefreshing(true);
    try {
      const data = await api.listProjects(100, 0);
      const mapped = (Array.isArray(data?.projects) ? data.projects : []).map(toHistoryItem);
      setItems(mapped);
      if (selected) {
        const fresh = mapped.find((x) => x.id === selected.id) || null;
        setSelected(fresh);
      }
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load().catch(() => {});
  }, []);

  const selectedPolyline = useMemo(
    () => (selected ? parseGeoJsonLine(selected.route_geojson) : []),
    [selected],
  );
  const selectedRegion = useMemo(
    () => regionFromPolyline(selectedPolyline, { paddingFactor: 1.25, minDelta: 0.005 }),
    [selectedPolyline],
  );
  const animationSeconds = useMemo(
    () => estimateAnimationOutputSeconds(selected, speedUp),
    [selected, speedUp],
  );

  const toggleVisibility = async () => {
    if (!selected) return;
    const next = { ...selected, is_public: !selected.is_public };
    setSelected(next);
    setItems((prev) => prev.map((item) => (item.id === next.id ? next : item)));
  };

  const publish = async () => {
    if (!selected) return;
    Alert.alert('Queued', `Project “${selected.route_title}” marked for publishing.`);
  };

  const exportFinalVideo = async () => {
    if (!selected) return;
    if (selected.status !== 'completed') {
      Alert.alert('Not ready', 'Process the project first to export a final video.');
      return;
    }
    try {
      setBusyAction(true);
      const outputDir = FileSystem.cacheDirectory || FileSystem.documentDirectory;
      if (!outputDir) {
        throw new Error('Local storage is unavailable');
      }
      const fileUri = `${outputDir}autocutai-final-${selected.id}.mp4`;
      await FileSystem.downloadAsync(downloadOutput(selected.id), fileUri);

      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(fileUri, {
          mimeType: 'video/mp4',
          dialogTitle: 'Export final video',
        });
      } else {
        Alert.alert('Unavailable', 'Sharing is unavailable on this device.');
      }
    } catch (error: any) {
      Alert.alert('Export failed', String(error?.message || error));
    } finally {
      setBusyAction(false);
    }
  };

  const openUploadDestination = async () => {
    try {
      await Linking.openURL('https://www.youtube.com/upload');
    } catch (error: any) {
      Alert.alert('Open failed', String(error?.message || error));
    }
  };

  const generateAnimation = async () => {
    if (!selected) return;
    Alert.alert('Coming soon', `GIF preview at x${speedUp} speed will be available in the next update.`);
  };

  const renderHistoryCard = ({ item }: { item: HistoryItem }) => {
    const cardPolyline = parseGeoJsonLine(item.route_geojson);
    const region = regionFromPolyline(cardPolyline, { paddingFactor: 1.15, minDelta: 0.003 });
    const runDate = formatRunDate(item.completed_at || item.created_at);

    return (
      <Pressable style={styles.card} onPress={() => setSelected(item)}>
        <View style={styles.previewWrap}>
          <MapView
            style={styles.cardMap}
            mapType="none"
            initialRegion={region}
            scrollEnabled={false}
            zoomEnabled={false}
            rotateEnabled={false}
            pitchEnabled={false}
            toolbarEnabled={false}
          >
            <UrlTile
              urlTemplate={HISTORY_TILE_URL}
              maximumZ={20}
              flipY={false}
            />
            {cardPolyline.length > 1 ? (
              <Polyline coordinates={cardPolyline} strokeColor="#145b9e" strokeWidth={4} />
            ) : null}
          </MapView>
          <View style={styles.dateBadge}>
            <Text style={styles.dateBadgeText}>{runDate}</Text>
          </View>
        </View>

        <Text style={styles.head}>
          {(item.route_title || item.shape_key)} • {item.actual_distance_km.toFixed(2)} km
        </Text>
        <Text style={styles.meta}>Duration: {formatDuration(item.duration_seconds)}</Text>
        <Text style={styles.meta}>
          Elevation: +{Math.round(item.elevation_gain_m || 0)} m / -{Math.round(item.elevation_loss_m || 0)} m
        </Text>
      </Pressable>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>History</Text>
      <Text style={styles.subtitle}>Export videos, publish projects, and manage processed edits.</Text>
      <FlatList
        data={items}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}
        renderItem={renderHistoryCard}
        ListEmptyComponent={<Text style={styles.emptyText}>No projects yet. Create and process your first edit.</Text>}
      />

      <Modal visible={!!selected} transparent animationType="slide" onRequestClose={() => setSelected(null)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>{selected?.route_title || selected?.shape_key} run</Text>
              <Pressable onPress={() => setSelected(null)}>
                <Text style={styles.close}>Close</Text>
              </Pressable>
            </View>
            {selected ? (
              <>
                <MapView style={styles.map} mapType="none" initialRegion={selectedRegion}>
                  <UrlTile
                    urlTemplate={HISTORY_TILE_URL}
                    maximumZ={20}
                    flipY={false}
                  />
                  {selectedPolyline.length > 1 ? (
                    <Polyline coordinates={selectedPolyline} strokeColor="#0f5fa3" strokeWidth={4} />
                  ) : null}
                </MapView>
                <View style={styles.statsTopRow}>
                  <View style={styles.statTopCard}>
                    <Text style={styles.statTopValue}>{selected.actual_distance_km.toFixed(2)}</Text>
                    <Text style={styles.statTopLabel}>Distance (km)</Text>
                  </View>
                  <View style={styles.statTopCard}>
                    <Text style={styles.statTopValue}>{formatDuration(selected.duration_seconds)}</Text>
                    <Text style={styles.statTopLabel}>Duration</Text>
                  </View>
                  <View style={styles.statTopCard}>
                    <Text style={styles.statTopValue}>
                      {Number.isFinite(selected.avg_speed_kmh) ? selected.avg_speed_kmh.toFixed(1) : '-'}
                    </Text>
                    <Text style={styles.statTopLabel}>Avg km/h</Text>
                  </View>
                </View>
                <View style={styles.metricRows}>
                  <View style={[styles.metricRow, styles.metricRowFirst]}>
                    <View style={styles.metricLeft}>
                      <MaterialCommunityIcons name="calendar-month-outline" size={18} color="#2a4a6f" />
                      <Text style={styles.metricLabel}>Date</Text>
                    </View>
                    <Text style={styles.metricValue}>{formatRunDate(selected.completed_at || selected.created_at)}</Text>
                  </View>
                  <View style={styles.metricRow}>
                    <View style={styles.metricLeft}>
                      <MaterialCommunityIcons name="speedometer" size={18} color="#2a4a6f" />
                      <Text style={styles.metricLabel}>Average Pace</Text>
                    </View>
                    <Text style={styles.metricValue}>
                      {formatPace(
                        (Number(selected.duration_seconds) > 0 && Number(selected.actual_distance_km) > 0)
                          ? (selected.duration_seconds / 60) / selected.actual_distance_km
                          : 0,
                      )}
                    </Text>
                  </View>
                  <View style={styles.metricRow}>
                    <View style={styles.metricLeft}>
                      <MaterialCommunityIcons name="terrain" size={18} color="#2a4a6f" />
                      <Text style={styles.metricLabel}>Elevation</Text>
                    </View>
                    <Text style={styles.metricValue}>
                      +{Math.round(selected.elevation_gain_m || 0)} / -{Math.round(selected.elevation_loss_m || 0)} m
                    </Text>
                  </View>
                </View>

                <View style={styles.row}>
                  <Pressable style={styles.actionGhost} onPress={toggleVisibility}>
                    <Text style={styles.actionGhostText}>{selected.is_public ? 'Make Private' : 'Make Public'}</Text>
                  </Pressable>
                  <Pressable style={styles.action} onPress={publish}>
                    <Text style={styles.actionText}>Publish</Text>
                  </Pressable>
                </View>

                <View style={styles.row}>
                  <Pressable style={[styles.action, busyAction && styles.actionDisabled]} onPress={exportFinalVideo} disabled={busyAction}>
                    <Text style={styles.actionText}>Export Final Video</Text>
                  </Pressable>
                  <Pressable style={[styles.actionGhost, busyAction && styles.actionDisabled]} onPress={openUploadDestination} disabled={busyAction}>
                    <Text style={styles.actionGhostText}>Open Upload</Text>
                  </Pressable>
                </View>

                <View style={styles.speedPanel}>
                  <Text style={styles.speedTitle}>Animation speed-up</Text>
                  <View style={styles.speedControls}>
                    <Pressable
                      style={styles.speedButton}
                      onPress={() => setSpeedUp((prev) => Math.max(1, prev - 1))}
                      disabled={busyAction}
                    >
                      <Text style={styles.speedButtonText}>-</Text>
                    </Pressable>
                    <Text style={styles.speedValue}>x{speedUp}</Text>
                    <Pressable
                      style={styles.speedButton}
                      onPress={() => setSpeedUp((prev) => Math.min(100, prev + 1))}
                      disabled={busyAction}
                    >
                      <Text style={styles.speedButtonText}>+</Text>
                    </Pressable>
                  </View>
                  <Text style={styles.speedMeta}>Output length: {animationSeconds.toFixed(1)}s (max 60s)</Text>
                </View>

                <Pressable style={[styles.action, busyAction && styles.actionDisabled]} onPress={generateAnimation} disabled={busyAction}>
                  {busyAction ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={styles.actionText}>Generate animation GIF</Text>
                  )}
                </Pressable>
              </>
            ) : null}
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f3f7fd', paddingHorizontal: 16 },
  title: { fontSize: 30, fontWeight: '700', color: '#12253f' },
  subtitle: { marginTop: 4, marginBottom: 8, color: '#4f6883' },
  emptyText: { marginTop: 24, color: '#5c7190', textAlign: 'center', fontWeight: '600' },
  card: {
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 10,
    marginTop: 12,
    borderWidth: 1,
    borderColor: '#d2dbe7',
  },
  previewWrap: {
    height: 132,
    borderRadius: 12,
    overflow: 'hidden',
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#c6d3e3',
  },
  cardMap: { flex: 1 },
  dateBadge: {
    position: 'absolute',
    top: 8,
    right: 8,
    backgroundColor: 'rgba(14, 38, 71, 0.9)',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  dateBadgeText: { color: '#eef4ff', fontWeight: '700', fontSize: 11 },
  head: { fontSize: 16, fontWeight: '700', color: '#12233f' },
  meta: { marginTop: 4, color: '#4b6380' },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.35)', justifyContent: 'flex-end' },
  modalContent: {
    backgroundColor: '#f4f8fe',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    padding: 16,
    gap: 8,
  },
  modalHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  modalTitle: { fontSize: 18, fontWeight: '700', color: '#13263f' },
  close: { color: '#2b3f58', fontWeight: '600' },
  map: { height: 220, borderRadius: 12, overflow: 'hidden', marginBottom: 4 },
  statsTopRow: {
    flexDirection: 'row',
    gap: 6,
    marginTop: 2,
  },
  statTopCard: {
    flex: 1,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#c9d5e2',
    backgroundColor: '#ffffff',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 8,
    minHeight: 54,
  },
  statTopValue: { color: '#183a62', fontSize: 20, fontWeight: '800' },
  statTopLabel: { color: '#59728c', fontSize: 11, fontWeight: '600' },
  metricRows: {
    marginTop: 2,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#c9d5e2',
    backgroundColor: '#ffffff',
    overflow: 'hidden',
  },
  metricRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 9,
    borderTopWidth: 1,
    borderTopColor: '#ebf1f8',
  },
  metricRowFirst: {
    borderTopWidth: 0,
  },
  metricLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  metricLabel: { color: '#2a4668', fontWeight: '700', fontSize: 14 },
  metricValue: { color: '#446280', fontWeight: '600', fontSize: 13 },
  row: { flexDirection: 'row', gap: 8 },
  action: {
    flex: 1,
    backgroundColor: '#0f4f87',
    borderRadius: 10,
    paddingVertical: 9,
    paddingHorizontal: 10,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 40,
  },
  actionGhost: {
    flex: 1,
    backgroundColor: '#ffffff',
    borderRadius: 10,
    paddingVertical: 9,
    paddingHorizontal: 10,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: '#b8c6d8',
    minHeight: 40,
  },
  actionDisabled: { opacity: 0.5 },
  actionText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  actionGhostText: { color: '#1c395f', fontWeight: '700', fontSize: 14 },
  speedPanel: {
    borderWidth: 1,
    borderColor: '#cbd6e3',
    borderRadius: 10,
    backgroundColor: '#fff',
    padding: 8,
    gap: 4,
  },
  speedTitle: { color: '#16345e', fontWeight: '700' },
  speedControls: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 12 },
  speedButton: {
    width: 32,
    height: 32,
    borderRadius: 9,
    borderWidth: 1,
    borderColor: '#b8c6d8',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  speedButtonText: { fontSize: 18, fontWeight: '700', color: '#14325a' },
  speedValue: { minWidth: 62, textAlign: 'center', fontSize: 18, fontWeight: '700', color: '#16345e' },
  speedMeta: { color: '#4e6580', fontSize: 12 },
});
