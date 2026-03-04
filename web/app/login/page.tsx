import Link from 'next/link';

export default function LoginPage() {
  return (
    <section className="card stack">
      <h1>Login</h1>
      <p className="muted">Sign in to continue editing with BestShotAI.</p>
      <input placeholder="Email" defaultValue="creator@bestshot.ai" />
      <input placeholder="Password" type="password" defaultValue="••••••••" />
      <Link className="btn" href="/onboarding">Sign In</Link>
    </section>
  );
}
