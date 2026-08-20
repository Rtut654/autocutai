import React, { useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  ActivityIndicator,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import {
  analyzeHybridProjectOnBackend,
  validateClipSelection,
  LocalHybridClip,
  MAX_CLIPS_PER_PROJECT,
  MAX_TOTAL_DURATION_SECONDS,
} from "../api/hybrid";

type Props = {
  token: string;
  onProjectReady: (projectId: string) => void;
};

export default function UploadScreen({ token, onProjectReady }: Props) {
  const [clips, setClips] = useState<LocalHybridClip[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);

  const pickClips = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: "video/*",
        multiple: true,
        copyToCacheDirectory: true,
      });
      if (!result.canceled) {
        const picked = result.assets.map((a) => ({
          uri: a.uri,
          name: a.name,
          mimeType: a.mimeType,
          lastModified: typeof a.lastModified === "number" ? a.lastModified : null,
        }));
        const problem = validateClipSelection(picked);
        if (problem) {
          Alert.alert("Too much footage", problem);
          return;
        }
        setClips(picked);
      }
    } catch (err: any) {
      Alert.alert("Selection failed", err?.message || "Unable to pick videos.");
    }
  };

  const startProcessing = async () => {
    if (clips.length === 0) return;
    setUploading(true);
    setProgress({ done: 0, total: clips.length });
    try {
      const result = await analyzeHybridProjectOnBackend({
        token,
        name: `Trip edit ${new Date().toLocaleDateString()}`,
        files: clips,
        settings: {
          smart_pause_cutter: true,
          generate_subtitles: true,
          insert_suggestions: true,
        },
        onProgress: (done, total) => setProgress({ done, total }),
      });
      onProjectReady(result.id);
    } catch (err: any) {
      Alert.alert("Analysis failed", err?.message || "Unknown error");
    } finally {
      setUploading(false);
      setProgress(null);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={["top"]}>
      <Text style={styles.title}>Upload</Text>
      <Text style={styles.subtitle}>
        Pick up to {MAX_CLIPS_PER_PROJECT} clips, {MAX_TOTAL_DURATION_SECONDS / 60} minutes total. Your footage stays on
        this device; only the audio is sent for transcription.
      </Text>

      <View style={styles.card}>
        <Pressable style={styles.button} onPress={pickClips}>
          <Text style={styles.buttonText}>Select Video Clips</Text>
        </Pressable>

        {clips.length > 0 && (
          <>
            <Text style={styles.count}>{clips.length} clips selected</Text>
            <FlatList
              data={clips}
              keyExtractor={(_, i) => String(i)}
              renderItem={({ item, index }) => (
                <Text style={styles.item}>
                  {index + 1}. {item.name}
                </Text>
              )}
              style={styles.list}
            />

            {uploading ? (
              <View style={styles.progressBlock}>
                <ActivityIndicator size="large" color="#031b33" />
                {progress ? (
                  <Text style={styles.count}>
                    Transcribing clip {Math.min(progress.done + 1, progress.total)} of {progress.total}
                  </Text>
                ) : null}
              </View>
            ) : (
              <Pressable style={styles.processButton} onPress={startProcessing}>
                <Text style={styles.buttonText}>
                  Analyze {clips.length} Clips
                </Text>
              </Pressable>
            )}
          </>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#f5f9ff",
    padding: 20,
    gap: 8,
  },
  title: {
    fontSize: 32,
    fontWeight: "700",
    color: "#031b33",
  },
  subtitle: {
    color: "#4c6077",
    marginBottom: 8,
  },
  card: {
    backgroundColor: "#fff",
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#cfdded",
    padding: 14,
    gap: 10,
    flex: 1,
  },
  button: {
    backgroundColor: "#031b33",
    borderRadius: 10,
    padding: 14,
    alignItems: "center",
  },
  processButton: {
    backgroundColor: "#185FA5",
    borderRadius: 10,
    padding: 14,
    alignItems: "center",
  },
  buttonText: {
    color: "#fff",
    fontWeight: "700",
    fontSize: 15,
  },
  count: {
    color: "#0b2845",
    fontWeight: "600",
    fontSize: 14,
  },
  list: {
    flex: 1,
  },
  progressBlock: {
    alignItems: "center",
    gap: 10,
    paddingVertical: 8,
  },
  item: {
    paddingVertical: 6,
    color: "#4c6077",
  },
});
