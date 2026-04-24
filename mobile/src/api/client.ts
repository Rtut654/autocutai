export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || 'https://autocutai.app';

export type SubscriptionPlan = 'free' | 'pro_monthly' | 'pro_yearly';
export type BillingPlanKey = 'monthly' | 'six_month' | 'yearly';
export type RenderStrategy = 'on_device' | 'selected_ranges_upload';

export type BillingPlan = {
  key: BillingPlanKey;
  title: string;
  billing_period: string;
  price_label: string;
  original_price_label: string;
  discount_label: string;
};

const DEFAULT_BILLING_PLANS: BillingPlan[] = [
  {
    key: 'monthly',
    title: '1 Month',
    billing_period: '1 month',
    price_label: '$19.99',
    original_price_label: '',
    discount_label: '',
  },
  {
    key: 'six_month',
    title: '6 Months',
    billing_period: '6 months',
    price_label: '$59.99',
    original_price_label: '$99.99',
    discount_label: '-40%',
  },
  {
    key: 'yearly',
    title: '1 Year',
    billing_period: '1 year',
    price_label: '$99.00',
    original_price_label: '$247.50',
    discount_label: '-60%',
  },
];

const BILLING_TO_SUBSCRIPTION: Record<BillingPlanKey, SubscriptionPlan> = {
  monthly: 'pro_monthly',
  six_month: 'pro_yearly',
  yearly: 'pro_yearly',
};

export type MeUser = {
  id: string;
  email: string;
  full_name?: string | null;
  onboarding_completed: boolean;
  subscription_plan: SubscriptionPlan;
  provider?: string | null;
};

export type AuthSession = {
  access_token: string;
  token_type: string;
  user_id: string;
  user: MeUser;
};

type ProjectSummary = {
  id: string;
  name: string;
  status: string;
  output_path?: string | null;
  created_at?: string;
  updated_at?: string;
  tracks?: Array<{ duration?: number }>;
  pipeline?: {
    insertion_suggestions?: Array<unknown>;
    gap_ranges?: Array<unknown>;
  };
};

export type ProjectSettingsPayload = {
  aspect_ratio?: 'horizontal' | 'vertical';
  edit_mode?: 'chronological' | 'manual';
  remove_duplicates?: boolean;
  smart_pause_cutter?: boolean;
  generate_subtitles?: boolean;
  insert_suggestions?: boolean;
  min_gap_seconds?: number;
};

export type ProjectAnalysisTrack = {
  id: string;
  filename: string;
  duration: number;
  metadata?: Record<string, unknown>;
  transcription?: {
    text?: string;
    words?: Array<{ word?: string; text?: string; start: number; end: number }>;
  } | null;
};

export type ProjectAnalysisResult = {
  id: string;
  name: string;
  status: string;
  pipeline: {
    combined_transcript: string;
    gap_ranges: Array<{ start: number; end: number; duration: number; reason?: string }>;
    insertion_suggestions: Array<{ time: number; suggestion: string; media_type: string }>;
    render_plan: Record<string, unknown>;
    subtitle_cues?: Array<unknown>;
  };
  tracks: ProjectAnalysisTrack[];
  output_path?: string | null;
};

async function check<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });
  return check<T>(res);
}

async function hydrateSession(raw: { access_token: string; token_type?: string; user_id: string }): Promise<AuthSession> {
  const user = await request<MeUser>('/api/auth/me', { method: 'GET' }, raw.access_token);
  return {
    access_token: raw.access_token,
    token_type: raw.token_type || 'bearer',
    user_id: raw.user_id,
    user,
  };
}

function normalizeBillingPlanKey(value?: string): BillingPlanKey {
  if (value === 'monthly' || value === 'six_month' || value === 'yearly') return value;
  return 'yearly';
}

