// The ATS check: facts we can verify about how recruiters' software will
// read this resume. Worked out on our server, with no AI and no "score".

const ICONS = { ok: "✓", warn: "!", info: "i" };

export default function AtsPanel({ ats }) {
  if (!ats) return null;
  const { found, missing } = ats.keywords;

  return (
    <details className="block ats" open>
      <summary>ATS check (how recruiters' software reads your resume)</summary>

      {(found.length > 0 || missing.length > 0) && (
        <>
          <p className="label">The job's keywords</p>
          <p className="chips">
            {found.map((k) => (
              <span key={`f-${k}`} className="chip chip-found" title="Already in your resume">
                ✓ {k}
              </span>
            ))}
            {missing.map((k) => (
              <span key={`m-${k}`} className="chip chip-missing" title="Not in your resume">
                {k}
              </span>
            ))}
          </p>
          <p className="muted small">
            Green: already in your resume. Grey: not found.
            {missing.length > 0 &&
              " Missing keywords are never added for you, and some are alternatives (\"MySQL or MongoDB\"), so you don't need them all. If you really have one, answer the questions below or edit a line yourself."}{" "}
            Keywords are picked out automatically, so a few may be names rather than skills.
          </p>
        </>
      )}

      <p className="label">Layout</p>
      <ul className="ats-findings">
        {ats.formatting.map((f, i) => (
          <li key={i} className={`finding finding-${f.level}`}>
            <span className="finding-icon" aria-hidden="true">
              {ICONS[f.level]}
            </span>
            <span>{f.text}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
