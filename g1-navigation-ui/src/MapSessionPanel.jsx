import React, { useRef, useState } from "react";

const readBase64 = file => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result.split(",")[1]);
  reader.onerror = () => reject(new Error("Unable to read the map image"));
  reader.readAsDataURL(file);
});

export default function MapSessionPanel({ consoleSession, onSwitch, posePicking, onPoseTool, canPose }) {
  const { session, error, request, refresh } = consoleSession;
  const [selected, setSelected] = useState("");
  const [yaml, setYaml] = useState(null);
  const [image, setImage] = useState(null);
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const yamlInput = useRef(null), imageInput = useRef(null);
  const run = async task => {
    setBusy(true); setMessage("");
    try { await task(); } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  };
  const importMap = () => run(async () => {
    if (!yaml || !image) throw new Error("Select the map YAML and its referenced image");
    if (yaml.size > 100000 || image.size > 20 * 1024 * 1024) throw new Error("Maximum: 100 KB YAML and 20 MB image");
    const result = await request("/api/maps/import", { name: name.trim(), yaml: await yaml.text(), image_name: image.name, image: await readBase64(image) });
    setSelected(result.id);
    setYaml(null); setImage(null);
    yamlInput.current.value = ""; imageInput.current.value = "";
    setMessage(`Imported ${result.name}. Click Load map to localize.`);
  });
  const unavailable = !session || session.transitioning || busy;
  return <div className="savecard sessioncard">
    <label>SAVED MAP / LOCALIZATION</label>
    {error && <p className="labelerror">{error}</p>}
    <p className="hint">{session?.transitioning ? "Switching ROS processes…" : session?.mode === "localization" ? `Loaded: ${session.selected_map?.name} · ${session.localized ? "Pose estimate received" : "Set the robot's initial pose"}` : "Live SLAM mapping"}</p>
    {session?.mode === "mapping" && <p className="hint">Save the current map before loading another; loading stops this SLAM session.</p>}
    <select aria-label="Saved map" value={selected} onChange={event => setSelected(event.target.value)} disabled={unavailable}>
      <option value="">Choose an existing map</option>
      {(session?.maps || []).map(map => <option key={map.id} value={map.id}>{map.name}</option>)}
    </select>
    <div className="sessionactions">
      <button disabled={unavailable || !selected} onClick={() => run(() => onSwitch("localization", selected))}>Load map</button>
      <button disabled={unavailable} onClick={() => run(refresh)}>Refresh maps</button>
      <button disabled={unavailable || session?.mode !== "localization"} onClick={() => run(() => onSwitch("mapping"))}>New mapping</button>
    </div>
    <button className={posePicking ? "active" : ""} disabled={!canPose || busy} onClick={onPoseTool}>{posePicking ? "Cancel initial pose" : "2D Pose Estimate"}</button>
    <p className="hint">Click and drag on free space: start = position, arrow = heading. Release to set the estimate.</p>
    <details><summary>Import a map from disk</summary>
      <label>Map YAML<input ref={yamlInput} type="file" accept=".yaml,.yml" onChange={event => { const file = event.target.files[0]; setYaml(file || null); if (file) setName(file.name.replace(/\.ya?ml$/i, "")); }} /></label>
      <label>Referenced image<input ref={imageInput} type="file" accept=".pgm,.png,.bmp,.jpg,.jpeg" onChange={event => setImage(event.target.files[0] || null)} /></label>
      <label>Map name<input value={name} onChange={event => setName(event.target.value)} /></label>
      <button disabled={unavailable || !yaml || !image || !name.trim()} onClick={importMap}>Import map</button>
      <p className="hint">Imports preserve the map resolution and origin. Existing files are never overwritten.</p>
    </details>
    {message && <p className="labelnotice" role="status">{message}</p>}
    {session?.error && <p className="labelerror">{session.error}</p>}
  </div>;
}
