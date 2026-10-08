import React, { useEffect, useRef, useState } from "react";
import { LiveAudio } from "./liveAudio.js";

const LANGUAGES = { en: "English", fr: "Français", de: "Deutsch", es: "Español", ru: "Русский", ja: "日本語", zh: "中文", ko: "한국어", hi: "हिंदी", si: "සිංහල", ta: "தமிழ்" };

export default function RagPanel({ navigationStatus, consoleSession, onOpenMaps }) {
  const [backend, setBackend] = useState(null);
  const [mapContext, setMapContext] = useState(null);
  const [navigation, setNavigation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en");
  const [phase, setPhase] = useState("idle");
  const [speaking, setSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState("");
  const [selectedMap, setSelectedMap] = useState("");
  const [mapBusy, setMapBusy] = useState(false);
  const session = consoleSession?.session;
  const mounted = useRef(false), generation = useRef(0), socket = useRef(null), audio = useRef(null), log = useRef(null), timeout = useRef(null), active = useRef(false);
  const supported = window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia) && Boolean(window.AudioWorkletNode);
  const ready = backend?.ready && backend?.configured;
  const running = phase !== "idle";
  const changingMap = mapBusy || session?.transitioning;

  useEffect(() => {
    setSelectedMap(session?.selected_map?.id || "");
  }, [session?.selected_map?.id]);

  const end = (message = "") => {
    active.current = false; generation.current += 1; clearTimeout(timeout.current);
    const ws = socket.current; socket.current = null;
    if (ws) { ws.onclose = ws.onerror = ws.onmessage = null; ws.close(); }
    const capture = audio.current; audio.current = null; capture?.close();
    if (mounted.current) { setPhase("idle"); setSpeaking(false); setMuted(false); if (message) setError(message); }
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
    setError(""); setMessages([]); setNavigation(null); setPhase("Connecting"); setMuted(false);
    try {
      const capture = new LiveAudio(packet => {
        const ws = socket.current;
        if (ws?.readyState === WebSocket.OPEN && capture.connected) {
          if (ws.bufferedAmount > 256000) { end("Connection too slow for live audio. Reconnect on a stronger network."); return; }
          ws.send(packet);
        }
      }, playing => { if (mounted.current && generation.current === turn) setSpeaking(playing); });
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
          if (result.type === "ready") { clearTimeout(timeout.current); capture.connected = true; setPhase("Listening"); }
          else if (result.type === "audio") capture.play(result.data, result.sample_rate);
          else if (result.type === "audio_file") capture.playFile(result.data).catch(err => { if (turn === generation.current) end(err.message); });
          else if (result.type === "notice") setError(result.message);
          else if (result.type === "navigation") setNavigation(result);
          else if (result.type === "interrupted") { capture.interrupt(); setPhase("Listening"); }
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
  const loadMap = async () => {
    if (!selectedMap || running || changingMap) return;
    setMapBusy(true); setError(""); setMessages([]); setNavigation(null); setMapContext(null);
    try {
      await consoleSession.request("/api/session", { mode: "localization", map_id: selectedMap, tab: "rag" });
    } catch (err) { setError(err.message); }
    finally { setMapBusy(false); }
  };
  const state = !running ? ready ? "Ready to talk" : backend?.ready && !backend.configured ? "API key required" : "Preparing" : muted ? "Microphone muted" : speaking ? "Assistant speaking" : phase;

  return <section className="ragpanel" aria-label="Live RAG conversation">
    <div className="cardhead"><span>AIRPORT PASSENGER ASSISTANT</span><span>{state}</span></div>
    <div className="ragbody">
      <p>Start once and talk naturally. Your airport assistant listens, replies aloud, and stays ready for your next question. You can interrupt while it is speaking.</p>
      <div className="ragsettings">
        <label>Language<select value={language} disabled={running} onChange={event => setLanguage(event.target.value)}>{Object.entries(LANGUAGES).map(([code, title]) => <option key={code} value={code}>{title}</option>)}</select></label>
        <label>Your name (optional)<input maxLength={60} value={name} disabled={running} onChange={event => setName(event.target.value)} /></label>
      </div>
      {backend?.error && <p className="labelerror" role="status">{backend.error}</p>}
      {backend?.ready && !backend.configured && <p className="labelerror">Set GOOGLE_API_KEY on the computer or in the workspace .env, then restart the UI. Your key stays on the computer.</p>}
      {!supported && <p className="labelerror">Live microphone access needs HTTPS on your phone, or localhost on this computer, and a browser with AudioWorklet support.</p>}
      <div className="voice-navigation">
        <strong>Spoken destination → Nav2</strong>
        <label>Conversation map<select aria-label="Conversation map" value={selectedMap} disabled={running || changingMap || !session} onChange={event => setSelectedMap(event.target.value)}>
          <option value="">Choose a labeled saved map</option>
          {(session?.maps || []).map(map => <option key={map.id} value={map.id}>{map.name}</option>)}
        </select></label>
        <div className="ragactions">
          <button disabled={running || changingMap || !selectedMap} onClick={loadMap}>{changingMap ? "Loading map…" : "Load conversation map"}</button>
          <button disabled={changingMap || !consoleSession} onClick={async () => { try { await consoleSession.refresh(); } catch (err) { setError(err.message); } }}>Refresh maps</button>
          <button disabled={changingMap || !onOpenMaps} onClick={() => { end(); onOpenMaps(); }}>Set robot pose / edit labels</button>
        </div>
        {session?.mode === "mapping" && <p className="hint">Live SLAM is active. Loading a saved map stops this mapping session; save your new map in Mapping first.</p>}
        {running && <p className="hint">End the conversation before changing maps.</p>}
        {session?.error && <p className="labelerror" role="status">{session.error}</p>}
        <p>{mapContext?.map_name ? `Map: ${mapContext.map_name} · ${mapContext.localized ? "Robot localized" : "Set initial pose in Maps & Localization"}` : "Load a saved map and set the robot pose in Maps & Localization before requesting guidance."}</p>
        <p>{mapContext?.locations?.length ? `Saved destinations: ${mapContext.locations.map(item => item.text).join(", ")}` : "No saved destinations for the loaded map."}</p>
        <p>Say “Take me to [saved location]” to request guidance, or “Stop navigation” to cancel.</p>
        {navigation && <p role="status">{navigation.message || navigation.state}</p>}
        {navigationStatus && <p role="status">Robot: {navigationStatus.message}</p>}
      </div>
      <div className={`livevoice ${running ? "running" : ""} ${speaking ? "speaking" : ""}`}>
        <div className="voiceorb" aria-hidden="true">◉</div>
        <strong role="status">{state}</strong>
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
      <p className="hint">This device supplies the microphone and speaker. {backend?.pipeline === "gemini_live" ? "Native Gemini Live audio is enabled for this session." : "Audio is checked by WebRTC VAD and transcribed by Whisper on the computer. Gemini answers from retrieved airport knowledge; TTS plays replies here."} Leaving this tab ends the conversation. Spoken guidance uses saved map locations and Nav2.</p>
    </div>
  </section>;
}
