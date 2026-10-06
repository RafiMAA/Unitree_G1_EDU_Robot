import React, { useEffect, useMemo, useRef, useState } from "react";
import { arrowPose } from "./initialPose.js";
import { fitMap, insideMap, quaternionYaw, screenToWorld, worldToScreen, zoomAt } from "./mapGeometry.js";

export default function MapView({ map, robotPose, goal, path, canSetGoal, onGoal,
  labels = [], labelDraft, labelPicking = false, onLabelPoint, posePicking = false, onInitialPose, staticMap = false }) {
  const viewportRef = useRef(null);
  const canvasRef = useRef(null);
  const liveRef = useRef(null);
  const pointers = useRef(new Map());
  const gesture = useRef(null);
  const [size, setSize] = useState({ width: 1, height: 1 });
  // Store the camera in world metres, so SLAM map expansion does not move the view.
  const [camera, setCamera] = useState(null);
  const [poseArrow, setPoseArrow] = useState(null);
  useEffect(() => { if (!posePicking) setPoseArrow(null); }, [posePicking]);
  const [dragging, setDragging] = useState(false);
  const [receivedAt, setReceivedAt] = useState(null);
  const [now, setNow] = useState(Date.now);
  const validMap = map?.info?.width > 0 && map?.info?.height > 0 && map?.info?.resolution > 0;
  const fitted = validMap ? fitMap(map.info, size) : { x: 0, y: 0, scale: 1 };
  const view = camera ?? fitted;
  liveRef.current = { view, size, validMap };

  useEffect(() => {
    if (map) {
      const received = Date.now();
      setReceivedAt(received);
      setNow(received);
    }
  }, [map]);

  useEffect(() => {
    if (!validMap) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [validMap]);

  const mapAge = receivedAt == null ? null : Math.max(0, Math.floor((now - receivedAt) / 1000));
  const robotOutside = validMap && robotPose && !insideMap(map.info, robotPose.position);

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(viewportRef.current);
    return () => observer.disconnect();
  }, []);

  // Rebuild occupancy pixels only when a map arrives, not on every robot pose update.
  const bitmap = useMemo(() => {
    if (!validMap) return null;
    const { width, height } = map.info;
    const layer = document.createElement("canvas");
    layer.width = width;
    layer.height = height;
    const ctx = layer.getContext("2d");
    const image = ctx.createImageData(width, height);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const value = map.data[(height - 1 - y) * width + x];
        // Unobserved cells blend with the viewport. The finite OccupancyGrid
        // extent is not a wall or a limit on the area SLAM can map.
        const unknown = value == null || value < 0;
        const color = value >= 50 ? [38, 43, 49] : [248, 250, 252];
        image.data.set([...color, unknown ? 0 : 255], (y * width + x) * 4);
      }
    }
    ctx.putImageData(image, 0, 0);
    return layer;
  }, [map, validMap]);

  useEffect(() => {
    if (!bitmap) return;
    const canvas = canvasRef.current;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.width * dpr);
    canvas.height = Math.round(size.height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.width, size.height);
    ctx.imageSmoothingEnabled = false;
    ctx.save();
    ctx.translate(size.width / 2, size.height / 2);
    ctx.scale(view.scale, -view.scale);
    ctx.translate(-view.x, -view.y);
    ctx.translate(map.info.origin.position.x, map.info.origin.position.y);
    ctx.rotate(quaternionYaw(map.info.origin.orientation));
    ctx.scale(map.info.resolution, -map.info.resolution);
    ctx.drawImage(bitmap, 0, -map.info.height);
    ctx.restore();
    const toScreen = point => worldToScreen(point, view, size);
    if (path.length > 1) {
      ctx.beginPath();
      ctx.strokeStyle = "#18b982";
      ctx.lineWidth = 3;
      path.forEach((entry, index) => {
        const p = toScreen(entry.pose.position);
        if (index === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();
    }
    if (goal) {
      const p = toScreen(goal.world);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
      ctx.fillStyle = "#ffb84a";
      ctx.fill();
      ctx.strokeStyle = "#07111f";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    if (poseArrow) {
      const p = toScreen(poseArrow.world);
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(-poseArrow.yaw);
      ctx.strokeStyle = "#16a34a"; ctx.fillStyle = "#16a34a"; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(45, 0); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(55, 0); ctx.lineTo(38, -10); ctx.lineTo(38, 10); ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    if (robotPose) {
      const p = toScreen(robotPose.position);
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(-quaternionYaw(robotPose.orientation));
      ctx.beginPath();
      ctx.moveTo(11, 0);
      ctx.lineTo(-7, -7);
      ctx.lineTo(-4, 0);
      ctx.lineTo(-7, 7);
      ctx.closePath();
      ctx.fillStyle = "#52a8ff";
      ctx.fill();
      ctx.strokeStyle = "#133859";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }
  }, [bitmap, map, view.x, view.y, view.scale, size, goal, path, robotPose, poseArrow]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wheel = event => {
      event.preventDefault();
      const { view: current, size: bounds, validMap: ready } = liveRef.current;
      if (!ready) return;
      const rect = canvas.getBoundingClientRect();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1);
      setCamera(zoomAt(current, { x: event.clientX - rect.left, y: event.clientY - rect.top },
        Math.exp(-Math.max(-200, Math.min(200, delta)) * 0.002), bounds));
    };
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => canvas.removeEventListener("wheel", wheel);
  }, []);

  const localPoint = event => {
    const rect = canvasRef.current.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const pinchPoints = () => {
    const [a, b] = [...pointers.current.values()];
    return { center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)) };
  };
  const pointerDown = event => {
    if (!validMap || event.button > 2) return;
    event.preventDefault();
    canvasRef.current.focus({ preventScroll: true });
    canvasRef.current.setPointerCapture(event.pointerId);
    const point = localPoint(event);
    pointers.current.set(event.pointerId, point);
    if (pointers.current.size === 2) {
      gesture.current = { type: "pinch", ...pinchPoints(), view, moved: true };
    } else if (pointers.current.size === 1) {
      gesture.current = { type: posePicking && event.button === 0 && !event.shiftKey ? "pose" : event.button === 2 ? "zoom" : "pan", start: point, view,
        goalClick: event.button === 0 && !event.shiftKey, moved: false };
    }
  };
  const pointerMove = event => {
    if (!pointers.current.has(event.pointerId) || !gesture.current) return;
    const point = localPoint(event);
    pointers.current.set(event.pointerId, point);
    const g = gesture.current;
    if (pointers.current.size >= 2 && g.type === "pinch") {
      const pinch = pinchPoints();
      const next = zoomAt(g.view, g.center, pinch.distance / g.distance, size);
      next.x -= (pinch.center.x - g.center.x) / next.scale;
      next.y += (pinch.center.y - g.center.y) / next.scale;
      setCamera(next);
      setDragging(true);
      return;
    }
    if (g.type === "pose") {
      setPoseArrow(arrowPose(g.start, point, g.view, size));
      return;
    }
    const dx = point.x - g.start.x, dy = point.y - g.start.y;
    if (Math.hypot(dx, dy) > 5) g.moved = true;
    if (!g.moved) return;
    setDragging(true);
    setCamera(g.type === "zoom" ? zoomAt(g.view, g.start, Math.exp(-dy * 0.01), size) :
      { ...g.view, x: g.view.x - dx / g.view.scale, y: g.view.y + dy / g.view.scale });
  };
  const pointerEnd = event => {
    if (!pointers.current.has(event.pointerId)) return;
    const g = gesture.current;
    const point = localPoint(event);
    pointers.current.delete(event.pointerId);
    if (g?.type === "pose") {
      const pose = arrowPose(g.start, point, g.view, size);
      if (event.type === "pointerup" && pose && posePicking) onInitialPose(pose);
      setPoseArrow(null);
    }
    if (g?.type !== "pose" && event.type === "pointerup" && g?.goalClick && !g.moved &&
        Math.hypot(point.x - g.start.x, point.y - g.start.y) <= 5 && (canSetGoal || labelPicking)) {
      const world = screenToWorld(point, view, size);
      if (labelPicking) onLabelPoint(world);
      else onGoal(world);
    }
    if (pointers.current.size) {
      gesture.current = { type: "pan", start: [...pointers.current.values()][0], view, moved: true };
    } else {
      gesture.current = null;
      setDragging(false);
    }
    if (canvasRef.current.hasPointerCapture(event.pointerId)) canvasRef.current.releasePointerCapture(event.pointerId);
  };
  const zoom = factor => setCamera(zoomAt(view, { x: size.width / 2, y: size.height / 2 }, factor, size));
  const keyDown = event => {
    // Map shortcuts must never reach the global robot-driving keyboard handler.
    if (event.key !== " ") event.stopPropagation();
    if (!validMap) return;
    if (["+", "=", "-", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].includes(event.key)) event.preventDefault();
    if (event.key === "+" || event.key === "=") zoom(1.25);
    if (event.key === "-") zoom(0.8);
    if (event.key === "Home") setCamera(null);
    const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[event.key];
    if (direction) setCamera({ ...view, x: view.x + direction[0] * 50 / view.scale, y: view.y + direction[1] * 50 / view.scale });
  };
  const scaleMetres = 10 ** Math.floor(Math.log10(100 / view.scale));
  const scaleLength = [5, 2, 1].map(n => n * scaleMetres).find(n => n * view.scale <= 120) ?? scaleMetres;

  return <>
    <div className="maptoolbar" role="toolbar" aria-label="Map view controls" onKeyDown={event => event.stopPropagation()}>
      <span className="viewname">TOP-DOWN · NORTH UP</span>
      <div className="maptools">
        <button disabled={!validMap} onClick={() => zoom(0.8)} aria-label="Zoom out" title="Zoom out">−</button>
        <output aria-label="Map zoom">{validMap ? `${Math.round(view.scale / fitted.scale * 100)}%` : "—"}</output>
        <button disabled={!validMap} onClick={() => zoom(1.25)} aria-label="Zoom in" title="Zoom in">+</button>
        <button disabled={!validMap} onClick={() => setCamera(null)}>Fit map</button>
        <button disabled={!validMap || !robotPose} onClick={() => setCamera({ ...view, ...robotPose.position })}>Center robot</button>
      </div>
    </div>
    <div ref={viewportRef} className={`mapviewport ${canSetGoal || labelPicking || posePicking ? "clickable" : ""} ${dragging ? "dragging" : ""}`}>
      <canvas ref={canvasRef} tabIndex={0} aria-label="Interactive occupancy map. Scroll to zoom, drag to pan. In Navigate mode, click to set a goal."
        onKeyDown={keyDown} onPointerDown={pointerDown} onPointerMove={pointerMove}
        onPointerUp={pointerEnd} onPointerCancel={pointerEnd} onLostPointerCapture={pointerEnd}
        onContextMenu={event => event.preventDefault()} />
      {validMap && <div className="maplabeloverlay" aria-label="Map location markers">
        {labels.map(label => {
          const point = worldToScreen(label, view, size);
          return <div key={label.id} className="maplocation" style={{ left: point.x, top: point.y }}>
            <span>{label.text}</span><i />
          </div>;
        })}
        {labelDraft && (() => {
          const point = worldToScreen(labelDraft, view, size);
          return <div className="maplocation draft" style={{ left: point.x, top: point.y }}><span>{labelDraft.text || "New location"}</span><i /></div>;
        })()}
      </div>}
      {!validMap && <div className="empty"><div className="scanner" /><p>Waiting for map</p></div>}
      {validMap && <div className={`mapfreshness ${(!staticMap && mapAge > 10) || robotOutside ? "delayed" : ""}`}>
        <span>{mapAge == null ? "Receiving map…" : `Map received ${mapAge}s ago`}</span>
        {robotOutside && <span>{staticMap ? "Robot outside loaded map · check initial pose" : "Robot beyond current grid · awaiting map expansion"}</span>}
        {!staticMap && mapAge > 10 && <span>No recent map update from SLAM</span>}
      </div>}
      {validMap && <div className="mapscale"><span style={{ width: `${scaleLength * view.scale}px` }} />{Number(scaleLength.toPrecision(2))} m</div>}
    </div>
    <p className="maphelp">Scroll / pinch to zoom · Drag to pan · Right-drag to zoom{posePicking ? " · Click and drag to set robot position and heading" : labelPicking ? " · Click a free cell to label it" : canSetGoal ? " · Click a free cell to set a goal" : ""}<br />White: observed free space · Dark: obstacles · Gray: unmapped space</p>
  </>;
}
