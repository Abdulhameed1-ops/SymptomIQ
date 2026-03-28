/* ══════════════════════════════════════════════════════════
   SymptomIQ — app.js
   • Text mode: ZERO TTS — silent, clean chat
   • Voice mode: speech → /correct → /chat → AI speaks back
   • Interrupt: tap mic while AI speaks → AI stops → listen
   • Source badges rendered under each AI message
   • Full WebGL orb with touch/speak/listen states
══════════════════════════════════════════════════════════ */

// ─── SOURCE METADATA (matches backend SOURCE_META keys) ───────
const SOURCE_META = {
  "MedlinePlus": {
    label: "MedlinePlus",
    logo:  "https://medlineplus.gov/images/medlineplus-logo.png",
    url:   "https://medlineplus.gov",
  },
  "WHO": {
    label: "WHO",
    logo:  "https://www.who.int/ResourcePackages/WHO/assets/dist/images/logos/en/h-logo-blue.svg",
    url:   "https://www.who.int",
  },
  "CDC": {
    label: "CDC",
    logo:  "https://www.cdc.gov/TemplatePackage/4.0/assets/imgs/favicon/apple-touch-icon.png",
    url:   "https://www.cdc.gov",
  },
};

// ─── APP STATE ─────────────────────────────────────────────────
let sessionData = {
  symptoms: [], asked_question_ids: [],
  answers: {}, stage: "start", question_count: 0,
};
let chatHistory  = [];
let isVoiceMode  = false;
let isListening  = false;
let isSpeaking   = false;
let voiceMat     = null;   // Three.js material — set by initOrb()

// ─── ELEMENT REFS ──────────────────────────────────────────────
const chatModeEl       = document.getElementById("chatMode");
const voiceModeEl      = document.getElementById("voiceMode");
const messagesEl       = document.getElementById("messages");
const textInput        = document.getElementById("textInput");
const sendBtn          = document.getElementById("sendBtn");
const toVoiceBtn       = document.getElementById("toVoiceBtn");
const toChatBtn        = document.getElementById("toChatBtn");
const newChatBtn       = document.getElementById("newChatBtn");
const voiceLabelEl     = document.getElementById("voiceLabel");
const voiceTranscript  = document.getElementById("voiceTranscript");
const orbTapBtn        = document.getElementById("orbTapBtn");


// ══════════════════════════════════════════════════════════════
//  GREETING
// ══════════════════════════════════════════════════════════════
function showGreeting() {
  addAI(
    "Hi, I'm SymptomIQ. Tell me what you're experiencing — " +
    "such as fever, headache, or vomiting — and I'll ask a few questions " +
    "to help you understand your situation.",
    null, null, null, []
  );
}
document.addEventListener("DOMContentLoaded", showGreeting);


// ══════════════════════════════════════════════════════════════
//  TEXT INPUT HANDLERS
// ══════════════════════════════════════════════════════════════
sendBtn.onclick = () => {
  const t = textInput.value.trim();
  if (!t) return;
  textInput.value = "";
  sendMessage(t, false);   // false → no TTS
};

textInput.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendBtn.click(); }
});

newChatBtn.onclick = () => {
  sessionData = { symptoms:[], asked_question_ids:[], answers:{}, stage:"start", question_count:0 };
  chatHistory = [];
  messagesEl.innerHTML = "";
  showGreeting();
};


