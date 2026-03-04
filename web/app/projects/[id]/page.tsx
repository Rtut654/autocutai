type Props = { params: Promise<{ id: string }> };

const gaps = ['[00:11.280; 00:12.310]', '[00:48.200; 00:49.400]'];
const insertions = ['(00:17, picture of Bosphorus sunset)', '(00:52, meme-video about street food reaction)'];

export default async function ProjectTimelinePage({ params }: Props) {
  const { id } = await params;

  return (
    <>
      <section className="card stack">
        <h1>Timeline: {id}</h1>
        <p className="muted">Auto pre-edit complete. Validate cuts, subtitles, and insertions before export.</p>
      </section>

      <section className="grid2">
        <div className="card stack">
          <h2>Detected Gaps ({'>'}1s)</h2>
          {gaps.map((g) => <span key={g}>{g}</span>)}
        </div>
        <div className="card stack">
          <h2>Insertion Suggestions</h2>
          {insertions.map((i) => <span key={i}>{i}</span>)}
        </div>
      </section>

      <section className="card stack">
        <h2>Subtitles + Location</h2>
        <span className="muted">[Istanbul] we started the day at the market and tried...</span>
        <div className="row">
          <button className="btn">Download Final Video</button>
          <button className="btn secondary">Download Word-Level SRT</button>
        </div>
      </section>
    </>
  );
}
