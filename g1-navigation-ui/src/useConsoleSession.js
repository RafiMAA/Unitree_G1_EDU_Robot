import { useCallback, useEffect, useState } from "react";

export default function useConsoleSession() {
  const [session, setSession] = useState(null);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const response = await fetch("/api/session");
    if (!response.ok) throw new Error("Start the console with npm run dev to enable map loading");
    const data = await response.json();
    if (!data.managed) throw new Error("Console manager is unavailable");
    setSession(data);
    return data;
  }, []);
  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const response = await fetch("/api/session");
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!data.managed) throw new Error();
        if (!disposed) { setSession(data); setError(""); }
      } catch {
        if (!disposed) setError("Start with npm run dev to enable saved-map loading");
      }
    };
    poll();
    const timer = setInterval(poll, 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, []);
  const request = useCallback(async (route, payload) => {
    const response = await fetch(route, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Console request failed");
    await refresh();
    return data;
  }, [refresh]);
  return { session, error, refresh, request };
}
