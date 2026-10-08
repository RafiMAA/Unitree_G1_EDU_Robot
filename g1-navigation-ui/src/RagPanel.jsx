import React, { useEffect, useRef, useState } from "react";
import MapSessionPanel from "./MapSessionPanel.jsx";
import MapView from "./MapView.jsx";
import { LiveAudio } from "./liveAudio.js";

const LANGUAGES = { en: "English", fr: "Français", de: "Deutsch", es: "Español", ru: "Русский", ja: "日本語", zh: "中文", ko: "한국어", hi: "हिंदी", si: "සිංහල", ta: "தமிழ்" };

export default function RagPanel({ navigationStatus, consoleSession, onOpenMaps, map, robotPose, path = [], connected, onSwitchMap, posePicking, onPoseTool, canPose, onInitialPose }) {
  const [backend, setBackend] = useState(null);
  const [mapContext, setMapContext] = useState(null);
  const [navigation, setNavigation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [name, setName] = useState("");
  const [passengerName, setPassengerName] = useState("");
  const [language, setLanguage] = useState("en");
  const [phase, setPhase] = useState("idle");
  const [speaking, setSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState("");
  const session = consoleSession?.session;
  const mounted = useRef(false), generation = useRef(0), socket = useRef(null), audio = useRef(null), log = useRef(null), timeout = useRef(null), active = useRef(false);
  const supported = window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia) && Boolean(window.AudioWorkletNode);
  const ready = backend?.ready && backend?.configured;
  const running = phase !== "idle";
  const changingMap = session?.transitioning;

  const end = (message = "") => {
    active.current = false; generation.current += 1; clearTimeout(timeout.current);
    const ws = socket.current; socket.current = null;
    if (ws) { ws.onclose = ws.onerror = ws.onmessage = null; ws.close(); }
    const capture = audio.current; audio.current = null; capture?.close();
    if (mounted.current) { setPhase("idle"); setSpeaking(false); setMuted(false); setPassengerName(""); if (message) setError(message); }
  };

  useEffect(() => {
    mounted.current = true; let disposed = false;
    const poll = async () => {
      try {
        const result = await fetch("/api/rag/status");
        if (!result.ok) throw new Error();
        const status = await result.json();
        if (!disposed) setBackend(status);
        const map = await fetch("/api/rag/context");
        const context = await map.json();
        if (!disposed) {
          setMapContext(map.ok ? context : null);
          if (!map.ok) setError(context.error || "Saved map labels could not be loaded.");
        }
      } catch { if (!disposed) setBackend({ error: "Conversation backend unavailable. Reopen this tab to retry." }); }
    };
    poll(); const timer = setInterval(poll, 1500);
    return () => { disposed = true; mounted.current = false; clearInterval(timer); end(); };
  }, []);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages]);

  const start = async () => {
    if (!ready || !supported || active.current) return;
    active.current = true; const turn = ++generation.current;
    setError(""); setMessages([]); setNavigation(null); setPhase("Connecting"); setMuted(false); setPassengerName(name.trim());
    try {
      const capture = new LiveAudio(packet => {
        const ws = socket.current;
        if (ws?.readyState === WebSocket.OPEN && capture.connected) {
          if (ws.bufferedAmount > 256000) { end("Connection too slow for live audio. Reconnect on a stronger network."); return; }
          ws.send(packet);
        }
      }, playing => { if (mounted.current && generation.current === turn) setSpeaking(playing); }, (type, id) => {
        const ws = socket.current;
        if (generation.current === turn && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type, id }));
      });
      audio.current = capture;
      // Resume audio during this user gesture, before any network request.
      await capture.open();
      if (!mounted.current || turn !== generation.current) { capture.close(); return; }
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${location.host}/api/rag/live`); socket.current = ws;
      timeout.current = setTimeout(() => { if (turn === generation.current) end("Live connection timed out. Check your API key and network, then restart."); }, 30000);
      ws.onopen = () => { if (turn === generation.current) ws.send(JSON.stringify({ type: "start", language, name: name.trim(), sample_rate: capture.context.sampleRate })); };
      ws.onmessage = event => {
        if (turn !== generation.current) return;
        try {
          const result = JSON.parse(event.data);
          if (result.type === "ready") { clearTimeout(timeout.current); capture.connected = true; capture.finishReplies = !!result.finish_replies; setPhase("Listening"); }
          else if (result.type === "audio") capture.play(result.data, result.sample_rate);
          else if (result.type === "audio_file") capture.playFile(result.data, result.id).catch(err => {
            if (turn === generation.current) {
              if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'playback_error', id: result.id }));
              setError(`Reply audio could not play: ${err.message}. Please try again.`);
            }
          });
          else if (result.type === "passenger_name") setPassengerName(result.name);
          else if (result.type === "notice") setError(result.message);
          else if (result.type === "navigation") setNavigation(result);
          else if (result.type === "listening") capture.setListening(result.enabled);
          else if (result.type === "interrupted") { if (!capture.finishReplies) capture.interrupt(); setPhase("Listening"); }
          else if (result.type === "state") setPhase(result.state);
          else if (result.type === "turn_complete") setPhase("Listening");
          else if (result.type === "transcript") setMessages(previous => {
            const exists = previous.some(message => message.id === result.id);
            return (exists ? previous.map(message => message.id === result.id ? result : message) : [...previous, result]).slice(-40);
          });
          else if (result.type === "error") end(result.message);
        } catch (err) { end(err.message || "Live audio could not be played."); }
      };
      ws.onerror = () => { if (turn === generation.current) end("Live connection failed. Reopen the RAG tab and check the computer RAG log."); };
      ws.onclose = () => { if (turn === generation.current) end("Live conversation disconnected. Press Start conversation to reconnect."); };
    } catch (err) {
      if (turn === generation.current) end(err.name === "NotAllowedError" ? "Allow microphone access in your browser settings." : err.message);
    }
  };

  const toggleMute = () => {
    const value = !muted; audio.current?.mute(value); setMuted(value);
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type: "mute", value }));
  };
  const contextMatchesMap = mapContext?.map_id === session?.selected_map?.id;
  const destinations = contextMatchesMap ? mapContext?.locations || [] : [];
  const escortStatus = contextMatchesMap ? mapContext?.navigation : null;
  const escortGoal = escortStatus?.goal && !["canceled", "idle"].includes(escortStatus.state)
    ? { world: escortStatus.goal } : null;
  const remaining = escortStatus?.distance_remaining;
  const state = !running ? ready ? "Ready to talk" : backend?.ready && !backend.configured ? "API key required" : "Preparing" : muted ? "Microphone muted" : speaking ? "Assistant speaking" : phase;

  return <section className="ragpanel" aria-label="Live RAG conversation">
    <div className="cardhead"><span>AIRPORT PASSENGER ASSISTANT</span><span>{state}</span></div>
    <div className="ragbody">
      <p>Start once and talk naturally. Your airport assistant listens, replies aloud, and stays ready for your next question. Listen to the full reply, then speak when it finishes. The microphone pauses during replies to prevent speaker echo.</p>
      <div className="ragsettings">
        <label>Language<select value={language} disabled={running} onChange={event => setLanguage(event.target.value)}>{Object.entries(LANGUAGES).map(([code, title]) => <option key={code} value={code}>{title}</option>)}</select></label>
        <label>Your name (optional — or tell the assistant)<input maxLength={60} value={name} disabled={running} onChange={event => setName(event.target.value)} /></label>
      </div>
      {backend?.error && <p className="labelerror" role="status">{backend.error}</p>}
      {backend?.ready && !backend.configured && <p className="labelerror">Set GOOGLE_API_KEY on the computer or in the workspace .env, then restart the UI. Your key stays on the computer.</p>}
      {!supported && <p className="labelerror">Live microphone access needs HTTPS on your phone, or localhost on this computer, and a browser with AudioWorklet support.</p>}
      <div className="voice-navigation">
        <strong>Spoken destination → A* navigation</strong>
        <MapSessionPanel consoleSession={consoleSession} onSwitch={onSwitchMap}
          posePicking={posePicking} onPoseTool={onPoseTool} canPose={canPose}
          showNewMapping={false} disabled={running} />
        <div className="ragactions"><button disabled={changingMap || !onOpenMaps} onClick={() => { end(); onOpenMaps(); }}>Edit location labels</button></div>
        {session?.mode === "mapping" && <p className="hint">Live SLAM is active. Loading a saved map stops this mapping session; save your new map in Mapping first.</p>}
        {running && <p className="hint">End the conversation before changing maps.</p>}
        {session?.error && <p className="labelerror" role="status">{session.error}</p>}
        <p>{mapContext?.map_name ? `Map: ${mapContext.map_name} · ${mapContext.localized ? "Robot localized" : "Set the initial pose below"}` : "Load a saved map and set the robot pose here before requesting guidance."}</p>
        <p>{mapContext?.locations?.length ? `Saved destinations: ${mapContext.locations.map(item => item.text).join(", ")}` : "No saved destinations for the loaded map."}</p>
        <p>Say “Take me to [saved location]” to request guidance, or “Stop navigation” to cancel.</p>
        {navigation && <p role="status">{navigation.message || navigation.state}</p>}
        {navigationStatus && <p role="status">Robot: {navigationStatus.message}</p>}
      </div>
      <section className="mapcard ragmap" aria-label="Live conversation map">
        <div className="cardhead"><span>LIVE ROBOT MAP</span><span>{changingMap ? "Loading map…" : session?.selected_map?.name || "No map loaded"}</span></div>
        {map ? <MapView key={session?.selected_map?.id || "live"} map={changingMap ? null : map}
          robotPose={changingMap ? null : robotPose} path={changingMap ? [] : path}
          goal={changingMap || !map || !session?.localized ? null : escortGoal} labels={changingMap || !map ? [] : destinations}
          canSetGoal={false} posePicking={!running && posePicking} onInitialPose={onInitialPose} staticMap={session?.mode === "localization"} /> : <p className="hint">Load a saved map above to display it, then use 2D Pose Estimate to place the robot.</p>}
        <div className="legend"><span><i className="robot" /> Robot / heading</span><span><i className="route" /> Route</span><span><i className="target" /> Destination</span><span><i className="location" /> Saved location</span></div>
        <p className="ragmapstatus" role="status">{!connected ? "ROS disconnected — waiting for live robot updates." : !robotPose || !mapContext?.localized ? "Set the robot's initial pose here to track it on this map." : "Robot position updates live as it follows your spoken destination."}{Number.isFinite(remaining) && ` ${remaining.toFixed(2)} m remaining.`}</p>
      </section>
      <div className={`livevoice ${running ? "running" : ""} ${speaking ? "speaking" : ""}`}>
        <div className="voiceorb" aria-hidden="true">◉</div>
        <strong role="status">{state}</strong>
        {running && passengerName && <p>Passenger: {passengerName}</p>}
        <p>{running ? "Keep talking — no record or send buttons needed." : "Press Start conversation and allow the microphone."}</p>
      </div>
      <div className="ragactions liveactions">
        <button disabled={!ready || !supported || running || changingMap} onClick={start}>Start conversation</button>
        <button disabled={!running} onClick={() => end()}>End conversation</button>
        <button disabled={!running || phase === "Connecting"} onClick={toggleMute}>{muted ? "Unmute microphone" : "Mute microphone"}</button>
      </div>
      {error && <p className="labelerror" role="alert">{error}</p>}
      <details className="livetranscript" open><summary>Live transcript</summary>
        <div ref={log} className="ragmessages" role="log" aria-live="polite">
          {!messages.length && <p className="hint">Your conversation will appear here as you speak.</p>}
          {messages.map(message => <article key={message.id} className={message.role === "user" ? "human" : "assistant"}><strong>{message.role === "user" ? "You" : "Airport assistant"}</strong><p>{message.text}</p></article>)}
        </div>
      </details>
      <p className="hint">This device supplies the microphone and speaker. {backend?.pipeline === "gemini_live" ? "Native Gemini Live audio is enabled for this session." : "Audio is checked by WebRTC VAD and transcribed by Whisper on the computer. Gemini answers from retrieved airport knowledge; TTS plays replies here."} Leaving this tab ends the conversation. Spoken guidance uses saved map locations and A* navigation.</p>
    </div>
  </section>;
}
