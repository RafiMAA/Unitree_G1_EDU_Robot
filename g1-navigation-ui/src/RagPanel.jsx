import React, { useEffect, useRef, useState } from "react";

const LANGUAGES = { en: "English", fr: "Français", de: "Deutsch", es: "Español", ru: "Русский", ja: "日本語", zh: "中文", ko: "한국어", hi: "हिंदी", si: "සිංහල", ta: "தமிழ்" };
const base64 = blob => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result.split(",")[1]);
  reader.onerror = () => reject(new Error("Unable to read microphone recording"));
  reader.readAsDataURL(blob);
});
const audioUrl = audio => {
  const bytes = Uint8Array.from(atob(audio.data), char => char.charCodeAt(0));
  return URL.createObjectURL(new Blob([bytes], { type: audio.mime }));
};

const closeContext = ref => { const current = ref.current; ref.current = null; current?.close().catch(() => {}); };
const newSession = () => crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");

export default function RagPanel() {
  const [backend, setBackend] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en");
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [speak, setSpeak] = useState(true);
  const [autoSend, setAutoSend] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [replyAudio, setReplyAudio] = useState("");
  const session = useRef(newSession());
  const epoch = useRef(0), mounted = useRef(true), controller = useRef(null);
  const recorder = useRef(null), stream = useRef(null), context = useRef(null), meter = useRef(null), urls = useRef([]);
  const player = useRef(null), sendRef = useRef(null), log = useRef(null);
  const microphoneSupported = window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia) && Boolean(window.MediaRecorder);
  const ready = backend?.ready && backend?.configured;

  useEffect(() => {
    mounted.current = true;
    const poll = async () => {
      try {
        const response = await fetch("/api/rag/status");
        if (!response.ok) throw new Error("Conversation backend unavailable");
        const state = await response.json();
        if (mounted.current) setBackend(state);
      } catch { if (mounted.current) setBackend({ error: "Conversation backend unavailable. Restart the UI or reopen this tab." }); }
    };
    poll(); const timer = setInterval(poll, 1500);
    return () => {
      mounted.current = false; epoch.current += 1; clearInterval(timer);
      controller.current?.abort();
      if (meter.current) clearInterval(meter.current);
      if (recorder.current?.state === "recording") { recorder.current.onstop = null; recorder.current.stop(); }
      stream.current?.getTracks().forEach(track => track.stop());
      closeContext(context); player.current?.pause();
      urls.current.forEach(URL.revokeObjectURL);
      fetch("/api/rag/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: session.current }), keepalive: true }).catch(() => {});
    };
  }, []);

  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages]);

  const sendTurn = async values => {
    if (busy || !ready) return;
    const turn = epoch.current;
    const abort = new AbortController(); controller.current = abort;
    const timeout = setTimeout(() => abort.abort(), 185000);
    setBusy(true); setError(""); setNotice("");
    player.current?.pause();
    try {
      const response = await fetch("/api/rag/turn", { method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.signal,
        body: JSON.stringify({ session_id: session.current, name: name.trim(), language, speak, ...values }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Conversation request failed");
      if (!mounted.current || turn !== epoch.current) return;
      setMessages(previous => [...previous, { role: "You", text: result.transcript }, { role: "Airport assistant", text: result.answer }].slice(-40));
      if (result.audio) {
        const url = audioUrl(result.audio); urls.current.push(url);
        if (urls.current.length > 3) URL.revokeObjectURL(urls.current.shift());
        setReplyAudio(url);
        if (player.current) {
          player.current.src = url;
          try { await player.current.play(); }
          catch { setNotice("Tap the audio player to hear the reply."); }
        }
      }
      if (result.audio_error) setNotice(result.audio_error);
      setText("");
    } catch (err) {
      if (mounted.current && turn === epoch.current) setError(err.name === "AbortError" ? "The reply timed out. Please try again." : err.message);
    } finally {
      clearTimeout(timeout);
      if (mounted.current && turn === epoch.current) setBusy(false);
    }
  };
  sendRef.current = sendTurn;

  const stopRecording = () => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  };
  const startRecording = async () => {
    if (!ready || busy || recording) return;
    setError(""); setNotice(""); player.current?.pause();
    const turn = epoch.current;
    try {
      const input = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (!mounted.current || turn !== epoch.current) { input.getTracks().forEach(track => track.stop()); return; }
      stream.current = input;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find(type => MediaRecorder.isTypeSupported(type));
      const capture = new MediaRecorder(input, mime ? { mimeType: mime } : undefined);
      recorder.current = capture;
      const chunks = [];
      capture.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      capture.onerror = () => { setError("Microphone recording failed. Try again."); stopRecording(); };
      capture.onstop = async () => {
        clearInterval(meter.current); input.getTracks().forEach(track => track.stop()); closeContext(context);
        if (!mounted.current || turn !== epoch.current) return;
        setRecording(false);
        try {
          const blob = new Blob(chunks, { type: capture.mimeType });
          if (!blob.size || blob.size > 4 * 1024 * 1024) throw new Error("Keep recordings short (maximum 4 MB).");
          const audio = await base64(blob);
          if (mounted.current && turn === epoch.current) await sendRef.current({ audio });
        } catch (err) { if (mounted.current && turn === epoch.current) setError(err.message); }
      };
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      const audioContext = new AudioContext(); context.current = audioContext; await audioContext.resume();
      const analyser = audioContext.createAnalyser(); analyser.fftSize = 2048;
      audioContext.createMediaStreamSource(input).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      const started = performance.now(); let voiced = 0, lastVoice = started;
      // This browser meter ends a turn after a pause; the server independently
      // checks the actual recording with the existing Silero VAD before STT.
      meter.current = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        const now = performance.now();
        if (rms > 0.015) { voiced += 100; lastVoice = now; }
        if (now - started > 59000 || (autoSend && voiced >= 200 && now - lastVoice > 1100)) stopRecording();
      }, 100);
      capture.start(); setRecording(true);
    } catch (err) {
      stream.current?.getTracks().forEach(track => track.stop());
      closeContext(context);
      if (mounted.current) { setRecording(false); setError(err.name === "NotAllowedError" ? "Allow microphone access in your browser settings." : err.message); }
    }
  };

  const endConversation = () => {
    const old = session.current; epoch.current += 1; controller.current?.abort();
    if (recorder.current?.state === "recording") { recorder.current.onstop = null; recorder.current.stop(); }
    clearInterval(meter.current); stream.current?.getTracks().forEach(track => track.stop()); closeContext(context);
    player.current?.pause(); urls.current.forEach(URL.revokeObjectURL); urls.current = [];
    setRecording(false); setBusy(false); setMessages([]); setText(""); setReplyAudio(""); setError(""); setNotice("Conversation cleared.");
    session.current = newSession();
    fetch("/api/rag/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: old }) }).catch(() => {});
  };

  return <section className="ragpanel" aria-label="RAG conversation">
    <div className="cardhead"><span>AIRPORT PASSENGER ASSISTANT</span><span>{recording ? "Listening" : busy ? backend?.stage === "Ready" ? "Preparing reply" : backend?.stage : ready ? "Ready" : "Preparing"}</span></div>
    <div className="ragbody">
      <p>Ask about airport places and passenger services. Speak through this device or type a message.</p>
      <div className="ragsettings">
        <label>Language<select value={language} disabled={busy || recording} onChange={event => { endConversation(); setLanguage(event.target.value); }}>{Object.entries(LANGUAGES).map(([code, title]) => <option key={code} value={code}>{title}</option>)}</select></label>
        <label>Your name (optional)<input maxLength={60} value={name} disabled={busy || recording} onChange={event => setName(event.target.value)} /></label>
      </div>
      {backend?.error && <p className="labelerror" role="status">{backend.error}</p>}
      {backend?.ready && !backend.configured && <p className="labelerror">Set GOOGLE_API_KEY on the computer or in the workspace .env, then restart the UI. Your key stays on the computer.</p>}
      {!microphoneSupported && <p className="hint">Microphone access needs HTTPS on a phone, or localhost on this computer. Typed conversation is available here.</p>}
      <div ref={log} className="ragmessages" role="log" aria-live="polite">
        {!messages.length && <p className="hint">Hello! I’m your airport assistant. How can I help you?</p>}
        {messages.map((message, index) => <article key={index} className={message.role === "You" ? "human" : "assistant"}><strong>{message.role}</strong><p>{message.text}</p></article>)}
      </div>
      <form onSubmit={event => { event.preventDefault(); if (text.trim() && !recording) sendTurn({ text: text.trim() }); }}>
        <label htmlFor="rag-message">Message</label><textarea id="rag-message" maxLength={2000} value={text} disabled={busy || recording} onChange={event => setText(event.target.value)} placeholder="Where can I find baggage claim?" />
        <div className="ragactions">
          <button type="submit" disabled={!ready || busy || recording || !text.trim()}>Send message</button>
          <button type="button" className={recording ? "active" : ""} disabled={!ready || busy || !microphoneSupported || backend?.microphone_available === false} onClick={recording ? stopRecording : startRecording}>{recording ? "Stop & send recording" : "Record voice"}</button>
          <button type="button" onClick={endConversation}>Stop / Clear conversation</button>
        </div>
      </form>
      <div className="ragpreferences">
        <label><input type="checkbox" checked={speak} disabled={busy || recording} onChange={event => setSpeak(event.target.checked)} />Speak replies on this device</label>
        <label><input type="checkbox" checked={autoSend} disabled={recording} onChange={event => setAutoSend(event.target.checked)} />Send recording after a pause</label>
      </div>
      <audio ref={player} src={replyAudio || undefined} controls hidden={!replyAudio} aria-label="Assistant reply audio" />
      {error && <p className="labelerror" role="alert">{error}</p>}
      {notice && <p className="labelnotice" role="status">{notice}</p>}
      <p className="hint">Selecting this tab pauses robot control. Conversation provides spoken guidance; navigation is controlled in the Navigate tab.</p>
    </div>
  </section>;
}
