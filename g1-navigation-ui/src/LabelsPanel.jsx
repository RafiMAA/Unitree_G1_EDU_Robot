import React, { useEffect, useRef, useState } from "react";

export default function LabelsPanel({ store, draft, setDraft, picking, setPicking, beginPicking }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const nameRef = useRef(null);
  const importRef = useRef(null);
  const currentMap = useRef(store.mapId);
  currentMap.current = store.mapId;

  useEffect(() => {
    setName(draft?.text ?? "");
    if (draft) {
      setMessage("");
      setError("");
      nameRef.current?.focus();
    }
  }, [draft]);

  useEffect(() => {
    setDraft(null);
    setPicking(false);
    setName("");
    setMessage("");
    setError("");
  }, [store.mapId, setDraft, setPicking]);

  const save = async event => {
    event.preventDefault();
    if (!draft || !name.trim()) return;
    const mapId = store.mapId;
    setBusy(true); setError("");
    try {
      await store.command("upsert", { label: { ...draft, text: name.trim(), z: draft.z ?? 0, yaw: draft.yaw ?? 0 } });
      if (currentMap.current === mapId) {
        setDraft(null); setPicking(false);
        setMessage(`Saved “${name.trim()}” to ${mapId}_labels.json`);
      }
    } catch (err) { if (currentMap.current === mapId) setError(err.message); }
    finally { setBusy(false); }
  };

  const remove = async label => {
    setBusy(true); setError("");
    try {
      await store.command("delete", { id: label.id });
      if (draft?.id === label.id) setDraft(null);
      setMessage(`Deleted “${label.text}”`);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const exportJson = () => {
    const exported = { schema_version: 1, map_id: store.mapId, frame_id: "map", labels: store.labels };
    const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2) + "\n"], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${store.mapId}_labels.json`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const importJson = async event => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true); setError("");
    try {
      if (file.size > 1_000_000) throw new Error("Location JSON must be smaller than 1 MB");
      const document = JSON.parse(await file.text());
      await store.command("import", { document });
      setMessage("Imported locations. Matching names were updated; other locations were kept.");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return <section className="labelcard" aria-label="Saved locations">
    <div className="cardhead"><span>SAVED LOCATIONS</span><span>{store.labels.length}</span></div>
    <div className="labelbody">
      <p className="labelmap">Map: <strong>{store.mapId}</strong></p>
      <p className="labelhint">Click Add location, then a white mapped cell. Positions are saved in metres.</p>
      <button className={`labeladd ${picking ? "selected" : ""}`} disabled={!store.ready || busy} onClick={() => {
        if (picking) { setPicking(false); setDraft(null); }
        else { setDraft(null); beginPicking(); }
      }}>{picking ? "Cancel picking" : "Add location"}</button>
      {picking && <p className="labelnotice">Click the map to choose the location. Dragging still pans.</p>}
      {draft && <form className="labelform" onSubmit={save}>
        <label htmlFor="location-name">Location name</label>
        <input ref={nameRef} id="location-name" required maxLength={80} value={name} onChange={event => setName(event.target.value)} placeholder="e.g. Entrance, Office, Washroom" />
        <p className="labelcoords">X {draft.x.toFixed(2)} m · Y {draft.y.toFixed(2)} m</p>
        <div className="labelactions">
          <button type="submit" disabled={!store.ready || busy || !name.trim()}>{busy ? "Saving…" : "Save location"}</button>
          <button type="button" disabled={busy} onClick={() => { setDraft(null); setPicking(false); }}>Cancel</button>
        </div>
      </form>}
      {(error || store.error) && <p className="labelerror" role="alert">{error || store.error}</p>}
      {message && <p className="labelnotice" role="status">{message}</p>}
      {!store.labels.length && !draft && <p className="labelhint">No saved locations for this map.</p>}
      <ul className="labellist">
        {store.labels.map(label => <li key={label.id}>
          <strong>{label.text}</strong>
          <span className="labelcoords">X {label.x.toFixed(2)} · Y {label.y.toFixed(2)}</span>
          <div className="labelactions">
            <button disabled={!store.ready || busy} onClick={() => { setPicking(false); setDraft(label); }}>Rename</button>
            <button disabled={!store.ready || busy} onClick={() => { setDraft(label); beginPicking(); }}>Move</button>
            <button className="labeldelete" disabled={!store.ready || busy} onClick={() => remove(label)}>Delete</button>
          </div>
        </li>)}
      </ul>
      <div className="labelactions labeltransfer">
        <button disabled={!store.ready || busy} onClick={exportJson}>Export JSON</button>
        <button disabled={!store.ready || busy} onClick={() => importRef.current.click()}>Import JSON</button>
        <input ref={importRef} className="fileinput" type="file" accept=".json,application/json" aria-label="Import location JSON" onChange={importJson} />
      </div>
      <button className="labelreload" disabled={!store.connected || busy} onClick={async () => {
        setBusy(true); setError("");
        try { await store.command("select"); setMessage("Locations reloaded from JSON"); }
        catch (err) { setError(err.message); }
        finally { setBusy(false); }
      }}>Reload locations</button>
      {store.savedFile && <p className="labelpath" title={store.savedFile}>Saved file: {store.mapId}_labels.json</p>}
    </div>
  </section>;
}
