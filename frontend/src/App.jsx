import { useEffect, useState } from "react";
import { checkHealth, deleteResume } from "./api.js";
import UploadStep from "./components/UploadStep.jsx";
import ResumeOutline from "./components/ResumeOutline.jsx";
import JobDescriptionStep from "./components/JobDescriptionStep.jsx";
import AnalysisView from "./components/AnalysisView.jsx";

// On Render's free plan the server sleeps when idle and takes up to about
// a minute to wake. We retry the first connection a few times and say what's
// happening, instead of showing an error straight away.
const HEALTH_RETRIES = 4;
const SLOW_START_MS = 4000;

export default function App() {
  // Backend connection: "checking", "waking", "ok" or "error"
  const [status, setStatus] = useState("checking");
  const [info, setInfo] = useState(null);
  const [healthError, setHealthError] = useState("");

  // The uploaded resume. null means nothing is uploaded yet.
  const [resume, setResume] = useState(null); // { outline, fileName }
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [expired, setExpired] = useState(false);

  // The AI's analysis for the current job description. null until analysed.
  const [analysis, setAnalysis] = useState(null);

  async function runCheck() {
    setStatus("checking");
    setHealthError("");
    const slow = setTimeout(() => setStatus((s) => (s === "checking" ? "waking" : s)), SLOW_START_MS);
    try {
      for (let attempt = 1; ; attempt++) {
        try {
          setInfo(await checkHealth());
          setStatus("ok");
          return;
        } catch (err) {
          if (attempt >= HEALTH_RETRIES) throw err;
          setStatus("waking");
          await new Promise((r) => setTimeout(r, 3000 * attempt));
        }
      }
    } catch (err) {
      setHealthError(err.message);
      setStatus("error");
    } finally {
      clearTimeout(slow);
    }
  }

  useEffect(() => {
    runCheck();
  }, []);

  // Any request that finds the session gone sends this event (see api.js).
  useEffect(() => {
    function onExpired() {
      setResume(null);
      setAnalysis(null);
      setExpired(true);
    }
    window.addEventListener("session-expired", onExpired);
    return () => window.removeEventListener("session-expired", onExpired);
  }, []);

  async function handleRemove() {
    setRemoving(true);
    setRemoveError("");
    try {
      await deleteResume(resume.outline.session_id);
      setResume(null);
      setAnalysis(null);
    } catch (err) {
      // "already removed" (expired) is fine: the goal was for it to be gone.
      if (err.message.includes("already removed")) {
        setResume(null);
        setAnalysis(null);
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
        {status === "waking" && (
          <span>Waking up the server. On the free plan this can take up to a minute…</span>
        )}
        {status === "ok" && <span>Connected · v{info.version}</span>}
        {status === "error" && (
          <>
            <span>{healthError}</span>
            <button onClick={runCheck}>Try again</button>
          </>
        )}
      </div>

      {expired && !resume && (
        <div className="message message-warn expired-banner" role="alert">
          <p>
            <strong>Your session ended.</strong> For your privacy, uploaded resumes are
            deleted {info?.session_minutes ?? 60} minutes after your last action. Please
            upload your resume again to continue.
          </p>
          <button className="btn" onClick={() => setExpired(false)}>
            OK
          </button>
        </div>
      )}

      {status === "ok" && !resume && (
        <UploadStep
          minutes={info?.session_minutes ?? 60}
          onUploaded={(outline, fileName) => {
            setExpired(false);
            setResume({ outline, fileName });
          }}
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
            collapsed={analysis !== null}
          />
          {!analysis && (
            <JobDescriptionStep
              sessionId={resume.outline.session_id}
              ai={info?.ai}
              onAnalysed={setAnalysis}
            />
          )}
          {analysis && (
            <AnalysisView
              sessionId={resume.outline.session_id}
              fileName={resume.fileName}
              analysis={analysis}
              onStartOver={() => setAnalysis(null)}
            />
          )}
        </>
      )}

      <footer className="footer">
        <p>
          <strong>Your privacy.</strong> Your resume is stored on our server only while
          you work on it, and deleted when you click Remove or{" "}
          {info?.session_minutes ?? 60} minutes after your last action. Nothing is sent to
          the AI until you agree. We don't keep copies of your files or job descriptions,
          and our logs never contain their text.
        </p>
      </footer>
    </main>
  );
}
