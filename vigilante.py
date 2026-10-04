#!/usr/bin/env python3
"""
Vigilante de stock: productos Pokémon 30 aniversario en Carrefour y Toys R Us.
Se ejecuta en GitHub Actions cada pocos minutos y avisa por email cuando
algo pasa a estar disponible, abre reserva o aparece un producto nuevo.
"""

import base64
import hashlib
import json
import os
import random
import re
import smtplib
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.utils import formatdate
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from playwright.sync_api import sync_playwright

# ============================================================
#  CONFIGURACIÓN  (esta es la parte que puedes editar)
# ============================================================

# Páginas de búsqueda o categoría donde se buscan productos nuevos.
PAGINAS_BUSQUEDA = [
    "https://www.carrefour.es/?q=pokemon+30+aniversario",
    "https://www.carrefour.es/juguetes/juegos-tradicionales/juegos-educativos-pokemon/F-102uZ1a42/c",
    "https://www.toysrus.es/search?text=pokemon+30+aniversario",
    "https://www.toysrus.es/Cartas-Pokemon/c/cartas-pokemon",
]

# Productos que se vigilan siempre, aparezcan o no en las búsquedas.
# Para añadir uno, pega su enlace entre comillas y termina la línea con una coma.
PRODUCTOS_FIJOS = [
    # Carrefour
    "https://www.carrefour.es/pokemon-caja-elite-30th-aniversario-ingles-juego-de-mesa-6-anos-unboxing/VC4A-34535246/p",
    "https://www.carrefour.es/pokemon-30th-aniversario-caja-ex-version-sylveon-greninja-ingles-6-anos-unboxing/VC4A-34535258/p",
    "https://www.carrefour.es/pokemon-30th-aniversario-caja-ex-version-sylveon-greninja-6-anos-unboxing/VC4A-34535250/p",
    "https://www.carrefour.es/pokemon-30th-aniversario-lata-ex-version-sylveon-greninja-6-anos-unboxing/VC4A-34535241/p",
    "https://www.carrefour.es/pokemon-caja-30th-aniversario-caja-coleccion-con-figura-mewtwo-6-anos/VC4A-34535249/p",
    "https://www.carrefour.es/pokemon-30th-aniversario-pack-2-sobres-con-moneda-6-anos/VC4A-34535251/p",
    "https://www.carrefour.es/pokemon-caja-30th-aniversario-coleccion-pegatinas-especiales-6-anos-unboxing/VC4A-34535257/p",
    "https://www.carrefour.es/pokemon-pack-4-figuras-30-aniversario-4-anos/VC4A-34428374/p",
    # Toys R Us
    "https://www.toysrus.es/Pok%C3%A9mon-30%C2%BA-Aniversario-Caja-de-Entrenador-%C3%89lite-(Espa%C3%B1ol)/p/K1108947",
    "https://www.toysrus.es/Pok%C3%A9mon-30%C2%BA-Aniversario-Lote-6-Sobres-de-Mejora-(Espa%C3%B1ol)/p/K1108954",
    "https://www.toysrus.es/Pok%C3%A9mon-30%C2%BA-Aniversario-Colecci%C3%B3n-con-P%C3%B3ster-(Espa%C3%B1ol)/p/K1108964",
    "https://www.toysrus.es/Pok%C3%A9mon-30%C2%BA-Aniversario-Colecci%C3%B3n-con-Pegatinas-Especiales-(Espa%C3%B1ol)-Varios-modelos/p/K1108950",
]

# Un producto encontrado en las búsquedas solo se vigila si su nombre
# (sin tildes y en minúsculas) encaja con este patrón.
PATRON_30_ANIVERSARIO = re.compile(
    r"30\s*(th|o|°)?\s*(aniversario|anniversary|celebration|celebracion)"
)

# Si el nombre contiene alguna de estas palabras, se ignora.
# Ejemplo: EXCLUIR = ["figura", "peluche", "album"]
EXCLUIR = []

# Palabras que marcan el aviso como prioritario (⭐ en el asunto del email).
PRIORIDAD = ["ultra premium"]

