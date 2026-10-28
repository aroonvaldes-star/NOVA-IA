// Interfaz de NOVA: conecta el orbe con el núcleo (eventos, mensajes, voz,
// confirmaciones). Funciona en tres modos:
//   escritorio  dentro de Tauri: ventana transparente a pantalla completa que
//               deja pasar los clics salvo sobre el orbe y la conversación
//   barra       dentro de Tauri: la ventanita aparte donde le escribes
//   navegador   la ventana de NOVA: el orbe flota junto a la conversación
//   celular     conectado a tu PC por la red de tu casa

import { Orbe } from './orbe.js';

const TOKEN = document.querySelector('meta[name="nova-token"]').content;
const TAURI = window.__TAURI__;
const CELULAR = document.body.classList.contains('celular');
const BARRA = TAURI && new URLSearchParams(location.search).has('barra');
const MODO = BARRA ? 'barra' : TAURI ? 'escritorio' : CELULAR ? 'celular' : 'navegador';
if (MODO === 'navegador' || MODO === 'barra') document.body.classList.add(MODO);


const $ = (s) => document.querySelector(s);
const entrada = $('#entrada'), campo = $('#texto');
const orbe = new Orbe($('#orbe'), { libre: MODO !== 'celular' });
if (MODO !== 'escritorio') campo.placeholder = MODO === 'barra' ? 'Escríbele a NOVA… (↑↓ mueven la plática · Esc cierra)' : 'Escríbele a NOVA…';

let tokenCelular = null;
try { tokenCelular = localStorage.getItem('nova-dispositivo'); } catch { /* sin almacenamiento */ }

function cabeceras(extra = {}) {
  const h = { 'X-Nova-Token': TOKEN, ...extra };
  if (CELULAR && tokenCelular) h['X-Nova-Dispositivo'] = tokenCelular;
  return h;
}

async function api(ruta, cuerpo, opciones = {}) {
  const r = await fetch(ruta, {
    method: cuerpo === undefined ? 'GET' : 'POST',
    headers: cabeceras(cuerpo instanceof Blob ? { 'Content-Type': cuerpo.type } : { 'Content-Type': 'application/json' }),
    body: cuerpo === undefined ? undefined : cuerpo instanceof Blob ? cuerpo : JSON.stringify(cuerpo),
    ...opciones,
  });
  if (!r.ok && r.status === 401 && CELULAR) mostrarEmparejar();
  return r;
}

// ------------------------------------------------------------------ texto

