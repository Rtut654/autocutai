import Link from 'next/link';

export default function BillingPage() {
  return (
    <section className="card stack">
      <h1>Billing</h1>
      <p className="muted">Unlock full BestShotAI automation for subtitles and insertion cues.</p>
      <div className="grid2">
        <article className="card stack">
          <strong>Free</strong>
          <span className="muted">Manual timeline only</span>
          <button className="btn secondary">Current Plan</button>
        </article>
        <article className="card stack">
          <strong>Pro Monthly</strong>
          <span className="muted">Auto pre-edit + advanced AI suggestions</span>
          <Link className="btn" href="/projects">Upgrade</Link>
        </article>
      </div>
    </section>
  );
}
