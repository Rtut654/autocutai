const featureCards = [
  {
    name: "Remove filler words and pauses",
    eyebrow: "Clean speech",
    description: "Detects ums, ahs, long pauses, and dead air so the final cut feels tighter without manual trimming.",
    demo: (
      <div className="marketingDemo marketingDemoTranscript" aria-hidden="true">
        <div className="marketingTranscriptLine">
          <span className="marketingWord marketingWordKeep">So</span>
          <span className="marketingWord marketingWordRemove">um</span>
          <span className="marketingWord marketingWordKeep">this</span>
          <span className="marketingWord marketingWordKeep">edit</span>
          <span className="marketingWord marketingWordRemove">like</span>
          <span className="marketingWord marketingWordKeep">gets</span>
          <span className="marketingWord marketingWordKeep">to</span>
          <span className="marketingWord marketingWordKeep">the point</span>
        </div>
        <div className="marketingTimelineBar">
          <span className="marketingTimelineKeep" style={{ width: "18%" }} />
          <span className="marketingTimelineRemove" style={{ width: "9%" }} />
          <span className="marketingTimelineKeep" style={{ width: "28%" }} />
          <span className="marketingTimelineRemove" style={{ width: "7%" }} />
          <span className="marketingTimelineKeep" style={{ width: "38%" }} />
        </div>
      </div>
    ),
  },
  {
    name: "Generate subtitles on the fly",
    eyebrow: "Live captions",
    description: "Auto-generates subtitle lines while the edit is rendering, ready for export as burned-in captions or SRT.",
    demo: (
      <div className="marketingDemo marketingDemoSubtitles" aria-hidden="true">
        <div className="marketingPhoneFrame">
          <div className="marketingVideoGlow" />
          <div className="marketingSubtitleBubble">Video editing made simple.</div>
          <div className="marketingSubtitleBubble marketingSubtitleBubbleAccent">Let AI choose the best shot.</div>
        </div>
      </div>
    ),
  },
  {
    name: "Automatic visual effects",
    eyebrow: "Visual polish",
    description: "Adds motion callouts, overlays, and punchy visual accents based on what is being said in the clip.",
    demo: (
      <div className="marketingDemo marketingDemoEffects" aria-hidden="true">
        <div className="marketingFxStage">
          <span className="marketingFxOrb marketingFxOrbA" />
          <span className="marketingFxOrb marketingFxOrbB" />
          <span className="marketingFxPanel">BEST SHOT</span>
          <span className="marketingFxArrow" />
        </div>
      </div>
    ),
  },
  {
    name: "Background and transition sound effects",
    eyebrow: "Audio layers",
    description: "Places whooshes, clicks, and music beds under transitions so every cut lands with more energy.",
    demo: (
      <div className="marketingDemo marketingDemoAudio" aria-hidden="true">
        <div className="marketingWaveRow">
          <span className="marketingWaveBar" style={{ height: "36%" }} />
          <span className="marketingWaveBar" style={{ height: "72%" }} />
          <span className="marketingWaveBar marketingWaveBarAccent" style={{ height: "94%" }} />
          <span className="marketingWaveBar" style={{ height: "54%" }} />
          <span className="marketingWaveBar" style={{ height: "68%" }} />
          <span className="marketingWaveBar marketingWaveBarAccent" style={{ height: "88%" }} />
          <span className="marketingWaveBar" style={{ height: "42%" }} />
          <span className="marketingWaveBar" style={{ height: "76%" }} />
        </div>
        <div className="marketingAudioLabels">
          <span>Whoosh</span>
          <span>Beat</span>
          <span>Cut</span>
        </div>
      </div>
    ),
  },
  {
    name: "Upload to all social medias in one click",
    eyebrow: "Publish everywhere",
    description: "Push the finished edit to TikTok, Facebook, YouTube, and Instagram from one export flow.",
    demo: (
      <div className="marketingDemo marketingDemoSocial" aria-hidden="true">
        <div className="marketingSocialHub">1 Click</div>
        <div className="marketingSocialIcons">
          <span className="marketingSocialIcon marketingSocialTikTok">t</span>
          <span className="marketingSocialIcon marketingSocialFacebook">f</span>
          <span className="marketingSocialIcon marketingSocialYoutube">▶</span>
          <span className="marketingSocialIcon marketingSocialInstagram">◎</span>
        </div>
      </div>
    ),
  },
] as const;

