import { useEffect, useState } from "react";
import { checkHealth } from "./api.js";

// The workflow from the project brief. In Milestone 1 these are only
// labels; each one becomes a real screen in later milestones.
const STEPS = [
  "Upload resume",
  "Paste job description",
  "Analyse",
  "Review suggestions",
  "Approve or reject",
  "Preview",
  "Download",
];

export default function App() {
  // status is one of: "checking", "ok", "error"
  const [status, setStatus] = useState("checking");
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");

  async function runCheck() {
    setStatus("checking");
    setError("");
    try {
      const data = await checkHealth();
      setInfo(data);
      setStatus("ok");
    } catch (err) {
      setError(err.message);
      setStatus("error");
    }
  }

  // useEffect with an empty list runs once, when the page first loads.
  useEffect(() => {
    runCheck();
  }, []);

  return (
    <main className="page">
      <header>
        <h1>AI Resume Refiner</h1>
        <p className="subtitle">
          Tailor your existing resume to a job description. You approve every
          change.
        </p>
      </header>

      <section className={`status status-${status}`} aria-live="polite">
        {status === "checking" && <span>Checking backend connection…</span>}
        {status === "ok" && (
          <span>
            Backend connected · {info.app} v{info.version}
          </span>
        )}
        {status === "error" && (
          <>
            <span>{error}</span>
            <button onClick={runCheck}>Try again</button>
          </>
        )}
      </section>

      <ol className="steps">
        {STEPS.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </main>
  );
}
