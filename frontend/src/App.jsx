import { useEffect, useState } from "react";
import { checkHealth, deleteResume } from "./api.js";
import UploadStep from "./components/UploadStep.jsx";
import ResumeOutline from "./components/ResumeOutline.jsx";

export default function App() {
  // Backend connection: "checking", "ok" or "error"
  const [status, setStatus] = useState("checking");
  const [info, setInfo] = useState(null);
  const [healthError, setHealthError] = useState("");

  // The uploaded resume. null means nothing is uploaded yet.
  const [resume, setResume] = useState(null); // { outline, fileName }
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState("");

  async function runCheck() {
    setStatus("checking");
    setHealthError("");
    try {
      setInfo(await checkHealth());
      setStatus("ok");
    } catch (err) {
      setHealthError(err.message);
      setStatus("error");
    }
  }

  useEffect(() => {
    runCheck();
  }, []);

  async function handleRemove() {
    setRemoving(true);
    setRemoveError("");
    try {
      await deleteResume(resume.outline.session_id);
      setResume(null);
    } catch (err) {
      // "already removed" (expired) is fine: the goal was for it to be gone.
      if (err.message.includes("already removed")) {
        setResume(null);
      } else {
        setRemoveError(err.message);
      }
    } finally {
      setRemoving(false);
    }
  }

  return (
    <main className="page">
      <header>
        <h1>AI Resume Refiner</h1>
        <p className="subtitle">
          Tailor your existing resume to a job description. You approve every
          change.
        </p>
      </header>

      <div className={`status status-${status}`} aria-live="polite">
        {status === "checking" && <span>Connecting…</span>}
        {status === "ok" && (
          <span>
            Connected · v{info.version}
          </span>
        )}
        {status === "error" && (
          <>
            <span>{healthError}</span>
            <button onClick={runCheck}>Try again</button>
          </>
        )}
      </div>

      {status === "ok" && !resume && (
        <UploadStep
          onUploaded={(outline, fileName) => setResume({ outline, fileName })}
        />
      )}

      {resume && (
        <>
          {removeError && (
            <p className="message message-error" role="alert">
              {removeError}
            </p>
          )}
          <ResumeOutline
            outline={resume.outline}
            fileName={resume.fileName}
            onRemove={handleRemove}
            removing={removing}
          />
          <section className="card card-next">
            <h2>2. Paste the job description</h2>
            <p className="muted">Coming in Milestone 3.</p>
          </section>
        </>
      )}
    </main>
  );
}
