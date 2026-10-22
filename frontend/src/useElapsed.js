import { useEffect, useState } from "react";

// Seconds since `running` became true, ticking once a second. Shown on
// buttons during AI steps, so a slow answer doesn't look like a frozen page.
export default function useElapsed(running) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) {
      setSeconds(0);
      return undefined;
    }
    const started = Date.now();
    const id = setInterval(() => setSeconds(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, [running]);
  return seconds;
}
