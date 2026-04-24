"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { clearStoredSession, getStoredSession, setStoredSession } from "../../lib/session";
import type { User } from "../../lib/types";

function planLabel(plan: string | undefined): string {
  if (plan === "pro_yearly") return "Pro Yearly";
  if (plan === "pro_monthly") return "Pro Monthly";
  return "Free";
}

function signInLabel(provider?: string | null): string {
  if (provider === "apple") return "Apple";
  if (provider === "google") return "Google";
  return "Email";
}

export default function ProfilePage() {
  const router = useRouter();
  const [profile, setProfile] = useState<User | null>(null);
  const [name, setName] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.replace("/login?next=/profile");
      return;
    }
    setProfile(session.user);
    setName(String(session.user.full_name || ""));
  }, [router]);

  const saveProfile = async () => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.push("/login?next=/profile");
      return;
    }
    try {
      setSaving(true);
      setMessage(null);
      const updated = await api.updateProfile(session.access_token, { full_name: name.trim() });
      setProfile(updated);
      setStoredSession({ ...session, user: updated });
      setMessage("Profile updated.");
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setSaving(false);
    }
  };

  const deleteAccount = async () => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.push("/login?next=/profile");
      return;
    }
    const confirmed = window.confirm("Delete your account permanently?");
    if (!confirmed) return;
    try {
      setDeleting(true);
      setMessage(null);
      await api.deleteAccount(session.access_token);
      clearStoredSession();
      router.push("/login");
    } catch (error) {
      setMessage(String((error as Error).message || error));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <section className="stack">
      <div className="card stack">
        <span className="badge">Profile</span>
        <h1>Manage account, billing, and support.</h1>
        <p className="muted">This follows the same account flow as the mobile app: profile first, pricing next, logout and legal actions at the end.</p>
      </div>

      <div className="grid2">
        <section className="card stack">
          <h2>Account</h2>
          <div className="stack">
            <label>Name</label>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Your name" />
          </div>
          <div className="list">
            <div className="listItem"><span>Email</span><strong>{profile?.email || "-"}</strong></div>
            <div className="listItem"><span>Sign in with</span><strong>{signInLabel(profile?.provider)}</strong></div>
          </div>
          <div className="row">
            <button className="btn" type="button" disabled={saving} onClick={saveProfile}>
              {saving ? "Saving..." : "Save Profile"}
            </button>
            <button className="btn secondary" type="button" onClick={() => router.push("/pricing")}>
              Open Pricing
            </button>
          </div>
        </section>

        <section className="card stack">
          <h2>Billing</h2>
          <div className="list">
            <div className="listItem"><span>Plan</span><strong>{planLabel(profile?.subscription_plan)}</strong></div>
            <div className="listItem"><span>Onboarding</span><strong>{profile?.onboarding_completed ? "Completed" : "Pending"}</strong></div>
          </div>
          <p className="muted">Upgrade or manage plan details from the pricing page. The current backend still uses a scaffold billing flow.</p>
          <button className="btn" type="button" onClick={() => router.push("/pricing")}>
            {profile?.subscription_plan === "free" ? "Upgrade to Premium" : "Manage Plan"}
          </button>
        </section>
      </div>

      <div className="grid2">
        <section className="card stack">
          <h2>Support</h2>
          <p className="muted">Use the direct support email for onboarding, billing, or export issues.</p>
          <a className="btn secondary" href="mailto:contact@autocutai.app">Email Support</a>
        </section>

        <section className="card stack">
          <h2>Legal</h2>
          <p className="muted">Review the current legal pages before production launch.</p>
          <div className="row">
            <a className="btn secondary" href="/terms-of-use">Terms of Use</a>
            <a className="btn secondary" href="/privacy-policy">Privacy Policy</a>
          </div>
        </section>
      </div>

      {message ? <div className="notice">{message}</div> : null}

      <div className="card stack">
        <h2>Danger Zone</h2>
        <p className="muted">Deleting an account removes the in-memory auth record in the current backend scaffold.</p>
        <div className="row">
          <button className="btn secondary" type="button" onClick={() => { clearStoredSession(); router.push("/login"); }}>
            Log Out
          </button>
          <button className="btn" type="button" disabled={deleting} onClick={deleteAccount}>
            {deleting ? "Deleting..." : "Delete Account"}
          </button>
        </div>
      </div>
    </section>
  );
}
