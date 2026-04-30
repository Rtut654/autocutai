"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import { getStoredSession, setLastProjectId } from "../../../lib/session";
import type { BackgroundMusicSettings, BackgroundVideoPlanArtifact, ProjectDetail, ProjectTrack, SpeechFilterArtifact, SpeechFilterCut, TrackRenderVersion, TranscriptSegment, VisualPlanArtifact, VisualPlanPart, ZoomPreviewBeat } from "../../../lib/types";

type TranscriptStatus = "pending" | "processing" | "completed" | "error" | "not_applicable";
const MEDIA_BLOB_CACHE_NAME = "bestshotai-track-media-v1";
const mediaObjectUrlCache = new Map<string, string>();
const mediaObjectUrlPromiseCache = new Map<string, Promise<string>>();

function formatDate(value?: string | null): string {
  if (!value) return "Unknown date";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDayLabel(value?: string | null): string {
  if (!value) return "Unknown day";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function getDayGroupKey(value?: string | null): string {
  if (!value) return "unknown-day";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "unknown-day";
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function formatDuration(seconds: number): string {
  return formatTime(seconds || 0);
}

function formatOrientation(value?: ProjectTrack["orientation"]): string | null {
  if (!value || value === "unknown") return null;
  if (value === "horizontal") return "Horizontal";
  if (value === "vertical") return "Vertical";
  if (value === "square") return "Square";
  return null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundToMillis(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function easeOutCubic(value: number): number {
  const clamped = clamp(value, 0, 1);
  return 1 - Math.pow(1 - clamped, 3);
}

function easeInOutCubic(value: number): number {
  const clamped = clamp(value, 0, 1);
  return clamped < 0.5
    ? 4 * clamped * clamped * clamped
    : 1 - Math.pow(-2 * clamped + 2, 3) / 2;
}

function filenameNaturalKey(value: string): Array<string | number> {
  return value
    .toLowerCase()
    .split(/(\d+)/)
    .filter(Boolean)
    .map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

function formatSpeechFilterReason(value: string): string {
  return value
    .split("+")
    .flatMap((part) => part.split("_"))
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" + ");
}

const STOP_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "how",
  "i", "if", "in", "into", "is", "it", "its", "not", "of", "on", "or", "so", "that", "the",
  "their", "then", "there", "they", "this", "to", "up", "was", "we", "what", "when", "which",
  "with", "you", "your", "once", "usually", "again",
]);

function compactSentence(text: string, maxWords = 8): string {
  const words = text
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean);
  if (words.length <= maxWords) return words.join(" ");
  return `${words.slice(0, maxWords).join(" ")}...`;
}

function extractKeywords(text: string, maxKeywords = 3): string[] {
  const counts = new Map<string, number>();
  const ordered: string[] = [];
  text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 4 && !STOP_WORDS.has(token))
    .forEach((token) => {
      if (!counts.has(token)) ordered.push(token);
      counts.set(token, (counts.get(token) || 0) + 1);
    });
  return ordered
    .sort((left, right) => {
      const scoreDelta = (counts.get(right) || 0) - (counts.get(left) || 0);
      if (scoreDelta !== 0) return scoreDelta;
      return right.length - left.length;
    })
    .slice(0, maxKeywords)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1));
}

type VisualAnimationSpec = {
  theme: "problem" | "solution" | "steps" | "insight";
  kicker: string;
  headline: string;
  chips: string[];
  align: "left" | "right";
  layout: "stack" | "split" | "badge" | "footer";
  palette: "cool" | "mint" | "sunset" | "mono" | "neon" | "editorial" | "berry" | "amber";
  variant: "v1" | "v2" | "v3" | "v4" | "v5" | "v6";
  motionProfile: "calm" | "punchy" | "drift" | "elastic" | "crisp";
  motif:
    | "conversation_flow"
    | "question_answer"
    | "step_sequence"
    | "checklist_reveal"
    | "compare_problem_solution"
    | "object_spotlight"
    | "concept_network"
    | "process_arrow"
    | "timeline_sequence"
    | "decision_split"
    | "chart_pop"
    | "map_pointer"
    | "idea_burst";
};

type VisualSfxKind =
  | "ui_click_soft"
  | "ui_click_snap"
  | "whoosh_soft"
  | "whoosh_rise"
  | "pop_air";

type SoundSpec = {
  duration: number;
  tones?: Array<{ start: number; end: number; fromHz: number; toHz: number; gain: number; type?: "sine" | "square" | "triangle" }>;
  noises?: Array<{ start: number; end: number; fromHz: number; toHz: number; gain: number }>;
};

type BackgroundMusicPreset = BackgroundMusicSettings["preset"];
const COMMON_TRANSITION_SFX: VisualSfxKind = "whoosh_soft";

const DEFAULT_BACKGROUND_MUSIC: BackgroundMusicSettings = {
  enabled: true,
  preset: "upbeat_motion",
  volume: 0.82,
  ducking: 0.5,
};

const BACKGROUND_MUSIC_LABELS: Record<BackgroundMusicPreset, string> = {
  ambient_pulse: "Ambient",
  upbeat_motion: "Upbeat",
  warm_focus: "Warm",
};

