import React, { useCallback, useEffect, useRef, useState } from "react";
import RagPanel from "./RagPanel.jsx";
import MapView from "./MapView.jsx";
import { freeMapCell } from "./mapGeometry.js";
import LabelsPanel from "./LabelsPanel.jsx";
import useMapLabels from "./useMapLabels.js";
import useConsoleSession from "./useConsoleSession.js";
import MapSessionPanel from "./MapSessionPanel.jsx";
import { initialPoseMessage } from "./initialPose.js";

const ROSBRIDGE_URL = import.meta.env.VITE_ROSBRIDGE_URL || `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/rosbridge`;
// Speed defaults match terminal teleop_keyboard.py (lin=0.2, ang=0.3).
// Limits match the RL policy's trained command ranges from deploy.yaml:
//   lin_vel_x: [-0.5, 1.0], lin_vel_y: [-0.5, 0.5], ang_vel_z: [-1.0, 1.0]
// The terminal allows up to 2.0/1.5, but on_cmd_vel clips to these anyway.
const DEFAULT_LINEAR_SPEED = 0.20;
const DEFAULT_ANGULAR_SPEED = 0.30;
const SPEED_STEP = 0.1;
const MAX_LIN = 1.0;
const MIN_LIN = 0.1;
const MAX_ANG = 1.0;
const MIN_ANG = 0.1;

const TOPICS = {
  teleop: ["/cmd_vel_teleop", "geometry_msgs/msg/Twist"],
  mode: ["/ui/mode", "std_msgs/msg/String"],
  goal: ["/ui/goal", "geometry_msgs/msg/PoseStamped"],
  cancel: ["/ui/cancel_navigation", "std_msgs/msg/Bool"],
  estop: ["/ui/emergency_stop", "std_msgs/msg/Bool"],
  initialPose: ["/initialpose", "geometry_msgs/msg/PoseWithCovarianceStamped"],
  labels: ["/ui/map_label_command", "std_msgs/msg/String"],
};

function nowStamp() {
  const milliseconds = Date.now();
  return {
    sec: Math.floor(milliseconds / 1000),
    nanosec: (milliseconds % 1000) * 1_000_000,
  };
}