function escapar(t) {
  return t.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function markdown(t) {
  const bloques = [];
  let html = escapar(t).replace(/```[\w-]*\n?([\s\S]*?)(```|$)/g, (_, c) => { bloques.push(c); return `\u0000${bloques.length - 1}\u0000`; });
  html = html
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
    .split(/\n{2,}/).map((p) => {
      const lineas = p.split('\n');
      if (lineas.every((l) => /^\s*[-*•] /.test(l))) return `<ul>${lineas.map((l) => `<li>${l.replace(/^\s*[-*•] /, '')}</li>`).join('')}</ul>`;
      return `<p>${p.replace(/\n/g, '<br>')}</p>`;
    }).join('');
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre><code>${bloques[+i]}</code></pre>`);
}

// ------------------------------------------------------------ conversación
// Todo lo que NOVA y tú se dicen aparece junto al orbe, en el mismo espacio:
// sin recuadros, con lo viejo desvaneciéndose hacia arriba.

const conversacion = $('#conversacion');
let enCurso = null;      // el mensaje de NOVA que se está escribiendo
let textoEnCurso = '';
let estado = null;       // la línea de "buscando…", "mirando tu pantalla…"
let ultimoDia = '';

function alFondo(forzar = false) {
  const cerca = conversacion.scrollHeight - conversacion.scrollTop - conversacion.clientHeight < 160;
  if (forzar || cerca) conversacion.scrollTop = conversacion.scrollHeight;
  desvanecer();
}

// Los mensajes se desvanecen mientras más arriba quedan; al subir con la barra
// vuelven a verse completos porque bajan a la zona clara.
let pendienteDesvanecer = false;
function desvanecer() {
  if (pendienteDesvanecer) return;
  pendienteDesvanecer = true;
  requestAnimationFrame(() => {
    pendienteDesvanecer = false;
    const caja = conversacion.getBoundingClientRect();
    if (!caja.height) return;
    for (const nodo of conversacion.children) {
      const r = nodo.getBoundingClientRect();
      const f = (r.bottom - caja.top) / caja.height;  // 0 arriba, 1 abajo
      const op = f >= 0.55 ? 1 : Math.max(0.12, 0.12 + (f / 0.55) * 0.88);
      nodo.style.setProperty('--op', op.toFixed(2));
    }
  });
}
conversacion.addEventListener('scroll', desvanecer, { passive: true });
addEventListener('resize', desvanecer);

function separadorDia(fecha) {
  const d = new Date(fecha * 1000);
  const dia = d.toDateString();
  if (dia === ultimoDia) return;
  ultimoDia = dia;
  const hoy = new Date().toDateString();
  const ayer = new Date(Date.now() - 864e5).toDateString();
  const texto = dia === hoy ? 'hoy' : dia === ayer ? 'ayer' : d.toLocaleDateString('es-MX', { weekday: 'long', day: 'numeric', month: 'long' });
  const sep = document.createElement('div');
  sep.className = 'separador';
  sep.textContent = texto;
  conversacion.append(sep);
}

function mensaje(quien, texto, { fecha = Date.now() / 1000, html = null } = {}) {
  separadorDia(fecha);
  const nodo = document.createElement('div');
  nodo.className = `msg ${quien}`;
  if (html !== null) nodo.innerHTML = html;
  else if (quien === 'nova') nodo.innerHTML = markdown(texto);
  else nodo.textContent = texto;
  conversacion.append(nodo);
  document.body.classList.add('conversando');
  acomodar();
  despertar();
  alFondo(quien === 'tu');
  return nodo;
}

function lineaEstado(texto) {
  if (!estado) {
    estado = mensaje('sistema', texto);
  } else {
    estado.textContent = texto;
  }
  alFondo();
}

function quitarEstado() {
  estado?.remove();
  estado = null;
}

function escribiendo(t) {
  if (!enCurso) {
    quitarEstado();
    enCurso = mensaje('nova', '');
    enCurso.classList.add('escribiendo');
  }
  textoEnCurso += t;
  enCurso.innerHTML = markdown(textoEnCurso);
  alFondo();
}

function terminarMensaje(texto) {
  quitarEstado();
  if (!enCurso) enCurso = mensaje('nova', '');
  enCurso.classList.remove('escribiendo');
  enCurso.innerHTML = markdown(texto);
  enCurso = null;
  textoEnCurso = '';
  alFondo();
}

function fuentes(urls) {
  const enlaces = [...new Set(urls)].slice(0, 5).map((u) => {
    let host = u;
    try { host = new URL(u).hostname.replace(/^www\./, ''); } catch { /* url rara */ }
    return `<a href="${escapar(u)}" target="_blank" rel="noopener noreferrer">${escapar(host)}</a>`;
  });
  if (enlaces.length) mensaje('sistema', '', { html: '🌐 ' + enlaces.join(' · ') });
}

function confirmacion(e) {
  quitarEstado();
  const nodo = $('#plantilla-confirmacion').content.firstElementChild.cloneNode(true);
  nodo.querySelector('.descripcion').textContent = e.descripcion;
  nodo.querySelector('.codigo').textContent = e.codigo;
  nodo.querySelector('.solo-clic').hidden = !e.solo_clic;
  nodo.querySelector('.si').onclick = async () => {
    nodo.classList.add('resuelta');
    const r = await (await api('/api/confirmar', { codigo: e.codigo })).json();
    mensaje('sistema', (r.ok ? '✔ ' : '✘ ') + (r.salida || '').slice(0, 600));
  };
  nodo.querySelector('.no').onclick = () => {
    nodo.classList.add('resuelta');
    api('/api/cancelar', { codigo: e.codigo });
    mensaje('sistema', 'Cancelado.');
  };
  separadorDia(Date.now() / 1000);
  conversacion.append(nodo);
  document.body.classList.add('conversando');
  acomodar();
  alFondo(true);
  mostrarEntrada(true);
}

async function cargarConversacion() {
  try {
    const r = await api('/api/conversacion');
    if (!r.ok) return;
    for (const m of await r.json()) mensaje(m.rol === 'usuario' ? 'tu' : 'nova', m.texto, { fecha: m.fecha });
    alFondo(true);
  } catch { /* sin núcleo todavía */ }
}

// ------------------------------------------------------------ indicadores

let temporizadorRed = null;
function indicadorInternet(urls) {
  const ind = $('#ind-internet');
  ind.hidden = false;
  ind.title = urls.join('\n');
  try { ind.textContent = `🌐 internet · ${new URL(urls[0]).hostname}${urls.length > 1 ? ` +${urls.length - 1}` : ''}`; } catch { /* url rara */ }
  clearTimeout(temporizadorRed);
  temporizadorRed = setTimeout(() => (ind.hidden = true), 12000);
}

// ---------------------------------------------------------------- eventos

function alEvento(e) {
  switch (e.tipo) {
    case 'estado':
      orbe.estado(e.estado, e.contexto);
      if (e.color) document.documentElement.style.setProperty('--color-orbe', e.color);
      if (e.estado === 'procesando' && !enCurso && !estado) lineaEstado('Pensando…');
      if (e.estado === 'reposo') $('#ind-pantalla').hidden = true;
      break;
    case 'usuario': {
      // Lo que escribiste aquí ya se ve; lo de otras ventanas (la barra, el celular, la voz) aparece al llegar.
      const i = enviadosAqui.indexOf(e.texto);
      if (i >= 0) enviadosAqui.splice(i, 1);
      else if (MODO !== 'barra') { mensaje('tu', e.texto); lineaEstado('Pensando…'); despertar(); }
      break;
    }
    case 'token':
      escribiendo(e.texto);
      break;
    case 'reinicio':
      if (enCurso) { enCurso.remove(); enCurso = null; textoEnCurso = ''; }
      lineaEstado('No lo sé de memoria; lo busco…');
      break;
    case 'herramienta':
      if (!enCurso) lineaEstado(descripcionHerramienta(e));
      break;
    case 'viendo':
      $('#ind-pantalla').hidden = false;
      break;
    case 'internet':
      indicadorInternet(e.urls);
      quitarEstado();
      fuentes(e.urls);
      break;
    case 'confirmacion':
      confirmacion(e);
      break;
    case 'final':
      terminarMensaje(e.texto);
      if (e.hablar && e.voz) hablar(e.voz);
      break;
    case 'platica':
      mensaje('nova', e.texto);
      break;
    case 'aprendi':
      mensaje('sistema', 'Lo recordaré' + (e.recuerdos?.length ? ': ' + e.recuerdos.map((r) => r.texto || r).join('; ') : '.'));
      break;
    case 'aviso':
      mensaje('sistema', e.texto);
      break;
    case 'parar':
      callar();
      break;
    case 'error':
      quitarEstado();
      mensaje('sistema', 'Algo falló: ' + e.texto);
      break;
  }
}

function descripcionHerramienta(e) {
  const a = e.args || {};
  return {
    buscar_web: `🌐 Buscando en internet: ${a.consulta || ''}`,
    leer_web: `🌐 Leyendo ${a.url || 'una página'}…`,
    buscar_memoria: 'Buscando en mi memoria y tus archivos…',
    buscar_archivos: `Buscando archivos: ${a.nombre || ''}`,
    leer_archivo: `Leyendo ${a.ruta || 'un archivo'}…`,
    listar: `Mirando ${a.ruta || 'una carpeta'}…`,
    ver_pantalla: '👁 Mirando tu pantalla…',
    orden: `Preparando: ${a.orden || ''}`,
    escribir_archivo: 'Preparando un archivo…',
    calcular: 'Calculando…',
  }[e.nombre] || 'Pensando…';
}

async function escucharEventos() {
  for (;;) {
    try {
      const r = await fetch('/api/eventos', { headers: cabeceras() });
      if (r.status === 401 && CELULAR) { mostrarEmparejar(); return; }
      $('#ind-offline').hidden = true;
      const lector = r.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await lector.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const bloque = buf.slice(0, i);
          buf = buf.slice(i + 2);
          for (const linea of bloque.split('\n')) {
            if (linea.startsWith('data: ')) {
              try { alEvento(JSON.parse(linea.slice(6))); } catch { /* evento dañado */ }
            }
          }
        }
      }
    } catch { /* núcleo reiniciándose */ }
    $('#ind-offline').hidden = false;
    $('#ind-offline').textContent = 'reconectando con el núcleo…';
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// ------------------------------------------------------------- entrada

let entradaVisible = MODO !== 'escritorio';

function mostrarEntrada(visible) {
  entradaVisible = visible;
  entrada.classList.toggle('oculto', !visible);
  if (MODO === 'escritorio') orbe.anclar(visible);
  if (visible) { despertar(); setTimeout(() => campo.focus(), 30); }
}

entrada.addEventListener('submit', (ev) => {
  ev.preventDefault();
  const texto = campo.value.trim();
  if (!texto) return;
  campo.value = '';
  callar();
  enviar(texto);
});

const enviadosAqui = [];

function enviar(texto, voz = false) {
  if (MODO === 'barra') {
    // La respuesta se lee junto al orbe; la barra se esconde.
    api('/api/mensaje', voz ? { texto, voz: true } : { texto });
    TAURI.core.invoke('cerrar_barra').catch(() => {});
    return;
  }
  if (enCurso) terminarMensaje(textoEnCurso);
  enviadosAqui.push(texto);
  mensaje('tu', texto);
  lineaEstado('Pensando…');
  api('/api/mensaje', voz ? { texto, voz: true } : { texto });
}

addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    if (MODO === 'escritorio') mostrarEntrada(false);
    if (MODO === 'barra') TAURI.core.invoke('cerrar_barra').catch(() => {});
    callar();
  } else if (MODO !== 'escritorio' && !entradaVisible && ev.key.length === 1 && !ev.ctrlKey && !ev.altKey) {
    mostrarEntrada(true);
  }
});

// Dónde vive el orbe según haya o no conversación, y el tamaño de la ventana.
function acomodar() {
  const conversando = document.body.classList.contains('conversando');
  if (MODO === 'celular') {
    orbe.limitar({ x: 0, y: 40, w: innerWidth, h: innerHeight * 0.3 - 40 }, 0.62);
    orbe.anclar(true);
  } else if (MODO === 'navegador') {
    if (!conversando) orbe.limitar(null, 1);
    else if (innerWidth <= 820) orbe.limitar({ x: 0, y: 30, w: innerWidth, h: innerHeight * 0.36 - 30 }, 0.62);
    else {
      const ancho = $('#charla').getBoundingClientRect().width || Math.min(innerWidth * 0.58, 760);
      orbe.limitar({ x: 0, y: 0, w: innerWidth - ancho, h: innerHeight }, 1);
    }
  }
}
addEventListener('resize', acomodar);
setTimeout(acomodar, 600);  // cuando termina la transición de la charla

// En el escritorio transparente la conversación se duerme cuando no la usas.
let temporizadorDormir = null;
function despertar() {
  if (MODO !== 'escritorio') return;
  conversacion.classList.remove('dormida');
  clearTimeout(temporizadorDormir);
  temporizadorDormir = setTimeout(() => { if (!entradaVisible) conversacion.classList.add('dormida'); }, 25000);
}

// Posición de los indicadores (y en el escritorio, de la charla) alrededor del orbe
orbe.alMover = ({ x, y, r }) => {
  if (MODO === 'celular') return;
  const ind = $('#indicadores');
  const toque = $('#toque');
  if (!toque.hidden) {
    const lado = r * 1.3;
    Object.assign(toque.style, { left: `${x - lado / 2}px`, top: `${y - lado / 2}px`, width: `${lado}px`, height: `${lado}px` });
    const halo = $('#halo'), h = r * 3.2;
    Object.assign(halo.style, { left: `${x - h / 2}px`, top: `${y - h / 2}px`, width: `${h}px`, height: `${h}px` });
  }
  ind.style.left = `${x}px`;
  ind.style.top = `${Math.max(8, y - r * 1.6 - 24)}px`;
  if (MODO !== 'escritorio') return;
  const charla = $('#charla');
  const ancho = charla.offsetWidth;
  const derecha = x + r * 1.4 + ancho + 24 < innerWidth;
  charla.style.left = `${derecha ? x + r * 1.4 : Math.max(16, x - r * 1.4 - ancho)}px`;
  // Deja 90 px abajo para la barra de escribir, que se pega debajo de la conversación.
  charla.style.top = `${Math.max(16, Math.min(y - charla.offsetHeight / 2, innerHeight - charla.offsetHeight - 90))}px`;
};
if (MODO !== 'celular') addEventListener('mousemove', (ev) => orbe.moverCursor(ev.clientX, ev.clientY));

// ------------------------------------------------------------------ voz

let contextoAudio = null;
let fuenteVoz = null;

function audioCtx() {
  contextoAudio ??= new AudioContext();
  return contextoAudio;
}

async function hablar(texto) {
  try {
    const r = await api('/api/voz/tts', { texto });
    if (!r.ok) return;
    const ctx = audioCtx();
    const buffer = await ctx.decodeAudioData(await r.arrayBuffer());
    callar();
    const fuente = ctx.createBufferSource();
    const analizador = ctx.createAnalyser();
    analizador.fftSize = 512;
    fuente.buffer = buffer;
    fuente.connect(analizador).connect(ctx.destination);
    const datos = new Uint8Array(analizador.fftSize);
    orbe.estado('hablando');
    fuenteVoz = fuente;
    const medir = () => {
      if (fuenteVoz !== fuente) return;
      analizador.getByteTimeDomainData(datos);
      let s = 0;
      for (const v of datos) s += ((v - 128) / 128) ** 2;
      orbe.audio(Math.min(1, Math.sqrt(s / datos.length) * 4));
      requestAnimationFrame(medir);
    };
    fuente.onended = () => { if (fuenteVoz === fuente) { fuenteVoz = null; orbe.audio(0); orbe.estado('reposo'); } };
    fuente.start();
    medir();
  } catch { /* sin voz instalada */ }
}

function callar() {
  if (fuenteVoz) { try { fuenteVoz.stop(); } catch { /* ya terminó */ } fuenteVoz = null; orbe.audio(0); }
}

// Pulsar para hablar: graba PCM mono a 16 kHz y lo manda como WAV a whisper.
let grabacion = null;

async function empezarGrabacion() {
  if (grabacion) return;
  callar();
  try {
    const flujo = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    const ctx = new AudioContext({ sampleRate: 16000 });
    const fuente = ctx.createMediaStreamSource(flujo);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const trozos = [];
    proc.onaudioprocess = (ev) => {
      const d = ev.inputBuffer.getChannelData(0);
      trozos.push(new Float32Array(d));
      let s = 0;
      for (let i = 0; i < d.length; i += 8) s += d[i] * d[i];
      orbe.audio(Math.min(1, Math.sqrt(s / (d.length / 8)) * 6));
    };
    fuente.connect(proc);
    proc.connect(ctx.destination);
    grabacion = { flujo, ctx, proc, trozos };
    $('#microfono').classList.add('grabando');
    $('#ind-escucha').hidden = false;
    orbe.estado('escuchando');
  } catch {
    mensaje('sistema', 'No tengo permiso para usar el micrófono.');
  }
}

async function terminarGrabacion() {
  const g = grabacion;
  if (!g) return;
  grabacion = null;
  g.proc.disconnect();
  g.flujo.getTracks().forEach((t) => t.stop());
  await g.ctx.close();
  $('#microfono').classList.remove('grabando');
  $('#ind-escucha').hidden = true;
  orbe.audio(0);
  const total = g.trozos.reduce((n, t) => n + t.length, 0);
  if (total < 16000 * 0.3) { orbe.estado('reposo'); return; }
  const wav = aWav(g.trozos, total, 16000);
  orbe.estado('procesando');
  const r = await api('/api/voz/stt', wav);
  const datos = await r.json();
  if (!r.ok || !datos.texto) { mensaje('sistema', datos.error || 'No te entendí.'); orbe.estado('reposo'); return; }
  enviar(datos.texto, true);
}

function aWav(trozos, total, tasa) {
  const buf = new ArrayBuffer(44 + total * 2);
  const v = new DataView(buf);
  const txt = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  txt(0, 'RIFF'); v.setUint32(4, 36 + total * 2, true); txt(8, 'WAVE'); txt(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, tasa, true); v.setUint32(28, tasa * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  txt(36, 'data'); v.setUint32(40, total * 2, true);
  let o = 44;
  for (const t of trozos) for (const s of t) { v.setInt16(o, Math.max(-1, Math.min(1, s)) * 0x7fff, true); o += 2; }
  return new Blob([buf], { type: 'audio/wav' });
}

const mic = $('#microfono');
mic.addEventListener('pointerdown', (ev) => { ev.preventDefault(); empezarGrabacion(); });
for (const tipo of ['pointerup', 'pointerleave', 'pointercancel']) mic.addEventListener(tipo, () => terminarGrabacion());

// ------------------------------------------------------- escritorio (Tauri)

function localStorageSeguro(clave, valor) {
  try {
    if (valor === undefined) return localStorage.getItem(clave);
    localStorage.setItem(clave, valor);
  } catch { /* sin almacenamiento */ }
  return null;
}

if (MODO === 'barra') {
  // Solo la barra: siempre visible y con el cursor listo para escribir.
  mostrarEntrada(true);
  TAURI.event.listen('enfocar', () => { campo.value = ''; campo.focus(); });
  // La conversación vive junto al orbe: desde aquí la subes y bajas con la rueda, el touchpad o las flechas.
  addEventListener('wheel', (ev) => {
    ev.preventDefault();
    TAURI.core.invoke('desplazar', { dy: ev.deltaMode === 1 ? ev.deltaY * 32 : ev.deltaY }).catch(() => {});
  }, { passive: false });
  addEventListener('keydown', (ev) => {
    const paso = { ArrowUp: -60, ArrowDown: 60, PageUp: -400, PageDown: 400 }[ev.key];
    if (paso) { ev.preventDefault(); TAURI.core.invoke('desplazar', { dy: paso }).catch(() => {}); }
  });
  addEventListener('focus', () => campo.focus());
  // Si das clic en otra parte, se esconde (con un respiro, por si el foco solo cambia de lugar dentro).
  addEventListener('blur', () => setTimeout(() => {
    if (!document.hasFocus() && !grabacion) TAURI.core.invoke('cerrar_barra').catch(() => {});
  }, 400));
}

if (MODO === 'escritorio') {
  document.body.classList.add('escritorio');
  mostrarEntrada(false);
  const { listen } = TAURI.event;
  const { invoke } = TAURI.core;
  listen('cursor', (e) => orbe.moverCursor(e.payload.x, e.payload.y));
  listen('despertar', () => despertar());
  // Mientras escribes, el orbe y la plática se quedan quietos para que la barra quede pegada debajo.
  listen('barra', (e) => {
    const p = orbe.posicion();
    orbe.anclar(Boolean(e.payload), e.payload ? { x: p.x, y: p.y } : null);
  });
  listen('desplazar', (e) => { despertar(); conversacion.scrollBy({ top: Number(e.payload) || 0 }); });
  // Con la rueda o el touchpad encima, la conversación despierta y se desplaza.
  conversacion.addEventListener('wheel', () => despertar(), { passive: true });
  listen('hablar', (e) => (e.payload.activo ? empezarGrabacion() : terminarGrabacion()));
  // Clic en el orbe: abre la barra para escribirle.
  const toque = $('#toque');
  toque.hidden = false;
  $('#halo').hidden = false;
  toque.addEventListener('click', () => invoke('abrir_barra').catch(() => {}));
  toque.addEventListener('mouseenter', () => despertar());  // al pasar sobre el orbe, la plática vuelve y se puede desplazar
  if (!localStorageSeguro('nova-pista-escritorio')) {
    mensaje('sistema', 'Dame clic para escribirme, o usa Ctrl+Alt+A. Para hablarme, mantén Ctrl+Alt+Espacio.');
    despertar();
    localStorageSeguro('nova-pista-escritorio', '1');
  }
  // Le dice al envoltorio qué zonas reciben clics; el resto deja pasar el ratón al escritorio.
  setInterval(() => {
    const zonas = [];
    const agregar = (el) => {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) zonas.push([r.left, r.top, r.width, r.height]);
    };
    agregar(toque);
    if (!conversacion.classList.contains('dormida')) agregar(conversacion);
    invoke('zonas', { zonas }).catch(() => {});
    // La barra se abre justo debajo de la conversación (o del orbe si todavía no hay plática).
    const c = $('#charla').getBoundingClientRect();
    const r = conversacion.getBoundingClientRect();
    const ancla = r.height ? { x: r.left, y: r.bottom + 8, ancho: r.width } : (() => {
      const o = orbe.posicion();
      return { x: Math.max(8, o.x - 300), y: Math.min(innerHeight - 80, o.y + o.r * 1.6), ancho: 600 };
    })();
    if (c.width || r.height) invoke('anclar_barra', ancla).catch(() => {});
  }, 120);
}

// ------------------------------------------------------------- celular

function mostrarEmparejar() {
  if (document.querySelector('.emparejar')) return;
  const nodo = mensaje('nova', '', { html: `
    <p>Para hablar conmigo desde aquí, en tu PC escríbeme <b>«conecta mi celular»</b>. Te daré un código de 6 cifras.</p>
    <p><input class="codigo-emparejar" inputmode="numeric" maxlength="6" placeholder="000000" aria-label="Código"></p>
    <p><button class="boton boton-emparejar">Conectar</button></p>` });
  nodo.classList.add('emparejar');
  nodo.querySelector('.boton-emparejar').onclick = async () => {
    const codigo = nodo.querySelector('.codigo-emparejar').value.trim();
    const r = await fetch('/api/emparejar', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Nova-Token': TOKEN },
      body: JSON.stringify({ codigo, nombre: navigator.userAgent.slice(0, 80) }),
    });
    const d = await r.json();
    if (!r.ok) { nodo.querySelector('p').textContent = d.error || 'Código incorrecto.'; return; }
    tokenCelular = d.dispositivo;
    try { localStorage.setItem('nova-dispositivo', tokenCelular); } catch { /* sin almacenamiento */ }
    nodo.remove();
    iniciar();
  };
}

async function iniciar() {
  await cargarConversacion();
  escucharEventos();
}

if (CELULAR) mostrarEntrada(true);
acomodar();
if (CELULAR && !tokenCelular) mostrarEmparejar();
else iniciar();
