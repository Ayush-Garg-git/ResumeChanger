// Sticky bar at the bottom of the suggestions: what will happen, at a glance.
// Only "change" and "remove" decisions will ever be applied (Milestone 5).

export default function DecisionSummary({ total, summary }) {
  const counts = summary?.counts ?? { change: 0, remove: 0, keep: 0, undecided: total };
  const changes = summary?.changes ?? [];

  return (
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
        Lines you don't decide on stay exactly as they are. Nothing in your file has
        changed yet: applying these to your Word document comes next (Milestone 5).
      </p>
    </div>
  );
}
