// El orbe de NOVA: una red holográfica de nodos y conexiones con anillos
// orbitales, que flota libre por el escritorio y se aparta del cursor.
//
// Estados: reposo (azul tenue), activa (azul brillante), procesando (dorado),
// buscando (verde), escuchando, hablando (púrpura, late con la voz).
// Todo se dibuja con mezcla aditiva y alfa premultiplicado, así el brillo se
// ve bien sobre un fondo transparente sin pasar por post-procesado (menos VRAM).

import * as THREE from './vendor/three.module.min.js';

export const COLORES = {
  general: '#3b82f6', tecnologia: '#22d3ee', creatividad: '#a855f7', analisis: '#f59e0b',
  investigacion: '#22c55e', prioridad: '#ef4444', apoyo: '#ec4899', neutral: '#e5e7eb',
};

const ESTADOS = {
  reposo:     { brillo: 0.72, rot: 0.10, pulso: 0.25, conex: 0.55, escala: 1.0, exp: 0.00, orbitas: 0.35 },
  activa:     { brillo: 1.00, rot: 0.30, pulso: 0.60, conex: 0.85, escala: 1.7, exp: 0.04, orbitas: 0.8 },
  escuchando: { brillo: 0.95, rot: 0.22, pulso: 0.50, conex: 0.80, escala: 1.7, exp: 0.03, orbitas: 0.7 },
  procesando: { brillo: 0.95, rot: 0.95, pulso: 0.90, conex: 1.00, escala: 1.7, exp: 0.14, orbitas: 1.8, color: '#f5a524' },
  buscando:   { brillo: 0.95, rot: 0.80, pulso: 0.90, conex: 1.00, escala: 1.7, exp: 0.10, orbitas: 1.5, color: '#22c55e' },
  hablando:   { brillo: 1.00, rot: 0.40, pulso: 1.00, conex: 0.95, escala: 1.7, exp: 0.05, orbitas: 1.0, color: '#a855f7' },
};

const RADIO_REPOSO_PX = 76;
const SIN_MOVIMIENTO = matchMedia('(prefers-reduced-motion: reduce)').matches;

const MEZCLA = {
  transparent: true,
  depthWrite: false,
  depthTest: false,
  blending: THREE.CustomBlending,
  blendEquation: THREE.AddEquation,
  blendSrc: THREE.OneFactor,
  blendDst: THREE.OneFactor,
  blendSrcAlpha: THREE.OneFactor,
  blendDstAlpha: THREE.OneFactor,
};

const VERT_PUNTOS = /* glsl */`
  uniform float uTiempo, uBrillo, uPixel, uPulso, uAudio, uExp;
  attribute float fase;
  attribute float tam;
  varying float vAlfa;
  varying float vBlanco;
  void main() {
    float respira = 1.0 + uExp * sin(uTiempo * 2.2 + fase * 6.2831);
    vec3 p = position * respira;
    vec3 n = normalize(mat3(modelMatrix) * normalize(p + 1e-5));
    float frente = 0.5 + 0.5 * n.z;
    float titila = 0.55 + 0.45 * sin(uTiempo * (1.2 + fase * 2.5) + fase * 40.0);
    float chispa = smoothstep(0.92, 1.0, sin(uTiempo * 0.9 + fase * 31.0)) * uPulso;
    vAlfa = uBrillo * titila * (0.35 + 0.65 * frente) * (1.0 + uAudio * 1.2) + chispa;
    vBlanco = chispa;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = tam * uPixel * (0.8 + 0.4 * frente) * (1.0 + 0.6 * uAudio + 0.5 * chispa);
    gl_Position = projectionMatrix * mv;
  }`;

const FRAG_PUNTOS = /* glsl */`
  uniform vec3 uColor;
  varying float vAlfa;
  varying float vBlanco;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float halo = pow(smoothstep(0.5, 0.0, d), 2.2);
    float centro = smoothstep(0.14, 0.0, d);
    vec3 c = mix(uColor, vec3(1.0), clamp(centro * 0.85 + vBlanco * 0.5, 0.0, 1.0));
    float a = clamp(halo * vAlfa, 0.0, 1.0);
    gl_FragColor = vec4(c * a, a);
  }`;

