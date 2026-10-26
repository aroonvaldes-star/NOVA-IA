// Envoltorio de escritorio de NOVA.
//
// Abre una ventana transparente, sin marco y siempre encima, del tamaño de la
// pantalla, donde vive el orbe. La ventana deja pasar los clics al escritorio
// salvo sobre las zonas que la interfaz reporta (la barra de texto y las
// tarjetas). También:
//   * arranca el núcleo de NOVA (Python) si no está corriendo y lo cierra al salir
//   * manda la posición del cursor al orbe para que se aparte
//   * registra los atajos globales: Ctrl+Alt+A (abrir) y Ctrl+Alt+Espacio (hablar)
//   * pone el icono en la bandeja del sistema

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const PUERTO: u16 = 8190;

struct Zonas(Mutex<Vec<[f64; 4]>>);
struct Nucleo(Mutex<Option<Child>>);

#[tauri::command]
fn zonas(estado: tauri::State<'_, Zonas>, zonas: Vec<[f64; 4]>) {
    if let Ok(mut z) = estado.0.lock() {
        *z = zonas;
    }
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
        Err(e) => eprintln!("No se pudo arrancar el núcleo de NOVA: {e}"),
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

    let app = tauri::Builder::default()
        .manage(Zonas(Mutex::new(Vec::new())))
        .manage(Nucleo(Mutex::new(None)))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, atajo, evento| {
                    if atajo == &abrir && evento.state() == ShortcutState::Pressed {
                        if let Some(w) = app.get_webview_window("orbe") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                        let _ = app.emit("abrir", ());
                    } else if atajo == &hablar {
                        let activo = evento.state() == ShortcutState::Pressed;
                        let _ = app.emit("hablar", serde_json::json!({ "activo": activo }));
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![zonas])
        .setup(move |app| {
            let handle = app.handle().clone();
            arrancar_nucleo(&handle);
            crear_ventana(&handle)?;

            if let Err(e) = app.global_shortcut().register(abrir) {
                eprintln!("No pude registrar Ctrl+Alt+A: {e}");
            }
            if let Err(e) = app.global_shortcut().register(hablar) {
                eprintln!("No pude registrar Ctrl+Alt+Espacio: {e}");
            }

            let mostrar = MenuItem::with_id(app, "mostrar", "Mostrar u ocultar a NOVA", true, None::<&str>)?;
            let salir = MenuItem::with_id(app, "salir", "Salir", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&mostrar, &salir])?;
            let mut bandeja = TrayIconBuilder::with_id("nova").tooltip("NOVA").menu(&menu);
            if let Some(icono) = app.default_window_icon() {
                bandeja = bandeja.icon(icono.clone());
            }
            bandeja
                .on_menu_event(|app, evento| match evento.id.as_ref() {
                    "salir" => app.exit(0),
                    "mostrar" => {
                        if let Some(w) = app.get_webview_window("orbe") {
                            if w.is_visible().unwrap_or(false) {
                                let _ = w.hide();
                            } else {
                                let _ = w.show();
                            }
                        }
                    }
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
