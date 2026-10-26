// Interfaz de NOVA: conecta el orbe con el núcleo (eventos, mensajes, voz,
// confirmaciones). Funciona en tres modos:
//   escritorio  dentro de Tauri: ventana transparente a pantalla completa que
//               deja pasar los clics salvo sobre la barra y las tarjetas
//   navegador   para probar: el orbe flota dentro de la página
//   celular     conectado a tu PC por la red de tu casa

import { Orbe } from './orbe.js';

const TOKEN = document.querySelector('meta[name="nova-token"]').content;
const TAURI = window.__TAURI__;
const CELULAR = document.body.classList.contains('celular');
const MODO = TAURI ? 'escritorio' : CELULAR ? 'celular' : 'navegador';
if (MODO === 'navegador') document.body.classList.add('navegador');


const $ = (s) => document.querySelector(s);
const entrada = $('#entrada'), campo = $('#texto'), subtitulos = $('#subtitulos'), tarjetas = $('#tarjetas');
const orbe = new Orbe($('#orbe'), { libre: MODO !== 'celular' });
if (MODO !== 'escritorio') campo.placeholder = 'Escríbele a NOVA…';

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
  let html = escapar(t).replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, c) => { bloques.push(c); return `\u0000${bloques.length - 1}\u0000`; });
  html = html
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre>${bloques[+i]}</pre>`);
}

let textoEnCurso = '';
let temporizadorSub = null;

function subtitulo(texto, segundos = 9) {
  subtitulos.textContent = texto.length > 320 ? texto.slice(0, 300) + '…' : texto;
  subtitulos.classList.remove('oculto');
  clearTimeout(temporizadorSub);
  temporizadorSub = setTimeout(() => subtitulos.classList.add('oculto'), segundos * 1000);
}

function tarjeta(titulo, html, { copiar = '' } = {}) {
  const nodo = $('#plantilla-tarjeta').content.firstElementChild.cloneNode(true);
  nodo.querySelector('.titulo').textContent = titulo;
  nodo.querySelector('.cuerpo').innerHTML = html;
  nodo.querySelector('.cerrar').onclick = () => nodo.remove();
  nodo.querySelector('.copiar').onclick = () => navigator.clipboard?.writeText(copiar || nodo.querySelector('.cuerpo').innerText);
  tarjetas.prepend(nodo);
  while (tarjetas.children.length > 6) tarjetas.lastElementChild.remove();
  return nodo;
}

function tarjetaConfirmacion(e) {
  const nodo = $('#plantilla-confirmacion').content.firstElementChild.cloneNode(true);
  nodo.querySelector('.descripcion').textContent = e.descripcion;
  nodo.querySelector('.codigo').textContent = e.codigo;
  nodo.querySelector('.solo-clic').hidden = !e.solo_clic;
  nodo.querySelector('.si').onclick = async () => {
    nodo.remove();
    const r = await (await api('/api/confirmar', { codigo: e.codigo })).json();
    tarjeta(r.ok ? 'Hecho' : 'No se pudo', `<pre>${escapar(r.salida || '')}</pre>`);
  };
  nodo.querySelector('.no').onclick = () => { nodo.remove(); api('/api/cancelar', { codigo: e.codigo }); };
  tarjetas.prepend(nodo);
  mostrarEntrada(true);
}

// ------------------------------------------------------------ indicadores

let temporizadorRed = null;
function indicadorInternet(urls) {
  const ind = $('#ind-internet');
  ind.hidden = false;
  ind.title = urls.join('\n');
  ind.textContent = `🌐 internet · ${new URL(urls[0]).hostname}${urls.length > 1 ? ` +${urls.length - 1}` : ''}`;
  clearTimeout(temporizadorRed);
  temporizadorRed = setTimeout(() => (ind.hidden = true), 12000);
}

// ---------------------------------------------------------------- eventos

function alEvento(e) {
  switch (e.tipo) {
    case 'estado':
      orbe.estado(e.estado, e.contexto);
      if (e.estado === 'procesando' && !textoEnCurso) subtitulo('…', 60);
      break;
    case 'token':
      textoEnCurso += e.texto;
      subtitulo(textoEnCurso, 60);
      break;
    case 'reinicio':
      textoEnCurso = '';
      subtitulo('No lo sé de memoria; lo busco…', 60);
      break;
    case 'herramienta':
      if (!textoEnCurso) subtitulo(descripcionHerramienta(e), 60);
      break;
    case 'internet':
      indicadorInternet(e.urls);
      break;
    case 'confirmacion':
      tarjetaConfirmacion(e);
      break;
    case 'final': {
      textoEnCurso = '';
      const largo = e.texto.length > 280 || e.texto.includes('```') || e.texto.includes('\n- ');
      subtitulo(largo ? e.texto.split('\n')[0].slice(0, 200) + ' (más en la tarjeta)' : e.texto, largo ? 8 : 12);
      if (largo) tarjeta('NOVA', markdown(e.texto), { copiar: e.texto });
      if (e.hablar && e.voz) hablar(e.voz);
      break;
    }
    case 'platica':
      subtitulo(e.texto, 20);
      break;
    case 'aprendi':
      setTimeout(() => subtitulo('Lo recordaré.', 3), 50);
      break;
    case 'resultado':
      break;
    case 'parar':
      callar();
      break;
    case 'error':
      subtitulo('Algo falló: ' + e.texto, 10);
      break;
  }
}