function hashString(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function paletteFromPart(part: VisualPlanPart, seed: number): VisualAnimationSpec["palette"] {
  const palettes: VisualAnimationSpec["palette"][] = ["cool", "mint", "sunset", "mono", "neon", "editorial", "berry", "amber"];
  const raw = String(part.palette || "").toLowerCase() as VisualAnimationSpec["palette"];
  return palettes.includes(raw) ? raw : palettes[seed % palettes.length];
}

function variantFromPart(part: VisualPlanPart, seed: number): VisualAnimationSpec["variant"] {
  const variants: VisualAnimationSpec["variant"][] = ["v1", "v2", "v3", "v4", "v5", "v6"];
  const raw = String(part.variant || "").toLowerCase() as VisualAnimationSpec["variant"];
  return variants.includes(raw) ? raw : variants[seed % variants.length];
}

function motionProfileFromPart(part: VisualPlanPart, seed: number): VisualAnimationSpec["motionProfile"] {
  const profiles: VisualAnimationSpec["motionProfile"][] = ["calm", "punchy", "drift", "elastic", "crisp"];
  const raw = String(part.motion_profile || "").toLowerCase() as VisualAnimationSpec["motionProfile"];
  return profiles.includes(raw) ? raw : profiles[seed % profiles.length];
}

function paletteColors(palette: VisualAnimationSpec["palette"]) {
  switch (palette) {
    case "mint":
      return { primary: "rgba(131,255,218,0.92)", secondary: "rgba(151,193,255,0.82)", accent: "rgba(255,255,255,0.95)", soft: "rgba(131,255,218,0.18)", dark: "rgba(8,26,22,0.84)" };
    case "sunset":
      return { primary: "rgba(255,173,113,0.94)", secondary: "rgba(255,126,147,0.82)", accent: "rgba(255,250,240,0.95)", soft: "rgba(255,173,113,0.18)", dark: "rgba(43,22,14,0.84)" };
    case "mono":
      return { primary: "rgba(255,255,255,0.92)", secondary: "rgba(182,188,202,0.82)", accent: "rgba(255,255,255,0.96)", soft: "rgba(255,255,255,0.1)", dark: "rgba(20,24,32,0.84)" };
    case "neon":
      return { primary: "rgba(124,255,208,0.96)", secondary: "rgba(216,130,255,0.82)", accent: "rgba(255,255,255,0.98)", soft: "rgba(124,255,208,0.16)", dark: "rgba(18,12,30,0.84)" };
    case "editorial":
      return { primary: "rgba(224,207,176,0.94)", secondary: "rgba(151,193,255,0.78)", accent: "rgba(255,255,255,0.92)", soft: "rgba(224,207,176,0.16)", dark: "rgba(34,29,23,0.84)" };
    case "berry":
      return { primary: "rgba(255,126,147,0.94)", secondary: "rgba(174,140,255,0.8)", accent: "rgba(255,247,250,0.96)", soft: "rgba(255,126,147,0.18)", dark: "rgba(43,16,26,0.84)" };
    case "amber":
      return { primary: "rgba(255,205,92,0.96)", secondary: "rgba(255,173,113,0.82)", accent: "rgba(255,252,240,0.96)", soft: "rgba(255,205,92,0.18)", dark: "rgba(45,31,12,0.84)" };
    default:
      return { primary: "rgba(151,193,255,0.94)", secondary: "rgba(131,255,218,0.82)", accent: "rgba(255,255,255,0.95)", soft: "rgba(151,193,255,0.18)", dark: "rgba(15,23,42,0.84)" };
  }
}

function resolveAlign(part: VisualPlanPart, partIndex: number): "left" | "right" {
  const placement = String(part.placement || "").toLowerCase();
  if (placement.includes("right")) return "right";
  if (placement.includes("left")) return "left";
  return partIndex % 2 === 0 ? "left" : "right";
}

function inferMotif(part: VisualPlanPart): VisualAnimationSpec["motif"] {
  const text = (part.text || "").trim();
  const prompt = (part.prompt || "").trim();
  const source = `${prompt} ${text}`.toLowerCase();

  if (source.includes("question") || source.includes("answer")) return "question_answer";
  if (source.includes("conversation") || source.includes("listen") || source.includes("speaker")) return "conversation_flow";
  if (source.includes("checklist") || source.includes("list")) return "checklist_reveal";
  if (source.includes("first") || source.includes("step") || source.includes("then")) return "step_sequence";
  if (source.includes("timeline") || source.includes("before") || source.includes("after")) return "timeline_sequence";
  if (source.includes("decision") || source.includes("choice")) return "decision_split";
  if (source.includes("problem") && source.includes("solution")) return "compare_problem_solution";
  if (source.includes("chart") || source.includes("graph") || source.includes("metric")) return "chart_pop";
  if (source.includes("map") || source.includes("city") || source.includes("place") || source.includes("country")) return "map_pointer";
  if (source.includes("phone") || source.includes("laptop") || source.includes("camera") || source.includes("object")) return "object_spotlight";
  if (source.includes("process") || source.includes("flow") || source.includes("pipeline")) return "process_arrow";
  if (source.includes("network") || source.includes("connect")) return "concept_network";
  return "idea_burst";
}

function themeForMotif(motif: VisualAnimationSpec["motif"]): VisualAnimationSpec["theme"] {
  if (motif === "compare_problem_solution" || motif === "decision_split") return "problem";
  if (motif === "step_sequence" || motif === "checklist_reveal" || motif === "timeline_sequence" || motif === "process_arrow") {
    return "steps";
  }
  if (motif === "chart_pop" || motif === "object_spotlight" || motif === "map_pointer") return "solution";
  return "insight";
}

function kickerForMotif(motif: VisualAnimationSpec["motif"]): string {
  switch (motif) {
    case "conversation_flow": return "Conversation";
    case "question_answer": return "Q&A";
    case "step_sequence": return "Sequence";
    case "checklist_reveal": return "Checklist";
    case "compare_problem_solution": return "Problem -> fix";
    case "object_spotlight": return "Spotlight";
    case "concept_network": return "Concept map";
    case "process_arrow": return "Process";
    case "timeline_sequence": return "Timeline";
    case "decision_split": return "Decision";
    case "chart_pop": return "Metrics";
    case "map_pointer": return "Location";
    default: return "Visual emphasis";
  }
}

function layoutForMotif(motif: VisualAnimationSpec["motif"], variant: VisualAnimationSpec["variant"]): VisualAnimationSpec["layout"] {
  if (motif === "chart_pop" || motif === "map_pointer" || motif === "object_spotlight") return "badge";
  if (motif === "conversation_flow" || motif === "question_answer" || motif === "decision_split") return "split";
  if (motif === "timeline_sequence" || motif === "process_arrow" || motif === "checklist_reveal") return "footer";
  return variant === "v5" || variant === "v6" ? "split" : "stack";
}

function buildAnimationSpec(part: VisualPlanPart, partIndex: number): VisualAnimationSpec {
  const text = (part.text || "").trim();
  const prompt = (part.prompt || "").trim();
  const seed = hashString(`${part.animation_kind || ""}|${part.title || ""}|${part.text || ""}|${partIndex}`);
  const motif = (part.animation_kind as VisualAnimationSpec["motif"] | undefined) || inferMotif(part);
  const chips = (part.keywords?.filter(Boolean)?.slice(0, 3) || extractKeywords(text || prompt, 3));
  const headline = (String(part.title || "").trim() || chips.slice(0, 2).join(" / ") || compactSentence(text || prompt, 3)).replace(/\.\.\.$/, "");
  const palette = paletteFromPart(part, seed);
  const variant = variantFromPart(part, seed);
  const motionProfile = motionProfileFromPart(part, seed);

  return {
    theme: themeForMotif(motif),
    kicker: kickerForMotif(motif),
    headline,
    chips: chips.length ? chips.slice(0, 2) : ["Focus", "Point"],
    align: resolveAlign(part, partIndex),
    layout: layoutForMotif(motif, variant),
    palette,
    variant,
    motionProfile,
    motif,
  };
}

function inferVisualSfx(part: VisualPlanPart, spec: VisualAnimationSpec): VisualSfxKind {
  void part;
  void spec;
  return COMMON_TRANSITION_SFX;
}

function getVisualSfxSpec(kind: VisualSfxKind): SoundSpec {
  switch (kind) {
    case "ui_click_snap":
      return {
        duration: 0.16,
        tones: [{ start: 0, end: 0.055, fromHz: 1600, toHz: 700, gain: 0.95, type: "square" }],
      };
    case "whoosh_soft":
      return {
        duration: 0.2,
        tones: [
          { start: 0.015, end: 0.09, fromHz: 520, toHz: 300, gain: 0.08, type: "triangle" },
          { start: 0.02, end: 0.07, fromHz: 1500, toHz: 980, gain: 0.05, type: "sine" },
        ],
        noises: [
          { start: 0, end: 0.18, fromHz: 700, toHz: 4200, gain: 0.22 },
          { start: 0.04, end: 0.2, fromHz: 420, toHz: 1600, gain: 0.12 },
        ],
      };
    case "whoosh_rise":
      return {
        duration: 0.38,
        noises: [{ start: 0, end: 0.34, fromHz: 620, toHz: 2500, gain: 0.48 }],
      };
    case "pop_air":
      return {
        duration: 0.18,
        tones: [{ start: 0, end: 0.12, fromHz: 920, toHz: 520, gain: 0.52, type: "triangle" }],
        noises: [{ start: 0, end: 0.12, fromHz: 980, toHz: 1800, gain: 0.18 }],
      };
    default:
      return {
        duration: 0.14,
        tones: [{ start: 0, end: 0.06, fromHz: 1040, toHz: 720, gain: 0.68, type: "square" }],
      };
  }
}

function sampleTone(type: "sine" | "square" | "triangle", phase: number): number {
  if (type === "square") return Math.sin(phase) >= 0 ? 1 : -1;
  if (type === "triangle") return (2 / Math.PI) * Math.asin(Math.sin(phase));
  return Math.sin(phase);
}

function createWavDataUrl(kind: VisualSfxKind): string {
  const spec = getVisualSfxSpec(kind);
  const sampleRate = 44100;
  const totalSamples = Math.max(1, Math.floor(spec.duration * sampleRate));
  const samples = new Float32Array(totalSamples);

  spec.tones?.forEach((tone) => {
    let phase = 0;
    for (let index = 0; index < totalSamples; index += 1) {
      const time = index / sampleRate;
      if (time < tone.start || time > tone.end) continue;
      const local = (time - tone.start) / Math.max(tone.end - tone.start, 0.0001);
      const frequency = tone.fromHz + (tone.toHz - tone.fromHz) * local;
      phase += (2 * Math.PI * frequency) / sampleRate;
      const attack = Math.min(1, local / 0.12);
      const release = Math.min(1, (tone.end - time) / Math.max((tone.end - tone.start) * 0.5, 0.0001));
      const envelope = Math.min(attack, release);
      samples[index] += sampleTone(tone.type || "sine", phase) * tone.gain * envelope;
    }
  });

  spec.noises?.forEach((noise) => {
    for (let index = 0; index < totalSamples; index += 1) {
      const time = index / sampleRate;
      if (time < noise.start || time > noise.end) continue;
      const local = (time - noise.start) / Math.max(noise.end - noise.start, 0.0001);
      const envelope = Math.sin(Math.PI * Math.min(1, Math.max(0, local)));
      const tilt = noise.fromHz + (noise.toHz - noise.fromHz) * local;
      const colored = Math.sin((index / sampleRate) * tilt * 0.006) * (Math.random() * 2 - 1);
      samples[index] += colored * noise.gain * envelope;
    }
  });

  const pcm = new Int16Array(totalSamples);
  for (let index = 0; index < totalSamples; index += 1) {
    const value = Math.max(-1, Math.min(1, samples[index]));
    pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }

  const byteLength = 44 + pcm.length * 2;
  const buffer = new ArrayBuffer(byteLength);
  const view = new DataView(buffer);
  const writeString = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + pcm.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  for (let index = 0; index < pcm.length; index += 1) {
    view.setInt16(44 + index * 2, pcm[index], true);
  }

  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return `data:audio/wav;base64,${btoa(binary)}`;
}

function ensureVisualSfxAudio(
  kind: VisualSfxKind,
  cache: Partial<Record<VisualSfxKind, HTMLAudioElement>>,
): HTMLAudioElement {
  const existing = cache[kind];
  if (existing) return existing;
  const audio = new Audio(createWavDataUrl(kind));
  audio.preload = "auto";
  audio.volume = 0.24;
  cache[kind] = audio;
  return audio;
}

async function playVisualSfx(
  kind: VisualSfxKind,
  cache: Partial<Record<VisualSfxKind, HTMLAudioElement>>,
): Promise<void> {
  const base = ensureVisualSfxAudio(kind, cache);
  const audio = base.cloneNode(true) as HTMLAudioElement;
  audio.volume = base.volume;
  audio.currentTime = 0;
  await audio.play().catch(() => undefined);
}

function createBackgroundMusicLoopWavDataUrl(preset: BackgroundMusicPreset): string {
  const sampleRate = 44100;
  const duration = 8;
  const totalSamples = sampleRate * duration;
  const samples = new Float32Array(totalSamples);

  const noteFreq = (name: string) => ({
    C3: 130.81, D3: 146.83, E3: 164.81, F3: 174.61, G3: 196.0, A3: 220.0,
    C4: 261.63, D4: 293.66, E4: 329.63, G4: 392.0, A4: 440.0,
  }[name]);

  const sampleTone = (type: "sine" | "square" | "triangle", phase: number) => {
    if (type === "square") return Math.sin(phase) >= 0 ? 1 : -1;
    if (type === "triangle") return (2 / Math.PI) * Math.asin(Math.sin(phase));
    return Math.sin(phase);
  };

  const config = preset === "upbeat_motion"
    ? { chords: [["C4", "E4", "G4"], ["A3", "C4", "E4"], ["F3", "A3", "C4"], ["G3", "B3", "D4"]], beat: 0.33, gain: 0.34, wave: "square" as const, bass: ["C3", "A2", "F2", "G2"] }
    : preset === "warm_focus"
      ? { chords: [["A3", "C4", "E4"], ["G3", "B3", "D4"], ["F3", "A3", "C4"], ["G3", "B3", "D4"]], beat: 0.5, gain: 0.24, wave: "triangle" as const, bass: ["A2", "G2", "F2", "G2"] }
      : { chords: [["C4", "E4", "G4"], ["A3", "C4", "E4"], ["F3", "A3", "C4"], ["G3", "B3", "D4"]], beat: 0.66, gain: 0.22, wave: "sine" as const, bass: ["C3", "A2", "F2", "G2"] };

  const phases = [0, 0, 0];
  let bassPhase = 0;
  for (let index = 0; index < totalSamples; index += 1) {
    const time = index / sampleRate;
    const chordIndex = Math.floor(time / (config.beat * 4)) % config.chords.length;
    const chord = config.chords[chordIndex];
    const bassNote = config.bass[chordIndex];
    const localBeat = (time % config.beat) / config.beat;
    const pulse = 0.35 + 0.65 * Math.max(0, 1 - localBeat * 1.3);
    let value = 0;
    chord.forEach((note, noteIndex) => {
      const frequency = noteFreq(note);
      phases[noteIndex] += (2 * Math.PI * frequency) / sampleRate;
      value += sampleTone(config.wave, phases[noteIndex]) * config.gain * pulse * (noteIndex === 1 ? 0.9 : 0.65);
    });
    bassPhase += (2 * Math.PI * noteFreq(bassNote)) / sampleRate;
    value += sampleTone("sine", bassPhase) * 0.14 * (0.6 + pulse * 0.4);
    const beatPhase = (time % config.beat) / config.beat;
    const transient = Math.max(0, 1 - beatPhase * 8);
    value += Math.sin(time * Math.PI * 2 * 140) * transient * 0.22;
    value += Math.sin(time * Math.PI * 2 * 280) * transient * 0.1;
    samples[index] = Math.max(-1, Math.min(1, value));
  }

  const pcm = new Int16Array(totalSamples);
  for (let index = 0; index < totalSamples; index += 1) {
    const value = Math.max(-1, Math.min(1, samples[index]));
    pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }

  const byteLength = 44 + pcm.length * 2;
  const buffer = new ArrayBuffer(byteLength);
  const view = new DataView(buffer);
  const writeString = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + pcm.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  for (let index = 0; index < pcm.length; index += 1) view.setInt16(44 + index * 2, pcm[index], true);
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
  return `data:audio/wav;base64,${btoa(binary)}`;
}

function ensureBackgroundMusicAudio(
  preset: BackgroundMusicPreset,
  cache: Partial<Record<BackgroundMusicPreset, HTMLAudioElement>>,
): HTMLAudioElement {
  const existing = cache[preset];
  if (existing) return existing;
  const audio = new Audio(createBackgroundMusicLoopWavDataUrl(preset));
  audio.preload = "auto";
  audio.loop = true;
  cache[preset] = audio;
  return audio;
}

function AnimatedMotif({
  spec,
  progress,
}: {
  spec: VisualAnimationSpec;
  progress: number;
}) {
  const colors = paletteColors(spec.palette);
  const orbit = easeInOutCubic(clamp(progress, 0, 1));
  const pulseBase = spec.motionProfile === "punchy" ? 0.05 : spec.motionProfile === "calm" ? 0.02 : 0.035;
  const pulse = 0.96 + Math.sin(progress * Math.PI * 2) * pulseBase;
  const draw = easeOutCubic(clamp(progress / 0.7, 0, 1));
  const variantIndex = Number(spec.variant.slice(1)) || 1;
  const mirrored = variantIndex % 2 === 0;
  const shift = (variantIndex - 3) * 6;

  if (spec.motif === "conversation_flow") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <g transform={mirrored ? "translate(320 0) scale(-1 1)" : undefined}>
        <g transform={`translate(${12 * (1 - orbit) + shift} ${18 * (1 - orbit)})`} opacity={0.92}>
          <rect x="34" y="22" width="112" height="56" rx="18" fill={colors.soft} stroke={colors.primary} strokeWidth="3" />
          <path d="M74 78 L64 98 L96 82" fill={colors.soft} stroke={colors.primary} strokeWidth="3" strokeLinejoin="round" />
        </g>
        <g transform={`translate(${220 - 14 * (1 - orbit)} ${58 + 16 * (1 - orbit)})`} opacity={0.9}>
          <rect x="-84" y="0" width="102" height="48" rx="16" fill={colors.soft} stroke={colors.secondary} strokeWidth="3" />
          <path d="M-20 48 L-2 68 L-28 56" fill={colors.soft} stroke={colors.secondary} strokeWidth="3" strokeLinejoin="round" />
        </g>
        <g transform={`translate(72 126) scale(${pulse.toFixed(4)})`}>
          <circle cx="0" cy="0" r="22" fill={colors.accent} />
          <rect x="-18" y="26" width="52" height="34" rx="17" fill={colors.accent} />
        </g>
        <g transform={`translate(238 126) scale(${(1.02 - (pulse - 0.96)).toFixed(4)})`}>
          <circle cx="0" cy="0" r="22" fill={colors.accent} />
          <rect x="-34" y="26" width="52" height="34" rx="17" fill={colors.accent} />
        </g>
        <path
          d="M120 132 C146 110, 174 110, 202 132"
          fill="none"
          stroke={colors.accent}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray="120"
          strokeDashoffset={120 - 120 * draw}
        />
        </g>
      </svg>
    );
  }

  if (spec.motif === "question_answer") {
    const local = easeOutCubic(clamp(progress / 0.35, 0, 1));
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <g transform={`translate(${38} ${38}) scale(${(0.82 + local * 0.18).toFixed(4)})`} opacity={local}>
          <circle cx="42" cy="42" r="34" fill={colors.soft} stroke={colors.primary} strokeWidth="4" />
          <text x="42" y="54" fill={colors.accent} fontSize="46" fontWeight="900" textAnchor="middle">?</text>
        </g>
        <g transform={`translate(${182} ${82}) scale(${(0.82 + local * 0.18).toFixed(4)})`} opacity={local}>
          <circle cx="42" cy="42" r="34" fill={colors.soft} stroke={colors.secondary} strokeWidth="4" />
          <path d="M22 44 L36 58 L62 28" fill="none" stroke={colors.accent} strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" />
        </g>
        <path d="M102 80 C132 72, 156 72, 184 102" fill="none" stroke={colors.accent} strokeWidth="7" strokeLinecap="round" strokeDasharray="120" strokeDashoffset={120 - 120 * draw} />
      </svg>
    );
  }

  if (spec.motif === "step_sequence") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        {[0, 1, 2].map((index) => {
          const local = easeOutCubic(clamp((progress - index * 0.12) / 0.28, 0, 1));
          return (
            <g key={index} transform={`translate(${44 + index * 82} ${118 - index * 24}) scale(${(0.8 + local * 0.2).toFixed(4)})`} opacity={local}>
              <rect x="0" y="-22" width="56" height="56" rx="18" fill="rgba(255,255,255,0.92)" />
              <circle cx="28" cy="6" r="10" fill="rgba(65,116,240,0.9)" />
            </g>
          );
        })}
        <path
          d="M70 118 C98 98, 116 92, 146 92 S196 72, 228 52"
          fill="none"
          stroke="rgba(150,193,255,0.92)"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray="210"
          strokeDashoffset={210 - 210 * draw}
        />
      </svg>
    );
  }

  if (spec.motif === "checklist_reveal") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        {[0, 1, 2].map((index) => {
          const local = easeOutCubic(clamp((progress - index * 0.09) / 0.22, 0, 1));
          return (
            <g key={index} transform={`translate(44 ${40 + index * 42})`} opacity={local}>
              <rect x="0" y="-10" width="34" height="34" rx="10" fill="rgba(111,255,211,0.18)" stroke="rgba(131,255,218,0.9)" strokeWidth="3" />
              <path d="M10 7 L16 14 L25 1" fill="none" stroke="rgba(255,255,255,0.94)" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
              <rect x="52" y="-1" width={120 - index * 18} height="10" rx="5" fill="rgba(255,255,255,0.85)" />
            </g>
          );
        })}
      </svg>
    );
  }

  if (spec.motif === "compare_problem_solution") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <circle cx="68" cy="108" r="30" fill="rgba(255,108,133,0.18)" stroke="rgba(255,126,147,0.84)" strokeWidth="4" />
        <path d="M54 94 L82 122 M82 94 L54 122" stroke="rgba(255,255,255,0.9)" strokeWidth="7" strokeLinecap="round" />
        <circle cx="252" cy="64" r="34" fill="rgba(98,227,182,0.18)" stroke="rgba(126,244,203,0.84)" strokeWidth="4" />
        <path d="M236 65 L248 78 L270 50" stroke="rgba(255,255,255,0.9)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <path
          d="M98 108 C132 118, 154 118, 180 92 S218 66, 220 66"
          fill="none"
          stroke="rgba(255,255,255,0.9)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray="170"
          strokeDashoffset={170 - 170 * draw}
        />
        <circle cx={98 + 122 * draw} cy={108 - 42 * draw} r="8" fill="rgba(157,197,255,1)" />
      </svg>
    );
  }

  if (spec.motif === "decision_split") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <path d="M160 32 L160 94" fill="none" stroke="rgba(255,255,255,0.92)" strokeWidth="8" strokeLinecap="round" />
        <path d="M160 94 L100 140" fill="none" stroke="rgba(255,126,147,0.86)" strokeWidth="8" strokeLinecap="round" strokeDasharray="90" strokeDashoffset={90 - 90 * draw} />
        <path d="M160 94 L222 140" fill="none" stroke="rgba(126,244,203,0.86)" strokeWidth="8" strokeLinecap="round" strokeDasharray="90" strokeDashoffset={90 - 90 * draw} />
        <circle cx="160" cy="26" r="16" fill="rgba(151,193,255,0.94)" />
        <circle cx="96" cy="144" r="22" fill="rgba(255,126,147,0.2)" stroke="rgba(255,126,147,0.88)" strokeWidth="4" />
        <circle cx="226" cy="144" r="22" fill="rgba(126,244,203,0.2)" stroke="rgba(126,244,203,0.88)" strokeWidth="4" />
      </svg>
    );
  }

  if (spec.motif === "timeline_sequence") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <path d="M38 92 L282 92" fill="none" stroke="rgba(255,255,255,0.72)" strokeWidth="8" strokeLinecap="round" />
        {[0, 1, 2, 3].map((index) => {
          const local = easeOutCubic(clamp((progress - index * 0.08) / 0.2, 0, 1));
          return (
            <g key={index} transform={`translate(${56 + index * 62} 92)`} opacity={local}>
              <circle cx="0" cy="0" r="16" fill={index % 2 === 0 ? "rgba(151,193,255,0.96)" : "rgba(126,244,203,0.92)"} />
              <rect x="-20" y={index % 2 === 0 ? -46 : 28} width="40" height="12" rx="6" fill="rgba(255,255,255,0.84)" />
            </g>
          );
        })}
      </svg>
    );
  }

  if (spec.motif === "object_spotlight") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <circle cx="160" cy="92" r={44 + orbit * 12} fill="rgba(255,255,255,0.08)" stroke="rgba(151,193,255,0.86)" strokeWidth="3" />
        <path d="M160 26 L160 56" fill="none" stroke="rgba(255,255,255,0.85)" strokeWidth="7" strokeLinecap="round" />
        <rect x="122" y="58" width="76" height="68" rx="16" fill="rgba(255,255,255,0.92)" />
        <circle cx="160" cy="92" r="18" fill="rgba(151,193,255,0.94)" />
        <rect x="138" y="132" width="44" height="12" rx="6" fill="rgba(255,255,255,0.7)" />
      </svg>
    );
  }

  if (spec.motif === "process_arrow") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <rect x="32" y="72" width="64" height="36" rx="14" fill="rgba(255,255,255,0.88)" />
        <rect x="126" y="72" width="64" height="36" rx="14" fill="rgba(151,193,255,0.92)" />
        <rect x="222" y="72" width="64" height="36" rx="14" fill="rgba(126,244,203,0.9)" />
        <path d="M96 90 L126 90" fill="none" stroke="rgba(255,255,255,0.88)" strokeWidth="7" strokeLinecap="round" strokeDasharray="36" strokeDashoffset={36 - 36 * draw} />
        <path d="M190 90 L222 90" fill="none" stroke="rgba(255,255,255,0.88)" strokeWidth="7" strokeLinecap="round" strokeDasharray="38" strokeDashoffset={38 - 38 * draw} />
      </svg>
    );
  }

  if (spec.motif === "chart_pop") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => {
          const local = easeOutCubic(clamp((progress - index * 0.06) / 0.18, 0, 1));
          const heights = [44, 74, 58, 96];
          return (
            <rect
              key={index}
              x={58 + index * 46}
              y={132 - heights[index] * local}
              width="26"
              height={heights[index] * local}
              rx="8"
              fill={index === 3 ? "rgba(126,244,203,0.94)" : "rgba(151,193,255,0.92)"}
            />
          );
        })}
        <path d="M48 132 L272 132" fill="none" stroke="rgba(255,255,255,0.7)" strokeWidth="6" strokeLinecap="round" />
      </svg>
    );
  }

  if (spec.motif === "map_pointer") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <path d="M44 44 C72 20, 124 18, 156 42 S236 54, 274 34 L274 126 C248 146, 200 154, 160 136 S76 122, 44 144 Z" fill="rgba(255,255,255,0.08)" stroke="rgba(151,193,255,0.72)" strokeWidth="3" />
        <path d="M160 54 C174 54, 186 66, 186 80 C186 102, 160 126, 160 126 C160 126, 134 102, 134 80 C134 66, 146 54, 160 54 Z" fill="rgba(126,244,203,0.94)" />
        <circle cx="160" cy="80" r="9" fill="rgba(15,23,42,0.84)" />
        <circle cx={160 + orbit * 28} cy={80 - orbit * 8} r="10" fill="rgba(255,255,255,0.92)" opacity="0.85" />
      </svg>
    );
  }

  if (spec.motif === "concept_network") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        {[[72,96],[134,54],[190,96],[250,62],[224,132]].map(([x, y], index) => {
          const local = easeOutCubic(clamp((progress - index * 0.05) / 0.22, 0, 1));
          return (
            <g key={index} transform={`translate(${x} ${y}) scale(${(0.65 + local * 0.35).toFixed(4)})`} opacity={local}>
              <circle cx="0" cy="0" r={index === 2 ? 22 : 15} fill={index === 2 ? "rgba(126,244,203,0.92)" : "rgba(151,193,255,0.94)"} />
            </g>
          );
        })}
        {["M72 96 L134 54","M134 54 L190 96","M190 96 L250 62","M190 96 L224 132","M72 96 L190 96"].map((d, index) => (
          <path key={index} d={d} fill="none" stroke="rgba(255,255,255,0.72)" strokeWidth="5" strokeLinecap="round" strokeDasharray="90" strokeDashoffset={90 - 90 * draw} />
        ))}
      </svg>
    );
  }

  if (spec.motif === "idea_burst") {
    return (
      <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
        <circle cx="160" cy="92" r="28" fill="rgba(255,255,255,0.94)" />
        {[0, 1, 2, 3, 4, 5].map((index) => {
          const angle = (Math.PI * 2 * index) / 6;
          const x = 160 + Math.cos(angle) * (34 + orbit * 22);
          const y = 92 + Math.sin(angle) * (34 + orbit * 22);
          return <circle key={index} cx={x} cy={y} r={8 + (index % 2) * 3} fill={index % 2 === 0 ? "rgba(151,193,255,0.92)" : "rgba(126,244,203,0.86)"} />;
        })}
      </svg>
    );
  }

  return (
    <svg className="visualOverlayIllustration" viewBox="0 0 320 180" aria-hidden="true">
      <circle cx="160" cy="92" r="24" fill="rgba(255,255,255,0.94)" />
      <path d="M160 50 L160 20 M202 64 L228 42 M202 122 L232 138 M118 122 L90 146 M118 64 L92 42" fill="none" stroke="rgba(151,193,255,0.86)" strokeWidth="7" strokeLinecap="round" strokeDasharray="48" strokeDashoffset={48 - 48 * draw} />
    </svg>
  );
}

