import { diffWords } from "../diff.js";

const KIND_LABELS = {
  shorten: "Shorten",
  reword: "Reword",
  emphasise: "Emphasise",
  consider_removing: "Consider removing",
  review: "Review",
};

const COVERAGE_LABELS = {
  clear: "Shown",
  partial: "Partly shown",
  missing: "Not shown",
};

function Diff({ before, after }) {
  return (
    <p className="diff">
      {diffWords(before, after).map((part, i) => (
        <span key={i} className={`diff-${part.type}`}>
          {part.text}
        </span>
      ))}
    </p>
  );
}

function SuggestionCard({ s }) {
  const rewrites = s.proposed_text !== null;
  return (
    <li className="suggestion">
      <div className="suggestion-head">
        <span className={`kind kind-${s.kind}`}>{KIND_LABELS[s.kind]}</span>
        <code className="pid">{s.paragraph_id}</code>
        {s.requirement && <span className="muted small">for: {s.requirement}</span>}
      </div>

      {rewrites ? (
        <>
          <p className="label">Original</p>
          <p className="original">{s.original_text}</p>
          <p className="label">Proposed (changes highlighted)</p>
          <Diff before={s.original_text} after={s.proposed_text} />
        </>
      ) : (
        <>
          <p className="label">Line</p>
          <p className="original">{s.original_text}</p>
        </>
      )}

      <p className="reason">
        <strong>Why: </strong>
        {s.reason}
      </p>
    </li>
  );
}

export default function AnalysisView({ analysis, onStartOver }) {
  const { suggestions, requirements, dropped, questions_for_user: questions } = analysis;

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>3. Suggestions for: {analysis.role_title}</h2>
          <p className="muted">{analysis.fit_summary}</p>
        </div>
        <button className="button-secondary" onClick={onStartOver}>
          New job description
        </button>
      </div>

      {analysis.no_changes_needed && (
        <p className="message message-ok">
          Your resume already fits this job well. No changes are recommended.
        </p>
      )}

      {requirements.length > 0 && (
        <details className="block" open>
          <summary>What the job asks for ({requirements.length})</summary>
          <ul className="req-list">
            {requirements.map((r) => (
              <li key={r.requirement}>
                <span className={`cov cov-${r.coverage}`}>{COVERAGE_LABELS[r.coverage]}</span>
                <span className="req-text">
                  {r.requirement}
                  {r.importance === "nice_to_have" && <span className="muted"> (nice to have)</span>}
                </span>
                {r.evidence.length > 0 && (
                  <span className="muted small">{r.evidence.join(", ")}</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      {suggestions.length > 0 && (
        <>
          <h3 className="block-title">Suggested changes ({suggestions.length})</h3>
          <ul className="suggestion-list">
            {suggestions.map((s) => (
              <SuggestionCard key={s.id} s={s} />
            ))}
          </ul>
          <p className="note">
            Nothing has been changed in your file. Approving or rejecting each suggestion
            comes in the next step (Milestone 4).
          </p>
        </>
      )}

      {questions.length > 0 && (
        <div className="block">
          <h3 className="block-title">Questions for you</h3>
          <p className="muted small">
            The job asks for these, but your resume doesn't show them. We won't add
            anything you haven't confirmed.
          </p>
          <ul className="questions">
            {questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      {dropped.length > 0 && (
        <details className="block">
          <summary>
            {dropped.length} suggestion{dropped.length > 1 ? "s" : ""} removed by our safety
            checks
          </summary>
          <ul className="dropped">
            {dropped.map((d, i) => (
              <li key={i}>
                <code className="pid">{d.paragraph_id}</code> {d.why}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
