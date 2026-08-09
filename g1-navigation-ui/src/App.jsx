import React, { useCallback, useEffect, useRef, useState } from "react";

const ROSBRIDGE_URL = import.meta.env.VITE_ROSBRIDGE_URL || "ws://localhost:9090";
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
};

function nowStamp() {
  const milliseconds = Date.now();
  return {
    sec: Math.floor(milliseconds / 1000),
    nanosec: (milliseconds % 1000) * 1_000_000,
  };
}

function quaternionYaw(q = {}) {
  return Math.atan2(
    2 * ((q.w ?? 1) * (q.z ?? 0) + (q.x ?? 0) * (q.y ?? 0)),
    1 - 2 * ((q.y ?? 0) ** 2 + (q.z ?? 0) ** 2),
  );
}

export default function App() {
  const socketRef = useRef(null);
  const reconnectRef = useRef(null);
  const canvasRef = useRef(null);
  const modeRef = useRef("mapping");
  const mapNameRef = useRef("g1_map");
  const [connected, setConnected] = useState(false);
  const [mode, setMode] = useState("mapping");
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

  const advertise = useCallback(() => {
    Object.values(TOPICS).forEach(([topic, type]) => {
      send({ op: "advertise", topic, type });
    });
  }, [send]);

  const subscribe = useCallback(() => {
    [
      ["/map", "nav_msgs/msg/OccupancyGrid", 250],
      ["/ui/robot_pose", "geometry_msgs/msg/PoseStamped", 50],
      ["/plan", "nav_msgs/msg/Path", 100],
      ["/ui/navigation_status", "std_msgs/msg/String", 20],
      ["/collision_monitor_state", "nav2_msgs/msg/CollisionMonitorState", 20],
    ].forEach(([topic, type, throttle_rate]) => {
      send({ op: "subscribe", topic, type, throttle_rate, queue_length: 1 });
    });
  }, [send]);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useEffect(() => {
    mapNameRef.current = mapName;
  }, [mapName]);

  useEffect(() => {
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      const socket = new WebSocket(ROSBRIDGE_URL);
      socketRef.current = socket;
      socket.onopen = () => {
        setConnected(true);
        setStatus({ state: "ready", message: "Connected to ROS" });
        advertise();
        subscribe();
        setTimeout(() => publish(TOPICS.mode[0], { data: modeRef.current }), 100);
      };
      socket.onmessage = ({ data }) => {
        const packet = JSON.parse(data);
        if (packet.topic === "/map") setMap(packet.msg);
        if (packet.topic === "/ui/robot_pose") setRobotPose(packet.msg.pose);
        if (packet.topic === "/plan") setPath(packet.msg.poses || []);
        if (packet.topic === "/ui/navigation_status") {
          try {
            setStatus(JSON.parse(packet.msg.data));
          } catch {
            setStatus({ state: "info", message: packet.msg.data });
          }
        }
        if (packet.topic === "/collision_monitor_state" && packet.msg.action_type > 0) {
          const action = ["clear", "stop", "slowdown", "approach", "limit"][packet.msg.action_type];
          setStatus({ state: action, message: `Safety ${action}: ${packet.msg.polygon_name}` });
        }
        if (packet.op === "service_response" && packet.id === "save-map") {
          setStatus({
            state: packet.result ? "saved" : "failed",
            message: packet.result ? `Map saved as ${mapNameRef.current}` : "Map save failed",
          });
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
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
  }, [advertise, publish, subscribe]);

  const setControlMode = useCallback(
    (nextMode) => {
      publish(TOPICS.teleop[0], zeroTwist());
      publish(TOPICS.mode[0], { data: nextMode });
      latchRef.current = { x: 0, y: 0, z: 0 };
      setActiveMotion(null);
      setMode(nextMode);
      setGoal(null);
      setPath([]);
      setStatus({ state: "ready", message: nextMode === "mapping" ? "Drive to build the map" : "Click the map to set a goal" });
    },
    [publish],
  );

  // ── Latching helpers — mirror teleop_keyboard.py exactly ──────────────
  // Each direction key sets ONE axis and zeroes the other two.
  const latch = useCallback((key) => {
    if (modeRef.current !== "mapping" || estop) return;
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

  useEffect(() => {
    if (!map || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const { width, height, resolution, origin } = map.info;
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    const image = context.createImageData(width, height);
    for (let screenY = 0; screenY < height; screenY += 1) {
      for (let x = 0; x < width; x += 1) {
        const value = map.data[(height - 1 - screenY) * width + x];
        const index = (screenY * width + x) * 4;
        const shade = value < 0 ? [216, 220, 225] : value >= 50 ? [38, 43, 49] : [248, 250, 252];
        image.data.set([...shade, 255], index);
      }
    }
    context.putImageData(image, 0, 0);
    const toPixel = (position) => ({
      x: (position.x - origin.position.x) / resolution,
      y: height - (position.y - origin.position.y) / resolution,
    });
    if (path.length > 1) {
      context.beginPath();
      context.strokeStyle = "#3ce6a8";
      context.lineWidth = 3;
      path.forEach((entry, index) => {
        const point = toPixel(entry.pose.position);
        if (index === 0) context.moveTo(point.x, point.y);
        else context.lineTo(point.x, point.y);
      });
      context.stroke();
    }
    if (goal) drawMarker(context, goal.pixel.x, goal.pixel.y, "#ffb84a", 6);
    if (robotPose) {
      const point = toPixel(robotPose.position);
      const yaw = quaternionYaw(robotPose.orientation);
      context.save();
      context.translate(point.x, point.y);
      context.rotate(-yaw);
      context.beginPath();
      context.moveTo(10, 0);
      context.lineTo(-7, -6);
      context.lineTo(-7, 6);
      context.closePath();
      context.fillStyle = "#52a8ff";
      context.fill();
      context.restore();
    }
  }, [goal, map, path, robotPose]);

  const mapClick = (event) => {
    if (mode !== "navigate" || !map || estop) return;
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const pixel = {
      x: ((event.clientX - rect.left) * canvas.width) / rect.width,
      y: ((event.clientY - rect.top) * canvas.height) / rect.height,
    };
    const world = {
      x: pixel.x * map.info.resolution + map.info.origin.position.x,
      y: (canvas.height - pixel.y) * map.info.resolution + map.info.origin.position.y,
    };
    const gridX = Math.floor(pixel.x);
    const gridY = Math.floor(canvas.height - pixel.y);
    const occupancy = map.data[gridY * map.info.width + gridX];
    if (occupancy == null || occupancy < 0 || occupancy >= 50) {
      setStatus({ state: "rejected", message: "Choose a known, free map cell" });
      return;
    }
    const yaw = robotPose
      ? Math.atan2(world.y - robotPose.position.y, world.x - robotPose.position.x)
      : 0;
    setGoal({ pixel, world });
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

  const saveMap = () => {
    send({
      op: "call_service",
      id: "save-map",
      service: "/slam_toolbox/save_map",
      type: "slam_toolbox/srv/SaveMap",
      args: { name: { data: mapName } },
    });
    setStatus({ state: "saving", message: `Saving ${mapName}…` });
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
        <button className={mode === "mapping" ? "active" : ""} onClick={() => setControlMode("mapping")}>01 · Mapping</button>
        <button className={mode === "navigate" ? "active" : ""} onClick={() => setControlMode("navigate")}>02 · Navigate</button>
        <button onClick={() => { publish(TOPICS.cancel[0], { data: true }); setControlMode("idle"); }}>Cancel / Idle</button>
        <button className={`estop ${estop ? "engaged" : ""}`} onClick={toggleEstop}>{estop ? "Release E-stop" : "Emergency stop"}</button>
      </section>

      <section className="workspace">
        <div className="mapcard">
          <div className="cardhead"><span>LIVE OCCUPANCY MAP</span><span>{map ? `${map.info.width} × ${map.info.height} · ${map.info.resolution.toFixed(2)} m/cell` : "WAITING FOR /map"}</span></div>
          <div className={`mapviewport ${mode === "navigate" ? "clickable" : ""}`}>
            {map ? <canvas ref={canvasRef} onClick={mapClick} /> : <div className="empty"><div className="scanner" /><p>Waiting for SLAM map</p></div>}
          </div>
          <div className="legend"><span><i className="robot" /> G1</span><span><i className="route" /> planned path</span><span><i className="target" /> goal</span></div>
        </div>

        <aside>
          <div className={`statuscard ${status.state}`}><p>ROBOT STATUS</p><strong>{status.message}</strong>{status.distance_remaining != null && <small>{status.distance_remaining.toFixed(2)} m remaining</small>}</div>
          {mode === "mapping" && (
            <div className="controlcard">
              <div className="cardhead"><span>MANUAL DRIVE</span><span>MAPPING ONLY</span></div>
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
                <DriveButton label="W" name="Forward" keyName="ArrowUp" activeMotion={activeMotion} press={press} />
                <DriveButton label="A" name="Turn left" keyName="ArrowLeft" activeMotion={activeMotion} press={press} />
                <DriveButton label="■" name="Stop" keyName="stop" activeMotion={activeMotion} press={stopMotion} />
                <DriveButton label="D" name="Turn right" keyName="ArrowRight" activeMotion={activeMotion} press={press} />
                <DriveButton label="S" name="Backward" keyName="ArrowDown" activeMotion={activeMotion} press={press} />
              </div>
              <div className="straferow">
                <DriveButton label="Q ← Strafe" name="Strafe left" keyName="q" activeMotion={activeMotion} press={press} wide />
                <DriveButton label="Strafe → E" name="Strafe right" keyName="e" activeMotion={activeMotion} press={press} wide />
              </div>
              <p className="hint">WASD + QE · +/− speed · SPACE stop · Commands latch until changed</p>
            </div>
          )}
          <div className="savecard">
            <label htmlFor="map-name">MAP OUTPUT NAME OR PATH</label>
            <div><input id="map-name" value={mapName} onChange={(event) => setMapName(event.target.value)} /><button disabled={mode !== "mapping"} onClick={saveMap}>Save</button></div>
          </div>
          <div className="limits"><span>PLANAR FLOOR MODE</span><p>No stair, drop-off, hole or footstep-planning support. Keep a physical E-stop and safety operator present.</p></div>
        </aside>
      </section>
    </main>
  );
}

function DriveButton({ label, name, keyName, activeMotion, press, wide = false }) {
  return <button
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

function drawMarker(context, x, y, color, radius) {
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.fillStyle = color;
  context.fill();
  context.lineWidth = 2;
  context.strokeStyle = "#07111f";
  context.stroke();
}
