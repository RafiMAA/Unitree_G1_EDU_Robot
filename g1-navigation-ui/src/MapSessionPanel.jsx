import React, { useEffect, useRef, useState } from "react";

const readBase64 = file => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result.split(",")[1]);
  reader.onerror = () => reject(new Error("Unable to read the map image"));
  reader.readAsDataURL(file);
});

export default function MapSessionPanel({ consoleSession, onSwitch, posePicking, onPoseTool, canPose, disabled = false, showNewMapping = true, showPose = true }) {
  const { session, error, request, refresh } = consoleSession;
  const [selected, setSelected] = useState("");
  const [yaml, setYaml] = useState(null);
  const [image, setImage] = useState(null);
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const yamlInput = useRef(null), imageInput = useRef(null), dropdown = useRef(null);
  const knownMaps = useRef(new Set());
  const loadedMap = useRef(null);
  useEffect(() => {
    if (!session) return;
    const maps = session.maps || [];
    const added = maps.filter(map => !knownMaps.current.has(map.id));
    knownMaps.current = new Set(maps.map(map => map.id));
    const loaded = session.selected_map?.id || null;
    if (loaded && loaded !== loadedMap.current) setSelected(loaded);
    else if (!loaded && added.length) setSelected(added[added.length - 1].id);
    else if (selected && !maps.some(map => map.id === selected)) setSelected("");
    loadedMap.current = loaded;
  }, [session?.selected_map?.id, (session?.maps || []).map(map => map.id).join("|")]);
  const availableName = filename => {
    const base = filename.replace(/\.ya?ml$/i, "").replace(/[^A-Za-z0-9_.-]/g, "_").replace(/^[^A-Za-z0-9]+/, "").slice(0, 70) || "imported_map";
    const names = new Set((session?.maps || []).map(map => map.name));
    let candidate = base, suffix = 2;
    while (names.has(candidate)) candidate = `${base}_${suffix++}`;
    return candidate;
  };
  const pairMatches = Boolean(yaml && image && /\.ya?ml$/i.test(yaml.name)
    && /\.(pgm|png|bmp|jpe?g)$/i.test(image.name)
    && yaml.name.replace(/\.[^.]+$/, "") === image.name.replace(/\.[^.]+$/, ""));
  const run = async task => {
    setBusy(true); setMessage("");
    try { await task(); } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  };
  const importMap = (load = false) => run(async () => {
    if (!yaml || !image) throw new Error("Select the map YAML and its referenced image");
    if (!pairMatches) throw new Error("YAML and image must have the same name before the extension (for example airport.yaml and airport.png).");
    if (yaml.size > 100000 || image.size > 20 * 1024 * 1024) throw new Error("Maximum: 100 KB YAML and 20 MB image");
    const result = await request("/api/maps/import", { name: name.trim(), yaml_name: yaml.name, yaml: await yaml.text(), image_name: image.name, image: await readBase64(image) });
    setSelected(result.id);
    setYaml(null); setImage(null);
    yamlInput.current.value = ""; imageInput.current.value = "";
    setMessage(`Imported ${result.name}. Click Load map to localize.`);
    if (load) { await onSwitch("localization", result.id); setMessage(`Loading ${result.name}…`); }
  });
  const unavailable = !session || session.transitioning || busy || disabled;
  useEffect(() => {
    if (unavailable && dropdown.current) dropdown.current.open = false;
  }, [unavailable]);
  return <div className="savecard sessioncard">
    <label>{showPose ? "SAVED MAP / LOCALIZATION" : "SAVED MAPS"}</label>
    {error && <p className="labelerror">{error}</p>}
    <p className="hint">{session?.transitioning ? "Switching ROS processes…" : session?.mode === "localization" ? `Loaded: ${session.selected_map?.name} · ${session.localized ? "Pose estimate received" : showPose ? "Set the robot's initial pose" : "Ready for location labels"}` : session?.mode === "mapping" ? "Live SLAM mapping" : "Choose Mapping or load a saved map"}</p>
    {session?.mode === "mapping" && <p className="hint">Save the current map before loading another; loading stops this SLAM session.</p>}
    <details className="savedmapdropdown" ref={dropdown} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); dropdown.current.open = false; dropdown.current.querySelector("summary").focus(); }
    }}>
      <summary aria-label="Choose a saved map" aria-disabled={unavailable}
        onClick={event => { if (unavailable) event.preventDefault(); }}
        onKeyDown={event => { if (unavailable && ["Enter", " "].includes(event.key)) event.preventDefault(); }}>
        <span>{(session?.maps || []).find(map => map.id === selected)?.name || "Choose an existing map"}</span>
        <span className="savedmapcaret" aria-hidden="true">⌄</span>
      </summary>
    <div className="savedmaplist" role="group" aria-label="Saved maps">
      {!(session?.maps || []).length && <p className="hint">No saved maps. Import a map or save one in Mapping.</p>}
      {(session?.maps || []).map(map => <div key={map.id} className={`savedmaprow ${selected === map.id ? "selected" : ""}`}>
        <button className="savedmapchoice" aria-pressed={selected === map.id} disabled={unavailable}
          title={map.name} onClick={() => { setSelected(map.id); dropdown.current.open = false; dropdown.current.querySelector("summary").focus({ preventScroll: true }); }}>
          <span className="savedmapchevron" aria-hidden="true">›</span><span className="savedmapname">{map.name}</span>{session?.selected_map?.id === map.id && <small>Loaded</small>}</button>
        <button className="savedmapdelete" aria-label={`Delete map ${map.name}`}
          title={session?.selected_map?.id === map.id ? "Load another map before deleting this map" : `Delete ${map.name}`}
          disabled={unavailable || session?.selected_map?.id === map.id} onClick={() => {
            if (!window.confirm(`Delete map “${map.name}” and its saved locations? The files will be archived for recovery.`)) return;
            run(async () => {
              await request("/api/maps/delete", { map_id: map.id });
              if (selected === map.id) setSelected("");
              setMessage(`Deleted ${map.name}.`);
            });
          }}><svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg></button>
      </div>)}
    </div>
    </details>
    <div className="sessionactions">
      <button disabled={unavailable || !selected} onClick={() => run(() => onSwitch("localization", selected))}>Load map</button>
      <button disabled={unavailable} onClick={() => run(async () => { const current = await refresh(); setMessage(`${current.maps.length} saved map(s) available.`); })}>Refresh maps</button>
      {showNewMapping && <button disabled={unavailable} onClick={() => run(() => onSwitch("mapping"))}>{session?.mode === "mapping" ? "Continue mapping" : "New mapping"}</button>}
    </div>
    {showPose && <><button className={posePicking ? "active" : ""} disabled={!canPose || unavailable} onClick={onPoseTool}>{posePicking ? "Cancel initial pose" : "2D Pose Estimate"}</button>
    <p className="hint">Click and drag on free space: start = position, arrow = heading. Release to set the estimate.</p></>}
    <details><summary>Import a map from disk</summary>
      <label>1. Map YAML<input ref={yamlInput} type="file" accept=".yaml,.yml" disabled={unavailable} onChange={event => { const file = event.target.files[0]; setYaml(file || null); setMessage(""); setName(file ? availableName(file.name) : ""); }} /></label>
      <label>2. Map image<input ref={imageInput} type="file" accept=".pgm,.png,.bmp,.jpg,.jpeg" disabled={unavailable} onChange={event => { setImage(event.target.files[0] || null); setMessage(""); }} /></label>
      <p className="hint">Choose both files with the same name, for example airport.yaml and airport.png. The YAML must reference the selected image.</p>
      {yaml && image && !pairMatches && <p className="labelerror" role="alert">YAML and image names must match before the extension.</p>}
      {yaml && <p className="hint">Import name: {name}</p>}
      <button disabled={unavailable || !pairMatches || !name.trim()} onClick={() => importMap(false)}>Import map</button>
      <button disabled={unavailable || !pairMatches || !name.trim()} onClick={() => importMap(true)}>Import & load</button>
      <p className="hint">Imports preserve the map resolution and origin. Existing files are never overwritten.</p>
    </details>
    {message && <p className="labelnotice" role="status">{message}</p>}
    {session?.error && <p className="labelerror">{session.error}</p>}
  </div>;
}
