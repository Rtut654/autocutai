export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || 'https://autocutai.app';

export type SubscriptionPlan = 'free' | 'pro_monthly' | 'pro_yearly';
export type BillingPlanKey = 'monthly' | 'six_month' | 'yearly';

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


async function check<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === 'string' && parsed.detail.trim()) detail = parsed.detail;
    } catch {
      // Not JSON; fall back to the raw body.
    }
    throw new Error(detail || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

/** Authenticated JSON request. Throws with the backend's detail message. */
export async function authorizedRequest<T>(
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<T> {
  return request<T>(path, options, token);
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
