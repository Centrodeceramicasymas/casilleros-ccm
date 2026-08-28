import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import hashlib
import math
import os
import random
import string
import textwrap
from datetime import datetime, timezone, timedelta
import io
import urllib.parse
from functools import lru_cache
from pathlib import Path
import base64
import hmac
import json
import html

import requests

st.set_page_config(
    page_title="Centro de Cerámicas y Más — Casillero & Catálogo China",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Cliente AliExpress inlined: Streamlit Cloud a veces no incluye módulos extra.
APP_KEY_DEFAULT = "544082"
GATEWAY_DEFAULT = "https://api-sg.aliexpress.com/sync"
TIMEOUT_S = 25
PAGE_SIZE = 20
TZ_CN = timezone(timedelta(hours=8))

ORDEN_API = {
    "Más vendidos": "LAST_VOLUME_DESC",
    "Mejor precio": "SALE_PRICE_ASC",
    "Calificación": "EVALUATE_RATE_DESC",
}

MENSAJES_ERROR = {
    "7": "Se alcanzó el límite de peticiones de AliExpress. Intente de nuevo en unos minutos.",
    "11": "La aplicación no tiene permiso para este método de AliExpress.",
    "15": "El servicio remoto de AliExpress no está disponible en este momento.",
    "25": "La firma de la API no es válida. Verifique ALIEXPRESS_APP_SECRET.",
    "27": "Falta el parámetro de sesión (autorización Dropshipper).",
    "29": "La App Key de AliExpress no es válida.",
    "40": "Falta un parámetro obligatorio en la consulta a AliExpress.",
    "41": "Un parámetro de la consulta a AliExpress no es válido.",
}


class AliExpressError(Exception):
    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


def _leer_secrets_aliexpress():
    try:
        import streamlit as st

        seccion = st.secrets.get("aliexpress", {})
        if hasattr(seccion, "to_dict"):
            return dict(seccion)
        return dict(seccion) if seccion else {}
    except Exception:
        return {}


def credenciales_aliexpress():
    secrets = _leer_secrets_aliexpress()
    app_key = (
        os.environ.get("ALIEXPRESS_APP_KEY")
        or str(secrets.get("APP_KEY") or secrets.get("app_key") or "")
        or APP_KEY_DEFAULT
    ).strip()
    app_secret = (
        os.environ.get("ALIEXPRESS_APP_SECRET")
        or str(secrets.get("APP_SECRET") or secrets.get("app_secret") or "")
    ).strip()
    tracking_id = (
        os.environ.get("ALIEXPRESS_TRACKING_ID")
        or str(secrets.get("TRACKING_ID") or secrets.get("tracking_id") or "")
    ).strip()
    gateway = (
        os.environ.get("ALIEXPRESS_GATEWAY")
        or str(secrets.get("GATEWAY") or secrets.get("gateway") or "")
        or GATEWAY_DEFAULT
    ).strip()
    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "tracking_id": tracking_id,
        "gateway": gateway.rstrip("/") or GATEWAY_DEFAULT,
    }


def credenciales_configuradas():
    creds = credenciales_aliexpress()
    return bool(creds["app_key"] and creds["app_secret"])


def firmar_top(params, secret, sign_method="md5"):
    """Firma TOP: orden alfabética de claves, concatenación key+value, MD5 o HMAC-MD5."""
    partes = []
    for clave in sorted(params):
        if clave == "sign":
            continue
        valor = params[clave]
        if valor is None or valor == "":
            continue
        if isinstance(valor, (bytes, bytearray)):
            continue
        partes.append(f"{clave}{valor}")
    concatenado = "".join(partes).encode("utf-8")
    secreto = (secret or "").encode("utf-8")
    metodo = (sign_method or "md5").lower()
    if metodo == "hmac":
        return hmac.new(secreto, concatenado, hashlib.md5).hexdigest().upper()
    return hashlib.md5(secreto + concatenado + secreto).hexdigest().upper()


def timestamp_top(ahora=None):
    dt = ahora or datetime.now(TZ_CN)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CN)
    else:
        dt = dt.astimezone(TZ_CN)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _params_sistema(method, app_key, sign_method="md5"):
    return {
        "method": method,
        "app_key": str(app_key),
        "timestamp": timestamp_top(),
        "format": "json",
        "v": "2.0",
        "sign_method": sign_method,
    }


def _codigo_error(payload):
    if not isinstance(payload, dict):
        return None, ""
    err = payload.get("error_response") or payload.get("error") or {}
    if isinstance(err, dict) and err:
        codigo = err.get("code") or err.get("sub_code") or err.get("error_code")
        msg = err.get("msg") or err.get("sub_msg") or err.get("message") or ""
        return str(codigo) if codigo is not None else None, str(msg)
    for clave in ("code", "error_code", "sub_code"):
        if payload.get(clave) not in (None, "", 0, "0"):
            return str(payload.get(clave)), str(payload.get("msg") or payload.get("sub_msg") or "")
    return None, ""


def mensaje_error_ae(codigo, detalle=""):
    base = MENSAJES_ERROR.get(str(codigo or ""), "")
    extra = str(detalle or "").strip()
    if base and extra:
        return f"{base} ({extra})"
    if base:
        return base
    if extra:
        return f"AliExpress devolvió un error: {extra}"
    return "No se pudo completar la consulta a AliExpress."


def llamar_aliexpress(method, biz_params, image_bytes=None, sign_method="md5"):
    creds = credenciales_aliexpress()
    if not creds["app_secret"]:
        raise AliExpressError(
            "Falta ALIEXPRESS_APP_SECRET. Configure el secreto para consultas en vivo.",
            codigo="no_secret",
        )
    params = _params_sistema(method, creds["app_key"], sign_method=sign_method)
    for clave, valor in (biz_params or {}).items():
        if valor is None or valor == "":
            continue
        params[clave] = str(valor)
    params["sign"] = firmar_top(params, creds["app_secret"], sign_method=sign_method)
    headers = {"Accept": "application/json"}
    try:
        if image_bytes:
            archivos = {"image_bytes": ("consulta.jpg", image_bytes, "image/jpeg")}
            resp = requests.post(
                creds["gateway"],
                data=params,
                files=archivos,
                headers=headers,
                timeout=TIMEOUT_S,
            )
        else:
            resp = requests.post(
                creds["gateway"],
                data=params,
                headers=headers,
                timeout=TIMEOUT_S,
            )
    except requests.Timeout as exc:
        raise AliExpressError("La consulta a AliExpress superó el tiempo de espera.", codigo="timeout") from exc
    except requests.RequestException as exc:
        raise AliExpressError(f"No se pudo conectar con AliExpress: {exc}", codigo="network") from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise AliExpressError(
            f"AliExpress devolvió una respuesta no válida (HTTP {resp.status_code}).",
            codigo="http",
        ) from exc

    codigo, detalle = _codigo_error(payload)
    if codigo:
        raise AliExpressError(mensaje_error_ae(codigo, detalle), codigo=codigo)
    if resp.status_code >= 400:
        raise AliExpressError(
            mensaje_error_ae(str(resp.status_code), f"HTTP {resp.status_code}"),
            codigo=str(resp.status_code),
        )
    return payload


def _es_producto(item):
    if not isinstance(item, dict):
        return False
    claves = {str(k).lower() for k in item}
    return bool(
        claves & {
            "product_id",
            "productid",
            "product_title",
            "product_main_image_url",
            "target_sale_price",
            "sale_price",
            "item_id",
        }
    )


