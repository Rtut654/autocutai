import React, { useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as FileSystem from "expo-file-system";
import * as Sharing from "expo-sharing";
import {
  ProcessingStatus,
  pollStatus,
  getOutputUrl,
} from "../api/pipeline";

const STAGE_LABELS: Record<string, string> = {
  ordering: "Sorting clips chronologically",
  demux: "Splitting audio & video streams",
  analysis: "Analysing scenes and speech",
  edit_plan: "Generating edit plan",
  rendering: "Rendering final video",
  done: "Complete",
};

const STAGE_ORDER = Object.keys(STAGE_LABELS);

type Props = {
  jobId: string;
  onBack: () => void;
};

export default function ProcessingScreen({ jobId, onBack }: Props) {
  const [status, setStatus] = useState<ProcessingStatus | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // Poll for status updates every 1.5s
    const poll = async () => {
      try {
        const s = await pollStatus(jobId);
        setStatus(s);
        if (s.stage === "done" && intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch {
        // ignore transient errors
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 1500);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobId]);

  const isDone = status?.stage === "done";
  const currentIdx = status ? STAGE_ORDER.indexOf(status.stage) : -1;

  const downloadAndShare = async () => {
    try {
      const url = getOutputUrl(jobId);
      const localUri = `${FileSystem.documentDirectory}travel_edit_${jobId.slice(0, 8)}.mp4`;
      const result = await FileSystem.downloadAsync(url, localUri);

      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(result.uri);
      } else {
        Alert.alert("Saved", `Video saved to ${result.uri}`);
      }
    } catch (err: any) {
      Alert.alert("Download failed", err?.message || "Unknown error");
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={["top"]}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.title}>Processing</Text>
        <Text style={styles.subtitle}>Your video is being created by AI.</Text>

        {/* Progress bar */}
        <View style={styles.progressBar}>
          <View
            style={[
              styles.progressFill,
              { width: `${(status?.progress ?? 0) * 100}%` },
            ]}
          />
        </View>

        {/* Stage list */}
        <View style={styles.stageList}>
          {STAGE_ORDER.map((key, i) => {
            const done = i < currentIdx;
            const active = i === currentIdx;
            return (
              <View key={key} style={styles.stageRow}>
                <Text
                  style={[
                    styles.stageIcon,
                    done && styles.stageDone,
                    active && styles.stageActive,
                  ]}
                >
                  {done ? "\u2713" : active ? "\u25B6" : "\u25CB"}
                </Text>
                <Text
                  style={[
                    styles.stageLabel,
                    done && styles.stageDone,
                    active && styles.stageActive,
                    !done && !active && styles.stageInactive,
                  ]}
                >
                  {STAGE_LABELS[key]}
                </Text>
              </View>
            );
          })}
        </View>

        {status?.message ? (
          <Text style={styles.message}>{status.message}</Text>
        ) : null}

        {/* Edit plan summary */}
        {status?.edit_plan && (
          <View style={styles.planCard}>
            <Text style={styles.planTitle}>Edit Plan</Text>
            <Text style={styles.planText}>
              Duration: {status.edit_plan.output_duration_estimate}
            </Text>
            <Text style={styles.planText}>
              Clips kept: {status.edit_plan.clips.length}
            </Text>
            <Text style={styles.planText}>
              Clips removed: {status.edit_plan.cuts_removed.length}
            </Text>
            <Text style={styles.planText}>
              Music cues: {status.edit_plan.music_cues.length}
            </Text>
          </View>
        )}

        {/* Download when done */}
        {isDone && (
          <View style={styles.doneSection}>
            <Text style={styles.doneTitle}>Your edited video is ready!</Text>
            <Pressable style={styles.downloadButton} onPress={downloadAndShare}>
              <Text style={styles.buttonText}>Download & Share</Text>
            </Pressable>
          </View>
        )}

        <Pressable style={styles.backButton} onPress={onBack}>
          <Text style={styles.backButtonText}>
            {isDone ? "Start New Edit" : "Back"}
          </Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#f5f9ff",
  },
  scroll: {
    padding: 20,
    gap: 12,
  },
  title: {
    fontSize: 32,
    fontWeight: "700",
    color: "#031b33",
  },
  subtitle: {
    color: "#4c6077",
  },
  progressBar: {
    backgroundColor: "#d2ddeb",
    borderRadius: 4,
    height: 6,
    overflow: "hidden",
  },
  progressFill: {
    backgroundColor: "#185FA5",
    borderRadius: 4,
    height: "100%",
  },
  stageList: {
    gap: 10,
    marginTop: 8,
  },
  stageRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  stageIcon: {
    fontSize: 16,
    width: 20,
    textAlign: "center",
    color: "#999",
  },
  stageLabel: {
    fontSize: 14,
    color: "#555",
  },
  stageDone: {
    color: "#1D9E75",
  },
  stageActive: {
    color: "#185FA5",
    fontWeight: "600",
  },
  stageInactive: {
    color: "#999",
  },
  message: {
    color: "#4c6077",
    fontSize: 13,
    marginTop: 4,
  },
  planCard: {
    backgroundColor: "#fff",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#cfdded",
    padding: 14,
    gap: 4,
    marginTop: 8,
  },
  planTitle: {
    fontWeight: "700",
    fontSize: 15,
    color: "#0b2845",
    marginBottom: 4,
  },
  planText: {
    color: "#4c6077",
    fontSize: 13,
  },
  doneSection: {
    backgroundColor: "#e8f5e9",
    borderRadius: 10,
    padding: 16,
    gap: 10,
    alignItems: "center",
    marginTop: 8,
  },
  doneTitle: {
    fontWeight: "700",
    fontSize: 16,
    color: "#1D9E75",
  },
  downloadButton: {
    backgroundColor: "#1D9E75",
    borderRadius: 10,
    padding: 14,
    alignItems: "center",
    width: "100%",
  },
  buttonText: {
    color: "#fff",
    fontWeight: "700",
    fontSize: 15,
  },
  backButton: {
    borderWidth: 1,
    borderColor: "#cfdded",
    borderRadius: 10,
    padding: 12,
    alignItems: "center",
    marginTop: 4,
  },
  backButtonText: {
    color: "#4c6077",
    fontWeight: "600",
  },
});