function normalizeTranscriptBackfillError(error: unknown): string {
  const text = String((error as Error).message || error || "").trim();
  if (text === "Not Found") {
    return "Transcript backfill endpoint is unavailable. Restart the backend so the latest /transcribe-missing route is loaded.";
  }
  return text || "Transcript processing request failed.";
}

function getTranscriptText(track: ProjectTrack): string | null {
  const text = track.transcription?.text;
  if (typeof text !== "string") return null;
  const normalized = text.trim();
  return normalized.length > 0 ? normalized : null;
}

function getTranscriptSegments(track: ProjectTrack): TranscriptSegment[] {
  return Array.isArray(track.transcription?.segments) ? track.transcription?.segments || [] : [];
}

function getTrackBackgroundMusic(track: ProjectTrack): BackgroundMusicSettings {
  const raw: Partial<BackgroundMusicSettings> = track.background_music || {};
  const preset = raw.preset === "upbeat_motion" || raw.preset === "warm_focus" || raw.preset === "ambient_pulse"
    ? raw.preset
    : DEFAULT_BACKGROUND_MUSIC.preset;
  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : DEFAULT_BACKGROUND_MUSIC.enabled,
    preset,
    volume: typeof raw.volume === "number" ? clamp(raw.volume, 0, 1) : DEFAULT_BACKGROUND_MUSIC.volume,
    ducking: typeof raw.ducking === "number" ? raw.ducking : DEFAULT_BACKGROUND_MUSIC.ducking,
  };
}

function getTranscriptWords(track: ProjectTrack) {
  return Array.isArray(track.transcription?.words) ? track.transcription?.words || [] : [];
}

function normalizeSpokenToken(value: string): string {
  return value
    .toLowerCase()
    .replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "")
    .trim();
}

function getNormalizedTranscriptWords(track: ProjectTrack) {
  return getTranscriptWords(track)
    .map((word, index) => ({
      index,
      word: String(word.word || "").trim(),
      token: normalizeSpokenToken(String(word.word || "")),
      start: Math.max(0, Number(word.start || 0)),
      end: Math.max(Number(word.start || 0), Number(word.end || 0)),
    }))
    .filter((word) => word.token && word.end >= word.start);
}

function buildVisualSearchTokens(part: VisualPlanPart): string[] {
  const base = [
    part.text || "",
    part.title || "",
    ...(Array.isArray(part.keywords) ? part.keywords : []),
  ]
    .join(" ")
    .split(/\s+/)
    .map(normalizeSpokenToken)
    .filter(Boolean);

  const filtered = base.filter((token) => token.length > 2 && !STOP_WORDS.has(token));
  return filtered.length >= 2 ? filtered : base;
}

function findOrderedPhraseMatch(
  transcriptTokens: string[],
  phraseTokens: string[],
): { startOffset: number; endOffset: number } | null {
  const maxWindow = Math.min(6, phraseTokens.length);
  for (let windowSize = maxWindow; windowSize >= 2; windowSize -= 1) {
    for (let phraseStart = 0; phraseStart <= phraseTokens.length - windowSize; phraseStart += 1) {
      const phraseSlice = phraseTokens.slice(phraseStart, phraseStart + windowSize);
      for (let transcriptStart = 0; transcriptStart <= transcriptTokens.length - windowSize; transcriptStart += 1) {
        let matched = true;
        for (let offset = 0; offset < windowSize; offset += 1) {
          if (transcriptTokens[transcriptStart + offset] !== phraseSlice[offset]) {
            matched = false;
            break;
          }
        }
        if (matched) {
          return {
            startOffset: transcriptStart,
            endOffset: transcriptStart + windowSize - 1,
          };
        }
      }
    }
  }
  return null;
}

function resolveVisualPartTiming(
  part: VisualPlanPart,
  transcriptWords: Array<{ index: number; word: string; token: string; start: number; end: number }>,
): { start: number; end: number; duration: number } {
  const fallbackStart = Math.max(0, part.start);
  const fallbackEnd = Math.max(fallbackStart + 0.18, part.end);
  if (transcriptWords.length === 0) {
    return {
      start: fallbackStart,
      end: fallbackEnd,
      duration: fallbackEnd - fallbackStart,
    };
  }

  const phraseTokens = buildVisualSearchTokens(part);
  if (phraseTokens.length === 0) {
    return {
      start: fallbackStart,
      end: fallbackEnd,
      duration: fallbackEnd - fallbackStart,
    };
  }

  const windowStart = Math.max(0, part.start - 0.45);
  const windowEnd = part.end + 0.45;
  const candidateWords = transcriptWords.filter((word) => word.end >= windowStart && word.start <= windowEnd);
  if (candidateWords.length === 0) {
    return {
      start: fallbackStart,
      end: fallbackEnd,
      duration: fallbackEnd - fallbackStart,
    };
  }

  const exact = findOrderedPhraseMatch(candidateWords.map((word) => word.token), phraseTokens);
  if (exact) {
    const first = candidateWords[exact.startOffset];
    const last = candidateWords[exact.endOffset];
    const alignedStart = Math.max(part.start, first.start - 0.08);
    const alignedEnd = Math.min(part.end, Math.max(last.end + 0.55, alignedStart + 0.75));
    return {
      start: alignedStart,
      end: alignedEnd,
      duration: alignedEnd - alignedStart,
    };
  }

  const fallbackToken = phraseTokens.find((token) => token.length > 3 && !STOP_WORDS.has(token)) || phraseTokens[0];
  const anchor = candidateWords.find((word) => word.token === fallbackToken);
  if (anchor) {
    const alignedStart = Math.max(part.start, anchor.start - 0.08);
    const alignedEnd = Math.min(part.end, Math.max(anchor.end + 0.9, alignedStart + Math.min(1.4, Math.max(0.75, part.duration * 0.45))));
    return {
      start: alignedStart,
      end: alignedEnd,
      duration: alignedEnd - alignedStart,
    };
  }

  return {
    start: fallbackStart,
    end: fallbackEnd,
    duration: fallbackEnd - fallbackStart,
  };
}

function enforceMinimumVisualDuration(
  parts: Array<{ part: VisualPlanPart; index: number; start: number; end: number; duration: number }>,
  maxEnd: number,
): Array<{ part: VisualPlanPart; index: number; start: number; end: number; duration: number }> {
  const minVisibleSeconds = 3;
  const boundedMaxEnd = Math.max(minVisibleSeconds, maxEnd || 0);
  const resolved: Array<{ part: VisualPlanPart; index: number; start: number; end: number; duration: number }> = [];

  parts.forEach((item) => {
    const previous = resolved[resolved.length - 1];
    const minStart = previous ? previous.start + minVisibleSeconds : 0;
    const start = Math.max(item.start, minStart);
    const end = Math.min(boundedMaxEnd, Math.max(item.end, start + minVisibleSeconds));
    resolved.push({
      ...item,
      start,
      end,
      duration: Math.max(0.001, end - start),
    });
  });

  return resolved.filter((item) => item.duration >= minVisibleSeconds - 0.001);
}

function buildActiveSubtitleWord(track: ProjectTrack, time: number): string | null {
  const words = getNormalizedTranscriptWords(track);

  if (words.length === 0) return null;

  const activeIndex = words.findIndex((word) => time >= word.start && time <= word.end + 0.04);
  if (activeIndex < 0) return null;
  return words[activeIndex].word || null;
}

function hasTranscript(track: ProjectTrack): boolean {
  if (getTranscriptText(track)) return true;
  return Array.isArray(track.transcription?.words) && track.transcription!.words!.length > 0;
}

function getTrackTranscriptStatus(track: ProjectTrack): TranscriptStatus | null {
  if (hasTranscript(track)) return "completed";
  const status = track.metadata?.transcript_status;
  if (
    status === "pending" ||
    status === "processing" ||
    status === "completed" ||
    status === "error" ||
    status === "not_applicable"
  ) {
    return status;
  }
  return null;
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" className="speechEditorIcon" aria-hidden="true">
      <path d="M8 6.5v11l9-5.5z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 24 24" className="speechEditorIcon" aria-hidden="true">
      <rect x="7" y="6" width="4" height="12" rx="1.5" fill="currentColor" />
      <rect x="13" y="6" width="4" height="12" rx="1.5" fill="currentColor" />
    </svg>
  );
}

function isTranscriptProcessing(track: ProjectTrack): boolean {
  const status = getTrackTranscriptStatus(track);
  return status === "pending" || status === "processing";
}

function getSpeechFilterStatus(track: ProjectTrack): string | null {
  const status = track.metadata?.speech_filter_status;
  return typeof status === "string" && status.trim() ? status : null;
}

function getVisualWorkerStatus(track: ProjectTrack): string | null {
  const status = track.metadata?.visual_worker_status;
  return typeof status === "string" && status.trim() ? status : null;
}

function normalizeEditableCuts(cuts: SpeechFilterCut[]): SpeechFilterCut[] {
  return [...cuts]
    .map((cut) => ({
      ...cut,
      start: roundToMillis(Math.max(0, cut.start)),
      end: roundToMillis(Math.max(cut.start, cut.end)),
      duration: roundToMillis(Math.max(0, cut.end - cut.start)),
    }))
    .sort((left, right) => left.start - right.start);
}

function findCutAtTime(cuts: SpeechFilterCut[], time: number): SpeechFilterCut | null {
  return cuts.find((cut) => time >= cut.start && time < cut.end - 0.01) || null;
}

function resolvePlayableTime(time: number, cuts: SpeechFilterCut[], duration: number): number {
  let nextTime = clamp(time, 0, duration);
  for (const cut of cuts) {
    if (nextTime >= cut.start && nextTime < cut.end - 0.01) {
      nextTime = clamp(roundToMillis(cut.end + 0.01), 0, duration);
    }
  }
  return nextTime;
}

function buildZoomPreviewBeats(track: ProjectTrack): Array<ZoomPreviewBeat & { id: string; label: string }> {
  const segments = getTranscriptSegments(track)
    .map((segment) => ({
      start: Math.max(0, segment.start || 0),
      end: Math.max(segment.start || 0, segment.end || 0),
      text: String(segment.text || "").trim(),
    }))
    .filter((segment) => segment.text && segment.end - segment.start >= 0.8)
    .sort((left, right) => left.start - right.start);

  if (segments.length === 0) {
    return [];
  }

  const merged: ZoomPreviewBeat[] = [];
  let currentStart = segments[0].start;
  let currentEnd = segments[0].end;
  let currentText = segments[0].text;

  const flush = () => {
    const duration = roundToMillis(Math.max(0, currentEnd - currentStart));
    if (duration < 1.2) return;
    const index = merged.length;
    const normalizedLabel = currentText.replace(/\s+/g, " ").trim();
    merged.push({
      start: roundToMillis(currentStart),
      end: roundToMillis(currentEnd),
      duration,
      text: normalizedLabel,
      scale: index % 3 === 1 ? 1.18 : 1.12,
      enabled: index % 2 === 0,
    });
  };

  for (let index = 1; index < segments.length; index += 1) {
    const segment = segments[index];
    const nextDuration = segment.end - currentStart;
    const gap = segment.start - currentEnd;
    if (nextDuration <= 4.3 && gap <= 0.55) {
      currentEnd = segment.end;
      currentText = `${currentText} ${segment.text}`.trim();
      continue;
    }
    flush();
    currentStart = segment.start;
    currentEnd = segment.end;
    currentText = segment.text;
  }
  flush();
  return merged.map((beat, index) => ({
    ...beat,
    id: `zoom-${index + 1}`,
    label: beat.text.length > 88 ? `${beat.text.slice(0, 85).trim()}...` : beat.text,
  }));
}

function isTrackTranscribable(track: ProjectTrack): boolean {
  return track.type === "video" || track.type === "audio";
}

function projectHasProcessingTranscripts(project: ProjectDetail | null): boolean {
  return Boolean(project?.tracks.some((track) => {
    const status = getTrackTranscriptStatus(track);
    return status === "pending" || status === "processing";
  }));
}

function shouldRequestTranscriptBackfill(project: ProjectDetail | null): boolean {
  if (!project) return false;
  return project.tracks.some((track) => {
    if (!isTrackTranscribable(track)) return false;
    const status = getTrackTranscriptStatus(track);
    if (status === "completed" || status === "processing" || status === "not_applicable") {
      return false;
    }
    return !hasTranscript(track);
  });
}