def extraer_lista_productos(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        if payload and all(_es_producto(x) for x in payload if isinstance(x, dict)):
            return [x for x in payload if isinstance(x, dict)]
        encontrados = []
        for item in payload:
            encontrados.extend(extraer_lista_productos(item))
        return encontrados
    if not isinstance(payload, dict):
        return []

    rutas = (
        ("aliexpress_affiliate_product_query_response", "resp_result", "result", "products", "product"),
        ("aliexpress_affiliate_product_query_response", "resp_result", "result", "products"),
        ("resp_result", "result", "products", "product"),
        ("resp_result", "result", "products"),
        ("result", "products", "product"),
        ("result", "products"),
        ("data", "products", "product"),
        ("data", "products"),
        ("aliexpress_ds_image_search_response", "result", "products", "product"),
        ("aliexpress_ds_image_search_response", "result", "products"),
        ("aliexpress_ds_image_search_response", "data", "products"),
        ("aliexpress_ds_product_get_response", "result"),
        ("products", "product"),
        ("products",),
    )
    for ruta in rutas:
        nodo = payload
        ok = True
        for clave in ruta:
            if isinstance(nodo, dict) and clave in nodo:
                nodo = nodo[clave]
            else:
                ok = False
                break
        if not ok:
            continue
        if isinstance(nodo, dict) and _es_producto(nodo):
            return [nodo]
        if isinstance(nodo, list):
            return [x for x in nodo if isinstance(x, dict)]
    # Recorrido amplio como último recurso.
    hallados = []
    for valor in payload.values():
        if isinstance(valor, (dict, list)):
            hallados.extend(extraer_lista_productos(valor))
    vistos = set()
    unicos = []
    for item in hallados:
        marca = id(item)
        if marca in vistos:
            continue
        vistos.add(marca)
        if _es_producto(item):
            unicos.append(item)
    return unicos


def _numero(valor, default=0.0):
    if valor is None or valor == "":
        return default
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(",", "")
    for token in ("USD", "US $", "US$", "$", "%"):
        texto = texto.replace(token, "")
    texto = texto.strip()
    try:
        return float(texto)
    except ValueError:
        return default


def _texto(valor, default=""):
    if valor is None:
        return default
    return str(valor).strip() or default


def _calificacion(valor):
    if valor is None or valor == "":
        return None
    numero = _numero(valor, default=-1)
    if numero < 0:
        return None
    texto = str(valor)
    if "%" in texto or numero > 5:
        # evaluate_rate suele venir como 95.0% → 4.75 / 5
        return round(min(5.0, max(0.0, numero / 20.0)), 2) if numero > 5 else round(numero, 2)
    return round(min(5.0, numero), 2)


def _enlace_producto(item, product_id):
    for clave in (
        "promotion_link",
        "product_detail_url",
        "product_url",
        "item_url",
        "detail_url",
        "enlace",
        "url",
    ):
        url = _texto(item.get(clave))
        if url.startswith("http"):
            return url
    if product_id:
        return f"https://www.aliexpress.com/item/{product_id}.html"
    return "https://www.aliexpress.com"


def _imagen_producto(item):
    for clave in (
        "product_main_image_url",
        "product_small_image_urls",
        "imagen_url",
        "image_url",
        "product_image",
        "main_image",
        "image",
        "pic_url",
    ):
        valor = item.get(clave)
        if isinstance(valor, dict):
            valor = valor.get("string") or valor.get("url") or valor.get("image")
        if isinstance(valor, list) and valor:
            valor = valor[0]
        url = _texto(valor)
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http"):
            return url
    return ""


def _stock_disponible(item):
    for clave in ("stock", "available_stock", "product_stock", "inventory", "sku_stock"):
        if clave in item and item[clave] not in (None, ""):
            try:
                return float(item[clave]) > 0
            except (TypeError, ValueError):
                continue
    estado = _texto(item.get("stock_status") or item.get("status") or "").lower()
    if estado in {"out_of_stock", "sold_out", "unavailable", "0"}:
        return False
    return True


def normalizar_producto_ae(item, origen="api"):
    if not isinstance(item, dict):
        return None
    product_id = _texto(
        item.get("product_id")
        or item.get("productId")
        or item.get("item_id")
        or item.get("itemId")
        or item.get("id")
    )
    titulo = _texto(
        item.get("product_title")
        or item.get("product_name")
        or item.get("title")
        or item.get("name")
        or item.get("titulo")
        or "Producto AliExpress"
    )
    precio = None
    for clave in (
        "target_sale_price",
        "target_app_sale_price",
        "sale_price",
        "product_price",
        "precio_usd",
        "price",
        "target_original_price",
        "original_price",
    ):
        if item.get(clave) not in (None, ""):
            precio = _numero(item.get(clave), default=-1)
            if precio >= 0:
                break
            precio = None
    if precio is None:
        precio = 0.0
    # Algunos listados envían centavos.
    if precio >= 1000 and str(item.get("target_sale_price") or "").isdigit():
        precio = precio / 100.0
    peso = _numero(item.get("product_weight") or item.get("weight") or item.get("peso_kg"), default=0.8)
    if peso <= 0:
        peso = 0.8
    volumen = _numero(item.get("volume") or item.get("volumen_m3"), default=0.004)
    if volumen <= 0:
        volumen = 0.004
    ventas = int(
        _numero(
            item.get("lastest_volume")
            or item.get("latest_volume")
            or item.get("volume_sales")
            or item.get("volumen_ventas")
            or 0
        )
    )
    return {
        "product_id": product_id or f"ae-{abs(hash(titulo)) % 10_000_000}",
        "titulo": titulo[:180],
        "precio_usd": round(float(precio), 2),
        "imagen_url": _imagen_producto(item),
        "calificacion": _calificacion(
            item.get("evaluate_rate")
            or item.get("evaluateRate")
            or item.get("rating")
            or item.get("calificacion")
        ),
        "enlace": _enlace_producto(item, product_id),
        "volumen_ventas": ventas,
        "peso_kg": round(peso, 3),
        "volumen_m3": round(volumen, 4),
        "origen": origen,
        "en_stock": _stock_disponible(item),
        "sku": f"AE-{product_id}" if product_id else f"AE-{abs(hash(titulo)) % 10_000_000}",
    }


def filtrar_y_ordenar(productos, min_usd=0, max_usd=0, orden="Más vendidos"):
    filtrados = []
    for prod in productos or []:
        if not prod:
            continue
        if prod.get("en_stock") is False:
            continue
        precio = _numero(prod.get("precio_usd"), default=0)
        if min_usd and precio < float(min_usd):
            continue
        if max_usd and precio > float(max_usd):
            continue
        filtrados.append(prod)
    clave_orden = orden or "Más vendidos"
    if clave_orden == "Mejor precio":
        filtrados.sort(key=lambda p: (p.get("precio_usd") is None, p.get("precio_usd") or 0))
    elif clave_orden == "Calificación":
        filtrados.sort(key=lambda p: (-(p.get("calificacion") or 0), -(p.get("volumen_ventas") or 0)))
    else:
        filtrados.sort(key=lambda p: (-(p.get("volumen_ventas") or 0), p.get("precio_usd") or 0))
    return filtrados


def catalogo_demostracion(keyword="", imagen=False):
    kw = (keyword or "").strip()
    base = [
        {
            "product_id": "1005007011110001",
            "titulo": "Auriculares Bluetooth TWS con cancelación de ruido",
            "precio_usd": 18.90,
            "imagen_url": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.8,
            "enlace": "https://www.aliexpress.com/item/1005007011110001.html",
            "volumen_ventas": 18420,
            "peso_kg": 0.28,
            "volumen_m3": 0.002,
        },
        {
            "product_id": "1005007011110002",
            "titulo": "Kit piezas CNC aluminio 6061 para router",
            "precio_usd": 42.50,
            "imagen_url": "https://images.unsplash.com/photo-1581091226825-a6a2a5aee158?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.6,
            "enlace": "https://www.aliexpress.com/item/1005007011110002.html",
            "volumen_ventas": 3210,
            "peso_kg": 1.85,
            "volumen_m3": 0.012,
        },
        {
            "product_id": "1005007011110003",
            "titulo": "Juego de herramientas manuales 108 piezas",
            "precio_usd": 29.99,
            "imagen_url": "https://images.unsplash.com/photo-1504148455328-c376907d081c?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.7,
            "enlace": "https://www.aliexpress.com/item/1005007011110003.html",
            "volumen_ventas": 9600,
            "peso_kg": 2.40,
            "volumen_m3": 0.015,
        },
        {
            "product_id": "1005007011110004",
            "titulo": "Taladro inalámbrico 21V con 2 baterías",
            "precio_usd": 54.20,
            "imagen_url": "https://images.unsplash.com/photo-1504148455328-c376907d081c?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.5,
            "enlace": "https://www.aliexpress.com/item/1005007011110004.html",
            "volumen_ventas": 7420,
            "peso_kg": 2.10,
            "volumen_m3": 0.018,
        },
        {
            "product_id": "1005007011110005",
            "titulo": "Porcelanato 60x120 acabado mármol (muestra)",
            "precio_usd": 16.40,
            "imagen_url": "https://images.unsplash.com/photo-1584622650111-993a426fbf0a?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.4,
            "enlace": "https://www.aliexpress.com/item/1005007011110005.html",
            "volumen_ventas": 2100,
            "peso_kg": 4.80,
            "volumen_m3": 0.022,
        },
        {
            "product_id": "1005007011110006",
            "titulo": "Cámara de acción 4K resistente al agua",
            "precio_usd": 37.80,
            "imagen_url": "https://images.unsplash.com/photo-1526170375885-4d8ecf77b99f?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.3,
            "enlace": "https://www.aliexpress.com/item/1005007011110006.html",
            "volumen_ventas": 12880,
            "peso_kg": 0.45,
            "volumen_m3": 0.003,
        },
        {
            "product_id": "1005007011110007",
            "titulo": "Lámpara LED de escritorio recargable",
            "precio_usd": 12.75,
            "imagen_url": "https://images.unsplash.com/photo-1507473883501-cd55bddb99de?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.6,
            "enlace": "https://www.aliexpress.com/item/1005007011110007.html",
            "volumen_ventas": 22100,
            "peso_kg": 0.62,
            "volumen_m3": 0.005,
        },
        {
            "product_id": "1005007011110008",
            "titulo": "Organizador de herramientas para taller",
            "precio_usd": 23.10,
            "imagen_url": "https://images.unsplash.com/photo-1581092918056-0c4c3acd3789?auto=format&fit=crop&w=600&q=80",
            "calificacion": 4.2,
            "enlace": "https://www.aliexpress.com/item/1005007011110008.html",
            "volumen_ventas": 1540,
            "peso_kg": 1.20,
            "volumen_m3": 0.010,
        },
    ]
    productos = []
    for raw in base:
        prod = normalizar_producto_ae(raw, origen="demo")
        if prod:
            productos.append(prod)
    if imagen:
        for prod in productos[:6]:
            prod["titulo"] = f"Coincidencia visual · {prod['titulo']}"
        return productos[:6]
    if not kw:
        return productos
    needle = kw.lower()
    coincidencias = [p for p in productos if needle in p["titulo"].lower()]
    if coincidencias:
        return coincidencias
    extra = normalizar_producto_ae(
        {
            "product_id": str(1005008000000 + abs(hash(kw)) % 900000),
            "product_title": f"{kw.strip().title()} — envío a EE. UU.",
            "target_sale_price": 19.90 + (abs(hash(kw)) % 40),
            "product_main_image_url": f"https://picsum.photos/seed/ae{abs(hash(kw)) % 10000}/600/400",
            "evaluate_rate": "4.5",
            "product_detail_url": "https://www.aliexpress.com",
            "lastest_volume": 800 + abs(hash(kw)) % 4000,
            "peso_kg": 0.9,
            "volumen_m3": 0.006,
        },
        origen="demo",
    )
    return ([extra] if extra else []) + productos[:5]


def preparar_imagen_busqueda(file_bytes, max_lado=800):
    from PIL import Image

    img = Image.open(io.BytesIO(file_bytes))
    img = img.convert("RGB")
    img.thumbnail((max_lado, max_lado))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def _params_busqueda_texto(keyword, min_usd, max_usd, orden, tracking_id, page_size=PAGE_SIZE):
    sort = ORDEN_API.get(orden, "LAST_VOLUME_DESC")
    params = {
        "keywords": keyword,
        "target_currency": "USD",
        "target_language": "ES",
        "ship_to_country": "US",
        "page_no": "1",
        "page_size": str(page_size),
        "sort": sort,
        "fields": (
            "commission_rate,sale_price,product_id,product_title,"
            "product_main_image_url,product_detail_url,evaluate_rate,"
            "lastest_volume,shop_id,promotion_link"
        ),
    }
    if tracking_id:
        params["tracking_id"] = tracking_id
    if min_usd and float(min_usd) > 0:
        params["min_sale_price"] = f"{float(min_usd):.2f}"
    if max_usd and float(max_usd) > 0:
        params["max_sale_price"] = f"{float(max_usd):.2f}"
    return params


def _params_busqueda_imagen(image_b64, min_usd, max_usd, orden, tracking_id):
    sort = ORDEN_API.get(orden, "LAST_VOLUME_DESC")
    params = {
        "image_base64": image_b64,
        "target_currency": "USD",
        "target_language": "ES",
        "ship_to_country": "US",
        "shpt_to": "US",
        "currency": "USD",
        "lang": "ES",
        "sort": sort,
        "sort_type": sort,
        "product_cnt": str(PAGE_SIZE),
        "page_size": str(PAGE_SIZE),
    }
    if tracking_id:
        params["tracking_id"] = tracking_id
    if min_usd and float(min_usd) > 0:
        params["min_sale_price"] = f"{float(min_usd):.2f}"
    if max_usd and float(max_usd) > 0:
        params["max_sale_price"] = f"{float(max_usd):.2f}"
    return params


def _normalizar_lote(crudos, origen="api"):
    productos = []
    vistos = set()
    for item in crudos or []:
        prod = normalizar_producto_ae(item, origen=origen)
        if not prod:
            continue
        if prod["product_id"] in vistos:
            continue
        vistos.add(prod["product_id"])
        productos.append(prod)
    return productos


def _resultado(productos, error=None, aviso=None, fuente="api", metodo=""):
    return {
        "productos": productos or [],
        "error": error,
        "aviso": aviso,
        "fuente": fuente,
        "metodo": metodo,
    }


def _llamar_con_reintentos(method, biz_params, image_bytes=None):
    ultimo = None
    for sign_method in ("md5", "hmac"):
        try:
            return llamar_aliexpress(method, biz_params, image_bytes=image_bytes, sign_method=sign_method)
        except AliExpressError as exc:
            ultimo = exc
            if str(exc.codigo) == "25" and sign_method == "md5":
                continue
            raise
    raise ultimo or AliExpressError("No se pudo firmar la consulta a AliExpress.")


def buscar_aliexpress_texto(keyword, min_usd=0, max_usd=0, orden="Más vendidos"):
    kw = (keyword or "").strip()
    if not kw:
        return _resultado([], error="Escriba una palabra clave para buscar en AliExpress.", fuente="none")

    demo = filtrar_y_ordenar(catalogo_demostracion(kw, imagen=False), min_usd, max_usd, orden)
    if not credenciales_configuradas():
        return _resultado(
            demo,
            aviso="Consultas en vivo desactivadas: configure ALIEXPRESS_APP_SECRET. Se muestra un catálogo de demostración con envío a EE. UU.",
            fuente="demo",
            metodo="demo",
        )

    creds = credenciales_aliexpress()
    params = _params_busqueda_texto(kw, min_usd, max_usd, orden, creds["tracking_id"])
    try:
        payload = _llamar_con_reintentos("aliexpress.affiliate.product.query", params)
    except AliExpressError as exc:
        if "tracking" in (exc.mensaje or "").lower() and creds["tracking_id"]:
            params.pop("tracking_id", None)
            try:
                payload = _llamar_con_reintentos("aliexpress.affiliate.product.query", params)
            except AliExpressError as exc2:
                return _resultado(demo, error=exc2.mensaje, aviso="Se muestran resultados de demostración.", fuente="demo", metodo="aliexpress.affiliate.product.query")
        else:
            return _resultado(demo, error=exc.mensaje, aviso="Se muestran resultados de demostración.", fuente="demo", metodo="aliexpress.affiliate.product.query")

    productos = filtrar_y_ordenar(_normalizar_lote(extraer_lista_productos(payload)), min_usd, max_usd, orden)
    if not productos:
        return _resultado(
            [],
            aviso="No hay stock disponible para los filtros indicados.",
            fuente="api",
            metodo="aliexpress.affiliate.product.query",
        )
    return _resultado(productos, fuente="api", metodo="aliexpress.affiliate.product.query")


def buscar_aliexpress_imagen(image_bytes, min_usd=0, max_usd=0, orden="Más vendidos"):
    if not image_bytes:
        return _resultado([], error="Cargue una imagen JPG o PNG del producto.", fuente="none")

    demo = filtrar_y_ordenar(catalogo_demostracion(imagen=True), min_usd, max_usd, orden)
    if not credenciales_configuradas():
        return _resultado(
            demo,
            aviso="Consultas en vivo desactivadas: configure ALIEXPRESS_APP_SECRET. Se muestran coincidencias visuales de demostración.",
            fuente="demo",
            metodo="demo",
        )

    try:
        jpeg = preparar_imagen_busqueda(image_bytes)
    except Exception:
        jpeg = image_bytes
    image_b64 = base64.b64encode(jpeg).decode("ascii")
    creds = credenciales_aliexpress()
    params = _params_busqueda_imagen(image_b64, min_usd, max_usd, orden, creds["tracking_id"])

    intentos = [
        (params, jpeg),
        ({k: v for k, v in params.items() if k != "image_base64"}, jpeg),
        (params, None),
    ]
    ultimo_error = None
    for biz, archivo in intentos:
        try:
            payload = _llamar_con_reintentos(
                "aliexpress.ds.image.search",
                biz,
                image_bytes=archivo,
            )
            productos = filtrar_y_ordenar(
                _normalizar_lote(extraer_lista_productos(payload)),
                min_usd,
                max_usd,
                orden,
            )
            if productos:
                return _resultado(productos, fuente="api", metodo="aliexpress.ds.image.search")
            ultimo_error = AliExpressError("No hay stock disponible para los filtros indicados.")
        except AliExpressError as exc:
            ultimo_error = exc
            continue

    aviso = "La búsqueda por imagen no está autorizada o falló. Se muestran coincidencias de demostración."
    error = ultimo_error.mensaje if ultimo_error else None
    return _resultado(demo, error=error, aviso=aviso, fuente="demo", metodo="aliexpress.ds.image.search")


def serializar_payload_debug(payload):
    try:
        return json.dumps(payload, ensure_ascii=False)[:4000]
    except TypeError:
        return str(payload)[:4000]


try:
    from zoneinfo import ZoneInfo

    ZONA_HONDURAS = ZoneInfo("America/Tegucigalpa")
except Exception:
    ZONA_HONDURAS = timezone(timedelta(hours=-6), name="America/Tegucigalpa")

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DEL SISTEMA & ZONA HORARIA HONDURAS (UTC-6)
# ---------------------------------------------------------
DB_NAME = str(Path(__file__).resolve().parent / "ccm_maritime_enterprise.db")
LOGO_FILENAME = "logo_ccm_print.jpg"
RUTAS_LOGO = (
    Path(__file__).resolve().parent / "assets" / "logo_ccm_print.jpg",
    Path(__file__).resolve().parent / "assets" / "logo_ccm.png",
    Path(__file__).resolve().parent / "logo_ccm_print.jpg",
    Path(__file__).resolve().parent / "logo centro y mas.jpg",
)

VIGENCIA_COTIZACION_HORAS = 24
VIGENCIA_COTIZACION = timedelta(hours=VIGENCIA_COTIZACION_HORAS)
FORMATOS_FECHA_COTIZACION = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
)


def obtener_tiempo_honduras():
    return datetime.now(ZONA_HONDURAS)


def estampa_tiempo_honduras(ahora=None):
    """Marca de emisión en hora oficial de Honduras (America/Tegucigalpa, UTC-6)."""
    dt = ahora or obtener_tiempo_honduras()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZONA_HONDURAS)
    else:
        dt = dt.astimezone(ZONA_HONDURAS)
    return dt, dt.strftime("%Y-%m-%d %H:%M:%S")


def formatear_fecha_pantalla(fecha_raw):
    dt = parsear_fecha_cotizacion(fecha_raw)
    if dt is None:
        return str(fecha_raw or "")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def clave_orden_cotizacion(fecha_raw, id_cot=0):
    dt = parsear_fecha_cotizacion(fecha_raw)
    ts = dt.timestamp() if dt is not None else 0.0
    try:
        cid = int(id_cot or 0)
    except (TypeError, ValueError):
        cid = 0
    return (-ts, -cid)


def ordenar_cotizaciones_desc(filas, idx_fecha=7, idx_id=0):
    """LIFO: la cotización más reciente (fecha Honduras) queda primero."""
    return sorted(filas, key=lambda r: clave_orden_cotizacion(r[idx_fecha], r[idx_id]))


def parsear_fecha_cotizacion(fecha_raw):
    if fecha_raw is None:
        return None
    if isinstance(fecha_raw, datetime):
        dt = fecha_raw
    else:
        texto = str(fecha_raw).strip()
        dt = None
        for fmt in FORMATOS_FECHA_COTIZACION:
            try:
                dt = datetime.strptime(texto, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZONA_HONDURAS)
    return dt.astimezone(ZONA_HONDURAS)


def cotizacion_vigente(fecha_raw, ahora=None):
    dt = parsear_fecha_cotizacion(fecha_raw)
    if dt is None:
        return False
    ahora = ahora or obtener_tiempo_honduras()
    edad = ahora - dt
    return timedelta(0) <= edad <= VIGENCIA_COTIZACION


def vencimiento_cotizacion(fecha_raw):
    dt = parsear_fecha_cotizacion(fecha_raw)
    if dt is None:
        return None
    return dt + VIGENCIA_COTIZACION


def texto_vigencia_cotizacion(fecha_raw, ahora=None):
    dt = parsear_fecha_cotizacion(fecha_raw)
    if dt is None:
        return "Sin vigencia"
    ahora = ahora or obtener_tiempo_honduras()
    fin = dt + VIGENCIA_COTIZACION
    restante = fin - ahora
    fin_txt = fin.strftime("%d/%m/%Y %I:%M %p")
    if restante.total_seconds() <= 0:
        return f"Vencida (era hasta {fin_txt})"
    total_min = int(restante.total_seconds() // 60)
    horas, minutos = divmod(total_min, 60)
    if horas >= 1:
        return f"Vigente {horas} h {minutos} min (hasta {fin_txt})"
    return f"Vigente {minutos} min (hasta {fin_txt})"


def leer_config_moneda(clave, valor_default):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT valor FROM config_sistema WHERE clave = ?", (clave,))
            row = cur.fetchone()
            if row and row[0] not in (None, ""):
                try:
                    return float(row[0])
                except ValueError:
                    return row[0]
    except Exception:
        pass
    try:
        seccion = st.secrets.get("moneda", {})
        if clave in seccion:
            return seccion[clave]
        return seccion.get(clave, valor_default)
    except Exception:
        return valor_default


OPCION_PREDETERMINADA = "🏬 Retirar en Almacén Principal (San Juan, Intibucá)"

if "vista_actual" not in st.session_state:
    st.session_state["vista_actual"] = "login"

if "sub_tab_inicio" not in st.session_state:
    st.session_state["sub_tab_inicio"] = "Inicio"

if "vista_activa" not in st.session_state:
    st.session_state["vista_activa"] = st.session_state.get("sub_tab_inicio", "Inicio")

if "hub" not in st.session_state:
    st.session_state["hub"] = None

if "mostrar_guia" not in st.session_state:
    st.session_state["mostrar_guia"] = False

if "modalidad_envio_seleccionada" not in st.session_state:
    st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA

# Hubs de origen: agregar módulos aquí no cambia la navegación general.
HUBS = {
    "china": {
        "label": "China",
        "icon": "🇨🇳",
        "descripcion": "Consolidación marítima, catálogo y flete",
        "activo": True,
        "modulos": [
            {
                "id": "Mis Cotizaciones",
                "label": "Mis Cotizaciones",
                "nav": "📄 Mis Cotiz.",
                "icon": "📄",
                "detalle": "Historial y descarga de PDF",
                "btn_key": "mod_cotizaciones",
            },
            {
                "id": "Catálogo",
                "label": "Catálogo",
                "nav": "🛍️ Catálogo",
                "icon": "🛍️",
                "detalle": "Fábricas 1688 y costo en Honduras",
                "btn_key": "mod_catalogo",
            },
            {
                "id": "Cotizador",
                "label": "Cotizador",
                "nav": "📐 Cotizador",
                "icon": "📐",
                "detalle": "Flete marítimo por libra o CBM",
                "btn_key": "mod_cotizador",
            },
            {
                "id": "Mis Envíos",
                "label": "Envíos",
                "nav": "📦 Envíos",
                "icon": "📦",
                "detalle": "Paquetes en tránsito",
                "btn_key": "mod_envios",
            },
            {
                "id": "Etiqueta",
                "label": "Fichas",
                "nav": "🏷️ Fichas",
                "icon": "🏷️",
                "detalle": "Etiqueta bodega Guangzhou",
                "btn_key": "mod_fichas",
            },
        ],
    },
    "eeuu": {
        "label": "EE. UU.",
        "icon": "🇺🇸",
        "descripcion": "Búsqueda y cotización AliExpress con envío a Estados Unidos",
        "activo": True,
        "modulos": [],
    },
    "honduras": {
        "label": "Honduras",
        "icon": "🇭🇳",
        "descripcion": "Módulo en preparación para operaciones locales",
        "activo": False,
        "modulos": [],
    },
}

MODULOS_POR_ID = {mod["id"]: hub_id for hub_id, hub in HUBS.items() for mod in hub["modulos"]}
VISTAS_MODULO = set(MODULOS_POR_ID.keys())
MODULOS_CHINA_INICIAL = ("Cotizador", "Catálogo", "Mis Cotizaciones")
MODULOS_CHINA_BLOQUEADOS = ("Mis Envíos", "Etiqueta")
ROLES_ADMIN = ("admin", "superadmin")
DNI_SUPERADMIN = "1301199800990"
NOMBRE_SUPERADMIN = "Domingo Heriberto Ardon"
CORREO_SUPERADMIN = "heribertoardon1998@gmail.com"
CLAVE_INICIAL_SUPERADMIN = "1301"
PERMISOS_ABIERTOS_TEMPORAL = True
HUB_PERMISO_COL = {"china": "hub_china", "eeuu": "hub_eeuu", "honduras": "hub_honduras"}
MODULO_PERMISO_COL = {
    "Cotizador": "mod_cotizador",
    "Catálogo": "mod_catalogo",
    "Mis Cotizaciones": "mod_cotizaciones",
    "Mis Envíos": "mod_envios",
    "Etiqueta": "mod_fichas",
}


def es_cotizacion_confirmada(valor):
    try:
        return int(valor or 0) == 1
    except (TypeError, ValueError):
        return False


def cotizacion_visible_historial(fecha_raw, confirmada, ahora=None):
    if es_cotizacion_confirmada(confirmada):
        return True
    return cotizacion_vigente(fecha_raw, ahora)


def texto_estado_cotizacion(fecha_raw, confirmada, ahora=None):
    if es_cotizacion_confirmada(confirmada):
        return "Consolidada — permanente en el historial del casillero"
    return texto_vigencia_cotizacion(fecha_raw, ahora)


def _limpiar_cotizacion_vencida_en_sesion(ahora):
    d_pdf = st.session_state.get("datos_pdf_confirmado")
    if not isinstance(d_pdf, dict):
        return None
    id_cot = d_pdf.get("id_cot")
    try:
        cid = int(id_cot or 0)
    except (TypeError, ValueError):
        cid = 0
    en_sesion = False
    if cid:
        _, lista = bolsa_cotizaciones_sesion(st.session_state.get("casillero"))
        en_sesion = any(int(r.get("id") or 0) == cid for r in lista)
    if not cotizacion_existe_en_casillero(id_cot) and not en_sesion:
        st.session_state.pop("datos_pdf_confirmado", None)
        st.session_state.pop("ultima_cot_id", None)
        return None
    if cotizacion_esta_confirmada(id_cot):
        return d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc")
    fecha_pdf = d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc")
    if cotizacion_vigente(fecha_pdf, ahora) or en_sesion:
        return fecha_pdf
    st.session_state.pop("datos_pdf_confirmado", None)
    st.session_state.pop("ultima_cot_id", None)
    return None


def fechas_cotizaciones_casillero(casillero):
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(fecha_creacion, fecha) FROM cotizaciones WHERE codigo_casillero = ? ORDER BY fecha_creacion DESC, id DESC",
                (cas,),
            )
            return [fila[0] for fila in cur.fetchall()]
    except Exception:
        return []


