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
import re
import queue
import threading

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


@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
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


@st.cache_data(ttl=60, max_entries=64, show_spinner=False)
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


def leer_database_url():
    """Obtiene la conexión privada desde Streamlit Secrets, sin exponerla en el código."""
    try:
        return str(st.secrets.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    except Exception:
        return str(os.environ.get("DATABASE_URL") or "").strip()


DATABASE_URL = leer_database_url()
USA_SUPABASE = DATABASE_URL.lower().startswith(("postgresql://", "postgres://"))

if USA_SUPABASE:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Falta psycopg. Agregue 'psycopg[binary]' al archivo requirements.txt de Streamlit."
        ) from exc
    # El resto de la aplicación conserva manejadores sqlite3.Error. Se mapean
    # al tipo de error de PostgreSQL para mantener mensajes controlados.
    sqlite3.Error = psycopg.Error
    sqlite3.IntegrityError = psycopg.IntegrityError
    sqlite3.OperationalError = psycopg.OperationalError


def traducir_sql_postgres(sql):
    """Compatibilidad temporal entre las consultas históricas SQLite y PostgreSQL."""
    sql_pg = str(sql)
    sql_pg = re.sub(r"\bIFNULL\(confirmada\s*,\s*0\)", "COALESCE(confirmada, FALSE)", sql_pg, flags=re.I)
    sql_pg = re.sub(r"\bIFNULL\(", "COALESCE(", sql_pg, flags=re.I)
    # Después de convertir IFNULL, también hay que convertir la comparación
    # histórica con 0/1. PostgreSQL no permite comparar boolean con entero.
    sql_pg = re.sub(
        r"COALESCE\(confirmada\s*,\s*FALSE\)\s*=\s*0\b",
        "COALESCE(confirmada, FALSE) = FALSE",
        sql_pg,
        flags=re.I,
    )
    sql_pg = re.sub(
        r"COALESCE\(confirmada\s*,\s*FALSE\)\s*=\s*1\b",
        "COALESCE(confirmada, FALSE) = TRUE",
        sql_pg,
        flags=re.I,
    )
    sql_pg = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql_pg, flags=re.I)
    if re.match(r"^\s*INSERT\s+INTO\b", sql_pg, flags=re.I) and "ON CONFLICT" not in sql_pg.upper():
        # Solo los INSERT OR IGNORE originales necesitan ignorar conflictos.
        if re.search(r"\bINSERT\s+OR\s+IGNORE\b", str(sql), flags=re.I):
            sql_pg = sql_pg.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    sql_pg = re.sub(r"\bactivo\s*=\s*1\b", "activo = TRUE", sql_pg, flags=re.I)
    sql_pg = re.sub(r"\bconfirmada\s*=\s*1\b", "confirmada = TRUE", sql_pg, flags=re.I)
    sql_pg = re.sub(r"\bconfirmada\s*=\s*0\b", "confirmada = FALSE", sql_pg, flags=re.I)
    for campo_bool in (
        "hub_china", "hub_eeuu", "hub_honduras", "mod_cotizador", "mod_catalogo",
        "mod_cotizaciones", "mod_envios", "mod_fichas",
    ):
        sql_pg = re.sub(
            rf"\b{campo_bool}\s*=\s*1\b", f"{campo_bool} = TRUE", sql_pg, flags=re.I
        )
        sql_pg = re.sub(
            rf"\b{campo_bool}\s*=\s*0\b", f"{campo_bool} = FALSE", sql_pg, flags=re.I
        )
    return sql_pg.replace("?", "%s")


class CursorPostgresCompatible:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, sql, params=None):
        sql_original = str(sql)
        sql_pg = traducir_sql_postgres(sql_original)
        tabla_con_id = re.match(
            r"^\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(cotizaciones|direcciones_entrega)\b",
            sql_original,
            flags=re.I,
        )
        if tabla_con_id and "RETURNING" not in sql_pg.upper():
            sql_pg = sql_pg.rstrip().rstrip(";") + " RETURNING id"
        self._cursor.execute(sql_pg, params or ())
        self.rowcount = self._cursor.rowcount
        self.lastrowid = None
        if tabla_con_id and self.rowcount:
            fila = self._cursor.fetchone()
            self.lastrowid = int(fila[0]) if fila else None
        return self

    def executemany(self, sql, params_seq):
        self._cursor.executemany(traducir_sql_postgres(sql), params_seq)
        self.rowcount = self._cursor.rowcount
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __getattr__(self, nombre):
        return getattr(self._cursor, nombre)


class PoolPostgresSimple:
    """Pool acotado y seguro para hilos, sin dependencias adicionales."""

    def __init__(self, dsn, min_size=1, max_size=8, timeout=20):
        self.dsn = dsn
        self.max_size = max(1, int(max_size))
        self.timeout = max(1, int(timeout))
        self._disponibles = queue.LifoQueue(maxsize=self.max_size)
        self._creadas = 0
        self._lock = threading.Lock()
        for _ in range(min(max(0, int(min_size)), self.max_size)):
            self._disponibles.put(self._nueva_conexion())

    def _nueva_conexion(self):
        conexion = psycopg.connect(self.dsn, connect_timeout=self.timeout)
        with self._lock:
            self._creadas += 1
        return conexion

    def obtener(self):
        try:
            conexion = self._disponibles.get_nowait()
        except queue.Empty:
            with self._lock:
                puede_crear = self._creadas < self.max_size
                if puede_crear:
                    self._creadas += 1
            if puede_crear:
                try:
                    conexion = psycopg.connect(self.dsn, connect_timeout=self.timeout)
                except Exception:
                    with self._lock:
                        self._creadas = max(0, self._creadas - 1)
                    raise
            else:
                conexion = self._disponibles.get(timeout=self.timeout)
        if getattr(conexion, "closed", False) or getattr(conexion, "broken", False):
            with self._lock:
                self._creadas = max(0, self._creadas - 1)
            conexion = self._nueva_conexion()
        return conexion

    def devolver(self, conexion, descartar=False):
        if descartar or getattr(conexion, "closed", False) or getattr(conexion, "broken", False):
            try:
                conexion.close()
            finally:
                with self._lock:
                    self._creadas = max(0, self._creadas - 1)
            return
        try:
            self._disponibles.put_nowait(conexion)
        except queue.Full:
            conexion.close()
            with self._lock:
                self._creadas = max(0, self._creadas - 1)


class ConexionPostgresCompatible:
    def __init__(self, conexion, pool=None):
        self._conexion = conexion
        self._pool = pool

    def cursor(self):
        return CursorPostgresCompatible(self._conexion.cursor())

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def commit(self):
        self._conexion.commit()

    def rollback(self):
        self._conexion.rollback()

    def __enter__(self):
        return self

    def __exit__(self, tipo_error, valor_error, traza):
        descartar = False
        try:
            if tipo_error is None:
                try:
                    self.commit()
                except Exception:
                    descartar = True
                    try:
                        self.rollback()
                    except Exception:
                        pass
                    raise
            else:
                try:
                    self.rollback()
                except Exception:
                    descartar = True
                descartar = descartar or bool(getattr(self._conexion, "broken", False))
        finally:
            if self._pool is None:
                self._conexion.close()
            else:
                self._pool.devolver(self._conexion, descartar=descartar)
        return False
LOGO_FILENAME = "logo_ccm_print.jpg"
RUTAS_LOGO = (
    Path(__file__).resolve().parent / "assets" / "logo_ccm_print.jpg",
    Path(__file__).resolve().parent / "assets" / "logo_ccm.png",
    Path(__file__).resolve().parent / "logo_ccm_print.jpg",
    Path(__file__).resolve().parent / "logo centro y mas.jpg",
)

VIGENCIA_COTIZACION_HORAS = 1
VIGENCIA_COTIZACION = timedelta(hours=VIGENCIA_COTIZACION_HORAS)
VIGENCIA_COTIZACION_CONFIRMADA_HORAS = 48
VIGENCIA_COTIZACION_CONFIRMADA = timedelta(hours=VIGENCIA_COTIZACION_CONFIRMADA_HORAS)
HISTORIAL_COTIZACIONES_MAX = 500
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


def pagina_registros(filas, clave, cantidad=10):
    """Limita widgets renderizados; el usuario puede ampliar de diez en diez."""
    total = len(filas)
    limite = max(cantidad, int(st.session_state.get(clave, cantidad) or cantidad))
    return list(filas[:limite]), total, limite


def aumentar_limite_registros(clave, paso=10):
    st.session_state[clave] = int(st.session_state.get(clave, paso) or paso) + paso


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
    fin_txt = fin.strftime("%d/%m/%Y %I:%M:%S %p")
    if restante.total_seconds() <= 0:
        return f"Vencida (era hasta {fin_txt})"
    total_segundos = max(0, int(restante.total_seconds()))
    horas, rem_segundos = divmod(total_segundos, 3600)
    minutos, segundos = divmod(rem_segundos, 60)
    if horas >= 1:
        return f"Vigente {horas} h {minutos} min {segundos} s (hasta {fin_txt})"
    return f"Vigente {minutos} min {segundos} s (hasta {fin_txt})"


def cotizacion_confirmada_vigente(fecha_confirmacion, ahora=None):
    """Una tarifa confirmada solo está disponible por 48 horas desde la confirmación."""
    dt = parsear_fecha_cotizacion(fecha_confirmacion)
    if dt is None:
        return False
    ahora = ahora or obtener_tiempo_honduras()
    edad = ahora - dt
    return timedelta(0) <= edad <= VIGENCIA_COTIZACION_CONFIRMADA


def texto_vigencia_cotizacion_confirmada(fecha_confirmacion, ahora=None):
    dt = parsear_fecha_cotizacion(fecha_confirmacion)
    if dt is None:
        return "Consolidada — sin fecha de confirmación"
    ahora = ahora or obtener_tiempo_honduras()
    fin = dt + VIGENCIA_COTIZACION_CONFIRMADA
    restante = fin - ahora
    fin_txt = fin.strftime("%d/%m/%Y %I:%M:%S %p")
    if restante.total_seconds() <= 0:
        return f"Consolidada — vigencia vencida (hasta {fin_txt})"
    total_segundos = max(0, int(restante.total_seconds()))
    horas, rem_segundos = divmod(total_segundos, 3600)
    minutos, segundos = divmod(rem_segundos, 60)
    return f"Consolidada — vigente {horas} h {minutos} min {segundos} s (hasta {fin_txt})"


def leer_config_moneda(clave, valor_default):
    try:
        valor = get_config_sistema(clave, "")
        if valor not in (None, ""):
            try:
                return float(valor)
            except (TypeError, ValueError):
                return valor
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

MUNICIPIOS_HONDURAS = {
    "Atlántida": ["La Ceiba", "El Porvenir", "Esparta", "Jutiapa", "La Masica", "San Francisco", "Tela", "Arizona"],
    "Colón": ["Trujillo", "Balfate", "Iriona", "Limón", "Sabá", "Santa Fe", "Santa Rosa de Aguán", "Sonaguera", "Tocoa", "Bonito Oriental"],
    "Comayagua": ["Comayagua", "Ajuterique", "El Rosario", "Esquías", "Humuya", "La Libertad", "Lamaní", "La Trinidad", "Lejamaní", "Meámbar", "Minas de Oro", "Ojos de Agua", "San Jerónimo", "San José de Comayagua", "San José del Potrero", "San Luis", "San Sebastián", "Siguatepeque", "Villa de San Antonio", "Las Lajas", "Taulabé"],
    "Copán": ["Santa Rosa de Copán", "Cabañas", "Concepción", "Copán Ruinas", "Corquín", "Cucuyagua", "Dolores", "Dulce Nombre", "El Paraíso", "Florida", "La Jigua", "La Unión", "Nueva Arcadia (La Entrada)", "San Agustín", "San Antonio", "San Jerónimo", "San José", "San Juan de Opoa", "San Nicolás", "San Pedro", "Santa Rita", "Trinidad de Copán"],
    "Cortés": ["San Pedro Sula", "Choloma", "Omoa", "Pimienta", "Potrerillos", "Puerto Cortés", "San Antonio de Cortés", "San Francisco de Yojoa", "San Manuel", "Santa Cruz de Yojoa", "Villanueva", "La Lima"],
    "Choluteca": ["Choluteca", "Apacilagua", "Concepción de María", "Duyure", "El Corpus", "El Triunfo", "Marcovia", "Morolica", "Namasigüe", "Orocuina", "Pespire", "San Antonio de Flores", "San Isidro", "San José", "San Marcos de Colón", "Santa Ana de Yusguare"],
    "El Paraíso": ["Yuscarán", "Alauca", "Danlí", "El Paraíso", "Güinope", "Jacaleapa", "Liure", "Morocelí", "Oropolí", "Potrerillos", "San Antonio de Flores", "San Lucas", "San Matías", "Soledad", "Teupasenti", "Texiguat", "Vado Ancho", "Yauyupe", "Trojes"],
    "Francisco Morazán": ["Distrito Central (Tegucigalpa / Comayagüela)", "Alubarén", "Cedros", "Curarén", "El Porvenir", "Guaimaca", "La Libertad", "La Venta", "Lepaterique", "Maraita", "Marale", "Nueva Armenia", "Ojojona", "Orica", "Reitoca", "Sabanagrande", "San Antonio de Oriente", "San Buenaventura", "San Ignacio", "San Juan de Flores (Cantarranas)", "San Miguelito", "Santa Ana", "Santa Lucía", "Talanga", "Tatumbla", "Valle de Ángeles", "Villa de San Francisco", "Vallecillo"],
    "Gracias a Dios": ["Puerto Lempira", "Brus Laguna", "Ahuas", "Juan Francisco Bulnes", "Ramón Villeda Morales", "Wampusirpi"],
    "Intibucá": ["San Juan", "La Esperanza", "Intibucá", "Camasca", "Colomoncagua", "Concepción", "Dolores", "Magdalena", "Masaguara", "San Antonio", "San Isidro", "San Marcos de la Sierra", "San Miguelito", "Santa Lucía", "Yamaranguila", "San Francisco de Opalaca"],
    "Islas de la Bahía": ["Roatán", "Guanaja", "José Santos Guardiola", "Utila"],
    "La Paz": ["La Paz", "Aguanqueterique", "Cabañas", "Cane", "Chinacla", "Guajiquiro", "Lauterique", "Marcala", "Mercedes de Oriente", "Opatoro", "San Antonio del Norte", "San José", "San Juan", "San Pedro de Tutule", "Santa Ana", "Santa Elena", "Santa María", "Santiago de Puringla", "Yarula"],
    "Lempira": ["Gracias", "Belén", "Candelaria", "Cololaca", "Erandique", "Gualcince", "Guarita", "La Campa", "La Iguala", "La Unión", "La Virtud", "Lepaera", "Mapulaca", "Piraera", "San Andrés", "San Francisco", "San Juan Guarita", "San Manuel Colohete", "San Rafael", "San Sebastián", "Santa Cruz", "Talgua", "Tambla", "Tomalá", "Valladolid", "Virginia", "San Marcos de Caiquín"],
    "Ocotepeque": ["Ocotepeque", "Belén Gualcho", "Concepción", "Dolores Merendón", "Fraternidad", "La Encarnación", "La Labor", "Lucerna", "Mercedes", "San Fernando", "San Francisco del Valle", "San Jorge", "San Marcos", "Santa Fe", "Sinuapa", "Sensenti"],
    "Olancho": ["Juticalpa", "Campamento", "Catacamas", "Concordia", "Dulce Nombre de Culmí", "El Rosario", "Esquipulas del Norte", "Gualaco", "Guarizama", "Guata", "Guayape", "Jano", "La Unión", "Mangulile", "Manto", "Salamá", "San Esteban", "San Francisco de Becerra", "San Francisco de la Paz", "Santa María del Real", "Silca", "Yocón", "Patuca"],
    "Santa Bárbara": ["Santa Bárbara", "Arada", "Atima", "Azacualpa", "Ceguaca", "Concepción del Norte", "Concepción del Sur", "Chinda", "El Níspero", "Gualala", "Ilama", "Las Vegas", "Macuelizo", "Naranjito", "Nuevo Celilac", "Petoa", "Protección", "Quimistán", "San Francisco de Ojuera", "San José de Colinas", "San Luis", "San Marcos", "San Nicolás", "San Pedro Zacapa", "Santa Rita", "San Vicente Centenario", "Trinidad"],
    "Valle": ["Nacaome", "Alianza", "Amapala", "Aramecina", "Caridad", "Goascorán", "Langue", "San Francisco de Coray", "San Lorenzo"],
    "Yoro": ["Yoro", "Arenal", "El Negrito", "El Progreso", "Jocón", "Morazán", "Olanchito", "Santa Rita", "Sulaco", "Victoria", "Yorito"]
}

DIAS_SEMANA_ES = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
MESES_ES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}

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
        "descripcion": "Módulo en preparación para envíos desde Estados Unidos",
        "activo": False,
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


def leer_clave_inicial_superadmin():
    """Lee la clave de bootstrap únicamente desde Secrets o entorno.

    No debe existir una clave administrativa conocida dentro del repositorio.
    """
    try:
        return str(
            st.secrets.get("SUPERADMIN_INITIAL_PASSWORD")
            or os.environ.get("SUPERADMIN_INITIAL_PASSWORD")
            or ""
        ).strip()
    except Exception:
        return str(os.environ.get("SUPERADMIN_INITIAL_PASSWORD") or "").strip()


CLAVE_INICIAL_SUPERADMIN = leer_clave_inicial_superadmin()
# Hubs y módulos base siguen abiertos; Envíos solo se ve en la barra al abrir Mis Cotizaciones.
# En producción los permisos deben salir de la tabla permisos_usuario.
PERMISOS_ABIERTOS_TEMPORAL = False
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


def cotizacion_visible_historial(fecha_raw, confirmada, ahora=None, fecha_confirmacion=None):
    if es_cotizacion_confirmada(confirmada):
        # Las filas históricas anteriores a esta regla no tenían fecha de
        # confirmación; su fecha de emisión es el respaldo seguro para no
        # mantenerlas visibles indefinidamente.
        return cotizacion_confirmada_vigente(fecha_confirmacion or fecha_raw, ahora)
    return cotizacion_vigente(fecha_raw, ahora)


def texto_estado_cotizacion(fecha_raw, confirmada, ahora=None, fecha_confirmacion=None):
    if es_cotizacion_confirmada(confirmada):
        return texto_vigencia_cotizacion_confirmada(fecha_confirmacion or fecha_raw, ahora)
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
        fecha_confirmacion = fecha_confirmacion_cotizacion(id_cot, st.session_state.get("casillero"))
        fecha_base = fecha_confirmacion or d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc")
        if cotizacion_confirmada_vigente(fecha_base, ahora):
            return d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc")
        st.session_state.pop("datos_pdf_confirmado", None)
        st.session_state.pop("ultima_cot_id", None)
        return None
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


def casillero_tiene_cotizacion_emitida(casillero):
    """Indica si el casillero ya emitió una tarifa en SQLite o en el snapshot del run actual."""
    cas = formatear_casillero(casillero or "")
    claves = coincidencias_casillero(cas)
    if not claves:
        return False
    # El callback de emisión se ejecuta antes del rerun. Reconocer su snapshot y la
    # colección de sesión hace que la píldora muestre Cotiz. de inmediato, aun si la
    # lectura de SQLite todavía no se ha refrescado en ese mismo ciclo.
    emitida = st.session_state.get("datos_pdf_confirmado")
    if isinstance(emitida, dict) and emitida.get("id_cot"):
        return True
    bolsa = st.session_state.get("cotizaciones", {})
    for clave in claves:
        if isinstance(bolsa, dict) and bolsa.get(clave):
            return True
    placeholders = ",".join("?" * len(claves))
    try:
        with get_db() as conn:
            fila = conn.execute(
                f"SELECT 1 FROM cotizaciones WHERE codigo_casillero IN ({placeholders}) LIMIT 1",
                claves,
            ).fetchone()
        return fila is not None
    except sqlite3.Error:
        return False


def cotizacion_existe_en_casillero(id_cot, casillero=None):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return False
    cas = formatear_casillero(casillero or st.session_state.get("casillero", "") or "")
    variantes = coincidencias_casillero(cas)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if variantes:
                marcadores = ",".join("?" * len(variantes))
                cur.execute(
                    f"SELECT 1 FROM cotizaciones WHERE id = ? AND codigo_casillero IN ({marcadores})",
                    (cid, *variantes),
                )
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
    variantes = coincidencias_casillero(cas)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if variantes:
                marcadores = ",".join("?" * len(variantes))
                cur.execute(
                    f"SELECT IFNULL(confirmada, 0) FROM cotizaciones WHERE id = ? AND codigo_casillero IN ({marcadores})",
                    (cid, *variantes),
                )
            else:
                cur.execute("SELECT IFNULL(confirmada, 0) FROM cotizaciones WHERE id = ?", (cid,))
            row = cur.fetchone()
        return bool(row and int(row[0]) == 1)
    except Exception:
        return False


def fecha_confirmacion_cotizacion(id_cot, casillero=None):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return None
    cas = formatear_casillero(casillero or st.session_state.get("casillero", "") or "")
    if cas:
        try:
            return cargar_confirmaciones_db(cas).get(cid)
        except Exception:
            pass
    variantes = coincidencias_casillero(cas)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if variantes:
                marcadores = ",".join("?" * len(variantes))
                cur.execute(
                    f"SELECT fecha_confirmacion FROM cotizaciones WHERE id = ? AND codigo_casillero IN ({marcadores})",
                    (cid, *variantes),
                )
            else:
                cur.execute("SELECT fecha_confirmacion FROM cotizaciones WHERE id = ?", (cid,))
            row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


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


@st.cache_data(ttl=15, show_spinner=False)
def cargar_direcciones_db(casillero):
    """Lee direcciones con una caché breve; las escrituras la invalidan de inmediato.

    Busca con coincidencias_casillero: una fila guardada como 15011985 también aparece
    al consultar CCM-15011985 (y viceversa).
    """
    # La consulta nunca debe depender de una escritura previa: si SQLite está ocupado,
    # igual debe devolver las direcciones que ya existan.
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
        # No se normaliza mediante UPDATE durante una lectura. La coincidencia IN(...)
        # ya cubre casilleros antiguos y evita que una contención de escritura oculte filas.
        return filas


@st.cache_data(ttl=20, show_spinner=False)
def cargar_cotizaciones_db(casillero):
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    variantes = coincidencias_casillero(cas)
    if not variantes:
        return []
    marcadores = ",".join("?" * len(variantes))
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd,
                   COALESCE(fecha_creacion, fecha), IFNULL(confirmada, 0)
            FROM cotizaciones
            WHERE codigo_casillero IN ({marcadores})
            ORDER BY fecha_creacion DESC, id DESC
            LIMIT ?
            """,
            (*variantes, HISTORIAL_COTIZACIONES_MAX),
        )
        return cur.fetchall()


@st.cache_data(ttl=20, show_spinner=False)
def cargar_confirmaciones_db(casillero):
    """Carga todas las fechas de confirmación en una consulta y evita el patrón N+1."""
    cas = formatear_casillero(casillero or "")
    variantes = coincidencias_casillero(cas)
    if not variantes:
        return {}
    marcadores = ",".join("?" * len(variantes))
    with get_db() as conn:
        filas = conn.execute(
            f"""
            SELECT id, fecha_confirmacion
            FROM cotizaciones
            WHERE codigo_casillero IN ({marcadores})
              AND fecha_confirmacion IS NOT NULL
            """,
            variantes,
        ).fetchall()
    return {int(cid): fecha for cid, fecha in filas if fecha}


@st.cache_data(ttl=15, show_spinner=False)
def cargar_paquetes_db(casillero):
    """Snapshot breve de paquetes para que la navegación no abra otra conexión."""
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion
            FROM paquetes
            WHERE codigo_casillero = ?
            ORDER BY fecha_actualizacion DESC
            """,
            (cas,),
        ).fetchall()


def hidratar_cotizaciones_sesion(casillero, filas_db=None, confirmaciones=None):
    cas, lista = bolsa_cotizaciones_sesion(casillero)
    if not cas:
        return
    conocidos = {int(r.get("id") or 0) for r in lista}
    try:
        if filas_db is None:
            filas_db = cargar_cotizaciones_db(cas)
        if confirmaciones is None:
            confirmaciones = cargar_confirmaciones_db(cas)
        for fila in filas_db:
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
                    "fecha_confirmacion": confirmaciones.get(cid),
                }
            )
            conocidos.add(cid)
    except Exception:
        pass


def filas_cotizaciones_casillero(casillero, ahora=None):
    cas = formatear_casillero(casillero or "")
    ahora = ahora or obtener_tiempo_honduras()
    try:
        filas_db = cargar_cotizaciones_db(cas)
        confirmaciones_db = cargar_confirmaciones_db(cas)
    except Exception:
        filas_db = []
        confirmaciones_db = {}
    hidratar_cotizaciones_sesion(cas, filas_db=filas_db, confirmaciones=confirmaciones_db)
    by_id = {}
    for fila in filas_db:
        by_id[int(fila[0])] = fila
    _, lista = bolsa_cotizaciones_sesion(cas)
    ids_db = {int(fila[0]) for fila in filas_db}
    lista[:] = [
        reg for reg in lista
        if int(reg.get("id") or 0) in ids_db
        or cotizacion_visible_historial(
            reg.get("fecha_creacion") or reg.get("fecha"),
            reg.get("confirmada"),
            ahora,
            reg.get("fecha_confirmacion"),
        )
    ]
    for reg in lista:
        try:
            by_id[int(reg.get("id") or 0)] = registro_sesion_a_fila(reg)
        except (TypeError, ValueError):
            continue
    todas = ordenar_cotizaciones_desc([f for f in by_id.values() if f and f[0]])
    confirmaciones_sesion = {
        int(reg.get("id") or 0): reg.get("fecha_confirmacion")
        for reg in lista
        if reg.get("fecha_confirmacion")
    }
    visibles = [
        f for f in todas
        if cotizacion_visible_historial(
            f[7], f[8], ahora,
            confirmaciones_sesion.get(int(f[0])) or confirmaciones_db.get(int(f[0])),
        )
    ]
    confirmaciones = dict(confirmaciones_db)
    confirmaciones.update(confirmaciones_sesion)
    return todas, visibles, confirmaciones


def marcar_cotizacion_sesion_confirmada(id_cot, casillero, fecha_confirmacion=None):
    cas, lista = bolsa_cotizaciones_sesion(casillero)
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return
    for reg in lista:
        if int(reg.get("id") or 0) == cid:
            reg["confirmada"] = 1
            reg["fecha_confirmacion"] = fecha_confirmacion or reg.get("fecha_confirmacion")
            break


def confirmar_cotizacion_casillero(id_cot, casillero):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return False
    cas = formatear_casillero(casillero or "")
    if not cas:
        st.session_state["ultimo_error_confirmacion"] = "Casillero inválido."
        return False
    _, ahora = estampa_tiempo_honduras()
    actualizado = False
    fecha_confirmacion = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if USA_SUPABASE:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'cotizaciones'"
                )
                columnas = {fila[0] for fila in cur.fetchall()}
            else:
                columnas = {fila[1] for fila in cur.execute("PRAGMA table_info(cotizaciones)").fetchall()}
            requeridas = {"id", "codigo_casillero", "confirmada", "fecha_confirmacion"}
            faltantes = sorted(requeridas - columnas)
            if faltantes:
                raise sqlite3.OperationalError(
                    "Faltan columnas en cotizaciones: " + ", ".join(faltantes)
                )
            # El ID es la llave primaria global de la cotización. Verificar la
            # fila antes de actualizar evita que variaciones CCM-/sin prefijo
            # impidan confirmar una tarifa que ya fue mostrada al cliente.
            cur.execute(
                "SELECT codigo_casillero, IFNULL(confirmada, 0), fecha_confirmacion "
                "FROM cotizaciones WHERE id = ?",
                (cid,),
            )
            fila = cur.fetchone()
            if fila is None:
                raise sqlite3.OperationalError(f"No existe la cotización con id={cid}")
            codigo_guardado, ya_confirmada, fecha_guardada = fila
            cur.execute(
                """
                UPDATE cotizaciones
                SET confirmada = TRUE, fecha_confirmacion = ?
                WHERE id = ? AND COALESCE(confirmada, FALSE) = FALSE
                """,
                (ahora, cid),
            )
            conn.commit()
            actualizado = cur.rowcount > 0
            if actualizado:
                fecha_confirmacion = ahora
            elif es_cotizacion_confirmada(ya_confirmada):
                # Operación idempotente frente a dobles clics o reruns.
                actualizado = True
                fecha_confirmacion = fecha_guardada
            else:
                raise sqlite3.OperationalError(
                    f"No se actualizó CCM-COT-{cid:05d}; casillero almacenado={codigo_guardado!r}"
                )
    except sqlite3.Error as exc:
        detalle = f"Error SQLite al confirmar CCM-COT-{cid:05d}: {exc}"
        print(detalle)
        st.session_state["ultimo_error_confirmacion"] = detalle
        actualizado = False
    except Exception as exc:
        detalle = f"Error al confirmar CCM-COT-{cid:05d}: {exc}"
        print(detalle)
        st.session_state["ultimo_error_confirmacion"] = detalle
        actualizado = False
    finally:
        # La siguiente ejecución debe leer la confirmación recién persistida,
        # nunca el resultado anterior guardado por la caché.
        cargar_cotizaciones_db.clear()
        cargar_confirmaciones_db.clear()
    if actualizado:
        st.session_state.pop("ultimo_error_confirmacion", None)
        marcar_cotizacion_sesion_confirmada(cid, cas, fecha_confirmacion or ahora)
    return actualizado


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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?)
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
        cargar_confirmaciones_db.clear()
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
            cur.execute(
                "SELECT id, fecha, fecha_confirmacion, IFNULL(confirmada, 0) FROM cotizaciones"
            )
        except sqlite3.OperationalError:
            return 0
        ids_borrar = []
        for cid, fecha, fecha_confirmacion, confirmada in cur.fetchall():
            if es_cotizacion_confirmada(confirmada):
                vencida = not cotizacion_confirmada_vigente(fecha_confirmacion or fecha, ahora)
            else:
                vencida = not cotizacion_vigente(fecha, ahora)
            if vencida:
                ids_borrar.append(cid)
        if not ids_borrar:
            return 0
        cur.executemany("DELETE FROM cotizaciones WHERE id = ?", [(cid,) for cid in ids_borrar])
        conn.commit()
        return len(ids_borrar)


@st.cache_resource(show_spinner=False)
def estado_mantenimiento_db():
    return {"lock": threading.Lock(), "ultima_purga": 0.0}


def purgar_cotizaciones_si_corresponde(ahora=None, intervalo_s=300):
    """Ejecuta el barrido SQLite una vez por proceso, no una vez por sesión."""
    if USA_SUPABASE:
        return 0
    estado = estado_mantenimiento_db()
    marca = datetime.now().timestamp()
    if marca - float(estado.get("ultima_purga") or 0) < intervalo_s:
        return 0
    with estado["lock"]:
        marca = datetime.now().timestamp()
        if marca - float(estado.get("ultima_purga") or 0) < intervalo_s:
            return 0
        eliminadas = purgar_cotizaciones_no_confirmadas_vencidas(ahora)
        estado["ultima_purga"] = marca
        return eliminadas


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
        if USA_SUPABASE:
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'direcciones_entrega'"
                )
                columnas_dir = {fila[0] for fila in c.fetchall()}
            requeridas = {
                "id", "codigo_casillero", "etiqueta", "receptor_nombre", "telefono",
                "departamento", "ciudad", "direccion_exacta", "fecha_creacion", "activa",
            }
            faltantes = sorted(requeridas - columnas_dir)
            if faltantes:
                raise sqlite3.OperationalError(
                    "Faltan columnas en direcciones_entrega: " + ", ".join(faltantes)
                )
            return True
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
                    fecha_creacion TEXT NOT NULL,
                    activa INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            c.execute("PRAGMA table_info(direcciones_entrega)")
            columnas_dir = {fila[1] for fila in c.fetchall()}
            for col in ("receptor_nombre", "telefono", "departamento", "ciudad", "direccion_exacta", "fecha_creacion"):
                if col not in columnas_dir:
                    c.execute(f"ALTER TABLE direcciones_entrega ADD COLUMN {col} TEXT")
            if "activa" not in columnas_dir:
                c.execute("ALTER TABLE direcciones_entrega ADD COLUMN activa INTEGER NOT NULL DEFAULT 1")
            conn.commit()
            return True
    except Exception as exc:
        # No ocultar fallos de esquema: el aviso aparece en el Cotizador.
        try:
            st.session_state["_dir_db_error"] = f"Esquema direcciones_entrega: {exc}"
        except Exception:
            pass
        return False


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