function sortTracksForProject(tracks: ProjectTrack[]): ProjectTrack[] {
  return [...tracks].sort((left, right) => {
    if (left.position !== right.position) return left.position - right.position;
    const leftKey = filenameNaturalKey(left.filename || "");
    const rightKey = filenameNaturalKey(right.filename || "");
    const maxLength = Math.max(leftKey.length, rightKey.length);
    for (let index = 0; index < maxLength; index += 1) {
      const a = leftKey[index];
      const b = rightKey[index];
      if (a === undefined) return -1;
      if (b === undefined) return 1;
      if (typeof a === "number" && typeof b === "number") {
        if (a !== b) return a - b;
      } else {
        const leftPart = String(a);
        const rightPart = String(b);
        if (leftPart !== rightPart) return leftPart.localeCompare(rightPart);
      }
    }
    return left.position - right.position;
  });
}

function isTrackHidden(track: ProjectTrack): boolean {
  return track.status === "hidden" || track.excluded === true;
}

function isBackgroundTrack(track: ProjectTrack): boolean {
  return track.role === "background";
}

function isImageTrack(track: ProjectTrack): boolean {
  return track.type === "image";
}

function getTrackRenderVersions(track: ProjectTrack): TrackRenderVersion[] {
  const raw = track.render_versions;
  if (!Array.isArray(raw)) return [];
  return [...raw].sort((left, right) => {
    const leftTime = new Date(left.created_at).getTime();
    const rightTime = new Date(right.created_at).getTime();
    if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return right.label.localeCompare(left.label, undefined, { numeric: true });
  });
}

function getTrackSize(track: ProjectTrack): number | null {
  const raw = track.metadata?.size;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function isDuplicateSelectedFile(file: File, tracks: ProjectTrack[]): boolean {
  const normalizedName = file.name.trim().toLowerCase();
  return tracks.some((track) => {
    const trackName = String(track.filename || "").trim().toLowerCase();
    const trackSize = getTrackSize(track);
    return trackName === normalizedName && trackSize === file.size;
  });
}

async function getCachedMediaResponse(url: string): Promise<Response | null> {
  if (typeof window === "undefined" || !("caches" in window)) return null;
  const cache = await window.caches.open(MEDIA_BLOB_CACHE_NAME);
  return cache.match(url);
}

async function putCachedMediaResponse(url: string, response: Response): Promise<void> {
  if (typeof window === "undefined" || !("caches" in window)) return;
  const cache = await window.caches.open(MEDIA_BLOB_CACHE_NAME);
  await cache.put(url, response);
}

async function loadAuthedMediaObjectUrl(url: string, token: string): Promise<string> {
  const existing = mediaObjectUrlCache.get(url);
  if (existing) return existing;

  const pending = mediaObjectUrlPromiseCache.get(url);
  if (pending) return pending;

  const task = (async () => {
    const cachedResponse = await getCachedMediaResponse(url);
    if (cachedResponse?.ok) {
      const cachedObjectUrl = URL.createObjectURL(await cachedResponse.blob());
      mediaObjectUrlCache.set(url, cachedObjectUrl);
      return cachedObjectUrl;
    }

    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    });
    if (!response.ok) throw new Error(`Media ${response.status}`);

    await putCachedMediaResponse(url, response.clone());
    const objectUrl = URL.createObjectURL(await response.blob());
    mediaObjectUrlCache.set(url, objectUrl);
    return objectUrl;
  })();

  mediaObjectUrlPromiseCache.set(url, task);
  try {
    return await task;
  } finally {
    mediaObjectUrlPromiseCache.delete(url);
  }
}

/**
 * Fetch the URL with the auth Bearer token and return an object URL
 * that can be assigned to a <video src>. Uses Cache Storage to persist
 * previously fetched previews across page reloads.
 */
