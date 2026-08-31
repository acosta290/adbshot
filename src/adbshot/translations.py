"""Translation catalogues, keyed by the English source string.

To add a language: copy the "es" dict, translate the values, and add it to
CATALOGUES under its two-letter code. Nothing else needs to change -- the new
language appears in Preferences automatically once it is listed in
i18n.LANGUAGES.

Leave a value out and that string simply falls back to English, so a partial
translation is perfectly usable.
"""

SPANISH = {
    # -- capture and devices ------------------------------------------------
    "No phone is connected. Plug it in over USB and accept the debugging prompt.":
        "No hay ningún móvil conectado. Conéctalo por USB y acepta la depuración.",
    "No phone is connected.":
        "No hay ningún móvil conectado.",
    "The phone did not respond within {seconds} s. Check the cable and that USB debugging is still authorised.":
        "El móvil no respondió en {seconds} s. Comprueba el cable y que la depuración USB sigue autorizada.",
    "adb was not found. Install it with: sudo apt install adb":
        "No se encontró «adb». Instálalo con: sudo apt install adb",
    "Could not launch adb: {error}":
        "No se pudo lanzar adb: {error}",
    "Could not run adb: {error}":
        "No se pudo ejecutar adb: {error}",
    "The phone has not authorised USB debugging. Accept the prompt on its screen.":
        "El móvil no ha autorizado la depuración USB. Acéptala en su pantalla.",
    "The phone is offline. Plug it in again.":
        "El móvil está desconectado. Vuelve a enchufarlo.",
    "adb has no permission for this device. Check your udev rules.":
        "adb no tiene permisos sobre el dispositivo. Revisa las reglas udev.",
    "adb returned no image (exit {status}).":
        "adb no devolvió una imagen (código {status}).",
    "adb returned no image: {error}":
        "adb no devolvió una imagen: {error}",
    "The phone rejected the chosen screen. Unlock it and try again.":
        "El móvil rechazó la pantalla elegida. Desbloquéalo e inténtalo de nuevo.",
    "The capture came out black. The screen is off, or the foreground app blocks screenshots (FLAG_SECURE).":
        "La captura salió en negro. La pantalla está apagada, o la app en primer plano bloquea las capturas (FLAG_SECURE).",
    "Could not process the capture: {error}":
        "No se pudo procesar la captura: {error}",
    # device states
    "ready": "listo",
    "unauthorised — accept the debugging prompt on the phone":
        "sin autorizar — acepta la depuración USB en el móvil",
    "no permissions — check your udev rules":
        "sin permisos — revisa las reglas udev",
    "offline": "desconectado",
    "authorising…": "autorizando…",
    "connecting…": "conectando…",
    "in recovery": "en modo recovery",
    "in the bootloader": "en el bootloader",
    "in sideload": "en sideload",
    "in rescue mode": "en modo rescue",

    # -- clipboard and paste ------------------------------------------------
    "wl-copy was not found. Install it with: sudo apt install wl-clipboard":
        "No se encontró «wl-copy». Instálalo con: sudo apt install wl-clipboard",
    "Could not write to the clipboard (is the screen locked?).":
        "No se pudo escribir en el portapapeles (¿pantalla bloqueada?).",
    "wl-copy exited with status {status}":
        "wl-copy terminó con código {status}",
    "Could not paste: the session is locked.":
        "No se pudo pegar: la sesión está bloqueada.",
    "Could not paste: {error}":
        "No se pudo pegar: {error}",
    "Could not paste: the compositor rejected every key.":
        "No se pudo pegar: el compositor rechazó todas las teclas.",
    "Invalid paste shortcut: {shortcut}":
        "Combinación de pegado no válida: {shortcut}",
    "available": "disponible",
    "unknown (screen locked)": "desconocido (pantalla bloqueada)",

    # -- tray menu ----------------------------------------------------------
    "Preferences…": "Preferencias…",
    "Capture now": "Capturar ahora",
    "Device: {name}": "Dispositivo: {name}",
    "Open last capture": "Abrir última captura",
    "Open folder": "Abrir carpeta",
    "Quit": "Salir",
    "Forget the remembered device": "Olvidar el dispositivo recordado",
    "No devices": "Sin dispositivos",
    "Choose a device": "Elegir dispositivo",
    "adbshot: capture the phone screen": "adbshot: capturar la pantalla del móvil",

    # -- feedback -----------------------------------------------------------
    "Capture ready": "Captura lista",
    "Could not capture": "No se pudo capturar",
    "Capturing {device}…": "Capturando {device}…",
    "Capturing…": "Capturando…",
    "on the clipboard": "en el portapapeles",
    "pasted": "pegado",
    "Open": "Abrir",

    # -- device chooser -----------------------------------------------------
    "Remember this choice": "Recordar esta elección",
    "Later captures will go to this phone without asking":
        "Las siguientes capturas irán a este móvil sin preguntar",
    "Plug the phone in over USB and accept the debugging prompt":
        "Conecta el móvil por USB y acepta la depuración",
    "Capture": "Capturar",

    # -- preferences --------------------------------------------------------
    "adbshot Preferences": "Preferencias de adbshot",
    "Capture": "Captura",
    "Files": "Archivos",
    "Behaviour": "Comportamiento",
    "Image": "Imagen",
    "Format": "Formato",
    "The format each capture is saved and copied in":
        "Formato en que se guarda y se copia la captura",
    "Lossless. Best for text and interface screenshots.":
        "Sin pérdida. Ideal para texto y capturas de interfaz.",
    "Lossy. Much smaller files.": "Con pérdida. Archivos mucho más pequeños.",
    "Lossy, and more efficient than JPEG.": "Con pérdida, y más eficiente que JPEG.",
    "Quality": "Calidad",
    "Only applies to lossy formats": "Solo se aplica a los formatos con pérdida",
    "PNG is lossless, so quality does not apply":
        "PNG no tiene pérdida, así que la calidad no se aplica",
    "Output scale": "Escala de salida",
    "Percentage of the phone's own resolution":
        "Porcentaje de la resolución original del móvil",
    "Device": "Dispositivo",
    "With several phones connected the remembered one is used; the chooser appears only when it is needed.":
        "Con varios móviles conectados se usa el recordado; el selector aparece solo cuando hace falta.",
    "Always ask which device to use": "Preguntar siempre qué dispositivo usar",
    "Remembered device": "Dispositivo recordado",
    "Forget": "Olvidar",
    "None": "Ninguno",
    "Choose a device now": "Elegir dispositivo ahora",
    "Choose…": "Elegir…",
    "Saving": "Guardado",
    "Save every capture to disk": "Guardar cada captura en disco",
    "Turn this off and the capture only reaches the clipboard":
        "Si lo desactivas, la captura solo llega al portapapeles",
    "Destination folder": "Carpeta de destino",
    "Change…": "Cambiar…",
    "Folder for captures": "Carpeta para las capturas",
    "Filename template": "Plantilla del nombre",
    "Available tokens": "Fichas disponibles",
    "Empty the folder": "Vaciar la carpeta",
    "Only captures created by adbshot are removed, and they go to the Trash. Anything else you keep in that folder is left alone.":
        "Solo se eliminan las capturas creadas por adbshot, y van a la papelera. Cualquier otro archivo que tengas ahí no se toca.",
    "adbshot captures in the folder": "Capturas de adbshot en la carpeta",
    "Empty…": "Vaciar…",
    "{count} file(s) · {size}": "{count} archivo(s) · {size}",
    " · {count} other file(s) will be kept": " · {count} archivo(s) ajeno(s) se conservarán",
    "No adbshot captures to remove": "No hay ninguna captura de adbshot que borrar",
    "Nothing to empty": "No hay nada que vaciar",
    "Empty the captures?": "¿Vaciar las capturas?",
    "{count} capture(s) ({size}) from\n{folder}\nwill be moved to the Trash.":
        "Se enviarán a la papelera {count} captura(s) ({size}) de\n{folder}.",
    "\n\nThe other {count} file(s) in the folder will not be touched.":
        "\n\nLos otros {count} archivo(s) de la carpeta no se tocarán.",
    "Cancel": "Cancelar",
    "Empty": "Vaciar",
    "{count} capture(s) moved to the Trash": "{count} captura(s) enviada(s) a la papelera",
    "{trashed} to the Trash, {failed} failed": "{trashed} a la papelera, {failed} con error",
    "Clipboard": "Portapapeles",
    "What to do after a capture": "Qué hacer tras capturar",
    "Auto-paste is experimental: it lights GNOME's screen-sharing indicator for 5 s on every capture.":
        "El pegado automático es experimental: enciende el indicador de «pantalla compartida» de GNOME durante 5 s en cada captura.",
    "Copy to the clipboard": "Copiar al portapapeles",
    "Copy and paste automatically (experimental)":
        "Copiar y pegar automáticamente (experimental)",
    "Do not use the clipboard": "No usar el portapapeles",
    "Shortcut sent when pasting": "Combinación que se envía al pegar",
    "Shortcut sent when pasting: {shortcut}": "Combinación que se envía al pegar: {shortcut}",
    "Ctrl+V works almost everywhere. Claude's CLI pastes images with Ctrl+Super+V.":
        "Ctrl+V vale para casi todo. La CLI de Claude pega imágenes con Ctrl+Super+V.",
    "Test in 3 s": "Probar en 3 s",
    "Focus the window you want to paste into; the shortcut will be sent there.":
        "Enfoca la ventana donde quieras pegar; se enviará la combinación.",
    "Sending {shortcut} in 3 s — focus the target window":
        "Enviando {shortcut} en 3 s — enfoca la ventana destino",
    "{shortcut} sent": "{shortcut} enviado",
    "Could not send: {error}": "No se pudo enviar: {error}",
    "Press the paste shortcut": "Pulsa la combinación de pegado",
    "Press the shortcut the target app uses to paste images.\nCtrl+V is the usual one; Ctrl+Super+V for Claude's CLI.\nEsc cancels.":
        "Pulsa la combinación con la que la app de destino pega imágenes.\nCtrl+V es lo habitual; Ctrl+Super+V para la CLI de Claude.\nEsc cancela.",
    "Pasting will use {shortcut}": "Se pegará con {shortcut}",
    "Auto-paste status": "Estado del pegado automático",
    "Check": "Comprobar",
    "Press “Check”": "Pulsa «Comprobar»",
    "Available": "Disponible",
    "Not available": "No disponible",
    "Global shortcut": "Atajo global",
    "gsd-media-keys applies the change immediately, with no need to log out.":
        "gsd-media-keys aplica el cambio al momento, sin cerrar sesión.",
    "Shortcut to capture": "Atajo para capturar",
    "not registered": "no registrado",
    "conflicts with {keys}": "en conflicto con {keys}",
    "Press the shortcut": "Pulsa la combinación",
    "Press the shortcut you want to use for capturing.\nEsc cancels.":
        "Pulsa el atajo que quieres usar para capturar.\nEsc cancela.",
    "Capture shortcut changed to {shortcut}": "Atajo de captura cambiado a {shortcut}",
    "Alerts": "Avisos",
    "Shutter sound when capturing": "Sonido de obturador al capturar",
    "Notification when finished": "Notificación al terminar",
    "With a thumbnail of the capture. Errors always notify.":
        "Con miniatura de la captura. Los errores avisan siempre.",
    "Language": "Idioma",
    "Interface language": "Idioma de la interfaz",
    "Takes effect immediately for the tray menu; reopen this window to apply it here.":
        "Se aplica al momento en el menú de la bandeja; vuelve a abrir esta ventana para verlo aquí.",
}

CATALOGUES = {
    "es": SPANISH,
}
