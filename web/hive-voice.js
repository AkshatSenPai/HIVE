/* HIVE owner-channel voice — a self-contained floating widget.
 *
 * Injected on every page via <script src="hive-voice.js"> (like hive-transitions.js).
 * It renders its own overlay and talks to the API — it never touches the
 * Claude-Design-generated pages, so a page re-export won't wipe it out.
 *
 * Push-to-talk: hold the mic (or the Space key) to speak. Your words are
 * transcribed by the backend (Whisper) and routed by intent:
 *   brief / status   -> HIVE speaks a short prioritized briefing
 *   review approvals -> HIVE reads each pending card; you say approve/reject/skip
 *   (anything else)   -> dispatched as a command (POST /jobs)
 * HIVE speaks back via the backend (Kokoro). Consequential approvals require a
 * spoken read-back + an affirmative "confirm" (negations abort). The kill
 * switch blocks command dispatch AND approvals — enforced server-side too.
 */
(function () {
  'use strict';

  var busy = false;          // a turn is in flight
  var recorder = null;       // active recorder while holding to talk
  var holding = false;       // button/Space currently held (guards the start/stop race)
  var backend = 'unknown';

  // ---------------------------------------------------------------- audio i/o

  function supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
      (window.AudioContext || window.webkitAudioContext));
  }

  // Capture mic as 16 kHz mono 16-bit WAV via the Web Audio API (no MediaRecorder
  // -> no webm -> no server-side ffmpeg). Returns a recorder with .stop() -> Blob.
  async function startRecording() {
    var stream = await navigator.mediaDevices.getUserMedia({
      // AGC lifts quiet speech; NS/EC clean the room — all raise STT accuracy.
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    var Ctx = window.AudioContext || window.webkitAudioContext;
    // Ask the browser to run the graph at 16 kHz so IT does the properly
    // anti-aliased resample to Whisper's rate — far better than decimating
    // ourselves. Falls back to the native rate if the option isn't honored.
    var ctx;
    try { ctx = new Ctx({ sampleRate: 16000 }); } catch (_) { ctx = new Ctx(); }
    var source = ctx.createMediaStreamSource(stream);
    // Low-pass just under Nyquist so that IF the browser ignored the 16 kHz
    // request, our fallback decimation in encodeWav can't alias the fricatives
    // (f / s / th) that tell "brief" from "breeze" / "breath" / "peace".
    var lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 7500;
    var proc = ctx.createScriptProcessor(4096, 1, 1);
    var chunks = [];
    proc.onaudioprocess = function (e) {
      chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    source.connect(lp);
    lp.connect(proc);
    proc.connect(ctx.destination); // output stays silent (we never fill it)
    return {
      stop: async function () {
        proc.disconnect();
        lp.disconnect();
        source.disconnect();
        stream.getTracks().forEach(function (t) { t.stop(); });
        var rate = ctx.sampleRate;
        await ctx.close();
        var len = chunks.reduce(function (a, c) { return a + c.length; }, 0);
        var flat = new Float32Array(len);
        var o = 0;
        chunks.forEach(function (c) { flat.set(c, o); o += c.length; });
        return encodeWav(flat, rate);
      },
    };
  }

  function encodeWav(float32, sampleRate) {
    var target = 16000;
    var ratio = sampleRate / target;
    var outLen = Math.max(1, Math.floor(float32.length / ratio));
    var pcm = new Int16Array(outLen);
    for (var i = 0; i < outLen; i++) {
      var s = float32[Math.floor(i * ratio)] || 0;
      s = Math.max(-1, Math.min(1, s));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    var buf = new ArrayBuffer(44 + pcm.length * 2);
    var dv = new DataView(buf);
    var w = function (off, str) { for (var k = 0; k < str.length; k++) dv.setUint8(off + k, str.charCodeAt(k)); };
    w(0, 'RIFF'); dv.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVE');
    w(12, 'fmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, target, true); dv.setUint32(28, target * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    w(36, 'data'); dv.setUint32(40, pcm.length * 2, true);
    for (var j = 0; j < pcm.length; j++) dv.setInt16(44 + j * 2, pcm[j], true);
    return new Blob([buf], { type: 'audio/wav' });
  }

  // Record for a bounded window (hands-free turns in the review loop).
  async function listenWindow(ms) {
    var rec = await startRecording();
    setStatus('listening', 'listening…');
    await sleep(ms);
    var wav = await rec.stop();
    return transcribe(wav);
  }

  // ---------------------------------------------------------------- api

  async function transcribe(wavBlob) {
    setStatus('thinking', 'transcribing…');
    var res = await fetch('/voice/transcribe', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wavBlob });
    if (!res.ok) throw new Error('transcribe ' + res.status);
    return (await res.json()).text || '';
  }

  async function speak(text) {
    if (!text) return;
    setStatus('speaking', text);
    try {
      var res = await fetch('/voice/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text }) });
      if (!res.ok) return;
      var url = URL.createObjectURL(await res.blob());
      var audio = new Audio(url);
      await new Promise(function (r) { audio.onended = r; audio.onerror = r; audio.play().catch(r); });
      URL.revokeObjectURL(url);
    } catch (_) { /* keep going */ }
  }

  function getJSON(path) {
    return fetch(path).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  // ---------------------------------------------------------------- intents

  // Short, keyword-dominated utterances are meta-commands; longer utterances
  // that merely contain a keyword (e.g. "review the pricing and summarize")
  // are real work and fall through to command dispatch.
  function classify(t) {
    var s = (t || '').toLowerCase().trim();
    var words = s ? s.split(/\s+/).length : 0;
    if (/\b(brief|briefing|catch me up|sit ?rep)\b/.test(s) ||
        (words <= 5 && /\b(what'?s happening|what is happening|status|digest)\b/.test(s))) return 'brief';
    if (/\breview\s+(the\s+)?(approvals?|queue)\b/.test(s) || /^approvals?\b/.test(s) ||
        (words <= 5 && /\b(anything waiting|what needs me|pending approvals?)\b/.test(s))) return 'approvals';
    return 'command';
  }

  // Decision parse: explicit stop/skip/reject are checked before approve, and
  // ANY negation blocks 'approve' (so "go ahead and reject" / "don't approve"
  // never resolve to an approval).
  function decision(t) {
    var s = (t || '').toLowerCase();
    var negated = /\b(no|nope|don'?t|do not|not|never ?mind)\b/.test(s);
    if (/\b(stop|quit|exit|cancel|that'?s all)\b/.test(s)) return 'stop';
    if (/\b(skip|next|pass|move on)\b/.test(s)) return 'skip';
    if (/\b(reject|deny|decline)\b/.test(s)) return 'reject';
    if (!negated && /\b(approve|accept|go ahead|send it)\b/.test(s)) return 'approve';
    return 'unclear';
  }

  // Confirm gate: affirmative-only, and any negation/abort word vetoes it — so
  // "no, don't do it" can never satisfy a read-back confirm.
  function isYes(t) {
    var s = (t || '').toLowerCase();
    if (/\b(no|nope|don'?t|do not|not|stop|cancel|wait|never ?mind|abort|hold on)\b/.test(s)) return false;
    return /\b(confirm|confirmed|yes|yeah|yep|approve|approved)\b/.test(s);
  }

  function moneyPhrase(u) {
    u = u || 0;
    if (u < 1) { var c = Math.round(u * 100); return c + ' cent' + (c === 1 ? '' : 's'); }
    return (u === Math.round(u) ? u.toLocaleString('en-US') : u.toFixed(2)) + ' ' + (u === 1 ? 'dollar' : 'dollars');
  }
  function shortId(id) { return (id || '').replace(/^job_/, ''); }

  async function handle(transcript) {
    if (!transcript.trim()) { await speak("I didn't catch that."); return; }
    setTranscript('“' + transcript + '”');
    var intent = classify(transcript);
    if (intent === 'brief') {
      var b = await getJSON('/brief');
      await speak(b ? b.text : "I couldn't build a brief right now.");
    } else if (intent === 'approvals') {
      await reviewApprovals();
    } else {
      await handleCommand(transcript);
    }
  }

  async function handleCommand(text) {
    var health = await getJSON('/health');
    if (!health) { await speak("I can't reach HIVE right now, so I'm not dispatching that."); return; } // fail closed
    if (health.kill_switch) { await speak('HIVE is paused. Release the kill switch to dispatch commands.'); return; }
    var res = await fetch('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject: text }) });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) { await speak("I couldn't start that — no workflow handles that request."); return; }
    var job = data.job || {};
    var msg = 'Dispatched. Job ' + shortId(job.id) + ' is ' + String(job.state || '').replace(/_/g, ' ') + '.';
    if (data.pending_cards && data.pending_cards.length) msg += ' It needs your approval — say review approvals to hear it.';
    await speak(msg);
  }

  async function reviewApprovals() {
    var health = await getJSON('/health');
    if (health && health.kill_switch) { await speak("HIVE is paused — the kill switch is engaged. I can't act on approvals right now."); return; }
    var data = await getJSON('/approvals');
    var cards = (data && data.cards) || [];
    if (!cards.length) { await speak('Nothing is waiting on you. All clear.'); return; }
    await speak('You have ' + cards.length + ' approval' + (cards.length === 1 ? '' : 's') + ' to review.');
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      try {
        var consequential = c.action_kind && c.action_kind !== 'internal';
        var effects = (c.downstream_effects || []).slice(0, 2).join('. ');
        await speak((i + 1) + ' of ' + cards.length + ': ' + c.title + '. ' + moneyPhrase(c.cost_so_far_usd) + '. ' +
          (effects ? effects + '. ' : '') + 'Approve, reject, or skip?');
        var d = decision(await listenWindow(4500));
        if (d === 'stop') { await speak('Stopping the review.'); return; }
        if (d === 'skip') continue;
        if (d === 'unclear') { await speak("I didn't catch a clear decision — skipping this one."); continue; }
        if (consequential) {
          await speak('You said ' + d + '. ' + (d === 'approve'
            ? 'This will ' + ((c.downstream_effects && c.downstream_effects[0]) || 'run the action') + '. '
            : '') + 'Say confirm, or no to cancel.');
          if (!isYes(await listenWindow(3500))) { await speak('Cancelled. Moving on.'); continue; }
        }
        var res = await postDecision(c.id, d);
        if (!res.ok) {
          var detail = (res.data && res.data.detail) || '';
          if (res.status === 409 && /paused|kill switch/i.test(detail)) { await speak('HIVE is paused. Stopping the review.'); return; }
          await speak("That didn't go through" + (detail ? ': ' + detail : '') + '. Moving on.');
          continue;
        }
        var job = res.data.job;
        if (job && job.state === 'failed') await speak('Approved, but the action failed: ' + (job.error || 'unknown error') + '.');
        else await speak((d === 'approve' ? 'Approved' : 'Rejected') + '. Job is now ' + String(job ? job.state : 'updated').replace(/_/g, ' ') + '.');
      } catch (e) {
        await speak('I had trouble with that one — skipping it.'); // a mic/transcribe hiccup skips one card, not the queue
      }
    }
    await speak("That's everything in the queue.");
  }

  // Returns { ok, status, data } so the caller can distinguish success, a
  // paused/already-decided 409, and a hard failure — never a false "Approved".
  function postDecision(cardId, d) {
    return fetch('/approvals/' + cardId + '/decision', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision: d, note: 'via voice' }),
    }).then(function (r) {
      return r.json().then(
        function (j) { return { ok: r.ok, status: r.status, data: j }; },
        function () { return { ok: r.ok, status: r.status, data: {} }; }
      );
    }).catch(function () { return { ok: false, status: 0, data: {} }; });
  }

  // ---------------------------------------------------------------- turn control

  async function runTurn(fn) {
    if (busy) return;
    busy = true;
    try { await fn(); }
    catch (e) { await speak('Sorry, something went wrong with voice.'); }
    finally { busy = false; setStatus('idle', backend === 'stub' ? 'hold to talk · demo voice' : 'hold to talk'); }
  }

  // Guarded against the start/stop race: if the button is released while
  // startRecording() is still awaiting (e.g. during the mic permission prompt),
  // the resolved recorder is torn down immediately instead of leaving a hot mic.
  async function pushStart() {
    if (busy || recorder || holding) return;
    holding = true;
    var rec;
    try { rec = await startRecording(); }
    catch (e) { holding = false; setStatus('idle', 'mic blocked'); return; }
    if (!holding) { try { await rec.stop(); } catch (_) {} return; } // released during startup
    recorder = rec;
    setStatus('listening', 'listening…');
  }
  async function pushStop() {
    if (!holding) return;
    holding = false;
    if (!recorder) return; // start still in flight; pushStart tears it down
    var rec = recorder; recorder = null;
    await runTurn(async function () {
      var wav = await rec.stop();
      await handle(await transcribe(wav));
    });
  }

  // ---------------------------------------------------------------- ui

  var els = {};
  function setStatus(state, text) {
    if (!els.pill) return;
    var color = { idle: '#8aa0b8', listening: '#5ccfe6', thinking: '#e6b23e', speaking: '#7ee0a6' }[state] || '#8aa0b8';
    els.dot.style.background = color;
    els.dot.style.boxShadow = state === 'idle' ? 'none' : '0 0 10px ' + color;
    els.btn.style.borderColor = state === 'listening' ? '#5ccfe6' : 'rgba(255,255,255,.14)';
    els.btn.classList.toggle('hv-pulse', state === 'listening');
    if (text) els.label.textContent = text;
  }
  function setTranscript(t) { if (els.label) els.label.textContent = t; }

  function mount() {
    var css = document.createElement('style');
    css.textContent =
      '.hv-wrap{position:fixed;right:20px;bottom:20px;z-index:2147483000;display:flex;align-items:center;gap:10px;font-family:"IBM Plex Sans",system-ui,sans-serif}' +
      '.hv-pill{max-width:340px;padding:8px 13px;border-radius:11px;background:rgba(10,13,20,.86);border:1px solid rgba(255,255,255,.10);color:#dfe7f2;font:500 12px/1.35 "IBM Plex Sans",sans-serif;backdrop-filter:blur(8px);display:flex;align-items:center;gap:8px;overflow:hidden}' +
      '.hv-pill .hv-txt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.hv-dot{width:7px;height:7px;border-radius:50%;flex:none;background:#8aa0b8}' +
      '.hv-btn{width:52px;height:52px;flex:none;border-radius:50%;border:1px solid rgba(255,255,255,.14);background:radial-gradient(120% 120% at 30% 25%,rgba(92,207,230,.20),rgba(10,13,20,.9));color:#eaf6ff;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 6px 22px rgba(0,0,0,.45);transition:border-color .15s,transform .1s}' +
      '.hv-btn:active{transform:scale(.94)}' +
      '.hv-btn.hv-pulse{animation:hvpulse 1.1s ease-in-out infinite}' +
      '@keyframes hvpulse{0%,100%{box-shadow:0 6px 22px rgba(0,0,0,.45),0 0 0 0 rgba(92,207,230,.4)}50%{box-shadow:0 6px 22px rgba(0,0,0,.45),0 0 0 9px rgba(92,207,230,0)}}';
    document.head.appendChild(css);

    var wrap = document.createElement('div'); wrap.className = 'hv-wrap';
    var pill = document.createElement('div'); pill.className = 'hv-pill';
    var dot = document.createElement('span'); dot.className = 'hv-dot';
    var label = document.createElement('span'); label.className = 'hv-txt'; label.textContent = 'hold to talk';
    pill.appendChild(dot); pill.appendChild(label);
    var btn = document.createElement('button'); btn.className = 'hv-btn'; btn.title = 'Hold to talk (or hold Space)';
    btn.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';
    wrap.appendChild(pill); wrap.appendChild(btn);
    document.body.appendChild(wrap);
    els = { wrap: wrap, pill: pill, dot: dot, label: label, btn: btn };

    // push-to-talk: mouse, touch, and hold-Space
    btn.addEventListener('mousedown', function (e) { e.preventDefault(); pushStart(); });
    document.addEventListener('mouseup', function () { pushStop(); });
    btn.addEventListener('touchstart', function (e) { e.preventDefault(); pushStart(); }, { passive: false });
    btn.addEventListener('touchend', function (e) { e.preventDefault(); pushStop(); });
    var spaceDown = false;
    document.addEventListener('keydown', function (e) {
      if (e.code === 'Space' && !spaceDown && !isTyping(e.target)) { spaceDown = true; e.preventDefault(); pushStart(); }
    });
    document.addEventListener('keyup', function (e) {
      if (e.code === 'Space' && spaceDown) { spaceDown = false; pushStop(); }
    });

    getJSON('/system').then(function (s) {
      if (s && s.voice) backend = s.voice.backend;
      setStatus('idle', backend === 'stub' ? 'hold to talk · demo voice' : 'hold to talk');
    });
  }

  function isTyping(el) {
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function boot() {
    if (!supported()) return; // no mic/AudioContext — no widget
    if (document.body) mount();
    else document.addEventListener('DOMContentLoaded', mount);
  }
  boot();
})();
