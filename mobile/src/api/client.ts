export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || 'http://localhost:8000';

export type SubscriptionPlan = 'free' | 'pro_monthly' | 'pro_yearly';

export type MeUser = {
  id: string;
  email: string;
  full_name?: string | null;
  onboarding_completed: boolean;
  subscription_plan: SubscriptionPlan;
};

export type AuthSession = {
  access_token: string;
  token_type: string;
  user_id: string;
  user: MeUser;
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

export const api = {
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

export function downloadOutput(projectId: string) {
  return `${API_BASE_URL}/api/projects/${projectId}/download`;
}
