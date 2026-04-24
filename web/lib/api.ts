import {
  AuthResponse,
  BillingPlan,
  BillingPlanKey,
  ProjectListResponse,
  ProjectResponse,
  RenderManifestResponse,
  SubscriptionPlan,
  TimelineResponse,
  User,
} from "./types";
import { clearStoredSession, getStoredSession, setStoredSession } from "./session";

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
let apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE;

const DEFAULT_BILLING_PLANS: BillingPlan[] = [
  {
    key: "monthly",
    title: "1 Month",
    billing_period: "1 month",
    price_label: "$19.99",
    original_price_label: "",
    discount_label: "",
    features: [
      "AI-first rough cut",
      "Subtitle timing",
      "Insertion suggestions",
      "Faster export workflow",
    ],
  },
  {
    key: "six_month",
    title: "6 Months",
    billing_period: "6 months",
    price_label: "$59.99",
    original_price_label: "$99.99",
    discount_label: "-40%",
    features: [
      "Everything in monthly",
      "Lower cost per project",
      "Best for active creators",
      "Priority product updates",
    ],
  },
  {
    key: "yearly",
    title: "1 Year",
    billing_period: "1 year",
    price_label: "$99.00",
    original_price_label: "$247.50",
    discount_label: "-60%",
    features: [
      "Everything in six months",
      "Maximum discount",
      "Best long-term value",
      "Full workflow access",
    ],
  },
];

const BILLING_TO_SUBSCRIPTION: Record<BillingPlanKey, SubscriptionPlan> = {
  monthly: "pro_monthly",
  six_month: "pro_yearly",
  yearly: "pro_yearly",
};

function getApiBase(): string {
  return apiBaseUrl;
}