// ══════════════════════════════════════════════════════════════
//  RENDER HELPERS
// ══════════════════════════════════════════════════════════════
function esc(s) {
  return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function addUser(text) {
  const w = document.createElement("div");
  w.className = "msg user";
  w.innerHTML = `<div class="msgLabel">You</div><div class="msgText">${esc(text)}</div>`;
  messagesEl.appendChild(w);
  scrollBottom();
}

function addAI(text, type, risk, recommendation, sources) {
  const w = document.createElement("div");
  w.className = "msg ai";

  let inner = `<div class="msgLabel">SymptomIQ</div><div class="msgText">${esc(text)}</div>`;
  w.innerHTML = inner;

  // Assessment card
  if (type === "assessment" && risk) {
    w.appendChild(makeCard(risk, recommendation));
  }

  // Source badges
  if (sources && sources.length > 0) {
    const badges = document.createElement("div");
    badges.className = "sourceBadges";
    sources.forEach(key => {
      const meta = SOURCE_META[key];
      if (!meta) return;
      const a = document.createElement("a");
      a.className = "srcBadge";
      a.href      = meta.url;
      a.target    = "_blank";
      a.rel       = "noopener noreferrer";
      a.title     = `Source: ${meta.label}`;
      a.innerHTML = `
        <img src="${meta.logo}" class="srcLogo" alt="${meta.label}"
             onerror="this.style.display='none'">
        ${esc(meta.label)}
      `;
      badges.appendChild(a);
    });
    w.appendChild(badges);
  }

  messagesEl.appendChild(w);
  scrollBottom();
}

function makeCard(risk, rec) {
  const c = document.createElement("div");
  c.className = "card";
  c.innerHTML = `
    <div class="riskRow">
      <div class="riskDot ${risk}"></div>
      <div class="riskLabel ${risk}">${risk} Risk</div>
    </div>
    <div class="recText">${esc(rec)}</div>
    <div class="cardDisclaimer">
      <i class="ri-information-line"></i>
      This is not a medical diagnosis. Always consult a qualified doctor.
    </div>
  `;
  return c;
}

function showTyping() {
  const w = document.createElement("div");
  w.className = "msg ai"; w.id = "typing";
  w.innerHTML = `<div class="msgLabel">SymptomIQ</div>
    <div class="dots"><span></span><span></span><span></span></div>`;
  messagesEl.appendChild(w);
  scrollBottom();
}
function hideTyping() { const el = document.getElementById("typing"); if (el) el.remove(); }
function scrollBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }


// ══════════════════════════════════════════════════════════════
//  SEND TO BACKEND
//  useTTS: true = voice mode (AI speaks back), false = text mode (silent)
// ══════════════════════════════════════════════════════════════
async function sendMessage(text, useTTS) {
  if (!text) return;

  addUser(text);
  chatHistory.push({ role: "user", content: text });

  showTyping();
  setInputEnabled(false);

  try {
    const resp = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        history: chatHistory.slice(-14),
        session: sessionData,
      }),
    });

    const data = await resp.json();
    hideTyping();

    if (data.session) sessionData = data.session;

    addAI(data.reply, data.type, data.risk, data.recommendation, data.sources || []);
    chatHistory.push({ role: "assistant", content: data.reply });

    // ── TTS only in voice mode ──────────────────────────
    if (useTTS) speak(data.reply);

  } catch {
    hideTyping();
    addAI("Connection issue — please check your network and try again.", null, null, null, []);
  } finally {
    setInputEnabled(true);
    if (!isVoiceMode) textInput.focus();
  }
}

function setInputEnabled(on) {
  textInput.disabled       = !on;
  sendBtn.disabled         = !on;
  sendBtn.style.opacity    = on ? "1" : "0.45";
}


// ══════════════════════════════════════════════════════════════
//  SCREEN SWITCHING
// ══════════════════════════════════════════════════════════════
toVoiceBtn.onclick = () => {
  isVoiceMode = true;
  chatModeEl.classList.remove("active");
  voiceModeEl.classList.add("active");
};

toChatBtn.onclick = () => {
  isVoiceMode = false;
  stopListening();
  stopSpeaking();
  voiceModeEl.classList.remove("active");
  chatModeEl.classList.add("active");
};


// ══════════════════════════════════════════════════════════════
//  SPEECH RECOGNITION
// ══════════════════════════════════════════════════════════════
let recognition = null;