def opciones_entrega_desde_sesion(casillero, direcciones=None):
    """Reconstruye el desplegable desde SQLite en cada run: almacén → direcciones activas → Crear Nueva."""
    opciones = [OPCION_PREDETERMINADA]
    if direcciones is None:
        try:
            filas = cargar_direcciones_db(casillero)
            direcciones = [
                {"etiqueta": fila[1], "ciudad": fila[3]}
                for fila in filas
            ]
        except Exception as exc:
            registrar_error_direcciones(exc, "Consulta directa para selector de entrega")
            direcciones = []
    for direccion in direcciones:
        opciones.append(f"📍 {direccion.get('etiqueta', '')} - {direccion.get('ciudad', '')}")
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
        return False
    cas_norm = formatear_casillero(casillero)
    f_ahora = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    id_dir_nuevo = None
    error_db = None
    if not asegurar_esquema_direcciones():
        st.session_state["_dir_form_error"] = "No se pudo preparar la base de datos para guardar la dirección."
        return False
    try:
        with get_db() as conn:
            cur = conn.cursor()
            # BEGIN IMMEDIATE solo existe en SQLite. En Supabase/PostgreSQL la
            # conexión ya abre y confirma una transacción normal mediante el
            # administrador de contexto, por lo que no debe ejecutarse aquí.
            if not USA_SUPABASE:
                cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                INSERT INTO direcciones_entrega (codigo_casillero, etiqueta, receptor_nombre, telefono, departamento, ciudad, direccion_exacta, fecha_creacion, activa)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE)
                """,
                (cas_norm, etiqueta, receptor, tel, dep, ciu, dir_exacta, f_ahora),
            )
            id_dir_nuevo = cur.lastrowid
            conn.commit()
            if not id_dir_nuevo:
                raise sqlite3.Error("INSERT direcciones_entrega no devolvió lastrowid.")
            cur.execute(
                "SELECT id FROM direcciones_entrega WHERE id = ? AND codigo_casillero = ?",
                (id_dir_nuevo, cas_norm),
            )
            if cur.fetchone() is None:
                raise sqlite3.Error("La dirección no pudo verificarse después de confirmar la escritura.")
    except Exception as exc:
        id_dir_nuevo = None
        error_db = str(exc)
        registrar_error_direcciones(exc, "INSERT direcciones_entrega")
    invalidar_cache_direcciones()
    if error_db:
        st.session_state["_dir_form_error"] = f"No se pudo guardar la dirección: {error_db}"
        return False
    # Relee la fila recién confirmada desde la base de datos; el selector del
    # siguiente rerun se construye desde esta fuente persistente, no desde una
    # caché local.
    direcciones_sesion(cas_norm)
    # La dirección recién creada queda seleccionada automáticamente y aparece
    # como opción del desplegable sin que el usuario tenga que recargar la página.
    nueva_opcion = f"📍 {etiqueta} - {ciu}"
    seleccionar_modalidad_entrega(nueva_opcion)
    st.session_state["destino_entrega_activo"] = nueva_opcion
    # Al guardar se vuelve al Cotizador. La dirección queda seleccionada y se
    # usará en la cotización, ficha y documentos que se emitan a continuación.
    st.session_state["mostrar_gestion_direcciones"] = False
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)
    return True


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
            return
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
    """Cierra la gestión de direcciones sin alterar el destino ya seleccionado."""
    st.session_state["mostrar_gestion_direcciones"] = False
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)


def abrir_gestion_direcciones():
    st.session_state["mostrar_gestion_direcciones"] = True


def usar_direccion_y_cotizar(opcion):
    """Selecciona un destino guardado y regresa de inmediato al Cotizador."""
    seleccionar_modalidad_entrega(opcion)
    st.session_state["destino_entrega_activo"] = opcion
    st.session_state["mostrar_gestion_direcciones"] = False
    st.session_state.pop("datos_pdf_confirmado", None)


def selector_modalidad_entrega(opciones_modalidad):
    """Select de entrega en el cuerpo del Cotizador (fuera del header sticky, sin CSS sobre Baseweb)."""
    if not opciones_modalidad:
        return
    pendiente = st.session_state.pop("_mod_entrega_pendiente", None)
    if pendiente in opciones_modalidad:
        st.session_state["sb_modalidad_entrega"] = pendiente
        st.session_state["modalidad_envio_seleccionada"] = pendiente
    elif pendiente:
        # La opción todavía no está en la lista (lectura rezagada): reintenta en el siguiente run
        # y no dejes el select trabado en «Crear Nueva Dirección».
        st.session_state["_mod_entrega_pendiente"] = pendiente
        if st.session_state.get("sb_modalidad_entrega") == "➕ Crear Nueva Dirección de Envío":
            st.session_state["sb_modalidad_entrega"] = OPCION_PREDETERMINADA
    if st.session_state.get("modalidad_envio_seleccionada") not in opciones_modalidad:
        st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
        st.session_state["destino_entrega_activo"] = OPCION_PREDETERMINADA
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


def preparar_nueva_cotizacion():
    """Restablece el Cotizador para iniciar una tarifa nueva, sin borradores visibles."""
    claves_fijas = (
        "sb_tipo_carga_select",
        "sb_unidad_medida",
        "sb_unidad_peso",
        "datos_pdf_confirmado",
        "ultima_cot_id",
        "_cot_emit_snapshot",
        "_ccm_scroll_emit",
        "_ccm_emit_error",
        "_mod_entrega_lista",
        "_mod_entrega_pendiente",
        "sb_modalidad_entrega",
        "destino_entrega_activo",
        "cotizacion_historial_foco",
        "cotizacion_envio_foco",
    )
    for clave in claves_fijas:
        st.session_state.pop(clave, None)
    for clave in list(st.session_state.keys()):
        nombre_clave = str(clave)
        if (
            nombre_clave.startswith(("in_al_", "in_an_", "in_la_", "in_pe_", "dl_pdf_fab_", "btn_ver_mis_cotizaciones_"))
        ):
            st.session_state.pop(clave, None)
    st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
    st.session_state["mostrar_gestion_direcciones"] = False


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
    if vista == "Cotizador":
        preparar_nueva_cotizacion()
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


def pintar_guias_informativas(guias):
    """Muestra reglas de contexto como información estática, no como controles."""
    paleta = {
        "azul": ("#eff6ff", "#bfdbfe", "#1e3a8a"),
        "verde": ("#f0fdf4", "#bbf7d0", "#166534"),
        "naranja": ("#fff7ed", "#fed7aa", "#9a3412"),
    }
    tarjetas = []
    for icono, titulo, descripcion, color in guias:
        fondo, borde, texto = paleta[color]
        tarjetas.append(
            f'<div style="background:{fondo};border:1px solid {borde};border-radius:12px;'
            f'padding:11px 12px;color:{texto};">'
            f'<b>{html.escape(icono)} {html.escape(titulo)}</b><br>'
            f'<span style="font-size:.86rem;">{descripcion}</span></div>'
        )
    st.markdown(
        '<div role="note" aria-label="Información importante" '
        'style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));'
        'gap:10px;margin:12px 0 14px;">'
        + "".join(tarjetas)
        + "</div>",
        unsafe_allow_html=True,
    )
    ir_a("Mis Cotizaciones", hub="china")


def ir_a_inicio():
    ir_a("Inicio", hub=None)


def catalogo_disponible_en_hub_actual():
    """El catálogo de fábricas pertenece al hub China; Inicio y otros hubs no lo exponen."""
    return st.session_state.get("hub") == "china" and usuario_puede_modulo("Catálogo")


def modulo_china_disponible_en_hub_actual(modulo):
    """Los accesos operativos de China solo existen tras seleccionar ese origen."""
    return st.session_state.get("hub") == "china" and usuario_puede_modulo(modulo)


def ir_a_catalogo():
    # Impide abrir el catálogo por un callback o URL residual si el origen fue deseleccionado.
    if not catalogo_disponible_en_hub_actual():
        ir_a_inicio()
        return
    ir_a("Catálogo", hub="china")


def ir_a_mis_cotizaciones():
    casillero = st.session_state.get("casillero", "")
    if not (
        modulo_china_disponible_en_hub_actual("Mis Cotizaciones")
        and casillero_tiene_cotizacion_emitida(casillero)
    ):
        ir_a_inicio()
        return
    ir_a("Mis Cotizaciones", hub="china")


def ir_a_cotizador():
    if not modulo_china_disponible_en_hub_actual("Cotizador"):
        ir_a_inicio()
        return
    avanzar_guia_si(1, 2)
    ir_a("Cotizador", hub="china")


def ir_a_mas():
    ir_a("Más")


def ir_a_actividad():
    """Centro único para cotizaciones, envíos y fichas del cliente."""
    ir_a("Actividad", hub="china")


def abrir_direcciones_desde_mas():
    """Abre Direcciones sin duplicar un acceso permanente en la barra inferior."""
    ir_a("Cotizador", hub="china")
    abrir_gestion_direcciones()


def iniciar_guia_desde_mas():
    """Activa la guía interactiva y abre Inicio (China) para el recorrido."""
    st.session_state["mostrar_guia"] = True
    iniciar_guia_interactiva(1)
    ir_a("Inicio", hub="china")


def ir_a_envios():
    if not modulo_china_disponible_en_hub_actual("Mis Envíos"):
        ir_a_inicio()
        return
    ir_a("Mis Envíos", hub="china")


def ir_a_fichas():
    if not modulo_china_disponible_en_hub_actual("Etiqueta"):
        ir_a_inicio()
        return
    ir_a("Fichas", hub="china")


def ir_a_envios_de_cotizacion(id_cot):
    """Abre Envíos con la cotización consolidada en foco (PDF Tarifa y datos del envío)."""
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
        fecha_confirmacion = fecha_confirmacion_cotizacion(id_cot, casillero)
        d_pdf = st.session_state.get("datos_pdf_confirmado")
        if isinstance(d_pdf, dict) and int(d_pdf.get("id_cot") or 0) == int(id_cot or 0):
            d_pdf["fecha_confirmacion"] = fecha_confirmacion
        st.session_state["china_modulos_desbloqueados"] = True
        try:
            st.session_state["cotizacion_envio_foco"] = int(id_cot)
        except (TypeError, ValueError):
            pass
        if int(st.session_state.get("cotizacion_historial_foco") or 0) == int(id_cot or 0):
            st.session_state.pop("cotizacion_historial_foco", None)
        avanzar_guia_si(5, 6)
        st.session_state["flash_cotizacion_confirmada"] = int(id_cot)
        # Streamlit vuelve a ejecutar la app automáticamente al terminar un
        # callback. Forzar st.rerun() aquí muestra una advertencia en móvil y
        # no aporta ningún refresco adicional.
    else:
        detalle = st.session_state.pop("ultimo_error_confirmacion", "")
        st.session_state["flash_error_confirmacion"] = (
            "No se pudo confirmar la cotización. " + (f"Detalle: {detalle}" if detalle else "Intente nuevamente.")
        )


def ir_a_cotizacion_emitida(id_cot):
    """Tras emitir en el Cotizador, abre Mis Cotizaciones en la tarifa recién generada."""
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
        "texto": "Pasa a tus cotizaciones para asegurar tu tarifa antes de 1 hora.",
    },
    {
        "paso": 5,
        "titulo": "Consolidación de tarifa",
        "texto": "Pulse <b>Confirmar Cotización</b> en la tarifa resaltada para habilitarla durante 48 horas en su casillero.",
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
    """La tarjeta instructiva solo vive en Inicio/China y bajo demanda desde Más → Guía."""
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
    """La guía ya no arranca sola al entrar a China; solo desde Más → Guía."""
    return


def proximo_cierre_contenedor(ahora=None):
    """Próximo viernes de cierre de consolidación (hora de Honduras)."""
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
    """Tarjeta publicitaria en Inicio / China (el acceso a módulos vive en la barra inferior)."""
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
        f'<div class="promo-ad-glow" aria-hidden="true">🚢</div>'
        f'<div class="promo-ad-kicker"><span class="promo-ad-live"></span> SALIDA PROGRAMADA · CONTENEDOR 40′ HC</div>'
        f'<div class="promo-ad-title">📦 Importación Consolidada Marítima | China ➔ Honduras</div>'
        f'<div class="promo-ad-subtitle">Reserve su espacio con salida programada</div>'
        f'<div class="promo-ad-body">Reserve su espacio en nuestro <b>Contenedor 40′ HC</b> con salida programada. '
        f'Ofrecemos soluciones integrales para carga comercial variada, cerámicas, acabados y mercancía general. '
        f'Modalidad flexible en paquetería por libra o carga consolidada por CBM con asesoría de casillero incluida.</div>'
        f'<div class="promo-ad-badges">'
        f'<div class="promo-ad-badge promo-ad-badge-close"><span>🚀</span><div><small>PRÓXIMO CIERRE DE CONTENEDOR</small><b>{cierre}</b></div></div>'
        f'<div class="promo-ad-badge"><span>🏢</span><div><small>CASILLERO AUTORIZADO</small><b>{cas_txt}</b></div></div>'
        f'<div class="promo-ad-badge"><span>✨</span><div><small>CARGA MIXTA</small><b>Variedad de productos</b></div></div>'
        f'</div>'
        f'<a class="promo-ad-cta" href="{url_wa}" target="_blank" rel="noopener noreferrer">'
        f"💬 Reservar cupo y consultar</a>"
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
    """Lee nombre, DNI, teléfono y dirección del casillero activo."""
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
    """Actualiza datos editables. Correo y código de casillero no cambian."""
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


def pintar_vista_actividad(total_cotizaciones=0):
    """Panel de actividad: concentra documentos y seguimiento sin recargar la navegación."""
    with st.container(key="vista_actividad"):
        st.markdown("#### 📌 Actividad")
        st.caption("Consulte sus tarifas, seguimiento y documentos desde un solo lugar.")
        cot, env, fichas = st.columns(3, gap="small")
        with cot:
            st.button(
                f"📄 Cotizaciones\n{int(total_cotizaciones or 0)} registradas",
                type="primary",
                key="actividad_cotizaciones",
                use_container_width=True,
                on_click=ir_a_mis_cotizaciones,
            )
        with env:
            st.button(
                "📦 Envíos\nSeguimiento de carga",
                key="actividad_envios",
                use_container_width=True,
                on_click=ir_a_envios,
            )
        with fichas:
            st.button(
                "🏷️ Fichas\nDocumentos disponibles",
                key="actividad_fichas",
                use_container_width=True,
                on_click=ir_a_fichas,
            )


def pintar_vista_mas():
    """Pantalla personal: cuenta, direcciones, catálogo, soporte y sesión."""
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
        if hub_activo == "china":
            st.markdown('<div class="mas-seccion mas-seccion-modulos">Cuenta y herramientas</div>', unsafe_allow_html=True)
            with st.container(key="mas_modulos"):
                st.button("📍  Direcciones de envío", key="mas_direcciones", use_container_width=True, on_click=abrir_direcciones_desde_mas)
                if catalogo_disponible_en_hub_actual():
                    st.button("🛍️  Catálogo", key="mas_catalogo", use_container_width=True, on_click=ir_a_catalogo)
        with st.container(key="mas_sesion"):
            st.markdown('<div class="mas-seccion">Soporte y sesión</div>', unsafe_allow_html=True)
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
    """Reserva altura real al final de la vista para que el último botón quede sobre la píldora."""
    with st.container(key=clave):
        st.markdown("&nbsp;")


def pintar_barra_inferior(total_cotizaciones=0, casillero=None):
    """Píldora flotante simple: Inicio, Cotizar, Actividad y Más."""
    vista = st.session_state.get("vista_activa") or st.session_state.get("sub_tab_inicio") or "Inicio"
    inicio_activo = vista == "Inicio"
    actividad_activa = vista in ("Actividad", "Mis Cotizaciones", "Mis Envíos", "Etiqueta")
    cotizador_activo = vista == "Cotizador"
    mas_activo = vista in ("Más", "Configuración", "Consultas")
    n_badge = int(total_cotizaciones or 0)

    st.markdown(
        f"<style>:root {{ --ccm-cot-badge: \"{n_badge}\"; }}</style>",
        unsafe_allow_html=True,
    )

    items = [("inicio", "🏠", "Inicio", inicio_activo)]
    if modulo_china_disponible_en_hub_actual("Cotizador"):
        items.append(("cotizador", "🧮", "Cotizar", cotizador_activo))
    if st.session_state.get("hub") == "china":
        items.append(("actividad", "📌", "Actividad", actividad_activa))
    items.append(("mas", "☰", "Más", mas_activo))
    with st.container(key="bottom_nav"):
        cols = st.columns(len(items), gap="small")
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
                elif dest == "actividad":
                    st.button(
                        f"{icono}\n{etiqueta}",
                        type="primary" if activo else "secondary",
                        key=f"bnav_{dest}",
                        use_container_width=True,
                        on_click=ir_a_actividad,
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
    """La barra se fija con CSS; no se modifica el DOM de Streamlit desde JavaScript."""
    # El script antiguo observaba y modificaba el DOM en cada actualización.
    # En algunos navegadores podía ocultar la vista completa, dejando una
    # pantalla blanca. Las reglas CSS de la aplicación ya fijan esta barra.
    return
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
                  doc.querySelector(".st-key-btn_confirmar_tarifa");
                const vistaModulo = catalogo || cotizador;
                const historial = doc.querySelector('[class~="st-key-vista_historial"]') || doc.querySelector(".st-key-vista_historial");
                const envios = doc.querySelector('[class~="st-key-vista_envios"]') || doc.querySelector(".st-key-vista_envios");
                let hueco = "calc(200px + env(safe-area-inset-bottom, 0px))";
                if (mas || vistaModulo || inicio || envios) hueco = "0px";
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
                    const sesion = caja.querySelector('[class~="st-key-mas_sesion"]') || caja.querySelector(".st-key-mas_sesion");
                    const safeMas = caja.querySelector('[class~="st-key-safe_mas"]') || caja.querySelector(".st-key-safe_mas");
                    let sesionHost = sesion;
                    if (sesionHost) {
                      while (sesionHost.parentElement && sesionHost.parentElement !== caja) {
                        sesionHost = sesionHost.parentElement;
                      }
                    }
                    caja.style.setProperty("box-sizing", "border-box", "important");
                    const tieneModulos = !!(caja.querySelector('[class~="st-key-mas_modulos"]') || caja.querySelector(".st-key-mas_modulos"));
                    if (tieneModulos) {
                      // Valor fijo: no depende del scroll, evitando ciclos de medición y vibración.
                      caja.style.setProperty("display", "block", "important");
                      caja.style.setProperty("min-height", "0px", "important");
                      caja.style.setProperty("padding-bottom", "calc(140px + env(safe-area-inset-bottom, 0px))", "important");
                      if (sesionHost) {
                        sesionHost.style.setProperty("margin-top", "0px", "important");
                        sesionHost.style.setProperty("margin-bottom", "0px", "important");
                      }
                    } else {
                      // Sin módulos, la cuenta y la sesión se presentan como un bloque compacto.
                      caja.style.setProperty("display", "block", "important");
                      caja.style.setProperty("min-height", "0px", "important");
                      caja.style.setProperty("padding-bottom", "112px", "important");
                      if (sesionHost) {
                        sesionHost.style.setProperty("margin-top", "48px", "important");
                        sesionHost.style.setProperty("margin-bottom", "0px", "important");
                        sesionHost.style.setProperty("padding-bottom", "0px", "important");
                      }
                    }
                    if (safeMas) {
                      safeMas.style.setProperty("height", "0px", "important");
                      safeMas.style.setProperty("min-height", "0px", "important");
                      safeMas.style.setProperty("margin", "0px", "important");
                      safeMas.style.setProperty("padding", "0px", "important");
                      safeMas.style.setProperty("overflow", "hidden", "important");
                    }
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
                  rootDoc.querySelectorAll("button, a, iframe").forEach((el) => {
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
                  const raizObservada = doc.querySelector("[data-testid='stAppViewContainer']") || doc.body;
                  new MutationObserver(anclarSuave).observe(raizObservada, { childList: true, subtree: true });
                } catch (e) {}
              }
            })();
            </script>
            """,
            height=0,
            scrolling=False,
        )


def sincronizar_altura_encabezado_fijo():
    """Mide el borde inferior del bloque congelado y actualiza --header-offset."""
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
    """Auto-scroll suave hasta un ancla, dejando el encabezado congelado por encima."""
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
    """Auto-scroll suave hasta la tarjeta que hay que confirmar (bajo el encabezado fijo)."""
    desplazar_a_ancla("cotizacion-foco-pendiente")


def desplazar_a_acciones_emit():
    """Tras emitir, deja los botones de PDF e Ir a Cotizaciones sobre la píldora."""
    desplazar_a_ancla("ccm-acciones-emit", alinear="end")


# ---------------------------------------------------------
# 2. GENERADORES DE PDF NATIVOS CON HORA DE HONDURAS
# ---------------------------------------------------------
@lru_cache(maxsize=1)
def cargar_logo_jpeg():
    """JPEG del logo CCM para incrustar en todos los PDF imprimibles."""
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
    """Banner azul tipográfico: nombre centrado a todo el ancho; datos debajo."""
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


@st.cache_data(ttl=900, show_spinner=False, max_entries=64)
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