function authHeader(token?: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function shouldTryRefresh(path: string): boolean {
  return ![
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/google_login",
    "/api/auth/apple_login",
    "/api/auth/refresh",
    "/api/auth/logout",
  ].some((prefix) => path.startsWith(prefix));
}

async function parseError(response: Response): Promise<string> {
  const text = await response.text().catch(() => "");
  if (!text) return `Request failed (${response.status})`;
  try {
    const data = JSON.parse(text) as { detail?: unknown; message?: unknown };
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
    if (typeof data.message === "string" && data.message.trim()) return data.message;
  } catch {
    // raw text fallback
  }
  return text;
}

async function request<T>(
  path: string,
  method: string = "GET",
  token?: string,
  body?: unknown,
  timeoutMs: number = 15000,
  allowRefresh: boolean = true,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${getApiBase()}${path}`, {
      method,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...authHeader(token),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    if (response.status === 401 && allowRefresh && shouldTryRefresh(path)) {
      const refreshed = await tryRefreshSession();
      if (refreshed) {
        return request<T>(path, method, refreshed.access_token, body, timeoutMs, false);
      }
    }
    if (!response.ok) throw new Error(await parseError(response));
    return response.json() as Promise<T>;
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      throw new Error("Request timed out. Check backend URL and try again.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function requestMultipart<T>(
  path: string,
  body: FormData,
  token?: string,
  timeoutMs: number = 120000,
  allowRefresh: boolean = true,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${getApiBase()}${path}`, {
      method: "POST",
      credentials: "include",
      headers: authHeader(token),
      body,
      signal: controller.signal,
    });
    if (response.status === 401 && allowRefresh && shouldTryRefresh(path)) {
      const refreshed = await tryRefreshSession();
      if (refreshed) {
        return requestMultipart<T>(path, body, refreshed.access_token, timeoutMs, false);
      }
    }
    if (!response.ok) throw new Error(await parseError(response));
    return response.json() as Promise<T>;
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      throw new Error("Upload timed out. Check backend URL and try again.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function hydrateSession(raw: { access_token: string; token_type?: string; user_id: string }): Promise<AuthResponse> {
  const user = await request<User>("/api/auth/me", "GET", raw.access_token);
  return {
    access_token: raw.access_token,
    token_type: raw.token_type || "bearer",
    user_id: raw.user_id,
    user,
  };
}

async function tryRefreshSession(): Promise<AuthResponse | null> {
  const current = getStoredSession();
  try {
    const response = await fetch(`${getApiBase()}/api/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: current?.access_token ? authHeader(current.access_token) : undefined,
    });
    if (!response.ok) {
      clearStoredSession();
      return null;
    }
    const raw = await response.json() as { access_token: string; token_type?: string; user_id: string };
    const session = await hydrateSession(raw);
    setStoredSession(session);
    return session;
  } catch {
    clearStoredSession();
    return null;
  }
}

export const api = {
  apiBase: getApiBase,
  setBaseUrl(url: string) {
    apiBaseUrl = url;
  },
  async emailSignup(payload: { email: string; password: string; name: string }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>(
      "/api/auth/signup",
      "POST",
      undefined,
      {
        email: payload.email,
        password: payload.password,
        full_name: payload.name,
      },
    );
    return hydrateSession(raw);
  },
  async emailSignin(payload: { email: string; password: string }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>(
      "/api/auth/login",
      "POST",
      undefined,
      payload,
    );
    return hydrateSession(raw);
  },
  async googleLogin(payload: { id_token?: string; email?: string; name?: string; picture?: string; provider_user_id?: string }) {
    const raw = await request<{ access_token: string; token_type: string; user_id: string }>(
      "/api/auth/google_login",
      "POST",
      undefined,
      payload,
    );
    const session = await hydrateSession(raw);
    if (payload.picture) {
      session.user.picture = payload.picture;
    }
    if (payload.name && !session.user.full_name) {
      session.user.full_name = payload.name;
    }
    return session;
  },
  async me(token: string) {
    return request<User>("/api/auth/me", "GET", token);
  },
  async updateProfile(token: string, payload: { full_name?: string }) {
    return request<User>("/api/auth/me", "PATCH", token, payload);
  },
  async deleteAccount(token: string) {
    return request<{ message: string }>("/api/auth/me", "DELETE", token);
  },
  async logout(token?: string) {
    return request<{ message: string }>("/api/auth/logout", "POST", token);
  },
  async completeOnboarding(token: string, payload: { goal?: string; niche?: string; preferred_edit_style?: string }) {
    return request<User>("/api/auth/onboarding", "PUT", token, payload);
  },
  async getBillingPlans() {
    return DEFAULT_BILLING_PLANS;
  },
  async subscribePremium(token: string, planKey: BillingPlanKey) {
    const mappedPlan = BILLING_TO_SUBSCRIPTION[planKey];
    await request("/api/auth/payments/start", "POST", token, { plan: mappedPlan });
    return request<User>("/api/auth/payments/activate", "POST", token, { plan: mappedPlan });
  },
  async listProjects(limit: number = 100, offset: number = 0, token?: string) {
    return request<ProjectListResponse>(
      `/api/projects/?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
      "GET",
      token,
    );
  },
  async createProject(payload: { name: string; files: File[] }, token?: string) {
    const form = new FormData();
    form.append("name", payload.name);
    form.append("smart_pause_cutter", "true");
    form.append("generate_subtitles", "true");
    form.append("insert_suggestions", "true");
    form.append("capture_times_json", JSON.stringify(payload.files.map(() => null)));
    form.append("metadata_json", JSON.stringify(payload.files.map(() => ({}))));
    payload.files.forEach((file) => form.append("files", file));
    return requestMultipart<{ project: { id: string } }>("/api/projects/", form, token);
  },
  async getProject(projectId: string, token?: string) {
    return request<ProjectResponse>(`/api/projects/${projectId}`, "GET", token);
  },
  getTrackMediaUrl(projectId: string, trackId: string) {
    return `${getApiBase()}/api/projects/${projectId}/tracks/${trackId}/media`;
  },
  async getTimeline(projectId: string, token?: string) {
    return request<TimelineResponse>(`/api/projects/${projectId}/timeline`, "GET", token);
  },
  async getRenderManifest(projectId: string, token?: string) {
    return request<RenderManifestResponse>(`/api/projects/${projectId}/render-manifest`, "GET", token);
  },
  getOutputUrl(projectId: string) {
    return `${getApiBase()}/api/projects/${projectId}/download`;
  },
  getWordSrtUrl(projectId: string) {
    return `${getApiBase()}/api/projects/${projectId}/word-srt`;
  },
  async excludeTrack(projectId: string, trackId: string, token?: string) {
    return request<{ track_id: string; excluded: boolean }>(
      `/api/projects/${projectId}/tracks/${trackId}/exclude`,
      "PATCH",
      token,
    );
  },
  async addTracksToProject(projectId: string, files: File[], token?: string) {
    const form = new FormData();
    form.append("capture_times_json", JSON.stringify(files.map(() => null)));
    form.append("metadata_json", JSON.stringify(files.map(() => ({}))));
    files.forEach((file) => form.append("files", file));
    return requestMultipart<ProjectResponse>(`/api/projects/${projectId}/tracks`, form, token);
  },
};
