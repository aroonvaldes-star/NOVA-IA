// Envoltorio de escritorio de NOVA.
//
// Abre una ventana transparente, sin marco y siempre encima, del tamaño de la
// pantalla, donde vive el orbe. La ventana deja pasar los clics al escritorio
// salvo sobre las zonas que la interfaz reporta (la barra de texto y las
// tarjetas). También:
//   * arranca el núcleo de NOVA (Python) si no está corriendo y lo cierra al salir
//   * manda la posición del cursor al orbe para que se aparte
//   * abre una barra aparte (ventana normal, que sí recibe el teclado) para escribirle:
//     con clic en el orbe, con Ctrl+Alt+A o desde la bandeja
//   * registra los atajos globales: Ctrl+Alt+A (escribir), Ctrl+Alt+Espacio (hablar) y
//     Alt Gr+Q o Ctrl+Shift+Q (esconder el orbe en la bandeja o volver a sacarlo)
//   * pone el icono en la bandeja del sistema
//   * deja lo que falle en %LOCALAPPDATA%\NOVA\estado\escritorio.log

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const PUERTO: u16 = 8190;

struct Zonas(Mutex<Vec<[f64; 4]>>);
/// Dónde va la barra: justo debajo de la conversación (x, y, ancho en píxeles de la página del orbe).
struct AnclaBarra(Mutex<Option<[f64; 3]>>);
struct Nucleo(Mutex<Option<Child>>);

/// Anota en estado\escritorio.log (la app no tiene consola donde verlo).
fn anotar(texto: &str) {
    let Ok(local) = std::env::var("LOCALAPPDATA") else { return };
    let dir = PathBuf::from(local).join("NOVA").join("estado");
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("escritorio.log")) {
        let segundos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{segundos}] {texto}");
    }
}

#[tauri::command]
fn zonas(estado: tauri::State<'_, Zonas>, zonas: Vec<[f64; 4]>) {
    if let Ok(mut z) = estado.0.lock() {
        if z.is_empty() && !zonas.is_empty() {
            anotar("La interfaz del orbe ya se comunica con el escritorio.");
        }
        *z = zonas;
    }
}

#[tauri::command]
fn anclar_barra(estado: tauri::State<'_, AnclaBarra>, x: f64, y: f64, ancho: f64) {
    if let Ok(mut a) = estado.0.lock() {
        *a = Some([x, y, ancho]);
    }
}

#[tauri::command]
fn abrir_barra(app: AppHandle) {
    mostrar_barra(&app, true);
}

/// Desde la barra: sube o baja la conversación que está junto al orbe (rueda, touchpad o flechas).
#[tauri::command]
fn desplazar(app: AppHandle, dy: f64) {
    let _ = app.emit_to("orbe", "desplazar", dy);
}

#[tauri::command]
fn cerrar_barra(app: AppHandle) {
    mostrar_barra(&app, false);
}

/// Muestra (y enfoca) o esconde la barra para escribirle.
fn mostrar_barra(app: &AppHandle, visible: bool) {
    let Some(b) = app.get_webview_window("barra") else {
        anotar("No existe la ventana de la barra.");
        return;
    };
    if visible {
        anotar("Abro la barra para escribir.");
        // Pegada debajo de la conversación, del mismo ancho: se ve como parte del diálogo.
        let ancla = app.state::<AnclaBarra>().0.lock().ok().and_then(|a| *a);
        if let (Some([x, y, ancho]), Some(orbe)) = (ancla, app.get_webview_window("orbe")) {
            if let (Ok(pos), Ok(escala)) = (orbe.outer_position(), orbe.scale_factor()) {
                let origen = pos.to_logical::<f64>(escala);
                let _ = b.set_size(tauri::LogicalSize::new(ancho.max(360.0), 64.0));
                let _ = b.set_position(tauri::LogicalPosition::new(origen.x + x, origen.y + y));
            }
        }
        let _ = app.emit_to("orbe", "barra", true);
        let _ = b.show();
        let _ = b.unminimize();
        let _ = b.set_focus();
        let _ = b.emit("enfocar", ());
        let _ = app.emit_to("orbe", "despertar", ());
    } else {
        let _ = b.hide();
        let _ = app.emit_to("orbe", "barra", false);
    }
}

/// Esconde el orbe (queda solo el icono junto al reloj) o lo vuelve a mostrar.
fn alternar_orbe(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("orbe") {
        if w.is_visible().unwrap_or(false) {
            let _ = w.hide();
            mostrar_barra(app, false);
        } else {
            let _ = w.show();
        }
    }
}

