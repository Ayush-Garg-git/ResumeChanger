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

  // With the Vite proxy, a stopped backend shows up as a 5xx error
  // returned by the proxy itself.
  if (response.status >= 500) {
    throw new Error(
      "The backend is not responding. Make sure it is running on port 8000."
    );
  }
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}).`);
  }
  return response.json();
}

export function checkHealth() {
  return request("/api/health");
}