export const api = {
  getBaseUrl() {
    return API_BASE_URL;
  },

  async signup(payload: { email: string; password: string; full_name?: string }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>(
      '/api/auth/signup',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
    return hydrateSession(raw);
  },

  async login(payload: { email: string; password: string }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return hydrateSession(raw);
  },

  async me(token: string) {
    return request<MeUser>('/api/auth/me', { method: 'GET' }, token);
  },

  async googleLogin(payload: {
    id_token?: string;
    email?: string;
    name?: string;
    picture?: string;
    provider_user_id?: string;
  }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>(
      '/api/auth/google_login',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
    return hydrateSession(raw);
  },

  async appleLogin(payload: {
    code?: string;
    id_token?: string;
    email?: string;
    name?: string;
    provider_user_id?: string;
  }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>(
      '/api/auth/apple_login',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
    return hydrateSession(raw);
  },

  async completeOnboarding(
    token: string,
    payload: { goal?: string; niche?: string; preferred_edit_style?: string },
  ) {
    return request<MeUser>(
      '/api/auth/onboarding',
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
      token,
    );
  },

  async startPayment(token: string, plan: SubscriptionPlan) {
    return request<{ checkout_url: string; plan: SubscriptionPlan; status: 'pending' | 'active' }>(
      '/api/auth/payments/start',
      {
        method: 'POST',
        body: JSON.stringify({ plan }),
      },
      token,
    );
  },

  async activatePayment(token: string, plan: SubscriptionPlan) {
    return request<MeUser>(
      '/api/auth/payments/activate',
      {
        method: 'POST',
        body: JSON.stringify({ plan }),
      },
      token,
    );
  },

  async getBillingPlans(_token?: string | null) {
    return DEFAULT_BILLING_PLANS;
  },

  async getBillingStatus(token: string) {
    const me = await request<MeUser>('/api/auth/me', { method: 'GET' }, token);
    return { is_premium: me.subscription_plan !== 'free', plan: me.subscription_plan };
  },

  async subscribePremium(
    token: string,
    payloadOrPlanKey:
      | BillingPlanKey
      | {
          plan_key?: string;
          purchase_provider?: string;
          product_id?: string;
          transaction_id?: string;
          receipt_data?: string;
          purchase_token?: string;
        },
  ) {
    const billingPlanKey =
      typeof payloadOrPlanKey === 'string'
        ? normalizeBillingPlanKey(payloadOrPlanKey)
        : normalizeBillingPlanKey(payloadOrPlanKey?.plan_key);

    const mappedPlan = BILLING_TO_SUBSCRIPTION[billingPlanKey];
    await api.startPayment(token, mappedPlan);
    return api.activatePayment(token, mappedPlan);
  },

  async listProjects(limit = 100, offset = 0) {
    return request<{ projects: ProjectSummary[]; total: number }>(
      `/api/projects/?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
      { method: 'GET' },
    );
  },

  async analyzeHybridProject(payload: {
    name: string;
    description?: string;
    tracks: Array<Record<string, unknown>>;
    settings?: Partial<ProjectSettingsPayload>;
    render_strategy?: RenderStrategy;
  }) {
    const response = await request<{ project: ProjectAnalysisResult }>(
      '/api/projects/hybrid-analyze',
      {
        method: 'POST',
        body: JSON.stringify({
          name: payload.name,
          description: payload.description,
          tracks: payload.tracks,
          settings: {
            smart_pause_cutter: true,
            generate_subtitles: true,
            insert_suggestions: true,
            ...(payload.settings || {}),
          },
          render_strategy: payload.render_strategy || 'on_device',
        }),
      },
    );
    return response.project;
  },

  async updateProfile(token: string, payload: { full_name?: string | null }) {
    return request<MeUser>(
      '/api/auth/me',
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      token,
    );
  },

  async deleteAccount(token: string) {
    return request<{ message: string }>('/api/auth/me', { method: 'DELETE' }, token);
  },
};

export async function createProject(payload: {
  name: string;
  files: { uri: string; name: string; mimeType?: string; recordedAt?: string }[];
}) {
  const form = new FormData();
  form.append('name', payload.name);
  form.append('smart_pause_cutter', 'true');
  form.append('generate_subtitles', 'true');
  form.append('insert_suggestions', 'true');

  const captureTimes = payload.files.map((f) => f.recordedAt || null);
  const metadata = payload.files.map(() => ({}));
  form.append('capture_times_json', JSON.stringify(captureTimes));
  form.append('metadata_json', JSON.stringify(metadata));

  payload.files.forEach((f) => {
    form.append('files', {
      uri: f.uri,
      name: f.name,
      type: f.mimeType || 'video/mp4',
    } as any);
  });

  const res = await fetch(`${API_BASE_URL}/api/projects/`, {
    method: 'POST',
    body: form,
  });

  return check<{ project: { id: string } }>(res);
}

export async function processProjectSync(projectId: string) {
  const res = await fetch(`${API_BASE_URL}/api/projects/${projectId}/process-sync`, { method: 'POST' });
  return check<any>(res);
}

export async function getTimeline(projectId: string) {
  const res = await fetch(`${API_BASE_URL}/api/projects/${projectId}/timeline`);
  return check<any>(res);
}

export async function getRenderManifest(projectId: string) {
  const res = await fetch(`${API_BASE_URL}/api/projects/${projectId}/render-manifest`);
  return check<any>(res);
}

export function downloadOutput(projectId: string) {
  return `${API_BASE_URL}/api/projects/${projectId}/download`;
}
