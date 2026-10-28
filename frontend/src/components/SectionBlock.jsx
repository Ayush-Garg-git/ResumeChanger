import SuggestionCard from "./SuggestionCard.jsx";

const RELEVANCE_LABELS = {
  high: "Very relevant",
  medium: "Partly relevant",
  low: "Less relevant",
};

// One resume section: the AI's short review of it, advice that is never
// applied automatically, and the suggested changes for its lines.
export default function SectionBlock({ name, review, suggestions, decisions, onDecide, locked }) {
  return (
    <section className="section-block" aria-label={name}>
      <div className="section-head">
        <h4>{name}</h4>
        {review && (
          <span className={`relevance relevance-${review.relevance}`}>
            {RELEVANCE_LABELS[review.relevance]}
          </span>
        )}
        <span className="muted small">
          {suggestions.length === 0
            ? "No changes"
            : `${suggestions.length} change${suggestions.length > 1 ? "s" : ""}`}
        </span>
      </div>

      {review?.verdict && <p className="section-verdict">{review.verdict}</p>}

      {review?.tips?.length > 0 && (
        <div className="tips">
          <p className="label">Advice (not applied automatically)</p>
          <ul>
            {review.tips.map((tip, i) => (
              <li key={i}>{tip}</li>
            ))}
          </ul>
        </div>
      )}

      {suggestions.length > 0 && (
        <ul className="suggestion-list">
          {suggestions.map((s) => (
            <SuggestionCard key={s.id} s={s} decision={decisions[s.id]} onDecide={onDecide} locked={locked} />
          ))}
        </ul>
      )}
    </section>
  );
}
