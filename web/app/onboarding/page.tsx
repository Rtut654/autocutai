import Link from 'next/link';

export default function OnboardingPage() {
  return (
    <section className="card stack">
      <h1>Onboarding</h1>
      <div className="grid2">
        <div className="stack">
          <label>Goal</label>
          <input defaultValue="Make short travel reels faster" />
        </div>
        <div className="stack">
          <label>Content Niche</label>
          <input defaultValue="Travel + story narration" />
        </div>
      </div>
      <div className="stack">
        <label>Edit style</label>
        <select defaultValue="energetic">
          <option value="energetic">Energetic</option>
          <option value="cinematic">Cinematic</option>
          <option value="minimal">Minimal</option>
        </select>
      </div>
      <Link className="btn" href="/billing">Save and Continue</Link>
    </section>
  );
}
