export default function Home() {
  return (
    <>
      <section className="pageHero">
        <div className="card stack">
          <span className="badge">BestShotAI Studio</span>
          <h1>Edit flow that matches the mobile app.</h1>
          <p className="muted">
            The web flow now follows the ShapeMiles structure: login, onboarding, pricing, project list, new project, and timeline review.
          </p>
          <div className="row">
            <a href="/login" className="btn">Open Login</a>
            <a href="/onboarding" className="btn secondary">Open Onboarding</a>
          </div>
        </div>

        <div className="card stack">
          <h2>Current Product Path</h2>
          <div className="list">
            <div className="listItem"><span>1. Log in with email or Google</span><b>/login</b></div>
            <div className="listItem"><span>2. Complete onboarding</span><b>/onboarding</b></div>
            <div className="listItem"><span>3. Activate pricing</span><b>/pricing</b></div>
            <div className="listItem"><span>4. Create or review projects</span><b>/projects</b></div>
          </div>
        </div>
      </section>

      <section className="flowGrid">
        <article className="flowCard">
          <strong>Login</strong>
          <p>Google button stays visible, email auth stays available, and the page redirects back into the chosen flow.</p>
        </article>
        <article className="flowCard">
          <strong>Onboarding</strong>
          <p>Profile setup lands before pricing, matching the same step order as the mobile app.</p>
        </article>
        <article className="flowCard">
          <strong>Pricing</strong>
          <p>The pricing page uses the same card structure and activation path as the ShapeMiles boilerplate.</p>
        </article>
        <article className="flowCard">
          <strong>Projects</strong>
          <p>Projects, new project, and timeline review now sit behind the same shell instead of a broken dashboard mock.</p>
        </article>
      </section>

      <section className="card stack">
        <h2>What The Web App Does</h2>
        <div className="kpiGrid">
          <div className="kpi"><b>Auth</b><span className="muted">email + Google entry point</span></div>
          <div className="kpi"><b>Hybrid</b><span className="muted">review timelines and render manifests</span></div>
          <div className="kpi"><b>Export</b><span className="muted">download final video and word SRT</span></div>
        </div>
      </section>

      <section className="card stack">
        <h2>Open Key Pages</h2>
        <div className="row">
          <a href="/pricing" className="btn secondary">Pricing</a>
          <a href="/projects" className="btn secondary">Projects</a>
          <a href="/projects/new" className="btn secondary">New Project</a>
        </div>
      </section>
    </>
  );
}