@st.cache_data(ttl=900, show_spinner=False, max_entries=64)
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
# 3. BASE DE DATOS SQLITE & UTILIDADES
# ---------------------------------------------------------
def hash_pwd(password):
    """Hash con sal y derivación lenta; nunca guarda contraseñas en texto plano."""
    salt = os.urandom(16)
    derivada = hashlib.scrypt(str(password).encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(derivada).decode("ascii")


def verificar_pwd(password, almacenada):
    """Acepta hashes SHA-256 históricos y los migra al siguiente inicio correcto."""
    valor = str(almacenada or "")
    if valor.startswith("scrypt$"):
        try:
            _, salt_b64, hash_b64 = valor.split("$", 2)
            salt = base64.b64decode(salt_b64)
            esperada = base64.b64decode(hash_b64)
            calculada = hashlib.scrypt(str(password).encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
            return hmac.compare_digest(calculada, esperada)
        except (ValueError, TypeError):
            return False
    # Compatibilidad de una sola vez con cuentas creadas antes de esta mejora.
    anterior = hashlib.sha256(str(password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(anterior, valor)


@st.cache_resource(show_spinner=False)
def obtener_pool_postgres():
    max_size = int(os.environ.get("CCM_DB_POOL_MAX", "8") or 8)
    return PoolPostgresSimple(DATABASE_URL, min_size=1, max_size=max_size, timeout=20)


def get_db():
    if USA_SUPABASE:
        pool = obtener_pool_postgres()
        return ConexionPostgresCompatible(pool.obtener(), pool=pool)
    conn = sqlite3.connect(DB_NAME, timeout=30)
    # Evita que una escritura breve de otro ciclo de Streamlit haga que el
    # botón parezca no responder; SQLite espera hasta 30 segundos por el lock.
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def validar_esquema_supabase():
    """Comprueba que las tablas creadas en Supabase estén disponibles antes de operar."""
    requeridas = {
        "usuarios", "direcciones_entrega", "config_maritima", "cotizaciones", "paquetes",
        "catalogo_productos", "carrito_catalogo", "permisos_usuario", "config_sistema",
    }
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        existentes = {fila[0] for fila in cur.fetchall()}
    faltantes = sorted(requeridas - existentes)
    if faltantes:
        raise sqlite3.OperationalError(
            "Faltan tablas en Supabase: " + ", ".join(faltantes)
        )


def init_db():
    if USA_SUPABASE:
        # El esquema no cambia durante una sesión normal. Validarlo una vez
        # evita una conexión y consulta adicional en cada clic de Streamlit.
        if not st.session_state.get("_ccm_esquema_supabase_validado"):
            validar_esquema_supabase()
            st.session_state["_ccm_esquema_supabase_validado"] = True
        return
    with get_db() as conn:
        c = conn.cursor()
        # WAL permite lecturas mientras se realiza una escritura y NORMAL evita
        # sincronizaciones de disco excesivas sin comprometer la integridad.
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT UNIQUE NOT NULL,
                nombre_completo TEXT NOT NULL,
                dni TEXT NOT NULL,
                rtn TEXT,
                correo_principal TEXT UNIQUE NOT NULL,
                telefono_principal TEXT NOT NULL,
                departamento TEXT NOT NULL,
                ciudad TEXT NOT NULL,
                direccion_exacta TEXT NOT NULL,
                rubro_carga TEXT,
                modalidad_entrega TEXT,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL,
                activo INTEGER DEFAULT 1,
                fecha_creacion TEXT NOT NULL
            )
            """
        )
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
                fecha_creacion TEXT NOT NULL,
                activa INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS config_maritima (
                clave TEXT PRIMARY KEY,
                valor REAL NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS cotizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT NOT NULL,
                alto_cm REAL,
                ancho_cm REAL,
                largo_cm REAL,
                peso_lb REAL,
                volumen_m3 REAL,
                volumen_ft3 REAL,
                total_usd REAL,
                fecha TEXT NOT NULL,
                confirmada INTEGER NOT NULL DEFAULT 0,
                fecha_confirmacion TEXT,
                fecha_creacion TEXT
            )
            """
        )
        c.execute("PRAGMA table_info(cotizaciones)")
        columnas_cot = {fila[1] for fila in c.fetchall()}
        if "confirmada" not in columnas_cot:
            c.execute("ALTER TABLE cotizaciones ADD COLUMN confirmada INTEGER NOT NULL DEFAULT 0")
        if "fecha_confirmacion" not in columnas_cot:
            c.execute("ALTER TABLE cotizaciones ADD COLUMN fecha_confirmacion TEXT")
        if "fecha_creacion" not in columnas_cot:
            c.execute("ALTER TABLE cotizaciones ADD COLUMN fecha_creacion TEXT")
        c.execute(
            """
            UPDATE cotizaciones
            SET fecha_creacion = fecha
            WHERE fecha_creacion IS NULL OR TRIM(fecha_creacion) = ''
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS paquetes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking TEXT UNIQUE NOT NULL,
                codigo_casillero TEXT NOT NULL,
                descripcion TEXT,
                contenedor_id TEXT,
                estado TEXT NOT NULL,
                fecha_actualizacion TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS catalogo_productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                categoria TEXT,
                proveedor TEXT,
                precio_fabrica_cny REAL,
                precio_fabrica_usd REAL,
                moq INTEGER DEFAULT 1,
                peso_kg REAL DEFAULT 0.5,
                volumen_m3 REAL DEFAULT 0.005,
                imagen_url TEXT,
                url_proveedor TEXT,
                fuente TEXT DEFAULT '1688',
                fecha_actualizacion TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS carrito_catalogo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT NOT NULL,
                sku TEXT NOT NULL,
                nombre TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                precio_unitario_usd REAL NOT NULL,
                peso_unitario_kg REAL NOT NULL,
                volumen_unitario_m3 REAL NOT NULL,
                imagen_url TEXT,
                fecha TEXT NOT NULL
            )
            """
        )
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_libra', 3.50)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_m3', 680.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('minimo_cobro_usd', 10.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('divisor_peso_volumetrico', 390.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('umbral_minimo_lb', 3.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('umbral_paqueteria_lb', 99.00)")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS permisos_usuario (
                codigo_casillero TEXT PRIMARY KEY,
                hub_china INTEGER NOT NULL DEFAULT 1,
                hub_eeuu INTEGER NOT NULL DEFAULT 0,
                hub_honduras INTEGER NOT NULL DEFAULT 0,
                mod_cotizador INTEGER NOT NULL DEFAULT 1,
                mod_catalogo INTEGER NOT NULL DEFAULT 1,
                mod_cotizaciones INTEGER NOT NULL DEFAULT 1,
                mod_envios INTEGER NOT NULL DEFAULT 1,
                mod_fichas INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS config_sistema (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL,
                descripcion TEXT
            )
            """
        )
        c.execute(
            "INSERT OR IGNORE INTO config_sistema (clave, valor, descripcion) VALUES ('TASA_USD_HNL', '24.85', 'Tasa USD a lempira')"
        )
        c.execute(
            "INSERT OR IGNORE INTO config_sistema (clave, valor, descripcion) VALUES ('COMISION_CCM_PORCENTAJE', '0.10', 'Comisión CCM sobre FOB')"
        )

        # Índices de las rutas más consultadas por cada rerun de Streamlit.
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cotizaciones_casillero_fecha "
            "ON cotizaciones(codigo_casillero, fecha_creacion DESC, id DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cotizaciones_casillero_confirmada "
            "ON cotizaciones(codigo_casillero, confirmada, fecha_confirmacion)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_paquetes_casillero "
            "ON paquetes(codigo_casillero, fecha_actualizacion DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_direcciones_casillero "
            "ON direcciones_entrega(codigo_casillero, activa, id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_usuarios_dni ON usuarios(dni)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_usuarios_correo_normalizado "
            "ON usuarios(LOWER(TRIM(correo_principal)))"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_usuarios_rol_nombre "
            "ON usuarios(rol, nombre_completo)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_carrito_casillero_sku "
            "ON carrito_catalogo(codigo_casillero, sku)"
        )

        # No se crean usuarios, contraseñas ni datos de demostración en un
        # arranque de producción. Las cuentas se gestionan desde el panel
        # administrativo o mediante el bootstrap protegido por Secrets.


def asegurar_indices_rendimiento():
    """Crea los índices de las rutas críticas tanto en SQLite como PostgreSQL."""
    sentencias = (
        "CREATE INDEX IF NOT EXISTS idx_cotizaciones_casillero_fecha "
        "ON cotizaciones(codigo_casillero, fecha_creacion DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cotizaciones_casillero_confirmada "
        "ON cotizaciones(codigo_casillero, confirmada, fecha_confirmacion)",
        "CREATE INDEX IF NOT EXISTS idx_paquetes_casillero "
        "ON paquetes(codigo_casillero, fecha_actualizacion DESC)",
        "CREATE INDEX IF NOT EXISTS idx_direcciones_casillero "
        "ON direcciones_entrega(codigo_casillero, activa, id)",
        "CREATE INDEX IF NOT EXISTS idx_usuarios_dni ON usuarios(dni)",
        "CREATE INDEX IF NOT EXISTS idx_usuarios_correo_normalizado "
        "ON usuarios(LOWER(TRIM(correo_principal)))",
        "CREATE INDEX IF NOT EXISTS idx_usuarios_rol_nombre "
        "ON usuarios(rol, nombre_completo)",
        "CREATE INDEX IF NOT EXISTS idx_carrito_casillero_sku "
        "ON carrito_catalogo(codigo_casillero, sku)",
    )
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            for sentencia in sentencias:
                cursor.execute(sentencia)
            conn.commit()
        return True
    except Exception as exc:
        print(f"[CCM rendimiento] No se pudieron crear todos los índices: {exc}", flush=True)
        return False


@st.cache_resource(show_spinner=False)
def inicializar_persistencia():
    """Prepara esquema e índices una vez por proceso, no una vez por botón."""
    init_db()
    asegurar_esquema_direcciones()
    asegurar_indices_rendimiento()
    return True


inicializar_persistencia()


@st.cache_data(ttl=120, show_spinner=False)
def get_tarifa(clave):
    """Devuelve siempre una tarifa numérica, aun si Supabase la almacena como texto."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT valor FROM config_maritima WHERE clave = ?", (clave,))
        res = c.fetchone()
    if not res or res[0] is None:
        return 0.0
    try:
        # Acepta tanto 3.50 como 3,50 en configuraciones antiguas.
        return float(str(res[0]).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def set_tarifa(clave, valor):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO config_maritima (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )
        conn.commit()
    get_tarifa.clear()


@st.cache_data(ttl=120, show_spinner=False)
def get_config_sistema(clave, valor_default=""):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT valor FROM config_sistema WHERE clave = ?", (clave,))
            row = c.fetchone()
            return row[0] if row else valor_default
    except Exception:
        return valor_default


def set_config_sistema(clave, valor, descripcion=""):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO config_sistema (clave, valor, descripcion) VALUES (?, ?, ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, descripcion = COALESCE(excluded.descripcion, config_sistema.descripcion)
            """,
            (clave, str(valor), descripcion),
        )
        conn.commit()
    get_config_sistema.clear()


PREFIJO_CASILLERO = "CCM-"


def nucleo_casillero_desde_id(valor):
    """Toma los primeros 8 dígitos del DNI o del código ingresado."""
    texto = str(valor or "").strip().upper()
    if texto.startswith(PREFIJO_CASILLERO):
        texto = texto[len(PREFIJO_CASILLERO) :]
    digitos = "".join(filter(str.isdigit, texto))
    if len(digitos) >= 8:
        return digitos[:8]
    if digitos:
        return digitos.zfill(8)
    return ""


def generar_codigo_casillero_dni(dni_raw):
    nucleo = nucleo_casillero_desde_id(dni_raw)
    if not nucleo:
        return ""
    return f"{PREFIJO_CASILLERO}{nucleo}"


def formatear_casillero(codigo):
    return generar_codigo_casillero_dni(codigo) or str(codigo or "").strip()


def codigo_casillero_desde_usuario(codigo, dni):
    dni_digitos = "".join(filter(str.isdigit, str(dni or "")))
    if len(dni_digitos) >= 8:
        return generar_codigo_casillero_dni(dni)
    return formatear_casillero(codigo)


def coincidencias_casillero(valor):
    """Variantes de un casillero (15011985, CCM-15011985, etc.) para consultas IN (...)."""
    vistos = []
    for candidato in (str(valor or "").strip(), formatear_casillero(valor), nucleo_casillero_desde_id(valor)):
        if candidato and candidato not in vistos:
            vistos.append(candidato)
    nucleo = nucleo_casillero_desde_id(valor)
    if nucleo:
        con_prefijo = f"{PREFIJO_CASILLERO}{nucleo}"
        if con_prefijo not in vistos:
            vistos.append(con_prefijo)
    return vistos


def es_rol_admin(rol=None):
    return normalizar_rol(rol if rol is not None else st.session_state.get("rol")) in ROLES_ADMIN


def es_superadmin(rol=None):
    return normalizar_rol(rol if rol is not None else st.session_state.get("rol")) == "superadmin"


def normalizar_rol(rol):
    """Evita una sesión válida sin vista cuando la BD trae un rol vacío o con mayúsculas."""
    valor = str(rol or "").strip().lower()
    return valor if valor in ("cliente", "admin", "superadmin") else "cliente"


def permisos_default(rol="cliente"):
    return {
        "hub_china": 1,
        "hub_eeuu": 1,
        "hub_honduras": 1,
        "mod_cotizador": 1,
        "mod_catalogo": 1,
        "mod_cotizaciones": 1,
        "mod_envios": 1,
        "mod_fichas": 1,
    }


def asegurar_permisos_casillero(casillero, rol="cliente"):
    cas = formatear_casillero(casillero)
    if not cas:
        return
    vals = permisos_default(rol)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO permisos_usuario (
                codigo_casillero, hub_china, hub_eeuu, hub_honduras,
                mod_cotizador, mod_catalogo, mod_cotizaciones, mod_envios, mod_fichas
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codigo_casillero) DO UPDATE SET
                hub_china = excluded.hub_china,
                hub_eeuu = excluded.hub_eeuu,
                hub_honduras = excluded.hub_honduras,
                mod_cotizador = excluded.mod_cotizador,
                mod_catalogo = excluded.mod_catalogo,
                mod_cotizaciones = excluded.mod_cotizaciones,
                mod_envios = excluded.mod_envios,
                mod_fichas = excluded.mod_fichas
            """,
            (
                cas,
                bool(vals["hub_china"]),
                bool(vals["hub_eeuu"]),
                bool(vals["hub_honduras"]),
                bool(vals["mod_cotizador"]),
                bool(vals["mod_catalogo"]),
                bool(vals["mod_cotizaciones"]),
                bool(vals["mod_envios"]),
                bool(vals["mod_fichas"]),
            ),
        )
    st.session_state.pop(f"_ccm_permisos_{cas}", None)


def abrir_permisos_todos_los_usuarios():
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE permisos_usuario SET
                hub_china=TRUE, hub_eeuu=TRUE, hub_honduras=TRUE,
                mod_cotizador=TRUE, mod_catalogo=TRUE, mod_cotizaciones=TRUE, mod_envios=TRUE, mod_fichas=TRUE
            """
        )
        conn.commit()


def permisos_de(casillero=None):
    cas = formatear_casillero(casillero or st.session_state.get("casillero", ""))
    base = permisos_default(st.session_state.get("rol", "cliente"))
    if not cas:
        return base
    clave_cache = f"_ccm_permisos_{cas}"
    en_cache = st.session_state.get(clave_cache)
    if isinstance(en_cache, dict):
        return en_cache
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT hub_china, hub_eeuu, hub_honduras, mod_cotizador, mod_catalogo,
                       mod_cotizaciones, mod_envios, mod_fichas
                FROM permisos_usuario WHERE codigo_casillero = ?
                """,
                (cas,),
            )
            row = c.fetchone()
        if not row:
            asegurar_permisos_casillero(cas, st.session_state.get("rol", "cliente"))
            permisos = permisos_default(st.session_state.get("rol", "cliente"))
            st.session_state[clave_cache] = permisos
            return permisos
        claves = (
            "hub_china",
            "hub_eeuu",
            "hub_honduras",
            "mod_cotizador",
            "mod_catalogo",
            "mod_cotizaciones",
            "mod_envios",
            "mod_fichas",
        )
        permisos = dict(zip(claves, [int(v or 0) for v in row]))
        st.session_state[clave_cache] = permisos
        return permisos
    except Exception:
        return base


def usuario_puede_hub(hub_id, casillero=None):
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True
    col = HUB_PERMISO_COL.get(hub_id)
    if not col:
        return False
    return bool(permisos_de(casillero).get(col, 0))


def usuario_puede_modulo(mod_id, casillero=None):
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True
    col = MODULO_PERMISO_COL.get(mod_id)
    if not col:
        return False
    return bool(permisos_de(casillero).get(col, 0))


def guardar_permisos(casillero, datos):
    cas = formatear_casillero(casillero)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO permisos_usuario (
                codigo_casillero, hub_china, hub_eeuu, hub_honduras,
                mod_cotizador, mod_catalogo, mod_cotizaciones, mod_envios, mod_fichas
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codigo_casillero) DO UPDATE SET
                hub_china = excluded.hub_china,
                hub_eeuu = excluded.hub_eeuu,
                hub_honduras = excluded.hub_honduras,
                mod_cotizador = excluded.mod_cotizador,
                mod_catalogo = excluded.mod_catalogo,
                mod_cotizaciones = excluded.mod_cotizaciones,
                mod_envios = excluded.mod_envios,
                mod_fichas = excluded.mod_fichas
            """,
            (
                cas,
                bool(datos.get("hub_china", 0)),
                bool(datos.get("hub_eeuu", 0)),
                bool(datos.get("hub_honduras", 0)),
                bool(datos.get("mod_cotizador", 0)),
                bool(datos.get("mod_catalogo", 0)),
                bool(datos.get("mod_cotizaciones", 0)),
                bool(datos.get("mod_envios", 0)),
                bool(datos.get("mod_fichas", 0)),
            ),
        )
    st.session_state.pop(f"_ccm_permisos_{cas}", None)


def _migrar_casillero_tablas(conn, origen, destino):
    for tabla in ("cotizaciones", "paquetes", "direcciones_entrega", "carrito_catalogo", "permisos_usuario"):
        try:
            conn.execute(
                f"UPDATE {tabla} SET codigo_casillero = ? WHERE codigo_casillero = ?",
                (destino, origen),
            )
        except sqlite3.IntegrityError:
            if tabla == "permisos_usuario":
                conn.execute("DELETE FROM permisos_usuario WHERE codigo_casillero = ?", (origen,))


def asegurar_superadmin():
    # Solo puede crear la cuenta raíz cuando un administrador configuró una
    # clave de bootstrap privada. Nunca sobreescribe la contraseña existente.
    if not CLAVE_INICIAL_SUPERADMIN:
        return
    cas_root = generar_codigo_casillero_dni(DNI_SUPERADMIN)
    hash_root = hash_pwd(CLAVE_INICIAL_SUPERADMIN)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, codigo_casillero, correo_principal, rol FROM usuarios WHERE dni = ? OR codigo_casillero = ?",
            (DNI_SUPERADMIN, cas_root),
        )
        ocupantes = c.fetchall()
        for uid, cas_old, correo, rol in ocupantes:
            if rol == "superadmin" or correo == CORREO_SUPERADMIN:
                continue
            nuevo_dni = "1501198500990"
            nuevo_cas = generar_codigo_casillero_dni(nuevo_dni)
            c.execute("SELECT id FROM usuarios WHERE codigo_casillero = ? AND id != ?", (nuevo_cas, uid))
            if c.fetchone():
                nuevo_cas = f"CCM-1501{str(uid).zfill(4)}"
            _migrar_casillero_tablas(conn, cas_old, nuevo_cas)
            c.execute(
                "UPDATE usuarios SET dni = ?, codigo_casillero = ? WHERE id = ?",
                (nuevo_dni, nuevo_cas, uid),
            )

        c.execute(
            "SELECT id FROM usuarios WHERE rol = 'superadmin' OR correo_principal = ? OR dni = ?",
            (CORREO_SUPERADMIN, DNI_SUPERADMIN),
        )
        existente = c.fetchone()
        if existente:
            c.execute(
                """
                UPDATE usuarios SET nombre_completo = ?, dni = ?, codigo_casillero = ?,
                    correo_principal = ?, rol = 'superadmin', activo = 1
                WHERE id = ?
                """,
                (NOMBRE_SUPERADMIN, DNI_SUPERADMIN, cas_root, CORREO_SUPERADMIN, existente[0]),
            )
        else:
            c.execute(
                """
                INSERT INTO usuarios (
                    codigo_casillero, nombre_completo, dni, correo_principal,
                    telefono_principal, departamento, ciudad, direccion_exacta,
                    password_hash, rol, activo, fecha_creacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'superadmin', TRUE, ?)
                """,
                (
                    cas_root,
                    NOMBRE_SUPERADMIN,
                    DNI_SUPERADMIN,
                    CORREO_SUPERADMIN,
                    "+504 9577-1099",
                    "Intibucá",
                    "San Juan",
                    "Oficina Central CCM",
                    hash_root,
                    obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
        c.execute("SELECT codigo_casillero, rol FROM usuarios")
        cuentas = c.fetchall()
        for cas, rol in cuentas:
            vals = permisos_default(rol)
            c.execute(
                """
                INSERT OR IGNORE INTO permisos_usuario (
                    codigo_casillero, hub_china, hub_eeuu, hub_honduras,
                    mod_cotizador, mod_catalogo, mod_cotizaciones, mod_envios, mod_fichas
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cas,
                    bool(vals["hub_china"]),
                    bool(vals["hub_eeuu"]),
                    bool(vals["hub_honduras"]),
                    bool(vals["mod_cotizador"]),
                    bool(vals["mod_catalogo"]),
                    bool(vals["mod_cotizaciones"]),
                    bool(vals["mod_envios"]),
                    bool(vals["mod_fichas"]),
                ),
            )
            if rol in ROLES_ADMIN or PERMISOS_ABIERTOS_TEMPORAL:
                c.execute(
                    """
                    UPDATE permisos_usuario SET hub_china=TRUE, hub_eeuu=TRUE, hub_honduras=TRUE,
                        mod_cotizador=TRUE, mod_catalogo=TRUE, mod_cotizaciones=TRUE, mod_envios=TRUE, mod_fichas=TRUE
                    WHERE codigo_casillero = ?
                    """,
                    (cas,),
                )
        conn.commit()


def restaurar_datos_operativos_cliente():
    """Marca el arranque operativo sin reescribir fechas de cotización (se conserva el orden cronológico)."""
    if get_config_sistema("datos_operativos_restaurados", "") == "1":
        return
    set_config_sistema("datos_operativos_restaurados", "1", "Módulos habilitados sin alterar timestamps de cotización")


def migrar_prefijo_casillero():
    tablas_hijas = ("cotizaciones", "paquetes", "direcciones_entrega", "carrito_catalogo")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, codigo_casillero, dni FROM usuarios")
        filas = c.fetchall()
        for uid, codigo, dni in filas:
            nuevo = codigo_casillero_desde_usuario(codigo, dni)
            if not nuevo or nuevo == codigo:
                continue
            c.execute("SELECT id FROM usuarios WHERE codigo_casillero = ? AND id != ?", (nuevo, uid))
            destino_existe = c.fetchone() is not None
            _migrar_casillero_tablas(conn, codigo, nuevo)
            if destino_existe:
                continue
            c.execute("UPDATE usuarios SET codigo_casillero = ? WHERE id = ?", (nuevo, uid))
        conn.commit()


# Estas tareas son migraciones de la instalación SQLite original. En Supabase
# no se ejecutan durante una sesión de cliente: una conexión lenta o una
# migración antigua no debe impedir que aparezca el portal tras el login.
if not st.session_state.get("_ccm_arranque_db_realizado"):
    if not USA_SUPABASE:
        migrar_prefijo_casillero()
        asegurar_superadmin()
        abrir_permisos_todos_los_usuarios()
        restaurar_datos_operativos_cliente()
    st.session_state["_ccm_arranque_db_realizado"] = True
purgar_cotizaciones_si_corresponde()


def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return "".join(random.choice(caracteres) for _ in range(8))


def normalizar_correo(correo):
    return str(correo or "").strip().lower()


# Medidas internas de un contenedor 40' High Cube y peso máximo IHTT (Honduras).
CONTENEDOR_40_ALTO_M = 2.69
CONTENEDOR_40_ANCHO_M = 2.35
CONTENEDOR_40_LARGO_M = 12.03
PESO_MAX_CONTENEDOR_HN_KG = 25_000.0
LB_POR_KG = 2.20462
PESO_MAX_PAQUETERIA_LB = 99.0


def peso_max_contenedor_hn_lb():
    return round(PESO_MAX_CONTENEDOR_HN_KG * LB_POR_KG, 2)


def max_alineado(min_v, max_v, step):
    n = math.floor((max_v - min_v) / step + 1e-9)
    return round(min_v + n * step, 4)


def limites_dimensiones(unidad_medida, comercial=False):
    if "Metros" in unidad_medida:
        factor = 1.0
        step = 0.01
        min_v = 0.01
        formato = "%.2f"
        defaults_paq = {"alto": 0.30, "ancho": 0.30, "largo": 0.40}
        defaults_com = {"alto": 1.20, "ancho": 1.20, "largo": 1.20}
        codigo = "m"
    elif "Pulgadas" in unidad_medida:
        factor = 1.0 / 0.0254
        step = 0.5
        min_v = 0.5
        formato = "%.1f"
        defaults_paq = {"alto": 12.0, "ancho": 12.0, "largo": 16.0}
        defaults_com = {"alto": 47.0, "ancho": 47.0, "largo": 47.0}
        codigo = "in"
    else:
        factor = 100.0
        step = 1.0
        min_v = 1.0
        formato = "%.0f"
        defaults_paq = {"alto": 30.0, "ancho": 30.0, "largo": 40.0}
        defaults_com = {"alto": 120.0, "ancho": 120.0, "largo": 120.0}
        codigo = "cm"

    maxes_fisicos = {
        "alto": max_alineado(min_v, CONTENEDOR_40_ALTO_M * factor, step),
        "ancho": max_alineado(min_v, CONTENEDOR_40_ANCHO_M * factor, step),
        "largo": max_alineado(min_v, CONTENEDOR_40_LARGO_M * factor, step),
    }
    # El campo permite registrar la medida real aun si excede el contenedor.
    # Así el cálculo se actualiza inmediatamente y la validación posterior
    # explica que debe dividirse la carga, en vez de dejar métricas antiguas.
    maxes = {
        clave: max_alineado(min_v, max(valor, 100.0 * factor), step)
        for clave, valor in maxes_fisicos.items()
    }
    base = defaults_com if comercial else defaults_paq
    defaults = {k: min(v, maxes_fisicos[k]) for k, v in base.items()}
    return {
        "min": min_v,
        "step": step,
        "formato": formato,
        "codigo": codigo,
        "defaults": defaults,
        "max": maxes,
        "max_fisico": maxes_fisicos,
    }


def limites_peso(unidad_peso, paqueteria):
    if paqueteria:
        # El campo permite ingresar hasta la capacidad legal del contenedor.
        # Si supera el umbral de paquetería, el Cotizador cambia el cálculo a
        # CBM automáticamente; no bloquea al usuario en 99 lb.
        max_lb = peso_max_contenedor_hn_lb()
        default_lb = 4.0
        min_lb = 0.5
        step_lb = 0.5
    else:
        max_lb = peso_max_contenedor_hn_lb()
        default_lb = 500.0
        min_lb = 1.0
        step_lb = 10.0

    if "Kilogramos" in unidad_peso:
        min_v = 0.1 if paqueteria else 1.0
        default = round(default_lb / LB_POR_KG, 1)
        max_v = round(max_lb / LB_POR_KG, 1)
        step = 0.1 if paqueteria else 1.0
        codigo = "kg"
        formato = "%.1f"
    else:
        min_v = min_lb
        default = default_lb
        max_v = max_lb
        step = step_lb
        codigo = "lb"
        formato = "%.1f"

    max_v = max_alineado(min_v, max_v, step)
    default = min(default, max_v)
    return {
        "min": min_v,
        "default": default,
        "max": max_v,
        "step": step,
        "codigo": codigo,
        "formato": formato,
    }


def campo_numerico(label, lim_min, valor, lim_max, paso, clave, formato, on_change=None):
    kwargs = {}
    if on_change is not None:
        kwargs["on_change"] = on_change
    if clave in st.session_state:
        try:
            actual = float(st.session_state[clave])
            limitado = min(max(actual, float(lim_min)), float(lim_max))
            if limitado != actual:
                st.session_state[clave] = limitado
        except (TypeError, ValueError):
            st.session_state[clave] = float(valor)
        valor_widget = st.number_input(
            label,
            min_value=float(lim_min),
            max_value=float(lim_max),
            step=float(paso),
            format=formato,
            key=clave,
            **kwargs,
        )
        return float(st.session_state.get(clave, valor_widget))
    valor_widget = st.number_input(
        label,
        min_value=float(lim_min),
        max_value=float(lim_max),
        value=float(valor),
        step=float(paso),
        format=formato,
        key=clave,
        **kwargs,
    )
    return float(st.session_state.get(clave, valor_widget))


# ---------------------------------------------------------
# 4. MOTOR DE CATÁLOGO 1688 ESTÁNDAR (BÚSQUEDA DINÁMICA)
# ---------------------------------------------------------
def calcular_costo_puesto_honduras(precio_fabrica_usd, peso_kg, vol_m3, cantidad=1):
    t_lb = get_tarifa("tarifa_libra")
    t_m3 = get_tarifa("tarifa_m3")
    min_usd = get_tarifa("minimo_cobro_usd")

    tasa_hnl = float(leer_config_moneda("TASA_USD_HNL", 24.85))
    comision_pct = float(leer_config_moneda("COMISION_CCM_PORCENTAJE", 0.10))

    fob_total_usd = precio_fabrica_usd * cantidad
    peso_total_kg = peso_kg * cantidad
    peso_total_lb = peso_total_kg * 2.20462
    vol_total_m3 = vol_m3 * cantidad

    umbral_min = float(get_tarifa("umbral_minimo_lb") or 3.0)
    umbral_paq = float(get_tarifa("umbral_paqueteria_lb") or 99.0)
    divisor = float(get_tarifa("divisor_peso_volumetrico") or 390.0)

    if peso_total_lb <= umbral_min:
        flete_usd = min_usd
    elif peso_total_lb <= umbral_paq:
        flete_usd = peso_total_lb * t_lb
    else:
        vol_peso = peso_total_kg / divisor
        cbm_facturable = max(vol_total_m3, vol_peso)
        flete_usd = cbm_facturable * t_m3

    comision_usd = fob_total_usd * comision_pct
    total_cif_usd = fob_total_usd + flete_usd + comision_usd
    total_cif_hnl = total_cif_usd * tasa_hnl

    return {
        "fob_total_usd": fob_total_usd,
        "peso_total_lb": peso_total_lb,
        "flete_maritimo_usd": flete_usd,
        "comision_usd": comision_usd,
        "total_estimado_usd": total_cif_usd,
        "total_estimado_hnl": total_cif_hnl,
    }


@st.cache_data(ttl=90, show_spinner=False)
def buscar_productos_1688_texto(keyword):
    kw_clean = keyword.strip().title()
    seed_id = abs(hash(keyword)) % 1000

    if any(k in keyword.lower() for k in ["martillo", "herramienta", "taladro", "llave"]):
        img_dinamica = f"https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=400&q=80&seed={seed_id}"
    elif any(k in keyword.lower() for k in ["porcelanato", "ceramica", "piso", "baño"]):
        img_dinamica = f"https://images.unsplash.com/photo-1584622650111-993a426fbf0a?auto=format&fit=crop&w=400&q=80&seed={seed_id}"
    else:
        img_dinamica = f"https://picsum.photos/400/300?random={seed_id}"

    return [
        {
            "sku": f"1688-DIR-{seed_id}",
            "nombre": f"{kw_clean} Calidad de Exportación",
            "precio_fabrica_cny": 58.00,
            "precio_fabrica_usd": 8.12,
            "moq": 10,
            "proveedor": "Foshan Industrial Export Co.",
            "peso_kg": 3.20,
            "volumen_m3": 0.009,
            "imagen_url": img_dinamica,
            "url_proveedor": "https://detail.1688.com",
            "fuente": "1688.com",
        },
        {
            "sku": f"1688-DIR-{seed_id + 1}",
            "nombre": f"{kw_clean} Industrial Reforzado",
            "precio_fabrica_cny": 135.00,
            "precio_fabrica_usd": 18.90,
            "moq": 5,
            "proveedor": "Guangzhou Hardware & Logistics Group",
            "peso_kg": 6.50,
            "volumen_m3": 0.018,
            "imagen_url": img_dinamica,
            "url_proveedor": "https://detail.1688.com",
            "fuente": "1688.com",
        },
    ]


def buscar_productos_1688_imagen(image_bytes):
    _ = image_bytes
    return [
        {
            "sku": "1688-VISUAL-001",
            "nombre": "Producto Detectado por Coincidencia Visual 1688",
            "precio_fabrica_cny": 88.00,
            "precio_fabrica_usd": 12.32,
            "moq": 10,
            "proveedor": "Zhejiang Export Manufacturing Ltd.",
            "peso_kg": 4.20,
            "volumen_m3": 0.012,
            "imagen_url": "https://images.unsplash.com/photo-1513519245088-0e12902e5a38?auto=format&fit=crop&w=400&q=80",
            "url_proveedor": "https://detail.1688.com",
            "fuente": "1688 Image Match",
        }
    ]


def agregar_producto_ae_a_casillero(casillero, producto, cantidad=1):
    cas = formatear_casillero(casillero)
    sku = producto.get("sku") or f"AE-{producto.get('product_id')}"
    _, fecha = estampa_tiempo_honduras()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, cantidad FROM carrito_catalogo WHERE codigo_casillero = ? AND sku = ?",
            (cas, sku),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """
                UPDATE carrito_catalogo
                SET cantidad = ?, precio_unitario_usd = ?, nombre = ?, imagen_url = ?,
                    peso_unitario_kg = ?, volumen_unitario_m3 = ?, fecha = ?
                WHERE id = ?
                """,
                (
                    int(row[1]) + int(cantidad or 1),
                    float(producto.get("precio_usd") or 0),
                    producto.get("titulo") or sku,
                    producto.get("imagen_url") or "",
                    float(producto.get("peso_kg") or 0.8),
                    float(producto.get("volumen_m3") or 0.004),
                    fecha,
                    row[0],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO carrito_catalogo (
                    codigo_casillero, sku, nombre, cantidad, precio_unitario_usd,
                    peso_unitario_kg, volumen_unitario_m3, imagen_url, fecha
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cas,
                    sku,
                    producto.get("titulo") or sku,
                    int(cantidad or 1),
                    float(producto.get("precio_usd") or 0),
                    float(producto.get("peso_kg") or 0.8),
                    float(producto.get("volumen_m3") or 0.004),
                    producto.get("imagen_url") or "",
                    fecha,
                ),
            )
        conn.commit()
    return sku


def listar_carrito_ae(casillero):
    cas = formatear_casillero(casillero)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sku, nombre, cantidad, precio_unitario_usd, imagen_url, fecha
            FROM carrito_catalogo
            WHERE codigo_casillero = ? AND sku LIKE 'AE-%'
            ORDER BY id DESC
            """,
            (cas,),
        )
        return cur.fetchall()


def _ejecutar_busqueda_aliexpress(modo, keyword, imagen_bytes, min_usd, max_usd, orden):
    if modo == "imagen":
        return buscar_aliexpress_imagen(imagen_bytes, min_usd=min_usd, max_usd=max_usd, orden=orden)
    return buscar_aliexpress_texto(keyword, min_usd=min_usd, max_usd=max_usd, orden=orden)


def _valor_numerico_producto(valor, unidad, destino):
    """Convierte peso a lb y dimensiones a pulgadas cuando la tienda indica unidad."""
    try:
        numero = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None
    u = str(unidad or "").lower()
    if destino == "peso":
        if "kg" in u or "kilogram" in u:
            return numero * LB_POR_KG
        if "oz" in u or "ounce" in u:
            return numero / 16.0
        return numero
    if "cm" in u or "centimeter" in u:
        return numero / 2.54
    if u in ("m", "meter", "meters"):
        return numero * 39.3701
    if "ft" in u or "feet" in u or "foot" in u:
        return numero * 12.0
    return numero


@st.cache_data(ttl=300, max_entries=128, show_spinner=False)
def consultar_producto_enlace_eeuu(enlace):
    """Obtiene metadatos públicos de cualquier tienda que permita su lectura.

    No intenta evadir CAPTCHA, inicio de sesión ni otras protecciones de una
    tienda: cuando no hay datos públicos, el usuario conserva la opción de
    completar la ficha manualmente.
    """
    url = str(enlace or "").strip()
    if not url.startswith(("https://", "http://")):
        return {"error": "Pegue un enlace completo que comience con https://."}
    try:
        respuesta = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=(3.05, 8),
            allow_redirects=True,
            stream=True,
        )
        respuesta.raise_for_status()
        contenido = bytearray()
        for bloque in respuesta.iter_content(chunk_size=65536):
            contenido.extend(bloque)
            if len(contenido) > 2_500_000:
                respuesta.close()
                return {"error": "La página es demasiado grande para procesarla automáticamente."}
        codificacion = respuesta.encoding or "utf-8"
        url_final = respuesta.url
        respuesta.close()
        pagina = contenido.decode(codificacion, errors="replace")
    except requests.RequestException as exc:
        return {"error": f"No se pudo consultar el enlace: {exc}"}

    pagina_baja = pagina.lower()
    if any(marca in pagina_baja for marca in ("robot check", "captcha", "automated access", "unusual traffic")):
        return {"error": "La tienda bloqueó la consulta automática de este producto. Use la foto y las medidas publicadas por la tienda para completar el paquete."}

    def meta(nombre):
        for etiqueta in re.findall(r"<meta\b[^>]*>", pagina, flags=re.I):
            coincide_nombre = re.search(r"(?:property|name)=[\"']" + re.escape(nombre) + r"[\"']", etiqueta, flags=re.I)
            coincide_valor = re.search(r"content=[\"']([^\"']+)[\"']", etiqueta, flags=re.I)
            if coincide_nombre and coincide_valor:
                return html.unescape(coincide_valor.group(1)).strip()
        return ""

    resultado = {
        "titulo": meta("og:title") or meta("twitter:title"),
        "descripcion": meta("og:description") or meta("twitter:description") or meta("description"),
        "imagen": meta("og:image") or meta("twitter:image"),
        "precio_usd": None,
        "moneda": meta("product:price:currency") or meta("og:price:currency") or "USD",
        "peso_lb": None, "ancho_in": None, "alto_in": None, "largo_in": None,
        "url_final": url_final,
    }
    precio_meta = meta("product:price:amount") or meta("og:price:amount")
    if precio_meta:
        try:
            resultado["precio_usd"] = float(re.sub(r"[^0-9.,]", "", precio_meta).replace(",", ""))
        except ValueError:
            pass
    if not resultado["titulo"]:
        titulo_html = re.search(r"<title[^>]*>(.*?)</title>", pagina, flags=re.I | re.S)
        if titulo_html:
            resultado["titulo"] = html.unescape(re.sub(r"\s+", " ", titulo_html.group(1))).strip()
    titulo_amazon = re.search(r'id=["\']productTitle["\'][^>]*>(.*?)<', pagina, flags=re.I | re.S)
    if titulo_amazon:
        resultado["titulo"] = html.unescape(re.sub(r"\s+", " ", titulo_amazon.group(1))).strip() or resultado["titulo"]
    if not resultado["imagen"]:
        imagen_amazon = re.search(r'id=["\']landingImage["\'][^>]+(?:data-old-hires|src)=["\']([^"\']+)', pagina, flags=re.I)
        if imagen_amazon:
            resultado["imagen"] = html.unescape(imagen_amazon.group(1))

    # JSON-LD suele contener las especificaciones de Amazon, Walmart, eBay y otras tiendas.
    nodos = []

    def reunir_nodos(valor):
        if isinstance(valor, dict):
            nodos.append(valor)
            for subvalor in valor.values():
                reunir_nodos(subvalor)
        elif isinstance(valor, list):
            for subvalor in valor:
                reunir_nodos(subvalor)

    for bloque in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", pagina, flags=re.I | re.S):
        try:
            dato = json.loads(html.unescape(bloque.strip()))
            reunir_nodos(dato)
        except (json.JSONDecodeError, TypeError):
            continue
    for nodo in nodos:
        if not isinstance(nodo, dict):
            continue
        if not resultado["titulo"]:
            resultado["titulo"] = str(nodo.get("name") or "").strip()
        imagen = nodo.get("image")
        if not resultado["imagen"] and imagen:
            resultado["imagen"] = imagen[0] if isinstance(imagen, list) else str(imagen)
        if not resultado["descripcion"]:
            resultado["descripcion"] = re.sub(r"\s+", " ", str(nodo.get("description") or "")).strip()
        oferta = nodo.get("offers")
        if isinstance(oferta, list):
            oferta = oferta[0] if oferta else None
        if isinstance(oferta, dict):
            if resultado["precio_usd"] is None:
                try:
                    resultado["precio_usd"] = float(str(oferta.get("price") or oferta.get("lowPrice") or "").replace(",", ""))
                except ValueError:
                    pass
            resultado["moneda"] = str(oferta.get("priceCurrency") or resultado["moneda"] or "USD").upper()
        for campo, destino in (("weight", "peso_lb"), ("width", "ancho_in"), ("height", "alto_in"), ("depth", "largo_in")):
            valor = nodo.get(campo)
            if resultado[destino] is None and isinstance(valor, dict):
                resultado[destino] = _valor_numerico_producto(valor.get("value"), valor.get("unitCode") or valor.get("unitText"), "peso" if destino == "peso_lb" else "medida")
            elif resultado[destino] is None and isinstance(valor, str):
                coincidencia = re.search(r"(\d+(?:[.,]\d+)?)\s*(lb|lbs|pounds?|kg|kilograms?|oz|inches?|in\.?|cm|ft|feet)", valor, flags=re.I)
                if coincidencia:
                    resultado[destino] = _valor_numerico_producto(coincidencia.group(1), coincidencia.group(2), "peso" if destino == "peso_lb" else "medida")

    texto = re.sub(r"<[^>]+>", " ", pagina)
    texto = html.unescape(re.sub(r"\s+", " ", texto))
    if resultado["peso_lb"] is None:
        peso = re.search(r"(?:item\s*)?weight\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(lb|lbs|pounds?|kg|kilograms?|oz)", texto, flags=re.I)
        if peso:
            resultado["peso_lb"] = _valor_numerico_producto(peso.group(1), peso.group(2), "peso")
    if not all(resultado[k] is not None for k in ("ancho_in", "alto_in", "largo_in")):
        medidas = re.search(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(inches?|in\.?|cm|ft|feet)", texto, flags=re.I)
        if medidas:
            vals = [_valor_numerico_producto(medidas.group(i), medidas.group(4), "medida") for i in (1, 2, 3)]
            resultado["largo_in"], resultado["ancho_in"], resultado["alto_in"] = vals
    if resultado["precio_usd"] is None:
        # Fallback para páginas sin JSON-LD: busca etiquetas de precio visibles.
        precio = re.search(r"(?:price|precio|ourprice|saleprice)[^0-9]{0,40}(?:US\$|\$|USD)?\s*(\d{1,6}(?:[.,]\d{2})?)", texto, flags=re.I)
        if precio:
            try:
                resultado["precio_usd"] = float(precio.group(1).replace(",", ""))
            except ValueError:
                pass
    titulo_limpio = str(resultado.get("titulo") or "").strip()
    host = urllib.parse.urlparse(url_final).netloc.lower().removeprefix("www.")
    if titulo_limpio.lower() in {host, host.replace(".com", ""), "amazon.com", "walmart.com", "ebay.com"}:
        resultado["titulo"] = ""
    resultado["descripcion"] = str(resultado.get("descripcion") or "").strip()[:900]
    return resultado


@st.cache_data(ttl=600, max_entries=128, show_spinner=False)
def descargar_imagen_producto(url):
    """Descarga la imagen al servidor para evitar bloqueos de hotlink en el navegador."""
    if not str(url or "").startswith(("https://", "http://")):
        return None
    try:
        respuesta = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CCM-Cotizador/1.0)"},
            timeout=(3.05, 6),
            stream=True,
        )
        tipo = respuesta.headers.get("content-type", "").lower()
        if respuesta.ok and tipo.startswith("image/"):
            contenido = bytearray()
            for bloque in respuesta.iter_content(chunk_size=65536):
                contenido.extend(bloque)
                if len(contenido) > 5_000_000:
                    respuesta.close()
                    return None
            respuesta.close()
            return bytes(contenido)
        respuesta.close()
    except requests.RequestException:
        pass
    return None


def calcular_paquete_eeuu(peso_lb, ancho, alto, largo, unidad, tarifa_lb, tarifa_m3):
    """Determina el método de cobro para un paquete EE. UU. → Honduras."""
    factor_in = 12.0 if unidad == "Pies" else 1.0
    ancho_m = float(ancho) * factor_in * 0.0254
    alto_m = float(alto) * factor_in * 0.0254
    largo_m = float(largo) * factor_in * 0.0254
    peso_real_lb = float(peso_lb)
    peso_volumetrico_lb = (float(ancho) * factor_in * float(alto) * factor_in * float(largo) * factor_in) / 166.0
    peso_cobrable_lb = max(peso_real_lb, peso_volumetrico_lb)
    peso_kg = peso_real_lb / LB_POR_KG
    volumen_m3 = ancho_m * alto_m * largo_m
    excede_contenedor = (
        alto_m > CONTENEDOR_40_ALTO_M + 1e-9
        or ancho_m > CONTENEDOR_40_ANCHO_M + 1e-9
        or largo_m > CONTENEDOR_40_LARGO_M + 1e-9
        or peso_kg > PESO_MAX_CONTENEDOR_HN_KG + 1e-9
    )
    usa_cbm = peso_cobrable_lb > PESO_MAX_PAQUETERIA_LB
    if excede_contenedor:
        return {
            "valido": False,
            "error": "El paquete supera las dimensiones internas o el peso legal de un contenedor 40′ HC. Divida la carga para cotizarla.",
        }
    if usa_cbm:
        cbm_por_peso = peso_kg / float(get_tarifa("divisor_peso_volumetrico") or 390.0)
        cbm_facturable = max(volumen_m3, cbm_por_peso)
        return {
            "valido": True, "modalidad": "Carga consolidada por CBM", "es_cbm": True,
            "peso_cobrable_lb": peso_cobrable_lb, "volumen_m3": volumen_m3,
            "cbm_facturable": cbm_facturable, "total_usd": cbm_facturable * float(tarifa_m3),
        }
    return {
        "valido": True, "modalidad": "Paquetería menor", "es_cbm": False,
        "peso_cobrable_lb": peso_cobrable_lb, "volumen_m3": volumen_m3,
        "cbm_facturable": 0.0, "total_usd": peso_cobrable_lb * float(tarifa_lb),
    }


def pintar_cotizador_eeuu(casillero):
    """Cotizador de paquetes EE. UU. → Honduras, sin catálogo de terceros."""
    st.markdown(
        "<section class='usq-hero'>"
        "<div class='usq-eyebrow'>🇺🇸 ENVÍOS DESDE ESTADOS UNIDOS</div>"
        "<h2>Cotizador EE. UU. <span>➜ Honduras</span></h2>"
        "<p>Agrega tu producto, valida sus medidas y recibe un estimado de compra y flete.</p>"
        f"<div class='usq-chip'>📍 Casillero activo: <strong>{html.escape(str(casillero))}</strong></div>"
        "</section>", unsafe_allow_html=True,
    )

    paquetes = st.session_state.setdefault("paquetes_eeuu", [])
    if st.session_state.pop("_us_limpiar_formulario", False):
        for clave in ("us_descripcion", "us_descripcion_origen", "us_enlace", "us_precio_unitario", "us_cantidad", "us_moneda_producto", "us_peso", "us_ancho", "us_alto", "us_largo", "us_imagen_producto", "us_imagen_producto_bytes", "us_foto_manual"):
            st.session_state.pop(clave, None)
    tarifa_default = float(get_tarifa("tarifa_eeuu_libra") or get_tarifa("tarifa_libra") or 0.0)
    tarifa_cbm_default = float(get_tarifa("tarifa_eeuu_m3") or get_tarifa("tarifa_m3") or 0.0)
    st.markdown("<div class='usq-section-title'><span>1</span> Tarifas y modalidad de envío</div>", unsafe_allow_html=True)
    with st.container(border=True):
        tarifa_col_1, tarifa_col_2 = st.columns(2)
        with tarifa_col_1:
            tarifa_lb = st.number_input(
                "📦 Tarifa por libra (USD)", min_value=0.0, max_value=500.0,
                value=float(st.session_state.get("us_tarifa_lb", tarifa_default)), step=0.01,
                format="%.2f", key="us_tarifa_lb",
            )
        with tarifa_col_2:
            tarifa_m3 = st.number_input(
                "🚢 Tarifa por CBM (USD)", min_value=0.0, max_value=10000.0,
                value=float(st.session_state.get("us_tarifa_m3", tarifa_cbm_default)), step=1.0,
                format="%.2f", key="us_tarifa_m3",
            )
        st.caption("Hasta 99 lb se cotiza por libra. Desde 99 lb el sistema cambia a CBM. Límite 40′ HC: 2.69 × 2.35 × 12.03 m y 25,000 kg.")

    st.markdown("<div class='usq-section-title'><span>2</span> Cotizador inteligente por enlace</div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(
            "<div class='usq-smart-head'><div class='usq-smart-icon'>✨</div><div><b>Pega el enlace de tu producto</b>"
            "<small>Buscaremos título, precio, imagen y especificaciones públicas para completar la ficha.</small></div></div>",
            unsafe_allow_html=True,
        )
        st.caption("Funciona con Amazon, Walmart, Best Buy, eBay, Target, Home Depot, Shopify y otras tiendas que publiquen información accesible.")
        st.text_input("Enlace del producto (opcional)", key="us_enlace", placeholder="https://www.amazon.com/...")
        if st.button("✨ Consultar enlace y autocompletar", key="btn_us_consultar_link", use_container_width=True):
            st.session_state.pop("us_imagen_producto", None)
            st.session_state.pop("us_imagen_producto_bytes", None)
            with st.spinner("Consultando datos públicos del producto..."):
                datos_link = consultar_producto_enlace_eeuu(st.session_state.get("us_enlace"))
            if datos_link.get("error"):
                st.warning(datos_link["error"])
            else:
                if datos_link.get("titulo"):
                    st.session_state["us_descripcion"] = datos_link["titulo"]
                if datos_link.get("descripcion"):
                    st.session_state["us_descripcion_origen"] = datos_link["descripcion"]
                if datos_link.get("precio_usd") is not None:
                    st.session_state["us_precio_unitario"] = round(float(datos_link["precio_usd"]), 2)
                st.session_state["us_moneda_producto"] = str(datos_link.get("moneda") or "USD").upper()
                if datos_link.get("peso_lb"):
                    st.session_state["us_peso"] = round(float(datos_link["peso_lb"]), 2)
                if datos_link.get("ancho_in"):
                    st.session_state["us_ancho"] = round(float(datos_link["ancho_in"]), 2)
                if datos_link.get("alto_in"):
                    st.session_state["us_alto"] = round(float(datos_link["alto_in"]), 2)
                if datos_link.get("largo_in"):
                    st.session_state["us_largo"] = round(float(datos_link["largo_in"]), 2)
                st.session_state["us_unidad"] = "Pulgadas"
                imagen = str(datos_link.get("imagen") or "").strip()
                if imagen.startswith(("https://", "http://")):
                    st.session_state["us_imagen_producto"] = imagen
                    imagen_bytes = descargar_imagen_producto(imagen)
                    if imagen_bytes:
                        st.session_state["us_imagen_producto_bytes"] = imagen_bytes
                campos = sum(bool(datos_link.get(k)) for k in ("titulo", "descripcion", "precio_usd", "peso_lb", "ancho_in", "alto_in", "largo_in"))
                if campos or st.session_state.get("us_imagen_producto_bytes"):
                    st.success("Datos públicos detectados. Revise precio, producto y medidas antes de agregarlo.")
                else:
                    st.warning("La tienda no publicó datos accesibles. Complete los campos manualmente o use una captura del producto.")
        foto_manual = st.file_uploader("Si la tienda bloquea la imagen, sube una foto del producto", type=["jpg", "jpeg", "png", "webp"], key="us_foto_manual")
        if foto_manual:
            st.session_state["us_imagen_producto_bytes"] = foto_manual.getvalue()
    imagen_producto = st.session_state.get("us_imagen_producto_bytes") or st.session_state.get("us_imagen_producto")

    st.markdown("<div class='usq-section-title'><span>3</span> Detalles del paquete</div>", unsafe_allow_html=True)
    imagen_producto = st.session_state.get("us_imagen_producto_bytes") or st.session_state.get("us_imagen_producto")
    if imagen_producto:
        with st.container(border=True):
            c_img, c_res = st.columns([1.15, 1])
            with c_img:
                st.image(imagen_producto, caption="Vista previa del producto", use_container_width=True)
            with c_res:
                st.markdown("**Producto detectado**")
                st.caption("Verifica los valores antes de agregarlo a la cotización.")
                st.success("Información lista para revisar")
    else:
        st.markdown(
            "<div class='usq-preview-empty'>🖼️ <span><b>Vista previa del producto</b><br>Consulta un enlace o sube una foto para verla aquí.</span></div>",
            unsafe_allow_html=True,
        )
    st.text_input("📦 Nombre del producto *", key="us_descripcion", placeholder="Ej. Laptop HP 16 pulgadas")
    st.text_area("📝 Descripción recuperada / notas", key="us_descripcion_origen", height=92,
                 placeholder="La descripción pública de la tienda aparecerá aquí; puedes corregirla o añadir detalles.")
    precio_col, cantidad_col, moneda_col = st.columns([1, 1, 0.7])
    with precio_col:
        precio_unitario = st.number_input("💳 Precio unitario", min_value=0.0, max_value=1_000_000.0,
                                          value=0.0, step=0.01, format="%.2f", key="us_precio_unitario")
    with cantidad_col:
        cantidad = st.number_input("Cantidad", min_value=1, max_value=10000, value=1, step=1, key="us_cantidad")
    with moneda_col:
        moneda_producto = st.text_input("Moneda", value=st.session_state.get("us_moneda_producto", "USD"),
                                         max_chars=3, key="us_moneda_producto").upper()
    p1, p2 = st.columns(2)
    with p1:
        peso_lb = st.number_input("⚖️ Peso (lb) *", min_value=0.0, max_value=2000.0, value=0.0, step=0.1, key="us_peso")
        alto = st.number_input("📏 Alto *", min_value=0.0, max_value=500.0, value=0.0, step=0.1, key="us_alto")
    with p2:
        ancho = st.number_input("📐 Ancho *", min_value=0.0, max_value=500.0, value=0.0, step=0.1, key="us_ancho")
        largo = st.number_input("📏 Largo *", min_value=0.0, max_value=500.0, value=0.0, step=0.1, key="us_largo")
    with st.container(border=True):
        st.markdown("**Tipo de unidad de medida**")
        unidad = st.radio("", ["Pulgadas", "Pies"], key="us_unidad", label_visibility="collapsed")

    if st.button("➕ Agregar paquete a la cotización", type="primary", use_container_width=True, key="btn_us_agregar"):
        descripcion = (st.session_state.get("us_descripcion") or "").strip()
        if not descripcion or min(float(peso_lb), float(ancho), float(alto), float(largo)) <= 0:
            st.error("Complete la descripción, peso y las tres medidas con valores mayores que cero.")
        else:
            calculo = calcular_paquete_eeuu(peso_lb, ancho, alto, largo, unidad, tarifa_lb, tarifa_m3)
            if not calculo["valido"]:
                st.error(calculo["error"])
            else:
                paquetes.append({
                    "descripcion": descripcion,
                    "peso_real": float(peso_lb),
                    "ancho": float(ancho), "alto": float(alto), "largo": float(largo),
                    "unidad": unidad,
                    "peso_cobrable": calculo["peso_cobrable_lb"],
                    "volumen_m3": calculo["volumen_m3"],
                    "cbm_facturable": calculo["cbm_facturable"],
                    "modalidad": calculo["modalidad"],
                    "total_usd": calculo["total_usd"],
                    "precio_unitario": float(precio_unitario), "cantidad": int(cantidad),
                    "moneda_producto": moneda_producto or "USD",
                    "descripcion_origen": (st.session_state.get("us_descripcion_origen") or "").strip(),
                    "enlace": (st.session_state.get("us_enlace") or "").strip(),
                })
                st.session_state["_us_limpiar_formulario"] = True
                st.rerun()

    st.markdown("<div class='usq-section-title'><span>4</span> Paquetes de la cotización</div>", unsafe_allow_html=True)
    if not paquetes:
        st.markdown("<div class='usq-empty-list'>📦 <b>Aún no hay paquetes</b><br><small>Agrega el primer producto para ver el total estimado.</small></div>", unsafe_allow_html=True)
        return

    total_lb = 0.0
    total_cbm = 0.0
    total_flete_usd = 0.0
    total_producto_usd = 0.0
    for indice, paquete in enumerate(list(paquetes)):
        recalculo = calcular_paquete_eeuu(
            paquete.get("peso_real", 0), paquete.get("ancho", 0), paquete.get("alto", 0),
            paquete.get("largo", 0), paquete.get("unidad", "Pulgadas"), tarifa_lb, tarifa_m3,
        )
        if recalculo.get("valido"):
            paquete.update({
                "peso_cobrable": recalculo["peso_cobrable_lb"], "volumen_m3": recalculo["volumen_m3"],
                "cbm_facturable": recalculo["cbm_facturable"], "modalidad": recalculo["modalidad"],
                "total_usd": recalculo["total_usd"],
            })
        total_lb += float(paquete.get("peso_cobrable") or 0)
        total_cbm += float(paquete.get("cbm_facturable") or 0)
        total_flete_usd += float(paquete.get("total_usd") or 0)
        # El precio se suma al total solo si está expresado en USD. Otras
        # monedas se conservan como referencia para no inventar conversiones.
        if str(paquete.get("moneda_producto") or "USD").upper() == "USD":
            total_producto_usd += float(paquete.get("precio_unitario") or 0) * int(paquete.get("cantidad") or 1)
        with st.container(border=True):
            info, eliminar = st.columns([5, 0.55])
            with info:
                st.markdown(
                    f"**{html.escape(paquete['descripcion'])}**  \n"
                    f"<span class='usq-mode'>{html.escape(str(paquete.get('modalidad', 'Paquetería menor')))}</span> "
                    f"<span class='usq-freight'>Flete ${float(paquete.get('total_usd') or 0):,.2f} USD</span>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"{int(paquete.get('cantidad') or 1)} unidad(es) · {str(paquete.get('moneda_producto') or 'USD').upper()} ${float(paquete.get('precio_unitario') or 0):,.2f} c/u · "
                    f"{paquete['ancho']:g} × {paquete['alto']:g} × {paquete['largo']:g} {paquete['unidad'].lower()} · Peso cobrable: {paquete['peso_cobrable']:.2f} lb"
                )
            with eliminar:
                if st.button("🗑️", key=f"us_del_{indice}", help="Eliminar paquete"):
                    paquetes.pop(indice)
                    st.rerun()

    st.markdown("---")
    total_usd = total_flete_usd + total_producto_usd
    a, b, c, d = st.columns(4)
    a.metric("Paquetes", len(paquetes))
    b.metric("Peso/CBM cobrable", f"{total_lb:.2f} lb" if total_cbm == 0 else f"{total_cbm:.3f} CBM")
    c.metric("Valor productos (USD)", f"${total_producto_usd:,.2f}")
    d.metric("Total producto + flete", f"${total_usd:,.2f} USD")
    st.caption(f"Flete estimado: ${total_flete_usd:,.2f} USD. El total no incluye impuestos, aranceles ni cargos de tienda. Se ajustará si cambian el peso o las medidas reales recibidas en bodega.")


# ---------------------------------------------------------
# 5. GESTIÓN DE SESIÓN PERSISTENTE MEDIANTE QUERY_PARAMS
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.update(
        {
            "autenticado": False,
            "usuario": None,
            "rol": None,
            "casillero": None,
            "nombre": None,
            "dni": None,
            "telefono": None,
            "departamento": None,
            "ciudad": None,
            "direccion_exacta": None,
            "reg_paso": 1,
            "reg_datos": {},
            "reg_exito": None,
        }
    )


def restaurar_sesion_persistente():
    """No autentica nunca desde parámetros de URL.

    Un enlace puede indicar una vista, pero no demuestra la identidad de quien
    lo abre. La sesión solo se establece después de verificar contraseña.
    """
    return bool(st.session_state.get("autenticado", False))

    try:
        params = st.query_params
        cas_param = params.get("casillero", "")
        if isinstance(cas_param, list):
            cas_param = cas_param[0] if cas_param else ""
        cas_param = str(cas_param).strip()

        if not cas_param:
            return False

        claves = coincidencias_casillero(cas_param)
        placeholders = ",".join("?" * len(claves))
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                f"""
                SELECT id, codigo_casillero, nombre_completo, correo_principal,
                       rol, activo, telefono_principal, ciudad
                FROM usuarios
                WHERE codigo_casillero IN ({placeholders}) AND activo = 1
                """,
                claves,
            )
            user_rec = c.fetchone()

        if not user_rec:
            return False

        st.session_state["autenticado"] = True
        st.session_state["rol"] = normalizar_rol(user_rec[4])
        perfil_rest = cargar_perfil_usuario(user_rec[1])
        if perfil_rest:
            aplicar_perfil_en_sesion(perfil_rest)
        else:
            st.session_state["casillero"] = formatear_casillero(user_rec[1])
            st.session_state["nombre"] = user_rec[2]
            st.session_state["usuario"] = user_rec[3]
            st.session_state["telefono"] = user_rec[6]
            st.session_state["ciudad"] = user_rec[7]

        vista_url = params.get("vista", "")
        if isinstance(vista_url, list):
            vista_url = vista_url[0] if vista_url else ""
        hub_url = params.get("hub", "")
        if isinstance(hub_url, list):
            hub_url = hub_url[0] if hub_url else ""

        vistas_validas = {"Inicio", "China", "EE. UU.", "Honduras", "Consultas", "Configuración", "Más", "Actividad", "Fichas"} | VISTAS_MODULO
        if vista_url in ALIAS_VISTA:
            vista_url = ALIAS_VISTA[vista_url]
        if vista_url in vistas_validas:
            st.session_state["sub_tab_inicio"] = vista_url
            st.session_state["vista_activa"] = vista_url
        if hub_url in HUBS:
            st.session_state["hub"] = hub_url
        elif vista_url in MODULOS_POR_ID:
            st.session_state["hub"] = MODULOS_POR_ID[vista_url]
        elif vista_url == "Inicio":
            # Inicio sin parámetro hub significa que se deseleccionó el país de origen.
            st.session_state["hub"] = None
        elif vista_url == "China":
            st.session_state["hub"] = "china"
        elif vista_url == "EE. UU.":
            st.session_state["hub"] = "eeuu"
        elif vista_url == "Honduras":
            st.session_state["hub"] = "honduras"

        return True

    except Exception:
        return False


restaurar_sesion_persistente()

# Una cuenta creada en versiones anteriores puede tener el rol vacío, con
# mayúsculas o espacios. La autenticación sigue siendo válida; en ese caso
# debe abrir el portal de cliente y nunca dejar la aplicación en blanco.
if st.session_state.get("autenticado"):
    rol_sesion = normalizar_rol(st.session_state.get("rol"))
    st.session_state["rol"] = rol_sesion

# La URL también puede abrir el Cotizador directamente. Solo al entrar desde
# otra vista se reinicia el formulario; los cambios del usuario sobreviven a
# los reruns normales mientras permanece dentro del Cotizador.
vista_sesion_anterior = st.session_state.get("_vista_operativa_anterior")
vista_sesion_actual = st.session_state.get("sub_tab_inicio")
if vista_sesion_actual == "Cotizador" and vista_sesion_anterior != "Cotizador":
    preparar_nueva_cotizacion()
st.session_state["_vista_operativa_anterior"] = vista_sesion_actual

if st.session_state.get("autenticado", False):
    cas_actual = formatear_casillero(st.session_state.get("casillero", ""))
    if cas_actual:
        st.session_state["casillero"] = cas_actual
        try:
            if st.query_params.get("casillero") != cas_actual:
                st.query_params["casillero"] = cas_actual
        except Exception:
            pass


def logout():
    for k in [
        "autenticado",
        "usuario",
        "rol",
        "casillero",
        "nombre",
        "dni",
        "telefono",
        "departamento",
        "ciudad",
        "direccion_exacta",
        "flash_perfil",
        "datos_pdf_confirmado",
        "ultima_cot_id",
        "cotizaciones",
        "_cot_emit_snapshot",
        "_seq_cot",
        "_ccm_rerun_app",
        "_ccm_scroll_emit",
        "_ccm_emit_error",
        "_mod_entrega_lista",
        "_mod_entrega_pendiente",
        "modalidad_envio_seleccionada",
        "sb_modalidad_entrega",
        "direcciones_usuario",
        "destino_entrega_activo",
        "_dir_db_error",
        "_dir_form_error",
        "_dir_form_exito",
        "_dir_form_reset",
        "dir_etiqueta_in",
        "dir_receptor_in",
        "dir_tel_in",
        "dir_exacta_in",
        "sub_tab_inicio",
        "vista_activa",
        "_vista_operativa_anterior",
        "hub",
        "china_modulos_desbloqueados",
        "cotizacion_envio_foco",
        "cotizacion_historial_foco",
        "abrir_guia_rapida",
        "guia_china_auto_vista",
        "guia_activa",
        "guia_paso",
        "guia_omitida",
        "guia_completada",
        "mostrar_guia",
    ]:
        st.session_state.pop(k, None)
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------
# 6. ESTILOS CSS REFINADOS: ADAPTABLES A MÓVILES (IPHONE Y ANDROID)
# ---------------------------------------------------------
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@700&display=swap');

    :root {
        --app-max-width: 520px;
        --app-pad: 0.7rem;
        --nav-btn-w: 108px;
        --nav-btn-h: 44px;
        --header-blue-pad-y: 8px;
        --header-blue-pad-x: 12px;
        --brand-size: clamp(1.05rem, 0.55rem + 2.6vw, 1.2rem);
        --greeting-title: clamp(0.95rem, 0.82rem + 0.7vw, 1.15rem);
        --greeting-sub: clamp(0.75rem, 0.66rem + 0.5vw, 0.9rem);
        --greeting-time: clamp(0.75rem, 0.66rem + 0.5vw, 0.9rem);
        --sticky-h: 208px;
        --sticky-delivery: 0px;
        --header-offset: var(--sticky-h);
        --header-box: 196px;
        --header-gap: 20px;
        --ccm-nav-clearance: calc(109px + env(safe-area-inset-bottom, 0px));
    }

    html, body {
        overflow: hidden !important;
        height: 100% !important;
        max-height: 100% !important;
        background-color: #f8fafc !important;
        background: #f8fafc !important;
        color: #0f172a !important;
        color-scheme: light !important;
    }
    [data-st-overlay-root="true"] {
        color-scheme: light !important;
        color: #0f172a !important;
    }

    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stAppViewBlockContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stBottomBlockContainer"],
    [data-testid="stBottom"],
    section.main,
    .stMain,
    .stMainBlockContainer,
    .main,
    .block-container {
        background-color: #f8fafc !important;
        background: #f8fafc !important;
        color: #0f172a !important;
    }

    .stApp {
        font-family: 'Plus Jakarta Sans', sans-serif !important;
        overflow-x: hidden !important;
        overflow-y: auto !important;
        height: 100% !important;
        max-height: 100% !important;
        min-height: 100% !important;
        color-scheme: light !important;
        max-width: 100% !important;
    }

    #MainMenu, footer,
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    .stStatusWidget,
    .stDeployButton,
    [data-testid="stAppDeployButton"],
    [class*="stAppDeployButton"],
    [class*="viewerBadge"],
    [class*="ViewerBadge"],
    iframe[title*="streamlit status" i],
    [data-testid="stBaseButton-header"],
    [data-testid="stBaseButton-headerNoPadding"],
    [data-testid="stAppHeader"],
    .stAppHeader,
    div[class*="stDeployButton"],
    [data-testid="stToolbarActions"],
    [data-testid="stHostToolbar"],
    [data-testid="stHostHeader"],
    [data-testid="stHeader"] button[kind="header"],
    [data-testid="stHeader"] [data-testid="stBaseButton-header"],
    [data-testid="stHeader"] [data-testid="stBaseButton-headerNoPadding"],
    [data-testid="stAppToolbar"],
    .stAppToolbar,
    [data-testid="stMainMenu"],
    #recordMenuPopoverButton,
    iframe[title*="streamlit cloud" i],
    a[href*="streamlit.io"],
    a[href*="share.streamlit.io"] {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
        height: 0 !important;
        min-height: 0 !important;
        width: 0 !important;
        opacity: 0 !important;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    section.main,
    .stMain,
    .stMainBlockContainer {
        overflow: visible !important;
        height: auto !important;
        min-height: 0 !important;
    }

    .block-container,
    [data-testid="stMainBlockContainer"],
    .stMainBlockContainer,
    [data-testid="stAppViewBlockContainer"] {
        max-width: var(--app-max-width) !important;
        width: 100% !important;
        padding-top: 0.15rem !important;
        padding-bottom: calc(200px + env(safe-area-inset-bottom, 0px)) !important;
        padding-left: var(--app-pad) !important;
        padding-right: var(--app-pad) !important;
        margin: 0 auto !important;
        overflow: visible !important;
        transform: none !important;
    }

    .st-key-sticky_top_header,
    div[class~="st-key-sticky_top_header"] {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        transform: none !important;
        width: min(100%, var(--app-max-width)) !important;
        max-width: var(--app-max-width) !important;
        margin-left: auto !important;
        margin-right: auto !important;
        z-index: 10 !important;
        background-color: #f8fafc !important;
        background: #f8fafc !important;
        background-image: none !important;
        opacity: 1 !important;
        padding-top: max(0.35rem, env(safe-area-inset-top, 0px)) !important;
        padding-bottom: 0.45rem !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        padding-left: var(--app-pad) !important;
        padding-right: var(--app-pad) !important;
        box-sizing: border-box !important;
        border-bottom: 1px solid #e2e8f0 !important;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.12) !important;
        overflow-x: visible !important;
        overflow-y: visible !important;
        isolation: isolate !important;
    }

    .ccm-header-spacer {
        display: block !important;
        height: max(var(--header-offset), 208px) !important;
        min-height: max(var(--header-offset), 208px) !important;
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        pointer-events: none !important;
        visibility: hidden !important;
    }
    [data-testid="stElementContainer"]:has(.ccm-header-spacer),
    [data-testid="stMarkdown"]:has(.ccm-header-spacer),
    [data-testid="stMarkdownContainer"]:has(.ccm-header-spacer) {
        height: max(var(--header-offset), 208px) !important;
        min-height: max(var(--header-offset), 208px) !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }

    .st-key-header_offset_sync {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }

    [id="cotizacion-foco-pendiente"],
    [id="cotizacion-envio-foco"],
    [id^="cotizacion-ccm-"],
    [id^="cotizacion-env-"],
    .cotizacion-pendiente-foco,
    [data-testid="stHeading"],
    [data-testid="stCaptionContainer"],
    .stApp:has(.st-key-sticky_top_header) .block-container > div > div {
        scroll-margin-top: var(--header-offset) !important;
    }

    .app-header-blue {
        background: linear-gradient(135deg, #004ac1 0%, #00368c 100%) !important;
        padding: var(--header-blue-pad-y) var(--header-blue-pad-x) !important;
        border-radius: 12px !important;
        color: #ffffff !important;
        box-shadow: 0 4px 14px rgba(0, 74, 193, 0.22) !important;
        max-width: 100% !important;
        width: 100% !important;
        box-sizing: border-box !important;
        margin-bottom: 4px !important;
        position: relative !important;
        z-index: 10 !important;
        display: flex !important;
        flex-direction: column !important;
        gap: 1px !important;
        container-type: inline-size;
        overflow: hidden !important;
    }

    .app-header-top {
        position: relative !important;
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: center !important;
        justify-content: center !important;
        width: 100% !important;
        min-width: 0 !important;
        margin: 0 0 6px 0 !important;
        padding: 2px 0 8px 0 !important;
        border-bottom: 1px solid rgba(219, 234, 254, 0.35) !important;
        box-sizing: border-box !important;
    }

    .app-header-copy {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        gap: 1px !important;
        width: 100% !important;
        min-width: 0 !important;
        text-align: left !important;
        text-align-last: left !important;
    }

    .app-header-brand {
        display: block !important;
        width: 100% !important;
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 2px !important;
        box-sizing: border-box !important;
        border-bottom: none !important;
        color: #ffffff !important;
        font-weight: 800 !important;
        text-transform: uppercase !important;
        white-space: nowrap !important;
        text-wrap: nowrap !important;
        text-align: center !important;
        text-align-last: center !important;
        letter-spacing: 0.04em !important;
        word-spacing: 0.02em !important;
        line-height: 1.2 !important;
        font-size: var(--brand-size) !important;
        overflow: hidden !important;
        text-overflow: clip !important;
    }

    .st-key-sticky_top_header [data-testid="stMarkdown"],
    .st-key-sticky_top_header [data-testid="stMarkdown"] p,
    .st-key-sticky_top_header [data-testid="stMarkdownContainer"] {
        text-align: left !important;
        text-align-last: left !important;
        word-spacing: normal !important;
    }

    .app-greeting-title {
        font-size: var(--greeting-title) !important;
        font-weight: 800 !important;
        margin: 0 !important;
        color: #ffffff !important;
        line-height: 1.25 !important;
        letter-spacing: -0.2px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        max-width: 100% !important;
    }

    .app-header-login {
        align-items: stretch !important;
        text-align: center !important;
    }
    .app-header-login .app-greeting-sub {
        text-align: center !important;
        text-align-last: center !important;
        width: 100% !important;
        margin-top: 4px !important;
    }
    .st-key-login_header [data-testid="stMarkdown"],
    .st-key-login_header [data-testid="stMarkdown"] p,
    .st-key-login_header [data-testid="stMarkdownContainer"],
    .st-key-login_header [data-testid="stMarkdownContainer"] h2 {
        text-align: center !important;
        text-align-last: center !important;
    }

    .app-greeting-sub {
        font-size: var(--greeting-sub) !important;
        color: #dbeafe !important;
        margin-top: 0 !important;
        font-weight: 500 !important;
        line-height: 1.35 !important;
        white-space: normal !important;
        overflow-wrap: break-word !important;
        word-break: break-word !important;
        max-width: 100% !important;
    }

    .app-header-casillero,
    .app-header-cots {
        display: inline !important;
    }
    .app-header-sep {
        display: inline !important;
    }

    .app-header-time {
        font-size: var(--greeting-time) !important;
        color: #bfdbfe !important;
        margin-top: 2px !important;
        font-weight: 600 !important;
        line-height: 1.35 !important;
        white-space: normal !important;
        max-width: 100% !important;
    }

    /* iPhone / Android compacto */
    @media (max-width: 480px) {
        :root {
            --app-max-width: 100vw;
            --app-pad: 0.55rem;
            --nav-btn-w: 104px;
            --nav-btn-h: 44px;
            --header-blue-pad-y: 8px;
            --header-blue-pad-x: 12px;
            --sticky-delivery: 0px;
        }
        .app-header-blue {
            border-radius: 11px !important;
            margin-bottom: 3px !important;
        }
        .app-header-brand {
            white-space: nowrap !important;
            text-wrap: nowrap !important;
            font-size: clamp(1.05rem, 0.55rem + 2.6vw, 1.2rem) !important;
            letter-spacing: 0.02em !important;
        }
        .app-greeting-sub {
            display: flex !important;
            flex-direction: column !important;
            align-items: flex-start !important;
            gap: 2px !important;
        }
        .app-header-sep {
            display: none !important;
        }
        .app-header-casillero,
        .app-header-cots {
            display: block !important;
        }
        .inicio-placeholder { min-height: 260px; }
        .inicio-placeholder-body { min-height: 180px; }
        .card-box { padding: 0.9rem; border-radius: 12px; }
        .app-banner-card { padding: 12px; border-radius: 16px; margin-bottom: 0.85rem; }
        .swipe-indicator-bar { font-size: 0.68rem; margin: 1px 0 4px 0; }
    }

    /* Teléfonos grandes */
    @media (min-width: 481px) and (max-width: 767px) {
        :root {
            --app-max-width: 560px;
            --app-pad: 0.75rem;
            --nav-btn-w: 112px;
            --nav-btn-h: 44px;
            --header-blue-pad-y: 11px;
            --header-blue-pad-x: 14px;
        }
        .app-header-blue {
            border-radius: 14px !important;
        }
        .app-header-brand {
            white-space: nowrap !important;
            text-wrap: nowrap !important;
            font-size: clamp(1.12rem, 0.7rem + 1.8vw, 1.25rem) !important;
            letter-spacing: 0.035em !important;
        }
    }

    /* Tablet */
    @media (min-width: 768px) and (max-width: 1023px) {
        :root {
            --app-max-width: 820px;
            --app-pad: 1.1rem;
            --nav-btn-w: 122px;
            --nav-btn-h: 44px;
            --header-blue-pad-y: 14px;
            --header-blue-pad-x: 18px;
        }
        .app-header-blue { border-radius: 16px !important; margin-bottom: 8px !important; }
        .app-header-brand {
            font-size: clamp(1.25rem, 0.85rem + 1.1vw, 1.4rem) !important;
            letter-spacing: 0.045em !important;
        }
        .app-greeting-sub {
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            overflow-wrap: normal !important;
            word-break: normal !important;
        }
        .card-box { padding: 1.35rem; }
        .inicio-placeholder { min-height: 380px; }
    }

    /* Computadora */
    @media (min-width: 1024px) {
        :root {
            --app-max-width: 1080px;
            --app-pad: 1.4rem;
            --nav-btn-w: 128px;
            --nav-btn-h: 46px;
            --header-blue-pad-y: 16px;
            --header-blue-pad-x: 22px;
        }
        .app-header-blue { border-radius: 18px !important; margin-bottom: 10px !important; }
        .app-header-brand {
            font-size: clamp(1.45rem, 1.05rem + 0.7vw, 1.65rem) !important;
            letter-spacing: 0.05em !important;
        }
        .app-greeting-sub {
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            overflow-wrap: normal !important;
            word-break: normal !important;
        }
        .card-box { padding: 1.5rem; border-radius: 16px; }
        .inicio-placeholder { min-height: 420px; }
        .swipe-indicator-bar { display: none; }
    }

    .st-key-bottom_nav_pin {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }
    [data-testid="stBottom"],
    [data-testid="stBottomBlockContainer"] {
        min-height: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    .st-key-bottom_nav,
    div[class~="st-key-bottom_nav"] {
        position: fixed !important;
        bottom: 20px !important;
        left: 50% !important;
        transform: translateX(-50%) !important;
        z-index: 9999 !important;
        width: min(96vw, 520px) !important;
        max-width: 520px !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
        box-sizing: border-box !important;
        overflow: visible !important;
        background: rgba(255, 255, 255, 0.95) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border-radius: 35px !important;
        box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12) !important;
        padding: 6px 8px !important;
        border: 1px solid rgba(226, 232, 240, 0.95) !important;
    }
    [data-testid="stElementContainer"]:has(.st-key-bottom_nav),
    [data-testid="stElementContainer"]:has([class~="st-key-bottom_nav"]) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
        border: 0 !important;
    }

    .ccm-bottom-safe,
    .st-key-safe_cotizador,
    .st-key-safe_cotizador_fin,
    .st-key-safe_catalogo {
        display: block !important;
        height: 0 !important;
        min-height: 0 !important;
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        pointer-events: none !important;
        opacity: 0 !important;
        overflow: hidden !important;
    }
    [data-testid="stElementContainer"]:has(> .st-key-safe_catalogo),
    [data-testid="stElementContainer"]:has(> .st-key-safe_cotizador),
    [data-testid="stElementContainer"]:has(> .st-key-safe_cotizador_fin),
    [data-testid="stElementContainer"]:has(> [class~="st-key-safe_catalogo"]),
    [data-testid="stElementContainer"]:has(> [class~="st-key-safe_cotizador"]),
    [data-testid="stElementContainer"]:has(> [class~="st-key-safe_cotizador_fin"]),
    [data-testid="stLayoutWrapper"]:has(> .st-key-safe_catalogo),
    [data-testid="stLayoutWrapper"]:has(> .st-key-safe_cotizador),
    [data-testid="stLayoutWrapper"]:has(> .st-key-safe_cotizador_fin) {
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }
    .st-key-safe_mas {
        display: block !important;
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        width: 100% !important;
        pointer-events: none !important;
        opacity: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
    .st-key-safe_historial {
        display: block !important;
        height: 16px !important;
        min-height: 16px !important;
        max-height: 16px !important;
        width: 100% !important;
        pointer-events: none !important;
        opacity: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    .st-key-safe_envios {
        display: block !important;
        height: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
        min-height: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
        width: 100% !important;
        pointer-events: none !important;
        opacity: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    .st-key-safe_fichas {
        display: block !important;
        height: 200px !important;
        min-height: 200px !important;
        width: 100% !important;
        pointer-events: none !important;
        opacity: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    .st-key-catalogo_formulario {
        padding-bottom: 0 !important;
        margin-bottom: 0 !important;
    }
    .block-container:has(.st-key-vista_catalogo),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_catalogo),
    .stMainBlockContainer:has(.st-key-vista_catalogo),
    .block-container:has(.st-key-vista_cotizador),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_cotizador),
    .stMainBlockContainer:has(.st-key-vista_cotizador),
    .block-container:has(.st-key-vista_inicio),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_inicio),
    .stMainBlockContainer:has(.st-key-vista_inicio) {
        padding-bottom: 0 !important;
    }
    .st-key-vista_inicio {
        display: block !important;
        padding-bottom: 180px !important;
        min-height: 0 !important;
        overflow: visible !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }
    .st-key-vista_inicio > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-vista_inicio > [data-testid="stVerticalBlock"] {
        gap: 8px !important;
        margin-top: 0 !important;
    }
    .block-container:has(.st-key-vista_mas),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_mas),
    .stMainBlockContainer:has(.st-key-vista_mas) {
        padding-bottom: 0 !important;
        padding-top: 0 !important;
    }
    .block-container:has(.ccm-vista-historial),
    [data-testid="stMainBlockContainer"]:has(.ccm-vista-historial),
    .stMainBlockContainer:has(.ccm-vista-historial),
    .stApp:has(.ccm-vista-historial) .block-container,
    .block-container:has(.st-key-vista_historial),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_historial),
    .stMainBlockContainer:has(.st-key-vista_historial) {
        padding-bottom: calc(var(--ccm-nav-clearance) + 16px) !important;
    }
    .block-container:has(.st-key-vista_envios),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_envios),
    .stMainBlockContainer:has(.st-key-vista_envios) {
        padding-bottom: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
    }
    .block-container:has(.st-key-vista_fichas),
    [data-testid="stMainBlockContainer"]:has(.st-key-vista_fichas),
    .stMainBlockContainer:has(.st-key-vista_fichas) {
        padding-bottom: calc(200px + env(safe-area-inset-bottom, 0px)) !important;
    }
    .st-key-vista_envios {
        display: block !important;
        padding-bottom: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
        min-height: 0 !important;
        overflow: visible !important;
    }
    .st-key-vista_fichas {
        display: block !important;
        padding-bottom: 200px !important;
        min-height: 0 !important;
        overflow: visible !important;
    }
    .st-key-vista_historial {
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        align-items: stretch !important;
        padding-bottom: calc(var(--ccm-nav-clearance) + 16px) !important;
        min-height: 0 !important;
        height: auto !important;
        max-height: none !important;
        flex: 0 0 auto !important;
        overflow: visible !important;
    }
    .st-key-vista_historial > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-vista_historial > [data-testid="stVerticalBlock"],
    .st-key-vista_historial > [data-testid="stLayoutWrapper"] {
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        flex: 0 0 auto !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        overflow: visible !important;
        width: 100% !important;
    }
    .st-key-vista_historial [data-testid="stElementContainer"]:has(> .st-key-safe_historial),
    .st-key-vista_historial [data-testid="stLayoutWrapper"]:has(> .st-key-safe_historial) {
        height: 16px !important;
        min-height: 16px !important;
        max-height: 16px !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        flex: 0 0 auto !important;
    }
    [data-testid="stElementContainer"]:has(> .st-key-safe_envios),
    [data-testid="stLayoutWrapper"]:has(> .st-key-safe_envios) {
        height: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
        min-height: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    [data-testid="stElementContainer"]:has(> .st-key-safe_fichas),
    [data-testid="stLayoutWrapper"]:has(> .st-key-safe_fichas) {
        height: 200px !important;
        min-height: 200px !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    [data-testid="stAppViewContainer"]:has(.st-key-vista_catalogo) [data-testid="stBottomBlockContainer"],
    [data-testid="stAppViewContainer"]:has(.st-key-vista_cotizador) [data-testid="stBottomBlockContainer"],
    [data-testid="stAppViewContainer"]:has(.st-key-vista_mas) [data-testid="stBottomBlockContainer"],
    .stApp:has(.st-key-vista_catalogo) [data-testid="stBottomBlockContainer"],
    .stApp:has(.st-key-vista_cotizador) [data-testid="stBottomBlockContainer"],
    .stApp:has(.st-key-vista_mas) [data-testid="stBottomBlockContainer"] {
        min-height: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
    }
    .stApp:has(.st-key-sticky_top_header) [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"],
    .stApp:has(.st-key-sticky_top_header) .stMainBlockContainer > [data-testid="stVerticalBlock"] {
        gap: 0 !important;
        row-gap: 0 !important;
    }
    .stApp:has(.st-key-vista_mas) .ccm-header-spacer {
        height: calc(var(--header-box, 196px) + 12px) !important;
        min-height: calc(var(--header-box, 196px) + 12px) !important;
    }
    .stApp:has(.st-key-vista_catalogo) .ccm-header-spacer,
    .stApp:has(.st-key-vista_cotizador) .ccm-header-spacer {
        height: calc(var(--header-box, 196px) + 16px) !important;
        min-height: calc(var(--header-box, 196px) + 16px) !important;
    }
    .stApp:has(.st-key-vista_catalogo) [data-testid="stElementContainer"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_catalogo) [data-testid="stMarkdown"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_catalogo) [data-testid="stMarkdownContainer"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_cotizador) [data-testid="stElementContainer"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_cotizador) [data-testid="stMarkdown"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_cotizador) [data-testid="stMarkdownContainer"]:has(.ccm-header-spacer) {
        height: calc(var(--header-box, 196px) + 16px) !important;
        min-height: calc(var(--header-box, 196px) + 16px) !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }
    .stApp:has(.st-key-vista_mas) [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"],
    .stApp:has(.st-key-vista_mas) .stMainBlockContainer > [data-testid="stVerticalBlock"] {
        gap: 0 !important;
        row-gap: 0 !important;
    }
    .stApp:has(.st-key-vista_mas) [data-testid="stElementContainer"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_mas) [data-testid="stMarkdown"]:has(.ccm-header-spacer),
    .stApp:has(.st-key-vista_mas) [data-testid="stMarkdownContainer"]:has(.ccm-header-spacer) {
        height: calc(var(--header-box, 196px) + 12px) !important;
        min-height: calc(var(--header-box, 196px) + 12px) !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }
    .st-key-vista_mas {
        display: block !important;
        box-sizing: border-box !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        min-height: 0 !important;
    }
    .st-key-vista_mas:has(.st-key-mas_modulos) {
        padding-bottom: calc(140px + env(safe-area-inset-bottom, 0px)) !important;
    }
    .st-key-vista_mas > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-vista_mas > [data-testid="stVerticalBlock"],
    .st-key-vista_mas > [data-testid="stLayoutWrapper"] {
        display: block !important;
        width: 100% !important;
    }
    .st-key-vista_mas > [data-testid="stLayoutWrapper"]:has(.st-key-mas_sesion),
    .st-key-vista_mas > [data-testid="stElementContainer"]:has(.st-key-mas_sesion) {
        margin-top: 4px !important;
        width: 100% !important;
    }
    .st-key-vista_mas [data-testid="stVerticalBlock"] {
        gap: 0.55rem !important;
    }
    .st-key-vista_mas [data-testid="stElementContainer"]:has(.mas-seccion),
    .st-key-vista_mas [data-testid="stMarkdown"]:has(.mas-seccion),
    .st-key-vista_mas [data-testid="stMarkdownContainer"]:has(.mas-seccion) {
        overflow: visible !important;
        height: auto !important;
        min-height: 1.6rem !important;
        max-height: none !important;
    }
    .st-key-vista_mas [data-testid="stElementContainer"]:has(.mas-seccion-modulos),
    .st-key-vista_mas [data-testid="stMarkdown"]:has(.mas-seccion-modulos) {
        margin-top: 18px !important;
        margin-bottom: 10px !important;
        padding-top: 6px !important;
        padding-bottom: 4px !important;
    }
    .st-key-vista_mas [data-testid="stElementContainer"]:has(.mas-seccion:not(.mas-seccion-cuenta):not(.mas-seccion-modulos)) {
        margin-top: 16px !important;
        margin-bottom: 8px !important;
        padding-top: 4px !important;
    }
    .st-key-vista_mas > [data-testid="stElementContainer"]:first-child,
    .st-key-vista_mas [data-testid="stElementContainer"]:has(.mas-seccion-cuenta) {
        margin-top: 0 !important;
        padding-top: 0 !important;
    }
    .stApp:has(.st-key-vista_mas) .st-key-guia_coach,
    .stApp:has(.st-key-vista_mas) .guia-globo {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        overflow: hidden !important;
        visibility: hidden !important;
    }
    .st-key-mas_modulos {
        display: flex !important;
        flex-direction: column !important;
        width: 100% !important;
        gap: 0 !important;
    }
    .st-key-mas_sesion {
        margin-bottom: 0 !important;
        padding-bottom: 0 !important;
        width: 100% !important;
    }
    .st-key-vista_mas div.stButton {
        margin-top: 0 !important;
        margin-bottom: 6px !important;
    }
    .st-key-btn_logout_cliente div.stButton {
        margin-bottom: 0 !important;
    }
    .st-key-vista_cotizador {
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        box-sizing: border-box !important;
        padding-top: 16px !important;
        padding-bottom: var(--ccm-nav-clearance) !important;
        margin-bottom: 0 !important;
        min-height: calc(100dvh - var(--header-offset, 208px)) !important;
    }
    .st-key-vista_cotizador:has(.st-key-formulario_direcciones) {
        padding-top: 16px !important;
        padding-bottom: 0 !important;
        min-height: 0 !important;
        height: auto !important;
        overflow: visible !important;
    }
    .st-key-formulario_direcciones {
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        box-sizing: border-box !important;
        width: 100% !important;
        height: auto !important;
        min-height: 0 !important;
        margin-top: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 220px !important;
        overflow: visible !important;
    }
    .st-key-formulario_direcciones > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-formulario_direcciones > [data-testid="stVerticalBlock"],
    .st-key-formulario_direcciones > [data-testid="stLayoutWrapper"] {
        display: flex !important;
        flex-direction: column !important;
        height: auto !important;
        min-height: 0 !important;
        width: 100% !important;
        overflow: visible !important;
    }
    .st-key-formulario_direcciones .st-key-btn_guardar_nueva_dir,
    .st-key-formulario_direcciones .st-key-btn_cancelar_dir {
        width: 100% !important;
        margin-top: 4px !important;
        margin-bottom: 8px !important;
    }
    .st-key-vista_catalogo {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        box-sizing: border-box !important;
        padding-top: 0 !important;
        padding-bottom: calc(var(--ccm-nav-clearance) + 16px) !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        min-height: calc(100dvh - var(--header-offset, 208px)) !important;
    }
    .st-key-vista_catalogo:has([data-testid="stImage"]) {
        justify-content: flex-start !important;
        padding-bottom: 180px !important;
        min-height: 0 !important;
    }
    .st-key-vista_cotizador:has(.st-key-guia_foco_pdf_fab),
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) {
        justify-content: flex-start !important;
        padding-top: 16px !important;
        padding-bottom: calc(var(--ccm-nav-clearance) + 16px) !important;
        min-height: calc(100dvh - var(--header-offset, 208px)) !important;
    }
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) > [data-testid="stVerticalBlock"],
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) > [data-testid="stLayoutWrapper"] {
        display: flex !important;
        flex-direction: column !important;
        flex: 1 1 auto !important;
        width: 100% !important;
        min-height: 0 !important;
        height: auto !important;
    }
    .st-key-vista_catalogo > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-vista_catalogo > [data-testid="stVerticalBlock"],
    .st-key-vista_catalogo > [data-testid="stLayoutWrapper"] {
        display: flex !important;
        flex-direction: column !important;
        flex: 0 0 auto !important;
        width: 100% !important;
        min-height: 0 !important;
        height: auto !important;
    }
    .st-key-vista_cotizador > [data-testid="stVerticalBlockBorderWrapper"],
    .st-key-vista_cotizador > [data-testid="stVerticalBlock"],
    .st-key-vista_cotizador > [data-testid="stLayoutWrapper"] {
        display: flex !important;
        flex-direction: column !important;
        flex: 0 0 auto !important;
        width: 100% !important;
    }
    .st-key-vista_catalogo [data-testid="stVerticalBlock"]:has(.st-key-catalogo_formulario),
    .st-key-vista_catalogo [data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-catalogo_formulario),
    .st-key-vista_catalogo [data-testid="stLayoutWrapper"]:has(.st-key-catalogo_formulario) {
        display: flex !important;
        flex-direction: column !important;
        flex: 0 0 auto !important;
        width: 100% !important;
        min-height: 0 !important;
        height: auto !important;
    }
    .st-key-vista_catalogo .st-key-catalogo_formulario {
        display: flex !important;
        flex-direction: column !important;
        flex: 0 0 auto !important;
        width: 100% !important;
        min-height: 0 !important;
        height: auto !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }
    .st-key-vista_cotizador:not(:has(.st-key-guia_foco_pdf_fab)):not(:has(.st-key-acciones_emit_cotizador)) > [data-testid="stElementContainer"]:has(.st-key-guia_foco_tarifa),
    .st-key-vista_cotizador:not(:has(.st-key-guia_foco_pdf_fab)):not(:has(.st-key-acciones_emit_cotizador)) > [data-testid="stElementContainer"]:has(.st-key-btn_confirmar_tarifa),
    .st-key-vista_cotizador:not(:has(.st-key-guia_foco_pdf_fab)):not(:has(.st-key-acciones_emit_cotizador)) > [data-testid="stLayoutWrapper"]:has(.st-key-guia_foco_tarifa),
    .st-key-vista_cotizador:not(:has(.st-key-guia_foco_pdf_fab)):not(:has(.st-key-acciones_emit_cotizador)) > [data-testid="stLayoutWrapper"]:has(.st-key-btn_confirmar_tarifa) {
        margin-top: auto !important;
        width: 100% !important;
        flex: 0 0 auto !important;
    }
    .st-key-vista_cotizador [data-testid="stHeading"] {
        margin-top: 0 !important;
        padding-top: 6px !important;
        scroll-margin-top: max(var(--header-offset, 208px), 208px) !important;
    }
    .st-key-vista_catalogo [data-testid="stHeading"],
    .st-key-catalogo_formulario [data-testid="stHeading"],
    .st-key-catalogo_formulario h1,
    .st-key-catalogo_formulario h2,
    .st-key-catalogo_formulario h3,
    .st-key-catalogo_formulario h4 {
        margin-top: 0 !important;
        margin-bottom: 8px !important;
        padding-top: 0 !important;
        scroll-margin-top: max(var(--header-offset, 208px), 208px) !important;
    }
    .st-key-guia_foco_tarifa,
    .st-key-guia_foco_tarifa [data-testid="stVerticalBlock"],
    .st-key-guia_foco_tarifa [data-testid="stLayoutWrapper"],
    .st-key-guia_foco_tarifa [data-testid="stElementContainer"],
    .st-key-btn_confirmar_tarifa {
        width: 100% !important;
        max-width: 100% !important;
        display: block !important;
        scroll-margin-bottom: var(--ccm-nav-clearance) !important;
        margin-bottom: 0 !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
    }
    .st-key-guia_foco_tarifa div.stButton,
    .st-key-btn_confirmar_tarifa div.stButton {
        width: 100% !important;
    }
    .st-key-guia_foco_tarifa div.stButton > button,
    .st-key-btn_confirmar_tarifa div.stButton > button,
    .st-key-guia_foco_tarifa [data-testid^="stBaseButton"],
    .st-key-btn_confirmar_tarifa [data-testid^="stBaseButton"] {
        width: 100% !important;
        max-width: 100% !important;
        min-height: 48px !important;
        height: auto !important;
        max-height: none !important;
        white-space: normal !important;
        box-sizing: border-box !important;
    }
    .st-key-btn_logout_cliente {
        scroll-margin-bottom: calc(88px + env(safe-area-inset-bottom, 0px)) !important;
        margin-bottom: 0 !important;
        margin-top: 0 !important;
    }
    .mas-seccion {
        font-size: 0.75rem;
        font-weight: 800;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #64748b;
        line-height: 1.45;
        margin: 0 4px;
        padding: 2px 0;
        overflow: visible;
    }
    .mas-seccion-cuenta {
        margin-top: 0;
        margin-bottom: 0;
    }
    .mas-seccion-modulos {
        margin-top: 0;
        margin-bottom: 0;
    }
    .st-key-mas_cuenta {
        background: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 18px !important;
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06) !important;
        padding: 4px 6px 10px 6px !important;
        margin: 0 0 4px 0 !important;
        overflow: visible !important;
    }
    .st-key-mas_cuenta [data-testid="stElementContainer"],
    .st-key-mas_cuenta [data-testid="stLayoutWrapper"] {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }
    .mas-card,
    .mas-cuenta {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 16px 18px;
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
        margin-bottom: 8px;
    }
    .st-key-mas_cuenta .mas-cuenta {
        background: transparent;
        border: none;
        box-shadow: none;
        padding: 10px 12px 6px 12px;
        margin-bottom: 0;
    }
    .mas-cuenta-nombre {
        font-size: 1.05rem;
        font-weight: 800;
        color: #0f172a;
        line-height: 1.3;
    }
    .mas-cuenta-cas {
        font-size: 0.88rem;
        font-weight: 700;
        color: #004ac1;
        margin-top: 4px;
    }
    .mas-cuenta-mail {
        font-size: 0.82rem;
        color: #64748b;
        margin-top: 2px;
        word-break: break-word;
    }
    .st-key-mas_editar_perfil div.stButton > button,
    .st-key-mas_editar_perfil [data-testid^="stBaseButton"] {
        background: #f8fafc !important;
        background-color: #f8fafc !important;
        color: #004ac1 !important;
        -webkit-text-fill-color: #004ac1 !important;
        border: 1px solid #dbeafe !important;
        border-radius: 12px !important;
        min-height: 40px !important;
        height: 40px !important;
        font-weight: 700 !important;
        box-shadow: none !important;
        margin: 0 8px 4px 8px !important;
        width: calc(100% - 16px) !important;
    }

    [data-st-overlay-root="true"],
    [data-testid="stDialog"],
    .stDialog {
        color-scheme: light !important;
        z-index: 100000 !important;
    }
    [data-testid="stDialog"] {
        background: rgba(15, 23, 42, 0.38) !important;
        padding: max(12px, env(safe-area-inset-top, 0px)) 12px max(12px, env(safe-area-inset-bottom, 0px)) 12px !important;
    }
    [data-testid="stDialog"] > div {
        background: #ffffff !important;
        color: #0f172a !important;
        color-scheme: light !important;
        border-radius: 20px !important;
        box-shadow: 0 18px 48px rgba(15, 23, 42, 0.16) !important;
        border: 1px solid #e2e8f0 !important;
        max-width: min(720px, calc(100vw - 24px)) !important;
        width: min(720px, calc(100vw - 24px)) !important;
        margin: auto !important;
        overflow: auto !important;
        max-height: min(92vh, 860px) !important;
    }
    [data-testid="stDialog"] h1,
    [data-testid="stDialog"] h2,
    [data-testid="stDialog"] h3,
    [data-testid="stDialog"] [slot="title"],
    [data-testid="stDialog"] [data-testid="stMarkdownContainer"] h2,
    [data-testid="stDialog"] [data-testid="stHeading"] {
        color: #004ac1 !important;
        -webkit-text-fill-color: #004ac1 !important;
        font-weight: 800 !important;
        font-size: clamp(1.15rem, 2.4vw, 1.45rem) !important;
        letter-spacing: -0.02em !important;
    }
    [data-testid="stDialog"] p,
    [data-testid="stDialog"] label,
    [data-testid="stDialog"] [data-testid="stWidgetLabel"],
    [data-testid="stDialog"] [data-testid="stWidgetLabel"] p {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
    }
    .perfil-dialog-nota {
        color: #64748b !important;
        font-size: clamp(0.82rem, 2.4vw, 0.92rem);
        font-weight: 500;
        line-height: 1.4;
        margin: 0 0 12px 0;
    }
    .st-key-dialogo_perfil {
        background: #ffffff !important;
        color: #0f172a !important;
        padding-bottom: 8px !important;
    }
    .st-key-dialogo_perfil [data-testid="stTextInput"] input,
    .st-key-dialogo_perfil [data-testid="stTextArea"] textarea {
        background: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        min-height: 44px !important;
        font-size: clamp(0.92rem, 2.6vw, 1rem) !important;
    }
    .st-key-perfil_mail [data-testid="stTextInput"] input,
    .st-key-perfil_cas [data-testid="stTextInput"] input,
    .st-key-dialogo_perfil input:disabled,
    .st-key-dialogo_perfil textarea:disabled {
        background: #f1f5f9 !important;
        color: #64748b !important;
        -webkit-text-fill-color: #64748b !important;
        border-color: #e2e8f0 !important;
        cursor: not-allowed !important;
    }
    .st-key-perfil_guardar div.stButton > button,
    .st-key-perfil_guardar [data-testid^="stBaseButton"] {
        background: #004ac1 !important;
        background-color: #004ac1 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: none !important;
        border-radius: 12px !important;
        min-height: 46px !important;
        font-weight: 800 !important;
        font-size: clamp(0.92rem, 2.5vw, 1rem) !important;
    }
    .st-key-perfil_cancelar div.stButton > button,
    .st-key-perfil_cancelar [data-testid^="stBaseButton"] {
        background: #f1f5f9 !important;
        background-color: #f1f5f9 !important;
        color: #334155 !important;
        -webkit-text-fill-color: #334155 !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        min-height: 46px !important;
        font-weight: 700 !important;
        font-size: clamp(0.92rem, 2.5vw, 1rem) !important;
    }

    @media (max-width: 640px) {
        [data-testid="stDialog"] {
            padding: 0 !important;
            align-items: stretch !important;
        }
        [data-testid="stDialog"] > div {
            width: 100% !important;
            max-width: 100% !important;
            min-height: 100% !important;
            max-height: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
        }
        .st-key-dialogo_perfil [data-testid="stHorizontalBlock"] {
            flex-direction: column !important;
            flex-wrap: nowrap !important;
            gap: 0 !important;
        }
        .st-key-dialogo_perfil [data-testid="stHorizontalBlock"] > div,
        .st-key-dialogo_perfil [data-testid="stColumn"] {
            width: 100% !important;
            min-width: 100% !important;
            flex: 1 1 100% !important;
        }
        /* La fila final se apila en móvil, pero conserva separación y área segura. */
        .st-key-dialogo_perfil [data-testid="stHorizontalBlock"]:has(.st-key-perfil_cancelar),
        .st-key-dialogo_perfil [data-testid="stHorizontalBlock"]:has(.st-key-perfil_guardar) {
            gap: 12px !important;
            margin-top: 20px !important;
            margin-bottom: max(24px, env(safe-area-inset-bottom, 0px)) !important;
            padding-bottom: 8px !important;
        }
        .st-key-dialogo_perfil .st-key-perfil_cancelar,
        .st-key-dialogo_perfil .st-key-perfil_guardar {
            margin: 0 !important;
            padding: 0 !important;
        }
        .st-key-dialogo_perfil .st-key-perfil_cancelar div.stButton,
        .st-key-dialogo_perfil .st-key-perfil_guardar div.stButton {
            margin: 0 !important;
        }
        .st-key-perfil_cancelar div.stButton > button,
        .st-key-perfil_guardar div.stButton > button {
            min-height: 50px !important;
        }
    }

    @media (min-width: 641px) {
        [data-testid="stDialog"] > div {
            border-radius: 20px !important;
        }
        .st-key-dialogo_perfil {
            padding: 4px 4px 8px 4px !important;
        }
    }
    .st-key-mas_envios div.stButton > button,
    .st-key-mas_fichas div.stButton > button,
    .st-key-mas_cotizaciones div.stButton > button,
    .st-key-mas_catalogo div.stButton > button,
    .st-key-mas_cotizador div.stButton > button,
    .st-key-btn_guia_rapida div.stButton > button,
    .st-key-btn_logout_cliente div.stButton > button,
    .st-key-btn_guia_rapida button[kind="secondary"],
    .st-key-btn_logout_cliente button[kind="secondary"],
    .st-key-mas_envios [data-testid^="stBaseButton"],
    .st-key-mas_fichas [data-testid^="stBaseButton"],
    .st-key-mas_cotizaciones [data-testid^="stBaseButton"],
    .st-key-mas_catalogo [data-testid^="stBaseButton"],
    .st-key-mas_cotizador [data-testid^="stBaseButton"],
    .st-key-btn_guia_rapida [data-testid^="stBaseButton"],
    .st-key-btn_logout_cliente [data-testid^="stBaseButton"] {
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 16px !important;
        min-height: 54px !important;
        height: 54px !important;
        justify-content: flex-start !important;
        text-align: left !important;
        padding: 0 44px 0 16px !important;
        font-weight: 700 !important;
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.05) !important;
        position: relative !important;
    }
    .st-key-mas_envios div.stButton > button::after,
    .st-key-mas_fichas div.stButton > button::after,
    .st-key-mas_cotizaciones div.stButton > button::after,
    .st-key-mas_catalogo div.stButton > button::after,
    .st-key-mas_cotizador div.stButton > button::after {
        content: ">" !important;
        position: absolute !important;
        right: 16px !important;
        color: #94a3b8 !important;
        font-weight: 700 !important;
        font-size: 1.05rem !important;
    }

    .promo-ad-card {
        position: relative;
        z-index: 1;
        overflow: hidden;
        background:
            radial-gradient(circle at 95% 4%, rgba(251, 191, 36, 0.30) 0%, transparent 29%),
            radial-gradient(circle at 4% 98%, rgba(56, 189, 248, 0.28) 0%, transparent 35%),
            linear-gradient(135deg, #062c76 0%, #004ac1 55%, #1558d6 100%);
        border: 1px solid rgba(191, 219, 254, 0.42);
        border-radius: 20px;
        padding: 22px 20px 19px 20px;
        color: #ffffff;
        margin: 16px 0 22px 0;
        box-shadow: 0 18px 38px rgba(0, 58, 145, 0.30);
        box-sizing: border-box;
        scroll-margin-top: calc(var(--header-offset, 208px) + 12px);
    }
    .promo-ad-glow {
        position: absolute;
        right: -12px;
        top: 9px;
        font-size: 5rem;
        opacity: 0.16;
        transform: rotate(-9deg);
        pointer-events: none;
    }
    .promo-ad-kicker {
        display: flex;
        align-items: center;
        gap: 7px;
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #dbeafe;
        margin: 0 0 8px 0;
    }
    .promo-ad-live { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; box-shadow: 0 0 0 4px rgba(74,222,128,.16); }
    .promo-ad-title {
        font-size: clamp(1.36rem, 4vw, 1.72rem);
        font-weight: 900;
        line-height: 1.25;
        margin: 0 0 3px 0;
        color: #ffffff;
        letter-spacing: -0.02em;
    }
    .promo-ad-subtitle { font-size: 0.90rem; color: #fef3c7; margin: 0 0 13px 0; }
    .promo-ad-body {
        font-size: 0.88rem;
        font-weight: 500;
        line-height: 1.45;
        color: #e2e8f0;
        margin: 0 0 14px 0;
    }
    .promo-ad-badges {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        margin: 0 0 16px 0;
    }
    .promo-ad-badge {
        display: flex;
        gap: 7px;
        align-items: flex-start;
        min-width: 0;
        padding: 9px 8px;
        background: rgba(255,255,255,.12);
        border: 1px solid rgba(255,255,255,.20);
        border-radius: 12px;
        color: #fff;
    }
    .promo-ad-badge > span { font-size: 1.05rem; line-height: 1.1; }
    .promo-ad-badge div { min-width: 0; }
    .promo-ad-badge small { display: block; font-size: .57rem; letter-spacing: .045em; color: #bfdbfe; font-weight: 800; line-height: 1.2; }
    .promo-ad-badge b { display: block; font-size: .70rem; line-height: 1.25; margin-top: 3px; color: #fff; overflow-wrap: anywhere; }
    .promo-ad-badge-close { background: rgba(245, 158, 11, .20); border-color: rgba(253, 230, 138, .42); }
    .promo-ad-product-label { font-size: .65rem; font-weight: 800; letter-spacing: .08em; color: #bfdbfe; margin: 0 0 7px; }
    .promo-ad-products { display: flex; flex-wrap: wrap; gap: 7px; margin: 0 0 14px; }
    .promo-ad-products span { background: rgba(255,255,255,.13); border: 1px solid rgba(255,255,255,.18); border-radius: 9px; padding: 5px 8px; color: #fff; font-size: .72rem; font-weight: 700; }
    .promo-ad-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin: 0 0 16px 0;
    }
    .promo-ad-pill {
        background: rgba(255, 255, 255, 0.14);
        border: 1px solid rgba(255, 255, 255, 0.22);
        border-radius: 999px;
        padding: 5px 10px;
        font-size: 0.72rem;
        font-weight: 700;
        color: #ffffff;
    }
    .promo-ad-cta {
        display: block;
        width: 100%;
        box-sizing: border-box;
        text-align: center;
        background: linear-gradient(135deg, #fef3c7, #fbbf24);
        color: #713f12;
        font-weight: 800;
        font-size: 0.95rem;
        text-decoration: none;
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.18);
    }
    .promo-ad-cta:hover { filter: brightness(1.04); color: #713f12; text-decoration: none; }
    @media (max-width: 640px) {
        .promo-ad-card { padding: 18px 15px 16px 15px; border-radius: 17px; }
        .promo-ad-title { font-size: 1.22rem; padding-right: 32px; }
        .promo-ad-body { font-size: .82rem; line-height: 1.43; }
        .promo-ad-badges { grid-template-columns: 1fr; gap: 7px; }
        .promo-ad-badge { padding: 8px 10px; }
        .promo-ad-badge b { font-size: .77rem; }
    }

    .st-key-bottom_nav [data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 8px !important;
        align-items: stretch !important;
        width: 100% !important;
        margin: 0 !important;
    }
    .st-key-bottom_nav [data-testid="stHorizontalBlock"] > div,
    .st-key-bottom_nav [data-testid="stColumn"] {
        flex: 1 1 0 !important;
        min-width: 0 !important;
        width: auto !important;
        padding: 0 !important;
    }

    .st-key-china_modulos {
        padding-bottom: 12px !important;
    }

    .st-key-guia_coach {
        position: relative !important;
        top: auto !important;
        z-index: 1 !important;
        background: #fffbeb !important;
        border: 1.5px solid #f59e0b !important;
        border-radius: 12px !important;
        padding: 10px 12px 8px 12px !important;
        margin: 4px 0 12px 0 !important;
        box-shadow: 0 8px 18px rgba(245, 158, 11, 0.18) !important;
        box-sizing: border-box !important;
        overflow: visible !important;
    }
    .st-key-guia_coach:not(:has(.ccm-guia-card)),
    .st-key-guia_coach:has(.ccm-guia-vacia) {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        overflow: hidden !important;
        box-shadow: none !important;
        background: transparent !important;
        visibility: hidden !important;
    }
    [data-testid="stElementContainer"]:has(.st-key-guia_coach:not(:has(.ccm-guia-card))),
    [data-testid="stLayoutWrapper"]:has(.st-key-guia_coach:not(:has(.ccm-guia-card))),
    [data-testid="stElementContainer"]:has(.ccm-guia-vacia),
    [data-testid="stLayoutWrapper"]:has(.ccm-guia-vacia) {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: 0 !important;
    }
    .guia-globo { margin: 0; }
    .guia-globo-kicker {
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #b45309;
        margin: 0 0 4px 0;
    }
    .guia-globo-titulo {
        font-size: 0.98rem;
        font-weight: 800;
        color: #0f172a;
        margin: 0 0 4px 0;
    }
    .guia-globo-txt {
        margin: 0;
        color: #334155;
        font-size: 0.84rem;
        line-height: 1.4;
        font-weight: 600;
    }
    .st-key-btn_omitir_guia button,
    .st-key-btn_omitir_guia button[kind="secondary"],
    .st-key-btn_omitir_guia [data-testid^="stBaseButton"] {
        min-height: 36px !important;
        height: 36px !important;
        font-size: 0.78rem !important;
        font-weight: 700 !important;
        margin-top: 6px !important;
        background: #e5e7eb !important;
        background-color: #e5e7eb !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1px solid #d1d5db !important;
        box-shadow: none !important;
    }
    .st-key-btn_omitir_guia button *,
    .st-key-btn_omitir_guia button[kind="secondary"] * {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        fill: #0f172a !important;
    }
    .st-key-btn_omitir_guia button:hover,
    .st-key-btn_omitir_guia button[kind="secondary"]:hover {
        background: #d1d5db !important;
        background-color: #d1d5db !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border-color: #9ca3af !important;
        transform: none !important;
    }

    .st-key-btn_guia_rapida button:hover,
    .st-key-btn_guia_rapida button[kind="secondary"]:hover {
        background: #f8fafc !important;
        background-color: #f8fafc !important;
        color: #004ac1 !important;
        border-color: #004ac1 !important;
        box-shadow: 0 4px 10px rgba(0,74,193,0.12) !important;
    }
    .st-key-btn_guia_rapida button:hover *,
    .st-key-btn_guia_rapida button[kind="secondary"]:hover * {
        color: #004ac1 !important;
        fill: #004ac1 !important;
    }

    @keyframes guiaPulse {
        0%, 100% {
            box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.50);
            outline: 2px solid #f59e0b;
        }
        50% {
            box-shadow: 0 0 0 10px rgba(245, 158, 11, 0.12);
            outline: 3px solid #d97706;
        }
    }
    .stApp.guia-paso-1 .st-key-mod_cotizador button,
    .stApp.guia-paso-1 .st-key-mod_cotizador [data-testid^="stBaseButton"],
    .stApp.guia-paso-1 .st-key-nav_mod_cotizador button,
    .stApp.guia-paso-1 .st-key-nav_mod_cotizador [data-testid^="stBaseButton"],
    .stApp.guia-paso-1 .st-key-bnav_cotizador button,
    .stApp.guia-paso-1 .st-key-bnav_cotizador [data-testid^="stBaseButton"],
    .stApp.guia-paso-2 .st-key-guia_foco_tarifa button,
    .stApp.guia-paso-2 .st-key-guia_foco_tarifa [data-testid^="stBaseButton"],
    .stApp.guia-paso-2 .st-key-btn_confirmar_tarifa button,
    .stApp.guia-paso-2 .st-key-btn_confirmar_tarifa [data-testid^="stBaseButton"],
    .stApp.guia-paso-3 .st-key-guia_foco_pdf_fab button,
    .stApp.guia-paso-3 .st-key-guia_foco_pdf_fab [data-testid^="stBaseButton"],
    .stApp.guia-paso-3 [class*="st-key-dl_pdf_fab_"] button,
    .stApp.guia-paso-3 [class*="st-key-dl_pdf_fab_"] [data-testid^="stBaseButton"],
    .stApp.guia-paso-4 .st-key-guia_foco_ver_cot button,
    .stApp.guia-paso-4 .st-key-guia_foco_ver_cot [data-testid^="stBaseButton"],
    .stApp.guia-paso-4 [class*="st-key-btn_ver_mis_cotizaciones_"] button,
    .stApp.guia-paso-4 [class*="st-key-btn_ver_mis_cotizaciones_"] [data-testid^="stBaseButton"],
    .stApp.guia-paso-5 [class*="st-key-foco_confirmar_"] button,
    .stApp.guia-paso-5 [class*="st-key-foco_confirmar_"] [data-testid^="stBaseButton"],
    .stApp.guia-paso-5 [class*="st-key-btn_confirmar_cot_"] button,
    .stApp.guia-paso-6 [class*="st-key-foco_ir_envios_"] button,
    .stApp.guia-paso-6 [class*="st-key-foco_ir_envios_"] [data-testid^="stBaseButton"],
    .stApp.guia-paso-6 [class*="st-key-btn_ir_envios_"] button,
    .stApp.guia-paso-6 [class*="st-key-btn_ir_envios_"] [data-testid^="stBaseButton"],
    button.ccm-guia-pulse,
    [data-testid^="stBaseButton"].ccm-guia-pulse {
        animation: guiaPulse 1.2s ease-in-out infinite !important;
        z-index: 2 !important;
    }

    @keyframes pulseBlink {
        0% { opacity: 0.35; }
        50% { opacity: 1; }
        100% { opacity: 0.35; }
    }

    @keyframes ccmVersiculoBrillo {
        0%, 100% {
            opacity: 0.90;
            box-shadow: 0 8px 22px rgba(0, 74, 193, 0.13);
            border-color: rgba(96, 165, 250, 0.42);
        }
        50% {
            opacity: 1;
            box-shadow: 0 12px 30px rgba(0, 74, 193, 0.25), 0 0 0 4px rgba(96, 165, 250, 0.08);
            border-color: rgba(147, 197, 253, 0.90);
        }
    }
    .ccm-versiculo-banner {
        position: relative;
        overflow: hidden;
        margin: 18px 0 8px;
        padding: 18px 22px;
        border: 1px solid rgba(96, 165, 250, 0.42);
        border-radius: 18px;
        background: linear-gradient(118deg, #003f9e 0%, #0756c9 54%, #0b6ddd 100%);
        color: #ffffff;
        text-align: center;
        animation: ccmVersiculoBrillo 3.6s ease-in-out infinite;
    }
    .ccm-versiculo-banner::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(120deg, transparent 18%, rgba(255,255,255,0.12) 50%, transparent 82%);
        transform: translateX(-72%);
        animation: ccmVersiculoDestello 5.5s ease-in-out infinite;
    }
    @keyframes ccmVersiculoDestello {
        0%, 56%, 100% { transform: translateX(-72%); }
        75% { transform: translateX(72%); }
    }
    .ccm-versiculo-texto,
    .ccm-versiculo-referencia {
        position: relative;
        z-index: 1;
    }
    .ccm-versiculo-texto {
        font-size: clamp(0.86rem, 2.5vw, 1.04rem);
        font-weight: 800;
        line-height: 1.5;
        letter-spacing: 0.035em;
    }
    .ccm-versiculo-referencia {
        display: block;
        margin-top: 7px;
        color: #dbeafe;
        font-size: 0.74rem;
        font-weight: 800;
        letter-spacing: 0.18em;
    }
    @media (prefers-reduced-motion: reduce) {
        .ccm-versiculo-banner,
        .ccm-versiculo-banner::before { animation: none !important; }
    }

    @keyframes cotPulseBorde {
        0%, 100% {
            box-shadow: 0 0 0 0 rgba(217, 119, 6, 0.38);
            border-color: #f59e0b;
        }
        50% {
            box-shadow: 0 0 0 7px rgba(245, 158, 11, 0.12);
            border-color: #d97706;
        }
    }
    @keyframes cotPulseInsignia {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.42; }
    }
    @keyframes cotPulseBoton {
        0%, 100% { box-shadow: 0 0 0 0 rgba(217, 119, 6, 0.40); }
        50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0.16); }
    }

    .cotizacion-pendiente-foco {
        animation: cotPulseBorde 1.55s ease-in-out infinite;
        scroll-margin-top: var(--header-offset) !important;
    }
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        border-radius: 0 !important;
        padding: 0 !important;
        margin: 0 0 16px 0 !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        overflow: visible !important;
        box-sizing: border-box !important;
        flex: 0 0 auto !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        gap: 10px !important;
    }
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) > [data-testid="stVerticalBlockBorderWrapper"],
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) > [data-testid="stVerticalBlock"],
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) > [data-testid="stLayoutWrapper"] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        flex: 0 0 auto !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        gap: 10px !important;
        width: 100% !important;
    }
    [class*="st-key-tarjeta_cot_info_"],
    [class*="st-key-tarjeta_cot_info_"] > [data-testid="stVerticalBlockBorderWrapper"],
    [class*="st-key-tarjeta_cot_info_"] [data-testid="stVerticalBlockBorderWrapper"],
    [class*="st-key-tarjeta_cot_info_"] [data-testid="stVerticalBlock"],
    [class*="st-key-tarjeta_cot_info_"] [data-testid="stElementContainer"],
    [class*="st-key-tarjeta_cot_info_"] [data-testid="stLayoutWrapper"],
    [class*="st-key-tarjeta_cot_info_"] [data-testid="stMarkdown"],
    [class*="st-key-tarjeta_cot_info_"] [data-testid="stMarkdownContainer"] {
        background: transparent !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        border-radius: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        overflow: visible !important;
        flex: 0 0 auto !important;
    }
    [class*="st-key-tarjeta_cot_"] .cot-card-body {
        display: flex !important;
        flex-direction: column !important;
        gap: 8px !important;
        margin: 0 !important;
        padding: 12px 14px !important;
        font-size: 0.85rem !important;
        line-height: 1.45 !important;
        background: #ffffff !important;
        border: 1.5px solid #e2e8f0 !important;
        border-radius: 12px !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        width: 100% !important;
    }
    [class*="st-key-tarjeta_cot_"] .cot-card-body.cotizacion-pendiente-caja,
    [class*="st-key-tarjeta_cot_"] .cot-card-body.cotizacion-pendiente-foco {
        background: #fffbeb !important;
        border-color: #c9a227 !important;
    }
    [class*="st-key-tarjeta_cot_"] .cot-card-head {
        display: flex !important;
        flex-wrap: wrap !important;
        align-items: center !important;
        gap: 6px 8px !important;
        margin: 0 !important;
    }
    [class*="st-key-tarjeta_cot_"] .cot-card-id,
    [class*="st-key-tarjeta_cot_"] .cot-card-meta,
    [class*="st-key-tarjeta_cot_"] .cot-card-vigencia {
        margin: 0 !important;
        padding: 0 !important;
        word-break: break-word !important;
        overflow-wrap: anywhere !important;
    }
    [class*="st-key-tarjeta_cot_"] .cot-card-vigencia {
        font-weight: 700 !important;
    }
    [class*="st-key-tarjeta_cot_"] .cot-card-body p {
        margin: 0 !important;
    }
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) [data-testid="stElementContainer"],
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) [data-testid="stLayoutWrapper"],
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) [data-testid="stDownloadButton"],
    [class*="st-key-tarjeta_cot_"]:not([class*="tarjeta_cot_info"]) div.stButton,
    [class*="st-key-foco_confirmar_"] {
        height: auto !important;
        min-height: 0 !important;
        overflow: visible !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        flex: 0 0 auto !important;
    }
    .st-key-vista_historial [data-testid="stCustomComponentV1"],
    .st-key-vista_historial [data-testid="stElementContainer"]:has(> [data-testid="stCustomComponentV1"]),
    .st-key-vista_historial [data-testid="stLayoutWrapper"]:has(> [data-testid="stCustomComponentV1"]),
    [class*="st-key-tarjeta_cot_"] [data-testid="stCustomComponentV1"],
    [class*="st-key-tarjeta_cot_"] iframe {
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        overflow: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    [class*="st-key-tarjeta_cot_"] div.stButton > button,
    [class*="st-key-tarjeta_cot_"] [data-testid^="stBaseButton"],
    [class*="st-key-tarjeta_cot_"] [data-testid="stDownloadButton"] button {
        width: 100% !important;
        height: auto !important;
        min-height: 44px !important;
        max-height: none !important;
        margin: 0 !important;
        white-space: normal !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
    }
    [class*="st-key-docs_env_"] {
        display: flex !important;
        flex-direction: column !important;
        height: auto !important;
        overflow: visible !important;
        width: 100% !important;
        margin: 10px 0 0 0 !important;
        padding-top: 0 !important;
        gap: 8px !important;
        scroll-margin-bottom: calc(var(--ccm-nav-clearance, 109px) + 12px) !important;
    }
    .st-key-acciones_emit_cotizador {
        display: flex !important;
        flex-direction: column !important;
        height: auto !important;
        overflow: visible !important;
        width: 100% !important;
        margin: 16px 0 0 0 !important;
        padding-top: 4px !important;
        gap: 12px !important;
        scroll-margin-bottom: calc(var(--ccm-nav-clearance) + 16px) !important;
    }
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) .st-key-acciones_emit_cotizador {
        margin-top: auto !important;
        margin-bottom: 0 !important;
    }
    [class*="st-key-docs_env_"] [data-testid="stDownloadButton"],
    [class*="st-key-docs_env_"] div.stButton,
    .st-key-acciones_emit_cotizador [data-testid="stDownloadButton"],
    .st-key-acciones_emit_cotizador div.stButton,
    .st-key-acciones_emit_cotizador [data-testid="stElementContainer"],
    .st-key-acciones_emit_cotizador [data-testid="stLayoutWrapper"] {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        height: auto !important;
        overflow: visible !important;
        width: 100% !important;
    }
    .st-key-acciones_emit_cotizador [data-testid="stDownloadButton"] button,
    .st-key-acciones_emit_cotizador div.stButton > button,
    .st-key-acciones_emit_cotizador [data-testid^="stBaseButton"] {
        white-space: normal !important;
        height: auto !important;
        min-height: 48px !important;
        line-height: 1.25 !important;
        padding-top: 10px !important;
        padding-bottom: 10px !important;
    }
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) .st-key-safe_cotizador_fin,
    .st-key-vista_cotizador:has(.st-key-guia_foco_pdf_fab) .st-key-safe_cotizador_fin {
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        opacity: 0 !important;
        overflow: hidden !important;
        pointer-events: none !important;
    }
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) [data-testid="stElementContainer"]:has(.st-key-safe_cotizador_fin),
    .st-key-vista_cotizador:has(.st-key-acciones_emit_cotizador) [data-testid="stLayoutWrapper"]:has(.st-key-safe_cotizador_fin),
    .st-key-vista_cotizador:has(.st-key-guia_foco_pdf_fab) [data-testid="stElementContainer"]:has(.st-key-safe_cotizador_fin),
    .st-key-vista_cotizador:has(.st-key-guia_foco_pdf_fab) [data-testid="stLayoutWrapper"]:has(.st-key-safe_cotizador_fin) {
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        overflow: hidden !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    .st-key-vista_historial {
        padding-top: 12px !important;
    }
    @keyframes pulso-suave {
        0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(30, 77, 183, 0.4); }
        50% { transform: scale(1.008); box-shadow: 0 0 10px 4px rgba(30, 77, 183, 0.2); }
        100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(30, 77, 183, 0); }
    }
    .destino-seleccionado-card {
        animation: pulso-suave 2.5s infinite ease-in-out;
        border-left: 4px solid #1E4DB7;
        background-color: #EBF3FF;
        padding: 10px 14px;
        border-radius: 8px;
        margin: 4px 0 10px 0;
        box-sizing: border-box;
        width: 100%;
    }
    .destino-seleccionado-kicker {
        font-size: 0.74rem;
        font-weight: 800;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        color: #1E4DB7;
        margin: 0 0 2px 0;
    }
    .destino-seleccionado-dir {
        font-size: 0.92rem;
        font-weight: 800;
        color: #0f2a6b;
        line-height: 1.35;
        word-break: break-word;
        margin: 0;
    }
    .destino-seleccionado-nota {
        font-size: 0.74rem;
        color: #64748b;
        margin: 2px 0 0 0;
    }
    .cotizacion-badge-pendiente {
        display: inline-flex;
        align-items: center;
        margin: 0;
        background: #fff7ed;
        color: #b45309;
        border: 1px solid #f59e0b;
        border-radius: 999px;
        padding: 2px 8px;
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.01em;
        animation: cotPulseInsignia 1.2s ease-in-out infinite;
        vertical-align: middle;
        flex: 0 0 auto;
        white-space: nowrap;
    }
    [class*="st-key-foco_confirmar_"] button,
    [class*="st-key-foco_confirmar_"] [data-testid^="stBaseButton"] {
        animation: cotPulseBoton 1.55s ease-in-out infinite;
    }

    .swipe-indicator-bar {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 6px;
        color: #3b82f6;
        font-size: 0.80rem;
        font-weight: 800;
        margin: 4px 0 8px 0;
        animation: pulseBlink 1.4s infinite ease-in-out;
        user-select: none;
        transform: none;
        overflow: visible;
        line-height: 1.3;
    }

    .banner-clearance {
        height: 12px;
        width: 100%;
    }

    .app-banner-card {
        background: linear-gradient(135deg, #eff6ff 0%, #f8fafc 100%);
        border: 1px solid #bfdbfe;
        border-radius: 16px;
        padding: 14px 16px;
        color: #0f172a;
        margin: 0.85rem auto 1rem auto;
        max-width: 34rem;
        width: 100%;
        box-sizing: border-box;
        text-align: center;
        box-shadow: 0 4px 12px rgba(0, 74, 193, 0.08);
    }
    .app-banner-title {
        font-size: clamp(0.92rem, 3.6cqi, 1.08rem);
        font-weight: 800;
        line-height: 1.35;
        margin: 0 auto 6px auto;
        color: #0f172a;
        max-width: 28rem;
    }
    .app-banner-accent {
        color: #004ac1;
    }
    .app-banner-sub {
        font-size: clamp(0.78rem, 3cqi, 0.88rem);
        color: #475569;
        font-weight: 500;
        line-height: 1.45;
        margin: 0 auto;
        max-width: 28rem;
    }
    .app-banner-tag {
        background: #ec4899;
        color: #ffffff;
        font-size: 0.72rem;
        font-weight: 800;
        padding: 3px 10px;
        border-radius: 6px;
        display: inline-block;
        margin: 0 auto 8px auto;
    }

    .card-box {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 1.2rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }
    .inicio-placeholder {
        min-height: 430px;
        margin-top: 8px;
    }
    .inicio-placeholder-head {
        display: flex;
        align-items: center;
        gap: 10px;
        padding-bottom: 14px;
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 18px;
        font-size: 1.05rem;
        font-weight: 800;
        color: #0f172a;
    }
    .inicio-placeholder-body {
        min-height: 340px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        color: #94a3b8;
    }
    .inicio-placeholder-plus {
        font-size: 2.2rem;
        margin-bottom: 8px;
    }
    .inicio-placeholder-title {
        font-size: 0.95rem;
        font-weight: 700;
        color: #64748b;
    }
    .inicio-placeholder-sub {
        font-size: 0.78rem;
        margin-top: 5px;
    }
    .hub-menu-head {
        display: flex;
        align-items: center;
        gap: 10px;
        padding-bottom: 12px;
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 14px;
        font-size: 1.02rem;
        font-weight: 800;
        color: #0f172a;
    }
    .hub-menu-caption {
        font-size: 0.78rem;
        color: #64748b;
        font-weight: 600;
        margin: -6px 0 12px 0;
    }
    .hub-empty-box {
        background: #f8fafc;
        border: 1.5px dashed #cbd5e1;
        border-radius: 14px;
        padding: 28px 16px;
        text-align: center;
        color: #64748b;
        margin-top: 8px;
    }
    .ae-casillero-chip {
        display: inline-block;
        background: #e8eef9;
        color: #003399;
        font-weight: 700;
        font-size: 0.78rem;
        padding: 4px 12px;
        border-radius: 999px;
        margin: 4px 0 12px 0;
    }
    .ae-price {
        font-weight: 800;
        color: #003399;
        font-size: 1.12rem;
        margin: 2px 0 6px 0;
    }
    .ae-empty-hint {
        text-align: left;
    }
    .china-address-box {
        background-color: #0f172a;
        border: 2px dashed #0052cc;
        border-radius: 12px;
        padding: 1.2rem;
        font-family: 'Space Mono', monospace;
        font-size: 0.82rem;
        color: #ffffff;
    }

    .stTextInput [data-testid="stWidgetLabel"],
    .stNumberInput [data-testid="stWidgetLabel"],
    .stSelectbox [data-testid="stWidgetLabel"],
    .stTextArea [data-testid="stWidgetLabel"],
    .stRadio [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        font-weight: 700 !important;
        font-size: 0.84rem !important;
        margin-bottom: 4px !important;
    }

    [data-testid="stRadio"],
    [data-testid="stRadioGroup"],
    [data-testid="stRadioOption"] {
        color: #0f172a !important;
    }

    [data-testid="stRadioOption"] {
        display: flex !important;
        align-items: flex-start !important;
        width: 100% !important;
        margin: 6px 0 !important;
        overflow: visible !important;
        height: auto !important;
    }

    [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"],
    [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stRadioOption"] [data-testid="stCaptionContainer"],
    [data-testid="stRadioOption"] span,
    .stRadio [data-testid="stMarkdownContainer"] p {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        opacity: 1 !important;
        visibility: visible !important;
        font-size: 0.92rem !important;
        font-weight: 600 !important;
        line-height: 1.35 !important;
        display: block !important;
    }

    [data-testid="stAlert"],
    [data-testid="stNotification"],
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"],
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] span,
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] strong,
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] code,
    [data-testid="stNotification"] p {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        opacity: 1 !important;
        visibility: visible !important;
    }

    [data-testid="stAlertContentSuccess"],
    [data-testid="stAlertContentSuccess"] p {
        background-color: #ecfdf5 !important;
        color: #14532d !important;
        -webkit-text-fill-color: #14532d !important;
    }

    [data-testid="stAlertContentWarning"],
    [data-testid="stAlertContentWarning"] p {
        background-color: #fffbeb !important;
        color: #92400e !important;
        -webkit-text-fill-color: #92400e !important;
    }

    [data-testid="stAlertContentInfo"],
    [data-testid="stAlertContentInfo"] p {
        background-color: #eff6ff !important;
        color: #1e3a8a !important;
        -webkit-text-fill-color: #1e3a8a !important;
    }

    [data-testid="stAlertContentError"],
    [data-testid="stAlertContentError"] p {
        background-color: #fef2f2 !important;
        color: #991b1b !important;
        -webkit-text-fill-color: #991b1b !important;
    }

    .reg-confirm-card {
        background: #ecfdf5;
        border: 2px solid #16a34a;
        border-radius: 14px;
        padding: 16px 14px;
        color: #14532d;
        margin: 12px 0 16px 0;
    }
    .reg-confirm-card h4 {
        margin: 0 0 10px 0;
        color: #14532d;
        font-size: 1.08rem;
        font-weight: 800;
    }
    .reg-confirm-card p, .reg-confirm-card div {
        color: #14532d;
        font-size: 0.92rem;
        font-weight: 600;
        line-height: 1.45;
        margin: 0 0 6px 0;
    }
    .reg-warn-card {
        background: #fffbeb;
        border: 2px solid #d97706;
        border-radius: 14px;
        padding: 14px;
        color: #92400e;
        font-weight: 700;
        margin: 12px 0;
    }

    div[data-baseweb="input"],
    div[data-baseweb="input"] > div,
    div[data-baseweb="base-input"],
    div[data-baseweb="textarea"],
    [data-testid="stNumberInputContainer"],
    [data-testid="stNumberInputContainer"] > div,
    [data-testid="stTextInput"] > div,
    input, textarea {
        background-color: #ffffff !important;
        background: #ffffff !important;
        border-color: #cbd5e1 !important;
        color: #0f172a !important;
        color-scheme: light !important;
    }

    div[data-baseweb="input"], div[data-baseweb="textarea"] {
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 10px !important;
        padding: 2px 6px !important;
    }

    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea,
    div[data-baseweb="input"] input:focus,
    div[data-baseweb="textarea"] textarea:focus,
    div[data-baseweb="textarea"] > div,
    [data-testid="stNumberInputContainer"] input,
    [data-testid="stNumberInput"] input,
    input[type="number"],
    input[type="text"],
    input[type="password"],
    textarea {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        font-size: 0.92rem !important;
        font-weight: 600 !important;
        background-color: #ffffff !important;
        background: #ffffff !important;
        color-scheme: light !important;
    }

    [data-testid="stNumberInputContainer"] button,
    [data-testid="stNumberInput"] button {
        background-color: #f1f5f9 !important;
        color: #0f172a !important;
        border-color: #cbd5e1 !important;
    }

    div[data-baseweb="input"] input::placeholder, div[data-baseweb="textarea"] textarea::placeholder, textarea::placeholder {
        color: #94a3b8 !important;
        -webkit-text-fill-color: #94a3b8 !important;
        font-weight: 400 !important;
    }

    [data-testid="stMetric"] {
        background: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 12px 10px !important;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04) !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.15rem !important;
        font-weight: 800 !important;
        color: #004ac1 !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.78rem !important;
        font-weight: 700 !important;
        color: #475569 !important;
    }
    [data-testid="stDataFrame"],
    [data-testid="stDataFrameResizable"],
    .stDataFrame,
    [data-testid="stMetricContainer"] {
        background-color: #ffffff !important;
    }

    div.stButton > button, div.stDownloadButton > button {
        width: 100% !important;
        height: 44px !important;
        min-height: 44px !important;
        max-height: 44px !important;
        border-radius: 10px !important;
        padding: 0 4px !important;
        font-size: 0.76rem !important;
        font-weight: 700 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        white-space: nowrap !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }

    div.stButton > button[kind="primary"], div.stDownloadButton > button {
        background: linear-gradient(135deg, #004ac1, #00368c) !important;
        color: #ffffff !important;
        border: 1px solid #004ac1 !important;
        box-shadow: 0 4px 12px rgba(0, 74, 193, 0.28) !important;
    }
    div.stButton > button[kind="primary"] * {
        color: #ffffff !important;
    }

    div.stButton > button[kind="secondary"] {
        background: #ffffff !important;
        color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04) !important;
    }
    div.stButton > button[kind="secondary"] * {
        color: #0f172a !important;
    }
    div.stButton > button[kind="secondary"]:hover {
        background-color: #f8fafc !important;
        border-color: #004ac1 !important;
        color: #004ac1 !important;
        box-shadow: 0 4px 10px rgba(0, 74, 193, 0.12) !important;
        transform: translateY(-1px);
    }
    div.stButton > button[kind="secondary"]:hover * {
        color: #004ac1 !important;
    }

    .st-key-btn_logout_cliente button:hover,
    .st-key-btn_logout_cliente button[kind="secondary"]:hover {
        background: #f8fafc !important;
        background-color: #f8fafc !important;
        color: #004ac1 !important;
        border-color: #004ac1 !important;
        box-shadow: 0 4px 10px rgba(0,74,193,0.12) !important;
    }

    .st-key-btn_logout_cliente button *,
    .st-key-btn_logout_cliente button[kind="secondary"] * {
        color: #0f172a !important;
        fill: #0f172a !important;
    }

    .st-key-btn_logout_cliente button:hover *,
    .st-key-btn_logout_cliente button[kind="secondary"]:hover * {
        color: #004ac1 !important;
        fill: #004ac1 !important;
    }

    .st-key-bottom_nav div.stButton,
    .st-key-bottom_nav div.stButton > button,
    .st-key-bottom_nav div.stButton > button[kind="primary"],
    .st-key-bottom_nav div.stButton > button[kind="secondary"],
    .st-key-bottom_nav [data-testid^="stBaseButton"],
    .st-key-bottom_nav [data-testid="stBaseButton-primary"],
    .st-key-bottom_nav [data-testid="stBaseButton-secondary"],
    .st-key-bnav_inicio button,
    .st-key-bnav_catalogo button,
    .st-key-bnav_cotizaciones button,
    .st-key-bnav_actividad button,
    .st-key-bnav_cotizador button,
    .st-key-bnav_mas button {
        width: 100% !important;
        height: auto !important;
        min-height: 52px !important;
        max-height: none !important;
        border-radius: 20px !important;
        padding: 6px 2px 5px 2px !important;
        margin: 0 !important;
        font-size: 0.58rem !important;
        font-weight: 700 !important;
        line-height: 1.15 !important;
        white-space: pre-line !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        box-shadow: none !important;
        border: none !important;
        outline: none !important;
        background: transparent !important;
        background-color: transparent !important;
        color: #666666 !important;
        -webkit-text-fill-color: #666666 !important;
        transform: none !important;
    }
    .st-key-bottom_nav *:focus,
    .st-key-bottom_nav *:focus-visible,
    .st-key-bottom_nav button:focus,
    .st-key-bottom_nav button:focus-visible {
        outline: none !important;
        box-shadow: none !important;
        border: none !important;
    }
    .st-key-bottom_nav div.stButton > button *,
    .st-key-bottom_nav div.stButton > button[kind="secondary"] *,
    .st-key-bottom_nav [data-testid="stBaseButton-secondary"] * {
        color: #666666 !important;
        -webkit-text-fill-color: #666666 !important;
        fill: #666666 !important;
    }
    .st-key-bottom_nav div.stButton > button[kind="primary"],
    .st-key-bottom_nav div.stButton > button[kind="primary"]:hover,
    .st-key-bottom_nav [data-testid="stBaseButton-primary"],
    .st-key-bottom_nav [data-testid="stBaseButton-primary"]:hover,
    .st-key-bnav_inicio button[kind="primary"],
    .st-key-bnav_catalogo button[kind="primary"],
    .st-key-bnav_cotizador button[kind="primary"],
    .st-key-bnav_mas button[kind="primary"],
    .st-key-bnav_inicio [data-testid="stBaseButton-primary"],
    .st-key-bnav_catalogo [data-testid="stBaseButton-primary"],
    .st-key-bnav_cotizador [data-testid="stBaseButton-primary"],
    .st-key-bnav_mas [data-testid="stBaseButton-primary"] {
        background: #E8EEFF !important;
        background-color: #E8EEFF !important;
        color: #003399 !important;
        -webkit-text-fill-color: #003399 !important;
        border-radius: 20px !important;
        box-shadow: none !important;
        border: none !important;
        outline: none !important;
    }
    .st-key-bottom_nav div.stButton > button[kind="primary"] *,
    .st-key-bottom_nav [data-testid="stBaseButton-primary"] * {
        color: #003399 !important;
        -webkit-text-fill-color: #003399 !important;
        fill: #003399 !important;
    }
    .st-key-bnav_cotizaciones div.stButton > button,
    .st-key-bnav_cotizaciones div.stButton > button[kind="primary"],
    .st-key-bnav_cotizaciones div.stButton > button[kind="secondary"],
    .st-key-bnav_cotizaciones [data-testid^="stBaseButton"],
    .st-key-bnav_actividad div.stButton > button,
    .st-key-bnav_actividad div.stButton > button[kind="primary"],
    .st-key-bnav_actividad div.stButton > button[kind="secondary"],
    .st-key-bnav_actividad [data-testid^="stBaseButton"],
    .st-key-bottom_nav [data-testid="stHorizontalBlock"] > div:nth-child(3) div.stButton > button,
    .st-key-bottom_nav [data-testid="stHorizontalBlock"] > div:nth-child(3) div.stButton > button[kind="primary"],
    .st-key-bottom_nav [data-testid="stHorizontalBlock"] > div:nth-child(3) div.stButton > button[kind="secondary"],
    .st-key-bottom_nav [data-testid="stColumn"]:nth-child(3) button,
    .st-key-bottom_nav [data-testid="stColumn"]:nth-child(3) [data-testid^="stBaseButton"] {
        position: relative !important;
        font-weight: 800 !important;
    }
    .st-key-bnav_cotizaciones div.stButton > button::after,
    .st-key-bnav_cotizaciones [data-testid^="stBaseButton"]::after,
    .st-key-bnav_actividad div.stButton > button::after,
    .st-key-bnav_actividad [data-testid^="stBaseButton"]::after,
    .st-key-bottom_nav [data-testid="stHorizontalBlock"] > div:nth-child(3) div.stButton > button::after {
        content: var(--ccm-cot-badge, "0");
        position: absolute;
        top: 3px;
        right: 6px;
        min-width: 16px;
        height: 16px;
        padding: 0 4px;
        border-radius: 999px;
        background: #004ac1;
        color: #ffffff;
        font-size: 0.58rem;
        font-weight: 800;
        line-height: 16px;
        text-align: center;
    }

    .st-key-hub_china div.stButton > button,
    .st-key-hub_eeuu div.stButton > button,
    .st-key-hub_honduras div.stButton > button {
        height: 72px !important;
        min-height: 72px !important;
        max-height: 72px !important;
        font-size: 1.05rem !important;
        border-radius: 14px !important;
        justify-content: flex-start !important;
        padding: 0 16px !important;
        white-space: normal !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
    }

    .st-key-china_modulos button,
    .st-key-china_modulos [data-testid^="stBaseButton"],
    .st-key-china_modulos div.stButton > button,
    .st-key-mod_cotizaciones button,
    .st-key-mod_catalogo button,
    .st-key-mod_cotizador button,
    .st-key-mod_envios button,
    .st-key-mod_fichas button {
        height: 64px !important;
        min-height: 64px !important;
        max-height: 72px !important;
        font-size: 0.92rem !important;
        font-weight: 800 !important;
        border-radius: 14px !important;
        white-space: normal !important;
        line-height: 1.25 !important;
        padding: 10px 12px !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06) !important;
    }

    .st-key-china_modulos button *,
    .st-key-china_modulos [data-testid^="stBaseButton"] *,
    .st-key-mod_cotizaciones button *,
    .st-key-mod_catalogo button *,
    .st-key-mod_cotizador button *,
    .st-key-mod_envios button *,
    .st-key-mod_fichas button * {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        fill: #0f172a !important;
    }

    .st-key-china_modulos button:hover,
    .st-key-china_modulos [data-testid^="stBaseButton"]:hover {
        background: #f8fafc !important;
        border-color: #004ac1 !important;
        color: #004ac1 !important;
    }

    .mod-detalle {
        font-size: 0.72rem;
        font-weight: 600;
        color: #64748b;
        text-align: center;
        margin: 4px 0 12px 0;
        line-height: 1.3;
    }
    /* Cotizador EE.UU.: tarjetas legibles y jerarquía visual propia. */
    .usq-hero {
        margin: 14px 0 18px; padding: 22px; border-radius: 22px;
        color: #fff; background: linear-gradient(135deg, #063b98 0%, #0758cf 58%, #0c77dd 100%);
        box-shadow: 0 14px 30px rgba(7, 74, 172, .22); position: relative; overflow: hidden;
    }
    .usq-hero::after { content: "✈"; position: absolute; right: 20px; top: 13px; font-size: 4.5rem; opacity: .12; transform: rotate(-12deg); }
    .usq-eyebrow { color: #bfdbfe; font-size: .68rem; font-weight: 800; letter-spacing: .1em; }
    .usq-hero h2 { color: #fff !important; margin: 5px 0 5px !important; font-size: clamp(1.35rem, 3.4vw, 1.85rem) !important; }
    .usq-hero h2 span { color: #dbeafe; }
    .usq-hero p { margin: 0 0 13px; color: #e0edff; font-size: .88rem; max-width: 590px; }
    .usq-chip { display: inline-block; border: 1px solid rgba(255,255,255,.32); border-radius: 999px; padding: 6px 10px; background: rgba(1, 24, 75, .22); font-size: .76rem; }
    .usq-section-title { display: flex; gap: 8px; align-items: center; margin: 22px 0 9px; color: #122a55; font-size: 1rem; font-weight: 800; }
    .usq-section-title span { display: inline-flex; width: 23px; height: 23px; align-items: center; justify-content: center; border-radius: 50%; color: #fff; background: #0d5bd7; font-size: .72rem; }
    .usq-smart-head { display: flex; align-items: center; gap: 11px; padding: 2px 0 8px; color: #103a7d; }
    .usq-smart-head b { display: block; font-size: 1rem; }
    .usq-smart-head small { display: block; margin-top: 3px; color: #60708b; line-height: 1.35; }
    .usq-smart-icon { display: grid; place-items: center; width: 40px; height: 40px; flex: 0 0 40px; border-radius: 13px; background: #e3efff; font-size: 1.2rem; }
    .usq-preview-empty { min-height: 72px; display: flex; align-items: center; justify-content: center; gap: 11px; margin: 2px 0 14px; border: 1px dashed #9fb8df; border-radius: 16px; background: #f6f9ff; color: #657694; text-align: left; font-size: .8rem; }
    .usq-preview-empty b { color: #27466f; }
    .usq-empty-list { padding: 20px; border: 1px dashed #a6b8d6; border-radius: 16px; background: #f8fbff; color: #4d6488; text-align: center; }
    .usq-mode, .usq-freight { display: inline-block; margin: 5px 5px 0 0; padding: 3px 8px; border-radius: 999px; font-size: .69rem; font-weight: 800; }
    .usq-mode { background: #e8f3ff; color: #0a4ca7; }
    .usq-freight { background: #e7f8ee; color: #08723b; }
    @media (min-width: 768px) {
        :root { --app-max-width: 860px; }
        .usq-hero { padding: 26px 28px; }
    }
    @media (max-width: 480px) {
        .usq-hero { margin-left: -2px; margin-right: -2px; padding: 18px; border-radius: 18px; }
        .usq-hero::after { font-size: 3.4rem; }
        .usq-section-title { margin-top: 18px; }
    }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# 7. PANTALLA DE ACCESO PÚBLICA (LOGIN / REGISTRO / RECUPERACIÓN)
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    if st.session_state["vista_actual"] == "login":
        with st.container(key="login_header"):
            st.markdown(
                html_encabezado_institucional(
                    '<div class="app-greeting-sub">Consolidación Marítima China ➔ Honduras</div>',
                    extra_class="app-header-login",
                    extra_style="margin-bottom: 2rem; border-radius: 16px;",
                ),
                unsafe_allow_html=True,
            )

        st.markdown("#### 🔐 Iniciar Sesión en su Casillero")
        u_ident = st.text_input(
            "Casillero, DNI o correo",
            placeholder="Ej: CCM-13011998 o correo@gmail.com",
            key="log_cas",
        )
        u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña", key="log_pwd")

        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        if st.button("➔ Ingresar a mi Casillero", type="primary", key="btn_login_submit"):
            u_ident = (st.session_state.get("log_cas") or u_ident or "").strip()
            u_pass = st.session_state.get("log_pwd") or u_pass or ""
            if u_ident and u_pass:
                u_correo = normalizar_correo(u_ident)
                claves = coincidencias_casillero(u_ident)
                placeholders = ",".join("?" * len(claves))
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        f"""
                        SELECT id, codigo_casillero, nombre_completo, correo_principal, rol, activo, telefono_principal, ciudad, password_hash
                        FROM usuarios
                        WHERE LOWER(TRIM(correo_principal)) = ? OR dni = ? OR codigo_casillero IN ({placeholders})
                        """,
                        (u_correo, u_ident, *claves),
                    )
                    user = c.fetchone()

                if user and verificar_pwd(u_pass, user[8]):
                    # Al entrar correctamente se actualiza de forma transparente
                    # cualquier hash SHA-256 legado a scrypt con sal.
                    if not str(user[8] or "").startswith("scrypt$"):
                        with get_db() as conn:
                            conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(u_pass), user[0]))
                    if user[5] == 0:
                        st.error("⛔ Cuenta inactiva. Contacte al soporte.")
                    else:
                        st.session_state["autenticado"] = True
                        st.session_state["rol"] = normalizar_rol(user[4])
                        perfil_login = cargar_perfil_usuario(user[1])
                        if perfil_login:
                            aplicar_perfil_en_sesion(perfil_login)
                        else:
                            st.session_state["casillero"] = formatear_casillero(user[1])
                            st.session_state["nombre"] = user[2]
                            st.session_state["usuario"] = user[3]
                            st.session_state["telefono"] = user[6]
                            st.session_state["ciudad"] = user[7]
                        st.session_state.pop("datos_pdf_confirmado", None)
                        st.session_state["china_modulos_desbloqueados"] = False
                        st.session_state["sub_tab_inicio"] = "Inicio"
                        st.session_state["hub"] = None
                        hidratar_cotizaciones_sesion(formatear_casillero(user[1]))

                        st.query_params["casillero"] = formatear_casillero(user[1])
                        st.query_params["vista"] = "Inicio"
                        st.rerun()
                else:
                    st.error("❌ Credenciales inválidas.")
            else:
                st.warning("Complete todos los campos.")

        c_b1, c_b2 = st.columns(2)
        with c_b1:
            if st.button("Recuperar Clave", type="secondary"):
                st.session_state["vista_actual"] = "recuperar"
                st.rerun()
        with c_b2:
            if st.button("Crear Casillero", type="secondary"):
                st.session_state["vista_actual"] = "registro"
                st.rerun()

    elif st.session_state["vista_actual"] == "registro":
        st.markdown("### 📋 Apertura de Casillero en China")
        if st.session_state.get("reg_exito"):
            creado = st.session_state["reg_exito"]
            nombre_creado = html.escape(str(creado.get("nombre") or ""))
            correo_creado = html.escape(str(creado.get("correo") or ""))
            casillero_creado = html.escape(str(creado.get("casillero") or ""))
            clave_creada = html.escape(str(creado.get("password") or ""))
            st.markdown(
                f"""
                <div class="reg-confirm-card">
                    <h4>🎉 Casillero y correo confirmados</h4>
                    <div>Guarde estos datos para iniciar sesión:</div>
                    <div>👤 {nombre_creado}</div>
                    <div>📧 Correo: <b>{correo_creado}</b></div>
                    <div>🔑 Casillero: <b>{casillero_creado}</b></div>
                    <div>🔒 Contraseña: <b>{clave_creada}</b></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("Ir al inicio de sesión", type="primary"):
                st.session_state["reg_exito"] = None
                st.session_state["reg_paso"] = 1
                st.session_state["reg_datos"] = {}
                st.session_state["vista_actual"] = "login"
                st.rerun()
            if st.button("Volver al Login", type="secondary"):
                st.session_state["reg_exito"] = None
                st.session_state["vista_actual"] = "login"
                st.rerun()
            st.stop()

        paso = st.session_state["reg_paso"]
        st.progress(paso / 4.0, text=f"Paso {paso} de 4")

        if paso == 1:
            nom = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nom", ""))
            dni = st.text_input(
                "Número de Identidad (DNI - 13 dígitos) *",
                value=st.session_state["reg_datos"].get("dni", ""),
                placeholder="Ej: 1301199800990",
            )
            if dni:
                st.caption(f"ℹ️ Su casillero asignado será: **{generar_codigo_casillero_dni(dni)}**")
            if st.button("Siguiente ➔", type="primary"):
                if nom and dni and len("".join(filter(str.isdigit, dni))) >= 8:
                    st.session_state["reg_datos"].update({"nom": nom, "dni": dni})
                    st.session_state["reg_paso"] = 2
                    st.rerun()
                else:
                    st.error("Ingrese un DNI válido.")

        elif paso == 2:
            cor = st.text_input("Correo Electrónico *", value=st.session_state["reg_datos"].get("cor", ""))
            tel = st.text_input("Teléfono / WhatsApp *", value=st.session_state["reg_datos"].get("tel", ""))
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Atrás", type="secondary"):
                    st.session_state["reg_paso"] = 1
                    st.rerun()
            with c2:
                if st.button("Siguiente ➔", type="primary"):
                    if cor and tel:
                        st.session_state["reg_datos"].update({"cor": cor, "tel": tel})
                        st.session_state["reg_paso"] = 3
                        st.rerun()
                    else:
                        st.error("Complete correo y teléfono.")

        elif paso == 3:
            dep_reg = st.selectbox(
                "Departamento *",
                list(MUNICIPIOS_HONDURAS.keys()),
                index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0,
                key="sb_dep_reg",
            )
            ciu_reg = st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_reg], key="sb_ciu_reg")
            dir_e = st.text_area("Dirección Exacta de Entrega *", value=st.session_state["reg_datos"].get("dir", ""))
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Atrás", type="secondary"):
                    st.session_state["reg_paso"] = 2
                    st.rerun()
            with c2:
                if st.button("Siguiente ➔", type="primary"):
                    if ciu_reg and dir_e:
                        st.session_state["reg_datos"].update({"dep": dep_reg, "ciu": ciu_reg, "dir": dir_e})
                        st.session_state["reg_paso"] = 4
                        st.rerun()
                    else:
                        st.error("Complete la dirección.")

        elif paso == 4:
            rub = st.selectbox(
                "Rubro Principal",
                ["Ferretería & Construcción", "Cerámica & Acabados", "Electrónica", "Ropa & Calzado", "Repuestos", "General"],
            )
            with st.container(key="reg_modalidad"):
                mod = st.radio(
                    "Modalidad de Entrega",
                    [
                        "Retiro en Bodega Central (San Juan, Intibucá)",
                        "Envío con Forza a Domicilio",
                    ],
                    captions=[
                        "Recoge su carga en el almacén principal",
                        "Entrega a domicilio con mensajería Forza",
                    ],
                )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Atrás", type="secondary"):
                    st.session_state["reg_paso"] = 3
                    st.rerun()
            with c2:
                if st.button("🚀 Confirmar y Crear", type="primary"):
                    d = st.session_state["reg_datos"]
                    n_cod = generar_codigo_casillero_dni(d["dni"])
                    n_pwd = generar_clave_provisional()
                    f_crea = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")

                    correo_registrado = normalizar_correo(d.get("cor"))
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT codigo_casillero, correo_principal, dni FROM usuarios "
                            "WHERE LOWER(TRIM(correo_principal)) = ? OR dni = ? OR codigo_casillero IN ({})".format(
                                ",".join("?" * len(coincidencias_casillero(n_cod)))
                            ),
                            (correo_registrado, d["dni"], *coincidencias_casillero(n_cod)),
                        )
                        existente = cur.fetchone()
                        if existente:
                            _, correo_existente, dni_existente = existente
                            if normalizar_correo(correo_existente) == correo_registrado:
                                detalle_existente = "Este correo ya tiene un casillero registrado. Use Recuperar Clave para entrar."
                            elif str(dni_existente or "").strip() == str(d["dni"] or "").strip():
                                detalle_existente = "Este DNI ya está asociado a otro correo. Verifique el correo registrado o contacte a soporte."
                            else:
                                detalle_existente = "El código de casillero ya existe. Contacte a soporte."
                            url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(
                                "Hola, necesito asistencia con mi casillero ya registrado."
                            )
                            st.markdown(
                                f'<div class="reg-warn-card">⚠️ {html.escape(detalle_existente)}</div>',
                                unsafe_allow_html=True,
                            )
                            st.markdown(
                                f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:10px; border-radius:8px; width:100%; font-weight:bold; cursor:pointer;">📲 Consultar por WhatsApp (+504 9577-1099)</button></a>',
                                unsafe_allow_html=True,
                            )
                        else:
                            cur.execute(
                                "INSERT INTO usuarios (codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', TRUE, ?)",
                                (
                                    n_cod,
                                    d["nom"],
                                    d["dni"],
                                    correo_registrado,
                                    d["tel"],
                                    d["dep"],
                                    d["ciu"],
                                    d["dir"],
                                    rub,
                                    mod,
                                    hash_pwd(n_pwd),
                                    f_crea,
                                ),
                            )
                            permisos_cliente = permisos_default("cliente")
                            cur.execute(
                                """
                                INSERT INTO permisos_usuario (
                                    codigo_casillero, hub_china, hub_eeuu, hub_honduras,
                                    mod_cotizador, mod_catalogo, mod_cotizaciones, mod_envios, mod_fichas
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(codigo_casillero) DO NOTHING
                                """,
                                (
                                    n_cod,
                                    bool(permisos_cliente["hub_china"]),
                                    bool(permisos_cliente["hub_eeuu"]),
                                    bool(permisos_cliente["hub_honduras"]),
                                    bool(permisos_cliente["mod_cotizador"]),
                                    bool(permisos_cliente["mod_catalogo"]),
                                    bool(permisos_cliente["mod_cotizaciones"]),
                                    bool(permisos_cliente["mod_envios"]),
                                    bool(permisos_cliente["mod_fichas"]),
                                ),
                            )
                            conn.commit()
                            st.session_state["reg_exito"] = {
                                "nombre": d["nom"],
                                "correo": correo_registrado,
                                "casillero": n_cod,
                                "password": n_pwd,
                            }
                            st.session_state["reg_paso"] = 1
                            st.session_state["reg_datos"] = {}
                            st.rerun()

        if st.button("Volver al Login", type="secondary"):
            st.session_state["vista_actual"] = "login"
            st.rerun()

    elif st.session_state["vista_actual"] == "recuperar":
        st.markdown("### 🔄 Restablecer Contraseña")
        r_mail = st.text_input("Correo Registrado")
        r_dni = st.text_input("o Número de Identidad (DNI)")
        if st.button("Generar Nueva Contraseña", type="primary"):
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT id FROM usuarios WHERE LOWER(TRIM(correo_principal)) = ? OR dni = ?",
                    (normalizar_correo(r_mail), str(r_dni or "").strip()),
                )
                u = c.fetchone()
            if u:
                nueva_p = generar_clave_provisional()
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(nueva_p), u[0]))
                st.success(f"✅ Nueva clave: **{nueva_p}**")
            else:
                st.error("No se encontró una cuenta con ese correo o DNI.")
        if st.button("Volver al Login", type="secondary"):
            st.session_state["vista_actual"] = "login"
            st.rerun()

