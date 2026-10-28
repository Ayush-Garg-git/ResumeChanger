import { useState } from "react";
import { submitAnswers } from "../api.js";
import useElapsed from "../useElapsed.js";

// Milestone 4b: the user answers the AI's questions in their own words.
// "No" answers never leave this page. "Yes" answers go to the AI, which
// suggests where each fits; the backend drops anything you didn't say.

const MAX_CHARS = 500;

export default function QuestionsPanel({ sessionId, analysis, ai, onAnswered }) {
  const questions = analysis.questions ?? [];
  // { q1: { has_it: true|false, details: "" } }
  const [answers, setAnswers] = useState({});
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const elapsed = useElapsed(busy);

  if (questions.length === 0) return null;

  function setAnswer(id, patch) {
    setAnswers((prev) => ({ ...prev, [id]: { has_it: false, details: "", ...prev[id], ...patch } }));
  }

  const yesAnswers = Object.entries(answers).filter(([, a]) => a.has_it);
  const missingDetails = yesAnswers.some(([, a]) => !a.details.trim());
  const canSubmit = yesAnswers.length > 0 && !missingDetails && consent && !busy;

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const payload = Object.entries(answers).map(([question_id, a]) => ({
        question_id,
        has_it: a.has_it,
        details: a.has_it ? a.details : "",
      }));
      const res = await submitAnswers(sessionId, analysis.analysis_id, payload, consent, ai?.provider);
      setResult(res);
      onAnswered(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const questionText = Object.fromEntries(questions.map((q) => [q.id, q.text]));

  return (
    <div className="block questions-panel">
      <h3 className="block-title">Questions for you</h3>
      <p className="muted small">
        The job asks for these, but your resume doesn't show them. If you have the
        experience, say where and how in your own words, and we'll suggest where it
        fits. Only what you write here can be added. If you don't have it, choose No:
        nothing is added.
      </p>

      <ul className="questions">
        {questions.map((q) => {
          const a = answers[q.id];
          return (
            <li key={q.id}>
              <p className="question-text">{q.text}</p>
              <div className="yes-no" role="radiogroup" aria-label={q.text}>
                <label>
                  <input
                    type="radio"
                    name={`answer-${q.id}`}
                    checked={a?.has_it === false}
                    onChange={() => setAnswer(q.id, { has_it: false })}
                    disabled={busy}
                  />
                  No
                </label>
                <label>
                  <input
                    type="radio"
                    name={`answer-${q.id}`}
                    checked={a?.has_it === true}
                    onChange={() => setAnswer(q.id, { has_it: true })}
                    disabled={busy}
                  />
                  Yes
                </label>
              </div>
              {a?.has_it && (
                <>
                  <textarea
                    className="jd-input answer-input"
                    rows={2}
                    maxLength={MAX_CHARS}
                    value={a.details}
                    onChange={(e) => setAnswer(q.id, { details: e.target.value })}
                    placeholder="Where and how? e.g. Built two sales dashboards in Tableau during my internship"
                    disabled={busy}
                  />
                  <p className="counter">
                    {a.details.length} / {MAX_CHARS}
                  </p>
                </>
              )}
            </li>
          );
        })}
      </ul>

      {yesAnswers.length > 0 && (
        <div className="consent">
          <p>
            Your resume and your Yes answers will be sent to {ai?.provider ?? "the AI service"}{" "}
            again, with the same privacy rules as before. Your No answers are not sent.
          </p>
          <label className="consent-check">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              disabled={busy}
            />
            I understand and want to send my answers.
          </label>
        </div>
      )}

      {error && (
        <p className="message message-error" role="alert">
          {error}
        </p>
      )}

      <button className="button-primary" onClick={submit} disabled={!canSubmit}>
        {busy ? `Finding where they fit… ${elapsed}s` : "Suggest where to add these"}
      </button>
      {missingDetails && (
        <p className="muted small">For each Yes, add a few words about where or how you used it.</p>
      )}

      {result && (
        <div className="answers-result">
          {result.added.length > 0 && (
            <p className="message message-ok">
              {result.added.length} new suggestion{result.added.length === 1 ? "" : "s"} added
              above, marked "From your answer". Accept, edit or reject each one.
            </p>
          )}
          {result.not_placed.length > 0 && (
            <div className="message message-warn">
              {result.not_placed.map((n) => (
                <p key={n.question_id}>
                  <strong>{questionText[n.question_id] ?? n.question_id}</strong> {n.reason}
                </p>
              ))}
            </div>
          )}
          {result.dropped.length > 0 && (
            <details className="block">
              <summary>
                {result.dropped.length} idea{result.dropped.length === 1 ? "" : "s"} removed by our
                safety checks
              </summary>
              <ul className="dropped">
                {result.dropped.map((d, i) => (
                  <li key={i}>
                    <code className="pid">{d.paragraph_id}</code> {d.why}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