function descripcionHerramienta(e) {
  const a = e.args || {};
  return {
    buscar_web: `Buscando en internet: ${a.consulta || ''}`,
    leer_web: 'Leyendo una página…',
    buscar_memoria: 'Buscando en mi memoria…',
    leer_archivo: `Leyendo ${a.ruta || 'un archivo'}…`,
    listar: `Mirando ${a.ruta || 'una carpeta'}…`,
    orden: `Preparando: ${a.orden || ''}`,
    escribir_archivo: 'Preparando un archivo…',
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
  if (MODO === 'celular') orbe.anclar(true, { x: innerWidth / 2, y: innerHeight * 0.24 });
  else if (MODO === 'escritorio') orbe.anclar(visible);
  if (visible) setTimeout(() => campo.focus(), 30);
}

entrada.addEventListener('submit', (ev) => {
  ev.preventDefault();
  const texto = campo.value.trim();
  if (!texto) return;
  campo.value = '';
  textoEnCurso = '';
  callar();
  api('/api/mensaje', { texto });
});

addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    if (MODO === 'escritorio') mostrarEntrada(false);
    callar();
  } else if (!entradaVisible && ev.key.length === 1 && !ev.ctrlKey && !ev.altKey) {
    mostrarEntrada(true);
  }
});

// Posición de los textos alrededor del orbe
orbe.alMover = ({ x, y, r }) => {
  if (MODO === 'celular') return;
  const debajo = y + r * 1.55;
  subtitulos.style.left = `${x}px`;
  subtitulos.style.top = `${debajo}px`;
  const ind = $('#indicadores');
  ind.style.left = `${x}px`;
  ind.style.top = `${Math.max(8, y - r * 1.6 - 24)}px`;
  if (MODO === 'navegador') return;  // en el navegador la barra se queda abajo
  const alto = subtitulos.classList.contains('oculto') ? 0 : subtitulos.offsetHeight + 12;
  entrada.style.left = `${Math.min(Math.max(x, 270), innerWidth - 270)}px`;
  entrada.style.top = `${Math.min(debajo + alto, innerHeight - 60)}px`;
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
    subtitulo('No tengo permiso para usar el micrófono.', 6);
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
  if (!r.ok || !datos.texto) { subtitulo(datos.error || 'No te entendí.', 6); orbe.estado('reposo'); return; }
  subtitulo(`«${datos.texto}»`, 5);
  textoEnCurso = '';
  api('/api/mensaje', { texto: datos.texto, voz: true });
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

if (TAURI) {
  document.body.classList.add('escritorio');
  mostrarEntrada(false);
  const { listen } = TAURI.event;
  const { invoke } = TAURI.core;
  listen('cursor', (e) => orbe.moverCursor(e.payload.x, e.payload.y));
  listen('abrir', () => mostrarEntrada(!entradaVisible));
  listen('hablar', (e) => (e.payload.activo ? empezarGrabacion() : terminarGrabacion()));
  // Le dice al envoltorio qué zonas reciben clics; el resto deja pasar el ratón al escritorio.
  setInterval(() => {
    const zonas = [];
    const agregar = (el) => {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) zonas.push([r.left, r.top, r.width, r.height]);
    };
    if (entradaVisible) agregar(entrada);
    for (const t of tarjetas.children) agregar(t);
    invoke('zonas', { zonas }).catch(() => {});
  }, 120);
}

// ------------------------------------------------------------- celular

function mostrarEmparejar() {
  if (document.querySelector('.emparejar')) return;
  const nodo = tarjeta('Conectar con tu PC', `
    <p>En tu PC, abre NOVA y di o escribe <b>«conecta mi celular»</b>. Te mostrará un código de 6 cifras.</p>
    <p><input class="codigo-emparejar" inputmode="numeric" maxlength="6" placeholder="000000"
      style="font-size:22px;letter-spacing:4px;width:9em;background:#0008;color:#fff;border:1px solid #fff4;border-radius:8px;padding:6px"></p>
    <p><button class="boton-emparejar">Conectar</button></p>`);
  nodo.classList.add('emparejar');
  nodo.querySelector('.boton-emparejar').onclick = async () => {
    const codigo = nodo.querySelector('.codigo-emparejar').value.trim();
    const r = await fetch('/api/emparejar', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Nova-Token': TOKEN },
      body: JSON.stringify({ codigo, nombre: navigator.userAgent.slice(0, 80) }),
    });
    const d = await r.json();
    if (!r.ok) { nodo.querySelector('.cuerpo p').textContent = d.error || 'Código incorrecto.'; return; }
    tokenCelular = d.dispositivo;
    try { localStorage.setItem('nova-dispositivo', tokenCelular); } catch { /* sin almacenamiento */ }
    nodo.remove();
    escucharEventos();
  };
}

if (CELULAR) mostrarEntrada(true);
if (CELULAR && !tokenCelular) mostrarEmparejar();
else escucharEventos();