def casillero_tiene_cotizacion_confirmada(casillero):
    cas = formatear_casillero(casillero or "")
    if not cas:
        return False
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM cotizaciones WHERE codigo_casillero = ? AND IFNULL(confirmada, 0) = 1 LIMIT 1",
                (cas,),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def cotizacion_existe_en_casillero(id_cot, casillero=None):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return False
    cas = formatear_casillero(casillero or st.session_state.get("casillero", "") or "")
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if cas:
                cur.execute("SELECT 1 FROM cotizaciones WHERE id = ? AND codigo_casillero = ?", (cid, cas))
            else:
                cur.execute("SELECT 1 FROM cotizaciones WHERE id = ?", (cid,))
            return cur.fetchone() is not None
    except Exception:
        return False


def cotizacion_esta_confirmada(id_cot, casillero=None):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return False
    cas = formatear_casillero(casillero or st.session_state.get("casillero", "") or "")
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if cas:
                cur.execute(
                    "SELECT IFNULL(confirmada, 0) FROM cotizaciones WHERE id = ? AND codigo_casillero = ?",
                    (cid, cas),
                )
            else:
                cur.execute("SELECT IFNULL(confirmada, 0) FROM cotizaciones WHERE id = ?", (cid,))
            row = cur.fetchone()
        return bool(row and int(row[0]) == 1)
    except Exception:
        return False


def bolsa_cotizaciones_sesion(casillero):
    cas = formatear_casillero(casillero or "")
    if "cotizaciones" not in st.session_state or not isinstance(st.session_state["cotizaciones"], dict):
        st.session_state["cotizaciones"] = {}
    if not cas:
        return cas, []
    return cas, st.session_state["cotizaciones"].setdefault(cas, [])


def registro_sesion_a_fila(reg):
    return (
        int(reg.get("id") or 0),
        float(reg.get("alto_cm") or 0),
        float(reg.get("ancho_cm") or 0),
        float(reg.get("largo_cm") or 0),
        float(reg.get("peso_lb") or 0),
        float(reg.get("volumen_m3") or 0),
        float(reg.get("total_usd") or 0),
        reg.get("fecha_creacion") or reg.get("fecha"),
        int(reg.get("confirmada") or 0),
    )


def registrar_error_direcciones(exc, contexto):
    """Registra fallos de SQLite en sesión y en logs; nunca silencia un INSERT/DELETE."""
    msg = f"{contexto}: {exc}"
    print(f"[CCM direcciones] {msg}", flush=True)
    try:
        st.session_state["_dir_db_error"] = msg
    except Exception:
        pass


def invalidar_cache_direcciones():
    """Si la carga llega a cachearse, fuerza relectura inmediata tras escribir."""
    clear = getattr(cargar_direcciones_db, "clear", None)
    if callable(clear):
        clear()


def cargar_direcciones_db(casillero):
    """Lee SIEMPRE del archivo SQLite (sin @st.cache_data): F5, cambio de vista y re-login ven las filas reales.

    Busca con coincidencias_casillero: una fila guardada como 15011985 también aparece
    al consultar CCM-15011985 (y viceversa).
    """
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    claves = coincidencias_casillero(casillero)
    if not claves:
        return []
    placeholders = ",".join("?" * len(claves))
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, etiqueta, receptor_nombre, ciudad, direccion_exacta
            FROM direcciones_entrega
            WHERE codigo_casillero IN ({placeholders})
            ORDER BY id ASC
            """,
            claves,
        )
        filas = cur.fetchall()
        cur.execute(
            f"""
            UPDATE direcciones_entrega
            SET codigo_casillero = ?
            WHERE codigo_casillero IN ({placeholders}) AND codigo_casillero != ?
            """,
            (cas, *claves, cas),
        )
        conn.commit()
        return filas


@st.cache_data(ttl=20, show_spinner=False)
def cargar_cotizaciones_db(casillero):
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd,
                   COALESCE(fecha_creacion, fecha), IFNULL(confirmada, 0)
            FROM cotizaciones
            WHERE codigo_casillero = ?
            ORDER BY fecha_creacion DESC, id DESC
            """,
            (cas,),
        )
        return cur.fetchall()


def hidratar_cotizaciones_sesion(casillero):
    cas, lista = bolsa_cotizaciones_sesion(casillero)
    if not cas:
        return
    conocidos = {int(r.get("id") or 0) for r in lista}
    try:
        for fila in cargar_cotizaciones_db(cas):
            cid = int(fila[0])
            if cid in conocidos:
                continue
            lista.append(
                {
                    "id": cid,
                    "codigo_casillero": cas,
                    "alto_cm": fila[1],
                    "ancho_cm": fila[2],
                    "largo_cm": fila[3],
                    "peso_lb": fila[4],
                    "volumen_m3": fila[5],
                    "total_usd": fila[6],
                    "fecha": fila[7],
                    "fecha_creacion": fila[7],
                    "confirmada": int(fila[8] or 0),
                }
            )
            conocidos.add(cid)
    except Exception:
        pass


def filas_cotizaciones_casillero(casillero, ahora=None):
    cas = formatear_casillero(casillero or "")
    ahora = ahora or obtener_tiempo_honduras()
    hidratar_cotizaciones_sesion(cas)
    by_id = {}
    try:
        for fila in cargar_cotizaciones_db(cas):
            by_id[int(fila[0])] = fila
    except Exception:
        pass
    _, lista = bolsa_cotizaciones_sesion(cas)
    for reg in lista:
        try:
            by_id[int(reg.get("id") or 0)] = registro_sesion_a_fila(reg)
        except (TypeError, ValueError):
            continue
    todas = ordenar_cotizaciones_desc([f for f in by_id.values() if f and f[0]])
    visibles = [f for f in todas if cotizacion_visible_historial(f[7], f[8], ahora)]
    return todas, visibles


def marcar_cotizacion_sesion_confirmada(id_cot, casillero):
    cas, lista = bolsa_cotizaciones_sesion(casillero)
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return
    for reg in lista:
        if int(reg.get("id") or 0) == cid:
            reg["confirmada"] = 1
            break


