#!/usr/bin/env python3
"""
Vigilante de avisos Pokémon 30 aniversario a partir de canales públicos de Telegram.
Lee los mensajes nuevos de cada canal, se queda con los que hablan de los
productos que te interesan y te los manda por email. También genera un panel
web cifrado con contraseña con los últimos avisos.
"""

import base64
import hashlib
import json
import os
import re
import smtplib
import sys
import unicodedata
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.utils import formatdate
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ============================================================
#  CONFIGURACIÓN  (esta es la parte que puedes editar)
# ============================================================

# Canales públicos de Telegram a vigilar (lo que va detrás de t.me/).
CANALES = [
    "pokestock_es",
    "stockTCGpokemon",
]

# Productos que te interesan. Para cada uno:
#   patron:       palabras que lo identifican (sin tildes y en minúsculas)
#   requiere_30:  True si el mensaje además tiene que mencionar el 30 aniversario
#                 (útil para palabras que también salen en otros productos, como "Mew")
PRODUCTOS = {
    "Ultra Premium": {"patron": r"ultra[\s-]?premium|\bupc\b", "requiere_30": False},
    "Ditto":         {"patron": r"\bditto\b", "requiere_30": False},
    "Álbum":         {"patron": r"\balbum\b|\bbinder\b|\barchivador\b", "requiere_30": True},
    "Mew":           {"patron": r"\bmew\b", "requiere_30": True},
    "Mewtwo":        {"patron": r"\bmewtwo\b", "requiere_30": True},
    "Invitaciones Amazon": {"patron": r"invitaci", "requiere_30": True},
}

# Cómo se reconoce que un mensaje habla del 30 aniversario.
PATRON_30 = r"30\s*(th|o|°)?\s*(aniversario|anniversary|celebration|celebracion)|\b30th\b|pokemon\s?30\b"

# Avisar también de cualquier mensaje que hable del 30 aniversario, aunque no
# mencione ninguno de los productos de arriba. Esos avisos llevan esta etiqueta.
AVISAR_TODO_30 = True
ETIQUETA_GENERAL = "Otros 30 aniversario"

# Si un mensaje contiene alguna de estas palabras, se ignora. Ej: ["vendo", "cardmarket"]
EXCLUIR = []

# Productos que se marcan con ⭐ en el asunto del email.
PRIORIDAD = ["Ultra Premium"]

# Cuántos avisos guardar para el panel.
MAX_AVISOS = 100

# ============================================================
#  A partir de aquí no hace falta tocar nada
# ============================================================

CARPETA = Path(__file__).parent
ARCHIVO_ESTADO = CARPETA / ".estado" / "estado.enc"   # se guarda en la caché de GitHub, no en el repo
ARCHIVO_PANEL = CARPETA / "docs" / "index.html"
PLANTILLA_PANEL = CARPETA / "panel.html"
PRUEBA = os.environ.get("PRUEBA", "false").strip().lower() == "true"
NO_GUARDAR = os.environ.get("NO_GUARDAR", "false").strip().lower() == "true"
CONTRASENA = os.environ.get("PANEL_PASSWORD") or ""
ITERACIONES = 600_000
ZONA = ZoneInfo("Europe/Madrid")
CABECERAS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}

RE_30 = re.compile(PATRON_30)
RE_PRODUCTOS = {nombre: (re.compile(cfg["patron"]), cfg["requiere_30"]) for nombre, cfg in PRODUCTOS.items()}


# ---------------------------- utilidades ----------------------------

def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower().replace("-", " ").replace("_", " ").replace("%20", " ")
    return re.sub(r"\s+", " ", texto)


def clasificar(texto):
    """Devuelve la lista de productos que menciona el mensaje (vacía si no interesa)."""
    t = normalizar(texto)
    if any(normalizar(x) in t for x in EXCLUIR):
        return []
    habla_30 = bool(RE_30.search(t))
    encontrados = [nombre for nombre, (patron, req30) in RE_PRODUCTOS.items()
                   if patron.search(t) and (habla_30 or not req30)]
    if not encontrados and habla_30 and AVISAR_TODO_30:
        encontrados = [ETIQUETA_GENERAL]
    return encontrados


