/* HIVE Coordinator Orb — a large, dense CONNECTED CONSTELLATION rendered on a
 * <canvas>. Hundreds of cool white/silver particles distributed through a
 * rotating sphere, linked by a glowing web of lines into a cloud-like cluster.
 * The orb is the centerpiece; it fills its canvas.
 *
 * Delegation is shown with COMETS: when the coordinator hands work to an agent,
 * a comet streaks out from the core along that agent's fixed direction and lands
 * with a small caption chip ("given to scout") that lingers, then fades. Ambient
 * comets drift now and then so the field always feels alive.
 *
 *   var orb = HiveOrb.create(canvasEl, { count, treatment, reducedMotion });
 *   orb.delegate('scout');          // fire a labelled delegation comet
 *   orb.pulse();                    // dispatch burst (brightness + speed)
 *   orb.setMode('idle'|'working'|'killed');
 *   orb.setTreatment('galaxy'|'nebula'|'core');   // constellation / nebula / core
 *   orb.setCount(n); orb.destroy();
 */
(function () {
  'use strict';

  function hash(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; }
  function ease(t) { return t * t * (3 - 2 * t); }
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }

  var COMET_HUE = 189; // cool cyan signal (comets stay this hue regardless of orb mood)

  // slow auto-cycling mood color: hold each ~9s, then crossfade ~3s to the next.
  // each stop carries its own saturation so "silver" can sit in the loop as a near-neutral.
  var CYCLE = [
    { h: 215, s: 14 },   // silver (near-neutral)
    { h: 266, s: 60 },   // violet
    { h: 32,  s: 66 },   // orange
    { h: 205, s: 56 }    // azure
  ];
  var HOLD = 9, FADE = 3;
  function lerpHue(a, b, t) { var d = ((b - a + 540) % 360) - 180; return (a + d * t + 360) % 360; }
  function smoothstep(t) { return t * t * (3 - 2 * t); }
  function cycleColor(e) {
    var seg = HOLD + FADE, cyc = CYCLE.length * seg, p = ((e % cyc) + cyc) % cyc;
    var k = Math.floor(p / seg), local = p - k * seg;
    var a = CYCLE[k], b = CYCLE[(k + 1) % CYCLE.length];
    if (local < HOLD) return { h: a.h, s: a.s };
    var t = smoothstep((local - HOLD) / FADE);
    return { h: lerpHue(a.h, b.h, t), s: a.s + (b.s - a.s) * t };
  }

  function create(canvas, opts) {
    opts = opts || {};
    var ctx = canvas.getContext('2d');
    var count = opts.count || 750;
    var treatment = opts.treatment || 'galaxy';
    var reduced = !!opts.reducedMotion;
    var mode = 'idle';

    var W = 0, H = 0, DPR = 1, cx = 0, cy = 0, R = 0;   // R = orb radius
    var particles = [], comets = [];
    var raf = null, running = false, last = performance.now(), elapsed = 0;
    var rot = 0.6, killMix = 0, workMix = 0, pulseV = 0, ambientT = 0;

    function makeParticle() {
      var u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      var dir = [s * Math.cos(th), s * Math.sin(th), u];
      var rr;
      if (treatment === 'core') rr = Math.pow(Math.random(), 1.7);
      else if (treatment === 'nebula') rr = 0.30 + 0.70 * Math.cbrt(Math.random());
      else rr = 0.15 + 0.85 * Math.cbrt(Math.random());     // constellation: fill the volume
      var hubProb = treatment === 'nebula' ? 0.30 : treatment === 'core' ? 0.52 : 0.50;
      return {
        x: dir[0] * rr, y: dir[1] * rr, z: dir[2] * rr, r: rr,
        size: 0.6 + Math.random() * 1.5,
        tw: Math.random() * 6.283, tws: 0.5 + Math.random() * 1.7,
        base: 0.42 + Math.random() * 0.58,
        hub: Math.random() < hubProb
      };
    }
    function build() { particles = []; for (var i = 0; i < count; i++) particles.push(makeParticle()); }

    function resize() {
      var rect = canvas.getBoundingClientRect();
      DPR = Math.min(2, window.devicePixelRatio || 1);
      W = Math.max(1, Math.round(rect.width)); H = Math.max(1, Math.round(rect.height));
      canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      cx = W / 2; cy = H / 2;
      R = Math.min(W, H) * 0.54;   // big, dominant orb
    }

    function roundRect(x, y, w, h, r) {
      ctx.beginPath(); ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
    }

    /* ---------------- comets ---------------- */
    function spawnComet(label, ambient) {
      var ang = label ? (((hash(label) % 360) + 360) % 360) * Math.PI / 180 : Math.random() * Math.PI * 2;
      comets.push({
        ang: ang, t: 0, phase: 'fly', hold: 0, alpha: 1, ambient: !!ambient,
        speed: ambient ? (1.0 + Math.random() * 0.5) : (0.85 + Math.random() * 0.25),
        label: label || '', r0: R * 0.12, r1: R * (ambient ? 0.96 : 1.05)
      });
      if (comets.length > 16) comets.shift();
    }

    function updateDrawComets(dt) {
      // ambient life
      ambientT += dt;
      if (!reduced && killMix < 0.2 && ambientT > 1.5) { ambientT = 0; if (Math.random() < 0.65) spawnComet(null, true); }
      if (killMix > 0.5) { comets.length = 0; return; }

      for (var i = comets.length - 1; i >= 0; i--) {
        var c = comets[i];
        if (c.phase === 'fly') { c.t += dt / c.speed; if (c.t >= 1) { c.t = 1; c.phase = 'hold'; } }
        else if (c.phase === 'hold') { c.hold += dt; if (c.hold > (c.ambient ? 0.25 : 2.4)) c.phase = 'fade'; }
        else { c.alpha -= dt / (c.ambient ? 0.4 : 0.9); if (c.alpha <= 0) { comets.splice(i, 1); continue; } }

        var e = easeOut(c.t), rr = c.r0 + (c.r1 - c.r0) * e;
        var ox = Math.cos(c.ang), oy = Math.sin(c.ang);
        var hx = cx + ox * rr, hy = cy + oy * rr;
        var al = c.alpha * (c.ambient ? 0.5 : 1);

        // tail
        ctx.globalCompositeOperation = 'lighter';
        var tail = c.ambient ? R * 0.16 : R * 0.26;
        var tx = cx + ox * (rr - tail), ty = cy + oy * (rr - tail);
        var g = ctx.createLinearGradient(tx, ty, hx, hy);
        g.addColorStop(0, 'hsla(' + COMET_HUE + ',92%,72%,0)');
        g.addColorStop(1, 'hsla(' + COMET_HUE + ',95%,84%,' + (0.55 * al * (c.phase === 'fly' ? 1 : 0.25)) + ')');
        ctx.strokeStyle = g; ctx.lineWidth = c.ambient ? 1.4 : 2.6; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(hx, hy); ctx.stroke();
        // head
        if (c.phase === 'fly') {
          ctx.fillStyle = 'hsla(' + COMET_HUE + ',95%,75%,' + (al * 0.45) + ')';
          ctx.beginPath(); ctx.arc(hx, hy, c.ambient ? 5 : 9, 0, 6.283); ctx.fill();
          ctx.fillStyle = 'hsla(' + COMET_HUE + ',95%,94%,' + al + ')';
          ctx.beginPath(); ctx.arc(hx, hy, c.ambient ? 1.8 : 3, 0, 6.283); ctx.fill();
        }
        ctx.globalCompositeOperation = 'source-over';

        // landing marker + caption chip (labelled comets only)
        if (!c.ambient && (c.phase === 'hold' || c.phase === 'fade')) {
          var lx = cx + ox * c.r1, ly = cy + oy * c.r1;
          ctx.strokeStyle = 'hsla(' + COMET_HUE + ',90%,82%,' + (al * 0.9) + ')';
          ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(lx, ly, 4, 0, 6.283); ctx.stroke();
          drawLabel(lx, ly, ox, oy, 'given to ' + c.label, al);
        }
      }
    }

    function drawLabel(lx, ly, ox, oy, text, al) {
      ctx.font = "600 12.5px 'IBM Plex Mono', ui-monospace, monospace";
      var tw = ctx.measureText(text).width, padX = 10, chipH = 25, chipW = tw + padX * 2;
      var bx = lx + ox * 12, by = ly + oy * 12;
      var rectX = ox >= 0 ? bx : bx - chipW, rectY = by - chipH / 2;
      rectX = Math.max(6, Math.min(W - chipW - 6, rectX));
      rectY = Math.max(6, Math.min(H - chipH - 6, rectY));
      ctx.globalAlpha = Math.max(0, al);
      ctx.strokeStyle = 'hsla(' + COMET_HUE + ',75%,78%,' + (al * 0.45) + ')';
      ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(rectX + (ox >= 0 ? 0 : chipW), by); ctx.stroke();
      roundRect(rectX, rectY, chipW, chipH, 7);
      ctx.fillStyle = 'rgba(8,11,15,0.88)'; ctx.fill();
      ctx.strokeStyle = 'hsla(' + COMET_HUE + ',70%,72%,0.42)'; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = '#dff4fb'; ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
      ctx.fillText(text, rectX + padX, rectY + chipH / 2 + 0.5);
      ctx.globalAlpha = 1;
    }

    /* ---------------- main draw ---------------- */
    function draw(dt, rdt) {
      rdt = (rdt == null ? dt : rdt);
      killMix += ((mode === 'killed' ? 1 : 0) - killMix) * Math.min(1, rdt * 3.2);
      workMix += ((mode === 'working' ? 1 : 0) - workMix) * Math.min(1, rdt * 3);
      pulseV *= Math.exp(-rdt * 3);

      var mood = cycleColor(elapsed);                    // auto-cycles: silver → violet → orange → azure
      var hue = mood.h * (1 - killMix) + 2 * killMix;    // drift toward red when killed
      var sat = mood.s * (1 - killMix) + 74 * killMix;   // per-stop tint; deep red when killed
      var rotSpeed = (0.13 + workMix * 0.14 + pulseV * 0.45) * (1 - killMix * 0.97);
      rot += rotSpeed * dt;
      var breathe = 1 + Math.sin(elapsed * 0.8) * 0.025 * (1 - killMix) + pulseV * 0.09;
      var bright = (0.92 + workMix * 0.32 + pulseV * 0.7) * (1 - killMix * 0.62);
      var scale = R * breathe;

      ctx.clearRect(0, 0, W, H);

      // soft volumetric cloud glow
      var gg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.15);
      gg.addColorStop(0, 'hsla(' + hue + ',' + (sat + 24) + '%,62%,' + (0.11 * bright) + ')');
      gg.addColorStop(0.6, 'hsla(' + hue + ',' + (sat + 24) + '%,55%,' + (0.04 * bright) + ')');
      gg.addColorStop(1, 'hsla(' + hue + ',40%,55%,0)');
      ctx.fillStyle = gg; ctx.beginPath(); ctx.arc(cx, cy, R * 1.15, 0, 6.283); ctx.fill();

      // project points
      var cY = Math.cos(rot), sY = Math.sin(rot), ax = rot * 0.32, cX = Math.cos(ax), sX = Math.sin(ax), i, p;
      for (i = 0; i < particles.length; i++) {
        p = particles[i];
        var x = p.x * cY + p.z * sY, z = -p.x * sY + p.z * cY, y = p.y;
        var y2 = y * cX - z * sX, z2 = y * sX + z * cX;
        var persp = 1 / (1.9 - z2 * 0.9);
        p._sx = cx + x * scale * persp; p._sy = cy + y2 * scale * persp; p._d = (z2 + 1) / 2; p._p = persp;
      }
      particles.sort(function (a, b) { return a._d - b._d; });

      // dense connection web
      var hubs = []; for (i = 0; i < particles.length; i++) if (particles[i].hub) hubs.push(particles[i]);
      var thresh = R * 0.40, cap = treatment === 'nebula' ? 300 : treatment === 'core' ? 460 : 720, drawn = 0;
      ctx.globalCompositeOperation = 'lighter'; ctx.lineWidth = 0.65;
      for (i = 0; i < hubs.length && drawn < cap; i++) {
        var a = hubs[i];
        for (var j = i + 1; j < hubs.length && drawn < cap; j++) {
          var b = hubs[j], dx = a._sx - b._sx, dy = a._sy - b._sy, dd = dx * dx + dy * dy;
          if (dd < thresh * thresh) {
            var d = Math.sqrt(dd), la = (1 - d / thresh) * 0.19 * bright * ((a._d + b._d) / 2 + 0.25);
            ctx.strokeStyle = 'hsla(' + hue + ',' + (sat + 34) + '%,76%,' + la + ')';
            ctx.beginPath(); ctx.moveTo(a._sx, a._sy); ctx.lineTo(b._sx, b._sy); ctx.stroke();
            drawn++;
          }
        }
      }

      // particles
      for (i = 0; i < particles.length; i++) {
        p = particles[i];
        var dep = p._d, tw = 0.7 + 0.3 * Math.sin(elapsed * p.tws + p.tw);
        var al = p.base * (0.25 + 0.75 * dep) * bright * tw;
        var light = 74 + dep * 24;
        var sz = p.size * p._p * (R / 230) * (treatment === 'nebula' ? 1.85 : treatment === 'core' ? 1.0 : 1.2);
        ctx.fillStyle = 'hsla(' + hue + ',' + sat + '%,' + light + '%,' + Math.min(1, al) + ')';
        ctx.beginPath(); ctx.arc(p._sx, p._sy, Math.max(0.4, sz), 0, 6.283); ctx.fill();
        if (dep > 0.72 || treatment === 'nebula') {
          ctx.fillStyle = 'hsla(' + hue + ',' + (sat + 40) + '%,' + (light + 6) + '%,' + (al * 0.16) + ')';
          ctx.beginPath(); ctx.arc(p._sx, p._sy, sz * (treatment === 'nebula' ? 3.1 : 2.3), 0, 6.283); ctx.fill();
        }
      }
      ctx.globalCompositeOperation = 'source-over';

      updateDrawComets(dt);
    }

    function frame(now) {
      if (!running) return;
      var raw = (now - last) / 1000, dt = Math.min(0.05, raw); last = now;
      if (killMix < 0.999 || mode !== 'killed') elapsed += dt * (1 - killMix * 0.98);
      draw(dt, raw);
      raf = requestAnimationFrame(frame);
    }
    function start() { if (running || reduced) return; running = true; last = performance.now(); raf = requestAnimationFrame(frame); }
    function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = null; }
    function onVis() { if (document.hidden) stop(); else start(); }

    var ro = new ResizeObserver(function () { resize(); if (reduced) draw(0); });
    resize(); build();
    if (reduced) { rot = 0.7; draw(0); } else { draw(0.016); start(); }
    document.addEventListener('visibilitychange', onVis);
    ro.observe(canvas);

    return {
      setTreatment: function (t) { if (t === treatment) return; treatment = t; build(); if (reduced) draw(0); },
      setMode: function (m) { mode = m; if (reduced) draw(0.016, 1); },
      setCount: function (n) { count = n; build(); if (reduced) draw(0); },
      pulse: function () { pulseV = 1; if (reduced) draw(0.016, 1); },
      delegate: function (label) { if (label == null) return; spawnComet(String(label), false); pulseV = Math.min(1, pulseV + 0.45); if (reduced) draw(0.016, 1); },
      destroy: function () { stop(); document.removeEventListener('visibilitychange', onVis); ro.disconnect(); }
    };
  }

  window.HiveOrb = { create: create };
})();