if ("SpeechRecognition" in window || "webkitSpeechRecognition" in window) {
  recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
  recognition.continuous     = false;
  recognition.interimResults = true;
  recognition.lang           = "en-US";

  recognition.onstart = () => {
    isListening = true;
    setOrbTouch(1.0);
    voiceLabelEl.textContent = "Listening…";
    voiceLabelEl.classList.add("active");
    orbTapBtn.className = "tapBtn listening";
    orbTapBtn.innerHTML = '<i class="ri-stop-circle-line"></i>';
  };

  recognition.onresult = (event) => {
    let interim = "", final = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) final   += event.results[i][0].transcript;
      else                          interim += event.results[i][0].transcript;
    }
    voiceTranscript.textContent = final || interim;

    if (final.trim()) {
      recognition.stop();
      handleVoiceInput(final.trim());
    }
  };

  recognition.onerror = () => stopListening();
  recognition.onend   = () => {
    isListening = false;
    if (!isSpeaking) resetOrbIdle();
  };
}

function startListening() {
  if (!recognition) {
    alert("Voice recognition is not supported in this browser. Please use Chrome or Safari.");
    return;
  }
  voiceTranscript.textContent = "";
  try { recognition.start(); } catch { /* already started */ }
}

function stopListening() {
  if (recognition && isListening) recognition.stop();
}

function resetOrbIdle() {
  setOrbTouch(0.0);
  voiceLabelEl.textContent = "Tap to speak";
  voiceLabelEl.classList.remove("active");
  orbTapBtn.className = "tapBtn";
  orbTapBtn.innerHTML = '<i class="ri-mic-fill"></i>';
  voiceTranscript.textContent = "";
}


// ── ORB TAP — handles speak/listen/interrupt ──────────────────
orbTapBtn.onclick = () => {
  if (isSpeaking) {
    stopSpeaking();
    setTimeout(startListening, 160);
    return;
  }
  if (isListening) {
    stopListening();
  } else {
    startListening();
  }
};


// ══════════════════════════════════════════════════════════════
//  VOICE PIPELINE
//  raw transcript → /correct (AI cleans it) → /chat with TTS
// ══════════════════════════════════════════════════════════════
async function handleVoiceInput(rawText) {
  voiceLabelEl.textContent = "Processing…";
  voiceTranscript.textContent = rawText;

  // Step 1 — AI autocorrect
  let cleanText = rawText;
  try {
    const r = await fetch("/correct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: rawText }),
    });
    const d = await r.json();
    if (d.corrected && d.corrected.trim()) cleanText = d.corrected.trim();
  } catch { /* use raw if correction fails */ }

  voiceTranscript.textContent = cleanText;

  // Step 2 — small pause so user sees corrected text
  await new Promise(res => setTimeout(res, 500));

  // Step 3 — switch to chat screen, send with TTS flag
  voiceModeEl.classList.remove("active");
  chatModeEl.classList.add("active");
  isVoiceMode = false;
  voiceTranscript.textContent = "";

  await sendMessage(cleanText, true);   // true → speak AI reply
}


// ══════════════════════════════════════════════════════════════
//  TTS — ONLY called from voice pipeline
// ══════════════════════════════════════════════════════════════
function speak(text) {
  if (!("speechSynthesis" in window)) return;
  stopSpeaking();

  const clean = text.replace(/[\u{1F000}-\u{1FAFF}]/gu, "").trim();
  if (!clean) return;

  const utt     = new SpeechSynthesisUtterance(clean);
  utt.rate      = 0.95;
  utt.pitch     = 1.0;
  utt.volume    = 1.0;

  const go = () => {
    const voices = speechSynthesis.getVoices();
    const voice  =
      voices.find(v => v.name.includes("Google") && v.lang.startsWith("en")) ||
      voices.find(v => v.lang.startsWith("en-US") && !v.name.toLowerCase().includes("zira")) ||
      voices.find(v => v.lang.startsWith("en")) ||
      voices[0];
    if (voice) utt.voice = voice;

    utt.onstart = () => {
      isSpeaking = true;
      setOrbTouch(0.65);
      voiceLabelEl.textContent = "Speaking…";
      voiceLabelEl.classList.add("active");
      orbTapBtn.className = "tapBtn speaking";
      orbTapBtn.innerHTML = '<i class="ri-mic-fill"></i>';
      // Switch back to voice screen so user can see orb while AI speaks
      chatModeEl.classList.remove("active");
      voiceModeEl.classList.add("active");
      isVoiceMode = true;
    };

    utt.onend = utt.onerror = () => {
      isSpeaking = false;
      resetOrbIdle();
    };

    speechSynthesis.speak(utt);
  };

  if (speechSynthesis.getVoices().length === 0) {
    speechSynthesis.addEventListener("voiceschanged", go, { once: true });
  } else {
    go();
  }
}