# ---------------------------------------------------------
# 8. PORTAL DEL CLIENTE
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    if st.session_state.get("hub") and not usuario_puede_hub(st.session_state["hub"]):
        st.session_state["hub"] = None
        st.session_state["sub_tab_inicio"] = "Inicio"
        st.session_state["vista_activa"] = "Inicio"
        st.query_params["vista"] = "Inicio"
        if "hub" in st.query_params:
            del st.query_params["hub"]
        st.rerun()
    if st.session_state.get("sub_tab_inicio") in VISTAS_MODULO and not usuario_puede_modulo(
        st.session_state["sub_tab_inicio"]
    ):
        st.session_state["sub_tab_inicio"] = "Inicio"
        st.session_state["vista_activa"] = "Inicio"
        st.session_state["hub"] = None
        st.query_params["vista"] = "Inicio"
        st.rerun()

    st.session_state.pop("_ccm_rerun_app", None)
    casillero = formatear_casillero(st.session_state["casillero"])
    if casillero != st.session_state["casillero"]:
        st.session_state["casillero"] = casillero
    ahora_hn = obtener_tiempo_honduras()
    # La purga ya se ejecutó al arrancar y se limita a intervalos para que
    # navegar o pulsar botones no dispare una operación completa en la BD.
    purgar_cotizaciones_si_corresponde(ahora_hn)
    _limpiar_cotizacion_vencida_en_sesion(ahora_hn)
    nombre_completo = str(st.session_state.get("nombre") or "Cliente")
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "San Juan, Intibucá")

    partes_nombre = nombre_completo.strip().split()
    if len(partes_nombre) >= 2:
        nombre_display = f"{partes_nombre[0]} {partes_nombre[1]}"
    elif len(partes_nombre) == 1:
        nombre_display = partes_nombre[0]
    else:
        nombre_display = "Cliente"

    hora_actual = ahora_hn.hour
    if 5 <= hora_actual < 12:
        saludo_horario = "Buenos días"
    elif 12 <= hora_actual < 19:
        saludo_horario = "Buenas tardes"
    else:
        saludo_horario = "Buenas noches"

    dia_nombre = DIAS_SEMANA_ES.get(ahora_hn.weekday(), "")
    mes_nombre = MESES_ES.get(ahora_hn.month, "")
    hora_formato = ahora_hn.strftime("%I:%M %p")
    fecha_hora_texto = f"{dia_nombre}, {ahora_hn.day} {mes_nombre} {ahora_hn.year} &bull; {hora_formato}"

    lista_todas_cotizaciones, lista_mis_cotizaciones, confirmaciones_cotizaciones = filas_cotizaciones_casillero(casillero, ahora_hn)
    total_cotizaciones = len(lista_mis_cotizaciones)
    direcciones_guardadas = direcciones_sesion(casillero)
    opciones_modalidad = opciones_entrega_desde_sesion(casillero, direcciones_guardadas)
    crear_nueva_dir = "➕ Crear Nueva Dirección de Envío"
    mod_actual = st.session_state.get("modalidad_envio_seleccionada")
    previa_destino = st.session_state.get("destino_entrega_activo")
    if mod_actual != crear_nueva_dir:
        if mod_actual in opciones_modalidad:
            st.session_state["destino_entrega_activo"] = mod_actual
        elif previa_destino in opciones_modalidad:
            st.session_state["modalidad_envio_seleccionada"] = previa_destino
        else:
            st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
            st.session_state["destino_entrega_activo"] = OPCION_PREDETERMINADA

    with st.container(key="sticky_top_header"):
        st.markdown(
            html_encabezado_institucional(
                f'<div class="app-greeting-title">{saludo_horario}, {nombre_display}</div>'
                f'<div class="app-greeting-sub"><span class="app-header-casillero">Casillero: <b>{casillero}</b></span><span class="app-header-sep"> &bull; </span><span class="app-header-cots">{total_cotizaciones} Cotizaciones</span></div>'
                f'<div class="app-header-time">🕒 {fecha_hora_texto}</div>'
            ),
            unsafe_allow_html=True,
        )

    sincronizar_altura_encabezado_fijo()
    detectar_avance_descarga_guia()
    aplicar_clase_guia_js()

    if st.session_state["sub_tab_inicio"] == "Inicio":
        hub_sel = st.session_state.get("hub")
        with st.container(key="vista_inicio"):
            if not hub_sel:
                st.markdown("#### 🏠 Inicio")
                st.caption("Seleccione el origen de su carga para ver los módulos disponibles.")
                visibles_hub = [hid for hid in HUBS if usuario_puede_hub(hid)]
                if not visibles_hub:
                    st.info("Su cuenta no tiene hubs habilitados. Contacte al administrador.")
                for hub_id, hub in HUBS.items():
                    if not usuario_puede_hub(hub_id):
                        continue
                    if st.button(
                        f"{hub['icon']}  {hub['label']}",
                        type="secondary",
                        key=f"hub_{hub_id}",
                        use_container_width=True,
                        on_click=ir_a,
                        args=("Inicio", hub_id),
                    ):
                        pass
                st.markdown(
                    """
                    <section class="ccm-versiculo-banner" aria-label="Versículo bíblico inspirador">
                        <div class="ccm-versiculo-texto">“ENCOMIENDA A JEHOVÁ TU CAMINO, CONFÍA EN ÉL; Y ÉL HARÁ.”</div>
                        <span class="ccm-versiculo-referencia">SALMOS 37:5</span>
                    </section>
                    """,
                    unsafe_allow_html=True,
                )
            elif hub_sel == "china":
                pintar_coach_guia()
                hub_china = HUBS["china"]
                st.markdown(f"#### {hub_china['icon']} {hub_china['label']}")
                st.caption("Consolidación marítima China ➔ Honduras")
                pintar_banner_promocional_china(casillero)
            elif hub_sel == "eeuu":
                # El área de EE. UU. queda intencionalmente limpia hasta que
                # se defina su próximo flujo operativo.
                hub_eeuu = HUBS["eeuu"]
                st.markdown(f"#### {hub_eeuu['icon']} {hub_eeuu['label']}")
                st.markdown(
                    f'<div class="hub-empty-box">'
                    f'<div style="font-size:2rem;margin-bottom:8px;">{hub_eeuu["icon"]}</div>'
                    f'<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">EE. UU.</div>'
                    f'<div style="font-size:0.86rem;font-weight:600;">Módulo en preparación para envíos desde Estados Unidos</div>'
                    f'<div style="font-size:0.78rem;margin-top:10px;color:#94a3b8;">Esta área está reservada para integrar nuevas funciones en una fase posterior.</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif hub_sel in HUBS:
                hub_vacio = HUBS[hub_sel]
                st.markdown(f"#### {hub_vacio['icon']} {hub_vacio['label']}")
                st.markdown(
                    f'<div class="hub-empty-box">'
                    f'<div style="font-size:2rem;margin-bottom:8px;">{hub_vacio["icon"]}</div>'
                    f'<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">{hub_vacio["label"]}</div>'
                    f'<div style="font-size:0.86rem;font-weight:600;">{hub_vacio["descripcion"]}</div>'
                    f'<div style="font-size:0.78rem;margin-top:10px;color:#94a3b8;">Espacio reservado para integrar funciones en una fase posterior.</div>'
                    f'<div style="font-size:0.78rem;margin-top:8px;color:#64748b;">Pulse <b>Guía</b> en el menú para el recorrido interactivo China → Honduras.</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

    if st.session_state["sub_tab_inicio"] == "Actividad":
        pintar_vista_actividad(total_cotizaciones)

    if st.session_state["sub_tab_inicio"] == "Más":
        pintar_vista_mas()

    if st.session_state["sub_tab_inicio"] == "Consultas":
        cas_txt = formatear_casillero(casillero)
        msg_wa = urllib.parse.quote(
            f"Hola Centro de Cerámicas y Más, tengo una consulta de mi casillero {cas_txt}."
        )
        st.markdown("#### 🔍 Consultas")
        st.caption("Pregunte a CCM por WhatsApp o busque productos para importar.")
        st.link_button("💬 Preguntar por WhatsApp", f"https://wa.me/50495771099?text={msg_wa}", use_container_width=True)
        c_q1, c_q2 = st.columns(2)
        with c_q1:
            st.button("📖 Catálogo 1688", key="btn_consultas_1688", use_container_width=True, on_click=ir_a_catalogo)
        with c_q2:
            st.button("🇺🇸 Cotizador EE. UU.", key="btn_consultas_eeuu", use_container_width=True, on_click=ir_a, args=("Inicio", "eeuu"))

    if st.session_state["sub_tab_inicio"] == "Configuración":
        st.markdown("#### ⚙️ Configuración")
        st.caption("Datos de su casillero. La sesión se conserva al cambiar de vista.")
        st.info(
            f"**Casillero:** `{casillero}`  \n"
            f"**Nombre:** {st.session_state.get('nombre') or '—'}  \n"
            f"**Correo:** {st.session_state.get('usuario') or '—'}  \n"
            f"**Entrega:** {destino_para_documentos()}"
        )
        st.link_button(
            "💬 Actualizar datos por WhatsApp",
            "https://wa.me/50495771099?text=" + urllib.parse.quote(
                f"Hola, necesito actualizar los datos de mi casillero {casillero}."
            ),
            use_container_width=True,
        )

    if st.session_state["sub_tab_inicio"] == "Mis Cotizaciones":
        with st.container(key="vista_historial"):
            st.markdown('<div class="ccm-vista-historial" aria-hidden="true"></div>', unsafe_allow_html=True)
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📄 Historial de Cotizaciones y Descarga de PDF")
            pintar_guias_informativas(
                [
                    ("⏳", "Tarifa pendiente", "Confírmela antes de que venza su vigencia de <b>1 hora</b>.", "naranja"),
                    ("🛡️", "Tarifa confirmada", "Permanece disponible durante <b>48 horas</b>.", "azul"),
                    ("📦", "Seguimiento y documentos", "El seguimiento y los PDFs están disponibles en <b>Mis Envíos</b>.", "verde"),
                ]
            )
            # Descarta filtros de versiones anteriores: la guía ya no es interactiva.
            st.session_state.pop("filtro_historial_cotizaciones", None)
            confirmada_flash = st.session_state.pop("flash_cotizacion_confirmada", None)
            error_confirmacion = st.session_state.pop("flash_error_confirmacion", None)
            if confirmada_flash:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,#ecfdf5,#dcfce7);border-left:5px solid #22c55e;'
                    f'border-radius:12px;padding:14px 16px;margin:10px 0 14px;color:#166534;">'
                    f'<div style="font-weight:800;font-size:1rem;">✅ CCM-COT-{int(confirmada_flash):05d} confirmada correctamente</div>'
                    f'<div style="margin-top:4px;font-size:.9rem;">Sus documentos oficiales ya están disponibles. '
                    f'<b>Pulse “📦 Ir a Envíos”</b> en la tarjeta para abrir el seguimiento.</div></div>',
                    unsafe_allow_html=True,
                )
            elif error_confirmacion:
                st.error(error_confirmacion)

            if lista_mis_cotizaciones:
                try:
                    foco_hist = int(st.session_state.get("cotizacion_historial_foco") or 0)
                except (TypeError, ValueError):
                    foco_hist = 0
                if foco_hist:
                    lista_mis_cotizaciones = sorted(
                        lista_mis_cotizaciones,
                        key=lambda r: (
                            0 if int(r[0]) == foco_hist else 1,
                            *clave_orden_cotizacion(r[7], r[0]),
                        ),
                    )
                cotizaciones_render, total_historial, limite_historial = pagina_registros(
                    lista_mis_cotizaciones, "limite_historial_cotizaciones"
                )
                scroll_pendiente_hecho = False
                for cot in cotizaciones_render:
                    id_cot_item, al_c, an_c, la_c, pe_lb_c, vol_m3_c, tot_c, fec_c, conf_c = cot
                    consolidada = es_cotizacion_confirmada(conf_c)
                    fecha_confirmacion = confirmaciones_cotizaciones.get(int(id_cot_item))
                    estado_txt = texto_estado_cotizacion(fec_c, conf_c, ahora_hn, fecha_confirmacion)
                    color_estado = "#1d4ed8" if consolidada else "#166534"
                    icono_estado = "✅" if consolidada else "⏳"
                    es_foco_hist = bool(foco_hist and int(id_cot_item) == foco_hist)
                    pendiente_foco = es_foco_hist and not consolidada
                    clase_foco = "cotizacion-pendiente-foco" if pendiente_foco else ""
                    clase_pendiente = "cotizacion-pendiente-caja" if not consolidada else ""
                    id_ancla = 'id="cotizacion-foco-pendiente"' if pendiente_foco else f'id="cotizacion-ccm-{id_cot_item}"'
                    insignia = (
                        '<span class="cotizacion-badge-pendiente">⚠️ Pendiente de Confirmar</span>'
                        if not consolidada
                        else '<span style="display:inline-flex;background:#dcfce7;color:#166534;border:1px solid #86efac;border-radius:999px;padding:3px 8px;font-size:.78rem;font-weight:800;">✅ Confirmada · 48 h</span>'
                    )
                    with st.container(key=f"tarjeta_cot_{id_cot_item}"):
                        with st.container(key=f"tarjeta_cot_info_{id_cot_item}"):
                            st.markdown(
                                f'<div {id_ancla} class="cot-card-body {clase_pendiente} {clase_foco}">'
                                f'<div class="cot-card-head">'
                                f'<span class="cot-card-id">🔖 CCM-COT-{id_cot_item:05d} • Fecha: {formatear_fecha_pantalla(fec_c)}</span>'
                                f"{insignia}"
                                f"</div>"
                                f'<div class="cot-card-meta">📐 Medidas: {al_c:.1f}x{an_c:.1f}x{la_c:.1f} cm | Peso: {pe_lb_c:.1f} lbs | 💰 Total: <b>${tot_c:.2f} USD</b></div>'
                                f'<div class="cot-card-vigencia" style="color:{color_estado};">{icono_estado} {estado_txt}</div>'
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                        pdf_historial = generar_pdf_confirmacion_cotizacion(
                            casillero=casillero,
                            nombre=nombre_completo,
                            telefono=tel_cli,
                            ciudad=ciu_cli,
                            tipo_carga="Cotización Histórica",
                            al=al_c,
                            an=an_c,
                            la=la_c,
                            peso_lb=pe_lb_c,
                            peso_kg=pe_lb_c / 2.20462,
                            vol_m3=vol_m3_c,
                            vol_ft3=vol_m3_c * 35.3147,
                            total_usd=tot_c,
                            detalle_tarifa="Tarifa Calculada Sistema CCM",
                            id_cot=id_cot_item,
                            destino_entrega=destino_para_documentos(),
                            fecha_emision=fec_c,
                        )
                        if consolidada:
                            es_foco_envios_guia = bool(
                                guia_esta_activa()
                                and guia_paso_actual() == 6
                                and int(st.session_state.get("cotizacion_envio_foco") or 0) == int(id_cot_item)
                            )
                            env_ctx = (
                                st.container(key=f"foco_ir_envios_{id_cot_item}")
                                if es_foco_envios_guia
                                else st.container()
                            )
                            with env_ctx:
                                st.button(
                                    "📦 Ir a Envíos",
                                    type="primary",
                                    key=f"btn_ir_envios_{id_cot_item}",
                                    use_container_width=True,
                                    on_click=ir_a_envios_de_cotizacion,
                                    args=(id_cot_item,),
                                )
                            st.download_button(
                                f"📥 Descargar PDF CCM-COT-{id_cot_item:05d}",
                                pdf_historial,
                                f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                                "application/pdf",
                                key=f"dl_cot_{id_cot_item}",
                                use_container_width=True,
                            )
                        else:
                            confirmar_ctx = (
                                st.container(key=f"foco_confirmar_{id_cot_item}")
                                if pendiente_foco
                                else st.container()
                            )
                            with confirmar_ctx:
                                st.button(
                                    "Confirmar Cotización",
                                    type="primary",
                                    key=f"btn_confirmar_cot_{id_cot_item}",
                                    use_container_width=True,
                                    on_click=on_confirmar_cot_historial,
                                    args=(id_cot_item, casillero),
                                )
                            st.download_button(
                                f"📥 PDF CCM-COT-{id_cot_item:05d}",
                                pdf_historial,
                                f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                                "application/pdf",
                                key=f"dl_cot_{id_cot_item}",
                                use_container_width=True,
                            )
                        if pendiente_foco and not scroll_pendiente_hecho:
                            desplazar_a_cotizacion_pendiente()
                            scroll_pendiente_hecho = True
                if limite_historial < total_historial:
                    st.button(
                        f"Mostrar 10 cotizaciones más ({limite_historial} de {total_historial})",
                        key="btn_mas_historial",
                        use_container_width=True,
                        on_click=aumentar_limite_registros,
                        args=("limite_historial_cotizaciones",),
                    )
            else:
                st.info(
                    "No hay cotizaciones vigentes ni consolidadas. Emita una tarifa en el Cotizador; "
                    "tiene 1 hora para confirmarla y habilitar Envíos."
                )
            espaciador_barra_inferior("safe_historial")

    if st.session_state["sub_tab_inicio"] == "Cotizador" and st.session_state.get("mostrar_gestion_direcciones"):
        # Esta vista puede completar la selección durante el mismo rerun; usa
        # una clave distinta para no colisionar con el Cotizador principal.
        with st.container(key="vista_direcciones"):
            with st.container(key="formulario_direcciones"):
                st.markdown("#### 📍 Administrar Direcciones de Envío")

                st.markdown(
                    f"""
                <div style="background:linear-gradient(135deg,#eff6ff,#f8fbff);border:1px solid #bfdbfe;border-left:5px solid #0757c8;border-radius:14px;padding:15px 16px;margin:8px 0 18px;box-shadow:0 5px 14px rgba(7,87,200,.10);">
                    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                        <span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:10px;background:#0757c8;color:#fff;font-size:1.15rem;">🏬</span>
                        <div style="flex:1;min-width:190px;">
                            <div style="color:#0f172a;font-size:.98rem;font-weight:800;line-height:1.25;">Retiro en Almacén Principal</div>
                            <div style="color:#475569;font-size:.82rem;margin-top:2px;">San Juan, Intibucá · Centro de Cerámicas y Más</div>
                        </div>
                        <span style="background:#0757c8;color:#fff;font-size:.72rem;padding:5px 10px;border-radius:999px;font-weight:800;white-space:nowrap;">⭐ DESTINO PREDETERMINADO</span>
                    </div>
                    <div style="margin-top:12px;padding-top:10px;border-top:1px solid #dbeafe;color:#34506f;font-size:.82rem;">
                        📍 Esta es la <b>Bodega Principal</b>. Puedes usarla o elegir una dirección personalizada; el destino elegido se imprimirá en todos los documentos. <span style="color:#64748b;">No se puede eliminar.</span>
                    </div>
                </div>
                """,
                    unsafe_allow_html=True,
                )

                if direcciones_guardadas:
                    st.markdown(
                        "<p style='font-weight:700; font-size:0.88rem; margin:10px 0 6px 0;'>Tus direcciones personalizadas:</p>",
                        unsafe_allow_html=True,
                    )
                    for idx_dir, dir_item in enumerate(direcciones_guardadas):
                        etiq = dir_item.get("etiqueta", "")
                        rec = dir_item.get("receptor", "")
                        ciu_d = dir_item.get("ciudad", "")
                        dir_e = dir_item.get("direccion", "")
                        id_dir = dir_item.get("id")
                        col_info_d, col_btn_usar, col_btn_del = st.columns([3.35, 1.15, 0.45])
                        with col_info_d:
                            st.markdown(
                                f"""
                            <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:8px; padding:8px 12px; font-size:0.85rem;">
                                <b>🏷️ {etiq}</b> &bull; Recibe: {rec}<br>
                                <small style="color:#64748b;">📍 {ciu_d} &bull; {dir_e}</small>
                            </div>
                            """,
                                unsafe_allow_html=True,
                            )
                        with col_btn_usar:
                            opcion_dir = f"📍 {etiq} - {ciu_d}"
                            st.button(
                                "✓ Usar para cotizar",
                                key=f"usar_dir_{id_dir or f'ses_{idx_dir}'}",
                                type="primary",
                                use_container_width=True,
                                on_click=usar_direccion_y_cotizar,
                                args=(opcion_dir,),
                            )
                        with col_btn_del:
                            # Solo las direcciones personalizadas tienen eliminación.
                            # La Bodega Principal se muestra fuera de este ciclo y
                            # por eso permanece siempre protegida.
                            if st.button(
                                "🗑️",
                                key=f"del_dir_{id_dir or f'ses_{idx_dir}'}",
                                type="secondary",
                                help="Eliminar esta dirección",
                                use_container_width=True,
                            ):
                                eliminar_direccion_usuario(casillero, etiq, ciu_d, id_dir)
                                st.session_state.pop("datos_pdf_confirmado", None)
                                st.toast(f"🗑️ Dirección '{etiq}' eliminada.")
                                st.rerun()

                st.markdown("---")
                st.markdown("##### ➕ Agregar Nueva Dirección de Entrega")
                if st.session_state.pop("_dir_form_reset", None):
                    for _campo_dir in CAMPOS_FORM_DIRECCION:
                        st.session_state.pop(_campo_dir, None)
                if "dir_receptor_in" not in st.session_state:
                    st.session_state["dir_receptor_in"] = nombre_completo
                if "dir_tel_in" not in st.session_state:
                    st.session_state["dir_tel_in"] = tel_cli
                st.text_input(
                    "Etiqueta de la dirección *",
                    key="dir_etiqueta_in",
                    placeholder="Ej: Mi Casa, Sucursal 2, Taller",
                )
                st.text_input("Nombre de quien recibe *", key="dir_receptor_in")
                st.text_input("Teléfono de contacto *", key="dir_tel_in")
                dep_dir_in = st.selectbox(
                    "Departamento *",
                    list(MUNICIPIOS_HONDURAS.keys()),
                    index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0,
                    key="sb_dep_nueva_dir",
                )
                st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_dir_in], key="sb_ciu_nueva_dir")
                st.text_area(
                    "Dirección exacta y referencias *",
                    key="dir_exacta_in",
                    placeholder="Barrio, calle, número de casa, puntos clave...",
                )
                error_dir = st.session_state.pop("_dir_form_error", None)
                if error_dir:
                    st.error(error_dir)
                guardar_direccion_pulsado = st.button(
                    "💾 Guardar Dirección",
                    type="primary",
                    key="btn_guardar_nueva_dir",
                    use_container_width=True,
                )
                if guardar_direccion_pulsado:
                    if guardar_nueva_direccion(casillero):
                        st.rerun()
                    error_guardado = st.session_state.pop("_dir_form_error", None)
                    if error_guardado:
                        st.error(error_guardado)
                st.button(
                    "Cancelar",
                    type="secondary",
                    key="btn_cancelar_dir",
                    use_container_width=True,
                    on_click=cancelar_nueva_direccion,
                )

    if st.session_state["sub_tab_inicio"] == "Catálogo":
        with st.container(key="vista_catalogo"):
            st.markdown(
                """
                <div role="status" style="margin:42px 0;padding:38px 20px;text-align:center;
                background:#eff6ff;border:1px solid #bfdbfe;border-radius:16px;color:#1e3a8a;">
                    <div style="font-size:2.5rem;line-height:1;margin-bottom:14px;">🛍️</div>
                    <div style="font-size:1.35rem;font-weight:800;">Catálogo próximamente disponible</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            espaciador_barra_inferior("safe_catalogo")

    elif (
        st.session_state["sub_tab_inicio"] == "Cotizador"
        and not st.session_state.get("mostrar_gestion_direcciones")
    ):
        with st.container(key="vista_cotizador"):
            st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
            st.button(
                "📍 Administrar direcciones de envío",
                type="secondary",
                key="btn_abrir_gestion_direcciones",
                use_container_width=True,
                on_click=abrir_gestion_direcciones,
            )
            # El usuario puede cambiar de destino antes de emitir. Esta misma
            # selección alimenta las cotizaciones, fichas y documentos.
            opciones_destino = [op for op in opciones_modalidad if op != crear_nueva_dir]
            selector_modalidad_entrega(opciones_destino)
            destino_estampado = html.escape(destino_para_documentos())
            st.markdown(
                f"""
                <div class="destino-seleccionado-card">
                    <div class="destino-seleccionado-kicker">📍 Destino de Entrega Seleccionado</div>
                    <div class="destino-seleccionado-dir">{destino_estampado}</div>
                    <div class="destino-seleccionado-nota">(Se imprimirá en todos los formatos y fichas de bodega)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            # El destino elegido se conserva durante todo el flujo documental.
            st.session_state.pop("_dir_form_exito", None)
            error_db_dir = st.session_state.pop("_dir_db_error", None)
            if error_db_dir:
                st.warning(
                    "⚠️ La dirección quedó activa en esta sesión, pero no pudo grabarse en la base de datos "
                    f"y se perderá al recargar. Detalle técnico: {error_db_dir}"
                )

            t_lb = get_tarifa("tarifa_libra")
            t_m3 = get_tarifa("tarifa_m3")
            min_usd = get_tarifa("minimo_cobro_usd")
            umbral_min = float(get_tarifa("umbral_minimo_lb") or 3.0)
            umbral_paq = float(get_tarifa("umbral_paqueteria_lb") or 99.0)
            divisor_vol = float(get_tarifa("divisor_peso_volumetrico") or 390.0)

            tipo_opts = [
                f"📦 Paquetería Menor (1 a {umbral_paq:.0f} lbs)",
                "🚢 Carga Comercial por CBM (hasta contenedor 40')",
            ]
            tipo_kwargs = {"key": "sb_tipo_carga_select", "on_change": invalidar_emision_visible_cotizador}
            if "sb_tipo_carga_select" not in st.session_state:
                tipo_kwargs["index"] = 0
            tipo_carga = st.selectbox("Modalidad de Importación:", tipo_opts, **tipo_kwargs)

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

            c_u1, c_u2 = st.columns(2)
            with c_u1:
                unidad_medida = st.selectbox(
                    "Unidad de Medida:",
                    ["Centímetros (cm)", "Pulgadas (in)", "Metros (m)"],
                    key="sb_unidad_medida",
                    on_change=invalidar_emision_visible_cotizador,
                )
            with c_u2:
                unidad_peso = st.selectbox(
                    "Unidad de Peso:",
                    ["Libras (lb)", "Kilogramos (kg)"],
                    key="sb_unidad_peso",
                    on_change=invalidar_emision_visible_cotizador,
                )

            es_paqueteria = "Paquetería Menor" in tipo_carga
            dim = limites_dimensiones(unidad_medida, comercial=not es_paqueteria)
            pes = limites_peso(unidad_peso, paqueteria=es_paqueteria)
            etiqueta_medida = unidad_medida.split()[1].strip("()")
            etiqueta_peso = unidad_peso.split()[1].strip("()")

            st.caption(
                f"Tope de medidas: contenedor 40' High Cube interno "
                f"({CONTENEDOR_40_ALTO_M:.2f} m alto × {CONTENEDOR_40_ANCHO_M:.2f} m ancho × {CONTENEDOR_40_LARGO_M:.2f} m largo). "
                f"Peso máximo legal en Honduras para un 40': {PESO_MAX_CONTENEDOR_HN_KG:,.0f} kg "
                f"({peso_max_contenedor_hn_lb():,.0f} lb)."
                + (f" En paquetería menor el peso no puede superar {umbral_paq:.0f} lb." if es_paqueteria else "")
            )

            st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)

            pref = "menor" if es_paqueteria else "com"
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                al_input = campo_numerico(
                    f"Alto ({etiqueta_medida})",
                    dim["min"],
                    dim["defaults"]["alto"],
                    dim["max"]["alto"],
                    dim["step"],
                    f"in_al_{pref}_{dim['codigo']}",
                    dim["formato"],
                    on_change=invalidar_emision_visible_cotizador,
                )
            with c2:
                an_input = campo_numerico(
                    f"Ancho ({etiqueta_medida})",
                    dim["min"],
                    dim["defaults"]["ancho"],
                    dim["max"]["ancho"],
                    dim["step"],
                    f"in_an_{pref}_{dim['codigo']}",
                    dim["formato"],
                    on_change=invalidar_emision_visible_cotizador,
                )
            with c3:
                la_input = campo_numerico(
                    f"Largo ({etiqueta_medida})",
                    dim["min"],
                    dim["defaults"]["largo"],
                    dim["max"]["largo"],
                    dim["step"],
                    f"in_la_{pref}_{dim['codigo']}",
                    dim["formato"],
                    on_change=invalidar_emision_visible_cotizador,
                )
            with c4:
                pe_input = campo_numerico(
                    f"Peso ({etiqueta_peso})",
                    pes["min"],
                    pes["default"],
                    pes["max"],
                    pes["step"],
                    f"in_pe_{pref}_{pes['codigo']}",
                    pes["formato"],
                    on_change=invalidar_emision_visible_cotizador,
                )

            if "Pulgadas" in unidad_medida:
                al_val = al_input * 2.54
                an_val = an_input * 2.54
                la_val = la_input * 2.54
            elif "Metros" in unidad_medida:
                al_val = al_input * 100.0
                an_val = an_input * 100.0
                la_val = la_input * 100.0
            else:
                al_val = al_input
                an_val = an_input
                la_val = la_input

            if "Kilogramos" in unidad_peso:
                pe_lb = float(pe_input) * 2.20462
                pe_kg = float(pe_input)
            else:
                pe_lb = float(pe_input)
                pe_kg = float(pe_input) / 2.20462

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147

            # Validación absoluta: una carga que no cabe físicamente o excede
            # el peso legal no puede enviarse ni siquiera como carga CBM.
            excede_contenedor = (
                al_val > CONTENEDOR_40_ALTO_M * 100.0 + 1e-6
                or an_val > CONTENEDOR_40_ANCHO_M * 100.0 + 1e-6
                or la_val > CONTENEDOR_40_LARGO_M * 100.0 + 1e-6
                or pe_kg > PESO_MAX_CONTENEDOR_HN_KG + 1e-6
            )
            excede_paqueteria = es_paqueteria and pe_lb > umbral_paq + 1e-6
            # La conversión automática solo procede si la carga aún cabe en el
            # contenedor. El límite físico absoluto se informa por separado.
            cambio_automatico_cbm = excede_paqueteria and not excede_contenedor
            usar_paqueteria = es_paqueteria and not cambio_automatico_cbm

            if excede_contenedor:
                st.error(
                    "La carga supera la capacidad física o el peso legal de un contenedor 40′ High Cube. "
                    "Revise las medidas o divida el envío antes de emitir la tarifa."
                )
            elif cambio_automatico_cbm:
                st.info(
                    f"🚢 Cambio automático a carga consolidada por CBM: el peso ingresado "
                    f"({pe_lb:.1f} lb) supera el máximo de paquetería ({umbral_paq:.0f} lb)."
                )

            if usar_paqueteria:
                if pe_lb <= umbral_min:
                    tot = min_usd
                    desc = f"Tarifa Mínima Base (1 a {umbral_min:.0f} lbs): ${min_usd:.2f} USD"
                else:
                    tot = float(pe_lb) * float(t_lb)
                    desc = f"Tarifa por Libra: {pe_lb:.1f} lbs x ${t_lb:.2f}/lb"

                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("Volumen (m³)", f"{vol_m3_val:.4f} m³")
                with m2:
                    st.metric("Pies Cúbicos", f"{vol_ft3_val:.2f} ft³")
                with m3:
                    st.metric("Total Estimado", f"${tot:.2f} USD")

                modalidad_pdf = f"Paquetería Menor (1 a {umbral_paq:.0f} lbs)"
                detalle_pdf = desc

            else:
                vol_m3_peso = pe_kg / divisor_vol
                cbm_facturable = max(vol_m3_val, vol_m3_peso)
                tot = cbm_facturable * t_m3

                st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("Volumen Físico", f"{vol_m3_val:.4f} m³")
                with m2:
                    st.metric("CBM Facturable", f"{cbm_facturable:.4f} CBM")
                with m3:
                    st.metric("Total Estimado", f"${tot:.2f} USD")

                modalidad_pdf = "Carga Comercial por Metro Cúbico (CBM)"
                detalle_pdf = (
                    f"Cambio automático desde paquetería: {cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"
                    if cambio_automatico_cbm
                    else f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"
                )

            firma_actual = firma_parametros_cotizador(
                al_val,
                an_val,
                la_val,
                pe_lb,
                st.session_state["modalidad_envio_seleccionada"],
                modalidad_pdf,
            )
            sincronizar_emision_con_formulario(firma_actual)
            st.session_state["_cot_emit_snapshot"] = {
                "al": al_val,
                "an": an_val,
                "la": la_val,
                "peso_lb": pe_lb,
                "peso_kg": pe_kg,
                "vol_m3": vol_m3_val,
                "vol_ft3": vol_ft3_val,
                "total_usd": tot,
                "tipo_carga": modalidad_pdf,
                "detalle_tarifa": detalle_pdf,
                "destino": destino_para_documentos(),
                "firma_params": list(firma_actual),
            }
            with st.container(key="guia_foco_tarifa"):
                pulso_confirmar = st.button(
                    "🤝 Confirmar Tarifa & Emitir Documentos",
                    type="primary",
                    key="btn_confirmar_tarifa",
                    use_container_width=True,
                    on_click=emitir_tarifa_desde_snapshot,
                    disabled=excede_contenedor,
                )
            if (
                pulso_confirmar
                and not excede_contenedor
                and not isinstance(st.session_state.get("datos_pdf_confirmado"), dict)
            ):
                emitir_tarifa_desde_snapshot()
            if st.session_state.get("_ccm_emit_error"):
                st.error(
                    "No se pudo guardar la tarifa en la base de datos. "
                    "Igual puede descargar el formato y abrir Mis Cotizaciones con la emisión en memoria."
                )

            if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
                d_pdf = st.session_state["datos_pdf_confirmado"]
                try:
                    id_c = int(d_pdf.get("id_cot") or 0)
                except (TypeError, ValueError):
                    id_c = 0
                if not id_c:
                    st.session_state.pop("datos_pdf_confirmado", None)
                else:
                    tarifa_consolidada = cotizacion_esta_confirmada(id_c, casillero) or any(
                        int(r.get("id") or 0) == int(id_c) and int(r.get("confirmada") or 0) == 1
                        for r in (st.session_state.get("cotizaciones") or {}).get(casillero, [])
                    )
                    fecha_confirmacion = (
                        d_pdf.get("fecha_confirmacion")
                        or confirmaciones_cotizaciones.get(int(id_c))
                    )
                    tarifa_sigue_visible = (
                        cotizacion_confirmada_vigente(
                            fecha_confirmacion or d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc"), ahora_hn
                        )
                        if tarifa_consolidada
                        else cotizacion_vigente(d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc"), ahora_hn)
                    )
                    # El atajo de la última emisión solo evita ocultar una pendiente
                    # durante el rerun inmediato; nunca prolonga una confirmada más
                    # allá de sus 48 horas.
                    if (
                        not tarifa_consolidada
                        and not tarifa_sigue_visible
                        and int(st.session_state.get("ultima_cot_id") or 0) == int(id_c)
                    ):
                        tarifa_sigue_visible = True
                    if not tarifa_sigue_visible:
                        st.session_state.pop("datos_pdf_confirmado", None)
                    else:
                        dest_pdf = d_pdf.get("destino_entrega", st.session_state["modalidad_envio_seleccionada"])
                        fecha_doc = d_pdf.get("fecha_hora_doc", obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"))
                        estado_doc = texto_estado_cotizacion(
                            d_pdf.get("fecha_sql") or fecha_doc,
                            1 if tarifa_consolidada else 0,
                            ahora_hn,
                            fecha_confirmacion,
                        )
                        if tarifa_consolidada:
                            titulo_emitida = (
                                f"Cotización CCM-COT-{id_c:05d} consolidada. El PDF Tarifa está listo."
                            )
                            contenido_emitida = (
                                f'<div style="color:#166534;font-size:.9rem;font-weight:700;margin-top:8px;">'
                                f'✅ {estado_doc}</div>'
                            )
                        else:
                            titulo_emitida = (
                                f"Tarifa CCM-COT-{id_c:05d} · Pendiente de Confirmar"
                            )
                            contenido_emitida = (
                                f'<div style="margin-top:10px;color:#166534;font-size:.9rem;line-height:1.5;">'
                                f'<div style="margin-bottom:8px;font-weight:800;">⏳ {estado_doc}</div>'
                                '<div style="background:rgba(255,255,255,.72);border-radius:9px;padding:10px 12px;margin:7px 0;">'
                                '<b>1. Envíe el PDF únicamente al fabricante o proveedor.</b><br>'
                                'El botón <b>“Descargar Formato / Documento para el Fabricante”</b> genera el documento para coordinar su mercancía con él.'
                                '</div>'
                                '<div style="background:rgba(255,255,255,.72);border-radius:9px;padding:10px 12px;margin-top:7px;">'
                                '<b>2. Confirme esta tarifa dentro de 1 hora.</b><br>'
                                f'Use <b>“Ir a Mis Cotizaciones”</b> y pulse <b>“Confirmar Cotización”</b> antes de que venza el código <b>CCM-COT-{id_c:05d}</b>.'
                                '</div></div>'
                            )

                        html_alerta_emitida = (
                            '<div style="background:linear-gradient(135deg,#f0fdf4,#dcfce7);border-left:5px solid #22c55e;'
                            'border-radius:12px;padding:16px;margin:15px 0;box-shadow:0 4px 12px rgba(34,197,94,.15);">'
                            '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
                            f'<span style="font-size:1.4rem;">🎉</span><h4 style="color:#166534;margin:0;font-size:1.05rem;font-weight:800;">{titulo_emitida}</h4>'
                            f'</div>{contenido_emitida}</div>'
                        )
                        st.markdown(html_alerta_emitida, unsafe_allow_html=True)

                        pdf_fab = b""
                        try:
                            pdf_fab = generar_pdf_etiqueta_proveedor(
                                casillero=casillero,
                                nombre=nombre_completo,
                                telefono=tel_cli,
                                ciudad=ciu_cli,
                                al=d_pdf.get("al", 0),
                                an=d_pdf.get("an", 0),
                                la=d_pdf.get("la", 0),
                                pe_lb=d_pdf.get("peso_lb", 0),
                                pe_kg=d_pdf.get("peso_kg", 0),
                                vol_m3=d_pdf.get("vol_m3", 0),
                                destino_entrega=dest_pdf,
                                fecha_emision=fecha_doc,
                            ) or b""
                        except Exception:
                            pdf_fab = b""

                        with st.container(key="acciones_emit_cotizador"):
                            st.markdown(
                                '<div id="ccm-acciones-emit" class="ccm-acciones-emit-ancla"></div>',
                                unsafe_allow_html=True,
                            )
                            with st.container(key="guia_foco_pdf_fab"):
                                if pdf_fab:
                                    if st.download_button(
                                        "🏷️ Descargar Formato / Documento para el Fabricante",
                                        pdf_fab,
                                        f"Shipping_Label_Fabricante_{casillero}.pdf",
                                        "application/pdf",
                                        key=f"dl_pdf_fab_{id_c}",
                                        use_container_width=True,
                                    ):
                                        avanzar_guia_si(3, 4)
                                else:
                                    st.button(
                                        "🏷️ Descargar Formato / Documento para el Fabricante",
                                        key=f"dl_pdf_fab_fallback_{id_c}",
                                        use_container_width=True,
                                        disabled=True,
                                    )
                                    st.caption("El PDF no se pudo generar en este momento. Use Ir a Mis Cotizaciones.")
                            with st.container(key="guia_foco_ver_cot"):
                                st.button(
                                    "📋 Ir a Mis Cotizaciones",
                                    type="primary",
                                    key=f"btn_ver_mis_cotizaciones_{id_c}",
                                    use_container_width=True,
                                    on_click=ir_a_historial_guia,
                                    args=(id_c,),
                                )
                        if st.session_state.pop("_ccm_scroll_emit", None):
                            pass

            espaciador_barra_inferior("safe_cotizador_fin")

    elif st.session_state["sub_tab_inicio"] == "Mis Envíos":
        with st.container(key="vista_envios"):
            pintar_banner_promocional_china(casillero)
            pintar_guias_informativas(
                [
                    ("🛡️", "Vigencia confirmada", "Cada cotización confirmada se conserva por <b>48 horas</b>.", "azul"),
                    ("📦", "Seguimiento activo", "Consulte aquí el estado de sus paquetes en tránsito.", "verde"),
                    ("📄", "Documentos oficiales", "La Ficha y el PDF Tarifa están debajo de cada envío.", "naranja"),
                ]
            )
            paquetes = cargar_paquetes_db(casillero)

            if paquetes:
                st.markdown("#### 📦 Mis Paquetes en Tránsito")
                for p in paquetes:
                    st.markdown(
                        f"""
                    <div style="background:#f1f5f9; border:1px solid #cbd5e1; border-radius:10px; padding:12px; margin-bottom:8px;">
                        <b>Tracking:</b> {p[0]} | <b>Contenedor:</b> {p[2]}<br>
                        <b>Estado:</b> <span style="color:#004ac1; font-weight:bold;">{p[3]}</span><br>
                        <small style="color:#64748b;">Actualizado: {p[4]}</small>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )
            st.markdown("#### 📄 Documentos de cotizaciones confirmadas")
            cotizaciones_despacho = ordenar_cotizaciones_desc(
                [row for row in lista_mis_cotizaciones if es_cotizacion_confirmada(row[8])]
            )
            try:
                foco_envios = int(st.session_state.get("cotizacion_envio_foco") or 0)
            except (TypeError, ValueError):
                foco_envios = 0
            if foco_envios:
                cotizaciones_despacho = sorted(
                    cotizaciones_despacho,
                    key=lambda r: (
                        0 if int(r[0]) == foco_envios else 1,
                        *clave_orden_cotizacion(r[7], r[0]),
                    ),
                )
                if not any(int(r[0]) == foco_envios for r in cotizaciones_despacho):
                    extra_foco = next((r for r in lista_mis_cotizaciones if int(r[0]) == foco_envios), None)
                    if extra_foco:
                        cotizaciones_despacho = [extra_foco] + list(cotizaciones_despacho)
            if cotizaciones_despacho:
                envios_render, total_envios, limite_envios = pagina_registros(
                    cotizaciones_despacho, "limite_documentos_envios"
                )
                for cot_env in envios_render:
                    id_e, al_e, an_e, la_e, pe_e, vol_e, tot_e, fec_e, conf_e = cot_env
                    es_foco = foco_envios and int(id_e) == foco_envios
                    borde = "#004ac1" if es_foco else "#e2e8f0"
                    fondo = "#eff6ff" if es_foco else "#f8fafc"
                    if es_foco:
                        st.markdown('<div id="cotizacion-envio-foco"></div>', unsafe_allow_html=True)
                        desplazar_a_ancla("cotizacion-envio-foco")
                    id_ancla_env = f'id="cotizacion-env-{id_e}"'
                    estado_envio = texto_estado_cotizacion(
                        fec_e, conf_e, ahora_hn, confirmaciones_cotizaciones.get(int(id_e))
                    )
                    aviso_seguimiento = (
                        '<div style="margin:10px 0 8px;padding:8px 10px;border-radius:8px;'
                        'background:rgba(0,74,193,.08);color:#003b99;font-weight:700;">'
                        '📦 Esta cotización está lista para seguimiento. Descargue su Ficha y PDF Tarifa a continuación.'
                        '</div>'
                        if es_foco else ""
                    )
                    html_tarjeta_envio = (
                        f'<div {id_ancla_env} style="background:{fondo};border:1.5px solid {borde};border-radius:12px;padding:14px;margin-bottom:8px;font-size:.88rem;box-shadow:0 3px 10px rgba(15,23,42,.06);">'
                        '<div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">'
                        f'<b style="color:#0f172a;">🔖 CCM-COT-{id_e:05d}</b>'
                        '<span style="color:#004ac1;font-weight:800;">✅ Confirmada</span>'
                        f'</div>{aviso_seguimiento}'
                        f'<div style="color:#475569;margin-top:5px;">Fecha de emisión: {formatear_fecha_pantalla(fec_e)}</div>'
                        f'<div style="color:#334155;margin-top:6px;">📐 <b>Medidas:</b> {al_e:.1f} × {an_e:.1f} × {la_e:.1f} cm &nbsp;|&nbsp; ⚖️ <b>Peso:</b> {pe_e:.1f} lbs &nbsp;|&nbsp; 💰 <b>Total:</b> ${tot_e:.2f} USD</div>'
                        f'<div style="color:#1d4ed8;font-weight:700;margin-top:8px;">⏳ {estado_envio}</div>'
                        '</div>'
                    )
                    st.markdown(html_tarjeta_envio, unsafe_allow_html=True)
                    pdf_ficha_env = generar_pdf_etiqueta_proveedor(
                        casillero=casillero,
                        nombre=nombre_completo,
                        telefono=tel_cli,
                        ciudad=ciu_cli,
                        al=al_e,
                        an=an_e,
                        la=la_e,
                        pe_lb=pe_e,
                        pe_kg=pe_e / 2.20462,
                        vol_m3=vol_e,
                        destino_entrega=destino_para_documentos(),
                        fecha_emision=fec_e,
                    )
                    pdf_tarifa_env = generar_pdf_confirmacion_cotizacion(
                        casillero=casillero,
                        nombre=nombre_completo,
                        telefono=tel_cli,
                        ciudad=ciu_cli,
                        tipo_carga="Cotización Confirmada",
                        al=al_e,
                        an=an_e,
                        la=la_e,
                        peso_lb=pe_e,
                        peso_kg=pe_e / 2.20462,
                        vol_m3=vol_e,
                        vol_ft3=vol_e * 35.3147,
                        total_usd=tot_e,
                        detalle_tarifa="Tarifa Calculada Sistema CCM",
                        id_cot=id_e,
                        destino_entrega=destino_para_documentos(),
                        fecha_emision=fec_e,
                    )
                    with st.container(key=f"docs_env_{id_e}"):
                        st.download_button(
                            f"🏷️ Descargar Ficha CCM-COT-{id_e:05d}",
                            pdf_ficha_env,
                            f"Ficha_Bodega_{casillero}_COT{id_e:05d}.pdf",
                            "application/pdf",
                            key=f"dl_ficha_env_{id_e}",
                            use_container_width=True,
                        )
                        st.download_button(
                            f"📥 PDF Tarifa CCM-COT-{id_e:05d}",
                            pdf_tarifa_env,
                            f"Comprobante_Tarifa_{casillero}_COT{id_e:05d}.pdf",
                            "application/pdf",
                            key=f"dl_tarifa_env_{id_e}",
                            use_container_width=True,
                        )
                if limite_envios < total_envios:
                    st.button(
                        f"Mostrar 10 documentos más ({limite_envios} de {total_envios})",
                        key="btn_mas_documentos_envios",
                        use_container_width=True,
                        on_click=aumentar_limite_registros,
                        args=("limite_documentos_envios",),
                    )
            else:
                st.info("Confirme una cotización para consultar y descargar la Ficha y el PDF Tarifa en este módulo.")
            espaciador_barra_inferior("safe_envios")

    elif st.session_state["sub_tab_inicio"] == "Etiqueta":
        with st.container(key="vista_fichas"):
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📋 Fichas")
            st.caption("Ficha técnica de bodega Guangzhou de cada cotización consolidada.")
            cotizaciones_ficha = ordenar_cotizaciones_desc(
                [row for row in lista_mis_cotizaciones if es_cotizacion_confirmada(row[8])]
            )
            if cotizaciones_ficha:
                fichas_render, total_fichas, limite_fichas = pagina_registros(
                    cotizaciones_ficha, "limite_fichas"
                )
                for cot_f in fichas_render:
                    id_f, al_f, an_f, la_f, pe_f, vol_f, tot_f, fec_f, conf_f = cot_f
                    st.markdown(
                        f"""
                    <div style="background:#f8fafc; border:1.5px solid #e2e8f0; border-radius:10px; padding:10px 14px; margin-bottom:8px; font-size:0.85rem;">
                        <b>🔖 CCM-COT-{id_f:05d}</b> &bull; Fecha: {formatear_fecha_pantalla(fec_f)}<br>
                        <small style="color:#475569;">📐 Medidas: {al_f:.1f}x{an_f:.1f}x{la_f:.1f} cm | Peso: {pe_f:.1f} lbs | 💰 Total: <b>${tot_f:.2f} USD</b></small>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )
                    pdf_ficha_mod = generar_pdf_etiqueta_proveedor(
                        casillero=casillero,
                        nombre=nombre_completo,
                        telefono=tel_cli,
                        ciudad=ciu_cli,
                        al=al_f,
                        an=an_f,
                        la=la_f,
                        pe_lb=pe_f,
                        pe_kg=pe_f / 2.20462,
                        vol_m3=vol_f,
                        destino_entrega=destino_para_documentos(),
                        fecha_emision=fec_f,
                    )
                    st.download_button(
                        f"🏷️ Descargar Ficha CCM-COT-{id_f:05d}",
                        pdf_ficha_mod,
                        f"Ficha_Bodega_{casillero}_COT{id_f:05d}.pdf",
                        "application/pdf",
                        key=f"dl_ficha_mod_{id_f}",
                        use_container_width=True,
                    )
                    st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)
                if limite_fichas < total_fichas:
                    st.button(
                        f"Mostrar 10 fichas más ({limite_fichas} de {total_fichas})",
                        key="btn_mas_fichas",
                        use_container_width=True,
                        on_click=aumentar_limite_registros,
                        args=("limite_fichas",),
                    )
            else:
                st.info("Confirme una cotización para descargar su ficha de bodega.")
            espaciador_barra_inferior("safe_fichas")

    pintar_barra_inferior(total_cotizaciones, casillero=casillero)

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

    admin_seccion = st.segmented_control(
        "Sección administrativa",
        options=["Usuarios", "Paquetes", "Tarifas", "Sistema"],
        default="Usuarios",
        label_visibility="collapsed",
        key="admin_seccion",
    )

    if admin_seccion == "Usuarios":
        with get_db() as conn:
            c = conn.cursor()
            if root:
                c.execute(
                    """
                    SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal,
                           departamento, ciudad, direccion_exacta, rol, activo
                    FROM usuarios ORDER BY rol DESC, nombre_completo
                    LIMIT 500
                    """
                )
            else:
                c.execute(
                    """
                    SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal,
                           departamento, ciudad, direccion_exacta, rol, activo
                    FROM usuarios WHERE rol = 'cliente' ORDER BY nombre_completo
                    LIMIT 500
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
                            (n_nom, n_dni, n_cor, n_tel, n_dep, n_ciu, n_dir, nuevo_cas, n_rol, bool(n_act), uid),
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
                st.success(f"Contraseña actualizada con almacenamiento seguro. Clave temporal: **{clave}**")

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
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)
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
                            conn.commit()
                        asegurar_permisos_casillero(n_cod, c_rol)
                        st.success(f"Cuenta creada. Casillero `{n_cod}` • Contraseña `{n_pwd}`")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ya existe un casillero o correo con esos datos.")

    if admin_seccion == "Paquetes":
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
                cargar_paquetes_db.clear()
                st.success("Paquete actualizado.")
            else:
                st.warning("Ingrese tracking y casillero.")

    if admin_seccion == "Tarifas":
        st.markdown("#### Tarifas y constantes del cotizador")
        n_lb = st.number_input("Tarifa por libra China (USD)", min_value=0.01, value=float(get_tarifa("tarifa_libra") or 3.5), step=0.05)
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

    if admin_seccion == "Sistema":
        st.markdown("#### Mantenimiento de base de datos")
        with get_db() as conn:
            c = conn.cursor()
            if USA_SUPABASE:
                c.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            else:
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
