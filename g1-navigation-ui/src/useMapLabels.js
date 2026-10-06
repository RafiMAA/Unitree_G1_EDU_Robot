import { useCallback, useEffect, useRef, useState } from "react";

export function mapIdentifier(name) {
  const basename = name.trim().split(/[\\/]/).pop().replace(/\.(yaml|yml|pgm|json)$/i, "");
  return basename.replace(/[^A-Za-z0-9_.-]/g, "_").replace(/^[^A-Za-z0-9]+/, "").slice(0, 80) || "g1_map";
}

export default function useMapLabels({ connected, publish, mapName }) {
  const mapId = mapIdentifier(mapName);
  const mapIdRef = useRef(mapId);
  const connectedRef = useRef(connected);
  const pending = useRef(new Map());
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState("");
  const [loadedMap, setLoadedMap] = useState(null);
  mapIdRef.current = mapId;
  connectedRef.current = connected;

  const command = useCallback((operation, values = {}) => new Promise((resolve, reject) => {
    if (!connectedRef.current) { reject(new Error("Connect to ROS before saving locations")); return; }
    const requestId = crypto.randomUUID();
    const timer = setTimeout(() => {
      pending.current.delete(requestId);
      reject(new Error("No response from the location saver. Start ros2 run g1_navigation map_labels, then click Reload locations."));
    }, 8000);
    pending.current.set(requestId, { resolve, reject, timer });
    if (!publish("/ui/map_label_command", { data: JSON.stringify({ operation, ...values, map_id: mapIdRef.current, request_id: requestId }) })) {
      clearTimeout(timer);
      pending.current.delete(requestId);
      reject(new Error("ROS connection closed before the location was sent"));
    }
  }), [publish]);

  const onMessage = useCallback(message => {
    let response;
    try { response = JSON.parse(message.data); } catch { return; }
    if (response.map_id === mapIdRef.current) {
      if (response.error) setError(response.error);
      else if (Array.isArray(response.labels)) {
        setSnapshot(response);
        setLoadedMap(response.map_id);
        setError("");
      }
    }
    const request = pending.current.get(response.request_id);
    if (request) {
      clearTimeout(request.timer);
      pending.current.delete(response.request_id);
      if (response.error) request.reject(new Error(response.error));
      else request.resolve(response);
    }
  }, []);

  useEffect(() => {
    setLoadedMap(null);
    setError("");
    if (!connected) return;
    let disposed = false;
    command("select").catch(err => { if (!disposed) setError(err.message); });
    return () => { disposed = true; };
  }, [connected, mapId, command]);

  useEffect(() => {
    if (connected) return;
    for (const item of pending.current.values()) {
      clearTimeout(item.timer);
      item.reject(new Error("ROS disconnected. Reconnect and check the saved locations before retrying."));
    }
    pending.current.clear();
  }, [connected]);

  useEffect(() => () => {
    for (const item of pending.current.values()) {
      clearTimeout(item.timer);
      item.reject(new Error("Location editor closed"));
    }
    pending.current.clear();
  }, []);

  return { mapId, labels: snapshot?.map_id === mapId ? snapshot.labels : [],
    savedFile: snapshot?.map_id === mapId ? snapshot.saved_file : "",
    ready: connected && loadedMap === mapId, connected, error, command, onMessage };
}