function stopSpeaking() {
  if ("speechSynthesis" in window) { speechSynthesis.cancel(); }
  isSpeaking = false;
}


// ══════════════════════════════════════════════════════════════
//  WEBGL ORB — exact shader (fbm noise, fresnel, breathing, touch)
// ══════════════════════════════════════════════════════════════
function setOrbTouch(v) { if (voiceMat) voiceMat.uniforms.touch.value = v; }

(function initOrb() {
  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
  camera.position.z = 3;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  const sz = Math.min(window.innerWidth * 0.68, 240);
  renderer.setSize(sz, sz);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  document.getElementById("orbContainer").appendChild(renderer.domElement);

  const geo = new THREE.SphereGeometry(1, 128, 128);

  voiceMat = new THREE.ShaderMaterial({
    uniforms: { time: { value: 0 }, touch: { value: 0 } },

    vertexShader: `
      varying vec3 vPosition;
      varying vec3 vNormal;
      void main(){
        vPosition = position;
        vNormal   = normal;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);
      }
    `,

    fragmentShader: `
      varying vec3 vPosition;
      varying vec3 vNormal;
      uniform float time;
      uniform float touch;

      float hash(vec3 p){ return fract(sin(dot(p,vec3(127.1,311.7,74.7)))*43758.5453); }

      float noise(vec3 p){
        vec3 i=floor(p); vec3 f=fract(p);
        f=f*f*(3.0-2.0*f);
        return mix(
          mix(mix(hash(i+vec3(0,0,0)),hash(i+vec3(1,0,0)),f.x),
              mix(hash(i+vec3(0,1,0)),hash(i+vec3(1,1,0)),f.x),f.y),
          mix(mix(hash(i+vec3(0,0,1)),hash(i+vec3(1,0,1)),f.x),
              mix(hash(i+vec3(0,1,1)),hash(i+vec3(1,1,1)),f.x),f.y),
          f.z);
      }

      float fbm(vec3 p){
        float v=0.0,a=0.5;
        for(int i=0;i<5;i++){ v+=a*noise(p); p*=2.0; a*=0.5; }
        return v;
      }

      void main(){
        vec3 p    = vPosition*2.5;
        float n   = fbm(p + time*0.3);
        float fog = smoothstep(0.2,0.8,n);

        vec3 green = vec3(0.0,0.75,0.4);
        vec3 white = vec3(1.0);
        vec3 color = mix(green,white,fog);

        float pulse = 0.5+0.5*sin(time*1.5);
        float glow  = (1.2-length(vPosition))*(0.6+pulse*0.3);
        color += glow;

        float fresnel = pow(1.0-dot(normalize(vNormal),vec3(0,0,1)),2.0);
        color += fresnel*0.9;

        float shine = smoothstep(0.5,1.0,vPosition.y);
        color += vec3(1.0)*shine*0.5;

        color += touch*0.6;
        gl_FragColor = vec4(color,1.0);
      }
    `,
  });

  const sphere = new THREE.Mesh(geo, voiceMat);
  scene.add(sphere);

  (function animate(){
    requestAnimationFrame(animate);
    voiceMat.uniforms.time.value += 0.01;
    renderer.render(scene, camera);
  })();
})();