function useAuthedBlobUrl(
  url: string,
  token: string | undefined,
): { blobUrl: string | null; loading: boolean; error: string | null } {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url || !token) {
      setBlobUrl(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    loadAuthedMediaObjectUrl(url, token)
      .then((objectUrl) => {
        if (cancelled) return;
        setBlobUrl(objectUrl);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setBlobUrl(null);
          setLoading(false);
          setError(err instanceof Error ? err.message : "Unable to load media");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [url, token]);

  return { blobUrl, loading, error };
}

function TranscriptPanel({
  track,
  speechFilter,
  speechFilterLoading,
  speechFilterError,
  visualPlan,
  visualPlanLoading,
  visualPlanError,
  onGenerateSpeechFilter,
  onGenerateVisualPlan,
}: {
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterError?: string | null;
  visualPlan?: VisualPlanArtifact | null;
  visualPlanLoading: boolean;
  visualPlanError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onGenerateVisualPlan: (track: ProjectTrack) => void;
}) {
  const transcriptStatus = getTrackTranscriptStatus(track);

  if (transcriptStatus === "pending" || transcriptStatus === "processing") {
    return (
      <span className="clipTag clipTagProcessing">
        <span className="spinner" aria-hidden="true" />
        transcript processing
      </span>
    );
  }

  if (transcriptStatus === "error") {
    return <span className="clipTag clipTagMuted">transcript failed</span>;
  }

  if (!hasTranscript(track)) {
    return <span className="clipTag clipTagMuted">no transcript</span>;
  }

  const transcriptText = getTranscriptText(track);
  const segments = getTranscriptSegments(track);
  const speechFilterStatus = getSpeechFilterStatus(track);
  const hasSpeechFilter = Boolean(speechFilter);
  const visualPlanStatus = getVisualWorkerStatus(track);
  const hasVisualPlan = Boolean(visualPlan);

  return (
    <div className="clipTranscriptStack">
      <details className="clipTranscriptDetails">
        <summary className="clipTag clipTagActive">transcript</summary>
        <div className="clipTranscriptPanel">
          {segments.length > 0 ? (
            <div className="clipTranscriptList">
              {segments.map((segment, index) => (
                <div key={`${segment.start}-${segment.end}-${index}`} className="clipTranscriptRow">
                  <span className="clipTranscriptTime">
                    {formatTime(segment.start)} - {formatTime(segment.end)}
                  </span>
                  <span>{segment.text}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted clipTranscriptText">{transcriptText}</p>
          )}
        </div>
      </details>

      <div className="clipSpeechFilter">
        <button
          type="button"
          className={`clipTag clipTagButton ${speechFilterLoading ? "clipTagProcessing" : "clipTagActive"}`}
          disabled={speechFilterLoading}
          onClick={() => onGenerateSpeechFilter(track)}
        >
          {speechFilterLoading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              filtering speech
            </>
          ) : hasSpeechFilter ? (
            "rerun filter speech"
          ) : speechFilterStatus === "completed" ? (
            "load speech cuts"
          ) : (
            "filter speech"
          )}
        </button>
        {speechFilterError ? <p className="clipSpeechFilterError">{speechFilterError}</p> : null}
        {speechFilter ? (
          <details className="clipSpeechFilterDetails">
            <summary className={`clipTag ${speechFilter.cuts.length ? "clipTagWarn" : "clipTagMuted"}`}>
              {speechFilter.cuts.length ? `suggested cuts (${speechFilter.cuts.length})` : "no cuts suggested"}
            </summary>
            <div className="clipSpeechFilterPanel">
              <p className="muted clipSpeechFilterSummary">{speechFilter.summary}</p>
              {speechFilter.cuts.length > 0 ? (
                <div className="clipSpeechFilterList">
                  {speechFilter.cuts.map((cut: SpeechFilterCut, index: number) => (
                    <div key={`${cut.start}-${cut.end}-${index}`} className="clipSpeechFilterRow">
                      <div className="clipSpeechFilterHeader">
                        <strong>{formatTime(cut.start)} - {formatTime(cut.end)}</strong>
                        <span className="muted">{formatSpeechFilterReason(cut.reason)}</span>
                      </div>
                      {cut.transcript ? <span className="clipSpeechFilterSnippet">“{cut.transcript}”</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </div>

      <div className="clipSpeechFilter">
        <button
          type="button"
          className={`clipTag clipTagButton ${visualPlanLoading ? "clipTagProcessing" : "clipTagActive"}`}
          disabled={visualPlanLoading}
          onClick={() => onGenerateVisualPlan(track)}
        >
          {visualPlanLoading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              generating visuals
            </>
          ) : hasVisualPlan ? (
            "rerun visuals"
          ) : visualPlanStatus === "completed" ? (
            "load visuals"
          ) : (
            "generate visuals"
          )}
        </button>
        {visualPlanError ? <p className="clipSpeechFilterError">{visualPlanError}</p> : null}
        {visualPlan ? (
          <details className="clipSpeechFilterDetails">
            <summary className={`clipTag ${visualPlan.parts.length ? "clipTagWarn" : "clipTagMuted"}`}>
              {visualPlan.parts.length ? `visual parts (${visualPlan.parts.length})` : "no visuals suggested"}
            </summary>
            <div className="clipSpeechFilterPanel">
              <p className="muted clipSpeechFilterSummary">{visualPlan.summary}</p>
              {visualPlan.parts.length > 0 ? (
                <div className="clipSpeechFilterList">
                  {visualPlan.parts.map((part: VisualPlanPart, index: number) => (
                    <div key={`${part.start}-${part.end}-${index}`} className="clipSpeechFilterRow">
                      <div className="clipSpeechFilterHeader">
                        <strong>{formatTime(part.start)} - {formatTime(part.end)}</strong>
                        <span className="muted">{part.visual_type === "web_image" ? "Web Image" : "Animation"}</span>
                      </div>
                      <span>{part.prompt}</span>
                      {part.text ? <span className="clipSpeechFilterSnippet">“{part.text}”</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function BackgroundClipAction({
  track,
  saving,
  onSave,
}: {
  track: ProjectTrack;
  saving: boolean;
  onSave: (track: ProjectTrack, description: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState(track.background_description || "");

  useEffect(() => {
    setEditing(false);
    setDescription(track.background_description || "");
  }, [track.background_description, track.id]);

  return (
    <div className="clipSpeechFilter">
      <button
        type="button"
        className={`clipTag clipTagButton ${saving ? "clipTagProcessing" : "clipTagActive"}`}
        disabled={saving}
        onClick={() => setEditing(true)}
      >
        {saving ? (
          <>
            <span className="spinner" aria-hidden="true" />
            saving background
          </>
        ) : (
          "use as background"
        )}
      </button>
      {editing ? (
        <div className="clipInlinePromptPanel">
          <p className="muted clipInlinePromptText">
            Describe what this background clip shows. This is used to place it against the spoken narration.
          </p>
          <textarea
            className="clipInlinePromptInput"
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Example: desk setup by a window with a person talking to camera"
          />
          {!description.trim() ? (
            <p className="clipSpeechFilterError">Background clips need a short description.</p>
          ) : null}
          <div className="clipInlinePromptActions">
            <button
              type="button"
              className="btn secondary"
              disabled={saving || !description.trim()}
              onClick={() => onSave(track, description)}
            >
              Save as background
            </button>
            <button
              type="button"
              className="btn secondary"
              disabled={saving}
              onClick={() => {
                setEditing(false);
                setDescription(track.background_description || "");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ClipCard({
  projectId,
  projectCreatedAt,
  token,
  track,
  speechFilter,
  speechFilterLoading,
  speechFilterError,
  visualPlan,
  visualPlanLoading,
  visualPlanError,
  onGenerateSpeechFilter,
  onGenerateVisualPlan,
  onMarkAsBackground,
  backgroundSaving,
  onOpen,
  onHide,
  hiding,
  draggable,
  dragActive,
  dragTarget,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: {
  projectId: string;
  projectCreatedAt?: string;
  token: string | undefined;
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterError?: string | null;
  visualPlan?: VisualPlanArtifact | null;
  visualPlanLoading: boolean;
  visualPlanError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onGenerateVisualPlan: (track: ProjectTrack) => void;
  onMarkAsBackground: (track: ProjectTrack, description: string) => void;
  backgroundSaving: boolean;
  onOpen: (track: ProjectTrack) => void;
  onHide: (trackId: string) => void;
  hiding: boolean;
  draggable: boolean;
  dragActive: boolean;
  dragTarget: boolean;
  onDragStart: (track: ProjectTrack) => void;
  onDragOver: (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => void;
  onDrop: (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
}) {
  const rawUrl = api.getTrackMediaUrl(projectId, track.id);
  const { blobUrl, loading, error } = useAuthedBlobUrl(rawUrl, token);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [posterReady, setPosterReady] = useState(false);
  const clipCreatedAt = track.recorded_at || projectCreatedAt;
  const orientationLabel = formatOrientation(track.orientation);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !blobUrl) return;
    setPosterReady(false);
    const onLoaded = () => {
      try {
        v.currentTime = 0.5;
      } catch {
        // ignore
      }
    };
    const onSeeked = () => setPosterReady(true);
    v.addEventListener("loadedmetadata", onLoaded);
    v.addEventListener("seeked", onSeeked);
    return () => {
      v.removeEventListener("loadedmetadata", onLoaded);
      v.removeEventListener("seeked", onSeeked);
    };
  }, [blobUrl]);

  return (
    <article
      className={`clipCard ${dragActive ? "clipCardDragging" : ""} ${dragTarget ? "clipCardDropTarget" : ""}`}
      draggable={draggable}
      onDragStart={() => onDragStart(track)}
      onDragOver={(event) => onDragOver(track, event)}
      onDrop={(event) => onDrop(track, event)}
      onDragEnd={onDragEnd}
    >
      <div className="clipMediaWrap">
        <button
          type="button"
          className="clipHideButton"
          aria-label={`Remove ${track.filename} from project`}
          disabled={hiding}
          onClick={(event) => {
            event.stopPropagation();
            onHide(track.id);
          }}
        >
          ×
        </button>
        <button type="button" className="clipPreviewButton" onClick={() => onOpen(track)}>
          {blobUrl ? (
            <video
              ref={videoRef}
              className="clipMedia"
              src={blobUrl}
              preload="metadata"
              playsInline
              muted
            />
          ) : (
            <div className="clipMedia clipMediaLoading" />
          )}
          {!blobUrl && error ? (
            <span className="clipPreviewPlaceholder">ERR</span>
          ) : !posterReady && (
            <span className="clipPreviewPlaceholder">{track.filename.slice(0, 2).toUpperCase()}</span>
          )}
          <span className="clipPreviewPlay">{error ? "Unavailable" : loading ? "Loading" : "▶ Play"}</span>
        </button>
      </div>
      <div className="clipMeta">
        <div className="stack" style={{ gap: 4 }}>
          <strong className="clipFileName" title={track.filename}>{track.filename}</strong>
          <span className="muted">{formatDate(clipCreatedAt)}</span>
          <span className="muted">Duration: {formatDuration(track.duration)}</span>
          {orientationLabel ? (
            <span className="muted">
              {orientationLabel}
              {track.width && track.height ? ` • ${track.width}×${track.height}` : ""}
            </span>
          ) : null}
        </div>
        <TranscriptPanel
          track={track}
          speechFilter={speechFilter}
          speechFilterLoading={speechFilterLoading}
          speechFilterError={speechFilterError}
          visualPlan={visualPlan}
          visualPlanLoading={visualPlanLoading}
          visualPlanError={visualPlanError}
          onGenerateSpeechFilter={onGenerateSpeechFilter}
          onGenerateVisualPlan={onGenerateVisualPlan}
        />
        <BackgroundClipAction
          track={track}
          saving={backgroundSaving}
          onSave={onMarkAsBackground}
        />
      </div>
    </article>
  );
}

function BackgroundTrackCard({
  projectId,
  token,
  track,
  saving,
  onPreview,
  onSaveDescription,
  onMoveToPrimary,
}: {
  projectId: string;
  token: string | undefined;
  track: ProjectTrack;
  saving: boolean;
  onPreview: (track: ProjectTrack) => void;
  onSaveDescription: (track: ProjectTrack, description: string) => void;
  onMoveToPrimary: (track: ProjectTrack) => void;
}) {
  const rawUrl = api.getTrackMediaUrl(projectId, track.id);
  const { blobUrl } = useAuthedBlobUrl(rawUrl, token);
  const [description, setDescription] = useState(track.background_description || "");

  useEffect(() => {
    setDescription(track.background_description || "");
  }, [track.background_description, track.id]);

  return (
    <article className="clipCard backgroundClipCard">
      <div className="clipMediaWrap">
        <button type="button" className="clipPreviewButton" onClick={() => onPreview(track)}>
          {blobUrl ? isImageTrack(track) ? (
            <img className="clipMedia" src={blobUrl} alt={track.filename} />
          ) : (
            <video className="clipMedia" src={blobUrl} preload="metadata" playsInline muted />
          ) : (
            <div className="clipMedia clipMediaLoading" />
          )}
          <span className="clipPreviewPlay">{isImageTrack(track) ? "Preview" : "▶ Preview"}</span>
        </button>
      </div>
      <div className="clipMeta">
        <div className="stack" style={{ gap: 4 }}>
          <strong className="clipFileName" title={track.filename}>{track.filename}</strong>
          <span className="badge">Background asset</span>
          <span className="muted">Describe what this clip shows so the worker can place it correctly.</span>
        </div>
        <textarea
          className="backgroundDescriptionInput"
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Example: quick workout set with push-ups and dumbbells in the gym"
        />
        {!description.trim() ? (
          <div className="notice">Add a short description before this clip can be used in automatic background placement.</div>
        ) : null}
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn secondary"
            disabled={saving}
            onClick={() => onSaveDescription(track, description)}
          >
            {saving ? "Saving..." : "Save Description"}
          </button>
          <button
            type="button"
            className="btn secondary"
            disabled={saving}
            onClick={() => onMoveToPrimary(track)}
          >
            Move to clips
          </button>
        </div>
      </div>
    </article>
  );
}

function BackgroundUploadCard({
  uploading,
  dropActive,
  onClick,
}: {
  uploading: boolean;
  dropActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`clipCard backgroundUploadCard ${dropActive ? "backgroundUploadCardActive" : ""}`}
      onClick={onClick}
    >
      <div className="backgroundUploadCardBody">
        <strong>Upload background assets</strong>
        <span className="backgroundUploadCardHint">
          {uploading ? "Uploading..." : "Drag here or click to upload"}
        </span>
      </div>
    </button>
  );
}

function VisualOverlayPreview({
  projectId,
  trackId,
  token,
  track,
  part,
  partIndex,
  progress,
}: {
  projectId: string;
  trackId: string;
  token: string | undefined;
  track: ProjectTrack;
  part: VisualPlanPart;
  partIndex: number;
  progress: number;
}) {
  const shouldLoadImage = part.visual_type === "web_image" && part.asset_status === "ready";
  const imageUrl = shouldLoadImage ? api.getTrackVisualAssetUrl(projectId, trackId, partIndex) : "";
  const { blobUrl } = useAuthedBlobUrl(imageUrl, shouldLoadImage ? token : undefined);
  const intro = easeOutCubic(clamp(progress / 0.22, 0, 1));
  const outro = easeOutCubic(clamp((1 - progress) / 0.18, 0, 1));
  const envelope = Math.min(intro, outro);

  if (part.visual_type === "web_image" && blobUrl) {
    return (
      <div
        className={`visualOverlay visualOverlayImage ${track.orientation === "vertical" ? "visualOverlayVertical" : ""}`}
        style={{
          opacity: clamp(envelope, 0, 1),
          transform: `translateY(${(1 - envelope) * 24}px) scale(${(0.94 + envelope * 0.06).toFixed(4)})`,
        }}
      >
        <img src={blobUrl} alt={part.prompt} className="visualOverlayAsset" />
        <div className="visualOverlayCaption">{part.text || part.prompt}</div>
      </div>
    );
  }

  const spec = buildAnimationSpec(part, partIndex);
  const cardLift = (1 - envelope) * 28;
  const titleProgress = easeOutCubic(clamp((progress - 0.04) / 0.28, 0, 1));
  const chipProgresses = spec.chips.map((_, index) => easeOutCubic(clamp((progress - 0.18 - index * 0.08) / 0.24, 0, 1)));

  return (
    <div
      className={`visualOverlay visualOverlayAnimation ${track.orientation === "vertical" ? "visualOverlayVertical" : ""} visualOverlayAnimation${spec.align === "right" ? "Right" : "Left"}`}
      style={{
        opacity: clamp(envelope, 0, 1),
      }}
    >
      <div
        className={`visualOverlayFloat visualOverlayFloat${spec.theme.charAt(0).toUpperCase()}${spec.theme.slice(1)} visualOverlayLayout${spec.layout.charAt(0).toUpperCase()}${spec.layout.slice(1)}`}
        style={{
          transform: `translateY(${cardLift}px) scale(${(0.92 + envelope * 0.08).toFixed(4)})`,
        }}
      >
        <AnimatedMotif spec={spec} progress={progress} />
        <div className="visualOverlayLabels">
          <div
            className="visualOverlayKicker"
            style={{
              opacity: titleProgress,
              transform: `translateY(${(1 - titleProgress) * 14}px)`,
            }}
          >
            {spec.kicker}
          </div>
          <div
            className="visualOverlayPrompt"
            style={{
              opacity: titleProgress,
              transform: `translateY(${(1 - titleProgress) * 12}px)`,
            }}
          >
            {spec.headline}
          </div>
          <div className="visualOverlayChipRow">
            {spec.chips.map((chip, index) => (
              <span
                key={`${chip}-${index}`}
                className="visualOverlayChip"
                style={{
                  opacity: chipProgresses[index],
                  transform: `translateY(${(1 - chipProgresses[index]) * 10}px) scale(${(0.92 + chipProgresses[index] * 0.08).toFixed(4)})`,
                }}
              >
                {chip}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

type ResolvedVisualPreviewPart = {
  part: VisualPlanPart;
  index: number;
  start: number;
  end: number;
  duration: number;
};

function SpeechFilterEditor({
  src,
  track,
  artifact,
  visualPlan,
  selectedVersionId,
  projectId,
  token,
  saving,
  saveError,
  onSave,
  onCutsChange,
  onBackgroundMusicChange,
}: {
  src: string;
  track: ProjectTrack;
  artifact: SpeechFilterArtifact;
  visualPlan?: VisualPlanArtifact | null;
  selectedVersionId: string;
  projectId: string;
  token: string | undefined;
  saving: boolean;
  saveError?: string | null;
  onSave: (cuts: SpeechFilterCut[]) => Promise<void>;
  onCutsChange: (cuts: SpeechFilterCut[]) => void;
  onBackgroundMusicChange: (settings: BackgroundMusicSettings) => Promise<void>;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const visualSfxAudioCacheRef = useRef<Partial<Record<VisualSfxKind, HTMLAudioElement>>>({});
  const backgroundMusicAudioCacheRef = useRef<Partial<Record<BackgroundMusicPreset, HTMLAudioElement>>>({});
  const lastVisualSfxIndexRef = useRef<number>(-1);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [mediaDuration, setMediaDuration] = useState<number | null>(null);
  const [editableCuts, setEditableCuts] = useState<SpeechFilterCut[]>(() => normalizeEditableCuts(artifact.cuts));
  const [selectedCutIndex, setSelectedCutIndex] = useState(0);
  const [dragState, setDragState] = useState<{ cutIndex: number; edge: "start" | "end" } | null>(null);
  const [zoomPreviewEnabled, setZoomPreviewEnabled] = useState(true);
  const [visualSfxEnabled, setVisualSfxEnabled] = useState(true);
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(true);
  const [backgroundMusic, setBackgroundMusic] = useState<BackgroundMusicSettings>(() => getTrackBackgroundMusic(track));
  const skipInFlightRef = useRef(false);
  const animationFrameRef = useRef<number | null>(null);
  const showEditablePreview = selectedVersionId === "source";
  const duration = Math.max((showEditablePreview ? track.duration : mediaDuration || track.duration) || 0, 0.1);
  const stageAspectRatio = useMemo(() => {
    if ((track.width || 0) > 0 && (track.height || 0) > 0) {
      return `${track.width} / ${track.height}`;
    }
    if (track.orientation === "vertical") return "9 / 16";
    if (track.orientation === "square") return "1 / 1";
    return "16 / 9";
  }, [track.height, track.orientation, track.width]);
  const zoomBeats = useMemo(() => {
    if (artifact.zoom_beats?.length) {
      return artifact.zoom_beats.map((beat, index) => ({
        ...beat,
        id: `zoom-${index + 1}`,
        label: beat.text.length > 88 ? `${beat.text.slice(0, 85).trim()}...` : beat.text,
      }));
    }
    return buildZoomPreviewBeats(track);
  }, [artifact.zoom_beats, track]);
  const resolvedVisualParts = useMemo<ResolvedVisualPreviewPart[]>(() => {
    if (!visualPlan?.parts?.length) return [];
    const transcriptWords = getNormalizedTranscriptWords(track);
    const aligned = visualPlan.parts
      .map((part, index) => {
        const timing = resolveVisualPartTiming(part, transcriptWords);
        return {
          part,
          index,
          start: timing.start,
          end: timing.end,
          duration: timing.duration,
        };
      })
      .filter((part) => part.duration > 0.12);
    return enforceMinimumVisualDuration(aligned, duration);
  }, [duration, track, visualPlan?.parts]);
  const activeZoomBeat = useMemo(() => {
    if (!zoomPreviewEnabled) return null;
    return zoomBeats.find((beat) => beat.enabled && currentTime >= beat.start && currentTime <= beat.end) || null;
  }, [currentTime, zoomBeats, zoomPreviewEnabled]);
  const videoTransform = useMemo(() => {
    if (!activeZoomBeat) return "scale(1)";
    const progress = clamp((currentTime - activeZoomBeat.start) / Math.max(activeZoomBeat.duration, 0.001), 0, 1);
    const eased = easeOutCubic(progress);
    const scale = 1 + (activeZoomBeat.scale - 1) * eased;
    return `scale(${scale.toFixed(4)})`;
  }, [activeZoomBeat, currentTime]);

  useEffect(() => {
    setEditableCuts(normalizeEditableCuts(artifact.cuts));
    setSelectedCutIndex(0);
  }, [artifact]);

  useEffect(() => {
    setBackgroundMusic(getTrackBackgroundMusic(track));
  }, [track]);

  useEffect(() => {
    return () => {
      Object.values(backgroundMusicAudioCacheRef.current).forEach((audio) => audio?.pause());
    };
  }, []);

  useEffect(() => {
    onCutsChange(editableCuts);
  }, [editableCuts, onCutsChange]);

  useEffect(() => {
    setZoomPreviewEnabled(zoomBeats.some((beat) => beat.enabled));
  }, [zoomBeats]);

  useEffect(() => {
    lastVisualSfxIndexRef.current = -1;
  }, [track.id, selectedVersionId]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const syncTime = () => setCurrentTime(video.currentTime || 0);
    const syncDuration = () => setMediaDuration(Number.isFinite(video.duration) ? video.duration : null);
    const tick = () => {
      let nextTime = video.currentTime || 0;
      if (showEditablePreview && !dragState && !skipInFlightRef.current) {
        const targetTime = resolvePlayableTime(nextTime, editableCuts, duration);
        if (Math.abs(targetTime - nextTime) >= 0.005) {
          skipInFlightRef.current = true;
          video.currentTime = targetTime;
          nextTime = targetTime;
          window.setTimeout(() => {
            skipInFlightRef.current = false;
          }, 0);
        }
      }
      setCurrentTime(nextTime);
      if (!video.paused && !video.ended) {
        animationFrameRef.current = window.requestAnimationFrame(tick);
      } else {
        animationFrameRef.current = null;
      }
    };
    const startTick = () => {
      if (animationFrameRef.current !== null) return;
      animationFrameRef.current = window.requestAnimationFrame(tick);
    };
    const stopTick = () => {
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
    };
    const syncPlay = () => setIsPlaying(true);
    const syncPause = () => setIsPlaying(false);

    video.addEventListener("timeupdate", syncTime);
    video.addEventListener("play", syncPlay);
    video.addEventListener("play", startTick);
    video.addEventListener("pause", syncPause);
    video.addEventListener("pause", stopTick);
    video.addEventListener("ended", stopTick);
    video.addEventListener("loadedmetadata", syncTime);
    video.addEventListener("loadedmetadata", syncDuration);
    return () => {
      stopTick();
      video.removeEventListener("timeupdate", syncTime);
      video.removeEventListener("play", syncPlay);
      video.removeEventListener("play", startTick);
      video.removeEventListener("pause", syncPause);
      video.removeEventListener("pause", stopTick);
      video.removeEventListener("ended", stopTick);
      video.removeEventListener("loadedmetadata", syncTime);
      video.removeEventListener("loadedmetadata", syncDuration);
    };
  }, [dragState, duration, editableCuts, showEditablePreview]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const enforcePlayablePosition = () => {
      if (!showEditablePreview || dragState || skipInFlightRef.current) return;
      const current = video.currentTime || 0;
      const targetTime = resolvePlayableTime(current, editableCuts, duration);
      if (Math.abs(targetTime - current) < 0.005) return;

      skipInFlightRef.current = true;
      video.currentTime = targetTime;
      setCurrentTime(targetTime);
      window.setTimeout(() => {
        skipInFlightRef.current = false;
      }, 0);
    };

    video.addEventListener("play", enforcePlayablePosition);
    video.addEventListener("seeked", enforcePlayablePosition);
    video.addEventListener("loadedmetadata", enforcePlayablePosition);
    return () => {
      video.removeEventListener("play", enforcePlayablePosition);
      video.removeEventListener("seeked", enforcePlayablePosition);
      video.removeEventListener("loadedmetadata", enforcePlayablePosition);
    };
  }, [dragState, duration, editableCuts, showEditablePreview]);

  useEffect(() => {
    const bgAudio = backgroundMusic.enabled ? ensureBackgroundMusicAudio(backgroundMusic.preset, backgroundMusicAudioCacheRef.current) : null;
    if (bgAudio) {
      bgAudio.volume = clamp(backgroundMusic.volume, 0, 1);
      const syncedTime = duration > 0 ? currentTime % Math.max(0.1, bgAudio.duration || 8) : 0;
      if (Math.abs((bgAudio.currentTime || 0) - syncedTime) > 1.5) {
        bgAudio.currentTime = syncedTime;
      }
      if (showEditablePreview && isPlaying) {
        bgAudio.play().catch(() => undefined);
      } else {
        bgAudio.pause();
      }
    }
    Object.entries(backgroundMusicAudioCacheRef.current).forEach(([key, audio]) => {
      if (!audio) return;
      if (key !== backgroundMusic.preset || !backgroundMusic.enabled || !showEditablePreview || !isPlaying) {
        audio.pause();
      }
    });
  }, [backgroundMusic, currentTime, duration, isPlaying, showEditablePreview]);

  useEffect(() => {
    if (!dragState) return;

    const handlePointerMove = (event: PointerEvent) => {
      const rect = timelineRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0) return;

      const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      const nextTime = roundToMillis(ratio * duration);
      setEditableCuts((current) => {
        const next = current.map((cut) => ({ ...cut }));
        const cut = next[dragState.cutIndex];
        if (!cut) return current;

        if (dragState.edge === "start") {
          const previousEnd = dragState.cutIndex > 0 ? next[dragState.cutIndex - 1].end : 0;
          cut.start = roundToMillis(clamp(nextTime, previousEnd, cut.end - 0.08));
        } else {
          const nextStart = dragState.cutIndex < next.length - 1 ? next[dragState.cutIndex + 1].start : duration;
          cut.end = roundToMillis(clamp(nextTime, cut.start + 0.08, nextStart));
        }
        cut.duration = roundToMillis(cut.end - cut.start);
        return next;
      });
    };

    const handlePointerUp = () => setDragState(null);

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    document.body.style.cursor = "ew-resize";
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      document.body.style.cursor = "";
    };
  }, [dragState, duration]);

  const activeCut = showEditablePreview ? findCutAtTime(editableCuts, currentTime) : null;
  const activeVisualCue = showEditablePreview
    ? resolvedVisualParts.find((part) => currentTime >= part.start && currentTime <= part.end) || null
    : null;
  const activeVisualPart = activeVisualCue?.part || null;
  const activeVisualPartIndex = activeVisualCue?.index ?? -1;
  const activeVisualProgress = activeVisualCue
    ? clamp(
        (currentTime - activeVisualCue.start) / Math.max(activeVisualCue.duration, 0.001),
        0,
        1,
      )
    : 0;
  const activeSubtitle = showEditablePreview && subtitlesEnabled ? buildActiveSubtitleWord(track, currentTime) : null;

  useEffect(() => {
    if (!showEditablePreview || !visualSfxEnabled || !isPlaying) {
      if (!isPlaying || !showEditablePreview || !visualSfxEnabled) {
        lastVisualSfxIndexRef.current = -1;
      }
      return;
    }
    if (!activeVisualPart || activeVisualPartIndex < 0) {
      lastVisualSfxIndexRef.current = -1;
      return;
    }
    if (lastVisualSfxIndexRef.current === activeVisualPartIndex) return;
    const spec = buildAnimationSpec(activeVisualPart, activeVisualPartIndex);
    playVisualSfx(inferVisualSfx(activeVisualPart, spec), visualSfxAudioCacheRef.current).catch(() => undefined);
    lastVisualSfxIndexRef.current = activeVisualPartIndex;
  }, [activeVisualPart, activeVisualPartIndex, isPlaying, showEditablePreview, visualSfxEnabled]);

  const seekTo = (time: number) => {
    const video = videoRef.current;
    if (!video) return;
    const nextTime = showEditablePreview
      ? resolvePlayableTime(time, editableCuts, duration)
      : clamp(time, 0, duration);
    video.currentTime = nextTime;
    setCurrentTime(video.currentTime);
  };

  const applyBackgroundMusicPreview = (settings: BackgroundMusicSettings) => {
    const activeAudio = ensureBackgroundMusicAudio(settings.preset, backgroundMusicAudioCacheRef.current);
    activeAudio.volume = clamp(settings.volume, 0, 1);
    if (showEditablePreview && isPlaying && settings.enabled) {
      activeAudio.currentTime = currentTime % Math.max(0.1, activeAudio.duration || 8);
      activeAudio.play().catch(() => undefined);
    } else {
      activeAudio.pause();
    }
    Object.entries(backgroundMusicAudioCacheRef.current).forEach(([key, audio]) => {
      if (!audio) return;
      if (key !== settings.preset) audio.pause();
    });
  };

  const persistBackgroundMusic = async (settings: BackgroundMusicSettings) => {
    setBackgroundMusic(settings);
    applyBackgroundMusicPreview(settings);
    await onBackgroundMusicChange(settings);
  };

  const togglePlayback = async () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      if (showEditablePreview && visualSfxEnabled) {
        ensureVisualSfxAudio(COMMON_TRANSITION_SFX, visualSfxAudioCacheRef.current);
      }
      if (showEditablePreview && backgroundMusic.enabled) {
        const bgAudio = ensureBackgroundMusicAudio(backgroundMusic.preset, backgroundMusicAudioCacheRef.current);
        bgAudio.volume = clamp(backgroundMusic.volume, 0, 1);
        bgAudio.currentTime = currentTime % Math.max(0.1, bgAudio.duration || 8);
        bgAudio.play().catch(() => undefined);
      }
      await video.play().catch(() => undefined);
    } else {
      video.pause();
      Object.values(backgroundMusicAudioCacheRef.current).forEach((audio) => audio?.pause());
    }
  };

  const handleTimelineClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!timelineRef.current) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    seekTo(ratio * duration);
  };

  return (
    <div className="speechEditor">
      <div className="speechEditorVideoShell">
        <div className="speechEditorStage" style={{ ["--stage-ratio" as string]: stageAspectRatio }}>
          <video
            ref={videoRef}
            className="speechEditorVideo"
            src={src}
            preload="metadata"
            playsInline
            style={{ transform: videoTransform }}
          />
          {activeCut ? (
            <div className="speechEditorVideoNotice">
              Suggested cut: {formatSpeechFilterReason(activeCut.reason)}
            </div>
          ) : null}
          {showEditablePreview && activeVisualPart && activeVisualPartIndex >= 0 ? (
            <VisualOverlayPreview
              projectId={projectId}
              trackId={track.id}
              token={token}
              track={track}
              part={activeVisualPart}
              partIndex={activeVisualPartIndex}
              progress={activeVisualProgress}
            />
          ) : null}
          {activeSubtitle ? (
            <div className="speechEditorSubtitleWrap">
              <div className="speechEditorSubtitle">{activeSubtitle}</div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="speechEditorControls">
        <button
          type="button"
          className="speechEditorIconButton"
          onClick={togglePlayback}
          aria-label={isPlaying ? "Pause preview" : "Play preview"}
          title={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button
          type="button"
          className={`btn secondary ${zoomPreviewEnabled ? "speechEditorToggleActive" : ""}`}
          onClick={() => setZoomPreviewEnabled((current) => !current)}
          disabled={!showEditablePreview || zoomBeats.length === 0}
        >
          {zoomPreviewEnabled ? "Zoom Preview On" : "Zoom Preview Off"}
        </button>
        <button
          type="button"
          className={`btn secondary ${visualSfxEnabled ? "speechEditorToggleActive" : ""}`}
          onClick={async () => {
            if (!visualSfxEnabled) {
              ensureVisualSfxAudio(COMMON_TRANSITION_SFX, visualSfxAudioCacheRef.current);
            }
            setVisualSfxEnabled((current) => !current);
            lastVisualSfxIndexRef.current = -1;
          }}
          disabled={!showEditablePreview || !visualPlan?.parts?.length}
        >
          {visualSfxEnabled ? "SFX On" : "SFX Off"}
        </button>
        <button
          type="button"
          className={`btn secondary ${backgroundMusic.enabled ? "speechEditorToggleActive" : ""}`}
          onClick={async () => {
            const next = { ...backgroundMusic, enabled: !backgroundMusic.enabled };
            await persistBackgroundMusic(next);
          }}
          disabled={!showEditablePreview}
        >
          {backgroundMusic.enabled ? "BGM On" : "BGM Off"}
        </button>
        <label className="speechEditorSelectWrap">
          <span className="muted speechEditorSelectLabel">Music</span>
          <select
            className="speechEditorSelect"
            value={backgroundMusic.preset}
            disabled={!showEditablePreview}
            onChange={async (event) => {
              const next = { ...backgroundMusic, preset: event.target.value as BackgroundMusicPreset };
              await persistBackgroundMusic(next);
            }}
          >
            {Object.entries(BACKGROUND_MUSIC_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label className="speechEditorSliderWrap">
          <span className="muted speechEditorSelectLabel">BGM {Math.round(backgroundMusic.volume * 100)}%</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            className="speechEditorSlider"
            value={backgroundMusic.volume}
            disabled={!showEditablePreview}
            onChange={(event) => {
              const nextVolume = Number(event.target.value);
              const next = {
                ...backgroundMusic,
                enabled: nextVolume > 0 ? true : backgroundMusic.enabled,
                volume: nextVolume,
              };
              setBackgroundMusic(next);
              applyBackgroundMusicPreview(next);
            }}
            onMouseUp={async (event) => {
              const nextVolume = Number((event.target as HTMLInputElement).value);
              const next = {
                ...backgroundMusic,
                enabled: nextVolume > 0 ? true : backgroundMusic.enabled,
                volume: nextVolume,
              };
              await persistBackgroundMusic(next);
            }}
            onTouchEnd={async (event) => {
              const nextVolume = Number((event.target as HTMLInputElement).value);
              const next = {
                ...backgroundMusic,
                enabled: nextVolume > 0 ? true : backgroundMusic.enabled,
                volume: nextVolume,
              };
              await persistBackgroundMusic(next);
            }}
            onBlur={async (event) => {
              const nextVolume = Number((event.target as HTMLInputElement).value);
              const next = {
                ...backgroundMusic,
                enabled: nextVolume > 0 ? true : backgroundMusic.enabled,
                volume: nextVolume,
              };
              await persistBackgroundMusic(next);
            }}
          />
        </label>
        <button
          type="button"
          className={`btn secondary ${subtitlesEnabled ? "speechEditorToggleActive" : ""}`}
          onClick={() => setSubtitlesEnabled((current) => !current)}
          disabled={!showEditablePreview || getTranscriptWords(track).length === 0}
        >
          {subtitlesEnabled ? "Subtitles On" : "Subtitles Off"}
        </button>
        <div className="speechEditorTime">
          {formatTime(currentTime)} / {formatDuration(duration)}
        </div>
        <button type="button" className="btn" disabled={saving} onClick={() => onSave(editableCuts)}>
          {saving ? "Saving..." : "Save Cuts"}
        </button>
      </div>

      {showEditablePreview ? (
      <div className="speechEditorTimelineWrap">
        <div ref={timelineRef} className="speechEditorTimeline" onClick={handleTimelineClick}>
          <div className="speechEditorTimelineBase" />
          <div className="speechEditorTimelinePlayed" style={{ width: `${(currentTime / duration) * 100}%` }} />
          {zoomBeats.map((beat) => {
            const left = (beat.start / duration) * 100;
            const width = ((beat.end - beat.start) / duration) * 100;
            return (
              <div
                key={beat.id}
                className={`speechEditorZoomBeat ${beat.enabled ? "speechEditorZoomBeatEnabled" : ""}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                title={`${formatTime(beat.start)} - ${formatTime(beat.end)} ${beat.enabled ? "center zoom" : "no zoom"}`}
              />
            );
          })}
          {editableCuts.map((cut, index) => {
            const left = (cut.start / duration) * 100;
            const width = ((cut.end - cut.start) / duration) * 100;
            const selected = index === selectedCutIndex;
            return (
              <button
                key={`${cut.start}-${cut.end}-${index}`}
                type="button"
                className={`speechEditorCut ${selected ? "speechEditorCutSelected" : ""}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedCutIndex(index);
                  seekTo(cut.start);
                }}
              >
                <span
                  className="speechEditorHandle speechEditorHandleStart"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setSelectedCutIndex(index);
                    setDragState({ cutIndex: index, edge: "start" });
                  }}
                />
                <span className="speechEditorCutLabel">{index + 1}</span>
                <span
                  className="speechEditorHandle speechEditorHandleEnd"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setSelectedCutIndex(index);
                    setDragState({ cutIndex: index, edge: "end" });
                  }}
                />
              </button>
            );
          })}
          <div className="speechEditorPlayhead" style={{ left: `${(currentTime / duration) * 100}%` }} />
        </div>
      </div>
      ) : null}

      {showEditablePreview ? (
      <div className="speechEditorCutsPanel">
        <div className="speechEditorSummary">
          <strong>{artifact.summary}</strong>
          <span className="muted">Drag the left or right edge of each red block to adjust the cut.</span>
          {zoomBeats.length > 0 ? (
            <span className="muted">
              Center zoom preview is generated from transcript beats. Blue blocks mark beats, darker blue means zoom-in.
            </span>
          ) : null}
          {saveError ? <span className="speechEditorError">{saveError}</span> : null}
        </div>
        {zoomBeats.length > 0 ? (
          <div className="speechEditorZoomList">
            {zoomBeats.map((beat) => (
              <div
                key={`${beat.id}-row`}
                className={`speechEditorZoomRow ${beat.enabled ? "speechEditorZoomRowEnabled" : ""} ${activeZoomBeat?.id === beat.id ? "speechEditorZoomRowActive" : ""}`}
              >
                <strong>{formatTime(beat.start)} - {formatTime(beat.end)}</strong>
                <span>{beat.enabled ? `Center zoom ${beat.scale.toFixed(2)}x` : "Normal framing"}</span>
                <span className="muted">{beat.label}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className="speechEditorCutList">
          {editableCuts.map((cut, index) => (
            <button
              key={`${cut.start}-${cut.end}-${index}-row`}
              type="button"
              className={`speechEditorCutRow ${index === selectedCutIndex ? "speechEditorCutRowSelected" : ""}`}
              onClick={() => {
                setSelectedCutIndex(index);
                seekTo(cut.start);
              }}
            >
              <strong>{formatTime(cut.start)} - {formatTime(cut.end)}</strong>
              <span>{formatSpeechFilterReason(cut.reason)}</span>
              {cut.transcript ? <span className="muted">“{cut.transcript}”</span> : null}
            </button>
          ))}
        </div>
      </div>
      ) : null}
    </div>
  );
}

function PreviewModal({
  projectId,
  token,
  projectCreatedAt,
  track,
  speechFilter,
  speechFilterLoading,
  speechFilterSaving,
  speechFilterError,
  visualPlan,
  visualPlanLoading,
  visualPlanError,
  renderLoading,
  renderError,
  onGenerateSpeechFilter,
  onGenerateVisualPlan,
  onSaveSpeechFilter,
  onUpdateBackgroundMusic,
  onRenderTrack,
  onClose,
}: {
  projectId: string;
  token: string | undefined;
  projectCreatedAt?: string;
  track: ProjectTrack;
  speechFilter?: SpeechFilterArtifact | null;
  speechFilterLoading: boolean;
  speechFilterSaving: boolean;
  speechFilterError?: string | null;
  visualPlan?: VisualPlanArtifact | null;
  visualPlanLoading: boolean;
  visualPlanError?: string | null;
  renderLoading: boolean;
  renderError?: string | null;
  onGenerateSpeechFilter: (track: ProjectTrack) => void;
  onGenerateVisualPlan: (track: ProjectTrack) => void;
  onSaveSpeechFilter: (track: ProjectTrack, cuts: SpeechFilterCut[]) => Promise<void>;
  onUpdateBackgroundMusic: (track: ProjectTrack, settings: BackgroundMusicSettings) => Promise<void>;
  onRenderTrack: (track: ProjectTrack, cuts: SpeechFilterCut[]) => Promise<TrackRenderVersion | null>;
  onClose: () => void;
}) {
  const renderVersions = getTrackRenderVersions(track);
  const [selectedVersionId, setSelectedVersionId] = useState<string>("source");
  const [draftCuts, setDraftCuts] = useState<SpeechFilterCut[]>(speechFilter?.cuts || []);

  useEffect(() => {
    setSelectedVersionId("source");
  }, [track.id]);

  useEffect(() => {
    setDraftCuts(speechFilter?.cuts || []);
  }, [speechFilter, track.id]);

  const selectedMediaUrl = selectedVersionId === "source"
    ? api.getTrackMediaUrl(projectId, track.id)
    : api.getTrackRenderAssetUrl(projectId, track.id, selectedVersionId);
  const { blobUrl, loading, error } = useAuthedBlobUrl(selectedMediaUrl, token);
  const selectedRenderVersion = renderVersions.find((version) => version.id === selectedVersionId) || null;

  return (
    <div className="previewModalBackdrop" onClick={onClose}>
      <div className="previewModal" onClick={(event) => event.stopPropagation()}>
        <div className="previewModalHead">
          <div className="stack" style={{ gap: 4 }}>
            <strong className="clipFileName">{track.filename}</strong>
            <span className="muted">
              {formatDate(track.recorded_at || projectCreatedAt)} • {formatDuration(track.duration)}
              {formatOrientation(track.orientation)
                ? ` • ${formatOrientation(track.orientation)}${track.width && track.height ? ` ${track.width}×${track.height}` : ""}`
                : ""}
            </span>
          </div>
          <div className="previewModalActions">
            <button
              type="button"
              className="btn"
              disabled={renderLoading || !speechFilter}
              onClick={async () => {
                if (!speechFilter) return;
                const version = await onRenderTrack(track, draftCuts);
                if (version) setSelectedVersionId(version.id);
              }}
            >
              {renderLoading ? "Rendering..." : "Render"}
            </button>
            {renderVersions.length > 0 ? (
              <>
                <label className="previewModalVersionLabel">
                  <span className="muted previewModalVersionLabelText">Version</span>
                  <select
                    className="previewModalVersionSelect"
                    value={selectedVersionId}
                    onChange={(event) => setSelectedVersionId(event.target.value)}
                  >
                    <option value="source">Original</option>
                    {renderVersions.map((version) => (
                      <option key={version.id} value={version.id}>
                        {version.label}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedVersionId !== "source" ? (
                  <a
                    className="btn secondary"
                    href={blobUrl || undefined}
                    download={selectedRenderVersion?.filename || undefined}
                  >
                    Download
                  </a>
                ) : null}
              </>
            ) : (
              <span className="muted previewModalNoVersion">No version available</span>
            )}
          </div>
          <button type="button" className="previewModalClose" onClick={onClose}>×</button>
        </div>
        {renderError ? <div className="speechEditorError">{renderError}</div> : null}
        {blobUrl ? (
          speechFilter ? (
            <SpeechFilterEditor
              src={blobUrl}
              track={track}
              artifact={speechFilter}
              visualPlan={visualPlan}
              selectedVersionId={selectedVersionId}
              projectId={projectId}
              token={token}
              saving={speechFilterSaving}
              saveError={speechFilterError}
              onSave={(cuts) => onSaveSpeechFilter(track, cuts)}
              onCutsChange={setDraftCuts}
              onBackgroundMusicChange={(settings) => onUpdateBackgroundMusic(track, settings)}
            />
          ) : isImageTrack(track) ? (
            <img
              className="previewModalVideo"
              src={blobUrl}
              alt={track.filename}
            />
          ) : (
            <video
              className="previewModalVideo"
              src={blobUrl}
              controls
              autoPlay
              preload="metadata"
              playsInline
            />
          )
        ) : (
          <div className="previewModalVideo clipMediaLoading">
            <span className="muted">{loading ? "Loading preview..." : error || "Preview unavailable"}</span>
          </div>
        )}
        <TranscriptPanel
          track={track}
          speechFilter={speechFilter}
          speechFilterLoading={speechFilterLoading}
          speechFilterError={speechFilterError}
          visualPlan={visualPlan}
          visualPlanLoading={visualPlanLoading}
          visualPlanError={visualPlanError}
          onGenerateSpeechFilter={onGenerateSpeechFilter}
          onGenerateVisualPlan={onGenerateVisualPlan}
        />
      </div>
    </div>
  );
}

function FinalVideo({ projectId, token }: { projectId: string; token: string | undefined }) {
  const { blobUrl, loading, error } = useAuthedBlobUrl(api.getOutputUrl(projectId), token);
  if (!blobUrl) {
    return (
      <div className="videoPlayerEmpty">
        <span className="muted">{loading ? "Loading final video..." : error || "Final video unavailable."}</span>
      </div>
    );
  }
  return (
    <video
      className="projectFinalVideo"
      src={blobUrl}
      controls
      preload="metadata"
      playsInline
    />
  );
}

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = String(params?.id || "");
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [addingTracks, setAddingTracks] = useState(false);
  const [groupByDay, setGroupByDay] = useState(false);
  const [deletingProject, setDeletingProject] = useState(false);
  const [hidingTrackId, setHidingTrackId] = useState<string | null>(null);
  const [activeTrack, setActiveTrack] = useState<ProjectTrack | null>(null);
  const [speechFilters, setSpeechFilters] = useState<Record<string, SpeechFilterArtifact | null>>({});
  const [speechFilterLoadingIds, setSpeechFilterLoadingIds] = useState<Record<string, boolean>>({});
  const [speechFilterSavingIds, setSpeechFilterSavingIds] = useState<Record<string, boolean>>({});
  const [speechFilterErrors, setSpeechFilterErrors] = useState<Record<string, string | null>>({});
  const [visualPlans, setVisualPlans] = useState<Record<string, VisualPlanArtifact | null>>({});
  const [visualPlanLoadingIds, setVisualPlanLoadingIds] = useState<Record<string, boolean>>({});
  const [visualPlanErrors, setVisualPlanErrors] = useState<Record<string, string | null>>({});
  const [renderLoadingIds, setRenderLoadingIds] = useState<Record<string, boolean>>({});
  const [renderErrors, setRenderErrors] = useState<Record<string, string | null>>({});
  const [backgroundTrackSavingIds, setBackgroundTrackSavingIds] = useState<Record<string, boolean>>({});
  const [backgroundPlan, setBackgroundPlan] = useState<BackgroundVideoPlanArtifact | null>(null);
  const [backgroundPlanLoading, setBackgroundPlanLoading] = useState(false);
  const [token, setToken] = useState<string | undefined>(undefined);
  const [draggingTrackId, setDraggingTrackId] = useState<string | null>(null);
  const [dragTargetTrackId, setDragTargetTrackId] = useState<string | null>(null);
  const [reorderingTracks, setReorderingTracks] = useState(false);
  const [backgroundUploading, setBackgroundUploading] = useState(false);
  const [backgroundLibraryDropActive, setBackgroundLibraryDropActive] = useState(false);
  const requestedTranscriptBackfill = useRef(false);
  const addTracksInputRef = useRef<HTMLInputElement>(null);
  const backgroundUploadInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.replace(`/login?next=/projects/${projectId}`);
      return;
    }
    setToken(session.access_token);
    if (!projectId) return;
    setLastProjectId(projectId);

    api.getProject(projectId, session.access_token)
      .then((response) => {
        setProject(response.project);
        setMessage(null);
      })
      .catch((error) => {
        const text = String((error as Error).message || error);
        setMessage(text);
        if (text.toLowerCase().includes("unauthorized")) {
          router.replace(`/login?next=/projects/${projectId}`);
        }
      })
      .finally(() => setLoading(false));
  }, [projectId, router]);

  useEffect(() => {
    requestedTranscriptBackfill.current = false;
    setSpeechFilters({});
    setSpeechFilterLoadingIds({});
    setSpeechFilterSavingIds({});
    setSpeechFilterErrors({});
    setVisualPlans({});
    setVisualPlanLoadingIds({});
    setVisualPlanErrors({});
    setRenderLoadingIds({});
    setRenderErrors({});
    setBackgroundTrackSavingIds({});
    setBackgroundPlan(null);
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !token || !project || !shouldRequestTranscriptBackfill(project)) return;
    if (requestedTranscriptBackfill.current) return;

    requestedTranscriptBackfill.current = true;
    api.requestMissingTranscripts(projectId, token)
      .then((response) => {
        setProject(response.project);
        setMessage(null);
      })
      .catch((error) => {
        requestedTranscriptBackfill.current = false;
        setMessage(normalizeTranscriptBackfillError(error));
      });
  }, [project, projectId, token]);

  useEffect(() => {
    if (!projectId || !token || !projectHasProcessingTranscripts(project)) return;

    const intervalId = window.setInterval(() => {
      api.getProject(projectId, token)
        .then((response) => {
          setProject(response.project);
          setMessage(null);
        })
        .catch((error) => setMessage(String((error as Error).message || error)));
    }, 3000);

    return () => window.clearInterval(intervalId);
  }, [project, projectId, token]);

  useEffect(() => {
    if (!activeTrack || !project || !token) return;
    if (speechFilters[activeTrack.id]) return;
    if (getSpeechFilterStatus(activeTrack) !== "completed") return;
    if (speechFilterLoadingIds[activeTrack.id]) return;
    if (speechFilterErrors[activeTrack.id]) return;

    setSpeechFilterLoadingIds((current) => ({ ...current, [activeTrack.id]: true }));
    api.getTrackSpeechFilter(project.id, activeTrack.id, token)
      .then((artifact) => {
        setSpeechFilters((current) => ({ ...current, [activeTrack.id]: artifact }));
      })
      .catch((error) => {
        setSpeechFilterErrors((current) => ({ ...current, [activeTrack.id]: String((error as Error).message || error) }));
      })
      .finally(() => {
        setSpeechFilterLoadingIds((current) => ({ ...current, [activeTrack.id]: false }));
      });
  }, [activeTrack, project, speechFilters, speechFilterErrors, speechFilterLoadingIds, token]);

  useEffect(() => {
    if (!activeTrack || !project || !token) return;
    if (visualPlans[activeTrack.id]) return;
    if (getVisualWorkerStatus(activeTrack) !== "completed") return;
    if (visualPlanLoadingIds[activeTrack.id]) return;
    if (visualPlanErrors[activeTrack.id]) return;

    setVisualPlanLoadingIds((current) => ({ ...current, [activeTrack.id]: true }));
    api.getTrackVisualPlan(project.id, activeTrack.id, token)
      .then((artifact) => {
        setVisualPlans((current) => ({ ...current, [activeTrack.id]: artifact }));
      })
      .catch((error) => {
        setVisualPlanErrors((current) => ({ ...current, [activeTrack.id]: String((error as Error).message || error) }));
      })
      .finally(() => {
        setVisualPlanLoadingIds((current) => ({ ...current, [activeTrack.id]: false }));
      });
  }, [activeTrack, project, token, visualPlans, visualPlanLoadingIds, visualPlanErrors]);

  const uploadFilesToProject = async (
    selectedFiles: File[],
    mode: "primary" | "background",
  ) => {
    if (selectedFiles.length === 0 || !token || !project) return;
    const duplicateFiles = selectedFiles.filter((file) => isDuplicateSelectedFile(file, project.tracks));
    const nextFiles = selectedFiles.filter((file) => !isDuplicateSelectedFile(file, project.tracks));
    if (nextFiles.length === 0) {
      setMessage(mode === "background" ? "All selected background assets are already in this project." : "All selected clips are already in this project.");
      return;
    }

    try {
      if (mode === "background") {
        setBackgroundUploading(true);
      } else {
        setAddingTracks(true);
      }
      setMessage(
        duplicateFiles.length
          ? `${duplicateFiles.length} duplicate ${mode === "background" ? "asset" : "clip"}${duplicateFiles.length === 1 ? "" : "s"} skipped.`
          : null
      );
      const response = await api.addTracksToProject(
        projectId,
        nextFiles,
        token,
        mode === "background"
          ? { metadata: nextFiles.map(() => ({ role: "background" })) }
          : undefined,
      );
      requestedTranscriptBackfill.current = false;
      setProject(response.project);
      if (response.message) {
        setMessage(response.message);
      }
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      if (mode === "background") {
        setBackgroundUploading(false);
      } else {
        setAddingTracks(false);
      }
    }
  };

  const handleAddTracks = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files || []);
    event.target.value = "";
    await uploadFilesToProject(selectedFiles, "primary");
  };

  const handleAddBackgroundAssets = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files || []);
    event.target.value = "";
    await uploadFilesToProject(selectedFiles, "background");
  };

  const handleDeleteProject = async () => {
    if (!token || !project || deletingProject) return;
    const confirmed = window.confirm(`Delete project "${project.name}"? This cannot be undone.`);
    if (!confirmed) return;
    setDeletingProject(true);
    setMessage(null);
    try {
      await api.deleteProject(project.id, token);
      router.push("/projects");
    } catch (error) {
      setMessage(String((error as Error).message || error));
      setDeletingProject(false);
    }
  };

  const handleHideTrack = async (trackId: string) => {
    if (!token || !project) return;
    try {
      setHidingTrackId(trackId);
      setMessage(null);
      await api.excludeTrack(project.id, trackId, token);
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      if (activeTrack?.id === trackId) {
        setActiveTrack(null);
      }
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setHidingTrackId(null);
    }
  };

  const handleMarkAsBackground = async (track: ProjectTrack, description?: string | null) => {
    if (!token || !project) return;
    const trimmed = String(description || "").trim() || null;
    setBackgroundTrackSavingIds((current) => ({ ...current, [track.id]: true }));
    try {
      const response = await api.updateTrackBackgroundVideo(
        project.id,
        track.id,
        { role: "background", background_description: trimmed },
        token,
      );
      setProject(response.project);
      setMessage("Clip moved to background library.");
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setBackgroundTrackSavingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleSaveBackgroundDescription = async (track: ProjectTrack, description: string) => {
    if (!token || !project) return;
    const trimmed = description.trim();
    if (!trimmed) {
      setMessage("Background clips need a short description.");
      return;
    }
    setBackgroundTrackSavingIds((current) => ({ ...current, [track.id]: true }));
    try {
      const response = await api.updateTrackBackgroundVideo(
        project.id,
        track.id,
        { role: "background", background_description: trimmed },
        token,
      );
      setProject(response.project);
      setMessage("Background description saved.");
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setBackgroundTrackSavingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleMoveToPrimary = async (track: ProjectTrack) => {
    if (!token || !project) return;
    setBackgroundTrackSavingIds((current) => ({ ...current, [track.id]: true }));
    try {
      const response = await api.updateTrackBackgroundVideo(
        project.id,
        track.id,
        { role: "primary", background_description: null },
        token,
      );
      setProject(response.project);
      setMessage("Clip moved back to editable clips.");
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setBackgroundTrackSavingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleGenerateBackgroundPlan = async () => {
    if (!token || !project) return;
    setBackgroundPlanLoading(true);
    try {
      const artifact = await api.generateBackgroundVideoPlan(project.id, token);
      setBackgroundPlan(artifact);
      const refreshed = await api.getProject(project.id, token);
      setProject(refreshed.project);
      setMessage(artifact.summary);
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setBackgroundPlanLoading(false);
    }
  };

  const persistTrackOrder = async (orderedVisibleTracks: ProjectTrack[]) => {
    if (!token || !project) return;
    setReorderingTracks(true);
    try {
      const hiddenTracks = (project.tracks || []).filter((track) => isTrackHidden(track));
      const orderedTrackIds = [...orderedVisibleTracks, ...hiddenTracks].map((track) => track.id);
      const response = await api.reorderProjectTracks(
        project.id,
        orderedTrackIds,
        token,
      );
      setProject(response.project);
      setMessage(null);
    } catch (error) {
      const text = String((error as Error).message || error);
      setMessage(text);
      const refreshed = await api.getProject(project.id, token).catch(() => null);
      if (refreshed) setProject(refreshed.project);
    } finally {
      setReorderingTracks(false);
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
    }
  };

  const handleDragStart = (track: ProjectTrack) => {
    if (reorderingTracks) return;
    setDraggingTrackId(track.id);
    setDragTargetTrackId(track.id);
  };

  const handleDragOver = (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => {
    if (!draggingTrackId || draggingTrackId === track.id || groupByDay) return;
    event.preventDefault();
    setDragTargetTrackId(track.id);
  };

  const handleDrop = async (track: ProjectTrack, event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    if (!draggingTrackId || draggingTrackId === track.id || !project || groupByDay) {
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
      return;
    }

    const visibleTracks = sortTracksForProject((project.tracks || []).filter((item) => !isTrackHidden(item)));
    const fromIndex = visibleTracks.findIndex((item) => item.id === draggingTrackId);
    const toIndex = visibleTracks.findIndex((item) => item.id === track.id);
    if (fromIndex < 0 || toIndex < 0) {
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
      return;
    }

    const nextVisible = [...visibleTracks];
    const [moved] = nextVisible.splice(fromIndex, 1);
    nextVisible.splice(toIndex, 0, moved);

    setProject((current) => {
      if (!current) return current;
      const hiddenTracks = current.tracks.filter((item) => isTrackHidden(item));
      return {
        ...current,
        tracks: [...nextVisible, ...hiddenTracks].map((item, index) => ({ ...item, position: index })),
      };
    });
    await persistTrackOrder(nextVisible);
  };

  const handleBackgroundLibraryDragOver = (event: React.DragEvent<HTMLElement>) => {
    const hasFiles = Array.from(event.dataTransfer?.types || []).includes("Files");
    if (!draggingTrackId && !hasFiles) return;
    event.preventDefault();
    setBackgroundLibraryDropActive(true);
  };

  const handleBackgroundLibraryDragLeave = (event: React.DragEvent<HTMLElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    setBackgroundLibraryDropActive(false);
  };

  const handleBackgroundLibraryDrop = async (event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    setBackgroundLibraryDropActive(false);

    const droppedFiles = Array.from(event.dataTransfer?.files || []);
    if (droppedFiles.length > 0) {
      await uploadFilesToProject(droppedFiles, "background");
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
      return;
    }

    if (!draggingTrackId || !project) {
      setDraggingTrackId(null);
      setDragTargetTrackId(null);
      return;
    }

    const track = project.tracks.find((item) => item.id === draggingTrackId);
    setDraggingTrackId(null);
    setDragTargetTrackId(null);
    if (!track || isBackgroundTrack(track)) return;
    await handleMarkAsBackground(track, track.background_description || null);
  };

  const handleGenerateSpeechFilter = async (track: ProjectTrack) => {
    if (!token || !project) return;

    setSpeechFilterLoadingIds((current) => ({ ...current, [track.id]: true }));
    setSpeechFilterErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const existingStatus = getSpeechFilterStatus(track);
      let artifact: SpeechFilterArtifact;
      if (existingStatus === "completed" && !speechFilters[track.id]) {
        try {
          artifact = await api.getTrackSpeechFilter(project.id, track.id, token);
        } catch (error) {
          const text = String((error as Error).message || error).toLowerCase();
          if (!text.includes("not found")) {
            throw error;
          }
          artifact = await api.generateTrackSpeechFilter(project.id, track.id, token);
        }
      } else {
        artifact = await api.generateTrackSpeechFilter(project.id, track.id, token);
      }
      setSpeechFilters((current) => ({ ...current, [track.id]: artifact }));
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setMessage(null);
    } catch (error) {
      const text = String((error as Error).message || error);
      setSpeechFilterErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
    } finally {
      setSpeechFilterLoadingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleGenerateVisualPlan = async (track: ProjectTrack) => {
    if (!token || !project) return;

    setVisualPlanLoadingIds((current) => ({ ...current, [track.id]: true }));
    setVisualPlanErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const existingStatus = getVisualWorkerStatus(track);
      let artifact: VisualPlanArtifact;
      if (existingStatus === "completed" && !visualPlans[track.id]) {
        try {
          artifact = await api.getTrackVisualPlan(project.id, track.id, token);
        } catch (error) {
          const text = String((error as Error).message || error).toLowerCase();
          if (!text.includes("not found")) throw error;
          artifact = await api.generateTrackVisualPlan(project.id, track.id, token);
        }
      } else {
        artifact = await api.generateTrackVisualPlan(project.id, track.id, token);
      }
      setVisualPlans((current) => ({ ...current, [track.id]: artifact }));
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setMessage(null);
    } catch (error) {
      const text = String((error as Error).message || error);
      setVisualPlanErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
    } finally {
      setVisualPlanLoadingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleSaveSpeechFilter = async (track: ProjectTrack, cuts: SpeechFilterCut[]) => {
    if (!token || !project) return;
    setSpeechFilterSavingIds((current) => ({ ...current, [track.id]: true }));
    setSpeechFilterErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const artifact = await api.updateTrackSpeechFilter(project.id, track.id, cuts, token);
      setSpeechFilters((current) => ({ ...current, [track.id]: artifact }));
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setMessage("Speech cuts saved.");
    } catch (error) {
      const text = String((error as Error).message || error);
      setSpeechFilterErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
    } finally {
      setSpeechFilterSavingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const handleUpdateBackgroundMusic = async (track: ProjectTrack, settings: BackgroundMusicSettings) => {
    if (!token || !project) return;
    try {
      const response = await api.updateTrackBackgroundMusic(project.id, track.id, settings, token);
      setProject(response.project);
      setActiveTrack(response.project.tracks.find((item) => item.id === track.id) || null);
      setMessage(null);
    } catch (error) {
      setMessage(String((error as Error).message || error));
    }
  };

  const handleRenderTrack = async (track: ProjectTrack, cuts: SpeechFilterCut[]): Promise<TrackRenderVersion | null> => {
    if (!token || !project) return null;
    setRenderLoadingIds((current) => ({ ...current, [track.id]: true }));
    setRenderErrors((current) => ({ ...current, [track.id]: null }));
    try {
      const result = await api.renderTrack(project.id, track.id, cuts, token);
      const response = await api.getProject(project.id, token);
      setProject(response.project);
      setActiveTrack(response.project.tracks.find((item) => item.id === track.id) || null);
      setMessage(`${result.version.label} rendered.`);
      return result.version;
    } catch (error) {
      const text = String((error as Error).message || error);
      setRenderErrors((current) => ({ ...current, [track.id]: text }));
      setMessage(text);
      return null;
    } finally {
      setRenderLoadingIds((current) => ({ ...current, [track.id]: false }));
    }
  };

  const sortedTracks = useMemo(
    () => sortTracksForProject((project?.tracks || []).filter((track) => !isTrackHidden(track))),
    [project?.tracks],
  );
  const primaryTracks = useMemo(
    () => sortedTracks.filter((track) => !isBackgroundTrack(track)),
    [sortedTracks],
  );
  const backgroundTracks = useMemo(
    () => sortedTracks.filter((track) => isBackgroundTrack(track)),
    [sortedTracks],
  );
  const activeBackgroundPlacements = backgroundPlan?.placements || project?.pipeline?.background_video_suggestions || [];
  const dayGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; tracks: ProjectTrack[] }> = [];
    for (const track of primaryTracks) {
      const sourceTime = track.recorded_at || project?.created_at || null;
      const key = getDayGroupKey(sourceTime);
      const label = formatDayLabel(sourceTime);
      const existing = groups[groups.length - 1];
      if (existing && existing.key === key) {
        existing.tracks.push(track);
      } else {
        groups.push({ key, label, tracks: [track] });
      }
    }
    return groups;
  }, [primaryTracks, project?.created_at]);

  if (loading) {
    return (
      <section className="card">
        <p className="muted" style={{ padding: 24 }}>Loading project...</p>
      </section>
    );
  }

  if (!project) {
    return (
      <section className="card stack">
        <h1>Project</h1>
        <div className="notice">{message || "Project not found."}</div>
      </section>
    );
  }

  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card stack" style={{ gap: 16 }}>
        <div className="projectOverviewHead">
          <div className="stack" style={{ gap: 6 }}>
            <h1>{project.name}</h1>
            <span className="muted">Created {formatDate(project.created_at)}</span>
          </div>
          <div className="projectOverviewActions">
            <span className="badge">{project.status}</span>
            <button
              type="button"
              className="btn secondary dangerButton"
              onClick={handleDeleteProject}
              disabled={deletingProject}
            >
              {deletingProject ? "Deleting..." : "Delete Project"}
            </button>
          </div>
        </div>

        <div className="projectFinalPreview">
          <div className="stack" style={{ gap: 6 }}>
            <h2>Final Version</h2>
            <span className="muted">Play the latest rendered version of the project.</span>
          </div>
          {project.status === "completed" && project.output_path ? (
            <FinalVideo projectId={project.id} token={token} />
          ) : project.status === "error" ? (
            <div className="videoPlayerEmpty">
              <span className="muted">{project.error_message || "Rendering failed."}</span>
            </div>
          ) : (
            <div className="videoPlayerEmpty">
              <span className="muted">Final video is not ready yet.</span>
            </div>
          )}
        </div>
      </section>

      {message ? <div className="notice">{message}</div> : null}

      <section className="card stack" style={{ gap: 16 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div className="stack" style={{ gap: 4 }}>
            <h2>Clips ({primaryTracks.length})</h2>
            <span className="muted">
              Ordered chronologically from the earliest clip to the latest.
            </span>
            <div className="clipViewControls">
              <button
                type="button"
                className={`clipViewToggle ${groupByDay ? "clipViewToggleActive" : ""}`}
                onClick={() => setGroupByDay((current) => !current)}
              >
                {groupByDay ? "Grouped by day" : "Split by day"}
              </button>
              {!groupByDay ? (
                <span className="muted">{reorderingTracks ? "Saving clip order..." : "Drag clips to reorder"}</span>
              ) : null}
            </div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <input
              ref={addTracksInputRef}
              type="file"
              accept="video/*"
              multiple
              hidden
              onChange={handleAddTracks}
            />
            <button
              type="button"
              className="btn secondary"
              disabled={addingTracks}
              onClick={() => addTracksInputRef.current?.click()}
            >
              {addingTracks ? "Adding..." : "Add Clips"}
            </button>
          </div>
        </div>

        {primaryTracks.length === 0 ? (
          <div className="notice">No clips in this project yet.</div>
        ) : groupByDay ? (
          <div className="clipDayGroups">
            {dayGroups.map((group) => (
              <section key={group.key} className="clipDaySection">
                <div className="clipDayHeader">
                  <strong>{group.label}</strong>
                  <span className="muted">{group.tracks.length} clip{group.tracks.length === 1 ? "" : "s"}</span>
                </div>
                <div className="clipGrid">
                  {group.tracks.map((track) => (
                    <ClipCard
                      key={track.id}
                      projectId={project.id}
                      projectCreatedAt={project.created_at}
                      token={token}
                      track={track}
                      speechFilter={speechFilters[track.id]}
                      speechFilterLoading={Boolean(speechFilterLoadingIds[track.id])}
                      speechFilterError={speechFilterErrors[track.id]}
                      visualPlan={visualPlans[track.id]}
                      visualPlanLoading={Boolean(visualPlanLoadingIds[track.id])}
                      visualPlanError={visualPlanErrors[track.id]}
                      onGenerateSpeechFilter={handleGenerateSpeechFilter}
                      onGenerateVisualPlan={handleGenerateVisualPlan}
                      onMarkAsBackground={handleMarkAsBackground}
                      backgroundSaving={Boolean(backgroundTrackSavingIds[track.id])}
                      onOpen={setActiveTrack}
                      onHide={handleHideTrack}
                      hiding={hidingTrackId === track.id}
                      draggable={!reorderingTracks}
                      dragActive={draggingTrackId === track.id}
                      dragTarget={dragTargetTrackId === track.id && draggingTrackId !== track.id}
                      onDragStart={handleDragStart}
                      onDragOver={handleDragOver}
                      onDrop={handleDrop}
                      onDragEnd={() => {
                        setDraggingTrackId(null);
                        setDragTargetTrackId(null);
                        setBackgroundLibraryDropActive(false);
                      }}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <div className="clipGrid">
            {sortedTracks.map((track) => (
              <ClipCard
                key={track.id}
                projectId={project.id}
                projectCreatedAt={project.created_at}
                token={token}
                track={track}
                speechFilter={speechFilters[track.id]}
                speechFilterLoading={Boolean(speechFilterLoadingIds[track.id])}
                speechFilterError={speechFilterErrors[track.id]}
                visualPlan={visualPlans[track.id]}
                visualPlanLoading={Boolean(visualPlanLoadingIds[track.id])}
                visualPlanError={visualPlanErrors[track.id]}
                onGenerateSpeechFilter={handleGenerateSpeechFilter}
                onGenerateVisualPlan={handleGenerateVisualPlan}
                onMarkAsBackground={handleMarkAsBackground}
                backgroundSaving={Boolean(backgroundTrackSavingIds[track.id])}
                onOpen={setActiveTrack}
                onHide={handleHideTrack}
                hiding={hidingTrackId === track.id}
                draggable={!reorderingTracks}
                dragActive={draggingTrackId === track.id}
                dragTarget={dragTargetTrackId === track.id && draggingTrackId !== track.id}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onDragEnd={() => {
                  setDraggingTrackId(null);
                  setDragTargetTrackId(null);
                  setBackgroundLibraryDropActive(false);
                }}
              />
            ))}
          </div>
        )}
      </section>

      <section
        className={`card stack backgroundLibrarySection ${backgroundLibraryDropActive ? "backgroundLibrarySectionActive" : ""}`}
        style={{ gap: 16 }}
        onDragOver={handleBackgroundLibraryDragOver}
        onDragLeave={handleBackgroundLibraryDragLeave}
        onDrop={handleBackgroundLibraryDrop}
      >
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div className="stack" style={{ gap: 4 }}>
            <h2>Background Library ({backgroundTracks.length})</h2>
            <span className="muted">
              These clips stay outside the main spoken timeline and are matched later as supportive background footage.
            </span>
          </div>
          <button
            type="button"
            className="btn secondary"
            disabled={backgroundPlanLoading || backgroundTracks.length === 0}
            onClick={handleGenerateBackgroundPlan}
          >
            {backgroundPlanLoading ? "Planning..." : "Suggest Background Placements"}
          </button>
        </div>

        <input
          ref={backgroundUploadInputRef}
          type="file"
          accept="video/*,image/*"
          multiple
          hidden
          onChange={handleAddBackgroundAssets}
        />
        <div className="clipGrid">
          <BackgroundUploadCard
            uploading={backgroundUploading}
            dropActive={backgroundLibraryDropActive}
            onClick={() => backgroundUploadInputRef.current?.click()}
          />
          {backgroundTracks.map((track) => (
            <BackgroundTrackCard
              key={track.id}
              projectId={project.id}
              token={token}
              track={track}
              saving={Boolean(backgroundTrackSavingIds[track.id])}
              onPreview={setActiveTrack}
              onSaveDescription={handleSaveBackgroundDescription}
              onMoveToPrimary={handleMoveToPrimary}
            />
          ))}
        </div>
        <div className="notice">
          Videos with no usable transcript are auto-marked here. You can also drag clips from the main grid into this section or upload dedicated background video and image assets directly here.
        </div>

        {activeBackgroundPlacements.length ? (
          <div className="backgroundPlacementList">
            {activeBackgroundPlacements.map((placement, index) => (
              <div key={`${placement.track_id}-${placement.start}-${index}`} className="backgroundPlacementItem">
                <strong>{placement.filename}</strong>
                <span className="muted">
                  {formatDuration(placement.start)} - {formatDuration(placement.end)} • {placement.description}
                </span>
                <span>{placement.transcript_excerpt}</span>
                {placement.rationale ? <span className="muted">{placement.rationale}</span> : null}
              </div>
            ))}
          </div>
        ) : null}
      </section>

      {activeTrack ? (
        <PreviewModal
          projectId={project.id}
          token={token}
          projectCreatedAt={project.created_at}
          track={activeTrack}
          speechFilter={speechFilters[activeTrack.id]}
          speechFilterLoading={Boolean(speechFilterLoadingIds[activeTrack.id])}
          speechFilterSaving={Boolean(speechFilterSavingIds[activeTrack.id])}
          speechFilterError={speechFilterErrors[activeTrack.id]}
          visualPlan={visualPlans[activeTrack.id]}
          visualPlanLoading={Boolean(visualPlanLoadingIds[activeTrack.id])}
              visualPlanError={visualPlanErrors[activeTrack.id]}
              renderLoading={Boolean(renderLoadingIds[activeTrack.id])}
              renderError={renderErrors[activeTrack.id]}
              onGenerateSpeechFilter={handleGenerateSpeechFilter}
              onGenerateVisualPlan={handleGenerateVisualPlan}
              onSaveSpeechFilter={handleSaveSpeechFilter}
              onUpdateBackgroundMusic={handleUpdateBackgroundMusic}
              onRenderTrack={handleRenderTrack}
              onClose={() => setActiveTrack(null)}
            />
      ) : null}
    </div>
  );
}
