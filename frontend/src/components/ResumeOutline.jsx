// Shows how the backend "sees" the resume: sections, and each paragraph
// with its ID. In later milestones, suggestions will point at these IDs.

function Tag({ children }) {
  return <span className="tag">{children}</span>;
}

export default function ResumeOutline({ outline, fileName, onRemove, removing }) {
  // Group paragraphs by section, keeping the order they appear in.
  const groups = outline.sections.map((section) => ({
    section,
    paragraphs: outline.paragraphs.filter((p) => p.section === section),
  }));

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>What we read from your resume</h2>
          <p className="muted">
            {fileName} · {outline.paragraphs.length} paragraphs ·{" "}
            {outline.sections.length} sections
          </p>
        </div>
        <button className="button-secondary" onClick={onRemove} disabled={removing}>
          {removing ? "Removing…" : "Remove"}
        </button>
      </div>

      {outline.warnings.length > 0 && (
        <div className="message message-warn">
          {outline.warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}

      <p className="note">
        Sections are our best guess from your headings. If something is in the
        wrong section, that's fine for now: nothing in your file has been
        changed.
      </p>

      {groups.map(({ section, paragraphs }) => (
        <div key={section} className="outline-section">
          <h3>{section}</h3>
          <ul className="outline-list">
            {paragraphs.map((p) => (
              <li key={p.id} className={p.is_heading ? "outline-heading" : ""}>
                <code className="pid">{p.id}</code>
                <span className="ptext">{p.text}</span>
                <span className="ptags">
                  {p.is_heading && <Tag>heading</Tag>}
                  {p.is_list_item && <Tag>bullet</Tag>}
                  {p.in_table && <Tag>table</Tag>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