# Avisar también cuando aparece un producto nuevo (aunque esté agotado).
AVISAR_PRODUCTOS_NUEVOS = True

# ============================================================
#  A partir de aquí no hace falta tocar nada
# ============================================================

CARPETA = Path(__file__).parent
ARCHIVO_ESTADO = CARPETA / "estado.enc"          # estado cifrado
ARCHIVO_ESTADO_ANTIGUO = CARPETA / "estado.json"  # versión antigua sin cifrar (se borra)
ARCHIVO_PANEL = CARPETA / "docs" / "index.html"
PRUEBA = os.environ.get("PRUEBA", "false").strip().lower() == "true"
CONTRASENA = os.environ.get("PANEL_PASSWORD") or ""
ITERACIONES = 600_000
ZONA = ZoneInfo("Europe/Madrid")

PATRONES_PRODUCTO = {
    "carrefour": re.compile(r"^https?://www\.carrefour\.es/[^?#]+/([A-Z0-9]+-\d+)/p/?$"),
    "toysrus": re.compile(r"^https?://www\.toysrus\.es/[^?#]+/p/([A-Za-z0-9]+)/?$"),
}
NOMBRES_TIENDA = {"carrefour": "Carrefour", "toysrus": "Toys R Us"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

RE_BLOQUEO = re.compile(
    r"access denied|acceso denegado|are you a robot|eres un robot|request unsuccessful|"
    r"pardon our interruption|just a moment|attention required|incapsula|verify you are human",
    re.I,
)
RE_COMPRAR = re.compile(
    r"añadir (al carrito|a la cesta|a cesta)|anadir al carrito|agregar al carrito|"
    r"^comprar( ya| ahora)?$|add to (cart|basket)"
)
RE_RESERVAR = re.compile(r"^reservar?( ahora| ya)?$|preventa|pre-?order")
RE_AGOTADO = re.compile(
    r"agotado|sin stock|fuera de stock|no disponible online|próximamente|proximamente|"
    r"lanzamiento el|avísame|avisame",
    re.I,
)

# Botones visibles y activos en la parte alta de la ficha (para no confundirse
# con los botones de "productos relacionados" que suelen estar más abajo).
JS_BOTONES = """() => Array.from(document.querySelectorAll('button, a, input[type=submit], [role=button]'))
  .filter(el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && (r.top + window.scrollY) < 1300
      && s.visibility !== 'hidden' && s.display !== 'none'
      && !el.disabled && el.getAttribute('aria-disabled') !== 'true'
      && !/disabled/i.test(String(el.className));
  })
  .map(el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase())
  .filter(t => t && t.length < 60)"""

ETIQUETAS = {
    "disponible": "🟢 DISPONIBLE",
    "reserva": "🟡 RESERVA ABIERTA",
    "agotado": "🔴 Agotado / no disponible",
    "desconocido": "⚪ No se pudo comprobar",
}


# ---------------------------- utilidades ----------------------------

def normalizar(texto):
    texto = unicodedata.normalize("NFKD", unquote(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", texto).strip()


def identificar(url):
    """Devuelve (clave única del producto, url limpia) o (None, url)."""
    limpia = url.split("#")[0].split("?")[0]
    for tienda, patron in PATRONES_PRODUCTO.items():
        m = patron.match(limpia)
        if m:
            return f"{tienda}:{m.group(1)}", limpia
    return None, limpia


def tienda_de(url):
    host = urlparse(url).netloc
    return "carrefour" if "carrefour" in host else "toysrus" if "toysrus" in host else host


def titulo_desde_url(url):
    partes = [p for p in urlparse(url).path.split("/") if p]
    slug = max(partes, key=len) if partes else url
    return unquote(slug).replace("-", " ")


def interesa(texto):
    t = normalizar(texto)
    if not PATRON_30_ANIVERSARIO.search(t):
        return False
    return not any(normalizar(x) in t for x in EXCLUIR)


def es_prioritario(texto):
    t = normalizar(texto)
    return any(normalizar(p) in t for p in PRIORIDAD)


def pausa():
    time.sleep(random.uniform(2, 5))


def _clave(salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERACIONES)
    return kdf.derive(CONTRASENA.encode("utf-8"))


def cifrar(texto):
    salt, iv = os.urandom(16), os.urandom(12)
    datos = AESGCM(_clave(salt)).encrypt(iv, texto.encode("utf-8"), None)
    return base64.b64encode(salt + iv + datos).decode("ascii")


def descifrar(b64):
    raw = base64.b64decode(b64)
    salt, iv, datos = raw[:16], raw[16:28], raw[28:]
    return AESGCM(_clave(salt)).decrypt(iv, datos, None).decode("utf-8")


def _vacio():
    return {"productos": {}, "sitios": {}}


def cargar_estado():
    datos = None
    if ARCHIVO_ESTADO.exists():
        try:
            datos = json.loads(descifrar(ARCHIVO_ESTADO.read_text().strip()))
        except Exception:
            print("!! No se pudo descifrar estado.enc (¿cambiaste PANEL_PASSWORD?). Empiezo de cero.")
    elif ARCHIVO_ESTADO_ANTIGUO.exists():
        try:
            datos = json.loads(ARCHIVO_ESTADO_ANTIGUO.read_text(encoding="utf-8"))
        except Exception:
            pass
    datos = datos or _vacio()
    datos.setdefault("productos", {})
    datos.setdefault("sitios", {})
    return datos


def _serializar(estado):
    return json.dumps(estado, ensure_ascii=False, sort_keys=True)


def guardar_estado(estado, original):
    # Solo se reescribe si algo ha cambiado (el cifrado cambia cada vez y si no
    # haríamos un commit en cada ejecución).
    if not ARCHIVO_ESTADO.exists() or _serializar(estado) != original:
        ARCHIVO_ESTADO.write_text(cifrar(_serializar(estado)) + "\n")
    if ARCHIVO_ESTADO_ANTIGUO.exists():
        ARCHIVO_ESTADO_ANTIGUO.unlink()


# ---------------------------- navegador ----------------------------

def abrir(page, url):
    """Carga una página. Devuelve 'ok', 'bloqueado' o 'error'."""
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"   ! Error cargando la página: {e}")
        return "error"
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    codigo = resp.status if resp else 0
    try:
        cabecera = page.title() + " " + page.inner_text("body")[:3000]
    except Exception:
        cabecera = ""
    if codigo in (403, 429) or RE_BLOQUEO.search(cabecera):
        print(f"   ! La web parece bloquear el acceso (HTTP {codigo})")
        return "bloqueado"
    if codigo >= 400:
        print(f"   ! HTTP {codigo}")
        return "error"
    return "ok"


def descubrir(page, url):
    """Busca enlaces a productos del 30 aniversario en una página de búsqueda/categoría."""
    resultado = abrir(page, url)
    if resultado != "ok":
        return resultado, {}
    for _ in range(6):  # bajar para que carguen más productos
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(700)
    enlaces = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(a => [a.href, (a.innerText || a.title || a.getAttribute('aria-label') || '').trim()])",
    )
    encontrados = {}
    for href, texto in enlaces:
        clave, limpia = identificar(href)
        if not clave:
            continue
        lineas = [l.strip() for l in texto.split("\n") if len(l.strip()) > 8]
        nombre = max(lineas, key=len) if lineas else titulo_desde_url(limpia)
        if not interesa(nombre + " " + titulo_desde_url(limpia)):
            continue
        if clave not in encontrados or len(nombre) > len(encontrados[clave]["titulo"]):
            encontrados[clave] = {"url": limpia, "titulo": nombre[:150]}
    return "ok", encontrados


def leer_jsonld(page):
    """Lee disponibilidad y precio de los datos estructurados de la ficha, si los hay."""
    try:
        bloques = page.eval_on_selector_all(
            'script[type="application/ld+json"]', "els => els.map(e => e.textContent)"
        )
    except Exception:
        return None, None
    hallado = {"availability": None, "price": None}

    def recorrer(nodo):
        if isinstance(nodo, list):
            for x in nodo:
                recorrer(x)
        elif isinstance(nodo, dict):
            for campo in hallado:
                if campo in nodo and hallado[campo] is None:
                    hallado[campo] = str(nodo[campo])
            for v in nodo.values():
                recorrer(v)

    for b in bloques:
        try:
            recorrer(json.loads(b))
        except Exception:
            pass
    disp = hallado["availability"]
    if disp:
        d = disp.lower()
        if "preorder" in d or "presale" in d:
            disp = "reserva"
        elif any(x in d for x in ("instock", "limitedavailability", "onlineonly")):
            disp = "disponible"
        else:
            disp = "agotado"
    return disp, hallado["price"]


def comprobar_producto(page, url):
    r = abrir(page, url)
    if r != "ok":
        return {"estado": r}

    titulo = ""
    try:
        titulo = page.inner_text("h1", timeout=3000).strip()
    except Exception:
        pass
    if not titulo:
        try:
            titulo = (page.get_attribute('meta[property="og:title"]', "content", timeout=2000) or "").strip()
        except Exception:
            pass

    disp_jsonld, precio = leer_jsonld(page)
    try:
        botones = page.evaluate(JS_BOTONES)
    except Exception:
        botones = []
    try:
        texto = page.inner_text("body")
    except Exception:
        texto = ""

    hay_comprar = any(RE_COMPRAR.search(b) for b in botones)
    hay_reservar = any(RE_RESERVAR.search(b) for b in botones)
    pos = texto.find(titulo[:30]) if titulo else -1
    zona = texto[pos:pos + 2500] if pos >= 0 else texto[:4000]
    hay_agotado = bool(RE_AGOTADO.search(zona))

    if hay_comprar:
        estado = "disponible"
    elif hay_reservar:
        estado = "reserva"
    elif disp_jsonld in ("disponible", "reserva") and not hay_agotado:
        estado = disp_jsonld
    elif hay_agotado or disp_jsonld == "agotado":
        estado = "agotado"
    else:
        estado = "agotado" if botones else "desconocido"

    if not precio:
        m = re.search(r"(\d{1,4}[.,]\d{2})\s?€", zona)
        precio = m.group(1) if m else None
    if precio:
        precio = precio.replace(".", ",") + " €" if "€" not in precio else precio

    vendedor = None
    m = re.search(r"Vendido por\s*:?\s*([^\n]{2,50})", zona)
    if m:
        vendedor = m.group(1).strip()

    return {"estado": estado, "titulo": titulo[:150], "precio": precio, "vendedor": vendedor}


# ---------------------------- email ----------------------------

def html_producto(p):
    extra = []
    if p.get("precio"):
        extra.append(escape(p["precio"]))
    if p.get("vendedor"):
        v = p["vendedor"]
        aviso = "" if normalizar(v).startswith(("carrefour", "toys")) else " ⚠️ vendedor externo"
        extra.append("vendido por " + escape(v) + aviso)
    extra_txt = f" — {' · '.join(extra)}" if extra else ""
    tienda = NOMBRES_TIENDA.get(p["tienda"], p["tienda"])
    estrella = "⭐ " if es_prioritario(p["titulo"]) else ""
    return (f'<li style="margin-bottom:8px"><b>{ETIQUETAS.get(p["estado"], p["estado"])}</b> · '
            f'{escape(tienda)}<br>{estrella}<a href="{escape(p["url"])}">{escape(p["titulo"])}</a>{extra_txt}</li>')


def enviar_email(asunto, cuerpo_html):
    usuario = os.environ.get("EMAIL_USER") or ""
    clave = (os.environ.get("EMAIL_PASSWORD") or "").replace(" ", "")
    destino = os.environ.get("EMAIL_TO") or usuario
    if not usuario or not clave:
        print("!! Faltan los secretos EMAIL_USER / EMAIL_PASSWORD. Email no enviado:")
        print("   " + asunto)
        return False
    html = f'<div style="font-family:Arial,sans-serif;font-size:14px">{cuerpo_html}</div>'
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = asunto
    msg["From"] = usuario
    msg["To"] = destino
    msg["Date"] = formatdate(localtime=True)
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    puerto = int(os.environ.get("SMTP_PORT") or 465)
    with smtplib.SMTP_SSL(host, puerto, timeout=30) as s:
        s.login(usuario, clave)
        s.sendmail(usuario, [d.strip() for d in destino.split(",") if d.strip()], msg.as_string())
    print(f"Email enviado: {asunto}")
    return True


def lista_productos(estado, claves=None):
    prods = []
    for clave, p in estado["productos"].items():
        if claves is None or clave in claves:
            prods.append({**p, "tienda": clave.split(":")[0]})
    orden = {"disponible": 0, "reserva": 1, "desconocido": 2, "agotado": 3}
    prods.sort(key=lambda p: (orden.get(p["estado"], 9), p["tienda"], p["titulo"]))
    return prods


def html_sitios(estado):
    filas = []
    for tienda, st in sorted(estado["sitios"].items()):
        icono = "✅ funcionando" if st == "ok" else "❌ sin acceso (la web bloquea al vigilante)"
        filas.append(f"<li>{escape(NOMBRES_TIENDA.get(tienda, tienda))}: {icono}</li>")
    return "<ul>" + "".join(filas) + "</ul>"


# ---------------------------- panel web ----------------------------

def generar_panel(estado, forzar=False):
    """Escribe docs/index.html con los datos cifrados. Solo si hay cambios o ha pasado 1 hora."""
    datos = {
        "productos": lista_productos(estado),
        "sitios": {NOMBRES_TIENDA.get(k, k): v for k, v in estado["sitios"].items()},
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
    html = (PLANTILLA_PANEL.replace("__DATOS__", cifrar(_serializar(datos)))
            .replace("__ITERACIONES__", str(ITERACIONES)))
    ARCHIVO_PANEL.write_text(html, encoding="utf-8")
    estado["panel_huella"] = huella
    estado["panel_actualizado"] = ahora.isoformat(timespec="seconds")
    print("Panel actualizado.")


# ---------------------------- programa principal ----------------------------

def main():
    if len(CONTRASENA) < 8:
        print("!! Falta el secreto PANEL_PASSWORD (mínimo 8 caracteres). Añádelo en Settings > Secrets.")
        sys.exit(1)
    estado = cargar_estado()
    original = _serializar(estado)
    primera_vez = not estado["productos"]
    productos, sitios = estado["productos"], estado["sitios"]
    eventos = []          # (tipo, clave)
    accesos = {}          # tienda -> lista de resultados de carga
    hoy = datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        contexto = navegador.new_context(
            user_agent=UA, locale="es-ES", timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
        )
        contexto.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = contexto.new_page()

        # 1) Reunir productos: fijos + los encontrados en búsquedas + los ya conocidos
        candidatos = {}
        for url in PRODUCTOS_FIJOS:
            clave, limpia = identificar(url)
            if clave:
                candidatos[clave] = {"url": limpia, "titulo": titulo_desde_url(limpia)}
            else:
                print(f"!! Enlace no reconocido en PRODUCTOS_FIJOS: {url}")

        for url in PAGINAS_BUSQUEDA:
            print(f"Buscando en {url}")
            res, encontrados = descubrir(page, url)
            accesos.setdefault(tienda_de(url), []).append(res)
            print(f"   -> {res}, {len(encontrados)} productos del 30 aniversario")
            for k, v in encontrados.items():
                candidatos.setdefault(k, v)
            pausa()

        for k, v in productos.items():
            candidatos.setdefault(k, {"url": v["url"], "titulo": v["titulo"]})

        # 2) Comprobar cada producto
        for clave, info in candidatos.items():
            print(f"Comprobando {clave}: {info['titulo'][:70]}")
            r = comprobar_producto(page, info["url"])
            tienda = clave.split(":")[0]
            accesos.setdefault(tienda, []).append("ok" if r["estado"] not in ("bloqueado", "error") else r["estado"])
            previo = productos.get(clave)

            if r["estado"] in ("bloqueado", "error"):
                if previo is None:
                    productos[clave] = {"url": info["url"], "titulo": info["titulo"],
                                        "estado": "desconocido", "precio": None,
                                        "vendedor": None, "visto_desde": hoy}
                pausa()
                continue

            print(f"   -> {r['estado']} {r.get('precio') or ''}")
            registro = previo or {"visto_desde": hoy}
            anterior = registro.get("estado")
            registro.update(
                url=info["url"],
                titulo=r.get("titulo") or registro.get("titulo") or info["titulo"],
                estado=r["estado"], precio=r.get("precio"), vendedor=r.get("vendedor"),
            )
            productos[clave] = registro

            if not primera_vez:
                if r["estado"] in ("disponible", "reserva") and anterior not in ("disponible", "reserva"):
                    eventos.append(("stock", clave))
                elif previo is None and AVISAR_PRODUCTOS_NUEVOS:
                    eventos.append(("nuevo", clave))
            pausa()

        navegador.close()

    # 3) ¿Alguna web ha empezado (o dejado) de bloquearnos?
    for tienda, lista in accesos.items():
        ahora = "ok" if any(x == "ok" for x in lista) else "bloqueado"
        antes = sitios.get(tienda)
        if not primera_vez and antes is not None and ahora != antes:
            eventos.append(("sitio_" + ahora, tienda))
        sitios[tienda] = ahora

    # 4) Emails (si el envío falla, no se guarda el estado y se reintenta la próxima vez)
    if primera_vez or PRUEBA:
        prods = lista_productos(estado)
        disp = sum(1 for p in prods if p["estado"] in ("disponible", "reserva"))
        asunto = ("🧪 Prueba del vigilante" if PRUEBA else "✅ Vigilante Pokémon activado") + \
                 f" — {len(prods)} productos vigilados, {disp} disponibles"
        cuerpo = (
            "<p>Este es el resumen de todo lo que estoy vigilando ahora mismo. "
            "A partir de aquí solo te escribiré cuando algo cambie.</p>"
            "<p><b>Estado de las webs:</b></p>" + html_sitios(estado) +
            "<p><b>Productos:</b></p><ul>" + "".join(html_producto(p) for p in prods) + "</ul>"
            "<p style='color:#666'>Comprueba un par de productos a mano para confirmar que "
            "el estado que ves aquí coincide con la web.</p>"
        )
        enviar_email(asunto, cuerpo)

    elif eventos:
        stock = [c for t, c in eventos if t == "stock"]
        nuevos = [c for t, c in eventos if t == "nuevo"]
        partes = []
        if stock:
            prods = lista_productos(estado, set(stock))
            partes.append("<h3>¡Disponible ahora!</h3><ul>" + "".join(html_producto(p) for p in prods) + "</ul>")
            prio = "⭐ " if any(es_prioritario(p["titulo"]) for p in prods) else ""
            mas = f" (+{len(prods) - 1} más)" if len(prods) > 1 else ""
            asunto = f"{prio}🟢 ¡Stock! {prods[0]['titulo'][:60]}{mas}"
        elif nuevos:
            asunto = f"🆕 {len(nuevos)} producto(s) nuevo(s) del 30 aniversario"
        else:
            asunto = "⚠️ Vigilante Pokémon: cambio en el acceso a una web"
        if nuevos:
            prods = lista_productos(estado, set(nuevos))
            partes.append("<h3>Productos nuevos detectados</h3><ul>" + "".join(html_producto(p) for p in prods) + "</ul>")
        for t, tienda in eventos:
            nombre = NOMBRES_TIENDA.get(tienda, tienda)
            if t == "sitio_bloqueado":
                partes.append(f"<p>⚠️ {escape(nombre)} ha empezado a bloquear al vigilante. "
                              "Mientras dure, no podré avisarte de esa tienda.</p>")
            elif t == "sitio_ok":
                partes.append(f"<p>✅ {escape(nombre)} vuelve a funcionar con normalidad.</p>")
        enviar_email(asunto, "".join(partes))
    else:
        print("Sin cambios. No se envía email.")

    generar_panel(estado, forzar=PRUEBA)
    guardar_estado(estado, original)


PLANTILLA_PANEL = (CARPETA / "panel.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except smtplib.SMTPException as e:
        print(f"!! Error enviando el email: {e}")
        sys.exit(1)