# ---------------------------- cifrado y estado ----------------------------

def _clave(salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERACIONES)
    return kdf.derive(CONTRASENA.encode("utf-8"))


def cifrar(texto):
    salt, iv = os.urandom(16), os.urandom(12)
    datos = AESGCM(_clave(salt)).encrypt(iv, texto.encode("utf-8"), None)
    return base64.b64encode(salt + iv + datos).decode("ascii")


def descifrar(b64):
    raw = base64.b64decode(b64)
    return AESGCM(_clave(raw[:16])).decrypt(raw[16:28], raw[28:], None).decode("utf-8")


def _serializar(datos):
    return json.dumps(datos, ensure_ascii=False, sort_keys=True)


def cargar_estado():
    datos = None
    if ARCHIVO_ESTADO.exists():
        try:
            datos = json.loads(descifrar(ARCHIVO_ESTADO.read_text().strip()))
        except Exception:
            print("!! No se pudo leer el estado guardado. Empiezo de cero.")
    datos = datos or {}
    datos.setdefault("canales", {})
    datos.setdefault("avisos", [])
    return datos


def guardar_estado(estado):
    ARCHIVO_ESTADO.parent.mkdir(exist_ok=True)
    ARCHIVO_ESTADO.write_text(cifrar(_serializar(estado)) + "\n")


# ---------------------------- Telegram ----------------------------

def leer_pagina(canal, antes=None):
    url = f"https://t.me/s/{canal}" + (f"?before={antes}" if antes else "")
    r = requests.get(url, headers=CABECERAS, timeout=20, allow_redirects=False)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    sopa = BeautifulSoup(r.text, "html.parser")
    mensajes = []
    for m in sopa.select("div.tgme_widget_message[data-post]"):
        if "service_message" in (m.get("class") or []):
            continue
        try:
            num = int(m["data-post"].split("/")[-1])
        except ValueError:
            continue
        cuerpo = m.select_one(".tgme_widget_message_text")
        texto = cuerpo.get_text("\n").strip() if cuerpo else ""
        enlaces = [a["href"] for a in (cuerpo.select("a[href]") if cuerpo else [])
                   if a["href"].startswith("http")]
        previa = m.select_one("a.tgme_widget_message_link_preview")
        titulo_previa = ""
        if previa:
            if previa.get("href"):
                enlaces.append(previa["href"])
            partes = [e.get_text(" ").strip() for e in previa.select(
                ".link_preview_site_name, .link_preview_title, .link_preview_description")]
            titulo_previa = " · ".join(p for p in partes if p)
        hora = m.select_one("time[datetime]")
        mensajes.append({
            "id": f"{canal}/{num}",
            "num": num,
            "canal": canal,
            "fecha": hora["datetime"] if hora else None,
            "texto": texto,
            "previa": titulo_previa,
            "enlaces": list(dict.fromkeys(enlaces))[:5],
            "url": f"https://t.me/{canal}/{num}",
        })
    if not mensajes and "tgme_channel_info" not in r.text:
        raise RuntimeError("el canal no tiene vista web pública")
    return mensajes


def mensajes_nuevos(canal, ultimo):
    """Mensajes posteriores a 'ultimo' (o los de la primera página si es la primera vez)."""
    todos = {m["num"]: m for m in leer_pagina(canal)}
    paginas = 1
    while ultimo is not None and todos and min(todos) > ultimo + 1 and paginas < 4:
        anteriores = leer_pagina(canal, antes=min(todos))
        if not anteriores:
            break
        todos.update({m["num"]: m for m in anteriores})
        paginas += 1
    return [todos[n] for n in sorted(todos) if ultimo is None or n > ultimo]


# ---------------------------- email ----------------------------

def fecha_bonita(iso):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(ZONA).strftime("%d/%m %H:%M")
    except ValueError:
        return iso


