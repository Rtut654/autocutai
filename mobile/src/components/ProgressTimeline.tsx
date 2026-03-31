import React from "react";
import { View, Text, StyleSheet } from "react-native";

const STAGES = [
  { key: "ordering", label: "Sorting clips", pct: 5 },
  { key: "demux", label: "Splitting streams", pct: 15 },
  { key: "analysis", label: "AI analysis", pct: 70 },
  { key: "edit_plan", label: "Edit plan", pct: 80 },
  { key: "rendering", label: "Rendering", pct: 90 },
  { key: "done", label: "Complete", pct: 100 },
];

type Props = {
  currentStage: string;
  progress: number;
};

export default function ProgressTimeline({ currentStage, progress }: Props) {
  const currentIdx = STAGES.findIndex((s) => s.key === currentStage);

  return (
    <View style={styles.container}>
      {/* Progress bar */}
      <View style={styles.bar}>
        <View style={[styles.fill, { width: `${progress * 100}%` }]} />
      </View>

      {/* Stage dots */}
      <View style={styles.stages}>
        {STAGES.map((stage, i) => {
          const done = i < currentIdx;
          const active = i === currentIdx;
          return (
            <View key={stage.key} style={styles.stageItem}>
              <View
                style={[
                  styles.dot,
                  done && styles.dotDone,
                  active && styles.dotActive,
                ]}
              />
              <Text
                style={[
                  styles.label,
                  done && styles.labelDone,
                  active && styles.labelActive,
                ]}
                numberOfLines={1}
              >
                {stage.label}
              </Text>
            </View>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 8,
  },
  bar: {
    backgroundColor: "#d2ddeb",
    borderRadius: 3,
    height: 4,
    overflow: "hidden",
  },
  fill: {
    backgroundColor: "#185FA5",
    height: "100%",
    borderRadius: 3,
  },
  stages: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  stageItem: {
    alignItems: "center",
    gap: 4,
    flex: 1,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "#ccc",
  },
  dotDone: {
    backgroundColor: "#1D9E75",
  },
  dotActive: {
    backgroundColor: "#185FA5",
  },
  label: {
    fontSize: 9,
    color: "#999",
    textAlign: "center",
  },
  labelDone: {
    color: "#1D9E75",
  },
  labelActive: {
    color: "#185FA5",
    fontWeight: "600",
  },
});
