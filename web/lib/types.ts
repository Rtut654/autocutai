export type SubscriptionPlan = "free" | "pro_monthly" | "pro_yearly";
export type BillingPlanKey = "monthly" | "six_month" | "yearly";

export type User = {
  id: string;
  email: string;
  full_name?: string | null;
  picture?: string | null;
  onboarding_completed: boolean;
  subscription_plan: SubscriptionPlan;
  provider?: string | null;
};

export type AuthResponse = {
  access_token: string;
  token_type: string;
  user_id: string;
  user: User;
};

export type BillingPlan = {
  key: BillingPlanKey;
  title: string;
  billing_period: string;
  price_label: string;
  original_price_label: string;
  discount_label: string;
  features: string[];
};

export type GapRange = {
  start: number;
  end: number;
  duration: number;
  reason?: string;
};

export type InsertionSuggestion = {
  time: number;
  suggestion: string;
  media_type: string;
};

export type BackgroundVideoPlacement = {
  track_id: string;
  filename: string;
  description: string;
  start: number;
  end: number;
  duration: number;
  transcript_excerpt: string;
  rationale: string;
  confidence: number;
};

export type BackgroundVideoPlanArtifact = {
  project_id: string;
  status: "completed" | "error";
  summary: string;
  placements: BackgroundVideoPlacement[];
  generated_at: string;
  source_word_count: number;
  model: string;
  worker: string;
  error_message?: string | null;
};

export type TranscriptWord = {
  word: string;
  start: number;
  end: number;
  confidence?: number;
};

export type TranscriptSegment = {
  start: number;
  end: number;
  text: string;
  words?: TranscriptWord[];
};

export type TrackTranscription = {
  text?: string;
  words?: TranscriptWord[];
  segments?: TranscriptSegment[];
  language?: string;
};

export type SpeechFilterCut = {
  start: number;
  end: number;
  duration: number;
  reason: string;
  transcript: string;
  confidence: number;
};

export type ZoomPreviewBeat = {
  start: number;
  end: number;
  duration: number;
  text: string;
  enabled: boolean;
  scale: number;
};

export type SpeechFilterArtifact = {
  project_id: string;
  track_id: string;
  filename: string;
  status: "completed" | "error";
  summary: string;
  cuts: SpeechFilterCut[];
  zoom_beats: ZoomPreviewBeat[];
  generated_at: string;
  source_word_count: number;
  model: string;
  error_message?: string | null;
};

export type TrackRenderVersion = {
  id: string;
  label: string;
  filename: string;
  file_path: string;
  created_at: string;
  source: "speech_filter";
  cut_count: number;
  duration_before: number;
  duration_after: number;
};

export type TrackRenderResponse = {
  version: TrackRenderVersion;
  message: string;
};

export type BackgroundMusicSettings = {
  enabled: boolean;
  preset: "ambient_pulse" | "upbeat_motion" | "warm_focus";
  volume: number;
  ducking: number;
};

export type VisualPlanPart = {
  start: number;
  end: number;
  duration: number;
  text: string;
  visual_type: "animation" | "web_image";
  prompt: string;
  search_query?: string | null;
  animation_kind?: string | null;
  title?: string | null;
  keywords?: string[];
  scene_objects?: string[];
  placement?: string | null;
  density?: "light" | "medium" | null;
  palette?: string | null;
  variant?: string | null;
  motion_profile?: string | null;
  background_style?: "transparent" | null;
  asset_status: "planned" | "ready" | "error";
  asset_url?: string | null;
  local_path?: string | null;
  transition_in?: string | null;
  transition_out?: string | null;
  sfx?: string | null;
};

export type VisualPlanArtifact = {
  project_id: string;
  track_id: string;
  filename: string;
  status: "completed" | "error";
  summary: string;
  parts: VisualPlanPart[];
  generated_at: string;
  source_word_count: number;
  model: string;
  worker: string;
  error_message?: string | null;
};

export type ProjectTrack = {
  id: string;
  type: string;
  filename: string;
  file_path: string;
  duration: number;
  orientation?: "horizontal" | "vertical" | "square" | "unknown";
  width?: number | null;
  height?: number | null;
  start_time?: number | null;
  end_time?: number | null;
  position: number;
  metadata?: Record<string, unknown>;
  transcription?: TrackTranscription | null;
  has_voice?: boolean;
  recorded_at?: string | null;
  local_gap_ranges?: GapRange[];
  background_music?: BackgroundMusicSettings;
  render_versions?: TrackRenderVersion[];
  role?: "primary" | "background";
  background_description?: string | null;
  background_trim_start?: number;
  background_trim_end?: number | null;
  background_playback_rate?: number;
  status?: "visible" | "hidden";
  excluded?: boolean;
};

export type ProjectPipeline = {
  combined_transcript?: string;
  combined_words?: TranscriptWord[];
  insertion_suggestions?: InsertionSuggestion[];
  background_video_suggestions?: BackgroundVideoPlacement[];
  gap_ranges?: GapRange[];
  render_plan?: Record<string, unknown>;
  subtitle_cues?: Array<Record<string, unknown>>;
};

export type ProjectSummary = {
  id: string;
  name: string;
  status: string;
  user_id?: string | null;
  output_path?: string | null;
  created_at?: string;
  updated_at?: string;
  tracks?: ProjectTrack[];
  pipeline?: ProjectPipeline;
};

export type ProjectDetail = ProjectSummary & {
  description?: string | null;
  tracks: ProjectTrack[];
  pipeline: ProjectPipeline;
  error_message?: string | null;
};

export type ProjectResponse = {
  project: ProjectDetail;
  message: string;
};

export type ProjectListResponse = {
  projects: ProjectSummary[];
  total: number;
};

export type TimelineResponse = {
  project_id: string;
  tracks: ProjectTrack[];
  pipeline: ProjectPipeline;
};

export type RenderManifestResponse = {
  project_id: string;
  render_plan: Record<string, unknown>;
  subtitle_cues: Array<Record<string, unknown>>;
  gap_ranges: GapRange[];
  insertions: InsertionSuggestion[];
};
