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

// AI steps get a hard limit in the browser too, a little longer than the
// server's own (100s) plus time for a sleeping free server to wake up.
// Whatever happens, the page never waits forever.
export const AI_WAIT_MS = 150_000;

async function request(path, options = {}) {
  const { timeoutMs, ...fetchOptions } = options;
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let response;
  try {
    response = await fetch(path, controller ? { ...fetchOptions, signal: controller.signal } : fetchOptions);
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(
        "We stopped waiting after 2½ minutes. Google Gemini is slow right now; please try again in a minute."
      );
    }
    // fetch() only throws when no response arrived at all.
    throw new Error("Could not reach the server. Check your connection.");
  } finally {
    if (timer) clearTimeout(timer);
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

export function analyzeResume(sessionId, jobDescription, consent) {
  return request(`/api/resume/${sessionId}/analyze`, {
    method: "POST",
    timeoutMs: AI_WAIT_MS,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_description: jobDescription, consent }),
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
export function submitAnswers(sessionId, analysisId, answers, consent) {
  return request(`/api/resume/${sessionId}/answers`, {
    method: "POST",
    timeoutMs: AI_WAIT_MS,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ analysis_id: analysisId, answers, consent }),
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
    throw new Error("Could not reach the server. Check your connection.");
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