const VERT_LINEAS = /* glsl */`
  uniform float uExp, uTiempo;
  attribute float fase;
  attribute float t;
  varying float vFase;
  varying float vT;
  varying float vFrente;
  void main() {
    vFase = fase;
    vT = t;
    vec3 p = position * (1.0 + uExp * sin(uTiempo * 2.2 + fase * 6.2831));
    vec3 n = normalize(mat3(modelMatrix) * normalize(p + 1e-5));
    vFrente = 0.5 + 0.5 * n.z;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }`;

const FRAG_LINEAS = /* glsl */`
  uniform vec3 uColor;
  uniform float uTiempo, uBrillo, uConex, uPulso, uVelPulso, uAudio;
  varying float vFase;
  varying float vT;
  varying float vFrente;
  void main() {
    float p = fract(uTiempo * uVelPulso + vFase);
    float impulso = smoothstep(0.14, 0.0, abs(vT - p)) * uPulso;
    float a = (uConex * 0.30 + impulso * 0.9 + uAudio * 0.25) * uBrillo * (0.25 + 0.75 * vFrente);
    a = clamp(a, 0.0, 1.0);
    vec3 c = mix(uColor, vec3(1.0), impulso * 0.45);
    gl_FragColor = vec4(c * a, a);
  }`;

function fibonacci(n, radioMin, radioMax, azar) {
  const pts = [];
  const oro = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = oro * i + (azar() - 0.5) * 0.35;
    const rad = radioMin + azar() * (radioMax - radioMin);
    pts.push(new THREE.Vector3(Math.cos(th) * r * rad, y * rad, Math.sin(th) * r * rad));
  }
  return pts;
}