def html_aviso(a):
    texto = escape(a["texto"][:700]).replace("\n", "<br>")
    previa = f'<div style="color:#555;margin-top:4px">{escape(a["previa"][:300])}</div>' if a["previa"] else ""
    enlaces = "".join(f'<li><a href="{escape(u)}">{escape(u[:90])}</a></li>' for u in a["enlaces"])
    enlaces = f'<ul style="margin:6px 0 0">{enlaces}</ul>' if enlaces else ""
    etiquetas = ", ".join(a["productos"])
    return (f'<div style="border-left:4px solid #FFCB05;padding:8px 12px;margin:0 0 16px">'
            f'<b>{escape(etiquetas)}</b> · <a href="{a["url"]}">@{escape(a["canal"])}</a> · {fecha_bonita(a["fecha"])}'
            f'<div style="margin-top:6px">{texto}</div>{previa}{enlaces}</div>')


def enviar_email(asunto, cuerpo_html):
    usuario = os.environ.get("EMAIL_USER") or ""
    clave = (os.environ.get("EMAIL_PASSWORD") or "").replace(" ", "")
    destino = os.environ.get("EMAIL_TO") or usuario
    if not usuario or not clave:
        print(f"(Sin datos de email, no se envía) {asunto}")
        return
    msg = MIMEText(f'<div style="font-family:Arial,sans-serif;font-size:14px">{cuerpo_html}</div>', "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, destino
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST") or "smtp.gmail.com",
                          int(os.environ.get("SMTP_PORT") or 465), timeout=30) as s:
        s.login(usuario, clave)
        s.sendmail(usuario, [d.strip() for d in destino.split(",") if d.strip()], msg.as_string())
    print(f"Email enviado: {asunto}")


# ---------------------------- panel ----------------------------

def generar_panel(estado, forzar=False):
    datos = {
        "avisos": estado["avisos"],
        "canales": {c: v.get("estado", "ok") for c, v in estado["canales"].items()},
        "productos": list(PRODUCTOS) + ([ETIQUETA_GENERAL] if AVISAR_TODO_30 else []),
        "prioridad": PRIORIDAD,
    }
    huella = hashlib.sha256(_serializar(datos).encode("utf-8")).hexdigest()
    ahora = datetime.now(ZONA)
    ultima = estado.get("panel_actualizado")
    viejo = not ultima or ahora - datetime.fromisoformat(ultima) > timedelta(minutes=60)
    if not (forzar or viejo or huella != estado.get("panel_huella") or not ARCHIVO_PANEL.exists()):
        return
    datos["actualizado"] = ahora.isoformat(timespec="minutes")
    ARCHIVO_PANEL.parent.mkdir(exist_ok=True)
    html = (PLANTILLA_PANEL.read_text(encoding="utf-8")
            .replace("__DATOS__", cifrar(_serializar(datos)))
            .replace("__ITERACIONES__", str(ITERACIONES)))
    ARCHIVO_PANEL.write_text(html, encoding="utf-8")
    estado["panel_huella"] = huella
    estado["panel_actualizado"] = ahora.isoformat(timespec="seconds")
    print("Panel actualizado.")


# ---------------------------- programa principal ----------------------------

