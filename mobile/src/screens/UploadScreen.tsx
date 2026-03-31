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
import { uploadClips } from "../api/pipeline";

type Props = {
  onJobStarted: (jobId: string) => void;
};

export default function UploadScreen({ onJobStarted }: Props) {
  const [clips, setClips] = useState<
    { uri: string; name: string; mimeType?: string }[]
  >([]);
  const [uploading, setUploading] = useState(false);

  const pickClips = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: "video/*",
        multiple: true,
        copyToCacheDirectory: true,
      });
      if (!result.canceled) {
        setClips(
          result.assets.map((a) => ({
            uri: a.uri,
            name: a.name,
            mimeType: a.mimeType,
          }))
        );
      }
    } catch (err: any) {
      Alert.alert("Selection failed", err?.message || "Unable to pick videos.");
    }
  };

  const startProcessing = async () => {
    if (clips.length === 0) return;
    setUploading(true);
    try {
      const jobId = await uploadClips(clips);
      onJobStarted(jobId);
    } catch (err: any) {
      Alert.alert("Upload failed", err?.message || "Unknown error");
    } finally {
      setUploading(false);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={["top"]}>
      <Text style={styles.title}>Upload</Text>
      <Text style={styles.subtitle}>
        Select your travel clips and let AI edit them.
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
              <ActivityIndicator size="large" color="#031b33" />
            ) : (
              <Pressable style={styles.processButton} onPress={startProcessing}>
                <Text style={styles.buttonText}>
                  Process {clips.length} Clips
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
  item: {
    paddingVertical: 6,
    color: "#4c6077",
  },
});
