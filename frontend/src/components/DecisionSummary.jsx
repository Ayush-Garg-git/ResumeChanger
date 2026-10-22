import { useState } from "react";
import { applyDecisions, downloadRefined } from "../api.js";
import PreviewPanel from "./PreviewPanel.jsx";

// Sticky bar at the bottom of the suggestions: what will happen, at a glance,
// and the button that writes the approved changes into a copy of the file.

function ApplyResult({ result, sessionId, fileName, onDecide, onRetry, retrying }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");

  async function download() {
    setDownloading(true);
    setError("");
    try {
      await downloadRefined(sessionId, fileName);
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloading(false);
    }
  }

  const { applied, skipped, validation, download_ready: ready } = result;

  return (
    <div className="apply-result">
      <p className={`message ${ready ? "message-ok" : "message-error"}`}>
        {ready
          ? `Your refined resume is ready: ${applied.length} change${applied.length === 1 ? "" : "s"} applied and checked.`
          : applied.length === 0
            ? "None of the approved changes could be applied safely, so no file was created."
            : "The refined file didn't pass our checks, so it isn't offered for download. Your original is unchanged."}
      </p>

      <ul className="checks">
        {validation.checks.map((c) => (
          <li key={c.name} className={c.passed ? "check-pass" : "check-fail"}>
            <span aria-hidden="true">{c.passed ? "✓" : "✗"}</span> {c.name}
            {c.detail && <span className="muted small"> ({c.detail})</span>}
          </li>
        ))}
      </ul>

      {skipped.length > 0 && (
        <div className="message message-warn">
          <p>
            <strong>
              {skipped.length} change{skipped.length === 1 ? " was" : "s were"} not applied:
            </strong>
          </p>
          {skipped.map((s) => (
            <p key={s.suggestion_id}>
              <code className="pid">{s.paragraph_id}</code> {s.why}
            </p>
          ))}
        </div>
      )}

      {ready && (
        <button className="button-primary" onClick={download} disabled={downloading}>
          {downloading ? "Preparing download…" : "Download refined resume (.docx)"}
        </button>
      )}
      {error && (
        <p className="message message-error" role="alert">
          {error}
        </p>
      )}
      {!ready && (
        <button className="btn" onClick={onRetry} disabled={retrying}>
          {retrying ? "Trying again…" : "Try again"}
        </button>
      )}

      {ready && <PreviewPanel sessionId={sessionId} onDecide={onDecide} />}
    </div>
  );
}

export default function DecisionSummary({
  total, summary, sessionId, fileName, result, onResult, onDecide, creating, onCreatingChange,
}) {
  const [error, setError] = useState("");
  const busy = creating;
  const setBusy = onCreatingChange;

  const counts = summary?.counts ?? { change: 0, remove: 0, keep: 0, undecided: total };
  const changes = summary?.changes ?? [];

  async function create() {
    setBusy(true);
    setError("");
    try {
      onResult(await applyDecisions(sessionId));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <div className="decision-summary">
      <div className="summary-counts">
        <span><strong>{counts.change}</strong> to change</span>
        <span><strong>{counts.remove}</strong> to remove</span>
        <span><strong>{counts.keep}</strong> kept</span>
        <span className={counts.undecided ? "undecided" : ""}>
          <strong>{counts.undecided}</strong> not decided
        </span>
      </div>

      {changes.length > 0 && (
        <details>
          <summary>What will be applied ({changes.length})</summary>
          <ol className="apply-list">
            {changes.map((c) => (
              <li key={c.suggestion_id}>
                <code className="pid">{c.paragraph_id}</code>{" "}
                {c.effect === "remove" ? (
                  <span className="diff-removed">{c.original_text}</span>
                ) : (
                  <span>{c.final_text}</span>
                )}
                {c.edited_by_user && <span className="muted small"> (your edit)</span>}
              </li>
            ))}
          </ol>
        </details>
      )}

      <p className="muted small">
        Lines you don't decide on stay exactly as they are. Your original file is
        never changed: we write the approved changes into a copy.
      </p>

      {!result && (
        <button className="button-primary" onClick={create} disabled={busy || changes.length === 0}>
          {busy ? "Creating…" : "Create refined resume"}
        </button>
      )}
      {error && (
        <p className="message message-error" role="alert">
          {error}
        </p>
      )}

    </div>

    {/* Outside the sticky bar: the result and preview are normal page content. */}
    {result && (
      <ApplyResult
        result={result}
        sessionId={sessionId}
        fileName={fileName}
        onDecide={onDecide}
        onRetry={create}
        retrying={busy}
      />
    )}
    </>
  );
}
