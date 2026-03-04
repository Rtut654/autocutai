import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

type Props = {
  summary: string;
  projectId: string | null;
  onSaveFinalVideo: () => Promise<void>;
};

export default function TimelineScreen({ summary, projectId, onSaveFinalVideo }: Props) {
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Timeline</Text>
      <Text style={styles.subtitle}>Review output and export your final cut.</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>AI Timeline Summary</Text>
        <Text style={styles.cardText}>{summary || 'Run auto pre-edit to generate timeline analysis.'}</Text>

        <Pressable style={[styles.button, !projectId && styles.disabled]} disabled={!projectId} onPress={onSaveFinalVideo}>
          <Text style={styles.buttonText}>Save Final Video</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, gap: 8 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { color: '#4c6077', marginBottom: 8 },
  card: { backgroundColor: '#fff', borderRadius: 14, borderWidth: 1, borderColor: '#cfdded', padding: 14, gap: 10 },
  cardTitle: { fontWeight: '700', fontSize: 16, color: '#0b2845' },
  cardText: { color: '#4c6077', lineHeight: 20 },
  button: { marginTop: 8, backgroundColor: '#031b33', borderRadius: 10, padding: 12, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
  disabled: { opacity: 0.5 },
});