export default function App() {
  const socketRef = useRef(null);
  const reconnectRef = useRef(null);
  const modeRef = useRef("idle");
  const [connected, setConnected] = useState(false);
  const [mode, setMode] = useState("idle");
  const [activeTab, setActiveTab] = useState(null);
  const [estop, setEstop] = useState(false);
  const [linearSpeed, setLinearSpeed] = useState(DEFAULT_LINEAR_SPEED);
  const [angularSpeed, setAngularSpeed] = useState(DEFAULT_ANGULAR_SPEED);
  const [activeMotion, setActiveMotion] = useState(null);
  // Latched velocity state — mirrors teleop_keyboard.py's lin_x, lin_y, ang_z
  const latchRef = useRef({ x: 0, y: 0, z: 0 });
  const linRef = useRef(DEFAULT_LINEAR_SPEED);
  const angRef = useRef(DEFAULT_ANGULAR_SPEED);
  const [map, setMap] = useState(null);
  const [robotPose, setRobotPose] = useState(null);
  const [goal, setGoal] = useState(null);
  const [path, setPath] = useState([]);
  const [status, setStatus] = useState({ state: "offline", message: "Connecting to ROS…" });
  const [mapName, setMapName] = useState("g1_map");
  const [mapSaving, setMapSaving] = useState(false);
  const consoleSession = useConsoleSession();
  const session = consoleSession.session;
  const [posePicking, setPosePicking] = useState(false);
  const [mapSwitching, setMapSwitching] = useState(false);
  const switching = mapSwitching || Boolean(session?.transitioning);
  const switchingRef = useRef(false);
  switchingRef.current = switching;
  const sessionKeyRef = useRef(null);
  const sessionTabRef = useRef(null);
  const [labelPicking, setLabelPicking] = useState(false);
  const [labelDraft, setLabelDraft] = useState(null);
  const labelEditingRef = useRef(false);
  labelEditingRef.current = labelPicking || Boolean(labelDraft) || posePicking || switching || Boolean(session && !session.safety_ready);
  const manualDriveEnabled = activeTab === "mapping" && mode === "mapping" && connected && !estop
    && !labelPicking && !labelDraft && !posePicking && !switching && Boolean(session?.safety_ready);
  const manualDriveRef = useRef(false);
  manualDriveRef.current = manualDriveEnabled;

  const send = useCallback((message) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(message));
      return true;
    }
    return false;
  }, []);

  const publish = useCallback(
    (topic, msg) => send({ op: "publish", topic, msg }),
    [send],
  );
  const labelStore = useMapLabels({ connected: connected && Boolean(session?.labels_ready) && !switching, publish, mapName });
  const onLabelMessage = labelStore.onMessage;

  const advertise = useCallback(() => {
    Object.values(TOPICS).forEach(([topic, type]) => {
      send({ op: "advertise", topic, type });
    });
  }, [send]);

  const subscribe = useCallback(() => {
    [
      ["/map", "nav_msgs/msg/OccupancyGrid", 250],
      ["/ui/map", "nav_msgs/msg/OccupancyGrid", 250],
      ["/ui/robot_pose", "geometry_msgs/msg/PoseStamped", 50],
      ["/plan", "nav_msgs/msg/Path", 100],
      ["/ui/safety_status", "std_msgs/msg/String", 20],
      ["/ui/navigation_status", "std_msgs/msg/String", 20],
      ["/collision_monitor_state", "nav2_msgs/msg/CollisionMonitorState", 20],
      ["/ui/map_labels", "std_msgs/msg/String", 0],
    ].forEach(([topic, type, throttle_rate]) => {
      send({ op: "subscribe", topic, type, throttle_rate, queue_length: 1 });
    });
  }, [send]);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useEffect(() => {
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      const socket = new WebSocket(ROSBRIDGE_URL);
      socketRef.current = socket;
      socket.onopen = () => {
        if (disposed || socketRef.current !== socket) return;
        setConnected(true);
        setStatus({ state: "ready", message: "Connected to ROS" });
        advertise();
        subscribe();
        setTimeout(() => publish(TOPICS.mode[0], { data: modeRef.current }), 100);
      };
      socket.onmessage = ({ data }) => {
        if (disposed || socketRef.current !== socket) return;
        const packet = JSON.parse(data);
        if ((packet.topic === "/map" || packet.topic === "/ui/map") && !switchingRef.current) setMap(packet.msg);
        if (packet.topic === "/ui/robot_pose" && !switchingRef.current) setRobotPose(packet.msg.pose);
        if (packet.topic === "/plan" && !switchingRef.current) setPath(packet.msg.poses || []);
        if (packet.topic === "/ui/map_labels") onLabelMessage(packet.msg);
        if (packet.topic === "/ui/navigation_status" || packet.topic === "/ui/safety_status") {
          try {
            setStatus(JSON.parse(packet.msg.data));
          } catch {
            setStatus({ state: "info", message: packet.msg.data });
          }
        }
        if (modeRef.current === "navigate" && packet.topic === "/collision_monitor_state" && packet.msg.action_type > 0) {
          const action = ["clear", "stop", "slowdown", "approach", "limit"][packet.msg.action_type];
          setStatus({ state: action, message: action === "stop" ? "Navigation paused: obstacle inside the collision zone" : `Safety ${action}: ${packet.msg.polygon_name}` });
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (disposed || socketRef.current !== socket) return;
        setConnected(false);
        setStatus({ state: "offline", message: "ROS disconnected; retrying…" });
        reconnectRef.current = setTimeout(connect, 1800);
      };
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(reconnectRef.current);
      socketRef.current?.close();
    };
  }, [advertise, publish, subscribe, onLabelMessage]);

  const setControlMode = useCallback(
    (nextMode) => {
      publish(TOPICS.teleop[0], zeroTwist());
      publish(TOPICS.mode[0], { data: nextMode });
      latchRef.current = { x: 0, y: 0, z: 0 };
      setActiveMotion(null);
      setMode(nextMode);
      if (nextMode !== "idle") setActiveTab(nextMode);
      setGoal(null);
      setPath([]);
      setLabelPicking(false);
      setLabelDraft(null);
      setPosePicking(false);
      setStatus({ state: "ready", message: nextMode === "mapping" ? "Drive to build the map" : nextMode === "navigate" ? "Click the map to set a goal" : "Robot idle" });
    },
    [publish],
  );

  // ── Latching helpers — mirror teleop_keyboard.py exactly ──────────────
  // Each direction key sets ONE axis and zeroes the other two.
  const latch = useCallback((key) => {
    if (!manualDriveRef.current) return;
    const lin = linRef.current;
    const ang = angRef.current;
    const L = latchRef.current;

    switch (key) {
      case "w": case "W": case "ArrowUp":    L.x =  lin; L.y = 0; L.z = 0; break;
      case "s": case "S": case "ArrowDown":  L.x = -lin; L.y = 0; L.z = 0; break;
      case "a": case "A": case "ArrowLeft":  L.x = 0; L.y = 0; L.z =  ang; break;
      case "d": case "D": case "ArrowRight": L.x = 0; L.y = 0; L.z = -ang; break;
      case "q": case "Q":                    L.x = 0; L.y =  lin; L.z = 0; break;
      case "e": case "E":                    L.x = 0; L.y = -lin; L.z = 0; break;
      default: return;
    }
    setActiveMotion(key);
    publish(TOPICS.teleop[0], twist(L.x, L.y, L.z));
  }, [estop, publish]);

  const stopMotion = useCallback(() => {
    latchRef.current = { x: 0, y: 0, z: 0 };
    setActiveMotion(null);
    publish(TOPICS.teleop[0], zeroTwist());
  }, [publish]);

  const labelEditing = labelPicking || Boolean(labelDraft) || posePicking || switching || Boolean(session && !session.safety_ready) || (mode === "mapping" && !connected);
  useEffect(() => {
    if (labelEditing) {
      stopMotion();
      publish(TOPICS.cancel[0], { data: true });
    }
  }, [labelEditing, stopMotion, publish]);

  useEffect(() => { if (labelPicking || labelDraft) setPosePicking(false); }, [labelPicking, labelDraft]);

  const beginLabelPicking = () => {
    setPosePicking(false);
    stopMotion();
    publish(TOPICS.cancel[0], { data: true });
    setLabelPicking(true);
  };

  const pickLabel = world => {
    if (!labelPicking || !map) return;
    if (!freeMapCell(map, world)) {
      setStatus({ state: "rejected", message: "Place the location on a known, free map cell" });
      return;
    }
    setLabelDraft(previous => ({ ...previous, x: world.x, y: world.y, z: previous?.z ?? 0, yaw: previous?.yaw ?? 0 }));
    setLabelPicking(false);
  };

  const speedUp = useCallback(() => {
    setLinearSpeed((prev) => { const v = Math.min(+(prev + SPEED_STEP).toFixed(1), MAX_LIN); linRef.current = v; return v; });
    setAngularSpeed((prev) => { const v = Math.min(+(prev + SPEED_STEP).toFixed(1), MAX_ANG); angRef.current = v; return v; });
  }, []);

  const speedDown = useCallback(() => {
    setLinearSpeed((prev) => { const v = Math.max(+(prev - SPEED_STEP).toFixed(1), MIN_LIN); linRef.current = v; return v; });
    setAngularSpeed((prev) => { const v = Math.max(+(prev - SPEED_STEP).toFixed(1), MIN_ANG); angRef.current = v; return v; });
  }, []);

  // Keep refs in sync when sliders are used directly
  useEffect(() => { linRef.current = linearSpeed; }, [linearSpeed]);
  useEffect(() => { angRef.current = angularSpeed; }, [angularSpeed]);

  useEffect(() => {
    const directionKeys = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "w", "W", "a", "A", "s", "S", "d", "D", "q", "Q", "e", "E"]);
    const down = (event) => {
      if (event.target instanceof HTMLElement &&
          (event.target.isContentEditable || event.target.closest("input, textarea, select, button"))) return;
      if (directionKeys.has(event.key)) {
        event.preventDefault();
        latch(event.key);
      } else if (event.key === " ") {
        event.preventDefault();
        stopMotion();
      } else if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        speedUp();
      } else if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        speedDown();
      }
    };
    const blur = () => stopMotion();
    window.addEventListener("keydown", down);
    window.addEventListener("blur", blur);
    // Republish latched command at 20 Hz. The terminal teleop uses 10 Hz over
    // native DDS (~1 ms latency). Rosbridge WebSocket adds 10-50 ms jitter,
    // so we publish twice as fast to keep the state machine's cmd_vel_timeout
    // (0.5 s) from ever triggering on a missed beat.
    const interval = setInterval(() => {
      if (modeRef.current === "mapping") {
        const L = latchRef.current;
        publish(TOPICS.teleop[0], twist(L.x, L.y, L.z));
      }
    }, 50);
    return () => {
      clearInterval(interval);
      window.removeEventListener("keydown", down);
      window.removeEventListener("blur", blur);
      publish(TOPICS.teleop[0], zeroTwist());
    };
  }, [latch, stopMotion, speedUp, speedDown, publish]);

  const mapGoal = (world) => {
    if (mode !== "navigate" || !map || estop || switching || (session?.mode === "localization" && (!session.localized || !session.navigation_ready || !session.safety_ready))) return;
    if (!freeMapCell(map, world)) {
      setStatus({ state: "rejected", message: "Choose a known, free map cell" });
      return;
    }
    const yaw = robotPose
      ? Math.atan2(world.y - robotPose.position.y, world.x - robotPose.position.x)
      : 0;
    setGoal({ world });
    publish(TOPICS.goal[0], {
      header: { stamp: nowStamp(), frame_id: "map" },
      pose: {
        position: { x: world.x, y: world.y, z: 0 },
        orientation: { x: 0, y: 0, z: Math.sin(yaw / 2), w: Math.cos(yaw / 2) },
      },
    });
  };

  const toggleEstop = () => {
    const next = !estop;
    setEstop(next);
    latchRef.current = { x: 0, y: 0, z: 0 };
    setActiveMotion(null);
    publish(TOPICS.estop[0], { data: next });
    publish(TOPICS.teleop[0], zeroTwist());
    if (next) publish(TOPICS.cancel[0], { data: true });
    setStatus({ state: next ? "stop" : "ready", message: next ? "Software emergency stop engaged" : "Emergency stop released" });
  };

  const switchMap = async (nextMode, mapId) => {
    stopMotion();
    publish(TOPICS.cancel[0], { data: true });
    setControlMode("idle");
    setPosePicking(false);
    setMapSwitching(true);
    try {
      await consoleSession.request("/api/session", { mode: nextMode, map_id: mapId });
      setMap(null); setRobotPose(null); setPath([]); setGoal(null);
    } finally { setMapSwitching(false); }
  };

  const selectTab = async tab => {
    stopMotion();
    publish(TOPICS.cancel[0], { data: true });
    setControlMode("idle");
    setMapSwitching(true);
    try {
      await consoleSession.request("/api/session", { tab });
      setActiveTab(tab);
      setControlMode(tab === "mapping" || tab === "navigate" ? tab : "idle");
    } catch (error) {
      setStatus({ state: "error", message: error.message });
    } finally { setMapSwitching(false); }
  };

  useEffect(() => {
    if (!session || session.transitioning) return;
    const key = `${session.mode}:${session.selected_map?.id || ""}`;
    const previousKey = sessionKeyRef.current;
    const mapChanged = previousKey !== key;
    if (mapChanged) {
      sessionKeyRef.current = key;
      setMap(null); setRobotPose(null); setPath([]); setGoal(null);
      if (session.selected_map) setMapName(session.selected_map.name);
      else if (previousKey?.startsWith("localization:")) setMapName(`g1_map_${Date.now()}`);
    }
    if (mapChanged || sessionTabRef.current !== session.tab) {
      sessionTabRef.current = session.tab;
      setActiveTab(session.tab);
      setControlMode(session.tab === "mapping" || session.tab === "navigate" ? session.tab : "idle");
    }
    if (session.error) setStatus({ state: "error", message: session.error });
  }, [session, setControlMode]);

  const canPose = connected && Boolean(map) && session?.mode === "localization" && session.amcl_ready && !switching && !estop;
  const poseTool = () => {
    stopMotion(); publish(TOPICS.cancel[0], { data: true });
    setLabelPicking(false); setLabelDraft(null);
    setPosePicking(previous => !previous);
  };
  const setInitialPose = ({ world, yaw }) => {
    if (!canPose) return;
    if (!freeMapCell(map, world)) {
      setStatus({ state: "rejected", message: "Place the robot on a known, free map cell" });
      return;
    }
    publish(TOPICS.initialPose[0], initialPoseMessage(world, yaw));
    setPosePicking(false);
    setStatus({ state: "localizing", message: "Initial pose sent. Wait for localization, then select Navigate." });
  };

  const saveMap = async () => {
    if (mapSaving) return;
    setMapSaving(true);
    setStatus({ state: "saving", message: `Saving ${mapName}…` });
    try {
      const result = await consoleSession.request("/api/maps/save", { name: labelStore.mapId });
      setStatus({ state: "saved", message: `Saved ${result.name}. It is available in the saved-map list.` });
    } catch (error) {
      setStatus({ state: "error", message: error.message });
    } finally { setMapSaving(false); }
  };

  const press = (key) => {
    // Mirror terminal teleop: each click latches one direction, zeroes others.
    latch(key);
  };

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">UNITREE G1 · ROS 2</p>
          <h1>Navigation Console</h1>
        </div>
        <div className="connection"><span className={connected ? "dot online" : "dot"} />{connected ? "ROS connected" : "Offline"}</div>
      </header>

      <section className="modebar">
        <button disabled={switching} className={activeTab === "mapping" ? "active" : ""} onClick={() => selectTab("mapping")}>01 · Mapping</button>
        <button disabled={switching} className={activeTab === "navigate" ? "active" : ""} onClick={() => selectTab("navigate")}>02 · Navigate</button>
        <button disabled={switching} className={activeTab === "maps" ? "active" : ""} onClick={() => selectTab("maps")}>03 · Maps & Localization</button>
        <button disabled={switching} className={activeTab === "rag" ? "active" : ""} onClick={() => selectTab("rag")}>04 · RAG Conversation</button>
        <button onClick={() => { publish(TOPICS.cancel[0], { data: true }); setControlMode("idle"); }}>Cancel / Idle</button>
        <button className={`estop ${estop ? "engaged" : ""}`} onClick={toggleEstop}>{estop ? "Release E-stop" : "Emergency stop"}</button>
      </section>

      {activeTab === "rag" && <RagPanel navigationStatus={status} consoleSession={consoleSession} onOpenMaps={() => selectTab("maps")}
        map={map} robotPose={robotPose} path={path} connected={connected} />}
      <section className="workspace" hidden={activeTab === "rag"}>
        <div className="mapcard">
          <div className="cardhead"><span>LIVE OCCUPANCY MAP</span><span>{map ? `${map.info.width} × ${map.info.height} · ${map.info.resolution.toFixed(2)} m/cell` : "WAITING FOR /map"}</span></div>
          <MapView map={map} robotPose={robotPose} goal={goal} path={path}
            canSetGoal={mode === "navigate" && !estop && !labelPicking && !labelDraft && !posePicking && !switching && (!session || (session.safety_ready && session.navigation_ready && (session.mode !== "localization" || session.localized)))} onGoal={mapGoal}
            labels={labelStore.labels} labelDraft={labelDraft} labelPicking={labelPicking} onLabelPoint={pickLabel} posePicking={posePicking} onInitialPose={setInitialPose} staticMap={session?.mode === "localization"} />
          <div className="legend"><span><i className="robot" /> G1</span><span><i className="route" /> planned path</span><span><i className="target" /> goal</span><span><i className="location" /> location</span></div>
        </div>

        <aside>
          <div className={`statuscard ${status.state}`}><p>ROBOT STATUS</p><strong>{status.message}</strong>{status.distance_remaining != null && <small>{status.distance_remaining.toFixed(2)} m remaining</small>}</div>
          <div className="mapmanagement" hidden={activeTab !== "maps"}>
          <MapSessionPanel consoleSession={consoleSession} onSwitch={switchMap}
            posePicking={posePicking} onPoseTool={poseTool} canPose={canPose} />
          <LabelsPanel store={labelStore} draft={labelDraft} setDraft={setLabelDraft}
            picking={labelPicking} setPicking={setLabelPicking} beginPicking={beginLabelPicking} />
          </div>
          {activeTab === "mapping" && (
            <div className="controlcard" onKeyDown={event => {
              if (event.target.closest("input")) return;
              if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "w", "W", "s", "S", "a", "A", "d", "D", "q", "Q", "e", "E"].includes(event.key)) {
                event.preventDefault(); event.stopPropagation(); latch(event.key);
              } else if (event.key === " ") {
                event.preventDefault(); event.stopPropagation(); stopMotion();
              }
            }}>
              <div className="cardhead"><span>MANUAL DRIVE</span><span>MAPPING ONLY</span></div>
              {!manualDriveEnabled && <p className="hint" role="status">{switching ? "Switching ROS processes…" : !connected ? "Waiting for ROS connection…" : estop ? "Emergency stop engaged." : mode !== "mapping" ? "Robot idle. Select Mapping to resume driving." : "Waiting for mapping drive services…"}</p>}
              <div className="speed-readout">
                <span>Speed: lin={linearSpeed.toFixed(1)} m/s &nbsp; ang={angularSpeed.toFixed(1)} rad/s</span>
              </div>
              <div className="speedcontrols">
                <label>
                  <span>Walk / strafe speed</span>
                  <output>{linearSpeed.toFixed(1)} m/s</output>
                  <input type="range" min={MIN_LIN} max={MAX_LIN} step={SPEED_STEP} value={linearSpeed} onChange={(event) => setLinearSpeed(Number(event.target.value))} />
                </label>
                <label>
                  <span>Angular speed</span>
                  <output>{angularSpeed.toFixed(1)} rad/s</output>
                  <input type="range" min={MIN_ANG} max={MAX_ANG} step={SPEED_STEP} value={angularSpeed} onChange={(event) => setAngularSpeed(Number(event.target.value))} />
                </label>
                <div className="speed-buttons">
                  <button className="speed-btn" onClick={speedDown} title="Decrease speed (−)">− Slower</button>
                  <button className="speed-btn" onClick={speedUp} title="Increase speed (+)">+ Faster</button>
                </div>
              </div>
              <div className="dpad">
                <DriveButton label="W" name="Forward" keyName="ArrowUp" activeMotion={activeMotion} press={press} disabled={!manualDriveEnabled} />
                <DriveButton label="A" name="Turn left" keyName="ArrowLeft" activeMotion={activeMotion} press={press} disabled={!manualDriveEnabled} />
                <DriveButton label="■" name="Stop" keyName="stop" activeMotion={activeMotion} press={stopMotion} />
                <DriveButton label="D" name="Turn right" keyName="ArrowRight" activeMotion={activeMotion} press={press} disabled={!manualDriveEnabled} />
                <DriveButton label="S" name="Backward" keyName="ArrowDown" activeMotion={activeMotion} press={press} disabled={!manualDriveEnabled} />
              </div>
              <div className="straferow">
                <DriveButton label="Q ← Strafe" name="Strafe left" keyName="q" activeMotion={activeMotion} press={press} wide disabled={!manualDriveEnabled} />
                <DriveButton label="Strafe → E" name="Strafe right" keyName="e" activeMotion={activeMotion} press={press} wide disabled={!manualDriveEnabled} />
              </div>
              <p className="hint">WASD + QE · +/− speed · SPACE stop · Commands latch until changed</p>
            </div>
          )}
          <div className="savecard">
            <label htmlFor="map-name">MAP NAME</label>
            <div><input id="map-name" disabled={switching || session?.mode === "localization"} value={mapName} onChange={(event) => setMapName(event.target.value)} /><button disabled={!map || !session || switching || mapSaving || session.mode !== "mapping"} onClick={saveMap}>{mapSaving ? "Saving…" : "Save new map"}</button></div>
          </div>
          <div className="limits"><span>PLANAR FLOOR MODE</span><p>No stair, drop-off, hole or footstep-planning support. Keep a physical E-stop and safety operator present.</p></div>
        </aside>
      </section>
    </main>
  );
}

function DriveButton({ label, name, keyName, activeMotion, press, wide = false, disabled = false }) {
  return <button
    disabled={disabled}
    className={`hold ${keyName === "stop" ? "stop" : ""} ${activeMotion === keyName ? "selected" : ""} ${wide ? "wide" : ""}`}
    aria-label={name}
    title={name}
    onClick={() => press(keyName)}
  >{label}</button>;
}

function twist(x, y, z) {
  return { linear: { x, y, z: 0 }, angular: { x: 0, y: 0, z } };
}

function zeroTwist() {
  return twist(0, 0, 0);
}
