import { useState } from "react";
import { analyzeResume } from "../api.js";

// Must match the backend limits in config.py.
const MIN_CHARS = 80;
const MAX_CHARS = 15000;

export default function JobDescriptionStep({ sessionId, ai, onAnalysed }) {
  const [text, setText] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const length = text.trim().length;
  const tooShort = length < MIN_CHARS;
  const tooLong = length > MAX_CHARS;
  const canSubmit = ai?.configured && consent && !tooShort && !tooLong && !busy;

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      onAnalysed(await analyzeResume(sessionId, text, consent));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>2. Paste the job description</h2>

      <form onSubmit={submit}>
        <textarea
          className="jd-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste the full job posting here: title, responsibilities and requirements."
          rows={10}
          disabled={busy}
        />
        <p className={`counter ${tooLong ? "counter-bad" : ""}`}>
          {length.toLocaleString()} / {MAX_CHARS.toLocaleString()} characters
          {length > 0 && tooShort && " · please paste the full posting"}
        </p>

        {ai && !ai.configured && (
          <p className="message message-warn">
            AI analysis isn't set up on this server yet. The site owner needs to add a
            Gemini API key.
          </p>
        )}

        {ai?.configured && (
          <div className="consent">
            <p>
              <strong>Before you continue:</strong> the text of your resume and this job
              description will be sent to <strong>{ai.provider}</strong> ({ai.model}) for
              analysis. We don't send your name, the contact block at the top, or
              sections such as Personal Details, Declaration or References, and we mask
              emails, phone numbers, links and ID numbers we recognise. Automatic masking
              can miss unusual formats, so only continue if you're comfortable with the
              rest of your resume being sent.
              {ai.tier === "free" ? (
                <>
                  {" "}This site uses Google's <strong>free tier</strong>, where Google may
                  use the content to improve its products.
                </>
              ) : (
                <> On the paid tier, Google does not use it to improve its products.</>
              )}
            </p>
            <label className="consent-check">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                disabled={busy}
              />
              I understand and want to send my resume for analysis.
            </label>
          </div>
        )}

        {error && (
          <p className="message message-error" role="alert">
            {error}
          </p>
        )}

        <button className="button-primary" type="submit" disabled={!canSubmit}>
          {busy ? "Analysing… this can take up to a minute" : "Analyse resume"}
        </button>
      </form>
    </section>
  );
}
