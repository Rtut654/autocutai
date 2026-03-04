import React from 'react';
import { View, Text, StyleSheet, Pressable, FlatList } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

type LocalFile = {
  uri: string;
  name: string;
};

type Props = {
  files: LocalFile[];
  canRun: boolean;
  onPickVideos: () => Promise<void>;
  onRunPipeline: () => Promise<void>;
  status: string;
};

export default function CreateScreen({ files, canRun, onPickVideos, onRunPipeline, status }: Props) {
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Create</Text>
      <Text style={styles.subtitle}>Upload raw clips and run auto pre-edit.</Text>

      <View style={styles.card}>
        <Pressable style={styles.button} onPress={onPickVideos}>
          <Text style={styles.buttonText}>Select Videos</Text>
        </Pressable>
        <Pressable style={[styles.button, !canRun && styles.disabled]} disabled={!canRun} onPress={onRunPipeline}>
          <Text style={styles.buttonText}>Run Auto Pre-Edit</Text>
        </Pressable>

        <Text style={styles.status}>Status: {status}</Text>

        <FlatList
          data={files}
          keyExtractor={(item) => item.uri}
          renderItem={({ item, index }) => <Text style={styles.item}>{`${index + 1}. ${item.name}`}</Text>}
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, gap: 8 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { color: '#4c6077', marginBottom: 8 },
  card: { backgroundColor: '#fff', borderRadius: 14, borderWidth: 1, borderColor: '#cfdded', padding: 14, gap: 10, flex: 1 },
  button: { backgroundColor: '#031b33', borderRadius: 10, padding: 12, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
  status: { color: '#0b2845', fontWeight: '600' },
  item: { paddingVertical: 6, color: '#4c6077' },
  disabled: { opacity: 0.5 },
});
