// Every call from the frontend to the backend lives in this file.
// Components never call fetch() directly. If a URL or error format
// changes, there is exactly one place to update.

// The backend says this when a session has been deleted or has expired.
// App.jsx listens for the "session-expired" event and offers a fresh start,
// so the user is never stuck on a screen whose resume no longer exists.
const EXPIRED_HINT = "upload it again";

function announceIfExpired(status, detail) {
  if (status === 404 && detail.toLowerCase().includes(EXPIRED_HINT)) {
    window.dispatchEvent(new CustomEvent("session-expired"));
  }
}

// A plain "no reply at all" failure (connection dropped, phone asleep,
// offline). Marked so the AI job loop can tell it apart from a real error
// and simply check again.
class NetworkError extends Error {}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    // fetch() only throws when no response arrived at all.
    throw new NetworkError("Could not reach the server. Check your connection and try again.");
  }

  // FastAPI sends errors as {"detail": "message"}. Show that message
  // when there is one, because the backend writes it for users.
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // The body wasn't JSON (for example, the dev proxy's own error page).
    }
    announceIfExpired(response.status, detail);
    if (detail) throw new Error(detail);
    if (response.status >= 500) {
      throw new Error("The server is not responding. Please try again shortly.");
    }
    throw new Error(`Request failed (${response.status}).`);
  }

  // 204 No Content has no body to read.
  if (response.status === 204) return null;
  return response.json();
}

export function checkHealth() {
  return request("/api/health");
}

export function uploadResume(file) {
  // FormData is how browsers send files: the same format a normal
  // <form> upload uses. FastAPI's UploadFile reads it on the other side.
  const form = new FormData();
  form.append("file", file);
  return request("/api/resume", { method: "POST", body: form });
}

// --- Slow AI steps run as background jobs on the server -------------------
// The page starts the job, then checks on it every 2 seconds. If a check
// fails because the connection dropped (phone locked, app switched, Wi-Fi
// to mobile data), we simply check again: the result waits on the server.
const CHECK_EVERY_MS = 2000;
// Longer than the server's own AI limit (100s), so the server normally
// reports a clear outcome first. Time the phone spends asleep doesn't count
// against this: we always check once more before giving up.
const GIVE_UP_AFTER_MS = 180_000;
// About 1½ minutes of checks with no reply at all.
const MAX_MISSED_CHECKS = 45;

// Waits `ms`, but wakes early when the page becomes visible again, so a
// phone coming back from sleep gets its result right away.
function pause(ms) {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      resolve();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") done();
    };
    const timer = setTimeout(done, ms);
    document.addEventListener("visibilitychange", onVisible);
  });
}

async function runJob(sessionId, startPath, body) {
  const started = await request(startPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const statusPath = `/api/resume/${sessionId}/jobs/${started.job_id}`;
  const deadline = Date.now() + GIVE_UP_AFTER_MS;
  let missed = 0;

  for (;;) {
    await pause(CHECK_EVERY_MS);
    let job;
    try {
      job = await request(statusPath);
      missed = 0;
    } catch (err) {
      if (!(err instanceof NetworkError)) throw err;
      missed += 1;
      if (missed >= MAX_MISSED_CHECKS) {
        throw new Error(
          "We lost the connection to the server. Check your internet connection and try again."
        );
      }
      continue; // the job keeps running on the server; check again
    }
    if (job.status === "done") return job.result;
    if (job.status === "failed") {
      announceIfExpired(job.status_code, job.detail || "");
      throw new Error(job.detail || "The analysis failed. Please try again.");
    }
    if (Date.now() > deadline) {
      throw new Error(
        "This is taking much longer than usual, so we stopped waiting. Please try again in a minute."
      );
    }
  }
}

// `provider`: the AI named in the notice the user agreed to. The server
// refuses if the site has since switched to a different AI.
export function analyzeResume(sessionId, jobDescription, consent, provider) {
  return runJob(sessionId, `/api/resume/${sessionId}/analyze`, {
    job_description: jobDescription,
    consent,
    provider,
  });
}

// action: "accept" | "reject" | "edit" | "undecided". text is only for "edit".
// analysisId ties the decision to the analysis it was made on.
export function decideSuggestion(sessionId, analysisId, suggestionId, action, text = null) {
  return request(`/api/resume/${sessionId}/suggestions/${suggestionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, text, analysis_id: analysisId }),
  });
}

// answers: [{ question_id, has_it, details }]. Only Yes answers are used.
export function submitAnswers(sessionId, analysisId, answers, consent, provider) {
  return runJob(sessionId, `/api/resume/${sessionId}/answers`, {
    analysis_id: analysisId,
    answers,
    consent,
    provider,
  });
}

export function applyDecisions(sessionId) {
  return request(`/api/resume/${sessionId}/apply`, { method: "POST" });
}

// Downloads the refined file and saves it under the user's own file name,
// e.g. "My Resume.docx" -> "My Resume (refined).docx". The server never
// needs to know the original name.
export async function downloadRefined(sessionId, originalName) {
  let response;
  try {
    response = await fetch(`/api/resume/${sessionId}/download`);
  } catch {
    throw new NetworkError("Could not reach the server. Check your connection and try again.");
  }
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail;
    } catch {
      // not JSON
    }
    announceIfExpired(response.status, detail || "");
    throw new Error(detail || `Download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const base = (originalName || "resume.docx").replace(/\.docx$/i, "");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${base} (refined).docx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

export function loadPreview(sessionId) {
  return request(`/api/resume/${sessionId}/preview`);
}

export function checkPageFit(sessionId, maxPages) {
  return request(`/api/resume/${sessionId}/page-fit?max_pages=${maxPages}`);
}

export function deleteResume(sessionId) {
  return request(`/api/resume/${sessionId}`, { method: "DELETE" });
}
