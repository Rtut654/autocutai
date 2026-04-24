"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { getStoredSession, setStoredSession } from "../../lib/session";
import type { BillingPlan, BillingPlanKey, User } from "../../lib/types";
import styles from "./pricing.module.css";

function cardShape(index: number): string {
  const all = [
    "M10,50 C25,15 55,15 70,50 C82,35 110,35 120,55 C130,75 112,100 88,98 C71,96 59,80 50,65 C42,80 30,96 14,98 C-8,100 -20,74 -8,55 C2,38 22,35 34,50 Z",
    "M60,8 L74,42 L112,42 L80,63 L92,98 L60,76 L28,98 L40,63 L8,42 L46,42 Z",
    "M18,78 C18,42 42,16 78,16 C102,16 118,32 118,54 C118,78 100,98 74,98 C42,98 18,110 18,78 Z",
  ];
  return all[index % all.length];
}

function displayName(user: User | null): string {
  return user?.full_name || user?.email || "Creator";
}

export default function PricingPage() {
  const router = useRouter();
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api.getBillingPlans().then(setPlans).catch(() => setPlans([]));
    const session = getStoredSession();
    setUser(session?.user || null);
  }, []);

  const title = useMemo(() => {
    if (!user) return "Choose your BestShotAI plan";
    return `${displayName(user)}, choose your BestShotAI plan`;
  }, [user]);

  const onBuy = async (plan: BillingPlan) => {
    const session = getStoredSession();
    if (!session?.access_token) {
      router.push("/login?next=/pricing");
      return;
    }

    try {
      setBusy(plan.key);
      setMessage(null);
      const updated = await api.subscribePremium(session.access_token, plan.key as BillingPlanKey);
      const nextSession = { ...session, user: updated };
      setStoredSession(nextSession);
      setUser(updated);
      setMessage(`Plan activated: ${plan.title}`);
    } catch (e) {
      setMessage(String((e as Error).message || e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className={styles.page}>
      <header className={styles.hero}>
        <span className={styles.badge}>BestShotAI Premium</span>
        <h1>{title}</h1>
        <p>Unlock the full edit workflow: subtitle generation, insertion suggestions, and fast AI-assisted project review.</p>
      </header>

      <div className={styles.grid}>
        {plans.map((plan, index) => (
          <article key={plan.key} className={styles.card}>
            <svg viewBox="0 0 130 110" className={styles.cardShape} aria-hidden="true">
              <path d={cardShape(index)} />
            </svg>
            <div className={styles.cardTop}>
              <h2>{plan.title}</h2>
              <small>{plan.billing_period}</small>
            </div>
            <div className={styles.priceRow}>
              <strong>{plan.price_label}</strong>
              <span>{plan.discount_label}</span>
            </div>
            <p className={styles.original}>{plan.original_price_label}</p>
            <ul className={styles.features}>
              {plan.features.map((feature) => (
                <li key={feature}>{feature}</li>
              ))}
            </ul>
            <button type="button" className={styles.cta} disabled={busy === plan.key} onClick={() => onBuy(plan)}>
              {busy === plan.key ? "Processing..." : "Get Premium"}
            </button>
          </article>
        ))}
      </div>

      <p className={styles.legal}>
        <a href="/terms-of-use">Terms of Use</a>
        <span>·</span>
        <a href="/privacy-policy">Privacy Policy</a>
      </p>

      {message && <p className={styles.notice}>{message}</p>}
    </section>
  );
}
