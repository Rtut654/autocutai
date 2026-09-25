import React from 'react';
import { Pressable, StyleSheet, Switch, Text, View } from 'react-native';

import { CaptionStyle, EditOptions, FillMode } from '../api/projects';

type Choice<T> = { value: T; label: string };

const CAPTION_CHOICES: Choice<CaptionStyle>[] = [
  { value: 'bold', label: 'Bold' },
  { value: 'boxed', label: 'Boxed' },
  { value: 'clean', label: 'Clean' },
  { value: 'none', label: 'Off' },
];

const CAPTION_HINTS: Record<CaptionStyle, string> = {
  bold: 'Big words, the spoken word lights up. Best for Reels and TikTok.',
  boxed: 'Words on a dark box, highlighted as they are spoken.',
  clean: 'Plain sentence captions.',
  none: 'No captions burned into the video.',
};

const FILL_CHOICES: Choice<FillMode>[] = [
  { value: 'blur', label: 'Blur' },
  { value: 'crop', label: 'Crop' },
  { value: 'black', label: 'Bars' },
];

const FILL_HINTS: Record<FillMode, string> = {
  blur: 'Landscape clips sit on a blurred copy of themselves. No black bars.',
  crop: 'Landscape clips are cropped to fill the frame.',
  black: 'Landscape clips keep black bars above and below.',
};

const BROLL_CHOICES: Choice<number>[] = [
  { value: 4, label: '4s' },
  { value: 6, label: '6s' },
  { value: 10, label: '10s' },
  { value: 0, label: 'Full' },
];

type Props = {
  value: EditOptions;
  onChange: (next: EditOptions) => void;
  disabled?: boolean;
};

function Segmented<T extends string | number>({
  choices,
  selected,
  onSelect,
  disabled,
  label,
}: {
  choices: Choice<T>[];
  selected: T;
  onSelect: (value: T) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <View style={styles.segmented} accessibilityRole="radiogroup" accessibilityLabel={label}>
      {choices.map((choice) => {
        const active = choice.value === selected;
        return (
          <Pressable
            key={String(choice.value)}
            style={[styles.segment, active && styles.segmentActive]}
            onPress={() => onSelect(choice.value)}
            disabled={disabled}
            accessibilityRole="radio"
            accessibilityState={{ selected: active, disabled }}
            accessibilityLabel={`${label}: ${choice.label}`}
          >
            <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{choice.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export default function EditOptionsPanel({ value, onChange, disabled }: Props) {
  const set = <K extends keyof EditOptions>(key: K, next: EditOptions[K]) => onChange({ ...value, [key]: next });

  return (
    <View style={[styles.panel, disabled && styles.disabled]}>
      <View style={styles.group}>
        <Text style={styles.label}>Captions</Text>
        <Segmented
          label="Captions"
          choices={CAPTION_CHOICES}
          selected={value.caption_style}
          onSelect={(next) => set('caption_style', next)}
          disabled={disabled}
        />
        <Text style={styles.hint}>{CAPTION_HINTS[value.caption_style]}</Text>
      </View>

      <View style={styles.group}>
        <Text style={styles.label}>Framing</Text>
        <Segmented
          label="Framing"
          choices={FILL_CHOICES}
          selected={value.fill_mode}
          onSelect={(next) => set('fill_mode', next)}
          disabled={disabled}
        />
        <Text style={styles.hint}>{FILL_HINTS[value.fill_mode]}</Text>
      </View>

      <View style={styles.group}>
        <Text style={styles.label}>Scenery shots</Text>
        <Segmented
          label="Longest scenery shot"
          choices={BROLL_CHOICES}
          selected={value.broll_max_seconds}
          onSelect={(next) => set('broll_max_seconds', next)}
          disabled={disabled}
        />
        <Text style={styles.hint}>
          {value.broll_max_seconds > 0
            ? `Clips without talking are trimmed to their best ${value.broll_max_seconds} seconds.`
            : 'Clips without talking are kept at full length.'}
        </Text>
      </View>

      <View style={styles.switchRow}>
        <View style={{ flex: 1 }}>
          <Text style={styles.label}>Clean up audio</Text>
          <Text style={styles.hint}>Reduces wind and background noise and evens out the volume.</Text>
        </View>
        <Switch
          value={value.audio_cleanup}
          onValueChange={(next) => set('audio_cleanup', next)}
          disabled={disabled}
          trackColor={{ true: '#185FA5', false: '#cfdded' }}
          accessibilityLabel="Clean up audio"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { gap: 16 },
  disabled: { opacity: 0.5 },
  group: { gap: 7 },
  label: { color: '#0b2845', fontWeight: '700', fontSize: 14 },
  hint: { color: '#60748a', fontSize: 12.5, lineHeight: 17 },
  segmented: {
    flexDirection: 'row',
    backgroundColor: '#eef3f9',
    borderRadius: 10,
    padding: 3,
    gap: 3,
  },
  segment: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: 'center' },
  segmentActive: {
    backgroundColor: '#fff',
    shadowColor: '#0b2845',
    shadowOpacity: 0.08,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 1 },
  },
  segmentText: { color: '#4c6077', fontWeight: '600', fontSize: 13 },
  segmentTextActive: { color: '#031b33' },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
});
