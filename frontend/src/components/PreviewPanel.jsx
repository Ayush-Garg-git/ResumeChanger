import { useEffect, useState } from "react";
import { checkPageFit, loadPreview } from "../api.js";

// Before/after page pictures, page counts, and the page-limit check.
// Nothing here changes the resume by itself: page-limit options only record
// a decision when the user clicks one, exactly like the suggestion cards.

const LIMIT_KEY = "resume-refiner:max-pages";

function readSavedLimit() {
  try {
    return localStorage.getItem(LIMIT_KEY) || "";
  } catch {
    return ""; // storage can be blocked; the feature still works
  }
}

function saveLimit(value) {
  try {
    localStorage.setItem(LIMIT_KEY, value);
  } catch {
    // ignore
  }
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function PageColumn({ title, info, maxShown }) {
  return (
    <div className="preview-col">
      <p className="preview-title">
        <strong>{title}</strong> <span className="muted">· about {plural(info.pages, "page")}</span>
      </p>
      {info.images.map((src, i) => (
        <img key={src} src={src} alt={`${title}, page ${i + 1}`} className="preview-page" loading="lazy" />
      ))}
      {info.pages > maxShown && (
        <p className="muted small">Showing the first {maxShown} pages.</p>
      )}
    </div>
  );
}

function PageLimit({ sessionId, pages, onDecide }) {
  const [limit, setLimit] = useState(readSavedLimit);
  const [fit, setFit] = useState(null);
  const [error, setError] = useState("");
  const [keepLonger, setKeepLonger] = useState(false);
  const [busyId, setBusyId] = useState("");

  useEffect(() => {
    setFit(null);
    setError("");
    if (!limit) return;
    checkPageFit(sessionId, Number(limit)).then(setFit, (err) => setError(err.message));
  }, [sessionId, limit, pages]);

  async function choose(option) {
    setBusyId(option.suggestion_id);
    try {
      // Same as clicking Accept / Remove line on the suggestion card.
      await onDecide(option.suggestion_id, option.action, null);
    } catch (err) {
      setError(err.message);
      setBusyId("");
    }
  }

  function goToCard(suggestionId) {
    document.getElementById(`card-${suggestionId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className="page-limit">
      <label className="limit-label">
        Maximum pages{" "}
        <select
          value={limit}
          onChange={(e) => {
            setLimit(e.target.value);
            saveLimit(e.target.value);
            setKeepLonger(false);
          }}
        >
          <option value="">No limit</option>
          <option value="1">1 page</option>
          <option value="2">2 pages</option>
          <option value="3">3 pages</option>
        </select>
      </label>

      {error && <p className="message message-error">{error}</p>}

      {fit?.fits && (
        <p className="message message-ok">
          Fits your limit: about {plural(fit.pages, "page")} (limit {fit.max_pages}).
        </p>
      )}

      {fit && !fit.fits && keepLonger && (
        <p className="message message-ok">
          Keeping it at about {plural(fit.pages, "page")}, as you chose.
        </p>
      )}

      {fit && !fit.fits && !keepLonger && (
        <div className="message message-warn fit-box">
          <p>
            <strong>
              Your refined resume is about {plural(fit.pages, "page")}, {plural(fit.over_by, "page")} over
              your limit.
            </strong>{" "}
            We won't delete anything or shrink fonts. Here are options; you decide.
          </p>
          <ul className="fit-options">
            {fit.options.map((o) => {
              if (o.type === "remove" || o.type === "shorten") {
                return (
                  <li key={o.suggestion_id}>
                    <p>
                      <strong>{o.type === "remove" ? "Remove a less relevant line" : "Shorten a line"}</strong>{" "}
                      <code className="pid">{o.paragraph_id}</code>
                      {o.previously_kept && <span className="muted small"> (you chose to keep this earlier)</span>}
                    </p>
                    <p className="muted small">{o.type === "remove" ? o.text : o.proposed_text}</p>
                    <button className="btn" disabled={!!busyId} onClick={() => choose(o)}>
                      {busyId === o.suggestion_id ? "Saving…" : o.type === "remove" ? "Remove this line" : "Use the shorter version"}
                    </button>
                  </li>
                );
              }
              if (o.type === "review") {
                return (
                  <li key={o.suggestion_id}>
                    <p>
                      <strong>Review a line</strong> <code className="pid">{o.paragraph_id}</code>
                    </p>
                    <p className="muted small">{o.text}</p>
                    <button className="btn" onClick={() => goToCard(o.suggestion_id)}>
                      Show it above to edit
                    </button>
                  </li>
                );
              }
              if (o.type === "adjust_layout") {
                return (
                  <li key="layout">
                    <p><strong>Adjust spacing or layout yourself</strong></p>
                    <p className="muted small">
                      In Word, slightly smaller margins or line spacing can bring it back to{" "}
                      {plural(fit.max_pages, "page")}. We don't change your design automatically.
                    </p>
                  </li>
                );
              }
              return (
                <li key="keep">
                  <p><strong>Keep everything</strong></p>
                  <button className="btn" onClick={() => setKeepLonger(true)}>
                    Keep it at about {plural(fit.pages, "page")}
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="muted small">
            Removing or shortening a line may not always save a full page. After choosing,
            click "Create refined resume" again to see the new page count.
          </p>
        </div>
      )}
    </div>
  );
}

export default function PreviewPanel({ sessionId, onDecide }) {
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    loadPreview(sessionId).then(
      (p) => active && setPreview(p),
      (err) => active && setError(err.message)
    );
    return () => {
      active = false;
    };
  }, [sessionId]);

  if (error) {
    const fileStillThere = !/upload it again|create (it|the refined resume) again|changed/i.test(error);
    return (
      <p className="message message-warn">
        Preview: {error}
        {fileStillThere && " You can still download the file."}
      </p>
    );
  }
  if (!preview) {
    return (
      <p className="message preview-loading">
        Drawing the before and after pages… this can take up to a minute on the free server.
      </p>
    );
  }
  if (!preview.available) {
    return (
      <p className="message message-warn">
        {preview.message} Open the downloaded file in Word to check it.
      </p>
    );
  }

  return (
    <div className="preview">
      <h3 className="block-title">Before and after</h3>
      <p className="muted small legend">
        <span className="swatch swatch-yellow" /> changed
        <span className="swatch swatch-red" /> removed
        <span className="legend-note">Highlights are only in this preview, not in your download.</span>
      </p>

      <PageLimit sessionId={sessionId} pages={preview.after.pages} onDecide={onDecide} />

      <div className="preview-grid">
        <PageColumn title="Before" info={preview.before} maxShown={preview.max_preview_pages} />
        <PageColumn title="After" info={preview.after} maxShown={preview.max_preview_pages} />
      </div>
      <p className="muted small">
        Page counts are close estimates: Word may lay out text slightly differently,
        especially with fonts we don't have.
      </p>
    </div>
  );
}
