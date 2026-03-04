const features = [
  {
    title: 'Silence Cleanup',
    description: 'Detect and remove pauses over 1 second while preserving natural speech flow.',
  },
  {
    title: 'Subtitles + Location',
    description: 'Generate subtitle overlays from word-level timestamps with optional location context.',
  },
  {
    title: 'Smart Insertions',
    description: 'Suggest supporting images, memes, or b-roll moments based on narration context.',
  },
];

const reviews = [
  {
    name: 'Maya R.',
    role: 'Travel Creator',
    text: 'My first cut went from 2 hours to 35 minutes. Subtitle timing is precise.',
  },
  {
    name: 'Oleg D.',
    role: 'YouTube Host',
    text: 'Gap detection cleans my talking-head videos with almost no manual edits.',
  },
  {
    name: 'Nina K.',
    role: 'Reels Editor',
    text: 'Insertion suggestions are surprisingly useful for engagement spikes.',
  },
];

const faqs = [
  {
    q: 'Does BestShotAI keep word-level timestamps for all clips?',
    a: 'Yes. We store a full word timeline and can export a word-level SRT file.',
  },
  {
    q: 'Can I review cuts before final export?',
    a: 'Yes. Auto pre-edit is applied first, then you confirm in timeline view before exporting.',
  },
  {
    q: 'Can I upload multiple videos at once from iPhone?',
    a: 'Yes. The iOS flow supports multi-select upload and chronological ordering.',
  },
];

export default function Home() {
  return (
    <>
      <section className="hero">
        <div className="card stack">
          <span className="badge">BestShotAI Studio</span>
          <h1>Cut dead air. Keep the story.</h1>
          <p className="muted">
            ShapeMiles-like structure, adapted for video editing: clear workflow, clean sections, fast path to export.
          </p>
          <div className="row">
            <a href="/projects/new" className="btn">Create Project</a>
            <a href="/projects/demo" className="btn secondary">Open Timeline</a>
          </div>
        </div>

        <div className="card stack">
          <h2>Pre-edit pipeline</h2>
          <span>1. Multi-video ingest + chronological ordering</span>
          <span>2. Whisper transcription with word timestamps</span>
          <span>3. Gap detection ({'>'}1 sec), subtitles, insertion cues</span>
          <span>4. Export final cut + word-level SRT</span>
          <div className="stack" style={{ marginTop: 6 }}>
            <div className="listItem"><span>VO waveform cleanup</span><b>92%</b></div>
            <div className="listItem"><span>Subtitle timing confidence</span><b>97%</b></div>
            <div className="listItem"><span>Insertion opportunities</span><b>14 cues</b></div>
          </div>
        </div>
      </section>

      <section className="card stack">
        <h2>Live metrics</h2>
        <div className="kpiGrid">
          <div className="kpi"><b>3.4x</b><span className="muted">faster first cut</span></div>
          <div className="kpi"><b>Word-level</b><span className="muted">timestamp sync</span></div>
          <div className="kpi"><b>Auto</b><span className="muted">subtitles + insertions</span></div>
        </div>
      </section>

      <section className="card stack">
        <div className="sectionHead">
          <h2>Features</h2>
          <p className="muted">Built for fast voice-driven video editing.</p>
        </div>
        <div className="featureGrid">
          {features.map((item) => (
            <article key={item.title} className="featureCard">
              <h3>{item.title}</h3>
              <p>{item.description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="card stack">
        <div className="sectionHead">
          <h2>Reviews</h2>
        </div>
        <div className="reviewGrid">
          {reviews.map((item) => (
            <article key={item.name} className="reviewCard">
              <p>“{item.text}”</p>
              <strong>{item.name}</strong>
              <span className="muted">{item.role}</span>
            </article>
          ))}
        </div>
      </section>

      <section className="card stack">
        <div className="sectionHead">
          <h2>FAQ</h2>
        </div>
        <div className="faqList">
          {faqs.map((item) => (
            <article key={item.q} className="faqItem">
              <h3>{item.q}</h3>
              <p>{item.a}</p>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
