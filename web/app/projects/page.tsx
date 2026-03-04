import Link from 'next/link';

const projects = [
  { id: 'demo', name: 'Istanbul Food Story', status: 'completed' },
  { id: 'draft-2', name: 'Ski Trip Journal', status: 'draft' },
];

export default function ProjectsPage() {
  return (
    <section className="card stack">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1>Projects</h1>
        <Link href="/projects/new" className="btn">New Project</Link>
      </div>
      <div className="list">
        {projects.map((p) => (
          <Link key={p.id} href={`/projects/${p.id}`} className="listItem">
            <strong>{p.name}</strong>
            <span className="muted">{p.status}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}
