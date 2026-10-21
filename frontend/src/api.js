// Every call from the frontend to the backend lives in this file.
// Components never call fetch() directly. If a URL or error format
// changes, there is exactly one place to update.

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    // fetch() only throws when no response arrived at all.
    throw new Error("Could not reach the server. Check your connection.");
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_description: jobDescription, consent }),
  });
}

// action: "accept" | "reject" | "edit" | "undecided". text is only for "edit".
export function decideSuggestion(sessionId, suggestionId, action, text = null) {
  return request(`/api/resume/${sessionId}/suggestions/${suggestionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, text }),
  });
}

export function deleteResume(sessionId) {
  return request(`/api/resume/${sessionId}`, { method: "DELETE" });
}
