import { useState } from "react";
import { decideSuggestion } from "../api.js";
import SuggestionCard from "./SuggestionCard.jsx";
import DecisionSummary from "./DecisionSummary.jsx";

const COVERAGE_LABELS = {
  clear: "Shown",
  partial: "Partly shown",
  missing: "Not shown",
};

export default function AnalysisView({ sessionId, fileName, analysis, onStartOver }) {
  const { suggestions, requirements, dropped, questions_for_user: questions } = analysis;

  // Decisions as saved by the server: { s1: {action, effect, final_text}, ... }
  // Missing means "not decided", which means the line stays unchanged.
  const [decisions, setDecisions] = useState({});
  const [summary, setSummary] = useState(null);
  // Result of "Create refined resume". Cleared whenever a decision changes,
  // because the server withdraws the old file at that point too.
  const [applyResult, setApplyResult] = useState(null);

  async function decide(suggestionId, action, text) {
    // The server checks the decision first; we only update the screen
    // once it has been saved. Errors are shown on the card.
    const result = await decideSuggestion(sessionId, suggestionId, action, text);
    setDecisions((prev) => {
      const next = { ...prev };
      if (result.decision) next[suggestionId] = result.decision;
      else delete next[suggestionId];
      return next;
    });
    setSummary(result.summary);
    setApplyResult(null);
  }

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
              <SuggestionCard key={s.id} s={s} decision={decisions[s.id]} onDecide={decide} />
            ))}
          </ul>
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

      {suggestions.length > 0 && (
        <DecisionSummary
          total={suggestions.length}
          summary={summary}
          sessionId={sessionId}
          fileName={fileName}
          result={applyResult}
          onResult={setApplyResult}
        />
      )}
    </section>
  );
}