function semilla(s) {
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function lineasConFase(segmentos, uniformes) {
  const pos = [], fase = [], t = [];
  for (const seg of segmentos) {
    const f = seg.fase;
    for (let i = 0; i < seg.puntos.length - 1; i++) {
      const a = seg.puntos[i], b = seg.puntos[i + 1];
      pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
      fase.push(f, f);
      const n = seg.puntos.length - 1;
      t.push(i / n, (i + 1) / n);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('fase', new THREE.Float32BufferAttribute(fase, 1));
  g.setAttribute('t', new THREE.Float32BufferAttribute(t, 1));
  const m = new THREE.ShaderMaterial({ uniforms: uniformes, vertexShader: VERT_LINEAS, fragmentShader: FRAG_LINEAS, ...MEZCLA });
  return new THREE.LineSegments(g, m);
}

export class Orbe {
  constructor(canvas, { calidad = 'alta', libre = true } = {}) {
    this.canvas = canvas;
    this.calidad = calidad;
    this.libre = libre;
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, premultipliedAlpha: true, powerPreference: 'low-power' });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, calidad === 'alta' ? 2 : 1));
    this.escena = new THREE.Scene();
    this.camara = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    this.camara.position.z = 10;

    this.nombreEstado = 'reposo';
    this.p = { ...ESTADOS.reposo };
    this.colorBase = new THREE.Color(COLORES.general);
    this.color = this.colorBase.clone();
    this.colorObjetivo = this.colorBase.clone();
    this.nivelAudio = 0;
    this.audioSuave = 0;
    this.cursor = { x: -1e4, y: -1e4 };
    this.rapidezCursor = 0;  // px/s, suavizada
    this.cursorT = 0;
    this.anclado = false;
    this.ancla = null;
    this.zona = null;  // {x, y, w, h}: espacio por el que flota (si no, toda la ventana)
    this.escalaZona = 1;

    const u = (v) => ({ value: v });
    this.uniformes = {
      uTiempo: u(0), uBrillo: u(0.5), uPixel: u(6), uPulso: u(0.2), uAudio: u(0), uExp: u(0),
      uColor: u(this.color), uConex: u(0.35), uVelPulso: u(0.35),
    };
    this.uniformesOrbitas = { ...this.uniformes, uConex: u(0.25), uVelPulso: u(0.12) };

    this.orbe = new THREE.Group();
    this.escena.add(this.orbe);
    this._construir();
    this._construirEstela();

    // Movimiento en píxeles de pantalla.
    this.pos = { x: innerWidth * 0.72, y: innerHeight * 0.38 };
    this.vel = { x: 0, y: 0 };
    this.fases = Array.from({ length: 6 }, () => Math.random() * 1000);
    this.radioPx = RADIO_REPOSO_PX;

    this._redimensionar();
    addEventListener('resize', () => this._redimensionar());
    this.reloj = new THREE.Clock();
    this.ultimoCuadro = 0;
    this._cuadro = this._cuadro.bind(this);
    requestAnimationFrame(this._cuadro);
  }

  _construir() {
    const azar = semilla(7);
    const nodos = fibonacci(this.calidad === 'alta' ? 170 : 100, 0.94, 1.04, azar);
    const internos = Array.from({ length: this.calidad === 'alta' ? 45 : 25 }, () => {
      const v = new THREE.Vector3(azar() - 0.5, azar() - 0.5, azar() - 0.5).normalize();
      return v.multiplyScalar(0.15 + azar() * 0.7);
    });
    const todos = [...nodos, ...internos];

    // Nodos
    const g = new THREE.BufferGeometry().setFromPoints(todos);
    g.setAttribute('fase', new THREE.Float32BufferAttribute(todos.map(() => azar()), 1));
    g.setAttribute('tam', new THREE.Float32BufferAttribute(todos.map((_, i) => (i < nodos.length ? 0.6 + azar() * 1.1 : 0.4 + azar() * 0.6)), 1));
    const mp = new THREE.ShaderMaterial({ uniforms: this.uniformes, vertexShader: VERT_PUNTOS, fragmentShader: FRAG_PUNTOS, ...MEZCLA });
    this.orbe.add(new THREE.Points(g, mp));

    // Conexiones entre nodos vecinos de la superficie
    const segmentos = [];
    const vistos = new Set();
    nodos.forEach((a, i) => {
      const cerca = nodos.map((b, j) => [a.distanceToSquared(b), j]).sort((x, y) => x[0] - y[0]).slice(1, 4);
      for (const [, j] of cerca) {
        const clave = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (vistos.has(clave)) continue;
        vistos.add(clave);
        segmentos.push({ puntos: [a, nodos[j]], fase: azar() });
      }
    });
    // Algunas conexiones hacia dentro (la «red neuronal»)
    internos.forEach((a) => {
      const b = nodos[Math.floor(azar() * nodos.length)];
      segmentos.push({ puntos: [a, b], fase: azar() });
    });
    // Círculos máximos: la retícula del globo
    for (let k = 0; k < 7; k++) {
      const eje = new THREE.Vector3(azar() - 0.5, azar() - 0.5, azar() - 0.5).normalize();
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), eje);
      const puntos = [];
      for (let i = 0; i <= 96; i++) {
        const th = (i / 96) * Math.PI * 2;
        puntos.push(new THREE.Vector3(Math.cos(th), Math.sin(th), 0).applyQuaternion(q));
      }
      segmentos.push({ puntos, fase: azar() });
    }
    this.orbe.add(lineasConFase(segmentos, this.uniformes));

    // Anillos orbitales con satélites
    this.anillos = [];
    for (let k = 0; k < 3; k++) {
      const grupo = new THREE.Group();
      const rx = 1.28 + k * 0.14, ry = rx * (0.62 + azar() * 0.25);
      const puntos = [];
      for (let i = 0; i <= 128; i++) {
        const th = (i / 128) * Math.PI * 2;
        puntos.push(new THREE.Vector3(Math.cos(th) * rx, Math.sin(th) * ry, 0));
      }
      grupo.add(lineasConFase([{ puntos, fase: azar() }], this.uniformesOrbitas));
      const sat = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      sat.setAttribute('fase', new THREE.Float32BufferAttribute([azar(), azar()], 1));
      sat.setAttribute('tam', new THREE.Float32BufferAttribute([1.5, 1.0], 1));
      const satelites = new THREE.Points(sat, new THREE.ShaderMaterial({ uniforms: this.uniformes, vertexShader: VERT_PUNTOS, fragmentShader: FRAG_PUNTOS, ...MEZCLA }));
      grupo.add(satelites);
      grupo.rotation.set(azar() * Math.PI, azar() * Math.PI, azar() * Math.PI);
      this.orbe.add(grupo);
      this.anillos.push({ grupo, satelites, rx, ry, vel: (0.25 + azar() * 0.4) * (k % 2 ? -1 : 1), giro: new THREE.Vector3(azar() - 0.5, azar() - 0.5, azar() - 0.5).normalize() });
    }

    // Núcleo brillante y halo
    const nucleo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    nucleo.setAttribute('fase', new THREE.Float32BufferAttribute([0.1, 0.6], 1));
    nucleo.setAttribute('tam', new THREE.Float32BufferAttribute([9, 26], 1));
    this.uniformesNucleo = { ...this.uniformes, uBrillo: { value: 0.5 } };
    this.orbe.add(new THREE.Points(nucleo, new THREE.ShaderMaterial({ uniforms: this.uniformesNucleo, vertexShader: VERT_PUNTOS, fragmentShader: FRAG_PUNTOS, ...MEZCLA })));
  }

  _construirEstela() {
    const n = 48;
    this.estela = Array.from({ length: n }, () => new THREE.Vector3());
    const g = new THREE.BufferGeometry().setFromPoints(this.estela);
    g.setAttribute('fase', new THREE.Float32BufferAttribute(Array.from({ length: n }, () => Math.random()), 1));
    g.setAttribute('tam', new THREE.Float32BufferAttribute(Array.from({ length: n }, (_, i) => 1.2 * (1 - i / n)), 1));
    this.uniformesEstela = { ...this.uniformes, uBrillo: { value: 0.3 }, uExp: { value: 0 } };
    this.puntosEstela = new THREE.Points(g, new THREE.ShaderMaterial({ uniforms: this.uniformesEstela, vertexShader: VERT_PUNTOS, fragmentShader: FRAG_PUNTOS, ...MEZCLA }));
    this.escena.add(this.puntosEstela);
  }

  _redimensionar() {
    const w = innerWidth, h = innerHeight;
    this.renderer.setSize(w, h, false);
    this.camara.aspect = w / h;
    this.camara.updateProjectionMatrix();
    const altoVisible = 2 * this.camara.position.z * Math.tan(THREE.MathUtils.degToRad(this.camara.fov / 2));
    this.mundoPorPx = altoVisible / h;
  }

  // ------------------------------------------------------------- API

  estado(nombre, contexto) {
    if (!ESTADOS[nombre]) nombre = 'reposo';
    // Libre en el escritorio: mientras piensa o habla se queda donde está (antes se iba al centro).
    if (nombre !== 'reposo' && this.nombreEstado === 'reposo') this.quieto = { x: this.pos.x, y: this.pos.y };
    if (nombre === 'reposo') this.quieto = null;
    this.nombreEstado = nombre;
    if (contexto && COLORES[contexto]) this.colorBase.set(COLORES[contexto]);
    if (nombre === 'reposo') this.colorBase.set(COLORES.general);
    const especial = ESTADOS[nombre].color;
    this.colorObjetivo.set(especial && !(nombre === 'procesando' && contexto && contexto !== 'general' && contexto !== 'analisis') ? especial : this.colorBase);
  }

  colorContexto(contexto) {
    if (COLORES[contexto]) {
      this.colorBase.set(COLORES[contexto]);
      if (!ESTADOS[this.nombreEstado].color) this.colorObjetivo.copy(this.colorBase);
    }
  }

  audio(nivel) { this.nivelAudio = Math.max(0, Math.min(1, nivel)); }
  moverCursor(x, y) {
    const ahora = performance.now();
    const dt = Math.max(1, ahora - this.cursorT) / 1000;
    const v = Math.hypot(x - this.cursor.x, y - this.cursor.y) / dt;
    this.rapidezCursor = this.cursorT ? this.rapidezCursor * 0.7 + Math.min(v, 5000) * 0.3 : 0;
    this.cursorT = ahora;
    this.cursor.x = x; this.cursor.y = y;
  }
  anclar(activo, punto = null) { this.anclado = activo; this.ancla = punto; }
  // Limita el vuelo a una zona (por ejemplo, junto a la conversación) y ajusta el tamaño.
  limitar(zona = null, escala = 1) { this.zona = zona; this.escalaZona = escala; }
  posicion() { return { x: this.pos.x, y: this.pos.y, r: this.radioPx }; }

  // ------------------------------------------------------------- bucle

  _objetivoLibre(t) {
    const f = this.fases;
    const z = this.zona || { x: 0, y: 0, w: innerWidth, h: innerHeight };
    // Suma de senos con frecuencias inconmensurables: trayectoria suave que no se repite.
    const nx = (Math.sin(t * 0.071 + f[0]) + Math.sin(t * 0.043 + f[1]) * 0.6 + Math.sin(t * 0.017 + f[2]) * 0.4) / 2;
    const ny = (Math.sin(t * 0.059 + f[3]) + Math.sin(t * 0.037 + f[4]) * 0.6 + Math.sin(t * 0.013 + f[5]) * 0.4) / 2;
    const mx = Math.min(this.radioPx * 1.6 + 20, z.w / 2), my = Math.min(this.radioPx * 1.6 + 20, z.h / 2);
    return { x: z.x + z.w / 2 + nx * (z.w / 2 - mx), y: z.y + z.h / 2 + ny * (z.h / 2 - my) };
  }

  _mover(t, dt) {
    const anclaPorEstado = this.nombreEstado !== 'reposo';
    let obj;
    if (this.anclado || anclaPorEstado || !this.libre) {
      obj = this.ancla || (this.zona ? { x: this.zona.x + this.zona.w / 2, y: this.zona.y + this.zona.h / 2 }
        : this.libre && this.quieto ? this.quieto : { x: innerWidth / 2, y: innerHeight * 0.4 });
    } else {
      obj = this._objetivoLibre(SIN_MOVIMIENTO ? t * 0.1 : t);
    }
    const k = anclaPorEstado || this.anclado ? 3.0 : 0.6;
    let ax = (obj.x - this.pos.x) * k - this.vel.x * 2.2;
    let ay = (obj.y - this.pos.y) * k - this.vel.y * 2.2;
    // Se aparta del cursor cuando pasa rápido (solo en reposo, para no huir cuando le hablas).
    // Si te acercas despacio, se queda quieta para que le puedas dar clic.
    if (!anclaPorEstado && !this.anclado && this.rapidezCursor > 450) {
      const dx = this.pos.x - this.cursor.x, dy = this.pos.y - this.cursor.y;
      const d = Math.hypot(dx, dy), radio = this.radioPx + 140;
      if (d < radio && d > 0.1) {
        const fuerza = ((radio - d) / radio) ** 2 * 2600;
        ax += (dx / d) * fuerza;
        ay += (dy / d) * fuerza;
      }
    }
    this.vel.x += ax * dt;
    this.vel.y += ay * dt;
    const vmax = anclaPorEstado ? 1400 : 420;
    const v = Math.hypot(this.vel.x, this.vel.y);
    if (v > vmax) { this.vel.x *= vmax / v; this.vel.y *= vmax / v; }
    this.pos.x += this.vel.x * dt;
    this.pos.y += this.vel.y * dt;
    const m = this.radioPx;
    this.pos.x = Math.min(innerWidth - m, Math.max(m, this.pos.x));
    this.pos.y = Math.min(innerHeight - m, Math.max(m, this.pos.y));
  }

  _cuadro(ahoraMs) {
    requestAnimationFrame(this._cuadro);
    if (document.hidden) return;
    const minimo = this.calidad === 'alta' ? 0 : 1000 / 30;
    if (ahoraMs - this.ultimoCuadro < minimo) return;
    this.ultimoCuadro = ahoraMs;
    const dt = Math.min(this.reloj.getDelta(), 0.05);
    const t = this.reloj.elapsedTime;

    // Suaviza hacia los parámetros del estado
    const obj = ESTADOS[this.nombreEstado];
    const s = 1 - Math.exp(-dt * 3.5);
    for (const clave of ['brillo', 'rot', 'pulso', 'conex', 'escala', 'exp', 'orbitas']) this.p[clave] += (obj[clave] - this.p[clave]) * s;
    this.color.lerp(this.colorObjetivo, 1 - Math.exp(-dt * 4));
    this.audioSuave += (this.nivelAudio - this.audioSuave) * (1 - Math.exp(-dt * 18));
    let audio = this.audioSuave;
    if (this.nombreEstado === 'hablando' && audio < 0.02) audio = 0.25 + 0.25 * Math.sin(t * 9) * Math.sin(t * 3.7); // sin audio real, late solo

    const respiro = 1 + Math.sin(t * 1.1) * 0.03 + audio * 0.12;
    this.radioPx = RADIO_REPOSO_PX * this.p.escala * this.escalaZona * respiro;

    this._mover(t, dt);

    const u = this.uniformes;
    u.uTiempo.value = t;
    u.uBrillo.value = this.p.brillo;
    u.uPulso.value = this.p.pulso;
    u.uConex.value = this.p.conex;
    u.uExp.value = this.p.exp;
    u.uAudio.value = audio;
    u.uVelPulso.value = 0.2 + this.p.rot * 0.5;
    u.uPixel.value = this.radioPx * 0.075 * this.renderer.getPixelRatio();
    this.uniformesOrbitas.uConex.value = 0.25 + this.p.orbitas * 0.15;
    this.uniformesNucleo.uBrillo.value = 0.5 + this.p.brillo * 0.6 + audio * 0.6;
    this.uniformesEstela.uBrillo.value = 0.12 + this.p.brillo * 0.2;

    this.orbe.rotation.y += dt * this.p.rot;
    this.orbe.rotation.x = Math.sin(t * 0.21) * 0.35;
    for (const a of this.anillos) {
      a.grupo.rotateOnAxis(a.giro, dt * a.vel * this.p.orbitas * 0.6);
      const pos = a.satelites.geometry.attributes.position;
      for (let i = 0; i < 2; i++) {
        const th = t * a.vel * (1 + this.p.orbitas) + i * Math.PI * 0.9;
        pos.setXYZ(i, Math.cos(th) * a.rx, Math.sin(th) * a.ry, 0);
      }
      pos.needsUpdate = true;
    }

    // Posición del orbe en el mundo (y crece desde el centro de la pantalla hacia afuera)
    const wx = (this.pos.x - innerWidth / 2) * this.mundoPorPx;
    const wy = -(this.pos.y - innerHeight / 2) * this.mundoPorPx;
    this.orbe.position.set(wx, wy, 0);
    this.orbe.scale.setScalar(this.radioPx * this.mundoPorPx);

    // Estela
    this.estela.pop();
    this.estela.unshift(new THREE.Vector3(wx, wy, -0.2));
    const pe = this.puntosEstela.geometry.attributes.position;
    for (let i = 0; i < this.estela.length; i++) {
      const v = this.estela[i];
      const temblor = i * 0.0015;
      pe.setXYZ(i, v.x + Math.sin(t * 3 + i) * temblor, v.y + Math.cos(t * 2.6 + i) * temblor, v.z);
    }
    pe.needsUpdate = true;
    this.uniformesEstela.uPixel.value = u.uPixel.value * 0.9;

    this.renderer.render(this.escena, this.camara);
    if (this.alMover) this.alMover(this.posicion());
  }
}
