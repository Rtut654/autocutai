import Link from 'next/link';

export default function NewProjectPage() {
  return (
    <section className="card stack">
      <h1>Create Project</h1>
      <p className="muted">Upload videos, auto pre-edit narration, then review timeline.</p>

      <div className="stack">
        <label>Project Name</label>
        <input defaultValue="BestShot travel story" />
      </div>

      <div className="stack">
        <label>Videos (multi-select)</label>
        <input type="file" multiple />
      </div>

      <div className="row">
        <Link className="btn" href="/projects/demo">Run Auto Pre-Edit</Link>
        <Link className="btn secondary" href="/projects/demo">Open Manual Timeline</Link>
      </div>
    </section>
  );
}
