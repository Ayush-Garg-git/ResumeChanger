import { useState } from "react";
import { decideSuggestion } from "../api.js";
import SectionBlock from "./SectionBlock.jsx";
import AtsPanel from "./AtsPanel.jsx";
import DecisionSummary from "./DecisionSummary.jsx";
import QuestionsPanel from "./QuestionsPanel.jsx";

const COVERAGE_LABELS = {
  clear: "Shown",
  partial: "Partly shown",
  missing: "Not shown",
};

export default function AnalysisView({ sessionId, fileName, analysis, ai, onAnalysisChange, onStartOver }) {
  const { suggestions, requirements, dropped, questions_for_user: questions } = analysis;

  // Decisions as saved by the server: { s1: {action, effect, final_text}, ... }
  // Missing means "not decided", which means the line stays unchanged.
  const [decisions, setDecisions] = useState({});
  const [summary, setSummary] = useState(null);
  // Result of "Create refined resume". Cleared whenever a decision changes,
  // because the server withdraws the old file at that point too.
  const [applyResult, setApplyResult] = useState(null);
  // While "Create refined resume" runs, the cards are locked, so a click
  // can't change a decision halfway through creating the file.
  const [creating, setCreating] = useState(false);

  // Every analysed section in resume order, each with its review and its
  // suggestions. Older saved analyses have no sections: one plain group.
  const sections = analysis.sections ?? [{ name: "Suggested changes", paragraph_ids: null }];
  const reviews = Object.fromEntries((analysis.section_reviews ?? []).map((r) => [r.section, r]));
  const sectionOf = {};
  sections.forEach((sec) => (sec.paragraph_ids ?? []).forEach((pid) => (sectionOf[pid] = sec.name)));
  const groups = sections.map((sec) => ({
    ...sec,
    suggestions: suggestions.filter((s) =>
      sec.paragraph_ids === null ? true : sectionOf[s.paragraph_id] === sec.name
    ),
  }));
  const ungrouped = analysis.sections ? suggestions.filter((s) => !sectionOf[s.paragraph_id]) : [];
  if (ungrouped.length > 0) groups.push({ name: "Other changes", key: "__ungrouped", paragraph_ids: [], suggestions: ungrouped });

  async function decide(suggestionId, action, text) {
    // The server checks the decision first; we only update the screen
    // once it has been saved. Errors are shown on the card.
    const result = await decideSuggestion(sessionId, analysis.analysis_id, suggestionId, action, text);
    setDecisions((prev) => {
      // `changed`: other cards for the same line that this decision replaced
      // (e.g. your own Edit replaces an earlier accepted change).
      const next = { ...prev, ...(result.changed ?? {}) };
      if (result.decision) next[suggestionId] = result.decision;
      else delete next[suggestionId];
      return next;
    });
    setSummary(result.summary);
    setApplyResult(null);
  }

  // New suggestions from the user's answers (Milestone 4b). Answering again
  // replaces the earlier answer cards; decisions on removed cards go too.
  function handleAnswered(result) {
    const stillThere = new Set(result.suggestions.map((s) => s.id));
    const lostDecision = Object.keys(decisions).some((id) => !stillThere.has(id));
    setDecisions((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => stillThere.has(id))));
    setSummary(result.summary);
    if (lostDecision) setApplyResult(null);
    onAnalysisChange({ ...analysis, suggestions: result.suggestions });
    if (result.added.length > 0) {
      setTimeout(() => {
        document.getElementById(`card-${result.added[0]}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 50);
    }
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

      {!analysis.no_changes_needed && suggestions.length === 0 && (
        <p className="message message-warn">
          We didn't find any safe changes to suggest for this job.
          {questions.length > 0 && " See the questions below about what the job asks for."}
          {dropped.length > 0 && " Some ideas were removed by our safety checks; you can see why at the bottom."}
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

      <AtsPanel ats={analysis.ats} />

      {(suggestions.length > 0 || analysis.sections) && (
        <>
          <h3 className="block-title">
            Section by section ({suggestions.length} suggested change{suggestions.length === 1 ? "" : "s"})
          </h3>
          <p className="muted small">
            Every change uses only facts already in your resume. Accept, edit or reject each
            one; nothing changes in your file unless you accept it.
          </p>
          {groups.map((g) => (
            <SectionBlock
              key={g.key ?? g.name}
              name={g.name}
              review={reviews[g.name]}
              suggestions={g.suggestions}
              decisions={decisions}
              onDecide={decide}
              locked={creating}
            />
          ))}
        </>
      )}

      <QuestionsPanel
        sessionId={sessionId}
        analysis={analysis}
        ai={ai}
        onAnswered={handleAnswered}
      />

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
          onDecide={decide}
          creating={creating}
          onCreatingChange={setCreating}
        />
      )}
    </section>
  );
}
