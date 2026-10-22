import { useRef, useState } from "react";
import { uploadResume } from "../api.js";

// Must match the backend limit (MAX_UPLOAD_MB). The backend still checks:
// these browser checks only give faster feedback, they are not security.
const MAX_MB = 5;

export default function UploadStep({ onUploaded, minutes = 60 }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  async function handleFile(file) {
    setError("");
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".docx")) {
      setError("Please choose a Word document (.docx).");
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(`The file is larger than ${MAX_MB} MB.`);
      return;
    }

    setBusy(true);
    try {
      const result = await uploadResume(file);
      onUploaded(result, file.name);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      // Clear the input so choosing the same file again still triggers onChange.
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(event) {
    event.preventDefault();
    setDragging(false);
    handleFile(event.dataTransfer.files[0]);
  }

  return (
    <section className="card">
      <h2>1. Upload your resume</h2>

      <label
        className={`dropzone ${dragging ? "dropzone-active" : ""} ${busy ? "dropzone-busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          disabled={busy}
          onChange={(e) => handleFile(e.target.files[0])}
        />
        {busy ? (
          <span>Reading your resume…</span>
        ) : (
          <>
            <strong>Choose a .docx file</strong>
            <span className="muted">or drag it here · up to {MAX_MB} MB</span>
          </>
        )}
      </label>

      {error && (
        <p className="message message-error" role="alert">
          {error}
        </p>
      )}

      <p className="note">
        Your file is stored temporarily on our server and deleted {minutes} minutes
        after your last action, or right away when you click Remove. This step
        does not send your resume to any AI service.
      </p>
    </section>
  );
}
