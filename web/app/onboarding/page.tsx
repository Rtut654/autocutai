"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { getStoredSession, setStoredSession } from "../../lib/session";

export default function OnboardingPage() {
  const router = useRouter();
  const [goal, setGoal] = useState("Make short travel reels faster");
  const [niche, setNiche] = useState("Travel + story narration");
  const [style, setStyle] = useState("energetic");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!getStoredSession()?.access_token) {
      router.replace("/login?next=/onboarding");
    }
  }, [router]);

  const save = async () => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.push("/login?next=/onboarding");
      return;
    }
    try {
      setLoading(true);
      setMessage(null);
      const user = await api.completeOnboarding(session.access_token, {
        goal,
        niche,
        preferred_edit_style: style,
      });
      setStoredSession({ ...session, user });
      router.push("/pricing");
    } catch (e) {
      setMessage(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="card stack">
      <h1>Onboarding</h1>
      <p className="muted">Match the same web flow as the mobile app: define your editing goal before pricing and projects.</p>

      <div className="grid2">
        <div className="stack">
          <label>Goal</label>
          <input value={goal} onChange={(e) => setGoal(e.target.value)} />
        </div>
        <div className="stack">
          <label>Content niche</label>
          <input value={niche} onChange={(e) => setNiche(e.target.value)} />
        </div>
      </div>

      <div className="stack">
        <label>Edit style</label>
        <select value={style} onChange={(e) => setStyle(e.target.value)}>
          <option value="energetic">Energetic</option>
          <option value="cinematic">Cinematic</option>
          <option value="minimal">Minimal</option>
        </select>
      </div>

      {message ? <div className="notice">{message}</div> : null}

      <div className="row">
        <button className="btn" disabled={loading} onClick={save}>
          {loading ? "Saving..." : "Save and Continue"}
        </button>
        <button className="btn secondary" onClick={() => router.push("/projects")}>Skip to projects</button>
      </div>
    </section>
  );
}
