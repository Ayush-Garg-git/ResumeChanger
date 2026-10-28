import { useState } from "react";
import { diffWords, movedToFront } from "../diff.js";

const KIND_LABELS = {
  tailor: "Tailor to job",
  remove_items: "Remove items",
  shorten: "Shorten",
  reword: "Reword",
  emphasise: "Emphasise",
  consider_removing: "Consider removing",
  review: "Review",
  add_confirmed: "From your answer",
};

// Button labels depend on the kind, so each button says exactly what happens.
function actionsFor(kind) {
  if (kind === "remove_items") {
    return [
      { action: "accept", label: "Remove these", style: "danger" },
      { action: "edit", label: "Edit" },
      { action: "reject", label: "Keep all" },
    ];
  }
  if (kind === "consider_removing") {
    return [
      { action: "accept", label: "Remove line", style: "danger" },
      { action: "edit", label: "Edit" },
      { action: "reject", label: "Keep" },
    ];
  }
  if (kind === "review") {
    return [
      { action: "edit", label: "Edit" },
      { action: "reject", label: "Keep as is" },
    ];
  }
  return [
    { action: "accept", label: "Accept", style: "primary" },
    { action: "edit", label: "Edit" },
    { action: "reject", label: "Reject" },
  ];
}

function statusText(decision) {
  if (!decision) return null;
  if (decision.effect === "remove") return "Will be removed";
  if (decision.effect === "keep") return "Kept as is";
  return decision.action === "edit" ? "Will change (your edit)" : "Will change";
}

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

// "Tools: Git, VS Code (Pro), Excel" -> ["Git", "VS Code (Pro)", "Excel"],
// keeping commas inside brackets. Must match the backend's list reading.
function listItems(text) {
  const colon = text.indexOf(":");
  const rest = colon > 0 && colon <= 40 ? text.slice(colon + 1) : text;
  const items = [];
  let depth = 0;
  let current = "";
  for (const ch of rest.trim().replace(/\.$/, "")) {
    if ("([{".includes(ch)) depth += 1;
    if (")]}".includes(ch)) depth = Math.max(0, depth - 1);
    if (",;|".includes(ch) && depth === 0) {
      items.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  items.push(current.trim());
  // Collapse spaces (including non-breaking ones) exactly like the server.
  return items.map((i) => i.replace(/\s+/g, " ").trim()).filter(Boolean);
}

function removedItems(before, after) {
  const kept = new Set(listItems(after).map((i) => i.toLowerCase()));
  return listItems(before).filter((i) => !kept.has(i.toLowerCase()));
}

function Proposal({ s }) {
  if (s.proposed_text === null) return null;

  if (s.kind === "remove_items") {
    const removed = removedItems(s.original_text, s.proposed_text);
    return (
      <>
        {removed.length > 0 && (
          <p className="moved">
            Removes: <strong>{removed.join(", ")}</strong>
          </p>
        )}
        <p className="label">After</p>
        <Diff before={s.original_text} after={s.proposed_text} />
      </>
    );
  }

  if (s.kind === "emphasise") {
    const moved = movedToFront(s.original_text, s.proposed_text);
    return (
      <>
        <p className="label">Proposed (same words, new order)</p>
        {moved.length > 0 && (
          <p className="moved">
            Moved to the front: <strong>{moved.join(", ")}</strong>
          </p>
        )}
        <p className="diff">{s.proposed_text}</p>
      </>
    );
  }

  // Shortening may drop a number; say so plainly and always show the diff.
  const numbers = (t) => t.match(/\d+(?:[.,]\d+)?/g) ?? [];
  const lost = numbers(s.original_text).filter((n) => !numbers(s.proposed_text).includes(n));
  if (lost.length > 0) {
    return (
      <>
        <p className="moved">
          Removes the number{lost.length > 1 ? "s" : ""}: <strong>{lost.join(", ")}</strong>
        </p>
        <p className="label">Proposed (changes highlighted)</p>
        <Diff before={s.original_text} after={s.proposed_text} />
      </>
    );
  }

  // A big rewrite reads better as clean text; small edits read best as a diff.
  const words = (t) => t.split(/\s+/).filter(Boolean).length;
  let same = 0;
  let changed = 0;
  for (const part of diffWords(s.original_text, s.proposed_text)) {
    if (part.type === "same") same += 2 * words(part.text);
    else changed += words(part.text);
  }
  if (changed / Math.max(1, same + changed) > 0.35) {
    return (
      <>
        <p className="label">Proposed</p>
        <p className="diff">{s.proposed_text}</p>
        <details className="word-changes">
          <summary>Show word-by-word changes</summary>
          <Diff before={s.original_text} after={s.proposed_text} />
        </details>
      </>
    );
  }

  return (
    <>
      <p className="label">Proposed (changes highlighted)</p>
      <Diff before={s.original_text} after={s.proposed_text} />
    </>
  );
}

export default function SuggestionCard({ s, decision, onDecide, locked = false }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busyHere, setBusy] = useState(false);
  const busy = busyHere || locked;
  const [error, setError] = useState("");

  async function run(action, text = null) {
    setBusy(true);
    setError("");
    try {
      await onDecide(s.id, action, text);
      setEditing(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function startEdit() {
    // Start from the user's earlier edit, else the proposal, else the original.
    setDraft(decision?.action === "edit" ? decision.final_text : s.proposed_text ?? s.original_text);
    setError("");
    setEditing(true);
  }

  const status = statusText(decision);

  return (
    <li id={`card-${s.id}`} className={`suggestion ${decision ? `decided decided-${decision.effect}` : ""}`}>
      <div className="suggestion-head">
        <span className={`kind kind-${s.kind}`}>{KIND_LABELS[s.kind]}</span>
        <code className="pid">{s.paragraph_id}</code>
        {s.requirement && <span className="muted small">for: {s.requirement}</span>}
        {status && <span className={`status-pill pill-${decision.effect}`}>{status}</span>}
      </div>

      <p className="label">{s.proposed_text === null ? "Line" : "Original"}</p>
      <p className="original">{s.original_text}</p>

      {decision?.action === "edit" && !editing ? (
        <>
          <p className="label">Your version</p>
          <Diff before={s.original_text} after={decision.final_text} />
        </>
      ) : (
        <Proposal s={s} />
      )}

      {s.from_answer && (
        <p className="from-answer">
          <strong>You said: </strong>"{s.from_answer}"
        </p>
      )}

      <p className="reason">
        <strong>Why: </strong>
        {s.reason}
      </p>

      {editing ? (
        <div className="edit-box">
          <textarea
            className="jd-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            disabled={busy}
            autoFocus
          />
          <p className="muted small">
            Write it the way you want it in your resume. Your words are used as written.
          </p>
          <div className="actions">
            <button className="btn btn-primary" disabled={busy} onClick={() => run("edit", draft)}>
              Save my version
            </button>
            <button className="btn" disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="actions">
          {actionsFor(s.kind).map(({ action, label, style }) => {
            const chosen = decision?.action === action;
            return (
              <button
                key={action}
                className={`btn ${style ? `btn-${style}` : ""} ${chosen ? "btn-chosen" : ""}`}
                disabled={busy}
                aria-pressed={chosen}
                onClick={() => (action === "edit" ? startEdit() : run(action))}
              >
                {label}
              </button>
            );
          })}
          {decision && (
            <button className="btn-link" disabled={busy} onClick={() => run("undecided")}>
              Undo
            </button>
          )}
        </div>
      )}

      {error && (
        <p className="message message-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}