fn alternar_barra(app: &AppHandle) {
    let visible = app
        .get_webview_window("barra")
        .and_then(|b| b.is_visible().ok())
        .unwrap_or(false);
    if let Some(w) = app.get_webview_window("orbe") {
        let _ = w.show();
    }
    mostrar_barra(app, !visible);
}

fn nucleo_responde() -> bool {
    let dir: SocketAddr = ([127, 0, 0, 1], PUERTO).into();
    TcpStream::connect_timeout(&dir, Duration::from_millis(300)).is_ok()
}

/// Busca cómo arrancar el núcleo: variable NOVA_NUCLEO, el ejecutable empaquetado
/// junto a esta app, o `python -m nova` desde la carpeta del proyecto (desarrollo).
fn comando_nucleo() -> Command {
    let argumentos = ["servidor", "--puerto", &PUERTO.to_string()].map(String::from);
    if let Ok(ruta) = std::env::var("NOVA_NUCLEO") {
        let mut c = Command::new(ruta);
        c.args(&argumentos);
        return c;
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for nombre in ["nova-nucleo.exe", "nucleo/nova-nucleo.exe", "nova-nucleo"] {
                let candidato = dir.join(nombre);
                if candidato.is_file() {
                    let mut c = Command::new(candidato);
                    c.args(&argumentos);
                    return c;
                }
            }
        }
    }
    // El entorno que crea instalar.bat
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let pyw = PathBuf::from(local).join("NOVA").join("venv").join("Scripts").join("pythonw.exe");
        if pyw.is_file() {
            let mut c = Command::new(pyw);
            c.arg("-m").arg("nova").args(&argumentos);
            return c;
        }
    }
    let raiz = std::env::var("NOVA_RAIZ")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join(".."));
    let python = if cfg!(windows) { "pythonw" } else { "python3" };
    let mut c = Command::new(python);
    c.arg("-m").arg("nova").args(&argumentos).current_dir(raiz);
    c
}

