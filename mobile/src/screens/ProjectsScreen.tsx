import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

type Props = {
  onGoCreate: () => void;
  projectId: string | null;
  status: string;
};

export default function ProjectsScreen({ onGoCreate, projectId, status }: Props) {
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Projects</Text>
      <Text style={styles.subtitle}>Your AI-assisted edits live here.</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Current Workspace</Text>
        <Text style={styles.cardText}>{projectId ? `Project ID: ${projectId}` : 'No project yet'}</Text>
        <Text style={styles.cardText}>Status: {status}</Text>
        <Pressable style={styles.button} onPress={onGoCreate}>
          <Text style={styles.buttonText}>Create New Edit</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, gap: 8 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { color: '#4c6077', marginBottom: 8 },
  card: { backgroundColor: '#fff', borderRadius: 14, borderWidth: 1, borderColor: '#cfdded', padding: 14, gap: 8 },
  cardTitle: { fontWeight: '700', fontSize: 16, color: '#0b2845' },
  cardText: { color: '#4c6077' },
  button: { marginTop: 4, backgroundColor: '#031b33', borderRadius: 10, padding: 12, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
});