def main():
    if len(CONTRASENA) < 8:
        print("!! Falta el secreto PANEL_PASSWORD (mínimo 8 caracteres).")
        sys.exit(1)

    estado = cargar_estado()
    primera_vez = not estado["canales"]
    nuevos_avisos, cambios_canal = [], []
    ya_vistos = {a["id"] for a in estado["avisos"]}

    for canal in CANALES:
        info = estado["canales"].setdefault(canal, {})
        antes = info.get("estado")
        print(f"Leyendo @{canal}…")
        try:
            mensajes = mensajes_nuevos(canal, info.get("ultimo_id"))
        except Exception as e:
            print(f"   ! No se pudo leer: {e}")
            info["estado"] = "sin_acceso"
            if antes == "ok":
                cambios_canal.append((canal, "sin_acceso"))
            continue
        info["estado"] = "ok"
        if antes == "sin_acceso":
            cambios_canal.append((canal, "ok"))

        primera_lectura = info.get("ultimo_id") is None
        coincidencias = 0
        for m in mensajes:
            productos = clasificar(" ".join([m["texto"], m["previa"]] + m["enlaces"]))
            if productos and m["id"] not in ya_vistos:
                aviso = {k: m[k] for k in ("id", "canal", "fecha", "texto", "previa", "enlaces", "url")}
                aviso["productos"] = productos
                estado["avisos"].insert(0, aviso)
                ya_vistos.add(m["id"])
                coincidencias += 1
                if not primera_lectura:
                    nuevos_avisos.append(aviso)
        if mensajes:
            info["ultimo_id"] = max(m["num"] for m in mensajes)
        print(f"   -> {len(mensajes)} mensajes {'recientes' if primera_lectura else 'nuevos'}, {coincidencias} interesantes")

    estado["avisos"].sort(key=lambda a: a.get("fecha") or "", reverse=True)
    del estado["avisos"][MAX_AVISOS:]

    # Emails
    if primera_vez or PRUEBA:
        canales_html = "".join(
            f"<li>@{escape(c)}: {'✅ leyendo bien' if v.get('estado') == 'ok' else '❌ no se puede leer'}</li>"
            for c, v in estado["canales"].items())
        recientes = estado["avisos"][:10]
        cuerpo = ("<p>El vigilante de Telegram está en marcha. A partir de ahora te escribiré "
                  "solo cuando aparezca un mensaje nuevo sobre tus productos.</p>"
                  f"<p><b>Canales:</b></p><ul>{canales_html}</ul>"
                  f"<p><b>Productos que vigilo:</b> {escape(', '.join(PRODUCTOS))}"
                  f"{' y cualquier mensaje sobre el 30 aniversario' if AVISAR_TODO_30 else ''}</p>"
                  "<p><b>Últimos mensajes que habrían pasado el filtro</b> (para que veas cómo filtra):</p>"
                  + ("".join(html_aviso(a) for a in recientes) or "<p>Ninguno en los mensajes recientes.</p>"))
        asunto = ("🧪 Prueba del vigilante de Telegram" if PRUEBA else "✅ Vigilante de Telegram activado")
        enviar_email(asunto, cuerpo)
    elif nuevos_avisos:
        productos = list(dict.fromkeys(p for a in nuevos_avisos for p in a["productos"]))
        estrella = "⭐ " if any(p in PRIORIDAD for p in productos) else ""
        primero = nuevos_avisos[0]
        resumen = re.sub(r"https?://\S+", "", primero["texto"]).strip() or primero["previa"]
        resumen = re.sub(r"\s+", " ", resumen)[:60]
        asunto = f"{estrella}🔔 {', '.join(productos)}: {resumen}"
        enviar_email(asunto, "".join(html_aviso(a) for a in nuevos_avisos))

    for canal, nuevo in cambios_canal:
        if nuevo == "sin_acceso":
            enviar_email(f"⚠️ Vigilante: no puedo leer @{canal}",
                         f"<p>El canal @{escape(canal)} ha dejado de poder leerse (puede que haya "
                         "desactivado la vista web o cambiado de nombre). Mientras tanto no recibirás sus avisos.</p>")
        else:
            enviar_email(f"✅ Vigilante: @{canal} vuelve a funcionar", "<p>Todo en orden de nuevo.</p>")

    print(f"\n{len(estado['avisos'])} avisos guardados; {len(nuevos_avisos)} nuevos en esta ejecución.")
    if NO_GUARDAR:
        print("(NO_GUARDAR activado: no se modifica nada)")
        for a in estado["avisos"][:10]:
            linea = re.sub(r"\s+", " ", a["texto"])[:90]
            etiquetas = ", ".join(a["productos"])
            print(f"  [{etiquetas}] @{a['canal']} {fecha_bonita(a['fecha'])}: {linea}")
        return
    generar_panel(estado, forzar=PRUEBA)
    guardar_estado(estado)


if __name__ == "__main__":
    try:
        main()
    except smtplib.SMTPException as e:
        print(f"!! Error enviando el email: {e}")
        sys.exit(1)