fn arrancar_nucleo(app: &AppHandle) {
    if nucleo_responde() {
        return;
    }
    let mut c = comando_nucleo();
    c.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    match c.spawn() {
        Ok(hijo) => {
            if let Ok(mut n) = app.state::<Nucleo>().0.lock() {
                *n = Some(hijo);
            }
        }
        Err(e) => anotar(&format!("No se pudo arrancar el núcleo de NOVA: {e}")),
    }
    let limite = Instant::now() + Duration::from_secs(40);
    while Instant::now() < limite && !nucleo_responde() {
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn detener_nucleo(app: &AppHandle) {
    if let Ok(mut n) = app.state::<Nucleo>().0.lock() {
        if let Some(mut hijo) = n.take() {
            let _ = hijo.kill();
            let _ = hijo.wait();
        }
    }
}

fn crear_ventana(app: &AppHandle) -> tauri::Result<()> {
    let url = format!("http://127.0.0.1:{PUERTO}/").parse().expect("URL válida");
    let ventana = WebviewWindowBuilder::new(app, "orbe", WebviewUrl::External(url))
        .title("NOVA")
        .transparent(true)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .shadow(false)
        .resizable(false)
        .focused(false)
        .visible(false)
        .build()?;
    if let Some(monitor) = ventana.primary_monitor()? {
        ventana.set_position(*monitor.position())?;
        ventana.set_size(*monitor.size())?;
    }
    ventana.set_ignore_cursor_events(true)?;
    ventana.show()?;

    // La barra para escribirle: una ventana normal (recibe teclado y clics), abajo al centro.
    let url_barra = format!("http://127.0.0.1:{PUERTO}/?barra=1").parse().expect("URL válida");
    let barra = WebviewWindowBuilder::new(app, "barra", WebviewUrl::External(url_barra))
        .title("Escríbele a NOVA")
        // Ventana normal (no transparente): así recibe bien el touchpad y la rueda en Windows.
        .transparent(false)
        .background_color(tauri::window::Color(8, 12, 26, 255))
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .shadow(false)
        .resizable(false)
        .inner_size(620.0, 64.0)
        .visible(false)
        .build()?;
    if let Some(monitor) = barra.primary_monitor()? {
        let escala = monitor.scale_factor();
        let tam = monitor.size().to_logical::<f64>(escala);
        let origen = monitor.position().to_logical::<f64>(escala);
        let _ = barra.set_position(tauri::LogicalPosition::new(
            origen.x + (tam.width - 620.0) / 2.0,
            origen.y + tam.height - 76.0 - 90.0,
        ));
    }
    // Ojo: no se esconde con WindowEvent::Focused(false). En Windows ese evento llega en cuanto
    // el foco pasa de la ventana a su WebView2, y la barra se cerraba al instante. La página
    // misma la cierra al perder el foco de verdad (ver app.js).
    let _ = barra;

    // Cursor: posición para el orbe y clics solo sobre las zonas interactivas.
    let w = ventana.clone();
    let h = app.clone();
    std::thread::spawn(move || {
        let mut recibe_clics = false;
        loop {
            std::thread::sleep(Duration::from_millis(33));
            let (Ok(c), Ok(p), Ok(escala)) = (w.cursor_position(), w.outer_position(), w.scale_factor()) else {
                continue;
            };
            let x = (c.x - p.x as f64) / escala;
            let y = (c.y - p.y as f64) / escala;
            let _ = w.emit("cursor", serde_json::json!({ "x": x, "y": y }));
            let dentro = h
                .state::<Zonas>()
                .0
                .lock()
                .map(|z| z.iter().any(|r| x >= r[0] && x <= r[0] + r[2] && y >= r[1] && y <= r[1] + r[3]))
                .unwrap_or(false);
            if dentro != recibe_clics {
                recibe_clics = dentro;
                let _ = w.set_ignore_cursor_events(!dentro);
            }
        }
    });
    Ok(())
}

fn main() {
    let abrir = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyA);
    let hablar = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space);
    // Alt Gr + Q (= Ctrl+Alt+Q) lo pidió Aroon sabiendo que en el teclado latinoamericano es la @:
    // mientras NOVA esté abierta no se puede escribir @. Ctrl+Shift+Q queda de respaldo.
    let esconder = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyQ);
    let esconder2 = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyQ);

    let app = tauri::Builder::default()
        .manage(Zonas(Mutex::new(Vec::new())))
        .manage(AnclaBarra(Mutex::new(None)))
        .manage(Nucleo(Mutex::new(None)))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, atajo, evento| {
                    if atajo == &abrir && evento.state() == ShortcutState::Pressed {
                        alternar_barra(app);
                    } else if (atajo == &esconder || atajo == &esconder2) && evento.state() == ShortcutState::Pressed {
                        alternar_orbe(app);
                    } else if atajo == &hablar {
                        let activo = evento.state() == ShortcutState::Pressed;
                        let _ = app.emit("hablar", serde_json::json!({ "activo": activo }));
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![zonas, anclar_barra, abrir_barra, cerrar_barra, desplazar])
        .setup(move |app| {
            let handle = app.handle().clone();
            arrancar_nucleo(&handle);
            if !nucleo_responde() {
                anotar("El núcleo de NOVA no respondió en 40 s; el orbe se quedará vacío.");
            }
            crear_ventana(&handle)?;
            anotar("NOVA de escritorio abierta.");

            if let Err(e) = app.global_shortcut().register(abrir) {
                anotar(&format!("No pude registrar Ctrl+Alt+A (¿otra app lo usa?): {e}"));
            }
            if let Err(e) = app.global_shortcut().register(hablar) {
                anotar(&format!("No pude registrar Ctrl+Alt+Espacio (¿otra app lo usa?): {e}"));
            }
            if let Err(e) = app.global_shortcut().register(esconder) {
                anotar(&format!("No pude registrar Alt Gr+Q (¿otra app lo usa?): {e}"));
            }
            if let Err(e) = app.global_shortcut().register(esconder2) {
                anotar(&format!("No pude registrar Ctrl+Shift+Q (¿otra app lo usa?): {e}"));
            }

            let escribir = MenuItem::with_id(app, "escribir", "Escribirle (Ctrl+Alt+A)", true, None::<&str>)?;
            let mostrar = MenuItem::with_id(app, "mostrar", "Esconder o sacar el orbe (Alt Gr+Q)", true, None::<&str>)?;
            let salir = MenuItem::with_id(app, "salir", "Salir", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&escribir, &mostrar, &salir])?;
            let mut bandeja = TrayIconBuilder::with_id("nova").tooltip("NOVA").menu(&menu);
            if let Some(icono) = app.default_window_icon() {
                bandeja = bandeja.icon(icono.clone());
            }
            bandeja
                .show_menu_on_left_click(false)
                // Clic izquierdo en el icono: la barra para escribirle. Clic derecho: el menú.
                .on_tray_icon_event(|icono, evento| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = evento {
                        alternar_barra(icono.app_handle());
                    }
                })
                .on_menu_event(|app, evento| match evento.id.as_ref() {
                    "salir" => app.exit(0),
                    "escribir" => mostrar_barra(app, true),
                    "mostrar" => alternar_orbe(app),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("no se pudo iniciar NOVA");

    app.run(|app, evento| {
        if let RunEvent::Exit = evento {
            detener_nucleo(app);
        }
    });
}