const proofPoints = [
  "Auto detect filler words, pauses, and bad takes",
  "Subtitle generation during the render flow",
  "Visual effects plus transition SFX from the same timeline",
  "One export path for TikTok, Facebook, YouTube, and Instagram",
];

export default function Home() {
  return (
    <>
      <section className="marketingHero">
        <div className="card marketingHeroCopy">
          <span className="badge">BestShotAI Studio</span>
          <h1>Video editing made simple.</h1>
          <p className="marketingLead">
            Allow AI to do the best shot. Remove filler words and pauses, generate subtitles, add automatic effects, and publish everywhere from one clean workflow.
          </p>
          <div className="marketingActionRow">
            <a href="/projects/new" className="btn">Start New Project</a>
            <a href="/pricing" className="btn secondary">See Pricing</a>
          </div>
          <div className="marketingProofList">
            {proofPoints.map((point) => (
              <div key={point} className="marketingProofItem">{point}</div>
            ))}
          </div>
        </div>

        <div className="card marketingHeroPreview">
          <div className="marketingPreviewTopline">
            <span className="marketingPreviewBadge">AI Edit Preview</span>
            <span className="marketingPreviewStatus">Ready to export</span>
          </div>
          <div className="marketingPreviewStage">
            <div className="marketingPreviewVideo">
              <div className="marketingPreviewOverlay">
                <div className="marketingPreviewCaption">Video editing made simple.</div>
                <div className="marketingPreviewCaption marketingPreviewCaptionAccent">AI picks the best shot.</div>
              </div>
            </div>
            <div className="marketingPreviewRail">
              <div className="marketingPreviewMetric">
                <b>12</b>
                <span>Filler cuts</span>
              </div>
              <div className="marketingPreviewMetric">
                <b>SRT</b>
                <span>Live subtitles</span>
              </div>
              <div className="marketingPreviewMetric">
                <b>SFX</b>
                <span>Transitions</span>
              </div>
            </div>
          </div>
          <div className="marketingPreviewTimeline">
            <span className="marketingPreviewSegment marketingPreviewSegmentKeep" style={{ width: "23%" }} />
            <span className="marketingPreviewSegment marketingPreviewSegmentDrop" style={{ width: "8%" }} />
            <span className="marketingPreviewSegment marketingPreviewSegmentKeep" style={{ width: "18%" }} />
            <span className="marketingPreviewSegment marketingPreviewSegmentAccent" style={{ width: "21%" }} />
            <span className="marketingPreviewSegment marketingPreviewSegmentKeep" style={{ width: "30%" }} />
          </div>
        </div>
      </section>

      <section className="marketingFeatureGrid">
        {featureCards.map((feature) => (
          <article key={feature.name} className="card marketingFeatureCard">
            <div className="marketingFeatureHead">
              <span className="marketingFeatureEyebrow">{feature.eyebrow}</span>
              <h2>{feature.name}</h2>
            </div>
            <p className="muted">{feature.description}</p>
            {feature.demo}
          </article>
        ))}
      </section>

      <section className="card marketingWorkflow">
        <div className="marketingWorkflowCopy">
          <span className="badge">Workflow</span>
          <h2>From raw clip to social-ready post.</h2>
          <p className="muted">
            Upload the footage, let AI clean the speech and choose the strongest moments, then review subtitles, effects, and export destinations in one place.
          </p>
        </div>
        <div className="marketingWorkflowSteps">
          <div className="marketingWorkflowStep"><strong>1</strong><span>Upload footage</span></div>
          <div className="marketingWorkflowStep"><strong>2</strong><span>AI removes pauses and filler words</span></div>
          <div className="marketingWorkflowStep"><strong>3</strong><span>Subtitles and effects are generated</span></div>
          <div className="marketingWorkflowStep"><strong>4</strong><span>Export to every social channel</span></div>
        </div>
      </section>
    </>
  );
}