def confirmar_cotizacion_casillero(id_cot, casillero):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return False
    cas = formatear_casillero(casillero or "")
    if not cas:
        return False
    _, ahora = estampa_tiempo_honduras()
    actualizado = False
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE cotizaciones
                SET confirmada = 1, fecha_confirmacion = ?
                WHERE id = ? AND codigo_casillero = ? AND IFNULL(confirmada, 0) = 0
                """,
                (ahora, cid, cas),
            )
            conn.commit()
            actualizado = cur.rowcount > 0
        cargar_cotizaciones_db.clear()
    except Exception:
        actualizado = False
    marcar_cotizacion_sesion_confirmada(cid, cas)
    return True


def firma_parametros_cotizador(al, an, la, peso_lb, destino, tipo_carga):
    """Huella de los parámetros que definen una tarifa emitida."""
    return (
        round(float(al or 0), 4),
        round(float(an or 0), 4),
        round(float(la or 0), 4),
        round(float(peso_lb or 0), 4),
        str(destino or "").strip(),
        str(tipo_carga or "").strip(),
    )


def firma_desde_emision(d_pdf):
    if not isinstance(d_pdf, dict):
        return None
    guardada = d_pdf.get("firma_params")
    if isinstance(guardada, (list, tuple)) and len(guardada) == 6:
        return tuple(guardada)
    return firma_parametros_cotizador(
        d_pdf.get("al"),
        d_pdf.get("an"),
        d_pdf.get("la"),
        d_pdf.get("peso_lb"),
        d_pdf.get("destino_entrega"),
        d_pdf.get("tipo_carga"),
    )


def invalidar_emision_visible_cotizador():
    """Oculta la tarjeta verde y los PDFs de la última emisión (el pendiente en historial se conserva)."""
    st.session_state.pop("datos_pdf_confirmado", None)
    st.session_state.pop("_ccm_scroll_emit", None)
    st.session_state.pop("_ccm_emit_error", None)
    for clave in list(st.session_state.keys()):
        ks = str(clave)
        if ks.startswith("dl_pdf_fab_") or ks.startswith("btn_ver_mis_cotizaciones_"):
            st.session_state.pop(clave, None)


def sincronizar_emision_con_formulario(firma_actual):
    """Si el formulario ya no coincide con la tarifa emitida, oculta documentos y código anteriores."""
    d_pdf = st.session_state.get("datos_pdf_confirmado")
    if not isinstance(d_pdf, dict):
        return False
    if firma_desde_emision(d_pdf) != tuple(firma_actual):
        invalidar_emision_visible_cotizador()
        return True
    return False


def emitir_tarifa_desde_snapshot():
    """on_click: guarda la tarifa en SQLite y en st.session_state['cotizaciones']."""
    snap = st.session_state.get("_cot_emit_snapshot")
    casillero = formatear_casillero(st.session_state.get("casillero") or "")
    if not isinstance(snap, dict) or not casillero:
        return
    firma_snap = snap.get("firma_params") or firma_parametros_cotizador(
        snap.get("al"),
        snap.get("an"),
        snap.get("la"),
        snap.get("peso_lb"),
        snap.get("destino"),
        snap.get("tipo_carga"),
    )
    firma_snap = tuple(firma_snap)
    d_emitida = st.session_state.get("datos_pdf_confirmado")
    if isinstance(d_emitida, dict) and firma_desde_emision(d_emitida) == firma_snap:
        return
    ahora_emision, f_hoy_sql = estampa_tiempo_honduras()
    f_hoy_doc = ahora_emision.strftime("%d/%m/%Y %I:%M:%S %p")
    id_generado = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO cotizaciones (
                    codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, volumen_ft3,
                    total_usd, fecha, confirmada, fecha_creacion
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    casillero,
                    snap.get("al"),
                    snap.get("an"),
                    snap.get("la"),
                    snap.get("peso_lb"),
                    snap.get("vol_m3"),
                    snap.get("vol_ft3"),
                    snap.get("total_usd"),
                    f_hoy_sql,
                    f_hoy_sql,
                ),
            )
            id_generado = cur.lastrowid
            conn.commit()
        cargar_cotizaciones_db.clear()
    except Exception as exc:
        id_generado = None
        st.session_state["_ccm_emit_error"] = str(exc)
    if not id_generado:
        id_generado = int(st.session_state.get("_seq_cot") or 900000) + 1
        st.session_state["_seq_cot"] = id_generado
    registro = {
        "id": int(id_generado),
        "codigo_casillero": casillero,
        "alto_cm": snap.get("al"),
        "ancho_cm": snap.get("an"),
        "largo_cm": snap.get("la"),
        "peso_lb": snap.get("peso_lb"),
        "peso_kg": snap.get("peso_kg"),
        "volumen_m3": snap.get("vol_m3"),
        "volumen_ft3": snap.get("vol_ft3"),
        "total_usd": snap.get("total_usd"),
        "fecha": f_hoy_sql,
        "fecha_creacion": f_hoy_sql,
        "confirmada": 0,
        "tipo_carga": snap.get("tipo_carga"),
        "detalle_tarifa": snap.get("detalle_tarifa"),
        "destino_entrega": snap.get("destino"),
        "fecha_hora_doc": f_hoy_doc,
    }
    _, lista = bolsa_cotizaciones_sesion(casillero)
    lista.insert(0, registro)
    st.session_state["ultima_cot_id"] = int(id_generado)
    st.session_state["cotizacion_historial_foco"] = int(id_generado)
    st.session_state["datos_pdf_confirmado"] = {
        "tipo_carga": snap.get("tipo_carga"),
        "al": snap.get("al"),
        "an": snap.get("an"),
        "la": snap.get("la"),
        "peso_lb": snap.get("peso_lb"),
        "peso_kg": snap.get("peso_kg"),
        "vol_m3": snap.get("vol_m3"),
        "vol_ft3": snap.get("vol_ft3"),
        "total_usd": snap.get("total_usd"),
        "detalle_tarifa": snap.get("detalle_tarifa"),
        "id_cot": int(id_generado),
        "destino_entrega": snap.get("destino"),
        "fecha_hora_doc": f_hoy_doc,
        "fecha_sql": f_hoy_sql,
        "firma_params": list(firma_snap),
    }
    st.session_state["china_modulos_desbloqueados"] = True
    st.session_state.pop("_ccm_emit_error", None)
    avanzar_guia_si(2, 3)
    st.session_state["_ccm_rerun_app"] = True
    st.session_state["_ccm_scroll_emit"] = True


def purgar_cotizaciones_no_confirmadas_vencidas(ahora=None):
    ahora = ahora or obtener_tiempo_honduras()
    with get_db() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, fecha, IFNULL(confirmada, 0) FROM cotizaciones")
        except sqlite3.OperationalError:
            return 0
        ids_borrar = []
        for cid, fecha, confirmada in cur.fetchall():
            if es_cotizacion_confirmada(confirmada):
                continue
            if not cotizacion_vigente(fecha, ahora):
                ids_borrar.append(cid)
        if not ids_borrar:
            return 0
        cur.executemany("DELETE FROM cotizaciones WHERE id = ?", [(cid,) for cid in ids_borrar])
        conn.commit()
        return len(ids_borrar)


def vista_muestra_envios_fichas():
    """Envíos solo en la barra cuando el usuario está en Mis Cotizaciones o Envíos."""
    return st.session_state.get("sub_tab_inicio") in ("Mis Cotizaciones", "Mis Envíos")


def china_seguimiento_habilitado():
    """Compatibilidad: Envíos se habilita al abrir Mis Cotizaciones."""
    habilitado = vista_muestra_envios_fichas()
    st.session_state["china_modulos_desbloqueados"] = habilitado
    return habilitado


def modulos_china_visibles():
    """Tarjetas del hub China: nunca Envíos ni Fichas."""
    mods = HUBS["china"]["modulos"]
    permitidos = [m for m in mods if usuario_puede_modulo(m["id"])]
    bloqueados = set(MODULOS_CHINA_BLOQUEADOS)
    return [m for m in permitidos if m["id"] not in bloqueados]


def modulos_china_nav():
    """Barra superior: Envíos solo en Mis Cotizaciones / Envíos. Fichas no va en el menú."""
    mods = HUBS["china"]["modulos"]
    permitidos = [m for m in mods if usuario_puede_modulo(m["id"]) and m["id"] != "Etiqueta"]
    if vista_muestra_envios_fichas():
        return permitidos
    bloqueados = set(MODULOS_CHINA_BLOQUEADOS)
    return [m for m in permitidos if m["id"] not in bloqueados]


def al_cambiar_modalidad_entrega():
    """on_change del select de entrega: sincroniza la modalidad ANTES de decidir la vista.

    Sin esto, elegir «Crear Nueva Dirección» solo mostraba el texto seleccionado y el
    formulario aparecía hasta la siguiente interacción.
    """
    invalidar_emision_visible_cotizador()
    nueva = st.session_state.get("sb_modalidad_entrega")
    if nueva:
        st.session_state["modalidad_envio_seleccionada"] = nueva


def seleccionar_modalidad_entrega(opcion):
    """Fija la modalidad activa; el widget se actualiza en el siguiente run, antes de instanciarse."""
    st.session_state["modalidad_envio_seleccionada"] = opcion
    st.session_state["_mod_entrega_pendiente"] = opcion


CAMPOS_FORM_DIRECCION = ("dir_etiqueta_in", "dir_receptor_in", "dir_tel_in", "dir_exacta_in")


def asegurar_esquema_direcciones():
    """Garantiza tabla y columnas de direcciones_entrega (migra bases desplegadas viejas)."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS direcciones_entrega (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo_casillero TEXT NOT NULL,
                    etiqueta TEXT NOT NULL,
                    receptor_nombre TEXT NOT NULL,
                    telefono TEXT NOT NULL,
                    departamento TEXT NOT NULL,
                    ciudad TEXT NOT NULL,
                    direccion_exacta TEXT NOT NULL,
                    fecha_creacion TEXT NOT NULL
                )
                """
            )
            c.execute("PRAGMA table_info(direcciones_entrega)")
            columnas_dir = {fila[1] for fila in c.fetchall()}
            for col in ("receptor_nombre", "telefono", "departamento", "ciudad", "direccion_exacta", "fecha_creacion"):
                if col not in columnas_dir:
                    c.execute(f"ALTER TABLE direcciones_entrega ADD COLUMN {col} TEXT")
            conn.commit()
    except Exception as exc:
        try:
            st.session_state["_dir_db_error"] = f"Esquema direcciones_entrega: {exc}"
        except Exception:
            pass


def direcciones_sesion(casillero):
    """Direcciones del casillero: SQLite es la fuente en CADA render (F5 / cambio de vista / re-login).

    session_state solo conserva, además, entradas que la BD rechazó (id None) para que
    no desaparezcan del desplegable durante la sesión actual.
    """
    cas = formatear_casillero(casillero or "")
    bolsa = st.session_state.setdefault("direcciones_usuario", {})
    previa = []
    vistos_prev = set()
    for clave in (*coincidencias_casillero(casillero), casillero, cas):
        if not clave or clave in vistos_prev:
            continue
        vistos_prev.add(clave)
        previa.extend(bolsa.get(clave) or [])
    try:
        filas = cargar_direcciones_db(casillero)
    except Exception as exc:
        registrar_error_direcciones(exc, "Consulta direcciones_entrega")
        filas = None
    if filas is None:
        bolsa[cas] = previa
        return bolsa[cas]
    dirs_db = [
        {"id": d[0], "etiqueta": d[1], "receptor": d[2], "ciudad": d[3], "direccion": d[4]}
        for d in filas
    ]
    claves_db = {(e["etiqueta"], e["ciudad"]) for e in dirs_db}
    extras_sesion = [
        e
        for e in previa
        if not e.get("id") and (e.get("etiqueta"), e.get("ciudad")) not in claves_db
    ]
    combinadas = dirs_db + extras_sesion
    bolsa[cas] = combinadas
    for clave in coincidencias_casillero(casillero):
        if clave != cas:
            bolsa.pop(clave, None)
    return combinadas


def opciones_entrega_desde_sesion(casillero):
    """Reconstruye el desplegable desde SQLite en cada run: almacén → direcciones activas → Crear Nueva."""
    opciones = [OPCION_PREDETERMINADA]
    for e in direcciones_sesion(casillero):
        opciones.append(f"📍 {e['etiqueta']} - {e['ciudad']}")
    opciones.append("➕ Crear Nueva Dirección de Envío")
    return opciones


def guardar_nueva_direccion(casillero):
    """on_click de Guardar Dirección: lee los widgets en el clic, INSERT + COMMIT inmediato."""
    etiqueta = (st.session_state.get("dir_etiqueta_in") or "").strip()
    receptor = (st.session_state.get("dir_receptor_in") or "").strip()
    tel = (st.session_state.get("dir_tel_in") or "").strip()
    dep = (st.session_state.get("sb_dep_nueva_dir") or "").strip()
    ciu = (st.session_state.get("sb_ciu_nueva_dir") or "").strip()
    dir_exacta = (st.session_state.get("dir_exacta_in") or "").strip()
    if not (etiqueta and receptor and tel and dep and ciu and dir_exacta):
        st.session_state["_dir_form_error"] = "Completa todos los campos obligatorios (*)."
        return
    cas_norm = formatear_casillero(casillero)
    f_ahora = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    id_dir_nuevo = None
    error_db = None
    asegurar_esquema_direcciones()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO direcciones_entrega (codigo_casillero, etiqueta, receptor_nombre, telefono, departamento, ciudad, direccion_exacta, fecha_creacion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cas_norm, etiqueta, receptor, tel, dep, ciu, dir_exacta, f_ahora),
            )
            id_dir_nuevo = cur.lastrowid
            conn.commit()
            if not id_dir_nuevo:
                raise sqlite3.Error("INSERT direcciones_entrega no devolvió lastrowid.")
    except Exception as exc:
        id_dir_nuevo = None
        error_db = str(exc)
        registrar_error_direcciones(exc, "INSERT direcciones_entrega")
    invalidar_cache_direcciones()
    opcion_nueva = f"📍 {etiqueta} - {ciu}"
    if error_db:
        direcciones_sesion(cas_norm).append(
            {
                "id": None,
                "etiqueta": etiqueta,
                "receptor": receptor,
                "telefono": tel,
                "departamento": dep,
                "ciudad": ciu,
                "direccion": dir_exacta,
            }
        )
    else:
        direcciones_sesion(cas_norm)
    seleccionar_modalidad_entrega(opcion_nueva)
    st.session_state["destino_entrega_activo"] = opcion_nueva
    st.session_state["_dir_form_exito"] = f"Dirección '{etiqueta}' guardada y seleccionada como destino."
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)
    st.toast(f"✅ Dirección '{etiqueta}' guardada y seleccionada.")


def destino_para_documentos():
    """Destino imprimible en PDFs y fichas: nunca la opción «Crear Nueva Dirección»."""
    mod = st.session_state.get("modalidad_envio_seleccionada")
    if mod and mod != "➕ Crear Nueva Dirección de Envío":
        return mod
    return st.session_state.get("destino_entrega_activo") or OPCION_PREDETERMINADA


def eliminar_direccion_usuario(casillero, etiqueta, ciudad, id_dir=None):
    """Quita la dirección de la colección en memoria y de SQLite (si existe la fila)."""
    if id_dir:
        claves = coincidencias_casillero(casillero)
        placeholders = ",".join("?" * len(claves)) if claves else "?"
        params = (id_dir, *(claves or (formatear_casillero(casillero),)))
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"DELETE FROM direcciones_entrega WHERE id = ? AND codigo_casillero IN ({placeholders})",
                    params,
                )
                conn.commit()
        except Exception as exc:
            registrar_error_direcciones(exc, "DELETE direcciones_entrega")
        invalidar_cache_direcciones()
    lista = direcciones_sesion(casillero)
    lista[:] = [
        e for e in lista if not (e.get("etiqueta") == etiqueta and e.get("ciudad") == ciudad)
    ]
    opcion_borrada = f"📍 {etiqueta} - {ciudad}"
    if st.session_state.get("destino_entrega_activo") == opcion_borrada:
        st.session_state["destino_entrega_activo"] = OPCION_PREDETERMINADA
    if st.session_state.get("modalidad_envio_seleccionada") == opcion_borrada:
        seleccionar_modalidad_entrega(OPCION_PREDETERMINADA)


def cancelar_nueva_direccion():
    """on_click de Cancelar: cierra el formulario y vuelve al destino previo (o al almacén)."""
    seleccionar_modalidad_entrega(st.session_state.get("destino_entrega_activo") or OPCION_PREDETERMINADA)
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)


def selector_modalidad_entrega(opciones_modalidad):
    """Select de entrega en el cuerpo del Cotizador (fuera del header sticky, sin CSS sobre Baseweb)."""
    pendiente = st.session_state.pop("_mod_entrega_pendiente", None)
    if pendiente in opciones_modalidad:
        st.session_state["sb_modalidad_entrega"] = pendiente
        st.session_state["modalidad_envio_seleccionada"] = pendiente
    elif pendiente:
        st.session_state["_mod_entrega_pendiente"] = pendiente
        if st.session_state.get("sb_modalidad_entrega") == "➕ Crear Nueva Dirección de Envío":
            st.session_state["sb_modalidad_entrega"] = OPCION_PREDETERMINADA
    idx_mod = opciones_modalidad.index(st.session_state["modalidad_envio_seleccionada"])
    sel_kwargs = {"key": "sb_modalidad_entrega"}
    if "sb_modalidad_entrega" not in st.session_state:
        sel_kwargs["index"] = idx_mod
    elif st.session_state.get("sb_modalidad_entrega") not in opciones_modalidad:
        st.session_state["sb_modalidad_entrega"] = opciones_modalidad[idx_mod]
    mod_elegida = st.selectbox(
        "🏪 ¿Cómo deseas recibir tu compra?",
        opciones_modalidad,
        on_change=al_cambiar_modalidad_entrega,
        **sel_kwargs,
    )
    previa = st.session_state.get("modalidad_envio_seleccionada")
    st.session_state["modalidad_envio_seleccionada"] = mod_elegida
    if mod_elegida != "➕ Crear Nueva Dirección de Envío":
        st.session_state["destino_entrega_activo"] = mod_elegida
    if (
        st.session_state.get("_mod_entrega_lista")
        and previa is not None
        and mod_elegida != previa
    ):
        invalidar_emision_visible_cotizador()
    st.session_state["_mod_entrega_lista"] = True


ALIAS_VISTA = {
    "Fichas": "Etiqueta",
    "Mis cotizaciones": "Mis Cotizaciones",
    "Mis envíos": "Mis Envíos",
    "Envíos": "Mis Envíos",
}


def ir_a(vista, hub="_omit"):
    """Cambia de vista en session_state. Pensado para on_click (un solo rerun)."""
    if vista == "Cerrar":
        logout()
        return
    vista = ALIAS_VISTA.get(vista, vista)
    if vista in VISTAS_MODULO and not usuario_puede_modulo(vista):
        vista = "Inicio"
        hub = None
    if hub != "_omit":
        st.session_state["hub"] = hub
    elif vista in MODULOS_POR_ID:
        st.session_state["hub"] = MODULOS_POR_ID[vista]
    if st.session_state.get("hub") and not usuario_puede_hub(st.session_state["hub"]):
        st.session_state["hub"] = None
        if vista != "Inicio":
            vista = "Inicio"
    st.session_state["sub_tab_inicio"] = vista
    st.session_state["vista_activa"] = vista
    cas = formatear_casillero(st.session_state.get("casillero", ""))
    if cas:
        st.query_params["casillero"] = cas
    st.query_params["vista"] = vista
    hub_actual = st.session_state.get("hub")
    if hub_actual:
        st.query_params["hub"] = hub_actual
    elif "hub" in st.query_params:
        del st.query_params["hub"]


def ir_a_inicio():
    ir_a("Inicio", hub=None)


def ir_a_catalogo():
    ir_a("Catálogo", hub="china")


def ir_a_mis_cotizaciones():
    ir_a("Mis Cotizaciones", hub="china")


def ir_a_cotizador():
    avanzar_guia_si(1, 2)
    ir_a("Cotizador", hub="china")


def ir_a_mas():
    ir_a("Más")


def iniciar_guia_desde_mas():
    st.session_state["mostrar_guia"] = True
    iniciar_guia_interactiva(1)
    ir_a("Inicio", hub="china")


def ir_a_envios():
    ir_a("Mis Envíos", hub="china")


def ir_a_fichas():
    ir_a("Fichas", hub="china")


def ir_a_envios_de_cotizacion(id_cot):
    try:
        st.session_state["cotizacion_envio_foco"] = int(id_cot)
    except (TypeError, ValueError):
        st.session_state.pop("cotizacion_envio_foco", None)
    st.session_state["china_modulos_desbloqueados"] = True
    avanzar_guia_si(6, completar=True)
    ir_a("Mis Envíos", hub="china")


def ir_a_historial_guia(id_cot):
    if guia_paso_actual() in (3, 4):
        st.session_state["guia_paso"] = 5
    ir_a_cotizacion_emitida(id_cot)


def on_confirmar_cot_historial(id_cot, casillero):
    if confirmar_cotizacion_casillero(id_cot, casillero):
        st.session_state["china_modulos_desbloqueados"] = True
        try:
            st.session_state["cotizacion_envio_foco"] = int(id_cot)
        except (TypeError, ValueError):
            pass
        if int(st.session_state.get("cotizacion_historial_foco") or 0) == int(id_cot or 0):
            st.session_state.pop("cotizacion_historial_foco", None)
        avanzar_guia_si(5, 6)


def ir_a_cotizacion_emitida(id_cot):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        cid = 0
    if cid:
        st.session_state["cotizacion_historial_foco"] = cid
        st.session_state["ultima_cot_id"] = cid
    ir_a("Mis Cotizaciones", hub="china")


PASOS_GUIA_INTERACTIVA = (
    {
        "paso": 1,
        "titulo": "Acceso al Cotizador",
        "texto": "Pulse <b>Cotizador</b> en la barra inferior para calcular el flete de su carga.",
    },
    {
        "paso": 2,
        "titulo": "Emisión de la tarifa",
        "texto": "Complete medidas y peso. Luego pulse <b>Confirmar Tarifa &amp; Emitir Documentos</b> para generar la tarifa.",
    },
    {
        "paso": 3,
        "titulo": "Documento del proveedor",
        "texto": "Descarga este archivo y envíalo a tu proveedor en China para rotular el paquete.",
    },
    {
        "paso": 4,
        "titulo": "Traslado al historial",
        "texto": "Pasa a tus cotizaciones para asegurar tu tarifa antes de 24 horas.",
    },
    {
        "paso": 5,
        "titulo": "Consolidación de tarifa",
        "texto": "Pulse <b>Confirmar Cotización</b> en la tarifa resaltada para dejarla permanente en su casillero.",
    },
    {
        "paso": 6,
        "titulo": "Gestión en Envíos",
        "texto": "¡Listo! Ahora puedes dar seguimiento a tu paquete y descargar tu ficha/etiqueta y comprobante de tarifa.",
    },
)


def guia_esta_activa():
    return bool(st.session_state.get("guia_activa"))


def guia_paso_actual():
    try:
        return int(st.session_state.get("guia_paso") or 0)
    except (TypeError, ValueError):
        return 0


def iniciar_guia_interactiva(paso=1):
    st.session_state["guia_activa"] = True
    st.session_state["guia_omitida"] = False
    st.session_state["guia_completada"] = False
    st.session_state["guia_paso"] = int(paso)
    st.session_state["guia_china_auto_vista"] = True
    st.session_state["abrir_guia_rapida"] = False
    for clave in list(st.session_state.keys()):
        if str(clave).startswith("dl_pdf_fab_"):
            st.session_state[clave] = False


def omitir_guia_interactiva():
    st.session_state["guia_activa"] = False
    st.session_state["mostrar_guia"] = False
    st.session_state["guia_omitida"] = True
    st.session_state["guia_paso"] = 0
    st.session_state["abrir_guia_rapida"] = False


def completar_guia_interactiva():
    st.session_state["guia_activa"] = False
    st.session_state["mostrar_guia"] = False
    st.session_state["guia_completada"] = True
    st.session_state["guia_paso"] = 0


def avanzar_guia_si(paso_esperado, siguiente=None, completar=False):
    if not guia_esta_activa() or guia_paso_actual() != int(paso_esperado):
        return
    if completar:
        completar_guia_interactiva()
        return
    if siguiente is not None:
        st.session_state["guia_paso"] = int(siguiente)


def detectar_avance_descarga_guia():
    if not guia_esta_activa() or guia_paso_actual() != 3:
        return
    for clave in list(st.session_state.keys()):
        if str(clave).startswith("dl_pdf_fab_") and st.session_state.get(clave):
            avanzar_guia_si(3, 4)
            return


def html_globo_guia():
    actual = guia_paso_actual()
    dato = next((p for p in PASOS_GUIA_INTERACTIVA if p["paso"] == actual), None)
    if not dato:
        return ""
    return (
        f'<div class="guia-globo ccm-guia-card">'
        f'<div class="guia-globo-kicker">Paso {dato["paso"]} de 6</div>'
        f'<div class="guia-globo-titulo">{dato["titulo"]}</div>'
        f'<p class="guia-globo-txt">{dato["texto"]}</p>'
        f"</div>"
    )


def aplicar_clase_guia_js():
    paso = guia_paso_actual() if guia_esta_activa() else 0
    components.html(
        f"""
        <script>
        (function () {{
          const doc = window.parent.document;
          const aplicar = () => {{
            const app = doc.querySelector(".stApp") || doc.body;
            if (!app) return;
            for (let n = 1; n <= 6; n++) app.classList.remove("guia-paso-" + n);
            app.classList.toggle("guia-activa", {str(paso > 0).lower()});
            if ({paso}) app.classList.add("guia-paso-{paso}");
            doc.querySelectorAll(".ccm-guia-pulse").forEach((el) => el.classList.remove("ccm-guia-pulse"));
            const mapa = {{
              1: [".st-key-mod_cotizador button", ".st-key-bnav_cotizador button", ".st-key-nav_mod_cotizador button", "[class*='st-key-mod_cotizador'] button", "[class*='st-key-bnav_cotizador'] button"],
              2: [".st-key-guia_foco_tarifa button", ".st-key-btn_confirmar_tarifa button", "[class*='btn_confirmar_tarifa'] button"],
              3: [".st-key-guia_foco_pdf_fab button", "[class*='dl_pdf_fab_'] button"],
              4: [".st-key-guia_foco_ver_cot button", "[class*='btn_ver_mis_cotizaciones_'] button"],
              5: ["[class*='st-key-foco_confirmar_'] button", "[class*='btn_confirmar_cot_'] button"],
              6: ["[class*='st-key-foco_ir_envios_'] button", "[class*='btn_ir_envios_'] button"]
            }};
            (mapa[{paso}] || []).forEach((sel) => {{
              doc.querySelectorAll(sel).forEach((el) => el.classList.add("ccm-guia-pulse"));
            }});
          }};
          aplicar();
          setTimeout(aplicar, 80);
          setTimeout(aplicar, 280);
          setTimeout(aplicar, 800);
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def guia_tarjeta_visible():
    vista_actual = st.session_state.get("sub_tab_inicio") or st.session_state.get("vista_activa")
    return bool(
        st.session_state.get("mostrar_guia")
        and guia_esta_activa()
        and not st.session_state.get("guia_omitida")
        and not st.session_state.get("guia_completada")
        and vista_actual == "Inicio"
        and st.session_state.get("hub") == "china"
    )


@st.fragment
def pintar_coach_guia():
    aplicar_clase_guia_js()
    with st.container(key="guia_coach"):
        if not guia_tarjeta_visible():
            st.markdown('<div class="ccm-guia-vacia" aria-hidden="true"></div>', unsafe_allow_html=True)
            return
        st.markdown(html_globo_guia(), unsafe_allow_html=True)
        if st.button("Omitir Guía", type="secondary", key="btn_omitir_guia", on_click=omitir_guia_interactiva):
            pass


def disparar_guia_china_si_aplica():
    return


def proximo_cierre_contenedor(ahora=None):
    ahora = ahora or obtener_tiempo_honduras()
    dias = (4 - ahora.weekday()) % 7
    if dias == 0 and ahora.hour >= 17:
        dias = 7
    cierre = ahora + timedelta(days=dias)
    dia = DIAS_SEMANA_ES.get(cierre.weekday(), "")
    mes = MESES_ES.get(cierre.month, "")
    return f"{dia} {cierre.day} {mes} {cierre.year}"


@st.fragment
def pintar_banner_promocional_china(casillero):
    cas_txt = formatear_casillero(casillero) or "su casillero"
    cierre = proximo_cierre_contenedor()
    msg = urllib.parse.quote(
        f"Hola Centro de Cerámicas y Más, soy del casillero {cas_txt}. "
        "Quiero consultar la promoción de consolidación marítima China → Honduras "
        f"y el cierre de contenedor del {cierre}."
    )
    url_wa = f"https://wa.me/50495771099?text={msg}"
    st.markdown(
        f'<div class="promo-ad-card">'
        f'<div class="promo-ad-kicker">Promoción vigente · Casillero {cas_txt}</div>'
        f'<div class="promo-ad-title">Tarifa especial de consolidación marítima</div>'
        f'<div class="promo-ad-body">'
        f"Reserve cupo en el contenedor 40&prime; HC China ➔ Honduras. "
        f"Próximo cierre: <b>{cierre}</b>. Paquetería por libra o carga comercial por CBM, "
        f"con asesoría de casillero incluida."
        f"</div>"
        f'<div class="promo-ad-pills">'
        f'<span class="promo-ad-pill">Cierre {cierre}</span>'
        f'<span class="promo-ad-pill">Cerámica y carga mixta</span>'
        f'<span class="promo-ad-pill">Asesor CCM</span>'
        f"</div>"
        f'<a class="promo-ad-cta" href="{url_wa}" target="_blank" rel="noopener noreferrer">'
        f"Consultar Promoción</a>"
        f"</div>",
        unsafe_allow_html=True,
    )


CLAVES_WIDGET_PERFIL = (
    "perfil_nom",
    "perfil_tel",
    "perfil_dep",
    "perfil_ciu",
    "perfil_dir",
    "perfil_dni",
)


def cargar_perfil_usuario(casillero):
    cas = formatear_casillero(casillero)
    if not cas:
        return None
    claves = coincidencias_casillero(cas)
    placeholders = ",".join("?" * len(claves))
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT codigo_casillero, nombre_completo, dni, correo_principal,
                   telefono_principal, departamento, ciudad, direccion_exacta
            FROM usuarios
            WHERE codigo_casillero IN ({placeholders}) AND activo = 1
            """,
            claves,
        )
        row = c.fetchone()
    if not row:
        return None
    return {
        "casillero": formatear_casillero(row[0]),
        "nombre": row[1] or "",
        "dni": row[2] or "",
        "correo": row[3] or "",
        "telefono": row[4] or "",
        "departamento": row[5] or "",
        "ciudad": row[6] or "",
        "direccion": row[7] or "",
    }


def aplicar_perfil_en_sesion(perfil):
    if not perfil:
        return
    st.session_state["casillero"] = perfil["casillero"]
    st.session_state["nombre"] = perfil["nombre"]
    st.session_state["dni"] = perfil["dni"]
    st.session_state["usuario"] = perfil["correo"]
    st.session_state["telefono"] = perfil["telefono"]
    st.session_state["departamento"] = perfil["departamento"]
    st.session_state["ciudad"] = perfil["ciudad"]
    st.session_state["direccion_exacta"] = perfil["direccion"]


def persistir_perfil_usuario(casillero, nombre, telefono, departamento, ciudad, direccion, dni):
    actual = cargar_perfil_usuario(casillero)
    if not actual:
        return False, "No se encontró el casillero."
    nombre = (nombre or "").strip()
    telefono = (telefono or "").strip()
    departamento = (departamento or "").strip()
    ciudad = (ciudad or "").strip()
    direccion = (direccion or "").strip()
    dni = (dni or "").strip()
    dni_dig = "".join(filter(str.isdigit, dni))
    if not nombre:
        return False, "Ingrese el nombre completo."
    if not telefono:
        return False, "Ingrese el teléfono / WhatsApp."
    if not departamento or not ciudad or not direccion:
        return False, "Ingrese departamento, ciudad y dirección de entrega."
    if len(dni_dig) < 8:
        return False, "Ingrese un DNI válido (mínimo 8 dígitos)."
    claves = coincidencias_casillero(actual["casillero"])
    placeholders = ",".join("?" * len(claves))
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT 1 FROM usuarios
            WHERE dni = ? AND codigo_casillero NOT IN ({placeholders})
            LIMIT 1
            """,
            (dni, *claves),
        )
        if c.fetchone():
            return False, "Ese DNI ya está registrado en otro casillero."
        c.execute(
            f"""
            UPDATE usuarios
            SET nombre_completo = ?, dni = ?, telefono_principal = ?,
                departamento = ?, ciudad = ?, direccion_exacta = ?
            WHERE codigo_casillero IN ({placeholders})
            """,
            (nombre, dni, telefono, departamento, ciudad, direccion, *claves),
        )
        conn.commit()
    aplicar_perfil_en_sesion(
        {
            **actual,
            "nombre": nombre,
            "dni": dni,
            "telefono": telefono,
            "departamento": departamento,
            "ciudad": ciudad,
            "direccion": direccion,
        }
    )
    return True, "¡Datos actualizados exitosamente!"


@st.dialog("Editar Perfil", width="large")
def dialogo_editar_perfil():
    perfil = cargar_perfil_usuario(st.session_state.get("casillero"))
    if not perfil:
        st.error("No se encontró el perfil de este casillero.")
        return
    with st.container(key="dialogo_perfil"):
        st.markdown(
            '<p class="perfil-dialog-nota">El correo electrónico y el código de casillero no se pueden modificar.</p>',
            unsafe_allow_html=True,
        )
        c_nom, c_tel = st.columns(2, gap="medium")
        with c_nom:
            nom = st.text_input("Nombre completo", value=perfil["nombre"], key="perfil_nom")
        with c_tel:
            tel = st.text_input("Teléfono / WhatsApp", value=perfil["telefono"], key="perfil_tel")
        c_dep, c_ciu = st.columns(2, gap="medium")
        with c_dep:
            dep = st.text_input("Departamento", value=perfil["departamento"], key="perfil_dep")
        with c_ciu:
            ciu = st.text_input("Ciudad", value=perfil["ciudad"], key="perfil_ciu")
        dir_e = st.text_area("Dirección de entrega", value=perfil["direccion"], key="perfil_dir")
        dni = st.text_input("Número de Identidad / DNI", value=perfil["dni"], key="perfil_dni")
        c_mail, c_cas = st.columns(2, gap="medium")
        with c_mail:
            st.text_input("Correo electrónico", value=perfil["correo"], disabled=True, key="perfil_mail")
        with c_cas:
            st.text_input("Código de casillero", value=perfil["casillero"], disabled=True, key="perfil_cas")
        c_cancel, c_ok = st.columns(2, gap="small")
        with c_cancel:
            if st.button("Cancelar", type="secondary", key="perfil_cancelar", use_container_width=True):
                for k in CLAVES_WIDGET_PERFIL:
                    st.session_state.pop(k, None)
                st.rerun()
        with c_ok:
            if st.button("Guardar Cambios", type="primary", key="perfil_guardar", use_container_width=True):
                ok, msg = persistir_perfil_usuario(
                    perfil["casillero"], nom, tel, dep, ciu, dir_e, dni
                )
                if ok:
                    st.session_state["flash_perfil"] = msg
                    for k in CLAVES_WIDGET_PERFIL:
                        st.session_state.pop(k, None)
                    st.rerun()
                st.error(msg)


def abrir_dialogo_editar_perfil():
    for k in CLAVES_WIDGET_PERFIL:
        st.session_state.pop(k, None)
    dialogo_editar_perfil()


def pintar_vista_mas():
    hub_activo = st.session_state.get("hub")
    mostrar_btn_guia = hub_activo == "china"
    perfil = cargar_perfil_usuario(st.session_state.get("casillero"))
    if perfil:
        aplicar_perfil_en_sesion(perfil)
        nombre = perfil["nombre"] or "Cliente"
        cas = perfil["casillero"] or "—"
        correo = perfil["correo"] or "—"
    else:
        nombre = st.session_state.get("nombre") or "Cliente"
        cas = formatear_casillero(st.session_state.get("casillero", "")) or "—"
        correo = st.session_state.get("usuario") or "—"

    with st.container(key="vista_mas"):
        aviso = st.session_state.pop("flash_perfil", None)
        if aviso:
            st.success(aviso)
        st.markdown('<div class="mas-seccion mas-seccion-cuenta">Cuenta</div>', unsafe_allow_html=True)
        with st.container(key="mas_cuenta"):
            st.markdown(
                f'<div class="mas-cuenta">'
                f'<div class="mas-cuenta-nombre">{html.escape(nombre)}</div>'
                f'<div class="mas-cuenta-cas">Casillero {html.escape(cas)}</div>'
                f'<div class="mas-cuenta-mail">{html.escape(correo)}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button("✏️  Editar perfil", key="mas_editar_perfil", use_container_width=True):
                abrir_dialogo_editar_perfil()
        st.markdown('<div class="mas-seccion mas-seccion-modulos">Módulos y operaciones</div>', unsafe_allow_html=True)
        with st.container(key="mas_modulos"):
            st.button("📦  Mis envíos", key="mas_envios", use_container_width=True, on_click=ir_a_envios)
            st.button("📋  Fichas", key="mas_fichas", use_container_width=True, on_click=ir_a_fichas)
            st.button("📄  Mis Cotizaciones", key="mas_cotizaciones", use_container_width=True, on_click=ir_a_mis_cotizaciones)
            st.button("🛍️  Catálogo", key="mas_catalogo", use_container_width=True, on_click=ir_a_catalogo)
            st.button("🧮  Cotizador", key="mas_cotizador", use_container_width=True, on_click=ir_a_cotizador)
        with st.container(key="mas_sesion"):
            st.markdown('<div class="mas-seccion">Sistema / Sesión</div>', unsafe_allow_html=True)
            if mostrar_btn_guia:
                st.button(
                    "Guía",
                    type="secondary",
                    key="btn_guia_rapida",
                    use_container_width=True,
                    on_click=iniciar_guia_desde_mas,
                )
            if st.button("⏻  Cerrar sesión", type="secondary", key="btn_logout_cliente", use_container_width=True):
                ir_a("Cerrar")
        espaciador_barra_inferior("safe_mas")


def espaciador_barra_inferior(clave):
    with st.container(key=clave):
        st.markdown("&nbsp;")


def pintar_barra_inferior(total_cotizaciones=0):
    vista = st.session_state.get("vista_activa") or st.session_state.get("sub_tab_inicio") or "Inicio"
    inicio_activo = vista == "Inicio"
    catalogo_activo = vista == "Catálogo"
    cot_activo = vista in ("Mis Cotizaciones", "Mis Envíos", "Etiqueta")
    cotizador_activo = vista == "Cotizador"
    mas_activo = vista in ("Más", "Configuración", "Consultas")
    n_badge = int(total_cotizaciones or 0)

    st.markdown(
        f"<style>:root {{ --ccm-cot-badge: \"{n_badge}\"; }}</style>",
        unsafe_allow_html=True,
    )

    items = (
        ("inicio", "🏠", "Inicio", inicio_activo),
        ("catalogo", "🔍", "Catálogo", catalogo_activo),
        ("cotizaciones", "📄", "Cotiz.", cot_activo),
        ("cotizador", "🧮", "Cotizador", cotizador_activo),
        ("mas", "☰", "Más", mas_activo),
    )
    with st.container(key="bottom_nav"):
        cols = st.columns(5, gap="small")
        for col, (dest, icono, etiqueta, activo) in zip(cols, items):
            with col:
                if dest == "inicio":
                    st.button(
                        f"{icono}\n{etiqueta}",
                        type="primary" if activo else "secondary",
                        key=f"bnav_{dest}",
                        use_container_width=True,
                        on_click=ir_a_inicio,
                    )
                elif dest == "catalogo":
                    st.button(
                        f"{icono}\n{etiqueta}",
                        type="primary" if activo else "secondary",
                        key=f"bnav_{dest}",
                        use_container_width=True,
                        on_click=ir_a_catalogo,
                    )
                elif dest == "cotizaciones":
                    st.button(
                        f"{icono}\n{etiqueta}",
                        type="primary" if activo else "secondary",
                        key=f"bnav_{dest}",
                        use_container_width=True,
                        on_click=ir_a_mis_cotizaciones,
                    )
                elif dest == "cotizador":
                    st.button(
                        f"{icono}\n{etiqueta}",
                        type="primary" if activo else "secondary",
                        key=f"bnav_{dest}",
                        use_container_width=True,
                        on_click=ir_a_cotizador,
                    )
                else:
                    st.button(
                        f"{icono}\n{etiqueta}",
                        type="primary" if activo else "secondary",
                        key=f"bnav_{dest}",
                        use_container_width=True,
                        on_click=ir_a_mas,
                    )
    anclar_barra_inferior()


def anclar_barra_inferior():
    with st.container(key="bottom_nav_pin"):
        components.html(
            """
            <script>
            (function () {
              const doc = window.parent.document;
              const win = window.parent;
              const nodoNav = () =>
                doc.querySelector('[class~="st-key-bottom_nav"]') ||
                doc.querySelector(".st-key-bottom_nav");
              const anclar = () => {
                const nav = nodoNav();
                if (nav) {
                  nav.style.setProperty("position", "fixed", "important");
                  nav.style.setProperty("bottom", "20px", "important");
                  nav.style.setProperty("left", "50%", "important");
                  nav.style.setProperty("right", "auto", "important");
                  nav.style.setProperty("margin", "0", "important");
                  nav.style.setProperty("transform", "translateX(-50%)", "important");
                  nav.style.setProperty("z-index", "9999", "important");
                  nav.style.setProperty("width", "min(96vw, 520px)", "important");
                  nav.style.setProperty("max-width", "520px", "important");
                  const cajaNav = nav.closest('[data-testid="stElementContainer"]') || nav.parentElement;
                  if (cajaNav && cajaNav !== doc.body) {
                    cajaNav.style.setProperty("height", "0", "important");
                    cajaNav.style.setProperty("min-height", "0", "important");
                    cajaNav.style.setProperty("margin", "0", "important");
                    cajaNav.style.setProperty("padding", "0", "important");
                    cajaNav.style.setProperty("overflow", "visible", "important");
                    cajaNav.style.setProperty("border", "0", "important");
                  }
                }
                const mas = doc.querySelector('[class~="st-key-vista_mas"]') || doc.querySelector(".st-key-vista_mas");
                const inicio = doc.querySelector('[class~="st-key-vista_inicio"]') || doc.querySelector(".st-key-vista_inicio");
                const catalogo = doc.querySelector('[class~="st-key-vista_catalogo"]') || doc.querySelector(".st-key-vista_catalogo");
                const cotizador = doc.querySelector('[class~="st-key-vista_cotizador"]') || doc.querySelector(".st-key-vista_cotizador");
                const logout = doc.querySelector('[class~="st-key-btn_logout_cliente"]') ||
                  doc.querySelector(".st-key-btn_logout_cliente") ||
                  Array.from(doc.querySelectorAll("button")).find((b) => (b.textContent || "").indexOf("Cerrar sesión") >= 0);
                const accion = doc.querySelector('[class~="st-key-btn_confirmar_tarifa"]') ||
                  doc.querySelector(".st-key-btn_confirmar_tarifa") ||
                  doc.querySelector('[class~="st-key-btn_buscar_china"]') ||
                  doc.querySelector(".st-key-btn_buscar_china") ||
                  doc.querySelector('[class~="st-key-btn_escanear_catalogo"]') ||
                  doc.querySelector(".st-key-btn_escanear_catalogo");
                const vistaModulo = catalogo || cotizador;
                const historial = doc.querySelector('[class~="st-key-vista_historial"]') || doc.querySelector(".st-key-vista_historial");
                let hueco = "calc(200px + env(safe-area-inset-bottom, 0px))";
                if (mas || vistaModulo || inicio) hueco = "0px";
                else if (historial) hueco = "calc(var(--ccm-nav-clearance, 109px) + 16px)";
                doc.querySelectorAll(".block-container, [data-testid='stMainBlockContainer'], .stMainBlockContainer, [data-testid='stAppViewBlockContainer']").forEach((el) => {
                  el.style.setProperty("padding-bottom", hueco, "important");
                });
                if (inicio) {
                  inicio.style.setProperty("padding-bottom", "180px", "important");
                  inicio.style.setProperty("box-sizing", "border-box", "important");
                }
                const GAP_OBJETIVO = 12;
                const esVistaMas = (nodo) =>
                  !!(nodo && ((nodo.className || "").indexOf("st-key-vista_mas") >= 0));
                if (vistaModulo || mas) {
                  doc.querySelectorAll("[data-testid='stBottomBlockContainer']").forEach((el) => {
                    el.style.setProperty("min-height", "0px", "important");
                    el.style.setProperty("padding-top", "0px", "important");
                    el.style.setProperty("padding-bottom", "0px", "important");
                    el.style.setProperty("margin-top", "0px", "important");
                    el.style.setProperty("margin-bottom", "0px", "important");
                  });
                }
                const dockVista = (caja, ancla) => {
                  if (!caja || !nav) return;
                  const navCaja = nav.getBoundingClientRect();
                  const app = doc.querySelector(".stApp") || doc.documentElement;
                  const isMas = esVistaMas(caja);
                  if (isMas) {
                    caja.style.setProperty("min-height", "0px", "important");
                    caja.style.setProperty("box-sizing", "border-box", "important");
                    if (!ancla) return;
                    const scrollTop = app.scrollTop || 0;
                    const maxScroll = Math.max(0, (app.scrollHeight || 0) - (app.clientHeight || 0));
                    const currentPad = parseFloat(win.getComputedStyle(caja).paddingBottom) || 0;
                    const anclaNow = ancla.getBoundingClientRect().bottom;
                    const anclaAtMax = anclaNow + scrollTop - maxScroll;
                    const objetivo = navCaja.top - GAP_OBJETIVO;
                    let huecoNav = Math.round(currentPad + (anclaAtMax - objetivo));
                    huecoNav = Math.max(8, Math.min(240, huecoNav));
                    caja.style.setProperty("padding-bottom", huecoNav + "px", "important");
                    return;
                  }
                  const formDir = caja.querySelector('[class~="st-key-formulario_direcciones"]') || caja.querySelector(".st-key-formulario_direcciones");
                  if (formDir) {
                    caja.style.setProperty("box-sizing", "border-box", "important");
                    caja.style.setProperty("min-height", "0px", "important");
                    caja.style.setProperty("height", "auto", "important");
                    caja.style.setProperty("padding-top", "16px", "important");
                    caja.style.setProperty("padding-bottom", "0px", "important");
                    formDir.style.setProperty("display", "flex", "important");
                    formDir.style.setProperty("flex-direction", "column", "important");
                    formDir.style.setProperty("height", "auto", "important");
                    formDir.style.setProperty("min-height", "0", "important");
                    formDir.style.setProperty("padding-bottom", "220px", "important");
                    return;
                  }
                  const form = caja.querySelector('[class~="st-key-catalogo_formulario"]') || caja.querySelector(".st-key-catalogo_formulario");
                  const host = form || caja;
                  const posteriores = [];
                  let nodoRef = form || (ancla && ancla.parentElement);
                  while (nodoRef && nodoRef !== caja) {
                    let sig = nodoRef.nextElementSibling;
                    while (sig) {
                      posteriores.push(sig);
                      sig = sig.nextElementSibling;
                    }
                    nodoRef = nodoRef.parentElement;
                  }
                  const hayMas = posteriores.some((ch) =>
                    ch.querySelector("button, a, img, [data-testid='stDownloadButton']")
                  );
                  const esCatalogo = !!(caja && ((caja.className || "").indexOf("st-key-vista_catalogo") >= 0));
                  if (esCatalogo) {
                    caja.style.setProperty("box-sizing", "border-box", "important");
                    if (form) {
                      form.style.setProperty("display", "flex", "important");
                      form.style.setProperty("flex-direction", "column", "important");
                      form.style.setProperty("width", "100%", "important");
                      form.style.setProperty("flex", "0 0 auto", "important");
                      form.style.setProperty("min-height", "0", "important");
                    }
                    const itemCat = ancla ? Array.from((form || caja).children).find((ch) => ch.contains(ancla)) : null;
                    if (itemCat) itemCat.style.setProperty("margin-top", "0", "important");
                    if (hayMas) {
                      caja.style.setProperty("justify-content", "flex-start", "important");
                      caja.style.setProperty("padding-bottom", "180px", "important");
                      caja.style.setProperty("min-height", "0px", "important");
                    } else {
                      caja.style.setProperty("justify-content", "center", "important");
                      caja.style.setProperty("padding-top", "0px", "important");
                      caja.style.setProperty("padding-bottom", "calc(var(--ccm-nav-clearance, 109px) + 16px)", "important");
                      const cajaTop = Math.max(0, caja.getBoundingClientRect().top);
                      const minH = Math.max(0, Math.round(win.innerHeight - cajaTop));
                      caja.style.setProperty("min-height", minH + "px", "important");
                    }
                    return;
                  }
                  if (form) {
                    form.style.setProperty("display", "flex", "important");
                    form.style.setProperty("flex-direction", "column", "important");
                    form.style.setProperty("width", "100%", "important");
                    form.style.setProperty("flex", hayMas ? "0 0 auto" : "1 1 auto", "important");
                    form.style.setProperty("min-height", hayMas ? "0" : "100%", "important");
                  }
                  if (!hayMas && form) {
                    const cadena = [];
                    let p = form.parentElement;
                    while (p && p !== caja) {
                      cadena.push(p);
                      p = p.parentElement;
                    }
                    cadena.forEach((nodo) => {
                      nodo.style.setProperty("display", "flex", "important");
                      nodo.style.setProperty("flex-direction", "column", "important");
                      nodo.style.setProperty("flex", "1 1 auto", "important");
                      nodo.style.setProperty("min-height", "0", "important");
                      nodo.style.setProperty("width", "100%", "important");
                    });
                  }
                  const hijosHost = Array.from(host.children);
                  const item = ancla ? hijosHost.find((ch) => ch.contains(ancla)) : null;
                  if (form) form.style.setProperty("flex", hayMas ? "0 0 auto" : "1 1 auto", "important");
                  if (item) {
                    if (hayMas) item.style.setProperty("margin-top", "0", "important");
                    else item.style.setProperty("margin-top", "auto", "important");
                  }
                  caja.style.setProperty("box-sizing", "border-box", "important");
                  const emitAcciones = caja.querySelector('[class~="st-key-acciones_emit_cotizador"]') ||
                    caja.querySelector(".st-key-acciones_emit_cotizador");
                  if (emitAcciones) {
                    caja.style.setProperty("display", "flex", "important");
                    caja.style.setProperty("flex-direction", "column", "important");
                    caja.style.setProperty("justify-content", "flex-start", "important");
                    caja.style.setProperty("padding-top", "16px", "important");
                    caja.style.setProperty("padding-bottom", "calc(var(--ccm-nav-clearance, 109px) + 16px)", "important");
                    const cajaTopEmit = Math.max(0, caja.getBoundingClientRect().top);
                    caja.style.setProperty("min-height", Math.max(0, Math.round(win.innerHeight - cajaTopEmit)) + "px", "important");
                    emitAcciones.style.setProperty("margin-top", "auto", "important");
                    emitAcciones.style.setProperty("margin-bottom", "0px", "important");
                    const cadenaEmit = [];
                    let pEmit = emitAcciones.parentElement;
                    while (pEmit && pEmit !== caja) {
                      cadenaEmit.push(pEmit);
                      pEmit = pEmit.parentElement;
                    }
                    cadenaEmit.forEach((nodo) => {
                      nodo.style.setProperty("display", "flex", "important");
                      nodo.style.setProperty("flex-direction", "column", "important");
                      nodo.style.setProperty("flex", "1 1 auto", "important");
                      nodo.style.setProperty("min-height", "0", "important");
                      nodo.style.setProperty("width", "100%", "important");
                    });
                    if (item) item.style.setProperty("margin-top", "0", "important");
                    return;
                  }
                  if (hayMas) {
                    caja.style.setProperty("padding-bottom", "200px", "important");
                    caja.style.setProperty("min-height", "0px", "important");
                    return;
                  }
                  const scrollTop = app.scrollTop || 0;
                  const maxScroll = Math.max(0, (app.scrollHeight || 0) - (app.clientHeight || 0));
                  const vistaLarga = maxScroll > 80;
                  if (scrollTop < 4 || !vistaLarga) {
                    const cajaTop = Math.max(0, caja.getBoundingClientRect().top);
                    const minH = Math.max(0, Math.round(win.innerHeight - cajaTop));
                    caja.style.setProperty("min-height", minH + "px", "important");
                  } else {
                    caja.style.setProperty("min-height", "0px", "important");
                  }
                  if (!ancla) return;
                  const currentPad = parseFloat(win.getComputedStyle(caja).paddingBottom) || 0;
                  const anclaNow = ancla.getBoundingClientRect().bottom;
                  const anclaAtMax = anclaNow + scrollTop - maxScroll;
                  const objetivo = navCaja.top - GAP_OBJETIVO;
                  const enPantalla = anclaNow <= win.innerHeight + 8 && anclaNow >= 40;
                  const referencia = (!vistaLarga || (scrollTop < 4 && enPantalla)) ? anclaNow : anclaAtMax;
                  let nextPad = Math.round(currentPad + (referencia - objetivo));
                  nextPad = Math.max(0, Math.min(160, nextPad));
                  caja.style.setProperty("padding-bottom", nextPad + "px", "important");
                };
                if (mas) {
                  doc.querySelectorAll("[data-testid='stMainBlockContainer'] > [data-testid='stVerticalBlock'], .stMainBlockContainer > [data-testid='stVerticalBlock']").forEach((col) => {
                    col.style.setProperty("gap", "0px", "important");
                    col.style.setProperty("row-gap", "0px", "important");
                  });
                }
                if (mas && nav) dockVista(mas, logout);
                if (!mas && vistaModulo && nav) dockVista(vistaModulo, accion);
                const chromeCss =
                  '#MainMenu, footer, [data-testid="stHeader"], [data-testid="stToolbar"],' +
                  '[data-testid="stDecoration"], [data-testid="stStatusWidget"], .stStatusWidget,' +
                  '.stDeployButton, [data-testid="stAppDeployButton"], [class*="stAppDeployButton"],' +
                  '[class*="viewerBadge"], [class*="ViewerBadge"], [data-testid="stAppHeader"], .stAppHeader,' +
                  '[data-testid="stToolbarActions"], [data-testid="stHostToolbar"], [data-testid="stHostHeader"],' +
                  '[data-testid="stAppToolbar"], .stAppToolbar, [data-testid="stMainMenu"],' +
                  '[data-testid="stHeader"] [data-testid="stBaseButton-header"], [data-testid="stHeader"] [data-testid="stBaseButton-headerNoPadding"],' +
                  '[data-testid="stHeader"] button[title="Deploy"], #recordMenuPopoverButton,' +
                  'iframe[title*="streamlit status" i], iframe[title*="streamlit cloud" i],' +
                  'a[href*="share.streamlit.io"], a[href*="streamlit.io/cloud"]' +
                  ' { display:none !important; visibility:hidden !important;' +
                  ' pointer-events:none !important; opacity:0 !important; width:0 !important; height:0 !important; }';
                const inyectarCss = (rootDoc) => {
                  if (!rootDoc || !rootDoc.documentElement) return;
                  let tag = rootDoc.getElementById("ccm-hide-chrome");
                  if (!tag) {
                    tag = rootDoc.createElement("style");
                    tag.id = "ccm-hide-chrome";
                    rootDoc.documentElement.appendChild(tag);
                  }
                  tag.textContent = chromeCss;
                };
                const docs = [doc];
                try {
                  if (win.parent && win.parent.document && win.parent.document !== doc) {
                    docs.push(win.parent.document);
                  }
                } catch (e) {}
                try {
                  if (win.top && win.top.document && win.top.document !== doc) {
                    docs.push(win.top.document);
                  }
                } catch (e) {}
                docs.forEach((rootDoc) => {
                  inyectarCss(rootDoc);
                  rootDoc.querySelectorAll(
                    '#MainMenu, footer, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"], .stStatusWidget, .stDeployButton, [data-testid="stAppDeployButton"], [class*="stAppDeployButton"], [class*="viewerBadge"], [class*="ViewerBadge"], [data-testid="stToolbarActions"], [data-testid="stHostToolbar"], [data-testid="stAppToolbar"], .stAppToolbar, [data-testid="stHeader"] [data-testid="stBaseButton-headerNoPadding"], #recordMenuPopoverButton, iframe[title*="streamlit status" i], iframe[title*="streamlit cloud" i]'
                  ).forEach((el) => {
                    if (el.closest('[data-testid="stDialog"], .stDialog, [data-st-overlay-root="true"]')) return;
                    el.style.setProperty("display", "none", "important");
                    el.style.setProperty("visibility", "hidden", "important");
                    el.style.setProperty("pointer-events", "none", "important");
                    el.style.setProperty("opacity", "0", "important");
                  });
                  const vista = rootDoc.defaultView || win;
                  rootDoc.querySelectorAll("button, a, iframe, div").forEach((el) => {
                    if (el.closest('[class~="st-key-bottom_nav"], .st-key-bottom_nav')) return;
                    if (el.closest('[data-testid="stDialog"], .stDialog, [data-st-overlay-root="true"]')) return;
                    const etiqueta = ((el.innerText || el.getAttribute("aria-label") || el.title || "") + "").replace(/\\s+/g, " ").trim();
                    if (/^(Manage app|Deploy this app|Deploy|Stop|Record a screencast|Record)$/i.test(etiqueta)) {
                      el.style.setProperty("display", "none", "important");
                      el.style.setProperty("visibility", "hidden", "important");
                      el.style.setProperty("pointer-events", "none", "important");
                      return;
                    }
                    const stilo = vista.getComputedStyle(el);
                    if (stilo.position !== "fixed" && stilo.position !== "sticky") return;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0 || r.width > 280 || r.height > 140) return;
                    if (r.right > vista.innerWidth - 180 && r.bottom > vista.innerHeight - 180) {
                      el.style.setProperty("display", "none", "important");
                      el.style.setProperty("visibility", "hidden", "important");
                      el.style.setProperty("pointer-events", "none", "important");
                    }
                  });
                });
              };
              anclar();
              setTimeout(anclar, 80);
              setTimeout(anclar, 280);
              setTimeout(anclar, 800);
              if (!win.__ccmBottomNavBound) {
                win.__ccmBottomNavBound = true;
                win.addEventListener("resize", anclar, { passive: true });
                win.addEventListener("orientationchange", anclar, { passive: true });
                const appScroll = doc.querySelector(".stApp");
                if (appScroll) {
                  appScroll.addEventListener("scroll", () => {
                    if (win.__ccmDockScrollTO) win.cancelAnimationFrame(win.__ccmDockScrollTO);
                    win.__ccmDockScrollTO = win.requestAnimationFrame(anclar);
                  }, { passive: true });
                }
                try {
                  let espera;
                  const anclarSuave = () => {
                    clearTimeout(espera);
                    espera = setTimeout(anclar, 60);
                  };
                  new MutationObserver(anclarSuave).observe(doc.body, { childList: true, subtree: true });
                } catch (e) {}
              }
            })();
            </script>
            """,
            height=0,
            scrolling=False,
        )


def sincronizar_altura_encabezado_fijo():
    with st.container(key="header_offset_sync"):
        components.html(
            """
            <script>
            (function () {
              const doc = window.parent.document;
              const win = window.parent;
              const nodosHeader = () => {
                const exactos = Array.from(doc.querySelectorAll('[class~="st-key-sticky_top_header"]'));
                return exactos.length ? exactos : Array.from(doc.querySelectorAll(".st-key-sticky_top_header"));
              };
              const medir = () => {
                let bottom = 0;
                nodosHeader().forEach((nodo) => {
                  const r = nodo.getBoundingClientRect();
                  if (r.bottom > bottom) bottom = r.bottom;
                });
                if (bottom < 40) return;
                const estilos = win.getComputedStyle(doc.documentElement);
                const gapRaw = estilos.getPropertyValue("--header-gap").trim();
                const gap = Number.parseFloat(gapRaw) || 16;
                const offset = Math.ceil(Math.max(bottom + gap, 208)) + "px";
                const destinos = [doc.documentElement, doc.body, doc.querySelector(".stApp")];
                destinos.forEach((nodo) => {
                  if (nodo && nodo.style) {
                    nodo.style.setProperty("--header-offset", offset);
                    nodo.style.setProperty("--header-box", Math.round(bottom) + "px");
                  }
                });
              };
              win.__ccmMedirHeader = medir;
              medir();
              if (win.__ccmHeaderOffsetRO) {
                try { win.__ccmHeaderOffsetRO.disconnect(); } catch (e) {}
              }
              if (typeof win.ResizeObserver === "function") {
                const ro = new win.ResizeObserver(medir);
                nodosHeader().forEach((nodo) => ro.observe(nodo));
                win.__ccmHeaderOffsetRO = ro;
              }
              if (!win.__ccmHeaderOffsetBound) {
                win.__ccmHeaderOffsetBound = true;
                win.addEventListener("resize", medir, { passive: true });
                win.addEventListener("orientationchange", medir, { passive: true });
              }
              setTimeout(medir, 80);
              setTimeout(medir, 280);
              setTimeout(medir, 800);
            })();
            </script>
            """,
            height=0,
            scrolling=False,
        )
    st.markdown('<div class="ccm-header-spacer" aria-hidden="true"></div>', unsafe_allow_html=True)


def desplazar_a_ancla(element_id, alinear="start"):
    eid = str(element_id or "").replace("\\", "").replace('"', "")
    if not eid:
        return
    bloque = "end" if alinear == "end" else "start"
    components.html(
        f"""
        <script>
        (function () {{
          const ir = () => {{
            const doc = window.parent.document;
            const win = window.parent;
            const el = doc.getElementById("{eid}");
            if (!el) return;
            if (typeof win.__ccmMedirHeader === "function") win.__ccmMedirHeader();
            let bottom = 0;
            doc.querySelectorAll('[class~="st-key-sticky_top_header"], .st-key-sticky_top_header').forEach((nodo) => {{
              const r = nodo.getBoundingClientRect();
              if (r.bottom > bottom) bottom = r.bottom;
            }});
            const estilos = win.getComputedStyle(doc.documentElement);
            const gapRaw = estilos.getPropertyValue("--header-gap").trim();
            const gap = Number.parseFloat(gapRaw) || 16;
            const margen = Math.max(bottom + gap, Number.parseFloat(estilos.getPropertyValue("--header-offset")) || 0, 196);
            const nav = doc.querySelector('[class~="st-key-bottom_nav"]') || doc.querySelector(".st-key-bottom_nav");
            let huecoNav = 125;
            if (nav) {{
              const nr = nav.getBoundingClientRect();
              huecoNav = Math.max(109, Math.round(win.innerHeight - nr.top + 16));
            }}
            el.style.scrollMarginTop = margen + "px";
            el.style.scrollMarginBottom = huecoNav + "px";
            el.scrollIntoView({{ behavior: "smooth", block: "{bloque}" }});
          }};
          setTimeout(ir, 180);
          setTimeout(ir, 480);
          setTimeout(ir, 900);
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def desplazar_a_cotizacion_pendiente():
    desplazar_a_ancla("cotizacion-foco-pendiente")


def desplazar_a_acciones_emit():
    desplazar_a_ancla("ccm-acciones-emit", alinear="end")


@lru_cache(maxsize=1)
def cargar_logo_jpeg():
    for ruta in RUTAS_LOGO:
        if not ruta.is_file():
            continue
        try:
            from PIL import Image

            with Image.open(ruta) as im:
                rgb = im.convert("RGB")
                if ruta.suffix.lower() == ".png":
                    mascara = rgb.convert("L").point(lambda p: 0 if p > 248 else 255)
                    recorte = mascara.getbbox()
                    if recorte:
                        rgb = rgb.crop(recorte)
                    rgb.thumbnail((320, 320), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    rgb.save(buf, format="JPEG", quality=88, optimize=True)
                    return buf.getvalue(), rgb.size[0], rgb.size[1]
                return ruta.read_bytes(), rgb.size[0], rgb.size[1]
        except Exception:
            continue
    return None, 0, 0


def _prefijo_logo_pdf(ancho_pt=118.0):
    datos, pix_w, pix_h = cargar_logo_jpeg()
    if not datos or not pix_w:
        return b"", None, 0, 0
    alto_pt = ancho_pt * (pix_h / float(pix_w))
    x = (595.0 - ancho_pt) / 2.0
    y = 842.0 - 18.0 - alto_pt
    ops = f"q\n{ancho_pt:.2f} 0 0 {alto_pt:.2f} {x:.2f} {y:.2f} cm\n/Im1 Do\nQ\n".encode("ascii")
    return ops, datos, pix_w, pix_h


def html_encabezado_institucional(cuerpo_html="", extra_class="", extra_style=""):
    clases = "app-header-blue"
    if extra_class:
        clases = f"{clases} {extra_class}"
    estilo = f' style="{extra_style}"' if extra_style else ""
    cuerpo = textwrap.dedent(cuerpo_html or "").strip()
    cuerpo_html_out = f'<div class="app-header-copy">{cuerpo}</div>' if cuerpo else ""
    return (
        f'<div class="{clases}"{estilo}>'
        f'<div class="app-header-top">'
        f'<div class="app-header-brand">CENTRO DE CERÁMICAS Y MÁS</div>'
        f"</div>"
        f"{cuerpo_html_out}"
        f"</div>"
    )


def compilar_pdf_simple(stream_content):
    texto = stream_content.encode("latin-1", "replace")
    logo_ops, jpeg, pix_w, pix_h = _prefijo_logo_pdf()
    stream_bytes = (logo_ops + texto) if jpeg else texto
    stream_len = len(stream_bytes)
    con_logo = bool(jpeg)

    pdf_buffer = io.BytesIO()
    pdf_buffer.write(b"%PDF-1.4\n")
    offsets = []

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    recursos = (
        b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >>"
        if con_logo
        else b"/Resources << /Font << /F1 5 0 R >> >>"
    )
    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        + recursos
        + b" >>\nendobj\n"
    )

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1"))
    pdf_buffer.write(stream_bytes)
    pdf_buffer.write(b"\nendstream\nendobj\n")

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")

    if con_logo:
        offsets.append(pdf_buffer.tell())
        pdf_buffer.write(
            (
                f"6 0 obj\n<< /Type /XObject /Subtype /Image /Width {pix_w} /Height {pix_h} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n"
            ).encode("ascii")
        )
        pdf_buffer.write(jpeg)
        pdf_buffer.write(b"\nendstream\nendobj\n")

    xref_offset = pdf_buffer.tell()
    n_obj = 6 if con_logo else 5
    pdf_buffer.write(f"xref\n0 {n_obj + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        pdf_buffer.write(f"{off:010d} 00000 n \n".encode("latin-1"))

    pdf_buffer.write(f"trailer\n<< /Size {n_obj + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("latin-1"))
    return pdf_buffer.getvalue()


def generar_pdf_etiqueta_proveedor(
    casillero,
    nombre,
    telefono,
    ciudad,
    al=0.0,
    an=0.0,
    la=0.0,
    pe_lb=0.0,
    pe_kg=0.0,
    vol_m3=0.0,
    destino_entrega="Retirar en Almacén",
    fecha_emision=None,
):
    dim_txt = f"{al:.1f} x {an:.1f} x {la:.1f} CM" if (al > 0 or an > 0 or la > 0) else "POR DEFINIR EN ORIGEN"
    peso_txt = f"{pe_kg:.2f} KG ({pe_lb:.1f} LBS)" if pe_lb > 0 else "_______ KG"
    vol_txt = f"{vol_m3:.4f} CBM" if vol_m3 > 0 else "_______ CBM"
    destino_clean = str(destino_entrega).replace("📍", "").replace("📦", "").replace("🏬", "").strip().upper()
    fecha_txt = fecha_emision if fecha_emision else obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p")

    stream = f"""BT
/F1 16 Tf
40 728 Td
(CENTRO DE CERAMICAS Y MAS - HONDURAS) Tj
/F1 9 Tf
0 -16 Td
(EMITIDO EL: {fecha_txt}) Tj
/F1 10 Tf
0 -16 Td
(MARITIME CONSOLIDATION CARGO [CHINA -> HONDURAS]) Tj
0 -26 Td
(================================================================) Tj
/F1 13 Tf
0 -22 Td
(CLIENT CODE / CASILLERO : {casillero}) Tj
/F1 10 Tf
0 -18 Td
(CLIENT NAME: {nombre}) Tj
0 -14 Td
(CONTACT PHONE: {telefono}) Tj
0 -14 Td
(FINAL DESTINATION / ENTREGA: {destino_clean}, HONDURAS) Tj
0 -22 Td
(================================================================) Tj
/F1 11 Tf
0 -18 Td
(SHIP TO / WAREHOUSE IN CHINA [CHILAT]:) Tj
/F1 9 Tf
0 -14 Td
(ATTN / RECEIVER : CHILAT / {casillero}) Tj
0 -12 Td
(ADDRESS : CHILAT Logistics Warehouse, District B, Port Area, Guangzhou) Tj
0 -12 Td
(WAREHOUSE TEL : +86 138 0000 0000) Tj
0 -22 Td
(================================================================) Tj
/F1 10 Tf
0 -16 Td
(PACKAGE SPECIFICATIONS / DETALLES DE CARGA:) Tj
/F1 9 Tf
0 -14 Td
(DIMENSIONS (L x W x H) : {dim_txt}) Tj
0 -12 Td
(GROSS WEIGHT           : {peso_txt}) Tj
0 -12 Td
(ESTIMATED VOLUME       : {vol_txt}) Tj
0 -20 Td
(----------------------------------------------------------------) Tj
/F1 9 Tf
0 -15 Td
(INSTRUCTIONS FOR SUPPLIER / FABRICANTE [ALIBABA / MADE-IN-CHINA / 1688]:) Tj
0 -13 Td
(1. Paste this shipping label firmly on at least 2 sides of every box.) Tj
0 -12 Td
(2. Packages received without the Client Code will NOT be processed.) Tj
0 -12 Td
(3. Send domestic tracking number to the buyer immediately upon dispatch.) Tj
ET"""
    return compilar_pdf_simple(stream)


def generar_pdf_confirmacion_cotizacion(
    casillero,
    nombre,
    telefono,
    ciudad,
    tipo_carga,
    al,
    an,
    la,
    peso_lb,
    peso_kg,
    vol_m3,
    vol_ft3,
    total_usd,
    detalle_tarifa,
    id_cot,
    destino_entrega="Retirar en Almacén",
    fecha_emision=None,
):
    fecha_txt = fecha_emision if fecha_emision else obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p")
    destino_clean = str(destino_entrega).replace("📍", "").replace("📦", "").replace("🏬", "").strip().upper()

    stream = f"""BT
/F1 15 Tf
40 728 Td
(CENTRO DE CERAMICAS Y MAS - HONDURAS) Tj
/F1 10 Tf
0 -16 Td
(COMPROBANTE OFICIAL DE COTIZACION Y ACEPTACION DE TARIFA) Tj
0 -20 Td
(================================================================) Tj
/F1 11 Tf
0 -20 Td
(NO. CONTROL / COTIZACION : CCM-COT-{id_cot:05d}) Tj
/F1 9 Tf
0 -14 Td
(FECHA Y HORA DE EMISION : {fecha_txt}) Tj
0 -18 Td
(================================================================) Tj
/F1 11 Tf
0 -16 Td
(DATOS DEL CLIENTE Y DIRECCION DE ENTREGA SELECCIONADA:) Tj
/F1 9 Tf
0 -14 Td
(CASILLERO INTERNACIONAL : {casillero}) Tj
0 -12 Td
(TITULAR DE LA CUENTA    : {nombre}) Tj
0 -12 Td
(TELEFONO / WHATSAPP    : {telefono}) Tj
0 -12 Td
(MODALIDAD DE ENTREGA   : {destino_clean}) Tj
0 -12 Td
(DESTINO BASE REGISTRADO: {ciudad.upper()}, HONDURAS) Tj
0 -18 Td
(================================================================) Tj
/F1 11 Tf
0 -16 Td
(DESGLOSE DE LA CARGA Y DIMENSIONES:) Tj
/F1 9 Tf
0 -14 Td
(MODALIDAD DE CARGA      : {tipo_carga.upper()}) Tj
0 -12 Td
(DIMENSIONES DEL PAQUETE : {al:.1f} cm (Alto) x {an:.1f} cm (Ancho) x {la:.1f} cm (Largo)) Tj
0 -12 Td
(PESO TOTAL CALCULADO   : {peso_lb:.2f} LBS  ({peso_kg:.2f} KG)) Tj
0 -12 Td
(VOLUMEN ESTIMADO        : {vol_m3:.4f} M3  ({vol_ft3:.2f} FT3)) Tj
0 -12 Td
(DESGLOSE DE TARIFA      : {detalle_tarifa}) Tj
0 -18 Td
(----------------------------------------------------------------) Tj
/F1 13 Tf
0 -16 Td
(TOTAL FLETE MARITIMO ESTIMADO: ${total_usd:.2f} USD) Tj
/F1 9 Tf
0 -18 Td
(================================================================) Tj
/F1 10 Tf
0 -16 Td
(DIRECCION DE BODEGA EN GUANGZHOU, CHINA:) Tj
/F1 8 Tf
0 -13 Td
(ATTN / CONSIGNATARIO : CHILAT / {casillero}) Tj
0 -11 Td
(DIRECCION EN GUANGZHOU: CHILAT Logistics Warehouse, District B, Port Area) Tj
0 -11 Td
(TELEFONO EN CHINA    : +86 138 0000 0000) Tj
0 -18 Td
(================================================================) Tj
/F1 8 Tf
0 -14 Td
(DECLARACION DE CONFORMIDAD DEL CLIENTE:) Tj
0 -11 Td
(El cliente declara estar conforme con la tarifa cotizada y autoriza el) Tj
0 -10 Td
(procesamiento de su carga con Centro de Ceramicas y Mas.) Tj
ET"""
    return compilar_pdf_simple(stream)


# ---------------------------------------------------------
# 9. PANEL ADMINISTRATIVO / SUPERADMINISTRADOR
# ---------------------------------------------------------
elif es_rol_admin():
    root = es_superadmin()
    st.markdown(
        """
        <style>
            :root { --app-max-width: 920px; }
            .block-container { max-width: 920px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    titulo = "Panel de Superadministrador" if root else "Panel Administrativo"
    st.markdown(
        html_encabezado_institucional(
            f'<div class="app-greeting-title">{titulo}</div>'
            f'<div class="app-greeting-sub">{st.session_state.get("nombre", "")} • {st.session_state.get("usuario", "")}</div>',
            extra_style="margin-bottom:12px;",
        ),
        unsafe_allow_html=True,
    )

    tab_u, tab_p, tab_t, tab_s = st.tabs(
        ["👥 Usuarios y permisos", "📦 Paquetes", "⚙️ Tarifas y fórmulas", "🗄️ Sistema"]
    )

    with tab_u:
        with get_db() as conn:
            c = conn.cursor()
            if root:
                c.execute(
                    """
                    SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal,
                           departamento, ciudad, direccion_exacta, rol, activo
                    FROM usuarios ORDER BY rol DESC, nombre_completo
                    """
                )
            else:
                c.execute(
                    """
                    SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal,
                           departamento, ciudad, direccion_exacta, rol, activo
                    FROM usuarios WHERE rol = 'cliente' ORDER BY nombre_completo
                    """
                )
            filas = c.fetchall()

        if filas:
            st.dataframe(
                {
                    "Casillero": [formatear_casillero(r[1]) for r in filas],
                    "Nombre": [r[2] for r in filas],
                    "DNI": [r[3] for r in filas],
                    "Correo": [r[4] for r in filas],
                    "Teléfono": [r[5] for r in filas],
                    "Rol": [r[9] for r in filas],
                    "Activo": ["Sí" if r[10] else "No" for r in filas],
                },
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No hay cuentas para mostrar.")

        etiquetas = [f"{formatear_casillero(r[1])} — {r[2]}" for r in filas]
        if etiquetas:
            elegido = st.selectbox("Cuenta a gestionar", etiquetas, key="admin_sel_user")
            idx = etiquetas.index(elegido)
            u = filas[idx]
            uid, cas_u, nom_u, dni_u, cor_u, tel_u, dep_u, ciu_u, dir_u, rol_u, act_u = u
            perm = permisos_de(cas_u)

            st.markdown("#### Perfil y casillero")
            n_nom = st.text_input("Nombre completo", value=nom_u, key=f"adm_nom_{cas_u}")
            n_dni = st.text_input("DNI", value=dni_u, key=f"adm_dni_{cas_u}")
            n_cor = st.text_input("Correo", value=cor_u, key=f"adm_cor_{cas_u}")
            n_tel = st.text_input("Teléfono", value=tel_u, key=f"adm_tel_{cas_u}")
            n_dep = st.selectbox(
                "Departamento",
                list(MUNICIPIOS_HONDURAS.keys()),
                index=list(MUNICIPIOS_HONDURAS.keys()).index(dep_u) if dep_u in MUNICIPIOS_HONDURAS else 0,
                key=f"adm_dep_{cas_u}",
            )
            munis = MUNICIPIOS_HONDURAS[n_dep]
            n_ciu = st.selectbox(
                "Ciudad",
                munis,
                index=munis.index(ciu_u) if ciu_u in munis else 0,
                key=f"adm_ciu_{cas_u}",
            )
            n_dir = st.text_area("Dirección", value=dir_u or "", key=f"adm_dir_{cas_u}")
            n_cas = st.text_input("Casillero", value=formatear_casillero(cas_u), key=f"adm_cas_{cas_u}")
            n_act = st.checkbox("Cuenta activa", value=bool(act_u), key=f"adm_act_{cas_u}")
            roles_disp = ["cliente", "admin"]
            if root:
                roles_disp = ["cliente", "admin", "superadmin"]
            n_rol = st.selectbox(
                "Rol",
                roles_disp,
                index=roles_disp.index(rol_u) if rol_u in roles_disp else 0,
                key=f"adm_rol_{cas_u}",
                disabled=(rol_u == "superadmin" and not root),
            )

            st.markdown("#### Hubs y módulos (impacto inmediato en el cliente)")
            p_china = st.checkbox("Hub China", value=bool(perm.get("hub_china")), key=f"adm_h_cn_{cas_u}")
            p_eeuu = st.checkbox("Hub EE. UU.", value=bool(perm.get("hub_eeuu")), key=f"adm_h_us_{cas_u}")
            p_hn = st.checkbox("Hub Honduras", value=bool(perm.get("hub_honduras")), key=f"adm_h_hn_{cas_u}")
            p_cot = st.checkbox("Módulo Cotizador", value=bool(perm.get("mod_cotizador")), key=f"adm_m_cot_{cas_u}")
            p_cat = st.checkbox("Módulo Catálogo", value=bool(perm.get("mod_catalogo")), key=f"adm_m_cat_{cas_u}")
            p_hist = st.checkbox("Módulo Mis Cotizaciones", value=bool(perm.get("mod_cotizaciones")), key=f"adm_m_hist_{cas_u}")
            p_env = st.checkbox("Módulo Envíos", value=bool(perm.get("mod_envios")), key=f"adm_m_env_{cas_u}")
            p_fic = st.checkbox("Módulo Fichas", value=bool(perm.get("mod_fichas")), key=f"adm_m_fic_{cas_u}")

            if st.button("Guardar perfil y permisos", type="primary", key="adm_save_user"):
                nuevo_cas = formatear_casillero(n_cas) or generar_codigo_casillero_dni(n_dni)
                if rol_u == "superadmin" and (n_rol != "superadmin" or not n_act) and not root:
                    st.error("Solo el superadministrador puede alterar la cuenta raíz.")
                else:
                    with get_db() as conn:
                        cur = conn.cursor()
                        if nuevo_cas != formatear_casillero(cas_u):
                            _migrar_casillero_tablas(conn, cas_u, nuevo_cas)
                        cur.execute(
                            """
                            UPDATE usuarios SET nombre_completo=?, dni=?, correo_principal=?, telefono_principal=?,
                                departamento=?, ciudad=?, direccion_exacta=?, codigo_casillero=?, rol=?, activo=?
                            WHERE id=?
                            """,
                            (n_nom, n_dni, n_cor, n_tel, n_dep, n_ciu, n_dir, nuevo_cas, n_rol, 1 if n_act else 0, uid),
                        )
                    guardar_permisos(
                        nuevo_cas,
                        {
                            "hub_china": p_china,
                            "hub_eeuu": p_eeuu,
                            "hub_honduras": p_hn,
                            "mod_cotizador": p_cot,
                            "mod_catalogo": p_cat,
                            "mod_cotizaciones": p_hist,
                            "mod_envios": p_env,
                            "mod_fichas": p_fic,
                        },
                    )
                    st.success("Cambios guardados. El cliente los verá en su próximo refresco.")
                    st.rerun()

            nueva_clave = st.text_input("Nueva contraseña (opcional)", type="password", key=f"adm_new_pwd_{cas_u}")
            if st.button("Restablecer credenciales", key="adm_reset_pwd"):
                clave = nueva_clave.strip() if nueva_clave else generar_clave_provisional()
                with get_db() as conn:
                    conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(clave), uid))
                st.success(f"Contraseña actualizada (hash SHA-256). Clave temporal: **{clave}**")

            if rol_u != "superadmin" and st.button("Eliminar cuenta", key="adm_del_user"):
                with get_db() as conn:
                    cur = conn.cursor()
                    for tabla in ("permisos_usuario", "direcciones_entrega", "carrito_catalogo", "cotizaciones", "paquetes"):
                        cur.execute(f"DELETE FROM {tabla} WHERE codigo_casillero = ?", (cas_u,))
                    cur.execute("DELETE FROM usuarios WHERE id = ?", (uid,))
                st.success("Cuenta eliminada.")
                st.rerun()

        with st.expander("➕ Crear cuenta"):
            c_nom = st.text_input("Nombre *", key="new_nom")
            c_dni = st.text_input("DNI *", key="new_dni")
            c_cor = st.text_input("Correo *", key="new_cor")
            c_tel = st.text_input("Teléfono *", key="new_tel")
            c_dep = st.selectbox("Departamento", list(MUNICIPIOS_HONDURAS.keys()), key="new_dep")
            c_ciu = st.selectbox("Ciudad", MUNICIPIOS_HONDURAS[c_dep], key="new_ciu")
            c_dir = st.text_input("Dirección", key="new_dir")
            c_pwd = st.text_input("Contraseña inicial", type="password", key="new_pwd")
            c_rol = st.selectbox("Rol", ["cliente", "admin"] if root else ["cliente"], key="new_rol")
            if st.button("Crear usuario", type="primary", key="adm_create_user"):
                if not (c_nom and c_dni and c_cor and c_tel):
                    st.warning("Complete los campos obligatorios.")
                else:
                    n_cod = generar_codigo_casillero_dni(c_dni)
                    n_pwd = c_pwd.strip() if c_pwd else generar_clave_provisional()
                    try:
                        with get_db() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                """
                                INSERT INTO usuarios (
                                    codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal,
                                    departamento, ciudad, direccion_exacta, password_hash, rol, activo, fecha_creacion
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                                """,
                                (
                                    n_cod,
                                    c_nom,
                                    c_dni,
                                    c_cor,
                                    c_tel,
                                    c_dep,
                                    c_ciu,
                                    c_dir or "San Juan, Intibucá",
                                    hash_pwd(n_pwd),
                                    c_rol,
                                    obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S"),
                                ),
                            )
                        asegurar_permisos_casillero(n_cod, c_rol)
                        st.success(f"Cuenta creada. Casillero `{n_cod}` • Contraseña `{n_pwd}`")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un casillero o correo con esos datos.")

    with tab_p:
        t_in = st.text_input("Tracking de China")
        c_in = st.text_input("Casillero asignado", placeholder="Ej: CCM-15011985 o DNI del cliente")
        d_in = st.text_input("Descripción de la carga", placeholder="Ej: 4 cajas de porcelanato 60x120")
        cont_in = st.text_input("ID de contenedor", placeholder="Ej: CCM-CNT-014")
        e_in = st.selectbox(
            "Estado",
            [
                "En Bodega China",
                "En Travesía Marítima",
                "En Desaduanaje",
                "Disponible en Bodega Central",
                "Entregado",
            ],
        )
        if st.button("Actualizar Paquete", type="primary"):
            if t_in and c_in:
                f_act = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO paquetes (tracking, codigo_casillero, descripcion, contenedor_id, estado, fecha_actualizacion)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(tracking) DO UPDATE SET
                            codigo_casillero = excluded.codigo_casillero,
                            descripcion = excluded.descripcion,
                            contenedor_id = excluded.contenedor_id,
                            estado = excluded.estado,
                            fecha_actualizacion = excluded.fecha_actualizacion
                        """,
                        (t_in, formatear_casillero(c_in), d_in, cont_in, e_in, f_act),
                    )
                st.success("Paquete actualizado.")
                st.rerun()
            else:
                st.warning("Ingrese tracking y casillero.")

    with tab_t:
        st.markdown("#### Tarifas y constantes del cotizador")
        n_lb = st.number_input("Tarifa por libra (USD)", min_value=0.01, value=float(get_tarifa("tarifa_libra") or 3.5), step=0.05)
        n_m3 = st.number_input("Tarifa por m³ (USD)", min_value=0.01, value=float(get_tarifa("tarifa_m3") or 680), step=1.0)
        n_min = st.number_input("Mínimo de cobro (USD)", min_value=0.01, value=float(get_tarifa("minimo_cobro_usd") or 10), step=0.50)
        n_umin = st.number_input("Umbral tarifa mínima (lb)", min_value=0.1, value=float(get_tarifa("umbral_minimo_lb") or 3), step=0.5)
        n_upaq = st.number_input("Tope paquetería (lb)", min_value=1.0, value=float(get_tarifa("umbral_paqueteria_lb") or 99), step=1.0)
        n_div = st.number_input("Divisor peso volumétrico (kg/CBM)", min_value=1.0, value=float(get_tarifa("divisor_peso_volumetrico") or 390), step=1.0)
        n_tasa = st.number_input("Tasa USD/HNL", min_value=0.01, value=float(leer_config_moneda("TASA_USD_HNL", 24.85)), step=0.01)
        n_com = st.number_input("Comisión CCM (0-1)", min_value=0.0, max_value=1.0, value=float(leer_config_moneda("COMISION_CCM_PORCENTAJE", 0.10)), step=0.01)
        if st.button("Guardar tarifas y fórmulas", type="primary"):
            set_tarifa("tarifa_libra", n_lb)
            set_tarifa("tarifa_m3", n_m3)
            set_tarifa("minimo_cobro_usd", n_min)
            set_tarifa("umbral_minimo_lb", n_umin)
            set_tarifa("umbral_paqueteria_lb", n_upaq)
            set_tarifa("divisor_peso_volumetrico", n_div)
            set_config_sistema("TASA_USD_HNL", n_tasa, "Tasa USD a lempira")
            set_config_sistema("COMISION_CCM_PORCENTAJE", n_com, "Comisión CCM sobre FOB")
            st.success("Parámetros globales actualizados.")

    with tab_s:
        st.markdown("#### Mantenimiento de base de datos")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tablas = [r[0] for r in c.fetchall()]
            conteos = {}
            for t in tablas:
                c.execute(f"SELECT COUNT(*) FROM {t}")
                conteos[t] = c.fetchone()[0]
        st.dataframe(
            {"Tabla": list(conteos.keys()), "Registros": list(conteos.values())},
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("#### Variables de entorno y configuración")
        env_keys = sorted(k for k in os.environ if k.startswith(("STREAMLIT_", "CCM_")) or k in ("PORT", "HOSTNAME", "HOME"))
        if env_keys:
            st.dataframe(
                {"Variable": env_keys, "Valor": [os.environ.get(k, "") for k in env_keys]},
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No hay variables STREAMLIT_/CCM_ en el entorno del proceso.")

        with get_db() as conn:
            filas_cfg = conn.execute("SELECT clave, valor, descripcion FROM config_sistema ORDER BY clave").fetchall()
        st.markdown("##### config_sistema (prioridad sobre secrets.toml)")
        for clave, valor, desc in filas_cfg:
            nv = st.text_input(f"{clave}", value=str(valor), help=desc or "", key=f"sys_{clave}")
            if nv != str(valor) and st.button(f"Guardar {clave}", key=f"save_sys_{clave}"):
                set_config_sistema(clave, nv, desc or "")
                st.success(f"{clave} actualizado.")
                st.rerun()

        nclave = st.text_input("Nueva clave de sistema", key="sys_new_k")
        nvalor = st.text_input("Valor", key="sys_new_v")
        ndesc = st.text_input("Descripción", key="sys_new_d")
        if st.button("Agregar variable de sistema", key="sys_add"):
            if nclave:
                set_config_sistema(nclave, nvalor, ndesc)
                st.success("Variable agregada.")
                st.rerun()

    if st.button("Cerrar sesión", type="secondary", key="btn_logout_admin"):
        logout()

else:
    st.error("Rol no reconocido. Inicie sesión de nuevo.")
    if st.button("Volver al login"):
        logout()
