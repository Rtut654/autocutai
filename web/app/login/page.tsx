"use client";

import Script from "next/script";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { setStoredSession } from "../../lib/session";
import { AuthResponse } from "../../lib/types";
import styles from "./styles.module.css";

const SHAPEMILES_LOCAL_WEB_CLIENT_ID = "856781364264-sismoqvdmqmfrp47rlhj77fmqsi123ma.apps.googleusercontent.com";
const DEFAULT_WEB_GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID
  || process.env.NEXT_PUBLIC_GOOGLE_WEB_CLIENT_ID
  || (process.env.NODE_ENV !== "production" ? SHAPEMILES_LOCAL_WEB_CLIENT_ID : "");

function getNextPath(): string | null {
  if (typeof window === "undefined") return null;
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith("/") ? next : null;
}

function closeLogin(router: ReturnType<typeof useRouter>) {
  const next = getNextPath();
  if (next && !next.startsWith("/login")) {
    router.push(next);
    return;
  }
  router.push("/");
}

function parseGoogleCredential(credential?: string): {
  email?: string;
  name?: string;
  picture?: string;
  provider_user_id?: string;
} {
  if (!credential) return {};
  try {
    const [, payload] = credential.split(".");
    if (!payload) return {};
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4 || 4)) % 4);
    const decoded = JSON.parse(window.atob(padded)) as {
      email?: string;
      name?: string;
      picture?: string;
      sub?: string;
    };
    return {
      email: decoded.email,
      name: decoded.name,
      picture: decoded.picture,
      provider_user_id: decoded.sub,
    };
  } catch {
    return {};
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("creator@example.com");
  const [password, setPassword] = useState("password123");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [signupConfirm, setSignupConfirm] = useState("");
  const [showSignup, setShowSignup] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [googleReady, setGoogleReady] = useState(false);
  const googleButtonWrapRef = useRef<HTMLDivElement | null>(null);
  const googleButtonRef = useRef<HTMLDivElement | null>(null);

  const onSuccess = (data: AuthResponse) => {
    setStoredSession(data);
    router.push(getNextPath() || "/projects");
  };

  const run = async (fn: () => Promise<AuthResponse>) => {
    try {
      setLoading(true);
      setError(null);
      setInfo(null);
      const data = await fn();
      onSuccess(data);
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (typeof window === "undefined") return;
    const clientId = DEFAULT_WEB_GOOGLE_CLIENT_ID;
    const google = (window as unknown as { google?: any }).google;
    if (!clientId || !google?.accounts?.id || !googleButtonRef.current || !googleButtonWrapRef.current) return;

    google.accounts.id.initialize({
      client_id: clientId,
      callback: (response: { credential?: string }) => {
        if (!response?.credential) {
          setError("Google login failed: missing credential.");
          return;
        }
        const profile = parseGoogleCredential(response.credential);
        run(() => api.googleLogin({ id_token: response.credential, ...profile }));
      },
      auto_select: false,
    });

    const renderGoogleButton = () => {
      if (!googleButtonRef.current || !googleButtonWrapRef.current) return;
      const width = Math.max(220, Math.floor(googleButtonWrapRef.current.clientWidth - 2));
      googleButtonRef.current.innerHTML = "";
      google.accounts.id.renderButton(googleButtonRef.current, {
        type: "standard",
        size: "large",
        text: "continue_with",
        shape: "pill",
        width,
        logo_alignment: "left",
      });
    };

    renderGoogleButton();
    window.addEventListener("resize", renderGoogleButton);
    return () => window.removeEventListener("resize", renderGoogleButton);
  }, [googleReady]);

  const runSignup = async () => {
    if (!signupEmail || !signupPassword) {
      setError("Please fill email and password.");
      return;
    }
    if (signupPassword !== signupConfirm) {
      setError("Passwords do not match.");
      return;
    }
    const derivedName = signupEmail.split("@")[0] || "Creator";
    try {
      setLoading(true);
      setError(null);
      setInfo(null);
      const session = await api.emailSignup({ email: signupEmail, password: signupPassword, name: derivedName });
      setInfo("Account created. You can continue immediately.");
      setStoredSession(session);
      router.push("/onboarding");
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className={styles.page}>
      <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" onLoad={() => setGoogleReady(true)} />
      <section className={styles.card}>
        <div className={styles.headRow}>
          <div className={styles.head}>
            <h1>Log in to BestShotAI</h1>
            <p>Sign in to manage onboarding, pricing, projects, and timeline review.</p>
          </div>
          <button className={styles.closeButton} type="button" onClick={() => closeLogin(router)}>×</button>
        </div>

        <div className={styles.googleButton} ref={googleButtonWrapRef}>
          {DEFAULT_WEB_GOOGLE_CLIENT_ID ? (
            <div ref={googleButtonRef} />
          ) : (
            <button type="button" className={styles.googlePlaceholder} onClick={() => setError("Google web sign-in needs NEXT_PUBLIC_GOOGLE_CLIENT_ID in web/.env.local. The iOS client ID cannot be used for the web button.")}>
              Continue with Google
            </button>
          )}
        </div>
        {!DEFAULT_WEB_GOOGLE_CLIENT_ID ? (
          <div className={styles.googleFallback}>
            Add `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in `web/.env.local` to enable the real Google web flow.
          </div>
        ) : null}

        <div className={styles.divider}><span>or</span></div>

        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" autoComplete="email" />
        </label>
        <label>
          Password
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" autoComplete="current-password" />
        </label>

        {error && <div className={styles.error}>{error}</div>}
        {info && <div className={styles.info}>{info}</div>}

        <button disabled={loading} onClick={() => run(() => api.emailSignin({ email, password }))}>
          Email login
        </button>

        <p className={styles.signupCta}>
          Don&apos;t have an account?{" "}
          <button type="button" className={styles.linkButton} onClick={() => setShowSignup(true)}>
            Sign up now
          </button>
        </p>
      </section>

      {showSignup && (
        <div className={styles.modalBackdrop}>
          <section className={styles.modalCard}>
            <div className={styles.headRow}>
              <div className={styles.head}>
                <h1>Create your BestShotAI account</h1>
                <p>Use email for now, or close and return to Google sign-in once a web client ID is configured.</p>
              </div>
              <button className={styles.closeButton} type="button" onClick={() => setShowSignup(false)}>×</button>
            </div>
            <label>
              Email
              <input value={signupEmail} onChange={(e) => setSignupEmail(e.target.value)} type="email" autoComplete="email" />
            </label>
            <label>
              Password
              <input value={signupPassword} onChange={(e) => setSignupPassword(e.target.value)} type="password" autoComplete="new-password" />
            </label>
            <label>
              Confirm password
              <input value={signupConfirm} onChange={(e) => setSignupConfirm(e.target.value)} type="password" autoComplete="new-password" />
            </label>
            {error && <div className={styles.error}>{error}</div>}
            <button disabled={loading} onClick={runSignup}>Sign up</button>
          </section>
        </div>
      )}
    </main>
  );
}
