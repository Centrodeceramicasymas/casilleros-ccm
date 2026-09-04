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
import secrets
import ipaddress
import socket
import time

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
ENLACE_POLITICAS_ENVIO = (
    "https://drive.google.com/file/d/1OevqlVTqsWSWb_R95QBTAOJq5h_F7kiK/view?usp=sharing"
)
ENLACE_FORMATO_PRODUCTOS = (
    "https://drive.google.com/drive/folders/1afzW8GMWePgIQq1aad6SfR7AkG3rNAVT?usp=sharing"
)

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
    # Algunas instalaciones antiguas guardan estas columnas como TIMESTAMP y
    # otras como TEXT. Para lecturas, normalizarlas a texto evita que COALESCE
    # intente combinar tipos incompatibles en PostgreSQL.
    sql_pg = re.sub(
        r"COALESCE\(fecha_creacion\s*,\s*fecha\)",
        "COALESCE(fecha_creacion::text, fecha::text)",
        sql_pg,
        flags=re.I,
    )
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

    def _abrir_conexion(self):
        return psycopg.connect(
            self.dsn,
            connect_timeout=self.timeout,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )

    def _nueva_conexion(self):
        conexion = self._abrir_conexion()
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
                    conexion = self._abrir_conexion()
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
ESTADOS_LOGISTICOS = (
    "Etiqueta Oficial Emitida",
    "Esperando Despacho del Proveedor",
    "Despachado por Proveedor",
    "En Bodega China",
    "En Inspección",
    "En Consolidación",
    "Asignado a Contenedor",
    "En Travesía Marítima",
    "Arribó a Puerto Honduras",
    "En Desaduanaje",
    "Disponible en Bodega Central",
    "Listo para Retirar",
    "Entregado",
)
ESTADOS_LOGISTICOS_ESPECIALES = ("Incidencia", "Retenido")
FORMATOS_FECHA_COTIZACION = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
)


def indice_estado_logistico(estado):
    try:
        return ESTADOS_LOGISTICOS.index(str(estado or ""))
    except ValueError:
        return -1


def porcentaje_estado_logistico(estado):
    indice = indice_estado_logistico(estado)
    if indice < 0:
        return 0
    return round((indice / max(1, len(ESTADOS_LOGISTICOS) - 1)) * 100)


def proximo_estado_logistico(estado):
    indice = indice_estado_logistico(estado)
    if 0 <= indice < len(ESTADOS_LOGISTICOS) - 1:
        return ESTADOS_LOGISTICOS[indice + 1]
    if indice == len(ESTADOS_LOGISTICOS) - 1:
        return "Proceso completado"
    return "Revisión del operador"


def transicion_logistica_valida(estado_anterior, estado_nuevo):
    """Impide retrocesos accidentales; una incidencia puede abrirse o resolverse."""
    anterior = str(estado_anterior or "").strip()
    nuevo = str(estado_nuevo or "").strip()
    if not anterior or anterior == nuevo:
        return True
    if anterior in ESTADOS_LOGISTICOS_ESPECIALES or nuevo in ESTADOS_LOGISTICOS_ESPECIALES:
        return True
    indice_anterior = indice_estado_logistico(anterior)
    indice_nuevo = indice_estado_logistico(nuevo)
    return indice_anterior < 0 or indice_nuevo >= indice_anterior


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
DNI_SUPERADMIN = str(os.environ.get("SUPERADMIN_DNI") or "").strip()
NOMBRE_SUPERADMIN = str(os.environ.get("SUPERADMIN_NAME") or "Superusuario CCM").strip()
CORREO_SUPERADMIN = str(os.environ.get("SUPERADMIN_EMAIL") or "").strip().lower()
TELEFONO_SUPERADMIN = str(os.environ.get("SUPERADMIN_PHONE") or "").strip()


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
        # La vigencia comercial puede terminar, pero el comprobante confirmado
        # debe permanecer visible como parte del historial del cliente.
        return True
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


def registrar_error_datos(exc, contexto):
    """Registra detalles en servidor y deja un aviso no sensible para la interfaz."""
    print(f"[CCM datos] {contexto}: {exc}", flush=True)
    try:
        st.session_state["_ccm_error_datos"] = True
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
                   COALESCE(fecha_creacion, fecha), IFNULL(confirmada, 0), fecha_confirmacion
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
    """Reutiliza el snapshot limitado de cotizaciones; no abre otra conexión."""
    cas = formatear_casillero(casillero or "")
    if not cas:
        return {}
    return {
        int(fila[0]): fila[9]
        for fila in cargar_cotizaciones_db(cas)
        if len(fila) > 9 and fila[9]
    }


@st.cache_data(ttl=20, show_spinner=False)
def cargar_estados_cotizaciones_db(casillero):
    cas = formatear_casillero(casillero or "")
    variantes = coincidencias_casillero(cas)
    if not variantes:
        return {}
    marcadores = ",".join("?" for _ in variantes)
    with get_db() as conn:
        filas = conn.execute(
            f"SELECT id, COALESCE(estado, 'emitida') FROM cotizaciones "
            f"WHERE codigo_casillero IN ({marcadores})",
            tuple(variantes),
        ).fetchall()
    return {int(fila[0]): str(fila[1] or "emitida") for fila in filas}


@st.cache_data(ttl=15, show_spinner=False)
def cargar_paquetes_db(casillero):
    """Snapshot breve de paquetes para que la navegación no abra otra conexión."""
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT p.tracking, p.descripcion, p.contenedor_id, p.estado, p.fecha_actualizacion,
                   p.cotizacion_id, p.tipo_contenedor, p.recibido_bodega, p.pago_confirmado,
                   p.costo_manipulacion_usd, p.fecha_recepcion, p.ubicacion_actual, p.eta,
                   p.proximo_paso, p.incidencia, p.visible_cliente,
                   COALESCE(c.estado_pago, CASE WHEN p.pago_confirmado = TRUE THEN 'Confirmado' ELSE 'Pendiente' END),
                   COALESCE(c.estado_documentos, 'Bloqueados'), c.fecha_compromiso,
                   c.receptor_entrega, c.fecha_entrega, c.evidencia_entrega_url,
                   COALESCE(c.incidencia_estado, 'Sin incidencia'), p.codigo_interno,
                   COALESCE(p.cantidad_bultos, 1), COALESCE(p.bultos_verificados, 0),
                   p.responsable_actual, p.zona_almacen, p.ultima_verificacion,
                   COALESCE(p.estado_integridad, 'Pendiente'), p.tracking_externo,
                   p.envio_id, COALESCE(p.numero_bulto, 1),
                   COALESCE(p.etiqueta_estado, 'No emitida'), p.proveedor_nombre,
                   e.codigo_envio, e.cantidad_bultos,
                   COALESCE((SELECT MAX(d.version) FROM documentos_paquete d
                             WHERE d.tracking_ccm=p.codigo_interno
                               AND d.tipo_documento='Etiqueta oficial CCM'), 1)
            FROM paquetes p
            LEFT JOIN control_envios c ON c.tracking = p.tracking
            LEFT JOIN envios e ON e.id = p.envio_id
            WHERE p.codigo_casillero = ? AND COALESCE(p.visible_cliente, TRUE) = TRUE
            ORDER BY p.fecha_actualizacion DESC
            """,
            (cas,),
        ).fetchall()


@st.cache_data(ttl=15, show_spinner=False)
def cargar_eventos_tracking_db(casillero):
    """Eventos visibles del casillero para construir una línea de tiempo real."""
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, estado, ubicacion, mensaje_cliente, fecha_evento, creado_por
            FROM eventos_tracking
            WHERE codigo_casillero = ? AND COALESCE(visible_cliente, TRUE) = TRUE
            ORDER BY fecha_evento DESC, id DESC
            LIMIT 1000
            """,
            (cas,),
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def cargar_trazabilidad_cliente_db(casillero):
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, secuencia, tipo_movimiento, estado_nuevo,
                   mensaje_cliente, fecha_evento, hash_evento
            FROM trazabilidad_paquetes
            WHERE codigo_casillero = ? AND visible_cliente = TRUE
            ORDER BY fecha_evento DESC, secuencia DESC
            LIMIT 2000
            """,
            (cas,),
        ).fetchall()


def refrescar_seguimiento_cliente():
    cargar_paquetes_db.clear()
    cargar_eventos_tracking_db.clear()
    cargar_trazabilidad_cliente_db.clear()
    cargar_notificaciones_cliente.clear()
    st.session_state["_seguimiento_actualizado_en"] = obtener_tiempo_honduras().strftime("%H:%M:%S")


def cotizaciones_habilitadas_por_operacion(paquetes):
    """Cotizaciones liberadas por operación o por decisión explícita del superusuario."""
    habilitadas = set()
    tracking_cotizacion = {}
    for paquete in paquetes or []:
        try:
            cotizacion_id = int(paquete[5]) if paquete[5] is not None else 0
        except (TypeError, ValueError, IndexError):
            cotizacion_id = 0
        recibido = bool(paquete[7]) if len(paquete) > 7 else False
        pagado = bool(paquete[8]) if len(paquete) > 8 else False
        tracking = str(paquete[0] or "").strip() if paquete else ""
        if tracking and cotizacion_id:
            tracking_cotizacion[tracking] = cotizacion_id
        if cotizacion_id and (recibido or pagado):
            habilitadas.add(cotizacion_id)
    if tracking_cotizacion:
        try:
            marcas = ",".join("?" for _ in tracking_cotizacion)
            with get_db() as conn:
                controles = conn.execute(
                    f"SELECT tracking, estado_documentos FROM control_envios WHERE tracking IN ({marcas})",
                    tuple(tracking_cotizacion),
                ).fetchall()
            for tracking, estado_documentos in controles:
                cotizacion_id = tracking_cotizacion.get(str(tracking))
                if not cotizacion_id:
                    continue
                if estado_documentos == "Habilitados":
                    habilitadas.add(cotizacion_id)
                elif estado_documentos in ("Bloqueados", "Anulados"):
                    habilitadas.discard(cotizacion_id)
        except Exception as exc:
            print(f"[CCM documentos] No se pudo aplicar el control administrativo: {exc}", flush=True)
    return habilitadas


@st.cache_data(ttl=10, show_spinner=False)
def cargar_paquetes_admin():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado,
                   fecha_actualizacion, cotizacion_id, tipo_contenedor,
                   recibido_bodega, pago_confirmado, costo_manipulacion_usd, fecha_recepcion,
                   ubicacion_actual, eta, proximo_paso, incidencia, visible_cliente,
                   COALESCE(version, 1), codigo_interno, COALESCE(cantidad_bultos, 1),
                   COALESCE(bultos_verificados, 0), responsable_actual, zona_almacen,
                   ultima_verificacion, COALESCE(estado_integridad, 'Pendiente'),
                   tracking_externo, envio_id, COALESCE(numero_bulto, 1),
                   COALESCE(etiqueta_estado, 'No emitida'), proveedor_nombre
            FROM paquetes
            ORDER BY fecha_actualizacion DESC
            LIMIT 500
            """
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def buscar_paquetes_admin(termino, limite=200):
    texto = str(termino or "").strip().lower()
    if not texto:
        return cargar_paquetes_admin()
    patron = f"%{texto}%"
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado,
                   fecha_actualizacion, cotizacion_id, tipo_contenedor,
                   recibido_bodega, pago_confirmado, costo_manipulacion_usd, fecha_recepcion,
                   ubicacion_actual, eta, proximo_paso, incidencia, visible_cliente,
                   COALESCE(version, 1), codigo_interno, COALESCE(cantidad_bultos, 1),
                   COALESCE(bultos_verificados, 0), responsable_actual, zona_almacen,
                   ultima_verificacion, COALESCE(estado_integridad, 'Pendiente'),
                   tracking_externo, envio_id, COALESCE(numero_bulto, 1),
                   COALESCE(etiqueta_estado, 'No emitida'), proveedor_nombre
            FROM paquetes
            WHERE LOWER(COALESCE(tracking, '')) LIKE ?
               OR LOWER(COALESCE(codigo_interno, '')) LIKE ?
               OR LOWER(COALESCE(codigo_casillero, '')) LIKE ?
               OR LOWER(COALESCE(contenedor_id, '')) LIKE ?
               OR LOWER(COALESCE(zona_almacen, '')) LIKE ?
               OR LOWER(COALESCE(tracking_externo, '')) LIKE ?
            ORDER BY fecha_actualizacion DESC
            LIMIT ?
            """,
            (patron, patron, patron, patron, patron, patron, int(limite)),
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def buscar_tracking_exacto_admin(tracking):
    codigo = str(tracking or "").strip()
    if not codigo:
        return None
    with get_db() as conn:
        filas = conn.execute(
            """
            SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado,
                   fecha_actualizacion, cotizacion_id, tipo_contenedor,
                   recibido_bodega, pago_confirmado, costo_manipulacion_usd, fecha_recepcion,
                   ubicacion_actual, eta, proximo_paso, incidencia, visible_cliente,
                   COALESCE(version, 1), codigo_interno, COALESCE(cantidad_bultos, 1),
                   COALESCE(bultos_verificados, 0), responsable_actual, zona_almacen,
                   ultima_verificacion, COALESCE(estado_integridad, 'Pendiente'),
                   tracking_externo, envio_id, COALESCE(numero_bulto, 1),
                   COALESCE(etiqueta_estado, 'No emitida'), proveedor_nombre
            FROM paquetes
            WHERE UPPER(TRIM(tracking)) = UPPER(TRIM(?))
               OR UPPER(TRIM(COALESCE(codigo_interno, ''))) = UPPER(TRIM(?))
            LIMIT 2
            """,
            (codigo, codigo),
        ).fetchall()
        if not filas:
            filas = conn.execute(
                """
                SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado,
                       fecha_actualizacion, cotizacion_id, tipo_contenedor,
                       recibido_bodega, pago_confirmado, costo_manipulacion_usd, fecha_recepcion,
                       ubicacion_actual, eta, proximo_paso, incidencia, visible_cliente,
                       COALESCE(version, 1), codigo_interno, COALESCE(cantidad_bultos, 1),
                       COALESCE(bultos_verificados, 0), responsable_actual, zona_almacen,
                       ultima_verificacion, COALESCE(estado_integridad, 'Pendiente'),
                       tracking_externo, envio_id, COALESCE(numero_bulto, 1),
                       COALESCE(etiqueta_estado, 'No emitida'), proveedor_nombre
                FROM paquetes
                WHERE UPPER(TRIM(COALESCE(tracking_externo, ''))) = UPPER(TRIM(?))
                LIMIT 2
                """,
                (codigo,),
            ).fetchall()
    if len(filas) > 1:
        raise sqlite3.IntegrityError(
            "El tracking externo corresponde a varios bultos. Use el tracking CCM o abra el casillero agrupado."
        )
    return filas[0] if filas else None


@st.cache_data(ttl=10, show_spinner=False)
def cargar_paquetes_casillero_admin(casillero):
    cas = formatear_casillero(casillero)
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado,
                   fecha_actualizacion, cotizacion_id, tipo_contenedor,
                   recibido_bodega, pago_confirmado, costo_manipulacion_usd, fecha_recepcion,
                   ubicacion_actual, eta, proximo_paso, incidencia, visible_cliente,
                   COALESCE(version, 1), codigo_interno, COALESCE(cantidad_bultos, 1),
                   COALESCE(bultos_verificados, 0), responsable_actual, zona_almacen,
                   ultima_verificacion, COALESCE(estado_integridad, 'Pendiente'),
                   tracking_externo, envio_id, COALESCE(numero_bulto, 1),
                   COALESCE(etiqueta_estado, 'No emitida'), proveedor_nombre
            FROM paquetes
            WHERE codigo_casillero = ?
            ORDER BY fecha_actualizacion DESC, tracking
            """,
            (cas,),
        ).fetchall()


@st.cache_data(ttl=30, show_spinner=False)
def cargar_clientes_con_paquetes_admin():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT u.codigo_casillero, u.nombre_completo, COUNT(p.tracking)
            FROM usuarios u
            LEFT JOIN paquetes p ON p.codigo_casillero = u.codigo_casillero
            WHERE u.rol = 'cliente'
            GROUP BY u.codigo_casillero, u.nombre_completo
            ORDER BY u.nombre_completo
            """
        ).fetchall()


def abrir_tracking_en_editor_admin(tracking=None):
    codigo = str(tracking or st.session_state.get("admin_tracking_directo_input") or "").strip()
    if not codigo:
        st.session_state["_admin_tracking_busqueda_error"] = "Ingrese un tracking para buscar."
        return
    try:
        paquete = buscar_tracking_exacto_admin(codigo)
    except sqlite3.IntegrityError as exc:
        st.session_state["_admin_tracking_busqueda_error"] = str(exc)
        return
    if not paquete:
        st.session_state["_admin_tracking_busqueda_error"] = (
            f"No se encontró ningún paquete con el tracking {codigo}."
        )
        return
    tracking_real = str(paquete[0])
    st.session_state["admin_pkg_busqueda"] = tracking_real
    st.session_state["admin_pkg_registro_editar"] = tracking_real
    st.session_state["_admin_tracking_busqueda_ok"] = (
        f"Tracking {tracking_real} localizado. El editor quedó preparado."
    )


@st.cache_data(ttl=15, show_spinner=False)
def cargar_metricas_paquetes_admin():
    """Métricas sobre toda la tabla, independientes del límite del inventario."""
    with get_db() as conn:
        fila = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN recibido_bodega = TRUE THEN 1 ELSE 0 END),
                   SUM(CASE WHEN estado = 'En Travesía Marítima' THEN 1 ELSE 0 END),
                   COALESCE(SUM(costo_manipulacion_usd), 0),
                   SUM(CASE WHEN COALESCE(estado_integridad, 'Pendiente') <> 'Verificado' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN estado IN ('Incidencia', 'Retenido') THEN 1 ELSE 0 END)
            FROM paquetes
            """
        ).fetchone()
    return tuple(fila or (0, 0, 0, 0, 0, 0))


@st.cache_data(ttl=10, show_spinner=False)
def cargar_eventos_tracking_admin(tracking):
    codigo = str(tracking or "").strip()
    if not codigo:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT fecha_evento, estado, ubicacion, mensaje_cliente, nota_interna,
                   creado_por, visible_cliente
            FROM eventos_tracking
            WHERE tracking = ?
            ORDER BY fecha_evento DESC, id DESC
            LIMIT 200
            """,
            (codigo,),
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def cargar_trazabilidad_paquete_db(tracking, solo_visible=False):
    codigo = str(tracking or "").strip()
    if not codigo:
        return []
    filtro = "AND visible_cliente = TRUE" if solo_visible else ""
    with get_db() as conn:
        return conn.execute(
            f"""
            SELECT secuencia, tipo_movimiento, estado_anterior, estado_nuevo,
                   datos_anteriores_json, datos_nuevos_json, mensaje_cliente,
                   nota_interna, visible_cliente, fecha_evento, creado_por,
                   hash_anterior, hash_evento
            FROM trazabilidad_paquetes
            WHERE tracking = ? {filtro}
            ORDER BY secuencia DESC
            LIMIT 500
            """,
            (codigo,),
        ).fetchall()


def verificar_integridad_trazabilidad(tracking):
    movimientos = list(reversed(cargar_trazabilidad_paquete_db(tracking, solo_visible=False)))
    hash_previo = "ORIGEN"
    for movimiento in movimientos:
        secuencia, tipo, estado_anterior, estado_nuevo, anterior_json, nuevo_json, _, _, _, fecha, actor, hash_anterior, hash_evento = movimiento
        contenido = "|".join(
            (
                str(tracking), str(secuencia), hash_previo, str(tipo),
                str(estado_anterior or ""), str(estado_nuevo or ""),
                str(anterior_json), str(nuevo_json), str(fecha), str(actor),
            )
        )
        calculado = hashlib.sha256(contenido.encode("utf-8")).hexdigest()
        if str(hash_anterior) != hash_previo or not hmac.compare_digest(str(hash_evento), calculado):
            return False, int(secuencia), len(movimientos)
        hash_previo = str(hash_evento)
    return True, None, len(movimientos)


@st.cache_data(ttl=10, show_spinner=False)
def cargar_cotizaciones_revision_admin():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT c.id, c.codigo_casillero, c.total_usd,
                   COALESCE(c.fecha_confirmacion, c.fecha_creacion, c.fecha),
                   COALESCE(c.estado, 'pendiente_revision'), c.tipo_carga,
                   c.destino_entrega, u.nombre_completo, u.telefono_principal,
                   a.condicion_pago, a.estado_acuerdo, a.estado_pago,
                   a.fecha_vencimiento, a.nota_cliente, a.nota_interna
            FROM cotizaciones c
            JOIN usuarios u ON u.codigo_casillero = c.codigo_casillero
            LEFT JOIN acuerdos_pago a ON a.cotizacion_id = c.id
            WHERE COALESCE(c.confirmada, FALSE) = TRUE
            ORDER BY COALESCE(c.fecha_confirmacion, c.fecha_creacion, c.fecha) DESC, c.id DESC
            LIMIT 500
            """
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def cargar_envios_aprobados_admin():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT e.id, e.codigo_envio, e.cotizacion_id, e.codigo_casillero,
                   e.cantidad_bultos, e.estado, e.proveedor_nombre,
                   e.fecha_aprobacion, u.nombre_completo,
                   SUM(CASE WHEN p.recibido_bodega = TRUE THEN 1 ELSE 0 END)
            FROM envios e
            JOIN usuarios u ON u.codigo_casillero = e.codigo_casillero
            LEFT JOIN paquetes p ON p.envio_id = e.id
            GROUP BY e.id, e.codigo_envio, e.cotizacion_id, e.codigo_casillero,
                     e.cantidad_bultos, e.estado, e.proveedor_nombre,
                     e.fecha_aprobacion, u.nombre_completo
            ORDER BY e.fecha_actualizacion DESC
            LIMIT 500
            """
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def cargar_bultos_envio_admin(envio_id):
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tracking, codigo_interno, tracking_externo, numero_bulto,
                   estado, etiqueta_estado, recibido_bodega, ubicacion_actual,
                   fecha_actualizacion, descripcion, codigo_casillero,
                   COALESCE((SELECT MAX(d.version) FROM documentos_paquete d
                             WHERE d.tracking_ccm=paquetes.codigo_interno
                               AND d.tipo_documento='Etiqueta oficial CCM'), 1)
            FROM paquetes WHERE envio_id = ?
            ORDER BY numero_bulto, tracking
            """,
            (int(envio_id),),
        ).fetchall()


@st.cache_data(ttl=10, show_spinner=False)
def buscar_bulto_ccm_admin(tracking_ccm):
    codigo = str(tracking_ccm or "").strip().upper()
    if not codigo:
        return None
    with get_db() as conn:
        return conn.execute(
            """
            SELECT p.tracking, p.codigo_interno, p.codigo_casillero, p.descripcion,
                   p.estado, p.recibido_bodega, p.numero_bulto, p.envio_id,
                   p.etiqueta_estado, p.cantidad_bultos, p.bultos_verificados,
                   p.responsable_actual, p.zona_almacen, p.tracking_externo,
                   e.codigo_envio, e.cantidad_bultos, u.nombre_completo
            FROM paquetes p
            LEFT JOIN envios e ON e.id = p.envio_id
            LEFT JOIN usuarios u ON u.codigo_casillero = p.codigo_casillero
            WHERE UPPER(TRIM(p.codigo_interno)) = UPPER(TRIM(?))
            """,
            (codigo,),
        ).fetchone()


@st.cache_data(ttl=10, show_spinner=False)
def cargar_excepciones_recepcion_admin():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, codigo_escaneado, categoria, detalle, estado,
                   codigo_casillero, tracking_ccm, fotografia_url,
                   responsable, resolucion, creado_por, fecha_creacion, fecha_actualizacion
            FROM excepciones_recepcion
            ORDER BY CASE WHEN estado IN ('Resuelta', 'Cerrada') THEN 1 ELSE 0 END,
                     fecha_actualizacion DESC, id DESC
            LIMIT 500
            """
        ).fetchall()


def _codigo_operativo_unico(prefijo, longitud=8):
    fecha = obtener_tiempo_honduras().strftime("%y%m")
    return f"{prefijo}-{fecha}-{secrets.token_hex(max(3, longitud // 2)).upper()[:longitud]}"


def actualizar_revision_cotizacion(
    cotizacion_id, estado_revision, condicion_pago, estado_acuerdo,
    estado_pago, monto, vencimiento, nota_cliente, nota_interna,
):
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    actor = st.session_state.get("usuario") or "superadmin"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT codigo_casillero, COALESCE(confirmada, FALSE) FROM cotizaciones WHERE id = ?",
            (int(cotizacion_id),),
        )
        cotizacion = cur.fetchone()
        if not cotizacion or not bool(cotizacion[1]):
            return False, "La cotización no está confirmada por el cliente."
        cas = formatear_casillero(cotizacion[0])
        cur.execute(
            """
            INSERT INTO acuerdos_pago (
                cotizacion_id, codigo_casillero, condicion_pago, estado_acuerdo,
                estado_pago, monto_acordado, fecha_vencimiento, nota_cliente,
                nota_interna, aprobado_por, fecha_aprobacion, fecha_actualizacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cotizacion_id) DO UPDATE SET
                condicion_pago=excluded.condicion_pago,
                estado_acuerdo=excluded.estado_acuerdo,
                estado_pago=excluded.estado_pago,
                monto_acordado=excluded.monto_acordado,
                fecha_vencimiento=excluded.fecha_vencimiento,
                nota_cliente=excluded.nota_cliente,
                nota_interna=excluded.nota_interna,
                aprobado_por=excluded.aprobado_por,
                fecha_aprobacion=excluded.fecha_aprobacion,
                fecha_actualizacion=excluded.fecha_actualizacion
            """,
            (
                int(cotizacion_id), cas, condicion_pago, estado_acuerdo,
                estado_pago, float(monto or 0), str(vencimiento or "").strip() or None,
                str(nota_cliente or "").strip(), str(nota_interna or "").strip(),
                actor, fecha if estado_acuerdo == "Aprobado" else None, fecha,
            ),
        )
        cur.execute(
            "UPDATE cotizaciones SET estado = ? WHERE id = ? AND codigo_casillero = ?",
            (estado_revision, int(cotizacion_id), cas),
        )
    crear_notificacion_cliente(
        cas, f"Revisión de CCM-COT-{int(cotizacion_id):05d}",
        str(nota_cliente or "").strip() or f"Su cotización cambió al estado {estado_revision.replace('_', ' ')}.",
        tipo="Cotización", prioridad="Alta" if estado_revision == "requiere_correccion" else "Normal",
    )
    cargar_cotizaciones_revision_admin.clear()
    cargar_estados_cotizaciones_db.clear()
    cargar_cotizaciones_db.clear()
    return True, "Revisión administrativa guardada."


def aprobar_y_generar_tracking_cotizacion(
    cotizacion_id, condicion_pago, estado_pago, monto, vencimiento,
    cantidad_bultos, proveedor, nota_cliente, nota_interna,
):
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    actor = st.session_state.get("usuario") or "superadmin"
    cantidad = int(cantidad_bultos or 0)
    if cantidad < 1 or cantidad > 500:
        return False, "La cantidad de bultos debe estar entre 1 y 500.", None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT codigo_casillero, COALESCE(confirmada, FALSE), total_usd,
                   tipo_carga, destino_entrega
            FROM cotizaciones WHERE id = ?
            """,
            (int(cotizacion_id),),
        )
        cot = cur.fetchone()
        if not cot or not bool(cot[1]):
            return False, "La cotización no fue confirmada por el cliente.", None
        cas = formatear_casillero(cot[0])
        cur.execute("SELECT id, codigo_envio FROM envios WHERE cotizacion_id = ?", (int(cotizacion_id),))
        existente = cur.fetchone()
        if existente:
            return False, f"Esta cotización ya generó el envío {existente[1]}.", existente[1]
        codigo_envio = _codigo_operativo_unico("CCM-ENV", 8)
        token_bultos = secrets.token_hex(4).upper()
        cur.execute(
            """
            INSERT INTO acuerdos_pago (
                cotizacion_id, codigo_casillero, condicion_pago, estado_acuerdo,
                estado_pago, monto_acordado, fecha_vencimiento, nota_cliente,
                nota_interna, aprobado_por, fecha_aprobacion, fecha_actualizacion
            ) VALUES (?, ?, ?, 'Aprobado', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cotizacion_id) DO UPDATE SET
                condicion_pago=excluded.condicion_pago,
                estado_acuerdo='Aprobado', estado_pago=excluded.estado_pago,
                monto_acordado=excluded.monto_acordado,
                fecha_vencimiento=excluded.fecha_vencimiento,
                nota_cliente=excluded.nota_cliente, nota_interna=excluded.nota_interna,
                aprobado_por=excluded.aprobado_por,
                fecha_aprobacion=excluded.fecha_aprobacion,
                fecha_actualizacion=excluded.fecha_actualizacion
            """,
            (
                int(cotizacion_id), cas, condicion_pago, estado_pago, float(monto or 0),
                str(vencimiento or "").strip() or None, str(nota_cliente or "").strip(),
                str(nota_interna or "").strip(), actor, fecha, fecha,
            ),
        )
        cur.execute(
            """
            INSERT INTO envios (
                codigo_envio, cotizacion_id, codigo_casillero, cantidad_bultos,
                estado, proveedor_nombre, aprobado_por, fecha_aprobacion, fecha_actualizacion
            ) VALUES (?, ?, ?, ?, 'Etiquetas emitidas', ?, ?, ?, ?)
            """,
            (codigo_envio, int(cotizacion_id), cas, cantidad, str(proveedor or "").strip(), actor, fecha, fecha),
        )
        cur.execute("SELECT id FROM envios WHERE codigo_envio = ?", (codigo_envio,))
        envio_id = int(cur.fetchone()[0])
        for numero in range(1, cantidad + 1):
            tracking_ccm = f"CCM-PKG-{token_bultos}-{numero:03d}"
            descripcion = f"Bulto {numero} de {cantidad} · {cot[3] or 'Carga cotizada'}"
            cur.execute(
                """
                INSERT INTO paquetes (
                    tracking, codigo_casillero, descripcion, contenedor_id, tipo_contenedor,
                    cotizacion_id, recibido_bodega, pago_confirmado, costo_manipulacion_usd,
                    fecha_recepcion, ubicacion_actual, eta, proximo_paso, incidencia,
                    visible_cliente, estado, fecha_actualizacion, codigo_interno,
                    cantidad_bultos, bultos_verificados, responsable_actual, zona_almacen,
                    ultima_verificacion, estado_integridad, version, tracking_externo,
                    envio_id, numero_bulto, etiqueta_estado, proveedor_nombre
                ) VALUES (?, ?, ?, '', 'Bulto individual', ?, FALSE, ?, 0, NULL,
                          'Proveedor en China', '', 'Despacho del proveedor', '', TRUE,
                          'Etiqueta Oficial Emitida', ?, ?, 1, 0, 'Proveedor / cliente',
                          'Pendiente de recepción', NULL, 'Pendiente', 1, NULL, ?, ?, 'Vigente', ?)
                """,
                (
                    tracking_ccm, cas, descripcion, int(cotizacion_id),
                    estado_pago == "Confirmado", fecha, tracking_ccm,
                    envio_id, numero, str(proveedor or "").strip(),
                ),
            )
            cur.execute(
                """
                INSERT INTO documentos_paquete (
                    tracking_ccm, tipo_documento, version, estado, fecha_emision, emitido_por
                ) VALUES (?, 'Etiqueta oficial CCM', 1, 'Vigente', ?, ?)
                """,
                (tracking_ccm, fecha, actor),
            )
            registrar_trazabilidad_paquete(
                cur, tracking_ccm, cas, "TRACKING_CCM_GENERADO", "", "Etiqueta Oficial Emitida",
                {}, {"codigo_envio": codigo_envio, "cotizacion_id": int(cotizacion_id),
                     "numero_bulto": numero, "total_bultos": cantidad},
                "CCM aprobó la operación y emitió la etiqueta oficial del bulto.",
                str(nota_interna or "").strip(), True, actor, fecha,
            )
            cur.execute(
                """
                INSERT INTO eventos_tracking (
                    tracking, codigo_casillero, estado, ubicacion, mensaje_cliente,
                    nota_interna, fecha_evento, creado_por, visible_cliente
                ) VALUES (?, ?, 'Etiqueta Oficial Emitida', 'Proveedor en China', ?, ?, ?, ?, TRUE)
                """,
                (
                    tracking_ccm, cas,
                    "Etiqueta oficial disponible. Envíela al proveedor para identificar este bulto.",
                    str(nota_interna or "").strip(), fecha, actor,
                ),
            )
        cur.execute(
            "UPDATE cotizaciones SET estado='aprobada_tracking_generado' WHERE id=? AND codigo_casillero=?",
            (int(cotizacion_id), cas),
        )
    crear_notificacion_cliente(
        cas, f"Envío {codigo_envio} aprobado",
        f"CCM generó {cantidad} etiqueta(s) oficiales. Descárguelas desde Mis Envíos.",
        tipo="Seguimiento", prioridad="Alta",
    )
    invalidar_cache_flujo_tracking()
    return True, f"Envío {codigo_envio} creado con {cantidad} tracking(s) CCM.", codigo_envio


def invalidar_cache_flujo_tracking():
    cargar_cotizaciones_revision_admin.clear()
    cargar_envios_aprobados_admin.clear()
    cargar_bultos_envio_admin.clear()
    buscar_bulto_ccm_admin.clear()
    cargar_excepciones_recepcion_admin.clear()
    cargar_paquetes_db.clear()
    cargar_eventos_tracking_db.clear()
    cargar_trazabilidad_cliente_db.clear()
    cargar_trazabilidad_paquete_db.clear()
    cargar_paquetes_admin.clear()
    buscar_paquetes_admin.clear()
    buscar_tracking_exacto_admin.clear()
    cargar_paquetes_casillero_admin.clear()
    cargar_clientes_con_paquetes_admin.clear()
    cargar_metricas_paquetes_admin.clear()


def registrar_recepcion_bodega(
    tracking_ccm, condicion, peso_kg, largo_cm, ancho_cm, alto_cm,
    fotografia_url, zona_almacen, observaciones,
):
    codigo = str(tracking_ccm or "").strip().upper()
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    actor = st.session_state.get("usuario") or "superadmin"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tracking, codigo_casillero, estado, recibido_bodega,
                   etiqueta_estado, numero_bulto, envio_id, descripcion,
                   ubicacion_actual, responsable_actual, zona_almacen, cotizacion_id
            FROM paquetes WHERE UPPER(TRIM(codigo_interno)) = UPPER(TRIM(?))
            """,
            (codigo,),
        )
        paquete = cur.fetchone()
        if not paquete:
            return False, "Tracking CCM no reconocido.", "desconocido"
        if str(paquete[4]) != "Vigente":
            return False, f"La etiqueta está {paquete[4]} y no puede recibirse.", "etiqueta_invalida"
        cur.execute("SELECT fecha_recepcion, recibido_por, zona_almacen FROM recepciones_bodega WHERE tracking_ccm=?", (codigo,))
        recepcion_previa = cur.fetchone()
        if recepcion_previa or bool(paquete[3]):
            detalle = recepcion_previa or ("fecha registrada", "operador anterior", paquete[10])
            return False, f"Este bulto ya fue recibido: {detalle[0]} por {detalle[1]} en {detalle[2]}.", "duplicada"
        diferencia_medidas = False
        if paquete[11] and paquete[6]:
            total_envio = cur.execute(
                "SELECT cantidad_bultos FROM envios WHERE id=?", (int(paquete[6]),)
            ).fetchone()
            esperado = cur.execute(
                "SELECT largo_cm, ancho_cm, alto_cm, peso_lb FROM cotizaciones WHERE id=?",
                (int(paquete[11]),),
            ).fetchone()
            if total_envio and int(total_envio[0] or 0) == 1 and esperado:
                reales = (float(largo_cm or 0), float(ancho_cm or 0), float(alto_cm or 0), float(peso_kg or 0))
                esperados = (
                    float(esperado[0] or 0), float(esperado[1] or 0),
                    float(esperado[2] or 0), float(esperado[3] or 0) / 2.20462,
                )
                diferencia_medidas = any(
                    valor_esperado > 0 and abs(valor_real - valor_esperado) / valor_esperado > 0.15
                    for valor_real, valor_esperado in zip(reales, esperados)
                )
        integridad = (
            "Diferencia detectada" if diferencia_medidas
            else "Verificado" if condicion == "Sin daños visibles"
            else "Dañado"
        )
        nuevo_estado = "En Bodega China" if integridad == "Verificado" else "Incidencia"
        cur.execute(
            """
            INSERT INTO recepciones_bodega (
                tracking_ccm, codigo_casillero, condicion, peso_real_kg,
                largo_real_cm, ancho_real_cm, alto_real_cm, fotografia_url,
                zona_almacen, observaciones, recibido_por, fecha_recepcion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                codigo, paquete[1], condicion, float(peso_kg or 0), float(largo_cm or 0),
                float(ancho_cm or 0), float(alto_cm or 0), str(fotografia_url or "").strip(),
                str(zona_almacen).strip(), str(observaciones or "").strip(), actor, fecha,
            ),
        )
        cur.execute(
            """
            UPDATE paquetes SET recibido_bodega=TRUE, fecha_recepcion=?, estado=?,
                ubicacion_actual='Bodega CCM Shanghái', proximo_paso=?,
                incidencia=?, bultos_verificados=1, responsable_actual=?, zona_almacen=?,
                ultima_verificacion=?, estado_integridad=?, fecha_actualizacion=?, version=version+1
            WHERE tracking=?
            """,
            (
                fecha, nuevo_estado,
                "Inspección de ingreso" if integridad == "Verificado" else "Revisión de incidencia",
                "" if integridad == "Verificado" else condicion,
                actor, str(zona_almacen).strip(), fecha, integridad, fecha, paquete[0],
            ),
        )
        mensaje = (
            "Su bulto fue recibido y verificado en la bodega de Shanghái."
            if integridad == "Verificado"
            else f"Su bulto fue recibido con una incidencia: {'diferencia de peso o medidas' if diferencia_medidas else condicion}."
        )
        registrar_trazabilidad_paquete(
            cur, paquete[0], paquete[1], "RECEPCION_BODEGA_CHINA",
            paquete[2], nuevo_estado,
            {"ubicacion": paquete[8], "responsable": paquete[9], "zona": paquete[10]},
            {"condicion": condicion, "peso_kg": float(peso_kg or 0),
             "dimensiones_cm": [float(largo_cm or 0), float(ancho_cm or 0), float(alto_cm or 0)],
             "zona": str(zona_almacen).strip(), "integridad": integridad},
            mensaje, str(observaciones or "").strip(), True, actor, fecha,
        )
        cur.execute(
            """
            INSERT INTO eventos_tracking (
                tracking, codigo_casillero, estado, ubicacion, mensaje_cliente,
                nota_interna, fecha_evento, creado_por, visible_cliente
            ) VALUES (?, ?, ?, 'Bodega CCM Shanghái', ?, ?, ?, ?, TRUE)
            """,
            (paquete[0], paquete[1], nuevo_estado, mensaje, str(observaciones or "").strip(), fecha, actor),
        )
        if paquete[6]:
            cur.execute(
                """
                UPDATE envios SET
                    estado = CASE
                        WHEN (SELECT COUNT(*) FROM paquetes WHERE envio_id=? AND recibido_bodega=TRUE)
                             = cantidad_bultos THEN 'Recibido completo en China'
                        ELSE 'Recepción parcial en China'
                    END,
                    fecha_actualizacion=? WHERE id=?
                """,
                (int(paquete[6]), fecha, int(paquete[6])),
            )
    crear_notificacion_cliente(
        paquete[1], f"Recepción confirmada · {codigo}", mensaje,
        tipo="Seguimiento", prioridad="Urgente" if integridad == "Dañado" else "Normal",
        tracking=paquete[0],
    )
    invalidar_cache_flujo_tracking()
    return True, mensaje, "recibida"


def registrar_excepcion_recepcion(codigo, categoria, detalle, fotografia_url=""):
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    actor = st.session_state.get("usuario") or "superadmin"
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO excepciones_recepcion (
                codigo_escaneado, categoria, detalle, estado, fotografia_url,
                creado_por, fecha_creacion, fecha_actualizacion
            ) VALUES (?, ?, ?, 'Abierta', ?, ?, ?, ?)
            """,
            (str(codigo or "").strip(), categoria, str(detalle or "").strip(),
             str(fotografia_url or "").strip(), actor, fecha, fecha),
        )
    cargar_excepciones_recepcion_admin.clear()
    return True


def cambiar_estado_etiqueta(tracking_ccm, accion):
    codigo = str(tracking_ccm or "").strip().upper()
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    actor = st.session_state.get("usuario") or "superadmin"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT tracking, codigo_casillero, estado, etiqueta_estado FROM paquetes "
            "WHERE UPPER(TRIM(codigo_interno))=UPPER(TRIM(?))",
            (codigo,),
        )
        paquete = cur.fetchone()
        if not paquete:
            return False, "No se encontró el bulto."
        if bool(conn.execute("SELECT 1 FROM recepciones_bodega WHERE tracking_ccm=?", (codigo,)).fetchone()):
            return False, "Una etiqueta recibida físicamente ya no puede anularse ni reemitirse."
        if accion == "anular":
            nuevo_estado = "Anulada"
            cur.execute("UPDATE documentos_paquete SET estado='Anulada' WHERE tracking_ccm=? AND estado='Vigente'", (codigo,))
        elif accion == "reemitir":
            nuevo_estado = "Vigente"
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM documentos_paquete "
                "WHERE tracking_ccm=? AND tipo_documento='Etiqueta oficial CCM'",
                (codigo,),
            )
            version = int((cur.fetchone() or (0,))[0] or 0) + 1
            cur.execute("UPDATE documentos_paquete SET estado='Reemplazada' WHERE tracking_ccm=? AND estado='Vigente'", (codigo,))
            cur.execute(
                "INSERT INTO documentos_paquete (tracking_ccm, tipo_documento, version, estado, fecha_emision, emitido_por) "
                "VALUES (?, 'Etiqueta oficial CCM', ?, 'Vigente', ?, ?)",
                (codigo, version, fecha, actor),
            )
        else:
            return False, "Acción de etiqueta no reconocida."
        cur.execute(
            "UPDATE paquetes SET etiqueta_estado=?, fecha_actualizacion=?, version=version+1 WHERE tracking=?",
            (nuevo_estado, fecha, paquete[0]),
        )
        registrar_trazabilidad_paquete(
            cur, paquete[0], paquete[1], "ETIQUETA_REEMITIDA" if accion == "reemitir" else "ETIQUETA_ANULADA",
            paquete[2], paquete[2], {"etiqueta": paquete[3]}, {"etiqueta": nuevo_estado},
            f"La etiqueta oficial fue {nuevo_estado.lower()} por CCM.", "", True, actor, fecha,
        )
    crear_notificacion_cliente(
        paquete[1], f"Etiqueta {nuevo_estado.lower()} · {codigo}",
        f"La etiqueta oficial del bulto fue {nuevo_estado.lower()}. Use únicamente la versión vigente.",
        tipo="Documentos", prioridad="Alta", tracking=paquete[0],
    )
    invalidar_cache_flujo_tracking()
    return True, f"Etiqueta {nuevo_estado.lower()} correctamente."


@st.cache_data(ttl=20, show_spinner=False)
def cargar_cotizaciones_confirmadas_admin():
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, codigo_casillero, total_usd, COALESCE(fecha_creacion, fecha)
            FROM cotizaciones
            WHERE IFNULL(confirmada, 0) = 1 AND COALESCE(estado, 'confirmada') <> 'vencida'
            ORDER BY fecha_creacion DESC, id DESC
            LIMIT 500
            """
        ).fetchall()


@st.cache_data(ttl=30, show_spinner=False)
def cargar_resumen_operativo_admin():
    with get_db() as conn:
        cotizaciones = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN IFNULL(confirmada, 0) = 1 THEN 1 ELSE 0 END),
                   COALESCE(SUM(total_usd), 0)
            FROM cotizaciones
            """
        ).fetchone()
        paquetes = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(costo_manipulacion_usd), 0)
            FROM paquetes
            """
        ).fetchone()
    return {
        "cotizaciones": int((cotizaciones or (0,))[0] or 0),
        "confirmadas": int((cotizaciones or (0, 0))[1] or 0),
        "valor_cotizado": float((cotizaciones or (0, 0, 0))[2] or 0),
        "paquetes": int((paquetes or (0,))[0] or 0),
        "manipulacion": float((paquetes or (0, 0))[1] or 0),
    }


@st.cache_data(ttl=10, show_spinner=False)
def cargar_notificaciones_cliente(casillero, incluir_ocultas=False):
    cas = formatear_casillero(casillero)
    if not cas:
        return []
    condicion_visible = "" if incluir_ocultas else "AND visible = TRUE"
    with get_db() as conn:
        return conn.execute(
            f"""
            SELECT id, tracking, tipo, prioridad, titulo, mensaje, canal,
                   leida, visible, fecha_creacion, creado_por
            FROM notificaciones_cliente
            WHERE codigo_casillero = ? {condicion_visible}
            ORDER BY fecha_creacion DESC, id DESC
            LIMIT 100
            """,
            (cas,),
        ).fetchall()


def consultar_casos_soporte_db(casillero):
    cas = formatear_casillero(casillero)
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, tracking, categoria, asunto, detalle, estado, prioridad,
                   respuesta_operador, creado_por, fecha_creacion, fecha_actualizacion
            FROM casos_cliente
            WHERE codigo_casillero = ?
            ORDER BY fecha_actualizacion DESC, id DESC
            LIMIT 100
            """,
            (cas,),
        ).fetchall()


def obtener_casos_soporte(casillero):
    """Caché local de sesión, independiente del decorador defectuoso de Streamlit."""
    cas = formatear_casillero(casillero)
    clave = "_ccm_soporte_casos_cache_v3"
    ahora = time.monotonic()
    cache = st.session_state.get(clave)
    if (
        cache and cache.get("casillero") == cas
        and ahora - float(cache.get("creado", 0)) < 10
    ):
        return cache["datos"]
    datos = consultar_casos_soporte_db(cas)
    st.session_state[clave] = {"casillero": cas, "creado": ahora, "datos": datos}
    return datos


def consultar_hilo_soporte_db(caso_id, casillero):
    """Carga el hilo sin permitir consultar casos de otro casillero."""
    cas = formatear_casillero(casillero)
    if not cas:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, autor_tipo, autor_nombre, mensaje, fecha_creacion
            FROM casos_mensajes
            WHERE caso_id = ? AND codigo_casillero = ?
            ORDER BY fecha_creacion ASC, id ASC
            """,
            (int(caso_id), cas),
        ).fetchall()


def obtener_hilo_soporte(caso_id, casillero):
    """Reutiliza el hilo durante la conversación sin usar st.cache_data."""
    cas = formatear_casillero(casillero)
    caso = int(caso_id)
    clave = "_ccm_soporte_hilo_cache_v3"
    ahora = time.monotonic()
    cache = st.session_state.get(clave)
    if (
        cache and cache.get("casillero") == cas and cache.get("caso_id") == caso
        and ahora - float(cache.get("creado", 0)) < 10
    ):
        return cache["datos"]
    datos = consultar_hilo_soporte_db(caso, cas)
    st.session_state[clave] = {
        "casillero": cas, "caso_id": caso, "creado": ahora, "datos": datos,
    }
    return datos


def invalidar_cache_soporte():
    st.session_state.pop("_ccm_soporte_casos_cache_v3", None)
    st.session_state.pop("_ccm_soporte_hilo_cache_v3", None)


def agregar_mensaje_caso(caso_id, casillero, autor_tipo, mensaje, nuevo_estado=None):
    """Agrega una respuesta al hilo y verifica la propiedad del caso en la misma transacción."""
    cas = formatear_casillero(casillero)
    texto = str(mensaje or "").strip()[:1500]
    autor = "operador" if str(autor_tipo).lower() == "operador" else "cliente"
    if not cas or not texto:
        return False
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, respuesta_operador, fecha_actualizacion FROM casos_cliente "
            "WHERE id = ? AND codigo_casillero = ?",
            (int(caso_id), cas),
        )
        caso_existente = cursor.fetchone()
        if caso_existente is None:
            return False
        # Conserva la respuesta del esquema anterior antes de actualizar el campo
        # de compatibilidad con la contestación más reciente.
        respuesta_anterior = str(caso_existente[1] or "").strip()
        if autor == "operador" and respuesta_anterior:
            cursor.execute(
                "SELECT COUNT(*) FROM casos_mensajes "
                "WHERE caso_id=? AND codigo_casillero=? AND autor_tipo='operador'",
                (int(caso_id), cas),
            )
            if int((cursor.fetchone() or (0,))[0] or 0) == 0:
                cursor.execute(
                    """
                    INSERT INTO casos_mensajes (
                        caso_id, codigo_casillero, autor_tipo, autor_nombre,
                        mensaje, fecha_creacion
                    ) VALUES (?, ?, 'operador', 'Equipo CCM', ?, ?)
                    """,
                    (
                        int(caso_id), cas, respuesta_anterior,
                        str(caso_existente[2] or fecha),
                    ),
                )
        cursor.execute(
            """
            INSERT INTO casos_mensajes (
                caso_id, codigo_casillero, autor_tipo, autor_nombre, mensaje, fecha_creacion
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(caso_id), cas, autor,
                st.session_state.get("usuario") or ("Cliente" if autor == "cliente" else "Operador"),
                texto, fecha,
            ),
        )
        estado = nuevo_estado or ("Abierto" if autor == "cliente" else "Esperando cliente")
        if autor == "operador":
            cursor.execute(
                "UPDATE casos_cliente SET estado=?, respuesta_operador=?, fecha_actualizacion=? "
                "WHERE id=? AND codigo_casillero=?",
                (estado, texto, fecha, int(caso_id), cas),
            )
        else:
            cursor.execute(
                "UPDATE casos_cliente SET estado=?, fecha_actualizacion=? "
                "WHERE id=? AND codigo_casillero=?",
                (estado, fecha, int(caso_id), cas),
            )
    invalidar_cache_soporte()
    return True


def abrir_conversacion_caso_cliente(caso_id):
    st.session_state["cliente_caso_activo"] = int(caso_id)


def cerrar_conversacion_caso_cliente():
    st.session_state.pop("cliente_caso_activo", None)


def cambiar_estado_caso_desde_cliente(caso_id, casillero, nuevo_estado):
    """Permite cerrar o reabrir únicamente un caso perteneciente al cliente."""
    estado = str(nuevo_estado or "").strip()
    if estado not in ("Cerrado", "Abierto"):
        return False
    cas = formatear_casillero(casillero)
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT estado FROM casos_cliente WHERE id=? AND codigo_casillero=?",
            (int(caso_id), cas),
        )
        fila = cursor.fetchone()
        if fila is None:
            return False
        if str(fila[0]) == estado:
            return True
        cursor.execute(
            "UPDATE casos_cliente SET estado=?, fecha_actualizacion=? "
            "WHERE id=? AND codigo_casillero=?",
            (estado, fecha, int(caso_id), cas),
        )
        texto_evento = (
            "El cliente cerró esta solicitud."
            if estado == "Cerrado" else "El cliente reabrió esta solicitud."
        )
        cursor.execute(
            """
            INSERT INTO casos_mensajes (
                caso_id, codigo_casillero, autor_tipo, autor_nombre, mensaje, fecha_creacion
            ) VALUES (?, ?, 'sistema', 'Sistema', ?, ?)
            """,
            (int(caso_id), cas, texto_evento, fecha),
        )
    invalidar_cache_soporte()
    return True


def crear_notificacion_cliente(
    casillero, titulo, mensaje, tipo="Información", prioridad="Normal",
    tracking="", canal="Portal", creado_por=None,
):
    cas = formatear_casillero(casillero)
    titulo_limpio = str(titulo or "").strip()
    mensaje_limpio = str(mensaje or "").strip()
    if not cas or not titulo_limpio or not mensaje_limpio:
        return False
    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO notificaciones_cliente (
                codigo_casillero, tracking, tipo, prioridad, titulo, mensaje,
                canal, leida, visible, fecha_creacion, creado_por
            ) VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, TRUE, ?, ?)
            """,
            (
                cas, str(tracking or "").strip() or None, tipo, prioridad,
                titulo_limpio, mensaje_limpio, canal, fecha,
                creado_por or st.session_state.get("usuario") or "sistema",
            ),
        )
    cargar_notificaciones_cliente.clear()
    return True


def marcar_notificacion_cliente(notificacion_id, casillero, visible=True):
    """Aplica la acción solo a una notificación perteneciente a la sesión cliente."""
    cas = formatear_casillero(casillero)
    with get_db() as conn:
        conn.execute(
            "UPDATE notificaciones_cliente SET leida = TRUE, visible = ? "
            "WHERE id = ? AND codigo_casillero = ?",
            (bool(visible), int(notificacion_id), cas),
        )
    cargar_notificaciones_cliente.clear()


def marcar_todas_notificaciones_cliente(casillero):
    """Marca únicamente las notificaciones visibles del cliente autenticado."""
    cas = formatear_casillero(casillero)
    if not cas:
        return
    with get_db() as conn:
        conn.execute(
            "UPDATE notificaciones_cliente SET leida = TRUE "
            "WHERE codigo_casillero = ? AND visible = TRUE",
            (cas,),
        )
    cargar_notificaciones_cliente.clear()


def pintar_centro_notificaciones_cliente(casillero):
    notificaciones = cargar_notificaciones_cliente(casillero)
    if not notificaciones:
        return
    no_leidas = sum(1 for fila in notificaciones if not bool(fila[7]))
    st.markdown(
        """
        <style>
        .st-key-centro_notificaciones_cliente {
            margin: 10px 0 16px;
        }
        .st-key-centro_notificaciones_cliente details {
            overflow: hidden;
            background: #ffffff !important;
            border: 1px solid #d9e2ec !important;
            border-radius: 8px !important;
            box-shadow: 0 3px 12px rgba(15, 23, 42, .06) !important;
        }
        .st-key-centro_notificaciones_cliente details > summary {
            min-height: 50px !important;
            padding: 0 16px !important;
            background: #ffffff !important;
            border: 0 !important;
        }
        .st-key-centro_notificaciones_cliente details > summary:hover {
            background: #f8fafc !important;
        }
        .st-key-centro_notificaciones_cliente details > summary * {
            color: #0f172a !important;
            -webkit-text-fill-color: #0f172a !important;
            font-weight: 800 !important;
            opacity: 1 !important;
        }
        .st-key-centro_notificaciones_cliente details[open] > summary {
            border-bottom: 1px solid #e2e8f0 !important;
        }
        .st-key-centro_notificaciones_cliente [data-testid="stExpanderDetails"] {
            padding: 14px 16px 12px !important;
            background: #f8fafc !important;
        }
        .st-key-centro_notificaciones_cliente [data-testid="stHorizontalBlock"] {
            align-items: center;
        }
        .st-key-centro_notificaciones_cliente .stButton > button {
            min-height: 36px;
            border: 1px solid #cbd5e1 !important;
            border-radius: 7px !important;
            background: #ffffff !important;
            color: #0f172a !important;
            font-size: .75rem !important;
            font-weight: 750 !important;
            box-shadow: none !important;
        }
        .st-key-centro_notificaciones_cliente .stButton > button:hover {
            border-color: #0757c8 !important;
            color: #0757c8 !important;
            background: #f0f7ff !important;
        }
        @media (max-width: 700px) {
            .st-key-centro_notificaciones_cliente details > summary {
                min-height: 46px !important;
                padding: 0 12px !important;
            }
            .st-key-centro_notificaciones_cliente [data-testid="stExpanderDetails"] {
                padding: 10px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    etiqueta = f"Notificaciones · {no_leidas} nuevas" if no_leidas else "Notificaciones"
    with st.container(key="centro_notificaciones_cliente"):
        # Siempre inicia contraído. El usuario decide cuándo consultar la bandeja.
        with st.expander(etiqueta, expanded=False):
            encabezado, accion_global = st.columns([4, 1.35])
            with encabezado:
                st.markdown(
                    '<div style="color:#0f172a;font-size:.9rem;font-weight:800;">'
                    'Centro de notificaciones</div>'
                    '<div style="color:#64748b;font-size:.74rem;margin-top:2px;">'
                    'Actualizaciones importantes de su casillero y sus envíos.</div>',
                    unsafe_allow_html=True,
                )
            with accion_global:
                if no_leidas:
                    st.button(
                        "Marcar todas leídas",
                        key=f"notif_read_all_{formatear_casillero(casillero)}",
                        use_container_width=True,
                        on_click=marcar_todas_notificaciones_cliente,
                        args=(casillero,),
                    )

            st.markdown('<div style="height:5px"></div>', unsafe_allow_html=True)
            for fila in notificaciones[:8]:
                nid, tracking, tipo, prioridad, titulo, mensaje, canal, leida, _, fecha, _ = fila
                prioridad_limpia = str(prioridad or "Normal")
                color_prioridad = {
                    "Urgente": ("#b91c1c", "#fef2f2"),
                    "Alta": ("#b45309", "#fffbeb"),
                    "Normal": ("#0757c8", "#eff6ff"),
                }.get(prioridad_limpia, ("#475569", "#f1f5f9"))
                color, fondo = color_prioridad
                detalle, accion = st.columns([4.6, 1.15])
                with detalle:
                    st.markdown(
                        f'<div style="margin:4px 0;padding:12px 14px;background:#fff;'
                        f'border:1px solid #e2e8f0;border-left:4px solid {color};border-radius:7px;">'
                        f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">'
                        f'<b style="color:#0f172a;font-size:.84rem;line-height:1.35;">{html.escape(str(titulo))}</b>'
                        f'<span style="flex:none;color:{color};background:{fondo};border:1px solid {color}33;'
                        f'border-radius:999px;padding:2px 7px;font-size:.62rem;font-weight:800;">'
                        f'{html.escape(prioridad_limpia)}</span></div>'
                        f'<div style="margin-top:5px;color:#475569;font-size:.77rem;line-height:1.45;">'
                        f'{html.escape(str(mensaje))}</div>'
                        f'<div style="display:flex;flex-wrap:wrap;gap:5px 10px;margin-top:7px;'
                        f'color:#64748b;font-size:.66rem;">'
                        f'<span>{html.escape(str(tipo))} · {html.escape(str(canal))}</span>'
                        f'<span>{html.escape(str(fecha))}</span>'
                        f'{f"<span>Tracking: {html.escape(str(tracking))}</span>" if tracking else ""}'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )
                with accion:
                    if not bool(leida):
                        st.button(
                            "Marcar leída", key=f"notif_read_{nid}",
                            use_container_width=True,
                            on_click=marcar_notificacion_cliente,
                            args=(nid, casillero, True),
                        )
                    else:
                        st.markdown(
                            '<div style="text-align:center;color:#15803d;font-size:.72rem;'
                            'font-weight:800;padding:11px 4px;">Leída</div>',
                            unsafe_allow_html=True,
                        )
            if len(notificaciones) > 8:
                st.caption(f"Mostrando las 8 notificaciones más recientes de {len(notificaciones)}.")


def hidratar_cotizaciones_sesion(casillero, filas_db=None, confirmaciones=None):
    cas, lista = bolsa_cotizaciones_sesion(casillero)
    if not cas:
        return
    conocidos = {int(r.get("id") or 0) for r in lista}
    try:
        if filas_db is None:
            filas_db = cargar_cotizaciones_db(cas)
        if confirmaciones is None:
            confirmaciones = {
                int(fila[0]): fila[9]
                for fila in filas_db
                if len(fila) > 9 and fila[9]
            }
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
    except Exception as exc:
        registrar_error_datos(exc, "Hidratación de cotizaciones")


def filas_cotizaciones_casillero(casillero, ahora=None):
    cas = formatear_casillero(casillero or "")
    ahora = ahora or obtener_tiempo_honduras()
    try:
        filas_db = cargar_cotizaciones_db(cas)
        confirmaciones_db = {
            int(fila[0]): fila[9]
            for fila in filas_db
            if len(fila) > 9 and fila[9]
        }
    except Exception as exc:
        registrar_error_datos(exc, "Carga del historial de cotizaciones")
        filas_db = []
        confirmaciones_db = {}
    hidratar_cotizaciones_sesion(cas, filas_db=filas_db, confirmaciones=confirmaciones_db)
    by_id = {}
    for fila in filas_db:
        by_id[int(fila[0])] = fila[:9]
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
    variantes = coincidencias_casillero(cas)
    marcadores = ",".join("?" * len(variantes))
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
                f"SELECT codigo_casillero, IFNULL(confirmada, 0), fecha_confirmacion, "
                f"COALESCE(estado, 'emitida') "
                f"FROM cotizaciones WHERE id = ? AND codigo_casillero IN ({marcadores})",
                (cid, *variantes),
            )
            fila = cur.fetchone()
            if fila is None:
                raise sqlite3.OperationalError("La cotización no existe o no pertenece a este casillero.")
            codigo_guardado, ya_confirmada, fecha_guardada, estado_guardado = fila
            if str(estado_guardado) in ("cancelada", "vencida", "en_revision"):
                raise sqlite3.OperationalError(
                    f"La cotización está {estado_guardado} y requiere autorización del operador."
                )
            cur.execute(
                f"""
                UPDATE cotizaciones
                SET confirmada = TRUE, fecha_confirmacion = ?, estado = 'pendiente_revision'
                WHERE id = ? AND codigo_casillero IN ({marcadores})
                  AND COALESCE(confirmada, FALSE) = FALSE
                  AND COALESCE(estado, 'emitida') = 'emitida'
                """,
                (ahora, cid, *variantes),
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
        print(detalle, flush=True)
        st.session_state["ultimo_error_confirmacion"] = "La base de datos no pudo completar la operación."
        actualizado = False
    except Exception as exc:
        detalle = f"Error al confirmar CCM-COT-{cid:05d}: {exc}"
        print(detalle, flush=True)
        st.session_state["ultimo_error_confirmacion"] = "Ocurrió un error inesperado al confirmar."
        actualizado = False
    finally:
        # La siguiente ejecución debe leer la confirmación recién persistida,
        # nunca el resultado anterior guardado por la caché.
        cargar_cotizaciones_db.clear()
        cargar_estados_cotizaciones_db.clear()
        cargar_confirmaciones_db.clear()
        cargar_cotizaciones_confirmadas_admin.clear()
        cargar_resumen_operativo_admin.clear()
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
    snapshot_tarifa = json.dumps(
        {
            "tipo_carga": snap.get("tipo_carga"),
            "dimensiones_cm": [snap.get("al"), snap.get("an"), snap.get("la")],
            "peso_lb": snap.get("peso_lb"),
            "volumen_m3": snap.get("vol_m3"),
            "total_usd": snap.get("total_usd"),
            "detalle": snap.get("detalle_tarifa"),
            "destino": snap.get("destino"),
            "fecha": f_hoy_sql,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    id_generado = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO cotizaciones (
                    codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, volumen_ft3,
                    total_usd, fecha, confirmada, fecha_creacion, estado, tipo_carga,
                    detalle_tarifa, destino_entrega, tarifa_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, 'emitida', ?, ?, ?, ?)
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
                    snap.get("tipo_carga"),
                    snap.get("detalle_tarifa"),
                    snap.get("destino"),
                    snapshot_tarifa,
                ),
            )
            id_generado = cur.lastrowid
            conn.commit()
        cargar_cotizaciones_db.clear()
        cargar_estados_cotizaciones_db.clear()
        cargar_confirmaciones_db.clear()
        cargar_resumen_operativo_admin.clear()
    except Exception as exc:
        registrar_error_datos(exc, "Emisión de cotización")
        st.session_state["_ccm_emit_error"] = (
            "No fue posible guardar la cotización. No se generó ningún documento; "
            "intente nuevamente."
        )
        return
    if not id_generado:
        st.session_state["_ccm_emit_error"] = (
            "La base de datos no devolvió el número de cotización. Intente nuevamente."
        )
        return
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
    """Marca cotizaciones vencidas sin destruir su historial operativo."""
    ahora = ahora or obtener_tiempo_honduras()
    if USA_SUPABASE:
        limite_pendiente = (ahora - VIGENCIA_COTIZACION).strftime("%Y-%m-%d %H:%M:%S")
        limite_confirmada = (ahora - VIGENCIA_COTIZACION_CONFIRMADA).strftime("%Y-%m-%d %H:%M:%S")
        expresion_pendiente = "COALESCE(NULLIF(fecha_creacion::text, ''), fecha::text)"
        expresion_confirmada = (
            "COALESCE(NULLIF(fecha_confirmacion::text, ''), "
            "NULLIF(fecha_creacion::text, ''), fecha::text)"
        )
        patron_iso = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_xact_lock(?)", (74219031,))
            bloqueo = cur.fetchone()
            if not bloqueo or not bloqueo[0]:
                return 0
            cur.execute(
                f"""
                UPDATE cotizaciones
                SET estado = 'vencida'
                WHERE COALESCE(estado, '') <> 'vencida'
                AND ((
                    COALESCE(confirmada, FALSE) = FALSE
                    AND {expresion_pendiente} ~ ?
                    AND {expresion_pendiente} < ?
                ) OR (
                    COALESCE(confirmada, FALSE) = TRUE
                    AND {expresion_confirmada} ~ ?
                    AND {expresion_confirmada} < ?
                ))
                """,
                (patron_iso, limite_pendiente, patron_iso, limite_confirmada),
            )
            eliminadas = max(0, int(cur.rowcount or 0))
            conn.commit()
        if eliminadas:
            cargar_cotizaciones_db.clear()
            cargar_estados_cotizaciones_db.clear()
            cargar_confirmaciones_db.clear()
            cargar_cotizaciones_confirmadas_admin.clear()
            cargar_resumen_operativo_admin.clear()
        return eliminadas
    with get_db() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, fecha, fecha_confirmacion, IFNULL(confirmada, 0), estado FROM cotizaciones"
            )
        except sqlite3.OperationalError:
            return 0
        ids_vencidos = []
        for cid, fecha, fecha_confirmacion, confirmada, estado in cur.fetchall():
            if str(estado or "") == "vencida":
                continue
            if es_cotizacion_confirmada(confirmada):
                vencida = not cotizacion_confirmada_vigente(fecha_confirmacion or fecha, ahora)
            else:
                vencida = not cotizacion_vigente(fecha, ahora)
            if vencida:
                ids_vencidos.append(cid)
        if not ids_vencidos:
            return 0
        cur.executemany(
            "UPDATE cotizaciones SET estado = 'vencida' WHERE id = ? AND estado <> 'vencida'",
            [(cid,) for cid in ids_vencidos],
        )
        conn.commit()
        return len(ids_vencidos)


@st.cache_resource(show_spinner=False)
def estado_mantenimiento_db():
    return {"lock": threading.Lock(), "ultima_purga": 0.0}


def purgar_cotizaciones_si_corresponde(ahora=None, intervalo_s=300):
    """Ejecuta mantenimiento una vez por proceso; PostgreSQL añade un bloqueo global."""
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
        "Modalidad de entrega",
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
    """Aviso de recepción y dirección oficial del almacén en China."""
    cas_txt = formatear_casillero(casillero) or "su casillero"
    msg = urllib.parse.quote(
        f"Hola Centro de Cerámicas y Más, soy del casillero {cas_txt}. "
        "Notifico con al menos 3 días de anticipación el envío de una carga a su almacén en China.\n\n"
        "Fecha estimada de llegada:\n"
        "Proveedor/remitente:\n"
        "Cantidad de bultos:\n"
        "Número de seguimiento:"
    )
    url_wa = f"https://wa.me/50495771099?text={msg}"
    st.markdown(
        f'<div class="promo-ad-card">'
        f'<div class="promo-ad-top">'
        f'<div class="promo-ad-heading">'
        f'<div class="promo-ad-kicker"><span class="promo-ad-live"></span> RECEPCIÓN DE CARGA · CHINA</div>'
        f'<div class="promo-ad-title">Avísanos antes de despachar</div>'
        f'<div class="promo-ad-subtitle">Una notificación a tiempo asegura la recepción de su mercancía.</div>'
        f'</div>'
        f'<div class="promo-ad-deadline" aria-label="Notificar con tres días de anticipación"><strong>3</strong><span>DÍAS</span><small>de anticipación</small></div>'
        f'</div>'
        f'<div class="promo-ad-alert"><span aria-hidden="true">!</span><div><b>Notificación obligatoria</b>'
        f'<p>Toda carga debe notificarse por WhatsApp antes de enviarse. <strong>Sin aviso previo, la carga no será recibida.</strong></p></div></div>'
        f'<div class="promo-ad-meta">'
        f'<div><small>SU CASILLERO</small><b>{html.escape(cas_txt)}</b></div>'
        f'<div><small>WHATSAPP DE RECEPCIÓN</small><b>+504 9577-1099</b></div>'
        f'</div>'
        f'<div class="promo-ad-addresses" aria-label="Dirección oficial del almacén en China">'
        f'<div class="promo-ad-address-head"><span aria-hidden="true">⌖</span><div><small>ALMACÉN EN SHANGHÁI</small><b>Dirección oficial de recepción</b></div></div>'
        f'<div class="promo-ad-address promo-ad-address-primary"><small>中文地址 · CHINO</small><b lang="zh">上海市浦东新区合庆镇人民塘路1333号</b></div>'
        f'<div class="promo-ad-translations">'
        f'<div class="promo-ad-address"><small>ESPAÑOL</small><b>N.º 1333, calle Renmintang, pueblo de Heqing, distrito nuevo de Pudong, Shanghái, China.</b></div>'
        f'<div class="promo-ad-address"><small>ENGLISH</small><b lang="en">No. 1333 Renmintang Road, Heqing Town, Pudong New Area, Shanghai, China.</b></div>'
        f'</div>'
        f'</div>'
        f'<a class="promo-ad-cta" href="{url_wa}" target="_blank" rel="noopener noreferrer">'
        f'<span class="promo-ad-cta-icon" aria-hidden="true">WA</span><span class="promo-ad-cta-copy"><b>Notificar carga por WhatsApp</b><small>+504 9577-1099 · mensaje preparado para completar</small></span><span class="promo-ad-cta-arrow" aria-hidden="true">→</span></a>'
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
    cas_formato = formatear_casillero(st.session_state.get("casillero", "")) or "mi casillero"
    mensaje_formato = urllib.parse.quote(
        f"Hola Centro de Cerámicas y Más, soy del casillero {cas_formato}. "
        "Adjunto el formato Excel de información del producto completado por mi fabricante para su revisión."
    )
    url_whatsapp_formato = f"https://wa.me/50495771099?text={mensaje_formato}"
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
        casos_cliente = obtener_casos_soporte(cas_formato)
        caso_activo = st.session_state.get("cliente_caso_activo")
        if caso_activo and not any(int(caso[0]) == int(caso_activo) for caso in casos_cliente):
            st.session_state.pop("cliente_caso_activo", None)
            caso_activo = None
        casos_pendientes = sum(
            1 for caso in casos_cliente if str(caso[5]) not in ("Resuelto", "Cerrado")
        )
        etiqueta_soporte = (
            f"Solicitudes y soporte · {casos_pendientes} activas"
            if casos_pendientes else "Solicitudes y soporte"
        )
        with st.container(key="soporte_cliente_panel"):
            st.markdown(
                """
                <style>
                .st-key-soporte_cliente_panel details { background:#fff !important; border:1px solid #dbe3ee !important; border-radius:8px !important; box-shadow:0 3px 12px rgba(15,23,42,.05) !important; }
                .st-key-soporte_cliente_panel details > summary { min-height:50px !important; padding:0 16px !important; background:#fff !important; }
                .st-key-soporte_cliente_panel details > summary * { color:#0f172a !important; -webkit-text-fill-color:#0f172a !important; font-weight:800 !important; opacity:1 !important; }
                .st-key-soporte_cliente_panel [data-testid="stExpanderDetails"] { padding:14px 16px 16px !important; background:#f8fafc !important; }
                .support-case-card { min-height:74px; padding:11px 13px; background:#fff; border:1px solid #e2e8f0; border-left:4px solid #0757c8; border-radius:7px; box-sizing:border-box; }
                .support-case-card.closed { border-left-color:#94a3b8; background:#fbfcfe; }
                .support-case-card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
                .support-case-card b { display:block; color:#0f172a; font-size:.82rem; line-height:1.35; }
                .support-case-card span { display:block; margin-top:5px; color:#64748b; font-size:.68rem; line-height:1.35; }
                .support-case-card .support-card-status { flex:none; display:inline-block; margin:0; padding:3px 7px; color:#166534; background:#dcfce7; border-radius:999px; font-size:.61rem; font-weight:850; }
                .support-case-card .support-card-status.closed { color:#475569; background:#e2e8f0; }
                .support-thread-head { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:14px 15px; margin:10px 0 12px; background:#fff; border:1px solid #d8e2ee; border-left:4px solid #0757c8; border-radius:8px; }
                .support-thread-identity { display:flex; align-items:center; gap:11px; min-width:0; }
                .support-thread-icon { display:grid !important; place-items:center; flex:0 0 38px; width:38px; height:38px; margin:0 !important; color:#0757c8 !important; background:#eaf3ff; border-radius:7px; font-size:.68rem !important; font-weight:900; }
                .support-thread-head small { display:block; margin-bottom:2px; color:#0757c8; font-size:.61rem; font-weight:850; text-transform:uppercase; }
                .support-thread-head b { display:block; overflow-wrap:anywhere; color:#0f172a; font-size:.87rem; line-height:1.3; }
                .support-thread-head .support-thread-meta { display:block; margin-top:4px; color:#64748b; font-size:.67rem; }
                .support-status { flex:none; padding:5px 9px; color:#166534; background:#dcfce7; border:1px solid #bbf7d0; border-radius:999px; font-size:.64rem; font-weight:850; }
                .support-status.closed { color:#475569; background:#f1f5f9; border-color:#cbd5e1; }
                .support-message { max-width:88%; margin:7px 0; padding:10px 12px; background:#fff; border:1px solid #dbe3ee; border-radius:7px; color:#334155; font-size:.77rem; line-height:1.45; white-space:pre-wrap; overflow-wrap:anywhere; }
                .support-message.client { margin-left:auto; background:#edf6ff; border-color:#bfd7f5; }
                .support-message.system { max-width:max-content; margin:9px auto; padding:6px 10px; color:#64748b; background:#eef2f6; border:0; border-radius:999px; font-size:.68rem; text-align:center; }
                .support-message small { display:block; margin-bottom:4px; color:#64748b; font-size:.62rem; font-weight:800; }
                .support-closed-note { margin:12px 0 8px; padding:11px 13px; color:#475569; background:#f1f5f9; border:1px solid #d8e1ea; border-radius:7px; font-size:.75rem; line-height:1.4; }
                [class*="st-key-cerrar_caso_cliente_"] .stButton > button { color:#b91c1c !important; -webkit-text-fill-color:#b91c1c !important; background:#fff !important; border-color:#fecaca !important; box-shadow:none !important; }
                [class*="st-key-cerrar_caso_cliente_"] .stButton > button:hover { background:#fef2f2 !important; border-color:#ef4444 !important; }
                .support-close-confirm { margin:9px 0; padding:11px 13px; color:#7f1d1d; background:#fef2f2; border:1px solid #fecaca; border-left:4px solid #dc2626; border-radius:7px; font-size:.74rem; }
                .st-key-soporte_cliente_panel .stButton > button { min-height:40px; border-radius:7px !important; font-weight:800 !important; }
                .st-key-soporte_cliente_panel [role="radiogroup"] { display:flex !important; gap:6px !important; padding:5px !important; margin-bottom:8px; background:#eaf0f6 !important; border:1px solid #d8e1ec !important; border-radius:8px !important; }
                .st-key-soporte_cliente_panel [role="radiogroup"] label { flex:1 !important; min-height:38px !important; margin:0 !important; padding:7px 11px !important; justify-content:center !important; background:#fff !important; border:1px solid #d7e0eb !important; border-radius:6px !important; }
                .st-key-soporte_cliente_panel [role="radiogroup"] label:has(input:checked) { background:#0757c8 !important; border-color:#0757c8 !important; }
                .st-key-soporte_cliente_panel [role="radiogroup"] label p { color:#334155 !important; -webkit-text-fill-color:#334155 !important; font-size:.74rem !important; font-weight:800 !important; }
                .st-key-soporte_cliente_panel [role="radiogroup"] label:has(input:checked) p { color:#fff !important; -webkit-text-fill-color:#fff !important; }
                [class*="st-key-soporte_composer_cliente_"] { margin-top:12px; padding:8px 10px; overflow:hidden; background:#fff; border:1px solid #cbd5e1; border-radius:8px; box-shadow:none !important; }
                [class*="st-key-soporte_composer_cliente_"] [data-testid="stHorizontalBlock"] { align-items:flex-end !important; }
                [class*="st-key-soporte_composer_cliente_"] [data-baseweb="textarea"], [class*="st-key-soporte_composer_cliente_"] [data-baseweb="textarea"] > div { border:0 !important; outline:0 !important; background:#f8fafc !important; border-radius:7px !important; box-shadow:none !important; }
                [class*="st-key-soporte_composer_cliente_"] textarea, [class*="st-key-soporte_composer_cliente_"] textarea:focus, [class*="st-key-soporte_composer_cliente_"] textarea:focus-visible { min-height:70px !important; outline:0 !important; color:#0f172a !important; background:#f8fafc !important; box-shadow:none !important; font-size:.8rem !important; }
                [class*="st-key-soporte_composer_cliente_"] .stButton > button { min-height:70px !important; height:70px !important; color:#fff !important; -webkit-text-fill-color:#fff !important; background:#0757c8 !important; border-color:#0757c8 !important; box-shadow:none !important; }
                [class*="st-key-soporte_composer_cliente_"] .stButton > button:focus, [class*="st-key-soporte_composer_cliente_"] .stButton > button:active { outline:0 !important; box-shadow:none !important; }
                @media(max-width:700px){ .support-thread-head{align-items:flex-start;flex-direction:column}.support-message{max-width:96%}.st-key-soporte_cliente_panel [data-testid="stExpanderDetails"]{padding:10px !important;} }
                </style>
                """,
                unsafe_allow_html=True,
            )
            # Al entrar en Actividad, la bandeja inicia cerrada. La conversación
            # seleccionada se conserva y solo se muestra al pulsar la flecha.
            with st.expander(etiqueta_soporte, expanded=False):
                st.markdown(
                    '<div style="color:#0f172a;font-size:.92rem;font-weight:850;">Centro de ayuda</div>'
                    '<div style="margin:2px 0 10px;color:#64748b;font-size:.73rem;">'
                    'Consulte el historial y continúe cualquier conversación sin perder respuestas.</div>',
                    unsafe_allow_html=True,
                )
                modo_soporte = st.radio(
                    "Vista de soporte", ["Mis solicitudes", "Nueva solicitud"],
                    horizontal=True, label_visibility="collapsed",
                    key="cliente_modo_soporte",
                )
                if modo_soporte == "Mis solicitudes":
                    if not casos_cliente:
                        st.info("Todavía no tiene solicitudes. Use la pestaña Nueva solicitud para contactar al equipo.")
                    else:
                        for caso in casos_cliente[:10]:
                            estado_texto = str(caso[5] or "Abierto")
                            caso_lista_cerrado = estado_texto in ("Cerrado", "Resuelto")
                            clase_caso_lista = "closed" if caso_lista_cerrado else ""
                            detalle_caso, accion_caso = st.columns([4.5, 1.35])
                            with detalle_caso:
                                st.markdown(
                                    f'<div class="support-case-card {clase_caso_lista}">'
                                    '<div class="support-case-card-head">'
                                    f'<b>#{int(caso[0]):04d} · {html.escape(str(caso[3]))}</b>'
                                    f'<span class="support-card-status {clase_caso_lista}">'
                                    f'{html.escape(estado_texto)}</span></div>'
                                    f'<span>{html.escape(str(caso[2]))} · '
                                    f'Actualizado {html.escape(str(caso[10]))}</span></div>',
                                    unsafe_allow_html=True,
                                )
                            with accion_caso:
                                st.button(
                                    "Ver historial" if caso_lista_cerrado else "Abrir conversación",
                                    key=f"cliente_abrir_caso_{caso[0]}",
                                    use_container_width=True,
                                    on_click=abrir_conversacion_caso_cliente,
                                    args=(caso[0],),
                                )

                        caso_seleccionado = next(
                            (caso for caso in casos_cliente if int(caso[0]) == int(caso_activo or 0)),
                            None,
                        )
                        if caso_seleccionado:
                            caso_id = int(caso_seleccionado[0])
                            mensajes = obtener_hilo_soporte(caso_id, cas_formato)
                            estado_caso_cliente = str(caso_seleccionado[5] or "Abierto")
                            caso_cerrado = estado_caso_cliente == "Cerrado"
                            clase_estado = "closed" if caso_cerrado else ""
                            tracking_caso = (
                                f" · Tracking: {html.escape(str(caso_seleccionado[1]))}"
                                if caso_seleccionado[1] else " · Sin tracking asociado"
                            )
                            st.markdown(
                                '<section class="support-thread-head">'
                                '<div class="support-thread-identity"><span class="support-thread-icon">SC</span><div>'
                                f'<small>Solicitud #{caso_id:04d}</small>'
                                f'<b>{html.escape(str(caso_seleccionado[3]))}</b>'
                                f'<span class="support-thread-meta">{html.escape(str(caso_seleccionado[2]))}'
                                f'{tracking_caso} · Actualizado {html.escape(str(caso_seleccionado[10]))}</span>'
                                f'</div></div><span class="support-status {clase_estado}">'
                                f'{html.escape(estado_caso_cliente)}</span></section>',
                                unsafe_allow_html=True,
                            )
                            st.markdown(
                                '<div class="support-message client"><small>Usted · solicitud inicial</small>'
                                f'{html.escape(str(caso_seleccionado[4]))}</div>',
                                unsafe_allow_html=True,
                            )
                            tiene_respuesta_nueva = any(str(m[1]) == "operador" for m in mensajes)
                            if caso_seleccionado[7] and not tiene_respuesta_nueva:
                                st.markdown(
                                    '<div class="support-message"><small>Equipo CCM · respuesta anterior</small>'
                                    f'{html.escape(str(caso_seleccionado[7]))}</div>',
                                    unsafe_allow_html=True,
                                )
                            for mensaje in mensajes:
                                tipo_autor = str(mensaje[1])
                                if tipo_autor == "cliente":
                                    clase, autor = "client", "Usted"
                                elif tipo_autor == "sistema":
                                    clase, autor = "system", "Sistema"
                                else:
                                    clase, autor = "", "Equipo CCM"
                                st.markdown(
                                    f'<div class="support-message {clase}"><small>{autor} · '
                                    f'{html.escape(str(mensaje[4]))}</small>{html.escape(str(mensaje[3]))}</div>',
                                    unsafe_allow_html=True,
                                )
                            clave_flash_cliente = f"_flash_cliente_caso_{caso_id}"
                            mensaje_flash_cliente = st.session_state.pop(clave_flash_cliente, "")
                            if mensaje_flash_cliente:
                                st.toast(mensaje_flash_cliente)
                            clave_respuesta_cliente = f"cliente_responder_caso_{caso_id}"
                            clave_limpiar_cliente = f"_limpiar_cliente_caso_{caso_id}"
                            if st.session_state.pop(clave_limpiar_cliente, False):
                                st.session_state.pop(clave_respuesta_cliente, None)
                            if not caso_cerrado:
                                with st.container(key=f"soporte_composer_cliente_{caso_id}"):
                                    campo_mensaje, accion_enviar = st.columns([5, 1.15], gap="small")
                                    with campo_mensaje:
                                        respuesta_cliente = st.text_area(
                                            "Mensaje para soporte",
                                            height=70,
                                            placeholder="Escriba un mensaje...",
                                            key=clave_respuesta_cliente,
                                            label_visibility="collapsed",
                                        )
                                    with accion_enviar:
                                        enviar_respuesta_cliente = st.button(
                                            "Enviar", type="primary",
                                            key=f"cliente_enviar_respuesta_{caso_id}",
                                            use_container_width=True,
                                        )
                                if enviar_respuesta_cliente:
                                    if agregar_mensaje_caso(
                                        caso_id, cas_formato, "cliente", respuesta_cliente, "Abierto"
                                    ):
                                        st.session_state[clave_limpiar_cliente] = True
                                        st.session_state[clave_flash_cliente] = "Mensaje enviado correctamente."
                                        st.rerun()
                                    else:
                                        st.warning("Escriba un mensaje antes de enviarlo.")

                                ocultar_col, cerrar_col = st.columns([1, 1], gap="small")
                                with ocultar_col:
                                    st.button(
                                        "Cerrar vista", key=f"cliente_cerrar_hilo_{caso_id}",
                                        use_container_width=True,
                                        on_click=cerrar_conversacion_caso_cliente,
                                    )
                                with cerrar_col:
                                    with st.container(key=f"cerrar_caso_cliente_{caso_id}"):
                                        if st.button(
                                            "Cerrar caso", key=f"cliente_solicitar_cierre_{caso_id}",
                                            use_container_width=True,
                                        ):
                                            st.session_state[f"_confirmar_cierre_caso_{caso_id}"] = True
                                clave_confirmar = f"_confirmar_cierre_caso_{caso_id}"
                                if st.session_state.get(clave_confirmar):
                                    st.markdown(
                                        '<div class="support-close-confirm"><b>Confirmar cierre</b><br>'
                                        'La solicitud quedará finalizada. Podrá reabrirla más adelante si necesita continuar.</div>',
                                        unsafe_allow_html=True,
                                    )
                                    confirmar_col, cancelar_col = st.columns(2, gap="small")
                                    with confirmar_col:
                                        confirmar_cierre = st.button(
                                            "Confirmar cierre", type="primary",
                                            key=f"cliente_confirmar_cierre_{caso_id}",
                                            use_container_width=True,
                                        )
                                    with cancelar_col:
                                        cancelar_cierre = st.button(
                                            "Cancelar", key=f"cliente_cancelar_cierre_{caso_id}",
                                            use_container_width=True,
                                        )
                                    if confirmar_cierre and cambiar_estado_caso_desde_cliente(
                                        caso_id, cas_formato, "Cerrado"
                                    ):
                                        st.session_state.pop(clave_confirmar, None)
                                        st.session_state[clave_flash_cliente] = "Caso cerrado correctamente."
                                        st.rerun()
                                    if cancelar_cierre:
                                        st.session_state.pop(clave_confirmar, None)
                                        st.rerun()
                            else:
                                st.markdown(
                                    '<div class="support-closed-note"><b>Caso cerrado.</b> '
                                    'La conversación se conserva como historial. Reabra el caso para enviar un mensaje nuevo.</div>',
                                    unsafe_allow_html=True,
                                )
                                reabrir_col, ocultar_col = st.columns(2, gap="small")
                                with reabrir_col:
                                    reabrir_caso = st.button(
                                        "Reabrir caso", type="primary",
                                        key=f"cliente_reabrir_caso_{caso_id}", use_container_width=True,
                                    )
                                with ocultar_col:
                                    st.button(
                                        "Cerrar vista", key=f"cliente_cerrar_hilo_{caso_id}",
                                        use_container_width=True,
                                        on_click=cerrar_conversacion_caso_cliente,
                                    )
                                if reabrir_caso and cambiar_estado_caso_desde_cliente(
                                    caso_id, cas_formato, "Abierto"
                                ):
                                    st.session_state[clave_flash_cliente] = "Caso reabierto correctamente."
                                    st.rerun()
                if modo_soporte == "Nueva solicitud":
                    st.caption("Cree una solicitud nueva solamente cuando no corresponda continuar un caso existente.")
                    paquetes_soporte = cargar_paquetes_db(cas_formato)
                    tracking_soporte = st.selectbox(
                        "Envío relacionado",
                        ["Sin tracking"] + [str(p[0]) for p in paquetes_soporte],
                        key="cliente_caso_tracking",
                    )
                    categoria_soporte = st.selectbox(
                        "Categoría",
                        ["Consulta", "Pago", "Documentos", "Demora", "Daño o faltante", "Datos de cuenta"],
                        key="cliente_caso_categoria",
                    )
                    asunto_soporte = st.text_input("Asunto", max_chars=120, key="cliente_caso_asunto")
                    detalle_soporte = st.text_area(
                        "Detalle", max_chars=1500, height=90, key="cliente_caso_detalle"
                    )
                    if st.button("Crear solicitud", type="primary", key="cliente_caso_enviar"):
                        if not asunto_soporte.strip() or not detalle_soporte.strip():
                            st.warning("Escriba el asunto y el detalle de la solicitud.")
                        else:
                            fecha_caso = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                            with get_db() as conn:
                                cursor = conn.cursor()
                                parametros_caso = (
                                    cas_formato,
                                    None if tracking_soporte == "Sin tracking" else tracking_soporte,
                                    categoria_soporte, asunto_soporte.strip(), detalle_soporte.strip(),
                                    fecha_caso, fecha_caso,
                                )
                                sentencia_caso = """
                                    INSERT INTO casos_cliente (
                                        codigo_casillero, tracking, categoria, asunto, detalle,
                                        estado, prioridad, respuesta_operador, creado_por,
                                        fecha_creacion, fecha_actualizacion
                                    ) VALUES (?, ?, ?, ?, ?, 'Abierto', 'Normal', '', 'cliente', ?, ?)
                                """
                                if USA_SUPABASE:
                                    cursor.execute(sentencia_caso + " RETURNING id", parametros_caso)
                                    nuevo_caso_id = int(cursor.fetchone()[0])
                                else:
                                    cursor.execute(sentencia_caso, parametros_caso)
                                    nuevo_caso_id = int(cursor.lastrowid)
                            invalidar_cache_soporte()
                            st.session_state["cliente_caso_activo"] = nuevo_caso_id
                            st.success("Solicitud registrada. Ya puede abrirla desde Mis solicitudes.")
                            st.rerun()
        st.markdown(
            f'<section class="actividad-politicas" aria-label="Políticas de envío y productos restringidos">'
            f'<div class="actividad-politicas-copy"><span class="actividad-politicas-icon" aria-hidden="true">!</span>'
            f'<div><small>ANTES DE COMPRAR O ENVIAR</small><b>Políticas de envío y productos restringidos</b>'
            f'<p>Revise los requisitos de recepción, embalaje y transporte, además de los productos que no pueden enviarse o requieren autorización.</p></div></div>'
            f'<a class="actividad-politicas-cta" href="{html.escape(ENLACE_POLITICAS_ENVIO)}" target="_blank" rel="noopener noreferrer">'
            f'<span>Consultar políticas</span><span aria-hidden="true">→</span></a></section>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="actividad-formato-gap" aria-hidden="true"></div>'
            f'<section class="actividad-formato" aria-label="Formato Excel de información del producto">'
            f'<div class="actividad-formato-head"><span class="actividad-formato-icon" aria-hidden="true">XLS</span>'
            f'<div><small>PLANTILLA PARA EL FABRICANTE</small><b>Formato Excel de información del producto</b>'
            f'<p>Descargue la plantilla y envíela a su fabricante para que complete todos los datos solicitados del producto. '
            f'Cuando esté llena, remítala a nuestro WhatsApp para revisión.</p></div></div>'
            f'<ol class="actividad-formato-pasos"><li><span>1</span>Descargar el Excel</li>'
            f'<li><span>2</span>Completar con el fabricante</li><li><span>3</span>Enviar el archivo a CCM</li></ol>'
            f'<div class="actividad-formato-acciones">'
            f'<a class="actividad-formato-descarga" href="{html.escape(ENLACE_FORMATO_PRODUCTOS)}" target="_blank" rel="noopener noreferrer">'
            f'<span>Abrir carpeta y descargar Excel</span><span aria-hidden="true">↓</span></a>'
            f'<a class="actividad-formato-whatsapp" href="{html.escape(url_whatsapp_formato)}" target="_blank" rel="noopener noreferrer">'
            f'<span>Enviar formato por WhatsApp</span><span aria-hidden="true">→</span></a>'
            f'</div></section>',
            unsafe_allow_html=True,
        )
        espaciador_barra_inferior("safe_actividad")


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
/F1 13 Tf
0 -18 Td
(DOCUMENTO PRELIMINAR - NO ES ETIQUETA OFICIAL DE RECEPCION) Tj
/F1 9 Tf
0 -18 Td
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
(SHIP TO / WAREHOUSE IN SHANGHAI, CHINA:) Tj
/F1 9 Tf
0 -14 Td
(ATTN / RECEIVER : CHILAT / {casillero}) Tj
0 -12 Td
(ADDRESS : No. 1333 Renmintang Road, Heqing Town) Tj
0 -12 Td
(          Pudong New Area, Shanghai, China) Tj
0 -12 Td
(NOTIFY 3 DAYS BEFORE SHIPPING: WHATSAPP +504 9577-1099) Tj
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


def _codigo_barras_code39_pdf(valor, y=500, alto=62, ancho_fino=1.15):
    patrones = {
        "0":"nnnwwnwnn","1":"wnnwnnnnw","2":"nnwwnnnnw","3":"wnwwnnnnn",
        "4":"nnnwwnnnw","5":"wnnwwnnnn","6":"nnwwwnnnn","7":"nnnwnnwnw",
        "8":"wnnwnnwnn","9":"nnwwnnwnn","A":"wnnnnwnnw","B":"nnwnnwnnw",
        "C":"wnwnnwnnn","D":"nnnnwwnnw","E":"wnnnwwnnn","F":"nnwnwwnnn",
        "G":"nnnnnwwnw","H":"wnnnnwwnn","I":"nnwnnwwnn","J":"nnnnwwwnn",
        "K":"wnnnnnnww","L":"nnwnnnnww","M":"wnwnnnnwn","N":"nnnnwnnww",
        "O":"wnnnwnnwn","P":"nnwnwnnwn","Q":"nnnnnnwww","R":"wnnnnnwwn",
        "S":"nnwnnnwwn","T":"nnnnwnwwn","U":"wwnnnnnnw","V":"nwwnnnnnw",
        "W":"wwwnnnnnn","X":"nwnnwnnnw","Y":"wwnnwnnnn","Z":"nwwnwnnnn",
        "-":"nwnnnnwnw",".":"wwnnnnwnn"," ":"nwwnnnwnn","*":"nwnnwnwnn",
    }
    codigo = "*" + "".join(c for c in str(valor or "").upper() if c in patrones and c != "*") + "*"
    anchos = []
    for caracter in codigo:
        for modulo in patrones[caracter]:
            anchos.append(ancho_fino * (3 if modulo == "w" else 1))
        anchos.append(ancho_fino)
    x = max(36.0, (595.0 - sum(anchos)) / 2.0)
    comandos = ["q", "0 0 0 rg"]
    indice = 0
    for caracter in codigo:
        patron = patrones[caracter]
        for posicion, modulo in enumerate(patron):
            ancho = ancho_fino * (3 if modulo == "w" else 1)
            if posicion % 2 == 0:
                comandos.append(f"{x:.2f} {y:.2f} {ancho:.2f} {alto:.2f} re f")
            x += ancho
            indice += 1
        x += ancho_fino
    comandos.append("Q")
    return "\n".join(comandos)


def _texto_pdf_seguro(valor, max_chars=100):
    texto = re.sub(r"[\r\n\t]+", " ", str(valor or "")).strip()[:max_chars]
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@st.cache_data(ttl=900, show_spinner=False, max_entries=256)
def generar_pdf_etiqueta_oficial_bulto(
    tracking_ccm, codigo_envio, casillero, nombre, telefono, proveedor,
    numero_bulto, total_bultos, descripcion, destino_entrega,
    fecha_emision=None, version=1,
):
    tracking = _texto_pdf_seguro(str(tracking_ccm or "").upper(), 48)
    fecha_txt = _texto_pdf_seguro(
        fecha_emision or obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"), 40
    )
    barras = _codigo_barras_code39_pdf(tracking)
    destino = _texto_pdf_seguro(str(destino_entrega or "Retiro en Almacén").upper(), 90)
    envio_pdf = _texto_pdf_seguro(codigo_envio, 40)
    casillero_pdf = _texto_pdf_seguro(casillero, 30)
    nombre_pdf = _texto_pdf_seguro(nombre, 70)
    telefono_pdf = _texto_pdf_seguro(telefono, 30)
    proveedor_pdf = _texto_pdf_seguro(proveedor or "POR DEFINIR", 70)
    descripcion_pdf = _texto_pdf_seguro(descripcion, 95)
    stream = f"""{barras}
BT
/F1 16 Tf
40 728 Td
(CENTRO DE CERAMICAS Y MAS - ETIQUETA OFICIAL) Tj
/F1 9 Tf
0 -17 Td
(VERSION: {int(version)}   EMISION: {fecha_txt}) Tj
/F1 15 Tf
0 -25 Td
(TRACKING CCM: {tracking}) Tj
/F1 11 Tf
0 -18 Td
(ENVIO: {envio_pdf}   BULTO: {int(numero_bulto)} DE {int(total_bultos)}) Tj
/F1 10 Tf
0 -18 Td
(CASILLERO: {casillero_pdf}) Tj
0 -14 Td
(CLIENTE: {nombre_pdf}) Tj
0 -14 Td
(TELEFONO: {telefono_pdf}) Tj
0 -14 Td
(PROVEEDOR: {proveedor_pdf}) Tj
0 -14 Td
(DESTINO FINAL: {destino}) Tj
0 -20 Td
(DESCRIPCION: {descripcion_pdf}) Tj
0 -95 Td
/F1 9 Tf
(CODIGO CODE 39 - ESCANEAR TRACKING CCM IMPRESO) Tj
0 -22 Td
(PEGAR ESTA ETIQUETA EN AL MENOS DOS LADOS DEL BULTO.) Tj
0 -14 Td
(NO CUBRIR EL CODIGO DE BARRAS. NO REUTILIZAR ESTA ETIQUETA.) Tj
0 -22 Td
(SHIP TO: No. 1333 Renmintang Road, Heqing Town,) Tj
0 -14 Td
(Pudong New Area, Shanghai, China.) Tj
0 -14 Td
(NOTIFICAR ANTES DEL DESPACHO: WHATSAPP +504 9577-1099) Tj
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
(DIRECCION DE BODEGA EN SHANGHAI, CHINA:) Tj
/F1 8 Tf
0 -13 Td
(ATTN / CONSIGNATARIO : CHILAT / {casillero}) Tj
0 -11 Td
(DIRECCION: No. 1333 Renmintang Road, Heqing Town) Tj
0 -11 Td
(           Pudong New Area, Shanghai, China) Tj
0 -11 Td
(NOTIFICAR 3 DIAS ANTES: WHATSAPP +504 9577-1099) Tj
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
def estado_intentos_acceso():
    return {"lock": threading.Lock(), "fallos": {}}


def clave_intento_acceso(identificador):
    bruto = str(identificador or "").strip()
    normalizado = normalizar_correo(bruto) if "@" in bruto else formatear_casillero(bruto).upper()
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def comprobar_limite_acceso(identificador, max_intentos=5, ventana_s=600, bloqueo_s=300):
    estado = estado_intentos_acceso()
    clave = clave_intento_acceso(identificador)
    ahora = datetime.now().timestamp()
    with estado["lock"]:
        fallos = [t for t in estado["fallos"].get(clave, []) if ahora - t < ventana_s]
        estado["fallos"][clave] = fallos
        if len(fallos) < max_intentos:
            return True, 0
        restante = max(0, int(bloqueo_s - (ahora - fallos[-1])))
        if restante <= 0:
            estado["fallos"].pop(clave, None)
            return True, 0
        return False, restante


def registrar_fallo_acceso(identificador, ventana_s=600):
    estado = estado_intentos_acceso()
    clave = clave_intento_acceso(identificador)
    ahora = datetime.now().timestamp()
    with estado["lock"]:
        fallos = [t for t in estado["fallos"].get(clave, []) if ahora - t < ventana_s]
        fallos.append(ahora)
        estado["fallos"][clave] = fallos[-10:]
        if len(estado["fallos"]) > 5000:
            limite = ahora - ventana_s
            estado["fallos"] = {
                k: [t for t in valores if t >= limite]
                for k, valores in estado["fallos"].items()
                if any(t >= limite for t in valores)
            }


def limpiar_fallos_acceso(identificador):
    estado = estado_intentos_acceso()
    with estado["lock"]:
        estado["fallos"].pop(clave_intento_acceso(identificador), None)


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
    conn.execute("PRAGMA foreign_keys = ON")
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
                fecha_creacion TEXT,
                estado TEXT NOT NULL DEFAULT 'emitida',
                tipo_carga TEXT,
                detalle_tarifa TEXT,
                destino_entrega TEXT,
                tarifa_snapshot_json TEXT
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
        if "estado" not in columnas_cot:
            c.execute("ALTER TABLE cotizaciones ADD COLUMN estado TEXT NOT NULL DEFAULT 'emitida'")
        for columna_cot in ("tipo_carga", "detalle_tarifa", "destino_entrega", "tarifa_snapshot_json"):
            if columna_cot not in columnas_cot:
                c.execute(f"ALTER TABLE cotizaciones ADD COLUMN {columna_cot} TEXT")
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
                tipo_contenedor TEXT DEFAULT '40 HC',
                cotizacion_id INTEGER,
                recibido_bodega INTEGER NOT NULL DEFAULT 0,
                pago_confirmado INTEGER NOT NULL DEFAULT 0,
                costo_manipulacion_usd REAL NOT NULL DEFAULT 0,
                fecha_recepcion TEXT,
                ubicacion_actual TEXT,
                eta TEXT,
                proximo_paso TEXT,
                incidencia TEXT,
                visible_cliente INTEGER NOT NULL DEFAULT 1,
                estado TEXT NOT NULL,
                fecha_actualizacion TEXT NOT NULL,
                FOREIGN KEY(cotizacion_id) REFERENCES cotizaciones(id) ON DELETE RESTRICT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS eventos_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking TEXT NOT NULL,
                codigo_casillero TEXT NOT NULL,
                estado TEXT NOT NULL,
                ubicacion TEXT,
                mensaje_cliente TEXT,
                nota_interna TEXT,
                fecha_evento TEXT NOT NULL,
                creado_por TEXT,
                visible_cliente INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(tracking) REFERENCES paquetes(tracking) ON DELETE CASCADE
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


def asegurar_esquema_cotizaciones():
    """Migra cotizaciones antiguas antes de leer confirmaciones o crear índices."""
    with get_db() as conn:
        cursor = conn.cursor()
        if USA_SUPABASE:
            cursor.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cotizaciones'
                """
            )
            columnas = {str(fila[0]): str(fila[1]) for fila in cursor.fetchall()}

            if "confirmada" not in columnas:
                cursor.execute(
                    "ALTER TABLE cotizaciones "
                    "ADD COLUMN confirmada BOOLEAN NOT NULL DEFAULT FALSE"
                )
            if "fecha_confirmacion" not in columnas:
                cursor.execute(
                    "ALTER TABLE cotizaciones ADD COLUMN fecha_confirmacion TEXT"
                )
            if "estado" not in columnas:
                cursor.execute(
                    "ALTER TABLE cotizaciones ADD COLUMN estado TEXT NOT NULL DEFAULT 'emitida'"
                )
            for columna_texto in (
                "tipo_carga", "detalle_tarifa", "destino_entrega", "tarifa_snapshot_json"
            ):
                if columna_texto not in columnas:
                    cursor.execute(
                        f"ALTER TABLE cotizaciones ADD COLUMN {columna_texto} TEXT"
                    )
            if "fecha_creacion" not in columnas:
                tipo_fecha = columnas.get("fecha", "text")
                tipos_permitidos = {
                    "date": "DATE",
                    "timestamp without time zone": "TIMESTAMP WITHOUT TIME ZONE",
                    "timestamp with time zone": "TIMESTAMP WITH TIME ZONE",
                }
                tipo_sql = tipos_permitidos.get(tipo_fecha, "TEXT")
                cursor.execute(
                    f"ALTER TABLE cotizaciones ADD COLUMN fecha_creacion {tipo_sql}"
                )
                if "fecha" in columnas:
                    # La columna nueva conserva el tipo de la fecha histórica.
                    cursor.execute(
                        """
                        UPDATE cotizaciones
                        SET fecha_creacion = fecha
                        WHERE fecha_creacion IS NULL
                        """
                    )
            cursor.execute(
                """
                UPDATE cotizaciones
                SET estado = CASE
                    WHEN COALESCE(confirmada, FALSE) = TRUE THEN 'confirmada'
                    ELSE 'emitida'
                END
                WHERE estado IS NULL OR BTRIM(estado) = ''
                   OR (COALESCE(confirmada, FALSE) = TRUE AND estado = 'emitida')
                """
            )
        else:
            cursor.execute("PRAGMA table_info(cotizaciones)")
            columnas = {str(fila[1]) for fila in cursor.fetchall()}
            faltantes = {
                "confirmada": "ALTER TABLE cotizaciones ADD COLUMN confirmada INTEGER NOT NULL DEFAULT 0",
                "fecha_confirmacion": "ALTER TABLE cotizaciones ADD COLUMN fecha_confirmacion TEXT",
                "fecha_creacion": "ALTER TABLE cotizaciones ADD COLUMN fecha_creacion TEXT",
                "estado": "ALTER TABLE cotizaciones ADD COLUMN estado TEXT NOT NULL DEFAULT 'emitida'",
                "tipo_carga": "ALTER TABLE cotizaciones ADD COLUMN tipo_carga TEXT",
                "detalle_tarifa": "ALTER TABLE cotizaciones ADD COLUMN detalle_tarifa TEXT",
                "destino_entrega": "ALTER TABLE cotizaciones ADD COLUMN destino_entrega TEXT",
                "tarifa_snapshot_json": "ALTER TABLE cotizaciones ADD COLUMN tarifa_snapshot_json TEXT",
            }
            for columna, sentencia in faltantes.items():
                if columna not in columnas:
                    cursor.execute(sentencia)
            cursor.execute(
                """
                UPDATE cotizaciones
                SET fecha_creacion = fecha
                WHERE fecha_creacion IS NULL OR TRIM(fecha_creacion) = ''
                """
            )
            cursor.execute(
                """
                UPDATE cotizaciones
                SET estado = CASE WHEN IFNULL(confirmada, 0) = 1 THEN 'confirmada' ELSE 'emitida' END
                WHERE estado IS NULL OR TRIM(estado) = ''
                   OR (IFNULL(confirmada, 0) = 1 AND estado = 'emitida')
                """
            )
        conn.commit()


def asegurar_esquema_paquetes_operativo():
    """Añade los campos logísticos sin perder registros de versiones anteriores."""
    with get_db() as conn:
        cursor = conn.cursor()
        if USA_SUPABASE:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'paquetes'
                """
            )
            columnas_paquetes = {str(fila[0]) for fila in cursor.fetchall()}
            faltantes_paquetes = {
                "tipo_contenedor": "ALTER TABLE public.paquetes ADD COLUMN tipo_contenedor TEXT DEFAULT '40 HC'",
                "cotizacion_id": "ALTER TABLE public.paquetes ADD COLUMN cotizacion_id BIGINT",
                "recibido_bodega": "ALTER TABLE public.paquetes ADD COLUMN recibido_bodega BOOLEAN NOT NULL DEFAULT FALSE",
                "pago_confirmado": "ALTER TABLE public.paquetes ADD COLUMN pago_confirmado BOOLEAN NOT NULL DEFAULT FALSE",
                "costo_manipulacion_usd": "ALTER TABLE public.paquetes ADD COLUMN costo_manipulacion_usd DOUBLE PRECISION NOT NULL DEFAULT 0",
                "fecha_recepcion": "ALTER TABLE public.paquetes ADD COLUMN fecha_recepcion TEXT",
                "ubicacion_actual": "ALTER TABLE public.paquetes ADD COLUMN ubicacion_actual TEXT",
                "eta": "ALTER TABLE public.paquetes ADD COLUMN eta TEXT",
                "proximo_paso": "ALTER TABLE public.paquetes ADD COLUMN proximo_paso TEXT",
                "incidencia": "ALTER TABLE public.paquetes ADD COLUMN incidencia TEXT",
                "visible_cliente": "ALTER TABLE public.paquetes ADD COLUMN visible_cliente BOOLEAN NOT NULL DEFAULT TRUE",
            }
            for columna, sentencia in faltantes_paquetes.items():
                if columna not in columnas_paquetes:
                    cursor.execute(sentencia)
            cursor.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'eventos_tracking'"
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    CREATE TABLE public.eventos_tracking (
                    id BIGSERIAL PRIMARY KEY,
                    tracking TEXT NOT NULL REFERENCES public.paquetes(tracking) ON DELETE CASCADE,
                    codigo_casillero TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    ubicacion TEXT,
                    mensaje_cliente TEXT,
                    nota_interna TEXT,
                    fecha_evento TEXT NOT NULL,
                    creado_por TEXT,
                    visible_cliente BOOLEAN NOT NULL DEFAULT TRUE
                )
                    """
                )
            # CREATE TABLE IF NOT EXISTS no repara una tabla creada parcialmente.
            # Cada columna se verifica también de forma individual.
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'eventos_tracking'
                """
            )
            columnas_eventos = {str(fila[0]) for fila in cursor.fetchall()}
            faltantes_eventos = {
                "id": "ALTER TABLE public.eventos_tracking ADD COLUMN id BIGSERIAL",
                "tracking": "ALTER TABLE public.eventos_tracking ADD COLUMN tracking TEXT",
                "codigo_casillero": "ALTER TABLE public.eventos_tracking ADD COLUMN codigo_casillero TEXT",
                "estado": "ALTER TABLE public.eventos_tracking ADD COLUMN estado TEXT",
                "ubicacion": "ALTER TABLE public.eventos_tracking ADD COLUMN ubicacion TEXT",
                "mensaje_cliente": "ALTER TABLE public.eventos_tracking ADD COLUMN mensaje_cliente TEXT",
                "nota_interna": "ALTER TABLE public.eventos_tracking ADD COLUMN nota_interna TEXT",
                "fecha_evento": "ALTER TABLE public.eventos_tracking ADD COLUMN fecha_evento TEXT",
                "creado_por": "ALTER TABLE public.eventos_tracking ADD COLUMN creado_por TEXT",
                "visible_cliente": "ALTER TABLE public.eventos_tracking ADD COLUMN visible_cliente BOOLEAN NOT NULL DEFAULT TRUE",
            }
            for columna, sentencia in faltantes_eventos.items():
                if columna not in columnas_eventos:
                    cursor.execute(sentencia)
        else:
            cursor.execute("PRAGMA table_info(paquetes)")
            columnas = {str(fila[1]) for fila in cursor.fetchall()}
            faltantes = {
                "tipo_contenedor": "ALTER TABLE paquetes ADD COLUMN tipo_contenedor TEXT DEFAULT '40 HC'",
                "cotizacion_id": "ALTER TABLE paquetes ADD COLUMN cotizacion_id INTEGER",
                "recibido_bodega": "ALTER TABLE paquetes ADD COLUMN recibido_bodega INTEGER NOT NULL DEFAULT 0",
                "pago_confirmado": "ALTER TABLE paquetes ADD COLUMN pago_confirmado INTEGER NOT NULL DEFAULT 0",
                "costo_manipulacion_usd": "ALTER TABLE paquetes ADD COLUMN costo_manipulacion_usd REAL NOT NULL DEFAULT 0",
                "fecha_recepcion": "ALTER TABLE paquetes ADD COLUMN fecha_recepcion TEXT",
                "ubicacion_actual": "ALTER TABLE paquetes ADD COLUMN ubicacion_actual TEXT",
                "eta": "ALTER TABLE paquetes ADD COLUMN eta TEXT",
                "proximo_paso": "ALTER TABLE paquetes ADD COLUMN proximo_paso TEXT",
                "incidencia": "ALTER TABLE paquetes ADD COLUMN incidencia TEXT",
                "visible_cliente": "ALTER TABLE paquetes ADD COLUMN visible_cliente INTEGER NOT NULL DEFAULT 1",
            }
            for columna, sentencia in faltantes.items():
                if columna not in columnas:
                    cursor.execute(sentencia)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS eventos_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking TEXT NOT NULL,
                    codigo_casillero TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    ubicacion TEXT,
                    mensaje_cliente TEXT,
                    nota_interna TEXT,
                    fecha_evento TEXT NOT NULL,
                    creado_por TEXT,
                    visible_cliente INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(tracking) REFERENCES paquetes(tracking) ON DELETE CASCADE
                )
                """
            )
            cursor.execute("PRAGMA table_info(eventos_tracking)")
            columnas_eventos = {str(fila[1]) for fila in cursor.fetchall()}
            faltantes_eventos = {
                "id": "ALTER TABLE eventos_tracking ADD COLUMN id INTEGER",
                "tracking": "ALTER TABLE eventos_tracking ADD COLUMN tracking TEXT",
                "codigo_casillero": "ALTER TABLE eventos_tracking ADD COLUMN codigo_casillero TEXT",
                "estado": "ALTER TABLE eventos_tracking ADD COLUMN estado TEXT",
                "ubicacion": "ALTER TABLE eventos_tracking ADD COLUMN ubicacion TEXT",
                "mensaje_cliente": "ALTER TABLE eventos_tracking ADD COLUMN mensaje_cliente TEXT",
                "nota_interna": "ALTER TABLE eventos_tracking ADD COLUMN nota_interna TEXT",
                "fecha_evento": "ALTER TABLE eventos_tracking ADD COLUMN fecha_evento TEXT",
                "creado_por": "ALTER TABLE eventos_tracking ADD COLUMN creado_por TEXT",
                "visible_cliente": "ALTER TABLE eventos_tracking ADD COLUMN visible_cliente INTEGER NOT NULL DEFAULT 1",
            }
            for columna, sentencia in faltantes_eventos.items():
                if columna not in columnas_eventos:
                    cursor.execute(sentencia)
        # Confirma primero el DDL. Así una instalación que se interrumpió en una
        # migración anterior conserva las columnas antes de poblar la bitácora.
        conn.commit()
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_paquetes_cotizacion ON paquetes(cotizacion_id)"
        )
        cursor.execute("SAVEPOINT ccm_backfill_tracking")
        try:
            cursor.execute(
                """
                INSERT INTO eventos_tracking (
                    tracking, codigo_casillero, estado, ubicacion, mensaje_cliente,
                    nota_interna, fecha_evento, creado_por, visible_cliente
                )
                SELECT p.tracking, p.codigo_casillero, p.estado, NULL,
                       'Estado inicial migrado al nuevo seguimiento.', '',
                       p.fecha_actualizacion, 'migración', TRUE
                FROM paquetes p
                WHERE NOT EXISTS (
                    SELECT 1 FROM eventos_tracking e WHERE e.tracking = p.tracking
                )
                """
            )
        except Exception as exc:
            cursor.execute("ROLLBACK TO SAVEPOINT ccm_backfill_tracking")
            print(
                f"[CCM migración] Se omitió el historial inicial de tracking: {exc}",
                flush=True,
            )
        finally:
            cursor.execute("RELEASE SAVEPOINT ccm_backfill_tracking")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_tracking_cliente "
            "ON eventos_tracking(codigo_casillero, tracking, fecha_evento DESC, id DESC)"
        )
        conn.commit()


def asegurar_esquema_control_cliente():
    """Crea el control 360 sin alterar los registros operativos existentes."""
    with get_db() as conn:
        cursor = conn.cursor()
        if USA_SUPABASE:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.control_envios (
                    tracking TEXT PRIMARY KEY REFERENCES public.paquetes(tracking) ON DELETE CASCADE,
                    estado_pago TEXT NOT NULL DEFAULT 'Pendiente',
                    referencia_pago TEXT,
                    comprobante_pago_url TEXT,
                    estado_documentos TEXT NOT NULL DEFAULT 'Bloqueados',
                    incidencia_estado TEXT NOT NULL DEFAULT 'Sin incidencia',
                    responsable_incidencia TEXT,
                    fecha_compromiso TEXT,
                    receptor_entrega TEXT,
                    fecha_entrega TEXT,
                    evidencia_entrega_url TEXT,
                    canal_notificacion TEXT NOT NULL DEFAULT 'Portal',
                    actualizado_por TEXT,
                    fecha_actualizacion TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.notificaciones_cliente (
                    id BIGSERIAL PRIMARY KEY,
                    codigo_casillero TEXT NOT NULL,
                    tracking TEXT,
                    tipo TEXT NOT NULL DEFAULT 'Información',
                    prioridad TEXT NOT NULL DEFAULT 'Normal',
                    titulo TEXT NOT NULL,
                    mensaje TEXT NOT NULL,
                    canal TEXT NOT NULL DEFAULT 'Portal',
                    leida BOOLEAN NOT NULL DEFAULT FALSE,
                    visible BOOLEAN NOT NULL DEFAULT TRUE,
                    fecha_creacion TEXT NOT NULL,
                    creado_por TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.casos_cliente (
                    id BIGSERIAL PRIMARY KEY,
                    codigo_casillero TEXT NOT NULL,
                    tracking TEXT,
                    categoria TEXT NOT NULL,
                    asunto TEXT NOT NULL,
                    detalle TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'Abierto',
                    prioridad TEXT NOT NULL DEFAULT 'Normal',
                    respuesta_operador TEXT,
                    creado_por TEXT,
                    fecha_creacion TEXT NOT NULL,
                    fecha_actualizacion TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.casos_mensajes (
                    id BIGSERIAL PRIMARY KEY,
                    caso_id BIGINT NOT NULL REFERENCES public.casos_cliente(id) ON DELETE CASCADE,
                    codigo_casillero TEXT NOT NULL,
                    autor_tipo TEXT NOT NULL,
                    autor_nombre TEXT,
                    mensaje TEXT NOT NULL,
                    fecha_creacion TEXT NOT NULL
                )
                """
            )
        else:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS control_envios (
                    tracking TEXT PRIMARY KEY,
                    estado_pago TEXT NOT NULL DEFAULT 'Pendiente',
                    referencia_pago TEXT,
                    comprobante_pago_url TEXT,
                    estado_documentos TEXT NOT NULL DEFAULT 'Bloqueados',
                    incidencia_estado TEXT NOT NULL DEFAULT 'Sin incidencia',
                    responsable_incidencia TEXT,
                    fecha_compromiso TEXT,
                    receptor_entrega TEXT,
                    fecha_entrega TEXT,
                    evidencia_entrega_url TEXT,
                    canal_notificacion TEXT NOT NULL DEFAULT 'Portal',
                    actualizado_por TEXT,
                    fecha_actualizacion TEXT NOT NULL,
                    FOREIGN KEY(tracking) REFERENCES paquetes(tracking) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS notificaciones_cliente (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo_casillero TEXT NOT NULL,
                    tracking TEXT,
                    tipo TEXT NOT NULL DEFAULT 'Información',
                    prioridad TEXT NOT NULL DEFAULT 'Normal',
                    titulo TEXT NOT NULL,
                    mensaje TEXT NOT NULL,
                    canal TEXT NOT NULL DEFAULT 'Portal',
                    leida INTEGER NOT NULL DEFAULT 0,
                    visible INTEGER NOT NULL DEFAULT 1,
                    fecha_creacion TEXT NOT NULL,
                    creado_por TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS casos_cliente (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo_casillero TEXT NOT NULL,
                    tracking TEXT,
                    categoria TEXT NOT NULL,
                    asunto TEXT NOT NULL,
                    detalle TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'Abierto',
                    prioridad TEXT NOT NULL DEFAULT 'Normal',
                    respuesta_operador TEXT,
                    creado_por TEXT,
                    fecha_creacion TEXT NOT NULL,
                    fecha_actualizacion TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS casos_mensajes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    caso_id INTEGER NOT NULL,
                    codigo_casillero TEXT NOT NULL,
                    autor_tipo TEXT NOT NULL,
                    autor_nombre TEXT,
                    mensaje TEXT NOT NULL,
                    fecha_creacion TEXT NOT NULL,
                    FOREIGN KEY(caso_id) REFERENCES casos_cliente(id) ON DELETE CASCADE
                )
                """
            )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notificaciones_cliente_fecha "
            "ON notificaciones_cliente(codigo_casillero, visible, fecha_creacion DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_casos_cliente_estado "
            "ON casos_cliente(codigo_casillero, estado, fecha_actualizacion DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_casos_cliente_fecha "
            "ON casos_cliente(codigo_casillero, fecha_actualizacion DESC, id DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notificaciones_cliente_todas "
            "ON notificaciones_cliente(codigo_casillero, fecha_creacion DESC, id DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_casos_mensajes_hilo "
            "ON casos_mensajes(caso_id, codigo_casillero, fecha_creacion, id)"
        )
        conn.commit()


def registrar_trazabilidad_paquete(
    cursor, tracking, casillero, tipo_movimiento, estado_anterior, estado_nuevo,
    datos_anteriores, datos_nuevos, mensaje_cliente="", nota_interna="",
    visible_cliente=True, creado_por=None, fecha_evento=None,
):
    """Añade un movimiento encadenado; nunca modifica movimientos anteriores."""
    tracking_limpio = str(tracking or "").strip()
    cas = formatear_casillero(casillero) if "formatear_casillero" in globals() else str(casillero or "").strip()
    if not tracking_limpio or not cas:
        raise ValueError("Tracking y casillero son obligatorios para registrar trazabilidad.")
    cursor.execute(
        "SELECT secuencia, hash_evento FROM trazabilidad_paquetes "
        "WHERE tracking = ? ORDER BY secuencia DESC LIMIT 1",
        (tracking_limpio,),
    )
    ultimo = cursor.fetchone()
    secuencia = int(ultimo[0] or 0) + 1 if ultimo else 1
    hash_anterior = str(ultimo[1] or "") if ultimo else "ORIGEN"
    fecha = fecha_evento or obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    anterior_json = json.dumps(datos_anteriores or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    nuevo_json = json.dumps(datos_nuevos or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actor = str(creado_por or st.session_state.get("usuario") or "sistema")
    contenido_hash = "|".join(
        (tracking_limpio, str(secuencia), hash_anterior, str(tipo_movimiento),
         str(estado_anterior or ""), str(estado_nuevo or ""), anterior_json,
         nuevo_json, str(fecha), actor)
    )
    hash_evento = hashlib.sha256(contenido_hash.encode("utf-8")).hexdigest()
    cursor.execute(
        """
        INSERT INTO trazabilidad_paquetes (
            tracking, codigo_casillero, secuencia, tipo_movimiento,
            estado_anterior, estado_nuevo, datos_anteriores_json, datos_nuevos_json,
            mensaje_cliente, nota_interna, visible_cliente, fecha_evento,
            creado_por, hash_anterior, hash_evento
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tracking_limpio, cas, secuencia, str(tipo_movimiento),
            str(estado_anterior or ""), str(estado_nuevo or ""), anterior_json,
            nuevo_json, str(mensaje_cliente or ""), str(nota_interna or ""),
            bool(visible_cliente), fecha, actor, hash_anterior, hash_evento,
        ),
    )
    return secuencia, hash_evento


def asegurar_esquema_trazabilidad_absoluta():
    """Amplía paquetes y crea el diario append-only usado contra extravíos."""
    with get_db() as conn:
        cursor = conn.cursor()
        if USA_SUPABASE:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'paquetes'"
            )
            columnas = {str(f[0]) for f in cursor.fetchall()}
            faltantes = {
                "codigo_interno": "ALTER TABLE public.paquetes ADD COLUMN codigo_interno TEXT",
                "cantidad_bultos": "ALTER TABLE public.paquetes ADD COLUMN cantidad_bultos INTEGER NOT NULL DEFAULT 1",
                "bultos_verificados": "ALTER TABLE public.paquetes ADD COLUMN bultos_verificados INTEGER NOT NULL DEFAULT 0",
                "responsable_actual": "ALTER TABLE public.paquetes ADD COLUMN responsable_actual TEXT",
                "zona_almacen": "ALTER TABLE public.paquetes ADD COLUMN zona_almacen TEXT",
                "ultima_verificacion": "ALTER TABLE public.paquetes ADD COLUMN ultima_verificacion TEXT",
                "version": "ALTER TABLE public.paquetes ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                "estado_integridad": "ALTER TABLE public.paquetes ADD COLUMN estado_integridad TEXT NOT NULL DEFAULT 'Pendiente'",
            }
            for columna, sentencia in faltantes.items():
                if columna not in columnas:
                    cursor.execute(sentencia)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.trazabilidad_paquetes (
                    id BIGSERIAL PRIMARY KEY,
                    tracking TEXT NOT NULL REFERENCES public.paquetes(tracking) ON DELETE RESTRICT,
                    codigo_casillero TEXT NOT NULL,
                    secuencia INTEGER NOT NULL,
                    tipo_movimiento TEXT NOT NULL,
                    estado_anterior TEXT,
                    estado_nuevo TEXT,
                    datos_anteriores_json TEXT NOT NULL,
                    datos_nuevos_json TEXT NOT NULL,
                    mensaje_cliente TEXT,
                    nota_interna TEXT,
                    visible_cliente BOOLEAN NOT NULL DEFAULT TRUE,
                    fecha_evento TEXT NOT NULL,
                    creado_por TEXT NOT NULL,
                    hash_anterior TEXT NOT NULL,
                    hash_evento TEXT NOT NULL,
                    UNIQUE(tracking, secuencia),
                    UNIQUE(hash_evento)
                )
                """
            )
        else:
            cursor.execute("PRAGMA table_info(paquetes)")
            columnas = {str(f[1]) for f in cursor.fetchall()}
            faltantes = {
                "codigo_interno": "ALTER TABLE paquetes ADD COLUMN codigo_interno TEXT",
                "cantidad_bultos": "ALTER TABLE paquetes ADD COLUMN cantidad_bultos INTEGER NOT NULL DEFAULT 1",
                "bultos_verificados": "ALTER TABLE paquetes ADD COLUMN bultos_verificados INTEGER NOT NULL DEFAULT 0",
                "responsable_actual": "ALTER TABLE paquetes ADD COLUMN responsable_actual TEXT",
                "zona_almacen": "ALTER TABLE paquetes ADD COLUMN zona_almacen TEXT",
                "ultima_verificacion": "ALTER TABLE paquetes ADD COLUMN ultima_verificacion TEXT",
                "version": "ALTER TABLE paquetes ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                "estado_integridad": "ALTER TABLE paquetes ADD COLUMN estado_integridad TEXT NOT NULL DEFAULT 'Pendiente'",
            }
            for columna, sentencia in faltantes.items():
                if columna not in columnas:
                    cursor.execute(sentencia)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trazabilidad_paquetes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking TEXT NOT NULL,
                    codigo_casillero TEXT NOT NULL,
                    secuencia INTEGER NOT NULL,
                    tipo_movimiento TEXT NOT NULL,
                    estado_anterior TEXT,
                    estado_nuevo TEXT,
                    datos_anteriores_json TEXT NOT NULL,
                    datos_nuevos_json TEXT NOT NULL,
                    mensaje_cliente TEXT,
                    nota_interna TEXT,
                    visible_cliente INTEGER NOT NULL DEFAULT 1,
                    fecha_evento TEXT NOT NULL,
                    creado_por TEXT NOT NULL,
                    hash_anterior TEXT NOT NULL,
                    hash_evento TEXT NOT NULL UNIQUE,
                    UNIQUE(tracking, secuencia),
                    FOREIGN KEY(tracking) REFERENCES paquetes(tracking) ON DELETE RESTRICT
                )
                """
            )
        paquetes_sin_folio = cursor.execute(
            "SELECT tracking, codigo_casillero FROM paquetes "
            "WHERE codigo_interno IS NULL OR TRIM(codigo_interno) = ''"
        ).fetchall()
        for tracking, casillero in paquetes_sin_folio:
            folio = "CCM-PKG-" + hashlib.sha256(
                f"{casillero}|{tracking}".encode("utf-8")
            ).hexdigest()[:10].upper()
            cursor.execute(
                "UPDATE paquetes SET codigo_interno = ? WHERE tracking = ?",
                (folio, tracking),
            )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paquetes_codigo_interno "
            "ON paquetes(codigo_interno) WHERE codigo_interno IS NOT NULL"
        )
        duplicados_tracking = cursor.execute(
            """
            SELECT UPPER(TRIM(tracking)), COUNT(*)
            FROM paquetes
            GROUP BY UPPER(TRIM(tracking))
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicados_tracking is None:
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paquetes_tracking_normalizado "
                "ON paquetes(UPPER(TRIM(tracking)))"
            )
        else:
            print(
                "[CCM trazabilidad] Existen trackings duplicados por formato; "
                "se bloqueará su edición hasta depurarlos.",
                flush=True,
            )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trazabilidad_cliente_fecha "
            "ON trazabilidad_paquetes(codigo_casillero, tracking, fecha_evento DESC)"
        )
        conn.commit()

        paquetes_sin_origen = cursor.execute(
            """
            SELECT p.tracking, p.codigo_casillero, p.estado, p.fecha_actualizacion
            FROM paquetes p
            WHERE NOT EXISTS (
                SELECT 1 FROM trazabilidad_paquetes t WHERE t.tracking = p.tracking
            )
            """
        ).fetchall()
        for tracking, casillero, estado, fecha in paquetes_sin_origen:
            registrar_trazabilidad_paquete(
                cursor, tracking, casillero, "MIGRACION_ORIGEN", "", estado,
                {}, {"estado": estado}, "Historial operativo incorporado al seguimiento.",
                "Movimiento inicial creado por migración.", True, "migración", fecha,
            )
        conn.commit()


def asegurar_esquema_flujo_tracking():
    """Crea el flujo comercial que antecede a la recepción física del bulto."""
    with get_db() as conn:
        cursor = conn.cursor()
        if USA_SUPABASE:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='paquetes'"
            )
            columnas = {str(f[0]) for f in cursor.fetchall()}
            faltantes = {
                "tracking_externo": "ALTER TABLE public.paquetes ADD COLUMN tracking_externo TEXT",
                "envio_id": "ALTER TABLE public.paquetes ADD COLUMN envio_id BIGINT",
                "numero_bulto": "ALTER TABLE public.paquetes ADD COLUMN numero_bulto INTEGER NOT NULL DEFAULT 1",
                "etiqueta_estado": "ALTER TABLE public.paquetes ADD COLUMN etiqueta_estado TEXT NOT NULL DEFAULT 'No emitida'",
                "proveedor_nombre": "ALTER TABLE public.paquetes ADD COLUMN proveedor_nombre TEXT",
            }
            for columna, sentencia in faltantes.items():
                if columna not in columnas:
                    cursor.execute(sentencia)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.acuerdos_pago (
                    id BIGSERIAL PRIMARY KEY,
                    cotizacion_id BIGINT NOT NULL UNIQUE REFERENCES public.cotizaciones(id) ON DELETE RESTRICT,
                    codigo_casillero TEXT NOT NULL,
                    condicion_pago TEXT NOT NULL,
                    estado_acuerdo TEXT NOT NULL,
                    estado_pago TEXT NOT NULL DEFAULT 'Pendiente',
                    monto_acordado DOUBLE PRECISION NOT NULL DEFAULT 0,
                    fecha_vencimiento TEXT,
                    nota_cliente TEXT,
                    nota_interna TEXT,
                    aprobado_por TEXT,
                    fecha_aprobacion TEXT,
                    fecha_actualizacion TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.envios (
                    id BIGSERIAL PRIMARY KEY,
                    codigo_envio TEXT NOT NULL UNIQUE,
                    cotizacion_id BIGINT NOT NULL UNIQUE REFERENCES public.cotizaciones(id) ON DELETE RESTRICT,
                    codigo_casillero TEXT NOT NULL,
                    cantidad_bultos INTEGER NOT NULL,
                    estado TEXT NOT NULL,
                    proveedor_nombre TEXT,
                    aprobado_por TEXT NOT NULL,
                    fecha_aprobacion TEXT NOT NULL,
                    fecha_actualizacion TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.documentos_paquete (
                    id BIGSERIAL PRIMARY KEY,
                    tracking_ccm TEXT NOT NULL,
                    tipo_documento TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    estado TEXT NOT NULL,
                    fecha_emision TEXT NOT NULL,
                    emitido_por TEXT NOT NULL,
                    UNIQUE(tracking_ccm, tipo_documento, version)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.recepciones_bodega (
                    id BIGSERIAL PRIMARY KEY,
                    tracking_ccm TEXT NOT NULL UNIQUE,
                    codigo_casillero TEXT NOT NULL,
                    condicion TEXT NOT NULL,
                    peso_real_kg DOUBLE PRECISION,
                    largo_real_cm DOUBLE PRECISION,
                    ancho_real_cm DOUBLE PRECISION,
                    alto_real_cm DOUBLE PRECISION,
                    fotografia_url TEXT,
                    zona_almacen TEXT NOT NULL,
                    observaciones TEXT,
                    recibido_por TEXT NOT NULL,
                    fecha_recepcion TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.excepciones_recepcion (
                    id BIGSERIAL PRIMARY KEY,
                    codigo_escaneado TEXT,
                    categoria TEXT NOT NULL,
                    detalle TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'Abierta',
                    codigo_casillero TEXT,
                    tracking_ccm TEXT,
                    fotografia_url TEXT,
                    responsable TEXT,
                    resolucion TEXT,
                    creado_por TEXT NOT NULL,
                    fecha_creacion TEXT NOT NULL,
                    fecha_actualizacion TEXT NOT NULL
                )
                """
            )
        else:
            cursor.execute("PRAGMA table_info(paquetes)")
            columnas = {str(f[1]) for f in cursor.fetchall()}
            faltantes = {
                "tracking_externo": "ALTER TABLE paquetes ADD COLUMN tracking_externo TEXT",
                "envio_id": "ALTER TABLE paquetes ADD COLUMN envio_id INTEGER",
                "numero_bulto": "ALTER TABLE paquetes ADD COLUMN numero_bulto INTEGER NOT NULL DEFAULT 1",
                "etiqueta_estado": "ALTER TABLE paquetes ADD COLUMN etiqueta_estado TEXT NOT NULL DEFAULT 'No emitida'",
                "proveedor_nombre": "ALTER TABLE paquetes ADD COLUMN proveedor_nombre TEXT",
            }
            for columna, sentencia in faltantes.items():
                if columna not in columnas:
                    cursor.execute(sentencia)
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS acuerdos_pago (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cotizacion_id INTEGER NOT NULL UNIQUE,
                    codigo_casillero TEXT NOT NULL,
                    condicion_pago TEXT NOT NULL,
                    estado_acuerdo TEXT NOT NULL,
                    estado_pago TEXT NOT NULL DEFAULT 'Pendiente',
                    monto_acordado REAL NOT NULL DEFAULT 0,
                    fecha_vencimiento TEXT,
                    nota_cliente TEXT,
                    nota_interna TEXT,
                    aprobado_por TEXT,
                    fecha_aprobacion TEXT,
                    fecha_actualizacion TEXT NOT NULL,
                    FOREIGN KEY(cotizacion_id) REFERENCES cotizaciones(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS envios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo_envio TEXT NOT NULL UNIQUE,
                    cotizacion_id INTEGER NOT NULL UNIQUE,
                    codigo_casillero TEXT NOT NULL,
                    cantidad_bultos INTEGER NOT NULL,
                    estado TEXT NOT NULL,
                    proveedor_nombre TEXT,
                    aprobado_por TEXT NOT NULL,
                    fecha_aprobacion TEXT NOT NULL,
                    fecha_actualizacion TEXT NOT NULL,
                    FOREIGN KEY(cotizacion_id) REFERENCES cotizaciones(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS documentos_paquete (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_ccm TEXT NOT NULL,
                    tipo_documento TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    estado TEXT NOT NULL,
                    fecha_emision TEXT NOT NULL,
                    emitido_por TEXT NOT NULL,
                    UNIQUE(tracking_ccm, tipo_documento, version)
                );
                CREATE TABLE IF NOT EXISTS recepciones_bodega (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_ccm TEXT NOT NULL UNIQUE,
                    codigo_casillero TEXT NOT NULL,
                    condicion TEXT NOT NULL,
                    peso_real_kg REAL,
                    largo_real_cm REAL,
                    ancho_real_cm REAL,
                    alto_real_cm REAL,
                    fotografia_url TEXT,
                    zona_almacen TEXT NOT NULL,
                    observaciones TEXT,
                    recibido_por TEXT NOT NULL,
                    fecha_recepcion TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS excepciones_recepcion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo_escaneado TEXT,
                    categoria TEXT NOT NULL,
                    detalle TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'Abierta',
                    codigo_casillero TEXT,
                    tracking_ccm TEXT,
                    fotografia_url TEXT,
                    responsable TEXT,
                    resolucion TEXT,
                    creado_por TEXT NOT NULL,
                    fecha_creacion TEXT NOT NULL,
                    fecha_actualizacion TEXT NOT NULL
                );
                """
            )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_envios_casillero ON envios(codigo_casillero, fecha_actualizacion DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_paquetes_envio ON paquetes(envio_id, numero_bulto)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_paquetes_tracking_externo ON paquetes(tracking_externo)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_excepciones_estado ON excepciones_recepcion(estado, fecha_actualizacion DESC)")
        conn.commit()


@st.cache_resource(show_spinner=False)
def inicializar_persistencia_v3():
    """Prepara esquema e índices una vez por proceso, no una vez por botón."""
    init_db()
    asegurar_esquema_cotizaciones()
    asegurar_esquema_paquetes_operativo()
    asegurar_esquema_control_cliente()
    asegurar_esquema_trazabilidad_absoluta()
    asegurar_esquema_flujo_tracking()
    asegurar_esquema_direcciones()
    asegurar_indices_rendimiento()
    return True


inicializar_persistencia_v3()


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


@st.cache_data(ttl=120, show_spinner=False, max_entries=2048)
def get_config_sistema(clave, valor_default=""):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT valor FROM config_sistema WHERE clave = ?", (clave,))
            row = c.fetchone()
            return row[0] if row else valor_default
    except Exception:
        return valor_default


@st.cache_data(ttl=60, show_spinner=False)
def obtener_conteos_tablas():
    """Evita recalcular conteos completos en cada interacción administrativa."""
    with get_db() as conn:
        c = conn.cursor()
        if USA_SUPABASE:
            c.execute(
                """
                SELECT relname, GREATEST(n_live_tup, 0)::BIGINT
                FROM pg_stat_user_tables
                ORDER BY relname
                """
            )
            return {str(tabla): int(conteo or 0) for tabla, conteo in c.fetchall()}
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tablas = [r[0] for r in c.fetchall()]
        conteos = {}
        for tabla in tablas:
            c.execute(f'SELECT COUNT(*) FROM "{tabla}"')
            conteos[tabla] = int(c.fetchone()[0])
        return conteos


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


ANUNCIO_PORTAL_CLAVE = "ANUNCIO_PORTAL_CLIENTES"
ANUNCIO_PORTAL_DEFAULT = {
    "id": "",
    "activo": False,
    "tipo": "Información",
    "icono": "📢",
    "titulo": "",
    "mensaje": "",
    "boton_texto": "",
    "boton_url": "",
}


def cargar_anuncio_portal():
    """Devuelve un anuncio normalizado desde config_sistema."""
    anuncio = dict(ANUNCIO_PORTAL_DEFAULT)
    raw = get_config_sistema(ANUNCIO_PORTAL_CLAVE, "")
    if not raw:
        return anuncio
    try:
        datos = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return anuncio
    if not isinstance(datos, dict):
        return anuncio
    for clave in anuncio:
        if clave in datos:
            anuncio[clave] = datos[clave]
    anuncio["activo"] = bool(anuncio.get("activo"))
    anuncio["tipo"] = str(anuncio.get("tipo") or "Información")[:30]
    anuncio["icono"] = str(anuncio.get("icono") or "📢")[:8]
    anuncio["titulo"] = str(anuncio.get("titulo") or "")[:120]
    anuncio["mensaje"] = str(anuncio.get("mensaje") or "")[:1200]
    anuncio["boton_texto"] = str(anuncio.get("boton_texto") or "")[:60]
    anuncio["boton_url"] = str(anuncio.get("boton_url") or "")[:600]
    anuncio["id"] = str(anuncio.get("id") or "")[:80]
    return anuncio


def url_anuncio_segura(url):
    """Solo permite enlaces web absolutos en el botón del anuncio."""
    valor = str(url or "").strip()
    if not valor:
        return ""
    try:
        parsed = urllib.parse.urlparse(valor)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return valor


def html_anuncio_portal(anuncio, vista_previa=False):
    paletas = {
        "Información": ("#eff6ff", "#93c5fd", "#1d4ed8", "#1e3a8a"),
        "Promoción": ("#ecfdf5", "#86efac", "#15803d", "#14532d"),
        "Importante": ("#fffbeb", "#fcd34d", "#b45309", "#78350f"),
    }
    tipo = str(anuncio.get("tipo") or "Información")
    fondo, borde, acento, texto = paletas.get(tipo, paletas["Información"])
    titulo = html.escape(str(anuncio.get("titulo") or "Aviso importante"))
    mensaje = html.escape(str(anuncio.get("mensaje") or "")).replace("\n", "<br>")
    icono = html.escape(str(anuncio.get("icono") or "📢"))
    etiqueta = "Vista previa" if vista_previa else tipo
    return (
        f'<section class="portal-announcement" style="--ann-bg:{fondo};--ann-border:{borde};'
        f'--ann-accent:{acento};--ann-text:{texto};" aria-label="Anuncio: {titulo}">'
        f'<div class="portal-announcement-icon">{icono}</div>'
        f'<div class="portal-announcement-copy"><span class="portal-announcement-label">{html.escape(etiqueta)}</span>'
        f'<div class="portal-announcement-title">{titulo}</div>'
        f'<div class="portal-announcement-message">{mensaje}</div></div>'
        f'</section>'
    )


def clave_omision_anuncio(casillero):
    cas = formatear_casillero(casillero)
    digest = hashlib.sha256(cas.encode("utf-8")).hexdigest()[:24]
    return f"ANUNCIO_OMITIDO_{digest}"


def omitir_anuncio_portal(anuncio_id, casillero):
    cas = formatear_casillero(casillero)
    clave_sesion = f"_ccm_anuncios_omitidos_{hashlib.sha256(cas.encode('utf-8')).hexdigest()[:16]}"
    ocultos = set(st.session_state.get(clave_sesion) or ())
    ocultos.add(str(anuncio_id or ""))
    st.session_state[clave_sesion] = ocultos
    if cas and anuncio_id:
        set_config_sistema(
            clave_omision_anuncio(cas),
            str(anuncio_id),
            "Versión del anuncio omitida por una cuenta de cliente",
        )


def pintar_anuncio_portal_cliente():
    anuncio = cargar_anuncio_portal()
    anuncio_id = str(anuncio.get("id") or "")
    casillero = formatear_casillero(st.session_state.get("casillero", ""))
    clave_sesion = f"_ccm_anuncios_omitidos_{hashlib.sha256(casillero.encode('utf-8')).hexdigest()[:16]}"
    ocultos = set(st.session_state.get(clave_sesion) or ())
    omitido_persistente = get_config_sistema(clave_omision_anuncio(casillero), "") if casillero else ""
    if (
        not anuncio.get("activo")
        or not anuncio_id
        or anuncio_id in ocultos
        or str(omitido_persistente or "") == anuncio_id
    ):
        return
    with st.container(key="portal_announcement_client"):
        st.markdown(html_anuncio_portal(anuncio), unsafe_allow_html=True)
        url_boton = url_anuncio_segura(anuncio.get("boton_url"))
        texto_boton = str(anuncio.get("boton_texto") or "").strip()
        clave_widget = hashlib.sha256(anuncio_id.encode("utf-8")).hexdigest()[:12]
        if url_boton and texto_boton:
            col_accion, col_omitir = st.columns([1.7, 1], gap="medium")
            with col_accion:
                st.link_button(texto_boton, url_boton, use_container_width=True)
            with col_omitir:
                st.button(
                    "Omitir anuncio",
                    key=f"omitir_anuncio_{clave_widget}",
                    use_container_width=True,
                    on_click=omitir_anuncio_portal,
                    args=(anuncio_id, casillero),
                )
        else:
            st.button(
                "Omitir anuncio",
                key=f"omitir_anuncio_{clave_widget}",
                use_container_width=True,
                on_click=omitir_anuncio_portal,
                args=(anuncio_id, casillero),
            )


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


def permisos_denegados():
    """Estado seguro cuando no es posible demostrar los permisos del usuario."""
    return {
        "hub_china": 0,
        "hub_eeuu": 0,
        "hub_honduras": 0,
        "mod_cotizador": 0,
        "mod_catalogo": 0,
        "mod_cotizaciones": 0,
        "mod_envios": 0,
        "mod_fichas": 0,
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
            UPDATE permisos_usuario SET
                hub_china=?, hub_eeuu=?, hub_honduras=?, mod_cotizador=?,
                mod_catalogo=?, mod_cotizaciones=?, mod_envios=?, mod_fichas=?
            WHERE codigo_casillero=?
            """,
            (
                bool(vals["hub_china"]),
                bool(vals["hub_eeuu"]),
                bool(vals["hub_honduras"]),
                bool(vals["mod_cotizador"]),
                bool(vals["mod_catalogo"]),
                bool(vals["mod_cotizaciones"]),
                bool(vals["mod_envios"]),
                bool(vals["mod_fichas"]),
                cas,
            ),
        )
        if c.rowcount == 0:
            c.execute(
                """
                INSERT INTO permisos_usuario (
                    codigo_casillero, hub_china, hub_eeuu, hub_honduras,
                    mod_cotizador, mod_catalogo, mod_cotizaciones, mod_envios, mod_fichas
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cas, bool(vals["hub_china"]), bool(vals["hub_eeuu"]),
                    bool(vals["hub_honduras"]), bool(vals["mod_cotizador"]),
                    bool(vals["mod_catalogo"]), bool(vals["mod_cotizaciones"]),
                    bool(vals["mod_envios"]), bool(vals["mod_fichas"]),
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
    cas_sesion = formatear_casillero(st.session_state.get("casillero", ""))
    cas = formatear_casillero(casillero or cas_sesion)
    base = permisos_denegados()
    if not cas:
        return base
    # Los roles administrativos se autorizan por rol. Solo se consulta la tabla
    # cuando el panel está revisando explícitamente los permisos de un cliente.
    if es_rol_admin() and (casillero is None or cas == cas_sesion):
        return permisos_default(st.session_state.get("rol"))
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
    except Exception as exc:
        print(f"[CCM permisos] No se pudieron validar permisos para {cas}: {exc}", flush=True)
        if es_rol_admin():
            st.session_state["_ccm_error_permisos_admin"] = True
        else:
            st.session_state["_ccm_error_permisos"] = True
        return base


def usuario_puede_hub(hub_id, casillero=None):
    if es_rol_admin() and casillero is None:
        return True
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True
    col = HUB_PERMISO_COL.get(hub_id)
    if not col:
        return False
    return bool(permisos_de(casillero).get(col, 0))


def usuario_puede_modulo(mod_id, casillero=None):
    if es_rol_admin() and casillero is None:
        return True
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True
    col = MODULO_PERMISO_COL.get(mod_id)
    if not col:
        return False
    return bool(permisos_de(casillero).get(col, 0))


def guardar_permisos(casillero, datos):
    cas = formatear_casillero(casillero)
    valores = (
        bool(datos.get("hub_china", 0)),
        bool(datos.get("hub_eeuu", 0)),
        bool(datos.get("hub_honduras", 0)),
        bool(datos.get("mod_cotizador", 0)),
        bool(datos.get("mod_catalogo", 0)),
        bool(datos.get("mod_cotizaciones", 0)),
        bool(datos.get("mod_envios", 0)),
        bool(datos.get("mod_fichas", 0)),
    )
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE permisos_usuario SET
                hub_china=?, hub_eeuu=?, hub_honduras=?, mod_cotizador=?,
                mod_catalogo=?, mod_cotizaciones=?, mod_envios=?, mod_fichas=?
            WHERE codigo_casillero=?
            """,
            (*valores, cas),
        )
        if c.rowcount == 0:
            c.execute(
                """
                INSERT INTO permisos_usuario (
                    codigo_casillero, hub_china, hub_eeuu, hub_honduras,
                    mod_cotizador, mod_catalogo, mod_cotizaciones, mod_envios, mod_fichas
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cas, *valores),
            )
    st.session_state.pop(f"_ccm_permisos_{cas}", None)


def _migrar_casillero_tablas(conn, origen, destino):
    for tabla in (
        "cotizaciones", "paquetes", "eventos_tracking", "direcciones_entrega",
        "carrito_catalogo", "permisos_usuario", "notificaciones_cliente", "casos_mensajes",
        "casos_cliente",
        "trazabilidad_paquetes", "acuerdos_pago", "envios", "recepciones_bodega",
        "excepciones_recepcion",
    ):
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
    if not (CLAVE_INICIAL_SUPERADMIN and DNI_SUPERADMIN and CORREO_SUPERADMIN):
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
                    TELEFONO_SUPERADMIN or "No configurado",
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
    return "".join(secrets.choice(caracteres) for _ in range(12))


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


def validar_url_publica(url):
    """Rechaza destinos locales, privados o reservados antes de una petición saliente."""
    texto = str(url or "").strip()
    parsed = urllib.parse.urlparse(texto)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("El enlace debe usar HTTP o HTTPS y contener un dominio válido.")
    if parsed.username or parsed.password:
        raise ValueError("El enlace no puede incluir credenciales.")
    puerto = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        destinos = socket.getaddrinfo(parsed.hostname, puerto, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("No se pudo resolver el dominio del enlace.") from exc
    direcciones = {registro[4][0].split("%", 1)[0] for registro in destinos}
    if not direcciones:
        raise ValueError("El dominio no devolvió una dirección válida.")
    for direccion in direcciones:
        try:
            ip = ipaddress.ip_address(direccion)
        except ValueError as exc:
            raise ValueError("El dominio devolvió una dirección no válida.") from exc
        if not ip.is_global:
            raise ValueError("No se permiten enlaces a redes privadas, locales o reservadas.")
    return texto


def abrir_url_publica(url, headers, timeout, max_redirecciones=3):
    """Abre una URL pública y revalida cada salto de redirección."""
    actual = validar_url_publica(url)
    for _ in range(max_redirecciones + 1):
        respuesta = requests.get(
            actual,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        if respuesta.status_code in (301, 302, 303, 307, 308):
            destino = respuesta.headers.get("location")
            respuesta.close()
            if not destino:
                raise requests.TooManyRedirects("Redirección sin destino.")
            actual = validar_url_publica(urllib.parse.urljoin(actual, destino))
            continue
        return respuesta
    raise requests.TooManyRedirects("El enlace superó el máximo de redirecciones permitido.")


@st.cache_data(ttl=300, max_entries=128, show_spinner=False)
def consultar_producto_enlace_eeuu(enlace):
    """Obtiene metadatos públicos de cualquier tienda que permita su lectura.

    No intenta evadir CAPTCHA, inicio de sesión ni otras protecciones de una
    tienda: cuando no hay datos públicos, el usuario conserva la opción de
    completar la ficha manualmente.
    """
    url = str(enlace or "").strip()
    try:
        respuesta = abrir_url_publica(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=(3.05, 8),
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
    except (requests.RequestException, ValueError) as exc:
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
    try:
        respuesta = abrir_url_publica(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CCM-Cotizador/1.0)"},
            timeout=(3.05, 6),
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
    except (requests.RequestException, ValueError):
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
        "cliente_caso_activo",
        "_ccm_control360_clientes_cache_v3",
        "_ccm_control360_expediente_cache_v3",
        "_ccm_soporte_casos_cache_v3",
        "_ccm_soporte_hilo_cache_v3",
    ]:
        st.session_state.pop(k, None)
    prefijos_soporte = (
        "cliente_responder_caso_", "_limpiar_cliente_caso_", "_flash_cliente_caso_",
        "c360_caso_resp_", "_limpiar_admin_caso_", "_flash_admin_caso_",
        "_confirmar_cierre_caso_",
        "soporte_composer_cliente_", "soporte_composer_admin_",
    )
    for clave_sesion in list(st.session_state.keys()):
        if str(clave_sesion).startswith(prefijos_soporte):
            st.session_state.pop(clave_sesion, None)
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.query_params.clear()
    st.rerun()


def abrir_creacion_usuario_admin():
    st.session_state["_admin_dialog_crear"] = True
    st.session_state["_admin_user_table_version"] = (
        int(st.session_state.get("_admin_user_table_version") or 0) + 1
    )


def reiniciar_tabla_usuarios_admin():
    st.session_state["_admin_user_table_version"] = (
        int(st.session_state.get("_admin_user_table_version") or 0) + 1
    )


def invalidar_cache_datos_admin():
    cargar_paquetes_db.clear()
    cargar_eventos_tracking_db.clear()
    cargar_paquetes_admin.clear()
    cargar_metricas_paquetes_admin.clear()
    cargar_eventos_tracking_admin.clear()
    cargar_cotizaciones_db.clear()
    cargar_estados_cotizaciones_db.clear()
    cargar_cotizaciones_confirmadas_admin.clear()
    cargar_resumen_operativo_admin.clear()
    invalidar_cache_clientes_control360()
    invalidar_cache_expediente_control360()
    invalidar_cache_soporte()


@st.dialog("Editar cuenta", width="large")
def dialogo_editar_usuario_admin(usuario, root=False):
    uid, cas_u, nom_u, dni_u, cor_u, tel_u, dep_u, ciu_u, dir_u, rol_u, act_u = usuario
    perm = permisos_de(cas_u)
    st.markdown(
        f"**{html.escape(str(nom_u or 'Sin nombre'))}**  \n"
        f"{html.escape(formatear_casillero(cas_u))} · {html.escape(str(cor_u or 'Sin correo'))}"
    )
    tab_perfil, tab_permisos, tab_seguridad = st.tabs(["Perfil", "Permisos", "Seguridad"])
    with tab_perfil:
        p1, p2 = st.columns(2, gap="medium")
        with p1:
            n_nom = st.text_input("Nombre completo", value=nom_u, key=f"dlg_nom_{uid}")
            n_dni = st.text_input("DNI", value=dni_u, key=f"dlg_dni_{uid}")
            n_cor = st.text_input("Correo", value=cor_u, key=f"dlg_cor_{uid}")
            n_tel = st.text_input("Teléfono", value=tel_u, key=f"dlg_tel_{uid}")
        with p2:
            departamentos = list(MUNICIPIOS_HONDURAS.keys())
            n_dep = st.selectbox(
                "Departamento", departamentos,
                index=departamentos.index(dep_u) if dep_u in departamentos else 0,
                key=f"dlg_dep_{uid}",
            )
            municipios = MUNICIPIOS_HONDURAS[n_dep]
            n_ciu = st.selectbox(
                "Ciudad", municipios,
                index=municipios.index(ciu_u) if ciu_u in municipios else 0,
                key=f"dlg_ciu_{uid}",
            )
            n_dir = st.text_area("Dirección exacta", value=dir_u or "", key=f"dlg_dir_{uid}", height=88)
            n_cas = st.text_input(
                "Código de casillero", value=formatear_casillero(cas_u), key=f"dlg_cas_{uid}"
            )
        acceso1, acceso2 = st.columns(2, gap="medium")
        roles = ["cliente", "admin", "superadmin"] if root else ["cliente", "admin"]
        with acceso1:
            n_rol = st.selectbox(
                "Rol", roles, index=roles.index(rol_u) if rol_u in roles else 0,
                key=f"dlg_rol_{uid}", disabled=(rol_u == "superadmin" and not root),
            )
        with acceso2:
            n_act = st.toggle("Cuenta activa", value=bool(act_u), key=f"dlg_act_{uid}")

    with tab_permisos:
        st.caption("Los cambios determinan las áreas visibles para esta cuenta.")
        col_hub, col_mod = st.columns(2, gap="medium")
        with col_hub:
            st.markdown("**Acceso por país**")
            p_china = st.toggle("China", value=bool(perm.get("hub_china")), key=f"dlg_h_cn_{uid}")
            p_eeuu = st.toggle("Estados Unidos", value=bool(perm.get("hub_eeuu")), key=f"dlg_h_us_{uid}")
            p_hn = st.toggle("Honduras", value=bool(perm.get("hub_honduras")), key=f"dlg_h_hn_{uid}")
        with col_mod:
            st.markdown("**Herramientas**")
            p_cot = st.toggle("Cotizador", value=bool(perm.get("mod_cotizador")), key=f"dlg_m_cot_{uid}")
            p_cat = st.toggle("Catálogo", value=bool(perm.get("mod_catalogo")), key=f"dlg_m_cat_{uid}")
            p_hist = st.toggle("Mis cotizaciones", value=bool(perm.get("mod_cotizaciones")), key=f"dlg_m_hist_{uid}")
            p_env = st.toggle("Envíos", value=bool(perm.get("mod_envios")), key=f"dlg_m_env_{uid}")
            p_fic = st.toggle("Fichas", value=bool(perm.get("mod_fichas")), key=f"dlg_m_fic_{uid}")

    with tab_seguridad:
        st.markdown("**Restablecer contraseña**")
        nueva_clave = st.text_input(
            "Nueva contraseña", type="password", key=f"dlg_pwd_{uid}",
            placeholder="Vacío para generar una clave temporal",
        )
        if st.button("Restablecer credenciales", key=f"dlg_reset_{uid}"):
            clave = nueva_clave.strip() if nueva_clave else generar_clave_provisional()
            with get_db() as conn:
                conn.execute("UPDATE usuarios SET password_hash=? WHERE id=?", (hash_pwd(clave), uid))
            st.success("Contraseña actualizada. Se muestra una sola vez.")
            st.code(clave, language="text")
        if rol_u != "superadmin":
            st.divider()
            confirmar = st.checkbox("Confirmo la eliminación definitiva", key=f"dlg_delete_ok_{uid}")
            if st.button("Eliminar cuenta", key=f"dlg_delete_{uid}", disabled=not confirmar):
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT (SELECT COUNT(*) FROM trazabilidad_paquetes WHERE codigo_casillero=?) + "
                        "(SELECT COUNT(*) FROM acuerdos_pago WHERE codigo_casillero=?)",
                        (cas_u, cas_u),
                    )
                    if int((cur.fetchone() or (0,))[0] or 0) > 0:
                        st.error(
                            "La cuenta tiene trazabilidad logística y no puede eliminarse. "
                            "Desactívela para conservar la cadena de custodia."
                        )
                        st.stop()
                    for tabla in (
                        "permisos_usuario", "direcciones_entrega", "carrito_catalogo",
                        "notificaciones_cliente", "casos_mensajes", "casos_cliente",
                        "eventos_tracking", "paquetes", "cotizaciones",
                    ):
                        cur.execute(f"DELETE FROM {tabla} WHERE codigo_casillero=?", (cas_u,))
                    cur.execute("DELETE FROM usuarios WHERE id=?", (uid,))
                invalidar_cache_datos_admin()
                reiniciar_tabla_usuarios_admin()
                st.session_state["_admin_usuario_flash"] = "Cuenta eliminada correctamente."
                st.rerun()

    accion_info, accion_cerrar, accion_guardar = st.columns([1.2, 0.55, 1], gap="medium")
    with accion_info:
        st.caption("Los cambios de perfil y permisos se aplicarán en el próximo refresco del cliente.")
    with accion_cerrar:
        if st.button("Cerrar", key=f"dlg_close_{uid}", use_container_width=True):
            reiniciar_tabla_usuarios_admin()
            st.rerun()
    with accion_guardar:
        guardar = st.button(
            "Guardar cambios", type="primary", key=f"dlg_save_{uid}", use_container_width=True
        )
    if guardar:
        nuevo_cas = formatear_casillero(n_cas) or generar_codigo_casillero_dni(n_dni)
        correo = normalizar_correo(n_cor)
        if not (n_nom.strip() and n_dni.strip() and correo and n_tel.strip() and nuevo_cas):
            st.error("Complete nombre, DNI, correo, teléfono y casillero.")
        elif "@" not in correo or "." not in correo.rsplit("@", 1)[-1]:
            st.error("Ingrese un correo electrónico válido.")
        elif rol_u == "superadmin" and (n_rol != "superadmin" or not n_act) and not root:
            st.error("Solo el superusuario puede alterar la cuenta raíz.")
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
                    (
                        n_nom.strip(), n_dni.strip(), correo, n_tel.strip(), n_dep, n_ciu,
                        n_dir.strip(), nuevo_cas, n_rol, bool(n_act), uid,
                    ),
                )
            guardar_permisos(
                nuevo_cas,
                {
                    "hub_china": p_china, "hub_eeuu": p_eeuu, "hub_honduras": p_hn,
                    "mod_cotizador": p_cot, "mod_catalogo": p_cat,
                    "mod_cotizaciones": p_hist, "mod_envios": p_env, "mod_fichas": p_fic,
                },
            )
            reiniciar_tabla_usuarios_admin()
            st.session_state["_admin_usuario_flash"] = "Cambios guardados correctamente."
            st.rerun()


@st.dialog("Crear usuario", width="large")
def dialogo_crear_usuario_admin(root=False):
    intro_crear, cerrar_crear = st.columns([1, 0.24], gap="medium")
    with intro_crear:
        st.caption("Registre la identidad, ubicación y credenciales iniciales.")
    with cerrar_crear:
        if st.button("Cancelar", key="dlg_new_cancel", use_container_width=True):
            st.session_state["_admin_dialog_crear"] = False
            st.rerun()
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        nombre = st.text_input("Nombre completo *", key="dlg_new_nom")
        dni = st.text_input("DNI *", key="dlg_new_dni")
        correo = st.text_input("Correo electrónico *", key="dlg_new_cor")
        telefono = st.text_input("Teléfono *", key="dlg_new_tel")
    with c2:
        departamento = st.selectbox("Departamento", list(MUNICIPIOS_HONDURAS), key="dlg_new_dep")
        ciudad = st.selectbox("Ciudad", MUNICIPIOS_HONDURAS[departamento], key="dlg_new_ciu")
        direccion = st.text_area("Dirección exacta", key="dlg_new_dir", height=88)
        clave_ingresada = st.text_input(
            "Contraseña inicial", type="password", key="dlg_new_pwd",
            placeholder="Vacío para generar una clave segura",
        )
        rol = st.selectbox("Rol inicial", ["cliente", "admin"] if root else ["cliente"], key="dlg_new_rol")
    casillero = generar_codigo_casillero_dni(dni)
    if casillero:
        st.info(f"Casillero que será asignado: {casillero}")
    if st.button("Crear cuenta", type="primary", key="dlg_new_submit", use_container_width=True):
        correo_normalizado = normalizar_correo(correo)
        if not (nombre.strip() and dni.strip() and correo_normalizado and telefono.strip() and casillero):
            st.error("Complete todos los campos obligatorios.")
        elif "@" not in correo_normalizado or "." not in correo_normalizado.rsplit("@", 1)[-1]:
            st.error("Ingrese un correo electrónico válido.")
        else:
            clave = clave_ingresada.strip() if clave_ingresada else generar_clave_provisional()
            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO usuarios (
                            codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal,
                            departamento, ciudad, direccion_exacta, password_hash, rol, activo, fecha_creacion
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)
                        """,
                        (
                            casillero, nombre.strip(), dni.strip(), correo_normalizado, telefono.strip(),
                            departamento, ciudad, direccion.strip() or f"{ciudad}, {departamento}",
                            hash_pwd(clave), rol, obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                asegurar_permisos_casillero(casillero, rol)
                reiniciar_tabla_usuarios_admin()
                st.session_state["_admin_cuenta_creada"] = {
                    "casillero": casillero, "nombre": nombre.strip(), "clave": clave,
                }
                st.session_state["_admin_dialog_crear"] = False
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Ya existe una cuenta con ese casillero, DNI o correo.")


# ---------------------------------------------------------
# 6. ESTILOS CSS REFINADOS: ADAPTABLES A MÓVILES (IPHONE Y ANDROID)
# ---------------------------------------------------------
st.markdown(
    """
<style>
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
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
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
    .stApp:has(.st-key-login_card),
    [data-testid="stAppViewContainer"]:has(.st-key-login_card) {
        background: #eef3f8 !important;
    }
    .stApp:has(.st-key-login_card) .block-container,
    .stApp:has(.st-key-login_card) [data-testid="stMainBlockContainer"],
    .stApp:has(.st-key-login_card) .stMainBlockContainer {
        max-width: 620px !important;
        padding-top: clamp(22px, 5vh, 54px) !important;
        padding-left: 14px !important;
        padding-right: 14px !important;
        padding-bottom: 48px !important;
    }
    .st-key-login_header {
        margin-bottom: 14px !important;
    }
    .st-key-login_header .app-header-blue {
        padding: 16px 20px !important;
        border-radius: 10px !important;
        box-shadow: 0 10px 24px rgba(0, 54, 140, .18) !important;
    }
    .st-key-login_header .app-header-top {
        margin-bottom: 7px !important;
        padding-bottom: 10px !important;
    }
    .st-key-login_header .app-header-brand {
        font-size: clamp(1.15rem, 3vw, 1.45rem) !important;
        letter-spacing: .035em !important;
    }
    .st-key-login_card {
        padding: 22px 24px 20px;
        background: #ffffff;
        border: 1px solid #d8e1ec !important;
        border-radius: 10px !important;
        box-shadow: 0 14px 34px rgba(15, 23, 42, .09);
    }
    .login-card-head {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 17px;
    }
    .login-card-icon {
        display: grid;
        place-items: center;
        width: 42px;
        height: 42px;
        flex: 0 0 42px;
        color: #ffffff;
        background: #0757c8;
        border-radius: 8px;
        font-size: 1.1rem;
    }
    .login-card-title {
        color: #0f172a;
        font-size: 1.08rem;
        font-weight: 850;
        line-height: 1.25;
    }
    .login-card-copy {
        margin-top: 3px;
        color: #64748b;
        font-size: .76rem;
        line-height: 1.4;
    }
    .st-key-login_card [data-baseweb="input"] {
        min-height: 46px !important;
        border-radius: 8px !important;
    }
    .st-key-login_card .st-key-btn_login_submit button {
        min-height: 46px !important;
        height: 46px !important;
        margin-top: 7px;
        border-radius: 8px !important;
        font-size: .84rem !important;
    }
    .login-divider {
        display: flex;
        align-items: center;
        gap: 10px;
        margin: 17px 0 13px;
        color: #94a3b8;
        font-size: .68rem;
        font-weight: 750;
        text-transform: uppercase;
    }
    .login-divider::before,
    .login-divider::after {
        content: "";
        flex: 1;
        height: 1px;
        background: #e2e8f0;
    }
    .st-key-login_secondary_actions [data-testid="stHorizontalBlock"] {
        gap: 12px !important;
    }
    .st-key-login_secondary_actions button {
        min-height: 42px !important;
        height: 42px !important;
        border-radius: 8px !important;
        font-size: .75rem !important;
    }
    .login-security-note {
        margin: 15px 0 0;
        padding-top: 13px;
        border-top: 1px solid #edf1f5;
        color: #64748b;
        font-size: .7rem;
        text-align: center;
    }
    @media (max-width: 640px) {
        .stApp:has(.st-key-login_card) .block-container,
        .stApp:has(.st-key-login_card) [data-testid="stMainBlockContainer"],
        .stApp:has(.st-key-login_card) .stMainBlockContainer {
            padding-top: 10px !important;
            padding-left: 9px !important;
            padding-right: 9px !important;
        }
        .st-key-login_card { padding: 18px 15px 16px; }
        .st-key-login_header { margin-bottom: 10px !important; }
        .st-key-login_header .app-header-blue { padding: 13px 14px !important; }
        .st-key-login_secondary_actions [data-testid="stHorizontalBlock"] { gap: 8px !important; }
        .st-key-login_secondary_actions button { font-size: .68rem !important; }
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
    .envios-title {
        margin: 2px 0 3px;
        color: #0f172a;
        font-size: 1.15rem;
        font-weight: 850;
    }
    .envios-copy {
        margin-bottom: 14px;
        color: #64748b;
        font-size: .8rem;
    }
    .st-key-envios_metricas [data-testid="stHorizontalBlock"] { gap: 10px !important; }
    .st-key-envios_metricas [data-testid="stMetric"] {
        min-height: 76px;
        padding: 11px 12px !important;
        border-radius: 8px !important;
        box-shadow: none !important;
    }
    .envios-section-label {
        margin: 20px 0 9px;
        color: #475569;
        font-size: .7rem;
        font-weight: 850;
        letter-spacing: .06em;
        text-transform: uppercase;
    }
    .shipment-card {
        margin-bottom: 10px;
        padding: 14px 15px;
        background: #ffffff;
        border: 1px solid #dbe3ee;
        border-left: 4px solid #0757c8;
        border-radius: 8px;
        box-shadow: 0 5px 14px rgba(15, 23, 42, .05);
    }
    .shipment-card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 8px;
    }
    .shipment-tracking { color: #0f172a; font-size: .9rem; font-weight: 850; }
    .shipment-status {
        padding: 4px 8px;
        color: #0757c8;
        background: #eaf2ff;
        border-radius: 999px;
        font-size: .66rem;
        font-weight: 800;
        white-space: nowrap;
    }
    .shipment-description { color: #334155; font-size: .78rem; line-height: 1.4; }
    .shipment-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px 12px;
        margin-top: 9px;
        color: #64748b;
        font-size: .7rem;
    }
    .shipment-flags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .shipment-flag {
        padding: 3px 7px;
        color: #64748b;
        background: #f1f5f9;
        border: 1px solid #e2e8f0;
        border-radius: 999px;
        font-size: .64rem;
        font-weight: 750;
    }
    .shipment-flag.ok { color: #166534; background: #ecfdf5; border-color: #bbf7d0; }
    .shipment-empty {
        padding: 20px 16px;
        color: #64748b;
        background: #f8fafc;
        border: 1px dashed #cbd5e1;
        border-radius: 8px;
        text-align: center;
        font-size: .8rem;
    }
    .quote-shipment-card {
        margin: 0 0 9px;
        padding: 14px 15px;
        background: #ffffff;
        border: 1px solid #dbe3ee;
        border-radius: 8px;
    }
    .quote-shipment-card.is-ready { border-left: 4px solid #16a34a; }
    .quote-shipment-card.is-locked { border-left: 4px solid #94a3b8; background: #f8fafc; }
    .quote-shipment-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
    }
    .quote-shipment-id { color: #0f172a; font-weight: 850; }
    .quote-shipment-state { color: #64748b; font-size: .7rem; font-weight: 800; }
    .quote-shipment-card.is-ready .quote-shipment-state { color: #15803d; }
    .quote-shipment-info { margin-top: 7px; color: #475569; font-size: .74rem; line-height: 1.45; }
    [class*="st-key-docs_locked_"] {
        display: flex !important;
        flex-direction: column !important;
        gap: 7px !important;
        margin: 8px 0 14px !important;
    }
    [class*="st-key-docs_locked_"] button { min-height: 42px !important; }
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
    .quote-hero {
        margin: 0 0 16px;
        padding: 4px 0 14px;
        background: transparent;
        border: 0;
        border-bottom: 1px solid #dbe3ee;
    }
    .quote-hero small {
        display: block;
        margin-bottom: 4px;
        color: #087050;
        font-size: .62rem;
        font-weight: 900;
        letter-spacing: .06em;
        text-transform: uppercase;
    }
    .quote-hero h2 {
        margin: 0 !important;
        padding: 0 !important;
        color: #0f172a;
        font-size: 1.18rem !important;
        line-height: 1.3;
    }
    .quote-hero h2 span { color: #0757c8; }
    .quote-hero p {
        margin: 5px 0 0;
        color: #526175;
        font-size: .76rem;
        line-height: 1.45;
    }
    .quote-stage {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        margin: 18px 0 10px;
    }
    .quote-stage-number {
        display: grid;
        place-items: center;
        flex: 0 0 28px;
        width: 28px;
        height: 28px;
        color: #ffffff;
        background: #0757c8;
        border-radius: 7px;
        font-size: .72rem;
        font-weight: 900;
    }
    .quote-stage small {
        display: block;
        margin-bottom: 2px;
        color: #087050;
        font-size: .59rem;
        font-weight: 900;
        letter-spacing: .05em;
        text-transform: uppercase;
    }
    .quote-stage b {
        display: block;
        color: #0f172a;
        font-size: .9rem;
        line-height: 1.3;
    }
    .quote-stage p {
        margin: 3px 0 0;
        color: #64748b;
        font-size: .69rem;
        line-height: 1.4;
    }
    [class*="st-key-cotizador_origen_"] {
        min-height: 130px;
        padding: 14px 15px 12px;
        background: #ffffff;
        border: 1px solid #d6e0eb;
        border-radius: 8px;
        box-sizing: border-box;
    }
    .st-key-cotizador_origen_china {
        background: #f0f7ff;
        border: 2px solid #0757c8;
    }
    .quote-route-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
    }
    .quote-route-place { display:flex; align-items:center; gap:9px; min-width:0; }
    .quote-route-code {
        display: grid;
        place-items: center;
        flex: 0 0 34px;
        width: 34px;
        height: 34px;
        color: #ffffff;
        background: #c92a2a;
        border-radius: 7px;
        font-size: .66rem;
        font-weight: 900;
    }
    .quote-route-code.us { background: #173c64; }
    .quote-route-place b { display:block; color:#0f172a; font-size:.87rem; }
    .quote-route-place small { display:block; margin-top:2px; color:#64748b; font-size:.62rem; }
    .quote-route-badge {
        flex: none;
        padding: 4px 8px;
        color: #0757c8;
        background: #dbeafe;
        border-radius: 999px;
        font-size: .61rem;
        font-weight: 850;
    }
    .quote-route-badge.soon { color:#64748b; background:#eef2f6; }
    .quote-route-copy { margin: 10px 0 0; color:#526175; font-size:.69rem; line-height:1.4; }
    [class*="st-key-cotizador_origen_"] .stButton > button {
        min-height: 34px !important;
        height: 34px !important;
        margin-top: 8px !important;
        border-radius: 6px !important;
        box-shadow: none !important;
        font-size: .7rem !important;
    }
    .st-key-cotizador_origen_china .stButton > button:disabled {
        opacity: 1 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        background: #0757c8 !important;
        border-color: #0757c8 !important;
    }
    .quote-subhead {
        margin: 14px 0 8px;
        color: #334155;
        font-size: .69rem;
        font-weight: 850;
    }
    .quote-capacity-note {
        margin: 11px 0 8px;
        padding: 10px 12px;
        color: #526175;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 3px solid #087050;
        border-radius: 7px;
        font-size: .67rem;
        line-height: 1.45;
    }
    .st-key-vista_cotizador [data-testid="stMetric"] {
        border: 1px solid #dbe3ee !important;
        border-top: 3px solid #0757c8 !important;
        border-radius: 8px !important;
        box-shadow: none !important;
    }
    .st-key-vista_cotizador [data-testid="stWidgetLabel"] p {
        color: #334155 !important;
        -webkit-text-fill-color: #334155 !important;
        font-size: .74rem !important;
        font-weight: 800 !important;
    }
    .st-key-vista_cotizador div[data-baseweb="select"] > div,
    .st-key-vista_cotizador [data-testid="stNumberInputContainer"] {
        border-color: #b9c7d6 !important;
        border-radius: 7px !important;
        box-shadow: none !important;
    }
    .st-key-vista_cotizador div[data-baseweb="select"] > div:focus-within,
    .st-key-vista_cotizador [data-testid="stNumberInputContainer"]:focus-within {
        border-color: #0757c8 !important;
        box-shadow: 0 0 0 2px rgba(7,87,200,.10) !important;
    }
    @media (max-width: 700px) {
        .quote-hero { padding: 14px 15px; }
        .quote-stage { margin-top: 15px; }
        [class*="st-key-cotizador_origen_"] { min-height: 0; }
        .quote-route-head { align-items: flex-start; }
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

    .actividad-politicas {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        margin-top: 12px;
        padding: 15px 16px;
        background: #fff8e6;
        border: 1px solid #f0d28b;
        border-left: 4px solid #d89b22;
        border-radius: 6px;
        color: #243447;
        box-sizing: border-box;
    }
    .st-key-vista_actividad {
        display: block !important;
        min-height: 0 !important;
        padding-bottom: 0 !important;
        overflow: visible !important;
        box-sizing: border-box !important;
    }
    .st-key-safe_actividad {
        display: block !important;
        width: 100% !important;
        height: calc(var(--ccm-nav-clearance, 109px) + 28px) !important;
        min-height: calc(var(--ccm-nav-clearance, 109px) + 28px) !important;
        margin: 0 !important;
        padding: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    [data-testid="stElementContainer"]:has(> .st-key-safe_actividad),
    [data-testid="stElementContainer"]:has(> [class~="st-key-safe_actividad"]),
    [data-testid="stLayoutWrapper"]:has(> .st-key-safe_actividad) {
        height: calc(var(--ccm-nav-clearance, 109px) + 28px) !important;
        min-height: calc(var(--ccm-nav-clearance, 109px) + 28px) !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
    .actividad-politicas-copy {
        display: flex;
        align-items: flex-start;
        gap: 11px;
        min-width: 0;
    }
    .actividad-politicas-icon {
        display: grid;
        place-items: center;
        width: 28px;
        height: 28px;
        flex: 0 0 28px;
        background: #d89b22;
        color: #17212b;
        border-radius: 50%;
        font-size: .78rem;
        font-weight: 900;
    }
    .actividad-politicas-copy small {
        display: block;
        margin-bottom: 3px;
        color: #8a641b;
        font-size: .60rem;
        font-weight: 800;
        letter-spacing: 0;
    }
    .actividad-politicas-copy b {
        display: block;
        color: #1c2d40;
        font-size: .86rem;
        line-height: 1.25;
    }
    .actividad-politicas-copy p {
        max-width: 520px;
        margin: 4px 0 0;
        color: #586675;
        font-size: .68rem;
        line-height: 1.4;
    }
    a.actividad-politicas-cta,
    a.actividad-politicas-cta:link,
    a.actividad-politicas-cta:visited,
    a.actividad-politicas-cta:hover,
    a.actividad-politicas-cta:active {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        gap: 12px;
        min-width: 166px;
        padding: 10px 12px;
        background: #173c64 !important;
        color: #ffffff !important;
        text-decoration: none !important;
        border: 1px solid #173c64;
        border-radius: 5px;
        box-sizing: border-box;
        font-size: .72rem;
        font-weight: 800;
        transition: transform 160ms ease, background-color 160ms ease;
    }
    .actividad-politicas-cta * {
        color: inherit !important;
        text-decoration: none !important;
    }
    a.actividad-politicas-cta:hover {
        transform: translateY(-1px);
        background: #0b2341 !important;
    }
    a.actividad-politicas-cta:focus-visible {
        outline: 3px solid #f2c75c;
        outline-offset: 3px;
    }
    @media (max-width: 640px) {
        .actividad-politicas {
            align-items: stretch;
            flex-direction: column;
            gap: 13px;
            padding: 14px 12px;
        }
        a.actividad-politicas-cta { width: 100% !important; min-width: 0; }
    }

    .actividad-formato {
        margin-top: 0;
        padding: 16px;
        background: #eef8f2;
        border: 1px solid #b9dec7;
        border-left: 4px solid #217346;
        border-radius: 6px;
        color: #20352a;
        box-sizing: border-box;
        box-shadow: 0 7px 16px rgba(33, 115, 70, .08);
    }
    .actividad-formato-gap {
        display: block;
        width: 100%;
        height: 20px;
        min-height: 20px;
    }
    .actividad-formato-head {
        display: flex;
        align-items: flex-start;
        gap: 11px;
    }
    .actividad-formato-icon {
        display: grid;
        place-items: center;
        width: 38px;
        height: 32px;
        flex: 0 0 38px;
        background: #217346;
        color: #ffffff;
        border-radius: 4px;
        font-size: .62rem;
        font-weight: 900;
    }
    .actividad-formato-head small {
        display: block;
        margin-bottom: 3px;
        color: #347553;
        font-size: .60rem;
        font-weight: 800;
        letter-spacing: 0;
    }
    .actividad-formato-head b {
        display: block;
        color: #173b29;
        font-size: .90rem;
        line-height: 1.25;
    }
    .actividad-formato-head p {
        max-width: 720px;
        margin: 5px 0 0;
        color: #50685b;
        font-size: .69rem;
        line-height: 1.45;
    }
    .actividad-formato-pasos {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        margin: 13px 0;
        padding: 0;
        list-style: none;
    }
    .actividad-formato-pasos li {
        display: flex;
        align-items: center;
        gap: 7px;
        min-width: 0;
        color: #365244;
        font-size: .64rem;
        font-weight: 700;
        line-height: 1.3;
    }
    .actividad-formato-pasos li span {
        display: grid;
        place-items: center;
        width: 22px;
        height: 22px;
        flex: 0 0 22px;
        background: #d7eddf;
        color: #17603a;
        border-radius: 50%;
        font-size: .62rem;
        font-weight: 900;
    }
    .actividad-formato-acciones {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 9px;
    }
    a.actividad-formato-descarga,
    a.actividad-formato-descarga:link,
    a.actividad-formato-descarga:visited,
    a.actividad-formato-whatsapp,
    a.actividad-formato-whatsapp:link,
    a.actividad-formato-whatsapp:visited {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        gap: 10px;
        min-height: 40px;
        padding: 9px 11px;
        border-radius: 5px;
        box-sizing: border-box;
        font-size: .68rem;
        font-weight: 800;
        text-decoration: none !important;
        transition: transform 160ms ease, background-color 160ms ease;
    }
    a.actividad-formato-descarga,
    a.actividad-formato-descarga:link,
    a.actividad-formato-descarga:visited {
        background: #217346 !important;
        color: #ffffff !important;
        border: 1px solid #217346;
    }
    a.actividad-formato-whatsapp,
    a.actividad-formato-whatsapp:link,
    a.actividad-formato-whatsapp:visited {
        background: #ffffff !important;
        color: #12613f !important;
        border: 1px solid #56a879;
    }
    .actividad-formato-acciones a * {
        color: inherit !important;
        text-decoration: none !important;
    }
    a.actividad-formato-descarga:hover,
    a.actividad-formato-whatsapp:hover {
        transform: translateY(-1px);
        text-decoration: none !important;
    }
    a.actividad-formato-descarga:hover { background: #185f39 !important; color: #ffffff !important; }
    a.actividad-formato-whatsapp:hover { background: #e0f3e7 !important; color: #0e5134 !important; }
    .actividad-formato-acciones a:focus-visible {
        outline: 3px solid #6bc28d;
        outline-offset: 3px;
    }
    @media (max-width: 640px) {
        .actividad-formato-gap { height: 16px; min-height: 16px; }
        .actividad-formato { padding: 14px 12px; }
        .actividad-formato-pasos { grid-template-columns: 1fr; gap: 7px; }
        .actividad-formato-acciones { grid-template-columns: 1fr; }
    }

    .promo-ad-card {
        position: relative;
        z-index: 1;
        overflow: hidden;
        background: #0b2341;
        border: 1px solid #173c64;
        border-radius: 8px;
        padding: 0;
        color: #ffffff;
        margin: 16px 0 22px 0;
        box-shadow: 0 16px 34px rgba(11, 35, 65, 0.22);
        box-sizing: border-box;
        scroll-margin-top: calc(var(--header-offset, 208px) + 12px);
        animation: promo-ad-enter 360ms ease-out both;
    }
    .promo-ad-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        padding: 24px 24px 20px;
        border-top: 4px solid #f2b84b;
    }
    .promo-ad-heading { min-width: 0; }
    .promo-ad-kicker {
        display: flex;
        align-items: center;
        gap: 7px;
        font-size: 0.66rem;
        font-weight: 800;
        letter-spacing: 0;
        text-transform: uppercase;
        color: #a9c4df;
        margin: 0 0 7px;
    }
    .promo-ad-live {
        width: 7px;
        height: 7px;
        flex: 0 0 7px;
        border-radius: 50%;
        background: #4ade80;
        box-shadow: 0 0 0 4px rgba(74, 222, 128, .13);
        animation: promo-ad-pulse 2.2s ease-in-out infinite;
    }
    .promo-ad-title {
        font-size: 1.52rem;
        font-weight: 900;
        line-height: 1.16;
        margin: 0 0 6px;
        color: #ffffff;
        letter-spacing: 0;
    }
    .promo-ad-subtitle {
        max-width: 430px;
        font-size: .84rem;
        line-height: 1.4;
        color: #cbd9e8;
        margin: 0;
    }
    .promo-ad-deadline {
        display: grid;
        width: 108px;
        min-width: 108px;
        padding: 11px 8px 10px;
        text-align: center;
        background: #f2b84b;
        color: #17212b;
        border-radius: 6px;
        box-sizing: border-box;
    }
    .promo-ad-deadline strong { font-size: 2.25rem; line-height: .9; font-weight: 900; }
    .promo-ad-deadline span { margin-top: 4px; font-size: .78rem; font-weight: 900; }
    .promo-ad-deadline small { margin-top: 2px; font-size: .59rem; font-weight: 700; }
    .promo-ad-alert {
        display: flex;
        align-items: flex-start;
        gap: 11px;
        margin: 0 24px 18px;
        padding: 12px 14px;
        background: #fff6df;
        color: #3f2b0b;
        border-left: 4px solid #e7a72e;
        border-radius: 4px;
    }
    .promo-ad-alert > span {
        display: grid;
        place-items: center;
        width: 22px;
        height: 22px;
        flex: 0 0 22px;
        background: #e7a72e;
        color: #17212b;
        border-radius: 50%;
        font-size: .78rem;
        font-weight: 900;
    }
    .promo-ad-alert b { display: block; font-size: .80rem; }
    .promo-ad-alert p { margin: 3px 0 0; font-size: .74rem; line-height: 1.42; }
    .promo-ad-meta {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0;
        margin: 0 24px 18px;
        border-top: 1px solid #31506f;
        border-bottom: 1px solid #31506f;
    }
    .promo-ad-meta > div { padding: 10px 0; }
    .promo-ad-meta > div + div { padding-left: 18px; border-left: 1px solid #31506f; }
    .promo-ad-meta small,
    .promo-ad-address small,
    .promo-ad-address-head small {
        display: block;
        color: #7894af;
        font-size: .58rem;
        font-weight: 800;
        letter-spacing: 0;
        line-height: 1.25;
    }
    .promo-ad-meta b { display: block; margin-top: 3px; color: #fff; font-size: .76rem; }
    .promo-ad-addresses {
        margin: 0 24px 18px;
        padding: 16px;
        background: #f7f9fc;
        color: #14283d;
        border-radius: 6px;
    }
    .promo-ad-address-head {
        display: flex;
        align-items: center;
        gap: 9px;
        padding-bottom: 11px;
        border-bottom: 1px solid #d8e0e9;
    }
    .promo-ad-address-head > span { color: #d9961d; font-size: 1.30rem; line-height: 1; }
    .promo-ad-address-head b { display: block; margin-top: 2px; color: #14283d; font-size: .86rem; }
    .promo-ad-address-primary { padding: 12px 0; border-bottom: 1px solid #d8e0e9; }
    .promo-ad-address-primary b { font-size: .95rem !important; color: #0d3155 !important; }
    .promo-ad-translations {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 18px;
        padding-top: 12px;
    }
    .promo-ad-address {
        min-width: 0;
    }
    .promo-ad-address b {
        display: block;
        margin-top: 4px;
        color: #31465b;
        font-size: .72rem;
        font-weight: 650;
        line-height: 1.42;
        overflow-wrap: anywhere;
    }
    a.promo-ad-cta,
    a.promo-ad-cta:link,
    a.promo-ad-cta:visited,
    a.promo-ad-cta:hover,
    a.promo-ad-cta:active {
        display: flex !important;
        align-items: center !important;
        gap: 11px;
        width: auto !important;
        min-height: 62px;
        box-sizing: border-box;
        margin: 0 24px 24px;
        background: #128c5b !important;
        color: #ffffff !important;
        text-decoration: none !important;
        border-radius: 6px;
        border: 1px solid #39c779;
        padding: 10px 12px;
        box-shadow: 0 8px 18px rgba(18, 140, 91, .24);
        transition: transform 160ms ease, background-color 160ms ease, box-shadow 160ms ease;
    }
    .promo-ad-cta *,
    .promo-ad-cta:link *,
    .promo-ad-cta:visited * {
        color: inherit !important;
        text-decoration: none !important;
    }
    .promo-ad-cta-icon {
        display: grid;
        place-items: center;
        width: 38px;
        height: 38px;
        flex: 0 0 38px;
        background: #ffffff;
        color: #128c5b !important;
        border-radius: 50%;
        font-size: .68rem;
        font-weight: 900;
    }
    .promo-ad-cta-copy { display: grid; min-width: 0; }
    .promo-ad-cta-copy b {
        color: #ffffff !important;
        font-size: .88rem;
        line-height: 1.2;
        text-decoration: none !important;
    }
    .promo-ad-cta-copy small {
        margin-top: 3px;
        color: #d8f8e6 !important;
        font-size: .64rem;
        line-height: 1.25;
        text-decoration: none !important;
    }
    .promo-ad-cta-arrow {
        display: grid;
        place-items: center;
        width: 30px;
        height: 30px;
        flex: 0 0 30px;
        margin-left: auto;
        background: rgba(255, 255, 255, .14);
        color: #ffffff !important;
        border-radius: 50%;
        font-size: 1rem;
        font-weight: 800;
    }
    a.promo-ad-cta:hover {
        transform: translateY(-2px);
        background: #0f744b !important;
        box-shadow: 0 11px 22px rgba(18, 140, 91, .30);
    }
    a.promo-ad-cta:focus-visible {
        outline: 3px solid #86efac;
        outline-offset: 3px;
    }
    @keyframes promo-ad-enter {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
    }
    @keyframes promo-ad-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: .55; }
    }
    @media (max-width: 640px) {
        .promo-ad-card { border-radius: 8px; }
        .promo-ad-top { align-items: flex-start; gap: 12px; padding: 18px 15px 15px; }
        .promo-ad-title { font-size: 1.24rem; }
        .promo-ad-subtitle { font-size: .76rem; }
        .promo-ad-deadline { width: 82px; min-width: 82px; padding: 9px 5px 8px; }
        .promo-ad-deadline strong { font-size: 1.78rem; }
        .promo-ad-deadline span { font-size: .69rem; }
        .promo-ad-alert,
        .promo-ad-meta,
        .promo-ad-addresses,
        .promo-ad-cta { margin-left: 15px; margin-right: 15px; }
        .promo-ad-alert { padding: 11px 10px; }
        .promo-ad-meta { grid-template-columns: 1fr; }
        .promo-ad-meta > div + div { padding-left: 0; border-left: 0; border-top: 1px solid #31506f; }
        .promo-ad-translations { grid-template-columns: 1fr; gap: 12px; }
        .promo-ad-addresses { padding: 14px 12px; }
        .promo-ad-address-primary b { font-size: .84rem !important; }
        .promo-ad-address b { font-size: .70rem; }
        a.promo-ad-cta { min-height: 60px; margin-bottom: 16px; padding: 9px 10px; }
        .promo-ad-cta-icon { width: 34px; height: 34px; flex-basis: 34px; }
        .promo-ad-cta-copy b { font-size: .80rem; }
        .promo-ad-cta-copy small { font-size: .58rem; }
        .promo-ad-cta-arrow { width: 28px; height: 28px; flex-basis: 28px; }
    }
    @media (prefers-reduced-motion: reduce) {
        .promo-ad-card,
        .promo-ad-live { animation: none; }
        .promo-ad-cta { transition: none; }
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
        animation: none !important;
        border: 1px solid #bde8d5;
        border-left: 4px solid #087050;
        background-color: #f0fdf7;
        padding: 11px 14px;
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
        color: #087050;
        margin: 0 0 2px 0;
    }
    .destino-seleccionado-dir {
        font-size: 0.92rem;
        font-weight: 800;
        color: #123d31;
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
    .client-home-title {
        margin: 2px 0 3px;
        color: #0f172a;
        font-size: 1.12rem;
        font-weight: 850;
        line-height: 1.3;
    }
    .client-home-copy {
        margin: 0 0 15px;
        color: #64748b;
        font-size: .84rem;
        line-height: 1.45;
    }
    .client-home-section {
        margin: 20px 0 10px;
        color: #334155;
        font-size: .72rem;
        font-weight: 850;
        letter-spacing: .06em;
        text-transform: uppercase;
    }
    [class*="st-key-home_origin_"] {
        min-height: 166px;
        padding: 15px 14px 12px;
        background: #ffffff;
        border: 1px solid #dbe3ee !important;
        border-radius: 8px !important;
        box-shadow: 0 5px 15px rgba(15, 23, 42, .05);
    }
    [class*="st-key-home_origin_"]:has(.home-origin-card[data-active="true"]) {
        border-top: 3px solid #0f9f6e !important;
    }
    .home-origin-card { min-height: 83px; }
    .home-origin-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 7px;
    }
    .home-origin-name {
        color: #0f172a;
        font-size: .96rem;
        font-weight: 850;
    }
    .home-origin-icon { margin-right: 5px; font-size: 1.12rem; }
    .home-origin-status {
        padding: 3px 7px;
        color: #526175;
        background: #f1f5f9;
        border-radius: 999px;
        font-size: .62rem;
        font-weight: 800;
        white-space: nowrap;
    }
    .home-origin-card[data-active="true"] .home-origin-status {
        color: #087050;
        background: #ddf7ed;
    }
    .home-origin-detail {
        min-height: 36px;
        color: #64748b;
        font-size: .72rem;
        line-height: 1.35;
    }
    [class*="st-key-home_origin_"] button {
        min-height: 38px !important;
        height: 38px !important;
        border-radius: 7px !important;
    }
    .st-key-portal_announcement_client {
        margin: 0 0 18px;
        padding: 14px 15px 13px;
        background: #ffffff;
        border: 1px solid #dbe3ee;
        border-radius: 8px;
        box-shadow: 0 8px 20px rgba(15, 23, 42, .06);
    }
    .portal-announcement {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 13px 14px;
        color: var(--ann-text);
        background: var(--ann-bg);
        border: 1px solid var(--ann-border);
        border-left: 4px solid var(--ann-accent);
        border-radius: 7px;
    }
    .portal-announcement-icon {
        display: grid;
        place-items: center;
        width: 38px;
        height: 38px;
        flex: 0 0 38px;
        background: #ffffff;
        border: 1px solid var(--ann-border);
        border-radius: 7px;
        font-size: 1.05rem;
    }
    .portal-announcement-copy { min-width: 0; }
    .portal-announcement-label {
        display: block;
        margin-bottom: 3px;
        color: var(--ann-accent);
        font-size: .62rem;
        font-weight: 850;
        letter-spacing: .06em;
        text-transform: uppercase;
    }
    .portal-announcement-title {
        color: var(--ann-text);
        font-size: .94rem;
        font-weight: 850;
        line-height: 1.3;
    }
    .portal-announcement-message {
        margin-top: 4px;
        color: var(--ann-text);
        font-size: .76rem;
        line-height: 1.5;
    }
    .st-key-portal_announcement_client [data-testid="stHorizontalBlock"] {
        gap: 10px !important;
        margin-top: 10px;
    }
    .st-key-portal_announcement_client button,
    .st-key-portal_announcement_client a {
        min-height: 39px !important;
        height: 39px !important;
        border-radius: 7px !important;
        font-size: .72rem !important;
    }
    .st-key-home_help {
        margin-top: 20px;
        padding: 15px 16px 13px;
        background: #f6f9fd;
        border: 1px solid #dbe3ee !important;
        border-left: 4px solid #2563eb !important;
        border-radius: 8px !important;
    }
    .home-help-title {
        color: #0f172a;
        font-size: .9rem;
        font-weight: 850;
    }
    .home-help-copy {
        margin: 3px 0 10px;
        color: #64748b;
        font-size: .74rem;
    }
    .st-key-home_help button,
    .st-key-home_help a {
        min-height: 40px !important;
        border-radius: 7px !important;
    }
    @media (max-width: 640px) {
        [class*="st-key-home_origin_"] { min-height: 0; }
        .home-origin-card { min-height: 0; }
        .home-origin-detail { min-height: 0; margin-bottom: 10px; }
        .client-home-section { margin-top: 17px; }
        .st-key-portal_announcement_client { padding: 10px; }
        .portal-announcement { padding: 11px; }
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
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
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

    /* Mensajes de resultado: visibles y consistentes en cliente y administración. */
    div[data-testid="stAlert"] {
        width: 100% !important;
        min-height: 72px !important;
        margin: 12px 0 16px !important;
        padding: 0 !important;
        overflow: hidden !important;
        background: #ffffff !important;
        border: 1px solid #dbe3ee !important;
        border-left-width: 5px !important;
        border-radius: 9px !important;
        box-shadow: 0 7px 18px rgba(15, 23, 42, .08) !important;
    }
    div[data-testid="stAlert"] > div,
    div[data-testid="stAlert"] [data-baseweb="notification"] {
        width: 100% !important;
        min-height: 70px !important;
        padding: 13px 15px !important;
        align-items: flex-start !important;
        background: transparent !important;
    }
    div[data-testid="stAlert"] [data-testid="stMarkdownContainer"] {
        width: 100% !important;
        color: #334155 !important;
        -webkit-text-fill-color: #334155 !important;
        font-size: .84rem !important;
        font-weight: 650 !important;
        line-height: 1.45 !important;
    }
    div[data-testid="stAlert"] [data-testid="stMarkdownContainer"]::before {
        display: block;
        margin-bottom: 3px;
        color: #0f172a;
        -webkit-text-fill-color: #0f172a;
        font-size: .78rem;
        font-weight: 900;
        letter-spacing: .01em;
    }
    div[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
        margin: 0 !important;
        color: inherit !important;
        -webkit-text-fill-color: inherit !important;
        font-size: inherit !important;
        font-weight: inherit !important;
        line-height: inherit !important;
        background: transparent !important;
    }
    div[data-testid="stAlert"] svg {
        width: 22px !important;
        height: 22px !important;
        margin-top: 1px !important;
        flex: 0 0 22px !important;
        opacity: 1 !important;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {
        border-color: #bbf7d0 !important;
        border-left-color: #16a34a !important;
        background: #f0fdf4 !important;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) [data-testid="stMarkdownContainer"]::before {
        content: "Cambio guardado correctamente";
        color: #166534;
        -webkit-text-fill-color: #166534;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) {
        border-color: #fde68a !important;
        border-left-color: #d97706 !important;
        background: #fffbeb !important;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) [data-testid="stMarkdownContainer"]::before {
        content: "Revise la información";
        color: #92400e;
        -webkit-text-fill-color: #92400e;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {
        border-color: #fecaca !important;
        border-left-color: #dc2626 !important;
        background: #fef2f2 !important;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) [data-testid="stMarkdownContainer"]::before {
        content: "No se pudo completar la acción";
        color: #991b1b;
        -webkit-text-fill-color: #991b1b;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) {
        border-color: #bfdbfe !important;
        border-left-color: #0757c8 !important;
        background: #eff6ff !important;
    }
    div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) [data-testid="stMarkdownContainer"]::before {
        content: "Información";
        color: #1d4ed8;
        -webkit-text-fill-color: #1d4ed8;
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
        height: 38px !important;
        min-height: 38px !important;
        max-height: 38px !important;
        font-size: 0.78rem !important;
        border-radius: 7px !important;
        justify-content: center !important;
        padding: 0 12px !important;
        white-space: normal !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
    }
    .st-key-hub_china div.stButton > button[kind="primary"] {
        background: #0f9f6e !important;
        background-color: #0f9f6e !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border-color: #0f8a60 !important;
        box-shadow: 0 4px 10px rgba(15, 159, 110, .20) !important;
    }
    .st-key-hub_china div.stButton > button[kind="primary"] * {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
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
def cargar_clientes_control360_v2():
    """Caché de sesión para evitar el fallo de cache_data durante recargas del código."""
    clave_cache = "_ccm_control360_clientes_cache_v3"
    ahora = time.monotonic()
    cache = st.session_state.get(clave_cache)
    if cache and ahora - float(cache.get("creado", 0)) < 30:
        return cache["datos"]
    with get_db() as conn:
        datos = conn.execute(
            """
            SELECT codigo_casillero, nombre_completo, correo_principal, telefono_principal,
                   activo, dni, departamento, ciudad, direccion_exacta
            FROM usuarios WHERE rol = 'cliente'
            ORDER BY nombre_completo LIMIT 500
            """
        ).fetchall()
    st.session_state[clave_cache] = {"creado": ahora, "datos": datos}
    return datos


def cargar_expediente_control360_v2(casillero):
    """Agrupa y reutiliza las consultas operativas mientras se pulsa dentro del panel."""
    cas = formatear_casillero(casillero)
    clave_cache = "_ccm_control360_expediente_cache_v3"
    ahora = time.monotonic()
    cache = st.session_state.get(clave_cache)
    if (
        cache and cache.get("casillero") == cas
        and ahora - float(cache.get("creado", 0)) < 15
    ):
        return cache["datos"]
    with get_db() as conn:
        cotizaciones = conn.execute(
            """
            SELECT id, total_usd, COALESCE(fecha_creacion, fecha), confirmada,
                   COALESCE(estado, 'emitida'), fecha_confirmacion, tipo_carga,
                   destino_entrega, detalle_tarifa, tarifa_snapshot_json
            FROM cotizaciones WHERE codigo_casillero = ?
            ORDER BY COALESCE(fecha_creacion, fecha) DESC, id DESC LIMIT 200
            """,
            (cas,),
        ).fetchall()
        paquetes = conn.execute(
            """
            SELECT p.tracking, p.descripcion, p.contenedor_id, p.estado, p.fecha_actualizacion,
                   p.cotizacion_id, p.tipo_contenedor, p.recibido_bodega, p.pago_confirmado,
                   p.costo_manipulacion_usd, p.fecha_recepcion, p.ubicacion_actual, p.eta,
                   p.proximo_paso, p.incidencia, p.visible_cliente,
                   COALESCE(c.estado_pago, 'Pendiente'), c.referencia_pago,
                   c.comprobante_pago_url, COALESCE(c.estado_documentos, 'Bloqueados'),
                   COALESCE(c.incidencia_estado, 'Sin incidencia'), c.responsable_incidencia,
                   c.fecha_compromiso, c.receptor_entrega, c.fecha_entrega,
                   c.evidencia_entrega_url, COALESCE(c.canal_notificacion, 'Portal')
            FROM paquetes p LEFT JOIN control_envios c ON c.tracking = p.tracking
            WHERE p.codigo_casillero = ?
            ORDER BY p.fecha_actualizacion DESC LIMIT 200
            """,
            (cas,),
        ).fetchall()
    datos = (cotizaciones, paquetes)
    st.session_state[clave_cache] = {
        "casillero": cas, "creado": ahora, "datos": datos,
    }
    return datos


def invalidar_cache_clientes_control360():
    st.session_state.pop("_ccm_control360_clientes_cache_v3", None)


def invalidar_cache_expediente_control360():
    st.session_state.pop("_ccm_control360_expediente_cache_v3", None)


def pintar_control_cliente_360():
    """Expediente operativo completo, disponible únicamente para el superusuario."""
    st.markdown(
        '<div class="admin-section-heading">Control integral del cliente</div>'
        '<div class="admin-section-copy">Cotizaciones, carga, pagos, documentos, incidencias, comunicaciones y seguridad en un solo expediente.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <style>
            .st-key-control360_selector { padding: 12px 14px; background:#fff; border:1px solid #dbe3ee; border-radius:8px; }
            .control360-head { display:flex; justify-content:space-between; gap:14px; align-items:flex-start; margin:12px 0 14px; padding:15px 16px; background:#f8fafc; border-left:4px solid #0757c8; border-radius:7px; }
            .control360-head b { display:block; color:#0f172a; font-size:1rem; }
            .control360-head span { color:#64748b; font-size:.76rem; }
            .control360-badge { padding:5px 9px; color:#166534 !important; background:#dcfce7; border-radius:999px; font-weight:800; white-space:nowrap; }
            .control360-section { margin:14px 0 8px; color:#0f172a; font-size:.86rem; font-weight:850; }
            .control360-profile { margin:10px 0 18px; overflow:hidden; background:#ffffff; border:1px solid #dbe3ee; border-radius:9px; box-shadow:0 5px 15px rgba(15,23,42,.04); }
            .control360-profile-title { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 15px; background:#f8fafc; border-bottom:1px solid #e2e8f0; }
            .control360-profile-title b { color:#0f172a; font-size:.88rem; }
            .control360-profile-title span { color:#64748b; font-size:.7rem; font-weight:700; }
            .control360-profile-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
            .control360-profile-item { min-width:0; padding:14px 16px; border-bottom:1px solid #edf1f5; }
            .control360-profile-item:nth-child(odd) { border-right:1px solid #edf1f5; }
            .control360-profile-label { display:block; margin-bottom:5px; color:#64748b; font-size:.66rem; font-weight:850; letter-spacing:.04em; text-transform:uppercase; }
            .control360-profile-value { display:block; overflow-wrap:anywhere; color:#0f172a; font-size:.86rem; font-weight:750; line-height:1.35; }
            .control360-profile-value a { color:#0757c8 !important; -webkit-text-fill-color:#0757c8 !important; text-decoration:none; }
            .control360-profile-value a:hover { text-decoration:underline; }
            .control360-address { display:flex; align-items:flex-start; gap:10px; padding:13px 16px; color:#334155; background:#fbfdff; }
            .control360-address-icon { display:grid; place-items:center; flex:0 0 30px; width:30px; height:30px; color:#0757c8; background:#eaf3ff; border-radius:7px; font-size:.85rem; font-weight:900; }
            .control360-address small { display:block; margin-bottom:3px; color:#64748b; font-size:.65rem; font-weight:850; letter-spacing:.04em; text-transform:uppercase; }
            .control360-address b { display:block; color:#0f172a; font-size:.82rem; line-height:1.4; }
            .st-key-control360_action { padding:14px; background:#f8fafc; border:1px solid #dbe3ee; border-radius:8px; }
            .c360-case-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin:10px 0 12px; padding:13px 15px; background:#f8fafc; border:1px solid #dbe3ee; border-left:4px solid #0757c8; border-radius:7px; }
            .c360-case-head b { display:block; color:#0f172a; font-size:.88rem; line-height:1.35; }
            .c360-case-head span { display:block; margin-top:4px; color:#64748b; font-size:.68rem; }
            .c360-case-status { flex:none; padding:4px 8px; color:#0757c8 !important; background:#eaf3ff; border-radius:999px; font-size:.65rem !important; font-weight:850; }
            .c360-thread { margin:12px 0; padding:12px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; }
            .c360-message { max-width:88%; margin:7px 0; padding:10px 12px; color:#334155; background:#fff; border:1px solid #dbe3ee; border-radius:7px; font-size:.77rem; line-height:1.45; white-space:pre-wrap; overflow-wrap:anywhere; }
            .c360-message.client { margin-left:auto; background:#edf6ff; border-color:#bfd7f5; }
            .c360-message.system { max-width:max-content; margin:9px auto; padding:6px 10px; color:#64748b; background:#eef2f6; border:0; border-radius:999px; font-size:.68rem; text-align:center; }
            .c360-message small { display:block; margin-bottom:4px; color:#64748b; font-size:.62rem; font-weight:800; }
            [class*="st-key-soporte_composer_admin_"] { margin:12px 0 8px; padding:8px 10px; overflow:hidden; background:#fff; border:1px solid #cbd5e1; border-radius:8px; box-shadow:none !important; }
            [class*="st-key-soporte_composer_admin_"] [data-testid="stHorizontalBlock"] { align-items:flex-end !important; }
            [class*="st-key-soporte_composer_admin_"] [data-baseweb="textarea"], [class*="st-key-soporte_composer_admin_"] [data-baseweb="textarea"] > div { border:0 !important; outline:0 !important; background:#f8fafc !important; border-radius:7px !important; box-shadow:none !important; }
            [class*="st-key-soporte_composer_admin_"] textarea, [class*="st-key-soporte_composer_admin_"] textarea:focus, [class*="st-key-soporte_composer_admin_"] textarea:focus-visible { min-height:70px !important; outline:0 !important; color:#0f172a !important; background:#f8fafc !important; box-shadow:none !important; font-size:.8rem !important; }
            [class*="st-key-soporte_composer_admin_"] .stButton > button { min-height:70px !important; height:70px !important; color:#fff !important; -webkit-text-fill-color:#fff !important; background:#0757c8 !important; border-color:#0757c8 !important; box-shadow:none !important; }
            [class*="st-key-soporte_composer_admin_"] .stButton > button:focus, [class*="st-key-soporte_composer_admin_"] .stButton > button:active { outline:0 !important; box-shadow:none !important; }
            .st-key-control360_nav [role="radiogroup"] { display:flex !important; gap:6px !important; padding:6px !important; overflow-x:auto !important; background:#eef3f8 !important; border:1px solid #d8e1ec !important; border-radius:9px !important; }
            .st-key-control360_nav [role="radiogroup"] label { flex:1 0 auto !important; min-height:42px !important; margin:0 !important; padding:8px 13px !important; justify-content:center !important; background:#fff !important; border:1px solid #d7e0eb !important; border-radius:7px !important; box-shadow:0 1px 3px rgba(15,23,42,.05) !important; }
            .st-key-control360_nav [role="radiogroup"] label:has(input:checked) { background:#0757c8 !important; border-color:#0757c8 !important; box-shadow:0 4px 10px rgba(7,87,200,.20) !important; }
            .st-key-control360_nav [role="radiogroup"] label p { color:#334155 !important; -webkit-text-fill-color:#334155 !important; font-size:.76rem !important; font-weight:800 !important; white-space:nowrap; }
            .st-key-control360_nav [role="radiogroup"] label:has(input:checked) p { color:#fff !important; -webkit-text-fill-color:#fff !important; }
            [data-testid="stTabs"] [data-baseweb="tab-list"] {
                display:flex !important;
                gap:6px !important;
                padding:6px !important;
                overflow-x:auto !important;
                background:#eef3f8 !important;
                border:1px solid #d8e1ec !important;
                border-radius:9px !important;
                scrollbar-width:thin;
            }
            [data-testid="stTabs"] [role="tab"] {
                flex:1 0 auto !important;
                min-height:42px !important;
                padding:9px 14px !important;
                color:#334155 !important;
                -webkit-text-fill-color:#334155 !important;
                background:#ffffff !important;
                border:1px solid #d7e0eb !important;
                border-radius:7px !important;
                box-shadow:0 1px 3px rgba(15,23,42,.05) !important;
                font-size:.78rem !important;
                font-weight:800 !important;
                opacity:1 !important;
            }
            [data-testid="stTabs"] [role="tab"] *,
            [data-testid="stTabs"] [role="tab"] p,
            [data-testid="stTabs"] [role="tab"] span {
                color:inherit !important;
                -webkit-text-fill-color:inherit !important;
                opacity:1 !important;
            }
            [data-testid="stTabs"] [role="tab"]:hover {
                color:#0757c8 !important;
                -webkit-text-fill-color:#0757c8 !important;
                border-color:#8eb5ea !important;
                background:#f5f9ff !important;
            }
            [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
                color:#ffffff !important;
                -webkit-text-fill-color:#ffffff !important;
                background:#0757c8 !important;
                border-color:#0757c8 !important;
                box-shadow:0 4px 10px rgba(7,87,200,.20) !important;
            }
            [data-testid="stTabs"] [data-baseweb="tab-highlight"] { display:none !important; }
            @media(max-width:640px){
                .control360-head{flex-direction:column}
                .control360-badge{align-self:flex-start}
                .control360-profile-grid{grid-template-columns:1fr}
                .control360-profile-item:nth-child(odd){border-right:0}
                .c360-case-head{flex-direction:column}
                .c360-message{max-width:96%}
                [data-testid="stTabs"] [role="tab"] { flex:0 0 auto !important; min-width:112px !important; }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    usuarios = cargar_clientes_control360_v2()
    if not usuarios:
        st.info("No hay cuentas de cliente registradas.")
        return

    etiquetas = {
        f"{formatear_casillero(u[0])} · {u[1]} · {u[2]}": u for u in usuarios
    }
    with st.container(key="control360_selector"):
        etiqueta_cliente = st.selectbox(
            "Cliente", list(etiquetas), key="control360_cliente",
            help="Seleccione una cuenta para abrir su expediente operativo.",
        )
    usuario = etiquetas[etiqueta_cliente]
    cas, nombre, correo, telefono, activo, dni, departamento, ciudad, direccion = usuario
    cas = formatear_casillero(cas)
    estado_cuenta = "Cuenta activa" if activo else "Cuenta suspendida"
    st.markdown(
        '<div class="control360-head"><div>'
        f'<b>{html.escape(str(nombre))}</b><span>{html.escape(cas)} · {html.escape(str(correo))} · {html.escape(str(telefono))}</span>'
        f'</div><span class="control360-badge">{estado_cuenta}</span></div>',
        unsafe_allow_html=True,
    )

    cotizaciones, paquetes = cargar_expediente_control360_v2(cas)
    casos = obtener_casos_soporte(cas)
    notificaciones = cargar_notificaciones_cliente(cas, incluir_ocultas=True)
    pendientes = sum(1 for c in casos if str(c[5]) not in ("Resuelto", "Cerrado"))
    en_transito = sum(1 for p in paquetes if str(p[3]) not in ("Entregado", "Incidencia", "Retenido"))
    m1, m2, m3, m4 = st.columns(4, gap="small")
    m1.metric("Cotizaciones", len(cotizaciones))
    m2.metric("Envíos activos", en_transito)
    m3.metric("Casos abiertos", pendientes)
    m4.metric("Notificaciones", len(notificaciones))

    secciones_control360 = [
        "Resumen", "Cotizaciones", "Envíos", "Casos", "Comunicaciones", "Seguridad"
    ]
    with st.container(key="control360_nav"):
        seccion_control360 = st.radio(
            "Sección del expediente",
            secciones_control360,
            horizontal=True,
            label_visibility="collapsed",
            key=f"control360_seccion_{cas}",
        )
    if seccion_control360 == "Resumen":
        correo_texto = str(correo or "—")
        correo_href = "mailto:" + urllib.parse.quote(correo_texto, safe="@.+") if correo else ""
        correo_html = (
            f'<a href="{html.escape(correo_href)}">{html.escape(correo_texto)}</a>'
            if correo else "—"
        )
        st.markdown(
            '<section class="control360-profile">'
            '<div class="control360-profile-title"><b>Datos de cuenta y entrega</b>'
            '<span>Información registrada del cliente</span></div>'
            '<div class="control360-profile-grid">'
            '<div class="control360-profile-item"><span class="control360-profile-label">Identidad / DNI</span>'
            f'<span class="control360-profile-value">{html.escape(str(dni or "No registrada"))}</span></div>'
            '<div class="control360-profile-item"><span class="control360-profile-label">Ubicación</span>'
            f'<span class="control360-profile-value">{html.escape(str(ciudad or "—"))}, {html.escape(str(departamento or "—"))}</span></div>'
            '<div class="control360-profile-item"><span class="control360-profile-label">Correo electrónico</span>'
            f'<span class="control360-profile-value">{correo_html}</span></div>'
            '<div class="control360-profile-item"><span class="control360-profile-label">Teléfono / WhatsApp</span>'
            f'<span class="control360-profile-value">{html.escape(str(telefono or "No registrado"))}</span></div>'
            '</div>'
            '<div class="control360-address"><span class="control360-address-icon">⌖</span><div>'
            '<small>Dirección principal de entrega</small>'
            f'<b>{html.escape(str(direccion or "No registrada"))}</b></div></div>'
            '</section>',
            unsafe_allow_html=True,
        )
        if paquetes:
            st.markdown('<div class="control360-section">Actividad logística reciente</div>', unsafe_allow_html=True)
            st.dataframe(
                {
                    "Tracking": [p[0] for p in paquetes[:10]],
                    "Estado": [p[3] for p in paquetes[:10]],
                    "Pago": [p[16] for p in paquetes[:10]],
                    "Documentos": [p[19] for p in paquetes[:10]],
                    "Actualizado": [p[4] for p in paquetes[:10]],
                }, hide_index=True, use_container_width=True,
            )

    if seccion_control360 == "Cotizaciones":
        if not cotizaciones:
            st.info("Este cliente todavía no tiene cotizaciones.")
        else:
            opciones_cot = {
                f"CCM-COT-{int(c[0]):05d} · ${float(c[1] or 0):,.2f} · {c[4]}": c
                for c in cotizaciones
            }
            etiqueta_cot = st.selectbox("Cotización", list(opciones_cot), key=f"c360_cot_{cas}")
            cot = opciones_cot[etiqueta_cot]
            st.caption(f"Emitida: {cot[2]} · Destino: {cot[7] or 'No indicado'} · Tipo: {cot[6] or 'No indicado'}")
            estados_cot = [
                "emitida", "pendiente_revision", "en_revision", "requiere_correccion",
                "aprobada_tracking_generado", "confirmada", "rechazada", "vencida", "cancelada",
            ]
            estado_actual_cot = str(cot[4] or "emitida")
            nuevo_estado_cot = st.selectbox(
                "Estado administrativo", estados_cot,
                index=estados_cot.index(estado_actual_cot) if estado_actual_cot in estados_cot else 0,
                key=f"c360_cot_estado_{cot[0]}",
            )
            nota_cot = st.text_area(
                "Mensaje para el cliente", key=f"c360_cot_nota_{cot[0]}", height=75,
                placeholder="Explique la aprobación, revisión, cancelación o vencimiento.",
            )
            notificar_cot = st.toggle("Notificar este cambio en el portal", value=True, key=f"c360_cot_notif_{cot[0]}")
            if st.button("Guardar control de cotización", type="primary", key=f"c360_cot_save_{cot[0]}"):
                fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                confirmada = nuevo_estado_cot in {
                    "confirmada", "pendiente_revision", "en_revision",
                    "requiere_correccion", "aprobada_tracking_generado",
                }
                fecha_confirmacion = fecha if confirmada and not cot[5] else cot[5] if confirmada else None
                with get_db() as conn:
                    if nuevo_estado_cot == "aprobada_tracking_generado":
                        envio_valido = conn.execute(
                            "SELECT 1 FROM envios WHERE cotizacion_id=?", (int(cot[0]),)
                        ).fetchone()
                        if envio_valido is None:
                            st.error(
                                "Este estado solo puede asignarse desde Aprobaciones al generar los trackings."
                            )
                            st.stop()
                    conn.execute(
                        "UPDATE cotizaciones SET estado=?, confirmada=?, fecha_confirmacion=? "
                        "WHERE id=? AND codigo_casillero=?",
                        (nuevo_estado_cot, confirmada, fecha_confirmacion, int(cot[0]), cas),
                    )
                if notificar_cot:
                    crear_notificacion_cliente(
                        cas, f"Cotización CCM-COT-{int(cot[0]):05d}",
                        nota_cot.strip() or f"Su cotización cambió al estado {nuevo_estado_cot}.",
                        tipo="Cotización", tracking="", creado_por=st.session_state.get("usuario"),
                    )
                cargar_cotizaciones_db.clear()
                cargar_estados_cotizaciones_db.clear()
                cargar_cotizaciones_confirmadas_admin.clear()
                cargar_resumen_operativo_admin.clear()
                invalidar_cache_expediente_control360()
                st.success("Cotización actualizada y registrada en el expediente.")
                st.rerun()
            with st.expander("Detalle comercial guardado"):
                st.write(cot[8] or "Sin detalle tarifario.")
                if cot[9]:
                    try:
                        st.json(json.loads(cot[9]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        st.code(str(cot[9]), language="text")

    if seccion_control360 == "Envíos":
        if not paquetes:
            st.info("No hay paquetes registrados para este cliente. Use la sección Paquetes para crear el primero.")
        else:
            opciones_env = {f"{p[0]} · {p[3]}": p for p in paquetes}
            etiqueta_env = st.selectbox("Envío", list(opciones_env), key=f"c360_env_{cas}")
            paquete = opciones_env[etiqueta_env]
            tracking = str(paquete[0])
            estado_ops = list(ESTADOS_LOGISTICOS) + list(ESTADOS_LOGISTICOS_ESPECIALES)
            estado_env = st.selectbox(
                "Estado logístico", estado_ops,
                index=estado_ops.index(paquete[3]) if paquete[3] in estado_ops else 0,
                key=f"c360_env_estado_{tracking}",
            )
            e1, e2, e3 = st.columns(3, gap="small")
            with e1:
                ubicacion_env = st.text_input("Ubicación", value=paquete[11] or "", key=f"c360_env_ubi_{tracking}")
            with e2:
                eta_env = st.text_input("ETA (AAAA-MM-DD)", value=paquete[12] or "", key=f"c360_env_eta_{tracking}")
            with e3:
                fecha_compromiso = st.text_input("Compromiso", value=paquete[22] or "", key=f"c360_env_comp_{tracking}")
            proximo_env = st.text_input("Próximo paso", value=paquete[13] or proximo_estado_logistico(estado_env), key=f"c360_env_next_{tracking}")
            st.markdown('<div class="control360-section">Pago y documentos</div>', unsafe_allow_html=True)
            p1, p2 = st.columns(2, gap="medium")
            with p1:
                estados_pago = ["Pendiente", "En revisión", "Confirmado", "Rechazado", "Reembolsado"]
                estado_pago = st.selectbox("Estado del pago", estados_pago, index=estados_pago.index(paquete[16]) if paquete[16] in estados_pago else 0, key=f"c360_pago_{tracking}")
                referencia_pago = st.text_input("Referencia", value=paquete[17] or "", key=f"c360_pago_ref_{tracking}")
                comprobante_url = st.text_input("URL del comprobante", value=paquete[18] or "", key=f"c360_pago_url_{tracking}")
            with p2:
                estados_docs = ["Bloqueados", "Habilitados", "En revisión", "Reemplazados", "Anulados"]
                estado_docs = st.selectbox("Estado de documentos", estados_docs, index=estados_docs.index(paquete[19]) if paquete[19] in estados_docs else 0, key=f"c360_docs_{tracking}")
                recibido_env = st.toggle("Recibido en China", value=bool(paquete[7]), key=f"c360_rec_{tracking}")
                visible_env = st.toggle("Visible para el cliente", value=bool(paquete[15]), key=f"c360_vis_{tracking}")
            st.markdown('<div class="control360-section">Incidencia y entrega</div>', unsafe_allow_html=True)
            i1, i2 = st.columns(2, gap="medium")
            with i1:
                estados_inc = ["Sin incidencia", "Abierta", "En investigación", "Esperando cliente", "Resuelta"]
                estado_inc = st.selectbox("Estado de incidencia", estados_inc, index=estados_inc.index(paquete[20]) if paquete[20] in estados_inc else 0, key=f"c360_inc_estado_{tracking}")
                incidencia_env = st.text_area("Detalle visible", value=paquete[14] or "", height=75, key=f"c360_inc_{tracking}")
                responsable_inc = st.text_input("Responsable", value=paquete[21] or "", key=f"c360_inc_resp_{tracking}")
            with i2:
                receptor_entrega = st.text_input("Recibido por", value=paquete[23] or "", key=f"c360_ent_rec_{tracking}")
                fecha_entrega = st.text_input("Fecha de entrega", value=paquete[24] or "", key=f"c360_ent_fecha_{tracking}")
                evidencia_entrega = st.text_input("URL de evidencia", value=paquete[25] or "", key=f"c360_ent_url_{tracking}")
            mensaje_env = st.text_area("Actualización para el cliente", height=75, key=f"c360_env_msg_{tracking}", placeholder="Mensaje que aparecerá en su bandeja y línea de tiempo.")
            canal_env = st.selectbox("Canal registrado", ["Portal", "WhatsApp", "Correo", "Portal + WhatsApp"], index=0, key=f"c360_env_canal_{tracking}")
            permitir_retroceso = st.checkbox("Autorizar corrección manual de un estado anterior", key=f"c360_env_override_{tracking}")
            if st.button("Guardar control integral del envío", type="primary", key=f"c360_env_save_{tracking}"):
                fechas_validar = [valor.strip() for valor in (eta_env, fecha_compromiso, fecha_entrega) if valor.strip()]
                fechas_validas = all(_fecha_es_valida(valor) for valor in fechas_validar)
                if not fechas_validas:
                    st.error("ETA, compromiso y entrega deben usar el formato AAAA-MM-DD.")
                elif not permitir_retroceso and not transicion_logistica_valida(paquete[3], estado_env):
                    st.error("El nuevo estado retrocede el proceso. Active la autorización de corrección manual.")
                elif estado_env == "Entregado" and not (
                    receptor_entrega.strip() and fecha_entrega.strip() and evidencia_entrega.strip()
                ):
                    st.error("Para cerrar como Entregado debe registrar receptor, fecha y URL de evidencia.")
                elif evidencia_entrega.strip() and not url_anuncio_segura(evidencia_entrega):
                    st.error("La evidencia de entrega debe ser un enlace público HTTP o HTTPS válido.")
                else:
                    fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                    pago_confirmado = estado_pago == "Confirmado"
                    documentos_habilitados = estado_docs == "Habilitados"
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            """
                            SELECT estado, ubicacion_actual, eta, proximo_paso, incidencia,
                                   recibido_bodega, pago_confirmado, visible_cliente,
                                   COALESCE(version, 1), COALESCE(cantidad_bultos, 1),
                                   COALESCE(bultos_verificados, 0),
                                   COALESCE(estado_integridad, 'Pendiente'), contenedor_id
                            FROM paquetes WHERE tracking=? AND codigo_casillero=?
                            """,
                            (tracking, cas),
                        )
                        estado_previo_360 = cur.fetchone()
                        if estado_previo_360 is None:
                            st.error("El paquete ya no existe o fue reasignado. Actualice el expediente.")
                            st.stop()
                        if indice_estado_logistico(estado_env) >= indice_estado_logistico("En Bodega China") and not recibido_env:
                            st.error("No puede avanzar un envío sin confirmar su recepción física.")
                            st.stop()
                        if (
                            indice_estado_logistico(estado_env) >= indice_estado_logistico("En Consolidación")
                            and (
                                int(estado_previo_360[9] or 1) != int(estado_previo_360[10] or 0)
                                or str(estado_previo_360[11] or "") != "Verificado"
                            )
                        ):
                            st.error("Verifique físicamente todos los bultos desde Paquetes antes de avanzar.")
                            st.stop()
                        if (
                            indice_estado_logistico(estado_env) >= indice_estado_logistico("Asignado a Contenedor")
                            and not str(estado_previo_360[12] or "").strip()
                        ):
                            st.error("El envío necesita un contenedor asignado antes de avanzar.")
                            st.stop()
                        cur.execute(
                            """
                            UPDATE paquetes SET estado=?, ubicacion_actual=?, eta=?, proximo_paso=?,
                                incidencia=?, recibido_bodega=?, pago_confirmado=?, visible_cliente=?,
                                fecha_actualizacion=?, version=version+1
                            WHERE tracking=? AND codigo_casillero=? AND version=?
                            """,
                            (estado_env, ubicacion_env.strip(), eta_env.strip(), proximo_env.strip(),
                             incidencia_env.strip(), bool(recibido_env), pago_confirmado,
                             bool(visible_env), fecha, tracking, cas, int(estado_previo_360[8] or 1)),
                        )
                        if cur.rowcount != 1:
                            st.error(
                                "Otro operador modificó el envío. Recargue el expediente antes de guardar."
                            )
                            st.stop()
                        cur.execute(
                            """
                            INSERT INTO control_envios (
                                tracking, estado_pago, referencia_pago, comprobante_pago_url,
                                estado_documentos, incidencia_estado, responsable_incidencia,
                                fecha_compromiso, receptor_entrega, fecha_entrega,
                                evidencia_entrega_url, canal_notificacion, actualizado_por,
                                fecha_actualizacion
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(tracking) DO UPDATE SET
                                estado_pago=excluded.estado_pago,
                                referencia_pago=excluded.referencia_pago,
                                comprobante_pago_url=excluded.comprobante_pago_url,
                                estado_documentos=excluded.estado_documentos,
                                incidencia_estado=excluded.incidencia_estado,
                                responsable_incidencia=excluded.responsable_incidencia,
                                fecha_compromiso=excluded.fecha_compromiso,
                                receptor_entrega=excluded.receptor_entrega,
                                fecha_entrega=excluded.fecha_entrega,
                                evidencia_entrega_url=excluded.evidencia_entrega_url,
                                canal_notificacion=excluded.canal_notificacion,
                                actualizado_por=excluded.actualizado_por,
                                fecha_actualizacion=excluded.fecha_actualizacion
                            """,
                            (tracking, estado_pago, referencia_pago.strip(), comprobante_url.strip(),
                             estado_docs, estado_inc, responsable_inc.strip(), fecha_compromiso.strip(),
                             receptor_entrega.strip(), fecha_entrega.strip(), evidencia_entrega.strip(),
                             canal_env, st.session_state.get("usuario") or "superadmin", fecha),
                        )
                        mensaje_traza_360 = mensaje_env.strip() or f"Estado confirmado: {estado_env}."
                        registrar_trazabilidad_paquete(
                            cur, tracking, cas, "CONTROL_360",
                            estado_previo_360[0], estado_env,
                            {
                                "estado": estado_previo_360[0], "ubicacion": estado_previo_360[1],
                                "eta": estado_previo_360[2], "proximo_paso": estado_previo_360[3],
                                "incidencia": estado_previo_360[4], "recibido": bool(estado_previo_360[5]),
                                "pago": bool(estado_previo_360[6]), "visible": bool(estado_previo_360[7]),
                            },
                            {
                                "estado": estado_env, "ubicacion": ubicacion_env.strip(),
                                "eta": eta_env.strip(), "proximo_paso": proximo_env.strip(),
                                "incidencia": incidencia_env.strip(), "recibido": bool(recibido_env),
                                "pago": pago_confirmado, "visible": bool(visible_env),
                                "estado_pago": estado_pago, "documentos": estado_docs,
                                "incidencia_estado": estado_inc, "receptor": receptor_entrega.strip(),
                                "fecha_entrega": fecha_entrega.strip(),
                            },
                            mensaje_traza_360,
                            f"Pago: {estado_pago}; documentos: {estado_docs}",
                            bool(visible_env), st.session_state.get("usuario") or "superadmin", fecha,
                        )
                        if mensaje_env.strip() or estado_env != paquete[3]:
                            cur.execute(
                                """
                                INSERT INTO eventos_tracking (
                                    tracking, codigo_casillero, estado, ubicacion, mensaje_cliente,
                                    nota_interna, fecha_evento, creado_por, visible_cliente
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (tracking, cas, estado_env, ubicacion_env.strip(),
                                 mensaje_env.strip() or f"Estado actualizado: {estado_env}.",
                                 f"Pago: {estado_pago}; documentos: {estado_docs}", fecha,
                                 st.session_state.get("usuario") or "superadmin", bool(visible_env)),
                            )
                    if documentos_habilitados and not (recibido_env or pago_confirmado):
                        st.warning("Los documentos se marcaron habilitados manualmente sin recepción ni pago confirmado.")
                    crear_notificacion_cliente(
                        cas, f"Actualización de {tracking}",
                        mensaje_env.strip() or f"Su envío ahora está en estado {estado_env}.",
                        tipo="Seguimiento", prioridad="Urgente" if estado_inc in ("Abierta", "En investigación") else "Normal",
                        tracking=tracking, canal=canal_env,
                    )
                    cargar_paquetes_db.clear()
                    cargar_eventos_tracking_db.clear()
                    cargar_trazabilidad_cliente_db.clear()
                    cargar_trazabilidad_paquete_db.clear()
                    cargar_paquetes_admin.clear()
                    buscar_paquetes_admin.clear()
                    buscar_tracking_exacto_admin.clear()
                    cargar_paquetes_casillero_admin.clear()
                    cargar_clientes_con_paquetes_admin.clear()
                    cargar_metricas_paquetes_admin.clear()
                    cargar_eventos_tracking_admin.clear()
                    cargar_resumen_operativo_admin.clear()
                    invalidar_cache_expediente_control360()
                    st.success("Control del envío actualizado; el cliente recibió una notificación.")
                    st.rerun()

    if seccion_control360 == "Casos":
        if not casos:
            st.info("No hay solicitudes o incidencias registradas.")
        else:
            opciones_caso = {f"#{int(c[0]):04d} · {c[3]} · {c[5]}": c for c in casos}
            st.markdown(
                '<div class="control360-section">Bandeja de solicitudes</div>',
                unsafe_allow_html=True,
            )
            etiqueta_caso = st.selectbox(
                "Seleccione una solicitud", list(opciones_caso), key=f"c360_caso_{cas}"
            )
            caso = opciones_caso[etiqueta_caso]
            caso_id = int(caso[0])
            st.markdown(
                '<div class="c360-case-head"><div>'
                f'<b>#{caso_id:04d} · {html.escape(str(caso[3]))}</b>'
                f'<span>{html.escape(str(caso[2]))}'
                f'{" · Tracking: " + html.escape(str(caso[1])) if caso[1] else ""}'
                f' · Actualizado {html.escape(str(caso[10]))}</span></div>'
                f'<span class="c360-case-status">{html.escape(str(caso[5]))}</span></div>',
                unsafe_allow_html=True,
            )
            mensajes_caso = obtener_hilo_soporte(caso_id, cas)
            st.markdown(
                '<div class="c360-message client"><small>Cliente · solicitud inicial</small>'
                f'{html.escape(str(caso[4]))}</div>',
                unsafe_allow_html=True,
            )
            tiene_respuesta_nueva = any(str(m[1]) == "operador" for m in mensajes_caso)
            if caso[7] and not tiene_respuesta_nueva:
                st.markdown(
                    '<div class="c360-message"><small>Equipo CCM · respuesta anterior</small>'
                    f'{html.escape(str(caso[7]))}</div>',
                    unsafe_allow_html=True,
                )
            for mensaje in mensajes_caso:
                tipo_autor = str(mensaje[1])
                if tipo_autor == "cliente":
                    clase, autor = "client", "Cliente"
                elif tipo_autor == "sistema":
                    clase, autor = "system", "Sistema"
                else:
                    clase, autor = "", "Equipo CCM"
                st.markdown(
                    f'<div class="c360-message {clase}"><small>{autor} · '
                    f'{html.escape(str(mensaje[4]))}</small>{html.escape(str(mensaje[3]))}</div>',
                    unsafe_allow_html=True,
                )
            estados_caso = ["Abierto", "En revisión", "Esperando cliente", "Resuelto", "Cerrado"]
            estado_actual_caso = str(caso[5] or "Abierto")
            estado_sugerido_caso = (
                "Esperando cliente"
                if estado_actual_caso in ("Abierto", "En revisión")
                else estado_actual_caso
            )
            config_estado, config_prioridad = st.columns(2, gap="medium")
            with config_estado:
                estado_caso = st.selectbox(
                    "Estado", estados_caso,
                    index=estados_caso.index(estado_sugerido_caso)
                    if estado_sugerido_caso in estados_caso else 0,
                    key=f"c360_caso_estado_{caso_id}",
                )
            with config_prioridad:
                prioridades_caso = ["Baja", "Normal", "Alta", "Urgente"]
                prioridad_caso = st.selectbox(
                    "Prioridad", prioridades_caso,
                    index=prioridades_caso.index(caso[6]) if caso[6] in prioridades_caso else 1,
                    key=f"c360_caso_prio_{caso_id}",
                )
            clave_respuesta_admin = f"c360_caso_resp_{caso_id}"
            clave_limpiar_admin = f"_limpiar_admin_caso_{caso_id}"
            clave_flash_admin = f"_flash_admin_caso_{caso_id}"
            mensaje_flash_admin = st.session_state.pop(clave_flash_admin, "")
            if mensaje_flash_admin:
                st.toast(mensaje_flash_admin)
            if st.session_state.pop(clave_limpiar_admin, False):
                st.session_state.pop(clave_respuesta_admin, None)
            with st.container(key=f"soporte_composer_admin_{caso_id}"):
                campo_admin, enviar_admin = st.columns([5, 1.15], gap="small")
                with campo_admin:
                    respuesta_caso = st.text_area(
                        "Nueva respuesta al cliente", height=70,
                        placeholder="Escriba un mensaje para el cliente...",
                        key=clave_respuesta_admin,
                        label_visibility="collapsed",
                    )
                with enviar_admin:
                    enviar_respuesta_caso = st.button(
                        "Enviar", type="primary",
                        key=f"c360_caso_save_{caso_id}", use_container_width=True,
                    )
            actualizar_solo_caso = st.button(
                "Actualizar estado y prioridad", key=f"c360_caso_estado_save_{caso_id}",
            )
            if enviar_respuesta_caso:
                if not respuesta_caso.strip():
                    st.warning("Escriba la respuesta que se enviará al cliente.")
                elif agregar_mensaje_caso(
                    caso_id, cas, "operador", respuesta_caso, estado_caso
                ):
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE casos_cliente SET prioridad=? WHERE id=? AND codigo_casillero=?",
                            (prioridad_caso, caso_id, cas),
                        )
                    crear_notificacion_cliente(
                        cas, f"Nueva respuesta al caso #{caso_id:04d}", respuesta_caso.strip(),
                        tipo="Soporte", prioridad=prioridad_caso, tracking=caso[1] or "",
                    )
                    st.session_state[clave_limpiar_admin] = True
                    st.session_state[clave_flash_admin] = (
                        "Respuesta agregada al historial y enviada al cliente."
                    )
                    st.rerun()
            if actualizar_solo_caso:
                fecha = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                with get_db() as conn:
                    conn.execute(
                        "UPDATE casos_cliente SET estado=?, prioridad=?, "
                        "fecha_actualizacion=? WHERE id=? AND codigo_casillero=?",
                        (estado_caso, prioridad_caso, fecha, caso_id, cas),
                    )
                invalidar_cache_soporte()
                if estado_caso != estado_actual_caso:
                    crear_notificacion_cliente(
                        cas, f"Estado del caso #{caso_id:04d}",
                        f"Su solicitud ahora está en estado: {estado_caso}.",
                        tipo="Soporte", prioridad=prioridad_caso, tracking=caso[1] or "",
                    )
                st.success("Estado y prioridad actualizados sin modificar la conversación.")
                st.rerun()

    if seccion_control360 == "Comunicaciones":
        tipo_notif = st.selectbox("Tipo", ["Información", "Cotización", "Seguimiento", "Pago", "Documentos", "Soporte"], key=f"c360_not_tipo_{cas}")
        prioridad_notif = st.selectbox("Prioridad", ["Baja", "Normal", "Alta", "Urgente"], index=1, key=f"c360_not_prio_{cas}")
        titulo_notif = st.text_input("Título", max_chars=120, key=f"c360_not_titulo_{cas}")
        mensaje_notif = st.text_area("Mensaje", max_chars=1500, height=100, key=f"c360_not_msg_{cas}")
        canal_notif = st.selectbox("Canal registrado", ["Portal", "WhatsApp", "Correo", "Portal + WhatsApp"], key=f"c360_not_canal_{cas}")
        tracking_notif = st.selectbox("Tracking relacionado", ["Sin tracking"] + [str(p[0]) for p in paquetes], key=f"c360_not_track_{cas}")
        if st.button("Enviar al centro de notificaciones", type="primary", key=f"c360_not_send_{cas}"):
            if crear_notificacion_cliente(
                cas, titulo_notif, mensaje_notif, tipo_notif, prioridad_notif,
                "" if tracking_notif == "Sin tracking" else tracking_notif, canal_notif,
            ):
                st.success("Notificación publicada para este cliente.")
                st.rerun()
            else:
                st.warning("Escriba un título y un mensaje.")
        if notificaciones:
            st.markdown('<div class="control360-section">Historial de comunicaciones</div>', unsafe_allow_html=True)
            st.dataframe(
                {
                    "Fecha": [n[9] for n in notificaciones], "Título": [n[4] for n in notificaciones],
                    "Tipo": [n[2] for n in notificaciones], "Canal": [n[6] for n in notificaciones],
                    "Leída": ["Sí" if n[7] else "No" for n in notificaciones],
                    "Visible": ["Sí" if n[8] else "No" for n in notificaciones],
                }, hide_index=True, use_container_width=True,
            )
            opciones_hist_not = {
                f"#{int(n[0]):04d} · {n[4]} · {n[9]}": n for n in notificaciones
            }
            notif_hist_sel = st.selectbox(
                "Administrar notificación", list(opciones_hist_not), key=f"c360_not_admin_{cas}"
            )
            notif_hist = opciones_hist_not[notif_hist_sel]
            notif_visible = st.toggle(
                "Visible en el portal", value=bool(notif_hist[8]), key=f"c360_not_visible_{notif_hist[0]}"
            )
            if st.button("Guardar visibilidad", key=f"c360_not_vis_save_{notif_hist[0]}"):
                with get_db() as conn:
                    conn.execute(
                        "UPDATE notificaciones_cliente SET visible=? WHERE id=? AND codigo_casillero=?",
                        (bool(notif_visible), int(notif_hist[0]), cas),
                    )
                cargar_notificaciones_cliente.clear()
                st.success("Visibilidad de la notificación actualizada.")
                st.rerun()

    if seccion_control360 == "Seguridad":
        with st.container(key="control360_action"):
            cuenta_habilitada = st.toggle("Permitir inicio de sesión", value=bool(activo), key=f"c360_activo_{cas}")
            if st.button("Guardar estado de la cuenta", key=f"c360_activo_save_{cas}"):
                with get_db() as conn:
                    conn.execute("UPDATE usuarios SET activo=? WHERE codigo_casillero=? AND rol='cliente'", (bool(cuenta_habilitada), cas))
                invalidar_cache_clientes_control360()
                st.success("Estado de acceso actualizado.")
                st.rerun()
        clave_temporal = st.text_input("Nueva contraseña", type="password", key=f"c360_pwd_{cas}", placeholder="Vacío para generar una clave temporal")
        if st.button("Restablecer contraseña", key=f"c360_pwd_save_{cas}"):
            clave = clave_temporal.strip() or generar_clave_provisional()
            with get_db() as conn:
                conn.execute("UPDATE usuarios SET password_hash=? WHERE codigo_casillero=? AND rol='cliente'", (hash_pwd(clave), cas))
            st.success("Contraseña restablecida. Comparta la clave por un canal privado.")
            st.code(clave, language="text")
        st.markdown('<div class="control360-section">Permisos del portal</div>', unsafe_allow_html=True)
        permisos_360 = permisos_de(cas)
        per1, per2 = st.columns(2, gap="medium")
        with per1:
            st.caption("Países")
            p360_china = st.toggle("China", value=bool(permisos_360.get("hub_china")), key=f"c360_perm_cn_{cas}")
            p360_eeuu = st.toggle("Estados Unidos", value=bool(permisos_360.get("hub_eeuu")), key=f"c360_perm_us_{cas}")
            p360_hn = st.toggle("Honduras", value=bool(permisos_360.get("hub_honduras")), key=f"c360_perm_hn_{cas}")
        with per2:
            st.caption("Módulos")
            p360_cot = st.toggle("Cotizador", value=bool(permisos_360.get("mod_cotizador")), key=f"c360_perm_cot_{cas}")
            p360_cat = st.toggle("Catálogo", value=bool(permisos_360.get("mod_catalogo")), key=f"c360_perm_cat_{cas}")
            p360_hist = st.toggle("Cotizaciones", value=bool(permisos_360.get("mod_cotizaciones")), key=f"c360_perm_hist_{cas}")
            p360_env = st.toggle("Envíos", value=bool(permisos_360.get("mod_envios")), key=f"c360_perm_env_{cas}")
            p360_fic = st.toggle("Fichas", value=bool(permisos_360.get("mod_fichas")), key=f"c360_perm_fic_{cas}")
        if st.button("Guardar permisos", type="primary", key=f"c360_perm_save_{cas}"):
            guardar_permisos(
                cas,
                {
                    "hub_china": p360_china, "hub_eeuu": p360_eeuu,
                    "hub_honduras": p360_hn, "mod_cotizador": p360_cot,
                    "mod_catalogo": p360_cat, "mod_cotizaciones": p360_hist,
                    "mod_envios": p360_env, "mod_fichas": p360_fic,
                },
            )
            st.success("Permisos actualizados. Se aplicarán en el próximo refresco del cliente.")


def _fecha_es_valida(valor):
    try:
        datetime.strptime(str(valor), "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


if not st.session_state["autenticado"]:
    if st.session_state["vista_actual"] == "login":
        with st.container(key="login_header"):
            st.markdown(
                html_encabezado_institucional(
                    '<div class="app-greeting-sub">Consolidación Marítima China ➔ Honduras</div>',
                    extra_class="app-header-login",
                    extra_style="margin-bottom: 0; border-radius: 10px;",
                ),
                unsafe_allow_html=True,
            )

        with st.container(border=True, key="login_card"):
            st.markdown(
                '<div class="login-card-head">'
                '<div class="login-card-icon">🔐</div>'
                '<div><div class="login-card-title">Acceso a su casillero</div>'
                '<div class="login-card-copy">Consulte cotizaciones, documentos y el estado de sus envíos.</div></div>'
                '</div>',
                unsafe_allow_html=True,
            )
            u_ident = st.text_input(
                "Casillero, DNI o correo",
                placeholder="Ej. CCM-13011998 o correo@gmail.com",
                key="log_cas",
            )
            u_pass = st.text_input(
                "Contraseña",
                type="password",
                placeholder="Ingrese su contraseña",
                key="log_pwd",
            )

            if st.button("Ingresar a mi casillero", type="primary", key="btn_login_submit"):
                u_ident = (st.session_state.get("log_cas") or u_ident or "").strip()
                u_pass = st.session_state.get("log_pwd") or u_pass or ""
                if u_ident and u_pass:
                    permitido, espera_s = comprobar_limite_acceso(u_ident)
                    if not permitido:
                        st.error(f"Demasiados intentos fallidos. Espere {max(1, math.ceil(espera_s / 60))} minuto(s).")
                        st.stop()
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
                        limpiar_fallos_acceso(u_ident)
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
                        registrar_fallo_acceso(u_ident)
                        st.error("❌ Credenciales inválidas.")
                else:
                    st.warning("Complete todos los campos.")

            st.markdown('<div class="login-divider">Otras opciones</div>', unsafe_allow_html=True)
            with st.container(key="login_secondary_actions"):
                c_b1, c_b2 = st.columns(2, gap="medium")
                with c_b1:
                    if st.button("Recuperar contraseña", type="secondary", key="btn_login_recover"):
                        st.session_state["vista_actual"] = "recuperar"
                        st.rerun()
                with c_b2:
                    if st.button("Crear casillero", type="secondary", key="btn_login_register"):
                        st.session_state["vista_actual"] = "registro"
                        st.rerun()
            st.markdown(
                '<div class="login-security-note">🔒 Acceso protegido para clientes y personal autorizado.</div>',
                unsafe_allow_html=True,
            )

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
        st.markdown("### 🔄 Recuperar acceso")
        st.info(
            "Por seguridad, una contraseña no puede cambiarse únicamente con un correo o DNI. "
            "Solicite la verificación de identidad con soporte."
        )
        r_identidad = st.text_input(
            "Correo o número de identidad registrado",
            key="recuperar_identidad",
        )
        msg_recuperacion = urllib.parse.quote(
            "Hola Centro de Cerámicas y Más. Solicito recuperar el acceso a mi casillero. "
            f"Mi correo o identidad registrada es: {str(r_identidad or '').strip() or '[indicar dato]'}. "
            "Entiendo que debo completar la verificación de identidad antes del restablecimiento."
        )
        st.link_button(
            "💬 Solicitar verificación por WhatsApp",
            f"https://wa.me/50495771099?text={msg_recuperacion}",
            use_container_width=True,
        )
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
    estados_cotizaciones_cliente = cargar_estados_cotizaciones_db(casillero)
    total_cotizaciones = len(lista_mis_cotizaciones)
    direcciones_guardadas = direcciones_sesion(casillero)
    opciones_modalidad = opciones_entrega_desde_sesion(casillero, direcciones_guardadas)
    if st.session_state.pop("_ccm_error_permisos", False):
        st.error(
            "No fue posible validar los permisos de la cuenta. "
            "Los módulos permanecerán bloqueados hasta recuperar la conexión."
        )
    if st.session_state.pop("_ccm_error_datos", False):
        st.warning("No fue posible actualizar todos los datos. Intente nuevamente en unos segundos.")
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
        nombre_header = html.escape(nombre_display)
        casillero_header = html.escape(casillero)
        st.markdown(
            html_encabezado_institucional(
                f'<div class="app-greeting-title">{saludo_horario}, {nombre_header}</div>'
                f'<div class="app-greeting-sub"><span class="app-header-casillero">Casillero: <b>{casillero_header}</b></span><span class="app-header-sep"> &bull; </span><span class="app-header-cots">{total_cotizaciones} Cotizaciones</span></div>'
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
                pintar_anuncio_portal_cliente()
                pintar_centro_notificaciones_cliente(casillero)
                st.markdown(
                    '<div class="client-home-title">¿Qué desea gestionar hoy?</div>'
                    '<div class="client-home-copy">Seleccione el origen de su carga o abra directamente una herramienta disponible.</div>',
                    unsafe_allow_html=True,
                )
                visibles_hub = [hid for hid in HUBS if usuario_puede_hub(hid)]
                if not visibles_hub:
                    st.info("Su cuenta no tiene hubs habilitados. Contacte al administrador.")
                else:
                    st.markdown('<div class="client-home-section">Origen de la carga</div>', unsafe_allow_html=True)
                    columnas_hub = st.columns(len(visibles_hub), gap="medium")
                    for columna_hub, hub_id in zip(columnas_hub, visibles_hub):
                        hub = HUBS[hub_id]
                        hub_activo = bool(hub.get("activo"))
                        estado_hub = "Disponible" if hub_activo else "Próximamente"
                        boton_hub = f"Abrir {hub['label']}" if hub_activo else "Ver información"
                        with columna_hub:
                            with st.container(border=True, key=f"home_origin_{hub_id}"):
                                st.markdown(
                                    f'<div class="home-origin-card" data-active="{str(hub_activo).lower()}">'
                                    f'<div class="home-origin-top"><div class="home-origin-name">'
                                    f'<span class="home-origin-icon">{html.escape(hub["icon"])}</span>{html.escape(hub["label"])}</div>'
                                    f'<span class="home-origin-status">{estado_hub}</span></div>'
                                    f'<div class="home-origin-detail">{html.escape(hub["descripcion"])}</div>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                                st.button(
                                    boton_hub,
                                    type="primary" if hub_activo else "secondary",
                                    key=f"hub_{hub_id}",
                                    use_container_width=True,
                                    on_click=ir_a,
                                    args=("Inicio", hub_id),
                                )

                    mensaje_ayuda = urllib.parse.quote(
                        f"Hola Centro de Cerámicas y Más, necesito ayuda con mi casillero {casillero}."
                    )
                    with st.container(border=True, key="home_help"):
                        st.markdown(
                            '<div class="home-help-title">Centro de ayuda</div>'
                            '<div class="home-help-copy">Obtenga asistencia o consulte el recorrido guiado del portal.</div>',
                            unsafe_allow_html=True,
                        )
                        ayuda_wa, ayuda_guia = st.columns(2, gap="medium")
                        with ayuda_wa:
                            st.link_button(
                                "💬 Soporte por WhatsApp",
                                f"https://wa.me/50495771099?text={mensaje_ayuda}",
                                use_container_width=True,
                            )
                        with ayuda_guia:
                            st.button(
                                "▶ Abrir guía paso a paso",
                                key="home_abrir_guia",
                                use_container_width=True,
                                on_click=iniciar_guia_desde_mas,
                            )
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
            documentos_operativos_habilitados = cotizaciones_habilitadas_por_operacion(
                cargar_paquetes_db(casillero)
            )
            st.markdown("#### 📄 Historial de Cotizaciones y Descarga de PDF")
            pintar_guias_informativas(
                [
                    ("⏳", "Tarifa pendiente", "Confírmela antes de que venza su vigencia de <b>1 hora</b>.", "naranja"),
                    ("🛡️", "Tarifa confirmada", "Permanece disponible durante <b>48 horas</b>.", "azul"),
                    ("📦", "Seguimiento y documentos", "CCM los habilita después de validar <b>recepción, pago y documentación</b>.", "verde"),
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
                    f'<div style="margin-top:4px;font-size:.9rem;">La solicitud pasó a revisión administrativa. '
                    f'CCM acordará las condiciones de pago antes de generar los trackings y etiquetas oficiales.</div></div>',
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
                    estado_admin_cot = estados_cotizaciones_cliente.get(int(id_cot_item), "confirmada" if consolidada else "emitida")
                    cotizacion_operable = estado_admin_cot == "emitida"
                    aprobada_operativa = estado_admin_cot == "aprobada_tracking_generado"
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
                        if not consolidada and cotizacion_operable
                        else f'<span style="display:inline-flex;background:#f1f5f9;color:#475569;border:1px solid #cbd5e1;border-radius:999px;padding:3px 8px;font-size:.78rem;font-weight:800;">{html.escape(estado_admin_cot.replace("_", " ").title())}</span>'
                        if not consolidada
                        else '<span style="display:inline-flex;background:#dcfce7;color:#166534;border:1px solid #86efac;border-radius:999px;padding:3px 8px;font-size:.78rem;font-weight:800;">Aprobada · Tracking generado</span>'
                        if aprobada_operativa
                        else f'<span style="display:inline-flex;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:999px;padding:3px 8px;font-size:.78rem;font-weight:800;">{html.escape(estado_admin_cot.replace("_", " ").title())}</span>'
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

                        docs_habilitados_historial = int(id_cot_item) in documentos_operativos_habilitados
                        pdf_historial = None
                        if not consolidada or docs_habilitados_historial:
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
                            if docs_habilitados_historial and pdf_historial is not None:
                                st.download_button(
                                    f"📥 Descargar PDF CCM-COT-{id_cot_item:05d}",
                                    pdf_historial,
                                    f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                                    "application/pdf",
                                    key=f"dl_cot_{id_cot_item}",
                                    use_container_width=True,
                                )
                            else:
                                st.button(
                                    "🔒 PDF pendiente de validación",
                                    key=f"dl_cot_locked_{id_cot_item}",
                                    disabled=True,
                                    use_container_width=True,
                                )
                        else:
                            if cotizacion_operable:
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
                            else:
                                st.button(
                                    f"Cotización {estado_admin_cot.replace('_', ' ')}",
                                    key=f"btn_cot_bloqueada_{id_cot_item}",
                                    disabled=True, use_container_width=True,
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
            st.markdown(
                '<section class="quote-hero">'
                '<small>Cotización logística internacional</small>'
                '<h2>Flete marítimo <span>China a Honduras</span></h2>'
                '<p>Configure la ruta, el destino y las medidas para obtener una tarifa clara antes de confirmar.</p>'
                '</section>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="quote-stage"><span class="quote-stage-number">1</span><div>'
                '<small>Ruta internacional</small><b>Origen de la carga</b>'
                '<p>Seleccione el país desde donde será despachada su mercancía.</p>'
                '</div></div>',
                unsafe_allow_html=True,
            )
            origen_china, origen_eeuu = st.columns(2, gap="medium")
            with origen_china:
                with st.container(key="cotizador_origen_china"):
                    st.markdown(
                        '<div class="quote-route-head"><div class="quote-route-place">'
                        '<span class="quote-route-code">CN</span><div><b>China</b>'
                        '<small>Consolidación marítima</small></div></div>'
                        '<span class="quote-route-badge">Seleccionado</span></div>'
                        '<p class="quote-route-copy">Ruta operativa hacia la bodega central en Honduras.</p>',
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "Origen seleccionado", key="cot_origen_china_activo",
                        disabled=True, use_container_width=True,
                    )
            with origen_eeuu:
                with st.container(key="cotizador_origen_eeuu"):
                    st.markdown(
                        '<div class="quote-route-head"><div class="quote-route-place">'
                        '<span class="quote-route-code us">US</span><div><b>Estados Unidos</b>'
                        '<small>Paquetería internacional</small></div></div>'
                        '<span class="quote-route-badge soon">Próximamente</span></div>'
                        '<p class="quote-route-copy">Nueva ruta de importación en preparación.</p>',
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "Ruta no disponible", key="cot_origen_eeuu_inactivo",
                        disabled=True, use_container_width=True,
                    )

            st.markdown(
                '<div class="quote-stage"><span class="quote-stage-number">2</span><div>'
                '<small>Recepción en Honduras</small><b>Destino de entrega</b>'
                '<p>Elija dónde recibirá su carga y confirme la dirección que aparecerá en los documentos.</p>'
                '</div></div>',
                unsafe_allow_html=True,
            )
            st.button(
                "Administrar direcciones de envío",
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
                    <div class="destino-seleccionado-kicker">Destino confirmado</div>
                    <div class="destino-seleccionado-dir">{destino_estampado}</div>
                    <div class="destino-seleccionado-nota">Esta información aparecerá en los formatos y fichas de bodega.</div>
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

            st.markdown(
                '<div class="quote-stage"><span class="quote-stage-number">3</span><div>'
                '<small>Características del envío</small><b>Modalidad y dimensiones</b>'
                '<p>Indique el tipo de carga, las unidades y las medidas reales del paquete.</p>'
                '</div></div>',
                unsafe_allow_html=True,
            )
            tipo_opts = [
                f"📦 Paquetería Menor (1 a {umbral_paq:.0f} lbs)",
                "🚢 Carga Comercial por CBM (hasta contenedor 40')",
            ]
            tipo_kwargs = {"key": "sb_tipo_carga_select", "on_change": invalidar_emision_visible_cotizador}
            if "sb_tipo_carga_select" not in st.session_state:
                tipo_kwargs["index"] = 0
            tipo_carga = st.selectbox("Tipo de carga", tipo_opts, **tipo_kwargs)

            st.markdown('<div class="quote-subhead">Unidades del paquete</div>', unsafe_allow_html=True)

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

            limite_paqueteria = (
                f" En paquetería menor el peso no puede superar {umbral_paq:.0f} lb."
                if es_paqueteria else ""
            )
            st.markdown(
                '<div class="quote-capacity-note"><b>Límites operativos del envío</b><br>'
                f"Contenedor 40' High Cube: {CONTENEDOR_40_ALTO_M:.2f} m alto × "
                f"{CONTENEDOR_40_ANCHO_M:.2f} m ancho × {CONTENEDOR_40_LARGO_M:.2f} m largo. "
                f"Peso máximo legal: {PESO_MAX_CONTENEDOR_HN_KG:,.0f} kg "
                f"({peso_max_contenedor_hn_lb():,.0f} lb).{limite_paqueteria}</div>",
                unsafe_allow_html=True,
            )

            st.markdown('<div class="quote-subhead">Medidas y peso reales</div>', unsafe_allow_html=True)

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
                        estado_operativo_emit = estados_cotizaciones_cliente.get(
                            int(id_c), "confirmada" if tarifa_consolidada else "emitida"
                        )
                        if tarifa_consolidada:
                            titulo_emitida = (
                                f"Cotización CCM-COT-{id_c:05d} · "
                                + (
                                    "Aprobada y con tracking generado."
                                    if estado_operativo_emit == "aprobada_tracking_generado"
                                    else "En revisión administrativa."
                                )
                            )
                            contenido_emitida = (
                                f'<div style="color:#166534;font-size:.9rem;font-weight:700;margin-top:8px;">'
                                f'{html.escape(estado_operativo_emit.replace("_", " ").title())}</div>'
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
                                'El botón <b>“Descargar instrucciones preliminares”</b> entrega la dirección y requisitos. No funciona como etiqueta oficial de recepción.'
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
                                        "Descargar instrucciones preliminares",
                                        pdf_fab,
                                        f"Instrucciones_Preliminares_{casillero}.pdf",
                                        "application/pdf",
                                        key=f"dl_pdf_fab_{id_c}",
                                        use_container_width=True,
                                    ):
                                        avanzar_guia_si(3, 4)
                                else:
                                    st.button(
                                        "Descargar instrucciones preliminares",
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
            paquetes = cargar_paquetes_db(casillero)
            eventos_tracking = cargar_eventos_tracking_db(casillero)
            eventos_por_tracking = {}
            for evento in eventos_tracking:
                eventos_por_tracking.setdefault(str(evento[0] or ""), []).append(evento)
            trazabilidad_cliente = cargar_trazabilidad_cliente_db(casillero)
            trazabilidad_por_tracking = {}
            for movimiento in trazabilidad_cliente:
                trazabilidad_por_tracking.setdefault(str(movimiento[0] or ""), []).append(movimiento)
            cotizaciones_habilitadas = cotizaciones_habilitadas_por_operacion(paquetes)
            paquetes_en_transito = sum(1 for p in paquetes if str(p[3] or "") == "En Travesía Marítima")
            paquetes_recibidos = sum(1 for p in paquetes if bool(p[7]))
            st.markdown(
                '<div class="envios-title">Mis envíos y documentos</div>'
                '<div class="envios-copy">Seguimiento operativo actualizado automáticamente y documentos de cotizaciones confirmadas.</div>',
                unsafe_allow_html=True,
            )
            sync1, sync2 = st.columns([1.5, 1], gap="small")
            with sync1:
                auto_actualizar = st.toggle(
                    "Actualización automática cada 15 segundos",
                    value=bool(st.session_state.get("envios_auto_actualizar", True)),
                    key="envios_auto_actualizar",
                )
                ultima_sync = st.session_state.get("_seguimiento_actualizado_en") or obtener_tiempo_honduras().strftime("%H:%M:%S")
                st.caption(f"Última consulta al sistema: {ultima_sync} · Hora de Honduras")
            with sync2:
                st.button(
                    "Actualizar ahora", key="envios_refresh", type="primary",
                    use_container_width=True, on_click=refrescar_seguimiento_cliente,
                )
            if auto_actualizar:
                components.html(
                    """
                    <script>
                    window.setTimeout(() => {
                      const button = window.parent.document.querySelector('.st-key-envios_refresh button');
                      if (button && !button.disabled) button.click();
                    }, 15000);
                    </script>
                    """,
                    height=0,
                )
            with st.container(key="envios_metricas"):
                em1, em2, em3, em4 = st.columns(4, gap="small")
                em1.metric("Paquetes", len(paquetes))
                em2.metric("En travesía", paquetes_en_transito)
                em3.metric("Recibidos", paquetes_recibidos)
                em4.metric("Docs. habilitados", len(cotizaciones_habilitadas))

            filtro_tracking = st.text_input(
                "Buscar por tracking, descripción o contenedor",
                key="filtro_tracking_cliente",
                placeholder="Ej. CN-84920317",
            ).strip().lower()
            paquetes_mostrar = paquetes
            if filtro_tracking:
                paquetes_mostrar = [
                    p for p in paquetes
                    if filtro_tracking in " ".join(str(v or "") for v in p[:7]).lower()
                ]

            if paquetes_mostrar:
                st.markdown('<div class="envios-section-label">Seguimiento de paquetes</div>', unsafe_allow_html=True)
                paquetes_render, total_paquetes_cliente, limite_paquetes_cliente = pagina_registros(
                    paquetes_mostrar, "limite_paquetes_cliente", cantidad=20
                )
                for p in paquetes_render:
                    tracking_p = html.escape(str(p[0] or "Sin tracking"))
                    descripcion_p = html.escape(str(p[1] or "Carga registrada"))
                    contenedor_p = html.escape(str(p[2] or "Pendiente de asignar"))
                    estado_p = html.escape(str(p[3] or "Sin estado"))
                    actualizado_p = html.escape(str(p[4] or "—"))
                    cotizacion_p = int(p[5]) if p[5] not in (None, "") else 0
                    tipo_p = html.escape(str(p[6] or "Carga consolidada"))
                    recibido_p = bool(p[7])
                    pagado_p = bool(p[8])
                    fecha_recepcion_p = html.escape(str(p[10] or ""))
                    ubicacion_p = html.escape(str(p[11] or "Ubicación pendiente"))
                    eta_p = html.escape(str(p[12] or "Por confirmar"))
                    proximo_paso_p = html.escape(str(p[13] or proximo_estado_logistico(p[3])))
                    incidencia_p = html.escape(str(p[14] or ""))
                    estado_pago_p = html.escape(str(p[16] or ("Confirmado" if pagado_p else "Pendiente"))) if len(p) > 16 else ("Confirmado" if pagado_p else "Pendiente")
                    estado_docs_p = html.escape(str(p[17] or "Bloqueados")) if len(p) > 17 else "Bloqueados"
                    compromiso_p = html.escape(str(p[18] or "")) if len(p) > 18 else ""
                    receptor_p = html.escape(str(p[19] or "")) if len(p) > 19 else ""
                    entrega_p = html.escape(str(p[20] or "")) if len(p) > 20 else ""
                    evidencia_p = url_anuncio_segura(p[21]) if len(p) > 21 else ""
                    incidencia_estado_p = html.escape(str(p[22] or "Sin incidencia")) if len(p) > 22 else "Sin incidencia"
                    codigo_interno_p = html.escape(str(p[23] or "Pendiente de asignar")) if len(p) > 23 else "Pendiente de asignar"
                    total_bultos_p = int(p[24] or 1) if len(p) > 24 else 1
                    bultos_verificados_p = int(p[25] or 0) if len(p) > 25 else 0
                    responsable_p = html.escape(str(p[26] or "Operador por asignar")) if len(p) > 26 else "Operador por asignar"
                    zona_p = html.escape(str(p[27] or "Ubicación física pendiente")) if len(p) > 27 else "Ubicación física pendiente"
                    ultima_verificacion_p = html.escape(str(p[28] or "Sin verificación")) if len(p) > 28 else "Sin verificación"
                    integridad_p = html.escape(str(p[29] or "Pendiente")) if len(p) > 29 else "Pendiente"
                    tracking_externo_p = html.escape(str(p[30] or "Pendiente del proveedor")) if len(p) > 30 else "Pendiente del proveedor"
                    numero_bulto_p = int(p[32] or 1) if len(p) > 32 else 1
                    etiqueta_estado_p = str(p[33] or "No emitida") if len(p) > 33 else "No emitida"
                    proveedor_p = str(p[34] or "") if len(p) > 34 else ""
                    codigo_envio_p = str(p[35] or "") if len(p) > 35 else ""
                    total_envio_p = int(p[36] or 1) if len(p) > 36 else 1
                    version_etiqueta_p = int(p[37] or 1) if len(p) > 37 else 1
                    fecha_actualizacion_dt = parsear_fecha_cotizacion(p[4])
                    horas_sin_actualizar = (
                        max(0.0, (obtener_tiempo_honduras() - fecha_actualizacion_dt).total_seconds() / 3600.0)
                        if fecha_actualizacion_dt else None
                    )
                    seguimiento_atrasado = bool(
                        horas_sin_actualizar is not None
                        and horas_sin_actualizar >= 24
                        and str(p[3] or "") != "Entregado"
                    )
                    progreso_p = porcentaje_estado_logistico(p[3])
                    flag_recepcion = "ok" if recibido_p else ""
                    flag_pago = "ok" if pagado_p else ""
                    st.markdown(
                        f'<article class="shipment-card">'
                        f'<div class="shipment-card-head"><span class="shipment-tracking">📦 {tracking_p}</span>'
                        f'<span class="shipment-status">{estado_p}</span></div>'
                        f'<div class="shipment-description">{descripcion_p}</div>'
                        f'<div class="shipment-meta"><span>Folio interno: <b>{codigo_interno_p}</b></span>'
                        f'<span>Tracking externo: <b>{tracking_externo_p}</b></span>'
                        f'<span>Bultos: <b>{bultos_verificados_p}/{total_bultos_p}</b></span>'
                        f'<span>Custodia: <b>{responsable_p}</b></span><span>Zona: <b>{zona_p}</b></span></div>'
                        f'<div class="shipment-meta"><span>🚢 {contenedor_p}</span><span>{tipo_p}</span>'
                        f'<span>📍 {ubicacion_p}</span><span>ETA: {eta_p}</span>'
                        f'<span>Actualizado: {actualizado_p}</span>'
                        + (f'<span>Cotización: CCM-COT-{cotizacion_p:05d}</span>' if cotizacion_p else '')
                        + (f'<span>Recepción: {fecha_recepcion_p}</span>' if fecha_recepcion_p else '')
                        + f'</div><div class="shipment-flags">'
                        f'<span class="shipment-flag {flag_recepcion}">{"✓ Recibido en China" if recibido_p else "Pendiente de recepción"}</span>'
                        f'<span class="shipment-flag {flag_pago}">{"✓ Pago confirmado" if pagado_p else "Pago pendiente"}</span>'
                        f'<span class="shipment-flag {"ok" if estado_docs_p == "Habilitados" else ""}">Documentos: {estado_docs_p}</span>'
                        f'</div>'
                        f'<div style="margin-top:12px;height:8px;background:#e2e8f0;border-radius:4px;overflow:hidden;">'
                        f'<div style="width:{progreso_p}%;height:100%;background:#157347;"></div></div>'
                        f'<div style="display:flex;justify-content:space-between;gap:12px;margin-top:6px;font-size:.76rem;color:#475569;">'
                        f'<span>Progreso {progreso_p}%</span><span>Próximo paso: <b>{proximo_paso_p}</b></span></div>'
                        + (f'<div style="margin-top:10px;padding:9px 11px;background:#fff7ed;border-left:3px solid #ea580c;color:#9a3412;font-size:.8rem;"><b>Atención:</b> {incidencia_p}</div>' if incidencia_p else '')
                        + f'<div style="margin-top:9px;color:#475569;font-size:.76rem;">Pago: <b>{estado_pago_p}</b> · Incidencia: <b>{incidencia_estado_p}</b>'
                        + (f' · Compromiso: <b>{compromiso_p}</b>' if compromiso_p else '')
                        + (f' · Entregado a: <b>{receptor_p}</b> ({entrega_p})' if receptor_p or entrega_p else '')
                        + '</div>'
                        + f'<div style="margin-top:6px;color:#475569;font-size:.74rem;">Control físico: <b>{integridad_p}</b> · Última verificación: {ultima_verificacion_p}</div>'
                        + (
                            f'<div style="margin-top:9px;padding:8px 10px;background:#fffbeb;border-left:3px solid #d97706;color:#92400e;font-size:.76rem;">'
                            f'Este envío lleva {int(horas_sin_actualizar)} horas sin actualización operativa. CCM debe verificar su ubicación.</div>'
                            if seguimiento_atrasado else ''
                        )
                        + f'</article>',
                        unsafe_allow_html=True,
                    )
                    if evidencia_p:
                        st.link_button(
                            "Ver evidencia de entrega", evidencia_p,
                            use_container_width=True,
                        )
                    if codigo_envio_p and etiqueta_estado_p == "Vigente":
                        pdf_etiqueta_cliente = generar_pdf_etiqueta_oficial_bulto(
                            p[23] or p[0], codigo_envio_p, casillero, nombre_completo,
                            tel_cli, proveedor_p, numero_bulto_p, total_envio_p,
                            p[1] or "Carga aprobada", destino_para_documentos(), p[4], version_etiqueta_p,
                        )
                        st.download_button(
                            f"Descargar etiqueta oficial · Bulto {numero_bulto_p} de {total_envio_p}",
                            pdf_etiqueta_cliente,
                            f"Etiqueta_Oficial_{p[23] or p[0]}.pdf",
                            "application/pdf",
                            key=f"cliente_dl_etiqueta_{p[23] or p[0]}",
                            use_container_width=True,
                        )
                    movimientos_paquete = trazabilidad_por_tracking.get(str(p[0] or ""), [])
                    eventos_paquete = eventos_por_tracking.get(str(p[0] or ""), [])
                    with st.expander(
                        f"Ver recorrido verificable · {len(movimientos_paquete) or len(eventos_paquete)} movimiento(s)",
                        expanded=False,
                    ):
                        if movimientos_paquete:
                            for movimiento in movimientos_paquete[:30]:
                                _, secuencia, tipo_mov, estado_mov, mensaje_mov, fecha_mov, hash_mov = movimiento
                                st.markdown(
                                    f"**#{int(secuencia):03d} · {html.escape(str(estado_mov or tipo_mov))}**  \n"
                                    f"{html.escape(str(fecha_mov or ''))} · {html.escape(str(tipo_mov or 'Actualización'))}  \n"
                                    f"{html.escape(str(mensaje_mov or 'Movimiento confirmado por CCM.'))}  \n"
                                    f"`Verificación {str(hash_mov or '')[:12]}`"
                                )
                        elif eventos_paquete:
                            for evento in eventos_paquete[:20]:
                                _, estado_evt, ubicacion_evt, mensaje_evt, fecha_evt, _ = evento
                                st.markdown(
                                    f"**{html.escape(str(estado_evt or 'Actualización'))}**  \n"
                                    f"{html.escape(str(fecha_evt or ''))} · "
                                    f"{html.escape(str(ubicacion_evt or 'Ubicación no indicada'))}  \n"
                                    f"{html.escape(str(mensaje_evt or 'Sin detalle adicional.'))}"
                                )
                        else:
                            st.caption("La próxima actualización del operador aparecerá en esta línea de tiempo.")
                if limite_paquetes_cliente < total_paquetes_cliente:
                    st.button(
                        f"Mostrar 20 envíos más ({limite_paquetes_cliente} de {total_paquetes_cliente})",
                        key="btn_mas_paquetes_cliente", use_container_width=True,
                        on_click=aumentar_limite_registros,
                        args=("limite_paquetes_cliente", 20),
                    )
            else:
                st.markdown(
                    '<div class="envios-section-label">Seguimiento de paquetes</div>'
                    + (
                        '<div class="shipment-empty">No hay envíos que coincidan con la búsqueda.</div>'
                        if filtro_tracking
                        else '<div class="shipment-empty">Aún no hay paquetes registrados por el almacén de China.</div>'
                    ),
                    unsafe_allow_html=True,
                )
            st.markdown('<div class="envios-section-label">Cotizaciones confirmadas y documentos</div>', unsafe_allow_html=True)
            cotizaciones_despacho = ordenar_cotizaciones_desc(
                [
                    row for row in lista_mis_cotizaciones
                    if es_cotizacion_confirmada(row[8])
                    and estados_cotizaciones_cliente.get(int(row[0])) == "aprobada_tracking_generado"
                ]
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
                    documentos_habilitados = int(id_e) in cotizaciones_habilitadas
                    if es_foco:
                        st.markdown('<div id="cotizacion-envio-foco"></div>', unsafe_allow_html=True)
                        desplazar_a_ancla("cotizacion-envio-foco")
                    id_ancla_env = f'id="cotizacion-env-{id_e}"'
                    estado_envio = texto_estado_cotizacion(
                        fec_e, conf_e, ahora_hn, confirmaciones_cotizaciones.get(int(id_e))
                    )
                    clase_documentos = "is-ready" if documentos_habilitados else "is-locked"
                    texto_documentos = "✓ Documentos habilitados" if documentos_habilitados else "🔒 Pendiente de validación"
                    html_tarjeta_envio = (
                        f'<article {id_ancla_env} class="quote-shipment-card {clase_documentos}">'
                        f'<div class="quote-shipment-head"><span class="quote-shipment-id">CCM-COT-{id_e:05d}</span>'
                        f'<span class="quote-shipment-state">{texto_documentos}</span></div>'
                        f'<div class="quote-shipment-info">Confirmada · {formatear_fecha_pantalla(fec_e)}<br>'
                        f'{al_e:.1f} × {an_e:.1f} × {la_e:.1f} cm · {pe_e:.1f} lb · '
                        f'<b>${tot_e:.2f} USD</b><br>{html.escape(estado_envio)}</div>'
                        f'</article>'
                    )
                    st.markdown(html_tarjeta_envio, unsafe_allow_html=True)
                    if documentos_habilitados:
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
                    else:
                        with st.container(key=f"docs_locked_{id_e}"):
                            st.button(
                                "🏷️ Ficha de bodega pendiente",
                                key=f"locked_ficha_{id_e}",
                                disabled=True,
                                use_container_width=True,
                            )
                            st.button(
                                "📄 PDF Tarifa pendiente",
                                key=f"locked_tarifa_{id_e}",
                                disabled=True,
                                use_container_width=True,
                            )
                            st.caption("CCM habilitará estos documentos después de la validación operativa.")
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
            st.markdown("#### 📋 Fichas")
            st.caption("Fichas de bodega habilitadas después de la recepción o confirmación del pago.")
            fichas_habilitadas = cotizaciones_habilitadas_por_operacion(
                cargar_paquetes_db(casillero)
            )
            cotizaciones_ficha = ordenar_cotizaciones_desc(
                [
                    row for row in lista_mis_cotizaciones
                    if es_cotizacion_confirmada(row[8])
                    and estados_cotizaciones_cliente.get(int(row[0])) == "aprobada_tracking_generado"
                ]
            )
            if cotizaciones_ficha:
                fichas_render, total_fichas, limite_fichas = pagina_registros(
                    cotizaciones_ficha, "limite_fichas"
                )
                for cot_f in fichas_render:
                    id_f, al_f, an_f, la_f, pe_f, vol_f, tot_f, fec_f, conf_f = cot_f
                    ficha_disponible = int(id_f) in fichas_habilitadas
                    st.markdown(
                        f"""
                    <div style="background:#f8fafc; border:1.5px solid #e2e8f0; border-radius:10px; padding:10px 14px; margin-bottom:8px; font-size:0.85rem;">
                        <b>🔖 CCM-COT-{id_f:05d}</b> &bull; Fecha: {formatear_fecha_pantalla(fec_f)}<br>
                        <small style="color:#475569;">📐 Medidas: {al_f:.1f}x{an_f:.1f}x{la_f:.1f} cm | Peso: {pe_f:.1f} lbs | 💰 Total: <b>${tot_f:.2f} USD</b></small><br>
                        <small style="color:{'#15803d' if ficha_disponible else '#64748b'};font-weight:800;">{'✓ Ficha habilitada' if ficha_disponible else '🔒 Pendiente de validación'}</small>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )
                    if ficha_disponible:
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
                    else:
                        st.button(
                            "🔒 Ficha pendiente de validación",
                            key=f"ficha_locked_{id_f}",
                            disabled=True,
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
    # Una alerta de permisos del portal de clientes nunca debe sobrevivir al
    # cambio hacia una sesión administrativa.
    st.session_state.pop("_ccm_error_permisos", None)
    st.markdown(
        """
        <style>
            :root { --app-max-width: 1120px; }
            .block-container,
            [data-testid="stMainBlockContainer"],
            .stMainBlockContainer,
            [data-testid="stAppViewBlockContainer"] {
                max-width: 1120px !important;
                padding-top: 18px !important;
                padding-left: 24px !important;
                padding-right: 24px !important;
                padding-bottom: 54px !important;
            }
            .stApp,
            [data-testid="stAppViewContainer"],
            [data-testid="stMain"],
            [data-testid="stMainBlockContainer"] {
                background: #f3f6fa !important;
            }
            .app-header-blue {
                background: #0b3a75 !important;
                border-radius: 10px !important;
                padding: 13px 18px !important;
                box-shadow: 0 10px 25px rgba(0, 54, 140, .16) !important;
            }
            .admin-header-panel .app-header-top {
                margin-bottom: 8px !important;
                padding-bottom: 9px !important;
                border-bottom-color: rgba(255, 255, 255, .18) !important;
            }
            .admin-header-kicker {
                margin-bottom: 4px;
                color: #bfdbfe;
                font-size: .65rem;
                font-weight: 850;
                letter-spacing: .08em;
                text-transform: uppercase;
            }
            .admin-title-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 14px;
            }
            .admin-access-badge {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                flex: 0 0 auto;
                padding: 5px 9px;
                color: #dcfce7;
                background: rgba(22, 163, 74, .18);
                border: 1px solid rgba(134, 239, 172, .34);
                border-radius: 999px;
                font-size: .66rem;
                font-weight: 800;
            }
            .admin-access-badge::before {
                content: "";
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: #4ade80;
                box-shadow: 0 0 0 3px rgba(74, 222, 128, .13);
            }
            .st-key-admin_nav {
                margin: 14px 0 22px;
                padding: 8px;
                background: #eef3f8;
                border: 1px solid #d8e1ec;
                border-radius: 10px;
                box-shadow: inset 0 1px 2px rgba(15, 23, 42, .03);
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"],
            .st-key-admin_nav [role="radiogroup"],
            .st-key-admin_nav [data-baseweb="button-group"] {
                display: flex !important;
                flex-direction: row !important;
                align-items: stretch !important;
                width: 100% !important;
                gap: 8px !important;
                flex-wrap: wrap !important;
                background: transparent;
                border: 0;
                border-radius: 7px;
                padding: 0;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button,
            .st-key-admin_nav [data-testid="stSegmentedControl"] label,
            .st-key-admin_nav [role="radiogroup"] > label,
            .st-key-admin_nav [data-baseweb="radio"] {
                flex: 1 1 120px !important;
                min-width: 112px !important;
                min-height: 48px !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                gap: 7px !important;
                color: #475569 !important;
                background: #ffffff !important;
                border: 1px solid #d7e0eb !important;
                border-radius: 8px !important;
                box-shadow: 0 2px 5px rgba(15, 23, 42, .04) !important;
                font-size: .8rem !important;
                font-weight: 780 !important;
                white-space: nowrap !important;
                cursor: pointer !important;
                transition: background-color .16s ease, border-color .16s ease, color .16s ease, box-shadow .16s ease !important;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button:hover,
            .st-key-admin_nav [data-testid="stSegmentedControl"] label:hover,
            .st-key-admin_nav [data-baseweb="radio"]:hover {
                color: #0757c8 !important;
                border-color: #8cb5e9 !important;
                background: #f8fbff !important;
                box-shadow: 0 4px 10px rgba(7, 87, 200, .09) !important;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-pressed="true"],
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-checked="true"],
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[data-state="on"],
            .st-key-admin_nav [data-testid="stSegmentedControl"] label:has(input:checked),
            .st-key-admin_nav [role="radiogroup"] > label:has(input:checked),
            .st-key-admin_nav [data-baseweb="radio"]:has(input:checked) {
                color: #ffffff !important;
                background: #0757c8 !important;
                border-color: #0757c8 !important;
                box-shadow: 0 5px 12px rgba(7, 87, 200, .22) !important;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-pressed="true"] *,
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-checked="true"] *,
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[data-state="on"] *,
            .st-key-admin_nav [data-testid="stSegmentedControl"] label:has(input:checked) *,
            .st-key-admin_nav [role="radiogroup"] > label:has(input:checked) *,
            .st-key-admin_nav [data-baseweb="radio"]:has(input:checked) * {
                color: #ffffff !important;
                -webkit-text-fill-color: #ffffff !important;
            }
            .admin-section-heading {
                margin: 4px 0 3px;
                color: #0f172a;
                font-size: 1.2rem;
                font-weight: 850;
            }
            .admin-section-copy {
                margin: 0 0 16px;
                color: #64748b;
                font-size: .86rem;
            }
            .admin-header-panel.app-header-blue {
                padding: 10px 16px !important;
            }
            .admin-header-panel .app-header-top {
                margin-bottom: 5px !important;
                padding-bottom: 6px !important;
            }
            .admin-header-panel .app-greeting-title {
                font-size: 1.05rem !important;
            }
            .admin-header-panel .app-greeting-sub {
                font-size: .72rem !important;
            }
            .st-key-admin_nav {
                margin: 8px 0 16px !important;
                padding: 0 !important;
                background: transparent !important;
                border: 0 !important;
                border-bottom: 1px solid #dbe3ee !important;
                border-radius: 0 !important;
                box-shadow: none !important;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button,
            .st-key-admin_nav [data-testid="stSegmentedControl"] label,
            .st-key-admin_nav [role="radiogroup"] > label,
            .st-key-admin_nav [data-baseweb="radio"] {
                min-height: 40px !important;
                background: transparent !important;
                border: 0 !important;
                border-bottom: 2px solid transparent !important;
                border-radius: 0 !important;
                box-shadow: none !important;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-pressed="true"],
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-checked="true"],
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[data-state="on"],
            .st-key-admin_nav [data-testid="stSegmentedControl"] label:has(input:checked),
            .st-key-admin_nav [role="radiogroup"] > label:has(input:checked),
            .st-key-admin_nav [data-baseweb="radio"]:has(input:checked) {
                color: #0757c8 !important;
                background: #eff6ff !important;
                border-bottom-color: #0757c8 !important;
                box-shadow: none !important;
            }
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-pressed="true"] *,
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[aria-checked="true"] *,
            .st-key-admin_nav [data-testid="stSegmentedControl"] button[data-state="on"] *,
            .st-key-admin_nav [data-testid="stSegmentedControl"] label:has(input:checked) *,
            .st-key-admin_nav [role="radiogroup"] > label:has(input:checked) *,
            .st-key-admin_nav [data-baseweb="radio"]:has(input:checked) * {
                color: #0757c8 !important;
                -webkit-text-fill-color: #0757c8 !important;
            }
            .st-key-admin_metrics,
            .st-key-admin_package_metrics,
            .st-key-admin_ops_metrics {
                margin-bottom: 14px;
            }
            .st-key-admin_metrics [data-testid="stHorizontalBlock"],
            .st-key-admin_package_metrics [data-testid="stHorizontalBlock"],
            .st-key-admin_ops_metrics [data-testid="stHorizontalBlock"] {
                gap: 14px !important;
            }
            .st-key-admin_metrics [data-testid="stMetric"],
            .st-key-admin_package_metrics [data-testid="stMetric"],
            .st-key-admin_ops_metrics [data-testid="stMetric"] {
                min-height: 88px;
                padding: 14px 16px !important;
                background: #ffffff !important;
                border: 1px solid #dbe3ee !important;
                border-radius: 8px !important;
                box-shadow: 0 5px 14px rgba(15, 23, 42, .04) !important;
            }
            .st-key-admin_metrics [data-testid="stMetric"] {
                min-height: 72px;
                padding: 10px 14px !important;
            }
            .st-key-admin_metrics [data-testid="stMetricValue"],
            .st-key-admin_package_metrics [data-testid="stMetricValue"],
            .st-key-admin_ops_metrics [data-testid="stMetricValue"] {
                color: #0757c8 !important;
                font-size: 1.45rem !important;
            }
            .st-key-admin_metrics [data-testid="stColumn"]:nth-child(2) [data-testid="stMetricValue"] {
                color: #15803d !important;
            }
            .st-key-admin_metrics [data-testid="stColumn"]:nth-child(3) [data-testid="stMetricValue"] {
                color: #b45309 !important;
            }
            .st-key-admin_metrics [data-testid="stColumn"]:nth-child(4) [data-testid="stMetricValue"] {
                color: #6d28d9 !important;
            }
            .st-key-admin_package_metrics [data-testid="stColumn"]:nth-child(2) [data-testid="stMetricValue"] {
                color: #15803d !important;
            }
            .st-key-admin_package_metrics [data-testid="stColumn"]:nth-child(3) [data-testid="stMetricValue"] {
                color: #0757c8 !important;
            }
            .st-key-admin_package_metrics [data-testid="stColumn"]:nth-child(4) [data-testid="stMetricValue"] {
                color: #b45309 !important;
            }
            .admin-metric-group-label {
                margin: 3px 0 8px;
                color: #64748b;
                font-size: .66rem;
                font-weight: 850;
                letter-spacing: .06em;
                text-transform: uppercase;
            }
            .st-key-admin_directory {
                margin: 4px 0 16px;
            }
            .st-key-admin_directory details {
                overflow: hidden;
                background: #ffffff;
                border: 1px solid #dbe3ee !important;
                border-radius: 8px !important;
                box-shadow: 0 4px 12px rgba(15, 23, 42, .04);
            }
            .st-key-admin_directory summary {
                min-height: 45px;
                font-weight: 800;
            }
            .st-key-admin_selector [data-baseweb="select"] > div {
                min-height: 45px !important;
                background: #ffffff !important;
                border-radius: 8px !important;
            }
            .st-key-admin_management_mode {
                max-width: 420px;
                margin: 4px 0 12px;
            }
            .st-key-admin_management_mode [data-testid="stSegmentedControl"],
            .st-key-admin_management_mode [role="radiogroup"] {
                padding: 4px;
                background: #e9eef5;
                border-radius: 8px;
            }
            .st-key-admin_management_mode button,
            .st-key-admin_management_mode label {
                min-height: 38px !important;
                border-radius: 6px !important;
                font-size: .78rem !important;
                font-weight: 800 !important;
            }
            .st-key-admin_selector {
                margin-top: 3px;
                padding: 14px 16px;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 9px;
                box-shadow: 0 5px 14px rgba(15, 23, 42, .04);
            }
            .st-key-admin_selected_summary {
                padding: 15px 18px;
                background: #f8fafc;
                border: 1px solid #dbe3ee;
                border-left: 3px solid #0757c8;
                border-radius: 8px;
            }
            .admin-selected-kicker {
                color: #64748b;
                font-size: .65rem;
                font-weight: 850;
                text-transform: uppercase;
            }
            .admin-selected-name {
                margin-top: 5px;
                color: #0f172a;
                font-size: 1rem;
                font-weight: 850;
            }
            .admin-selected-meta {
                margin-top: 5px;
                color: #64748b;
                font-size: .76rem;
            }
            .admin-selected-access {
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                margin-top: 11px;
            }
            .admin-selected-access span {
                padding: 4px 7px;
                color: #334155;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 5px;
                font-size: .66rem;
                font-weight: 750;
            }
            .admin-editor-title {
                margin: 3px 0 4px;
                color: #0f172a;
                font-size: .96rem;
                font-weight: 850;
            }
            .admin-editor-copy {
                margin-bottom: 10px;
                color: #64748b;
                font-size: .74rem;
            }
            .admin-account-head {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin: 2px 0 16px;
                padding: 15px 16px;
                background: #f5f9ff;
                border: 1px solid #dbe3ee;
                border-left: 4px solid #0757c8;
                border-radius: 8px;
            }
            .admin-account-identity {
                display: flex;
                align-items: center;
                gap: 12px;
                min-width: 0;
            }
            .admin-account-avatar {
                display: grid;
                place-items: center;
                flex: 0 0 42px;
                width: 42px;
                height: 42px;
                color: #ffffff;
                background: #0757c8;
                border-radius: 8px;
                font-size: .78rem;
                font-weight: 900;
            }
            .admin-account-name { color: #0f172a; font-weight: 850; line-height: 1.3; }
            .admin-account-meta { color: #64748b; font-size: .8rem; margin-top: 3px; }
            .admin-account-badge {
                flex: 0 0 auto;
                padding: 5px 9px;
                color: #075e45;
                background: #e1f7ef;
                border: 1px solid #a6e5cf;
                border-radius: 999px;
                font-size: .72rem;
                font-weight: 850;
                text-transform: uppercase;
            }
            .admin-account-badge.is-inactive {
                color: #9f1239;
                background: #fff1f2;
                border-color: #fecdd3;
            }
            .st-key-admin_user_workspace {
                margin-top: 8px;
                padding: 18px 20px 20px;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 10px;
                box-shadow: 0 12px 28px rgba(15, 23, 42, .07);
            }
            .st-key-admin_user_workspace .admin-account-head { display: none; }
            .st-key-admin_directory { display: none !important; }
            .st-key-admin_section_action button {
                min-height: 40px !important;
                border-radius: 7px !important;
                font-weight: 800 !important;
            }
            .st-key-admin_user_workspace [data-baseweb="tab-list"] {
                gap: 6px;
                padding: 4px;
                background: #f1f5f9;
                border-radius: 8px;
            }
            .st-key-admin_user_workspace [data-baseweb="tab"] {
                min-height: 40px;
                border-radius: 6px;
                font-weight: 750;
            }
            .st-key-admin_user_workspace [data-baseweb="tab"][aria-selected="true"] {
                color: #0757c8 !important;
                background: #ffffff !important;
                box-shadow: 0 2px 8px rgba(15, 23, 42, .08);
            }
            .st-key-admin_user_workspace [data-baseweb="input"],
            .st-key-admin_user_workspace [data-baseweb="select"] > div,
            .st-key-admin_user_workspace [data-baseweb="textarea"] {
                border-radius: 8px !important;
            }
            .admin-form-section {
                display: flex;
                align-items: center;
                gap: 10px;
                margin: 18px 0 10px;
                padding-bottom: 8px;
                color: #0f172a;
                border-bottom: 1px solid #e5eaf1;
                font-size: .82rem;
                font-weight: 850;
            }
            .admin-form-section:first-child { margin-top: 10px; }
            .admin-form-step {
                display: grid;
                place-items: center;
                width: 24px;
                height: 24px;
                color: #0757c8;
                background: #eaf3ff;
                border-radius: 6px;
                font-size: .68rem;
                font-weight: 900;
            }
            .admin-form-section small {
                margin-left: auto;
                color: #7c8798;
                font-size: .67rem;
                font-weight: 650;
            }
            .st-key-admin_save_bar {
                position: sticky;
                bottom: 8px;
                z-index: 20;
                margin-top: 18px;
                padding: 12px 14px;
                background: #f8fafc;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
                box-shadow: 0 8px 20px rgba(15, 23, 42, .10);
            }
            .st-key-admin_save_bar [data-testid="stCaptionContainer"] { color: #64748b; }
            .st-key-admin_perm_hubs,
            .st-key-admin_perm_modules,
            .st-key-admin_security_box {
                height: 100%;
                padding: 14px 15px 10px;
                background: #f8fafc;
                border-color: #dbe3ee !important;
                border-radius: 8px !important;
            }
            .st-key-admin_perm_hubs [data-testid="stCheckbox"],
            .st-key-admin_perm_modules [data-testid="stCheckbox"] {
                margin: 3px 0;
                padding: 8px 9px;
                background: #ffffff;
                border: 1px solid #e7edf4;
                border-radius: 7px;
            }
            .st-key-adm_save_user button,
            .st-key-admin_save_bar button {
                min-height: 46px;
                font-weight: 850;
                border-radius: 8px !important;
                box-shadow: 0 5px 13px rgba(7, 87, 200, .18) !important;
            }
            .st-key-admin_delete_zone {
                margin-top: 18px;
                padding: 13px 15px;
                background: #fff7f7;
                border: 1px solid #fecaca;
                border-radius: 8px;
            }
            .st-key-admin_delete_zone button:not(:disabled) {
                color: #b42318;
                border-color: #f0a3a0;
                background: #ffffff;
            }
            .admin-announcement-status {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 14px;
                padding: 12px 14px;
                color: #334155;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            .admin-announcement-status b { color: #0f172a; }
            .admin-announcement-state {
                padding: 4px 8px;
                border-radius: 999px;
                font-size: .68rem;
                font-weight: 850;
                white-space: nowrap;
            }
            .admin-announcement-state.is-active {
                color: #087050;
                background: #dcfce7;
            }
            .admin-announcement-state.is-inactive {
                color: #64748b;
                background: #eef2f7;
            }
            .st-key-admin_announcement_editor {
                padding: 18px 20px 20px;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 10px;
                box-shadow: 0 10px 24px rgba(15, 23, 42, .06);
            }
            .st-key-admin_package_editor {
                padding: 18px 20px 20px;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 10px;
                box-shadow: 0 10px 24px rgba(15, 23, 42, .06);
            }
            .st-key-admin_package_editor [data-baseweb="input"],
            .st-key-admin_package_editor [data-baseweb="select"] > div {
                border-radius: 8px !important;
            }
            [class*="st-key-admin_pkg_recibido_"] [data-testid="stSegmentedControl"],
            [class*="st-key-admin_pkg_pago_"] [data-testid="stSegmentedControl"] {
                width: 100% !important;
                padding: 4px !important;
                gap: 4px !important;
                background: #e9eef5 !important;
                border: 1px solid #cbd5e1 !important;
                border-radius: 8px !important;
            }
            [class*="st-key-admin_pkg_recibido_"] [role="radiogroup"],
            [class*="st-key-admin_pkg_pago_"] [role="radiogroup"] {
                display: flex !important;
                width: 100% !important;
                gap: 4px !important;
            }
            [class*="st-key-admin_pkg_recibido_"] [data-testid="stSegmentedControl"] button,
            [class*="st-key-admin_pkg_pago_"] [data-testid="stSegmentedControl"] button,
            [class*="st-key-admin_pkg_recibido_"] [role="radiogroup"] > label,
            [class*="st-key-admin_pkg_pago_"] [role="radiogroup"] > label {
                flex: 1 1 50% !important;
                min-height: 38px !important;
                color: #334155 !important;
                -webkit-text-fill-color: #334155 !important;
                background: #ffffff !important;
                border: 1px solid #d7e0eb !important;
                border-radius: 6px !important;
                font-size: .74rem !important;
                font-weight: 800 !important;
                opacity: 1 !important;
            }
            [class*="st-key-admin_pkg_recibido_"] [aria-pressed="true"],
            [class*="st-key-admin_pkg_recibido_"] label:has(input:checked) {
                color: #ffffff !important;
                -webkit-text-fill-color: #ffffff !important;
                background: #15803d !important;
                border-color: #15803d !important;
            }
            [class*="st-key-admin_pkg_pago_"] [aria-pressed="true"],
            [class*="st-key-admin_pkg_pago_"] label:has(input:checked) {
                color: #ffffff !important;
                -webkit-text-fill-color: #ffffff !important;
                background: #0757c8 !important;
                border-color: #0757c8 !important;
            }
            [class*="st-key-admin_pkg_recibido_"] [aria-pressed="true"] *,
            [class*="st-key-admin_pkg_recibido_"] label:has(input:checked) *,
            [class*="st-key-admin_pkg_pago_"] [aria-pressed="true"] *,
            [class*="st-key-admin_pkg_pago_"] label:has(input:checked) * {
                color: #ffffff !important;
                -webkit-text-fill-color: #ffffff !important;
            }
            .st-key-admin_pkg_guardar button {
                min-height: 46px !important;
                border-radius: 8px !important;
                box-shadow: 0 5px 13px rgba(7, 87, 200, .18) !important;
            }
            .st-key-admin_announcement_editor [data-baseweb="input"],
            .st-key-admin_announcement_editor [data-baseweb="select"] > div,
            .st-key-admin_announcement_editor [data-baseweb="textarea"] {
                border-radius: 8px !important;
            }
            .st-key-admin_announcement_preview {
                margin-top: 16px;
                padding: 16px;
                background: #f8fafc;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
            }
            .admin-preview-label {
                margin-bottom: 9px;
                color: #64748b;
                font-size: .68rem;
                font-weight: 850;
                letter-spacing: .06em;
                text-transform: uppercase;
            }
            .st-key-admin_disable_announcement {
                width: 220px;
                margin: 12px 0 0 auto;
            }
            details:has(.st-key-adm_create_user) {
                margin-top: 12px;
                overflow: hidden;
                background: #ffffff;
                border: 1px solid #dbe3ee !important;
                border-radius: 8px !important;
                box-shadow: 0 4px 12px rgba(15, 23, 42, .04);
            }
            details:has(.st-key-adm_create_user) summary {
                min-height: 45px;
                font-weight: 800;
            }
            .st-key-admin_create_area {
                margin-top: 14px;
            }
            .admin-create-intro {
                margin: 4px 0 14px;
                padding: 12px 14px;
                color: #334155;
                background: #f5f9ff;
                border-left: 3px solid #0757c8;
                border-radius: 6px;
                font-size: .78rem;
            }
            .admin-generated-code {
                margin: 2px 0 12px;
                padding: 10px 12px;
                color: #0757c8;
                background: #f8fafc;
                border: 1px dashed #9cbce3;
                border-radius: 7px;
                font-size: .78rem;
                font-weight: 800;
            }
            .admin-created-result {
                margin: 0 0 14px;
                padding: 13px 15px;
                color: #14532d;
                background: #f0fdf4;
                border: 1px solid #bbf7d0;
                border-left: 4px solid #16a34a;
                border-radius: 8px;
                font-size: .82rem;
            }
            .st-key-adm_create_user button {
                min-height: 44px !important;
                border-radius: 8px !important;
            }
            .st-key-btn_logout_admin {
                width: 180px;
                margin: 24px 0 0 auto;
            }
            .st-key-btn_logout_admin button {
                min-height: 42px !important;
                border-radius: 8px !important;
            }
            @media (max-width: 640px) {
                .block-container,
                [data-testid="stMainBlockContainer"],
                .stMainBlockContainer,
                [data-testid="stAppViewBlockContainer"] {
                    padding-top: 9px !important;
                    padding-left: 9px !important;
                    padding-right: 9px !important;
                }
                .st-key-admin_nav { margin: 9px 0 15px; padding: 5px; }
                .st-key-admin_nav [data-testid="stSegmentedControl"],
                .st-key-admin_nav [role="radiogroup"],
                .st-key-admin_nav [data-baseweb="button-group"] {
                    gap: 4px !important;
                    flex-wrap: wrap !important;
                }
                .st-key-admin_nav [data-testid="stSegmentedControl"] button,
                .st-key-admin_nav [data-testid="stSegmentedControl"] label,
                .st-key-admin_nav [role="radiogroup"] > label,
                .st-key-admin_nav [data-baseweb="radio"] {
                    flex: 1 1 30% !important;
                    min-height: 42px !important;
                    padding-left: 3px !important;
                    padding-right: 3px !important;
                    font-size: .66rem !important;
                    white-space: normal !important;
                    text-align: center !important;
                    line-height: 1.15 !important;
                }
                .st-key-admin_user_workspace { padding: 12px; }
                .st-key-admin_management_mode { max-width: none; }
                .st-key-admin_selected_summary { margin-top: 8px; }
                .admin-account-head,
                .admin-title-row { align-items: flex-start; }
                .admin-account-head { flex-direction: column; }
                .admin-form-section small { display: none; }
                .admin-account-badge { white-space: nowrap; }
                .admin-access-badge { display: none; }
                .st-key-admin_announcement_editor { padding: 13px; }
                .st-key-admin_package_editor { padding: 13px; }
                .st-key-admin_disable_announcement { width: 100%; }
                .st-key-btn_logout_admin { width: 100%; }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    titulo = "Panel de Superadministrador" if root else "Panel Administrativo"
    admin_nombre = html.escape(str(st.session_state.get("nombre") or ""))
    admin_usuario = html.escape(str(st.session_state.get("usuario") or ""))
    st.markdown(
        html_encabezado_institucional(
            '<div class="admin-header-kicker">Operaciones · Administración</div>'
            '<div class="admin-title-row">'
            f'<div class="app-greeting-title">{titulo}</div>'
            f'<span class="admin-access-badge">{"Sistema operativo" if root else "Sesión administrativa"}</span>'
            '</div>'
            f'<div class="app-greeting-sub">{admin_nombre} · {"Superusuario" if root else "Administrador"}</div>',
            extra_class="admin-header-panel",
            extra_style="margin-bottom:12px;",
        ),
        unsafe_allow_html=True,
    )
    if st.session_state.pop("_ccm_error_permisos_admin", False):
        st.warning(
            "No se pudieron cargar los permisos del cliente seleccionado. "
            "No se aplicó ningún cambio."
        )

    opciones_admin = ["Usuarios", "Paquetes"]
    if root:
        opciones_admin = [
            "Usuarios", "Aprobaciones", "Recepción", "Control 360",
            "Paquetes", "Anuncios", "Tarifas", "Sistema",
        ]
    etiquetas_admin = {
        "Usuarios": "👥 Usuarios",
        "Aprobaciones": "✓ Aprobaciones",
        "Recepción": "▣ Recepción",
        "Control 360": "🎛️ Control 360",
        "Anuncios": "📣 Anuncios",
        "Paquetes": "📦 Paquetes",
        "Tarifas": "⚙️ Tarifas",
        "Sistema": "🗄️ Sistema",
    }
    if st.session_state.get("admin_seccion") not in (None, *opciones_admin):
        st.session_state["admin_seccion"] = "Usuarios"
    with st.container(key="admin_nav"):
        admin_seccion = st.segmented_control(
            "Sección administrativa",
            options=opciones_admin,
            format_func=lambda opcion: etiquetas_admin.get(opcion, opcion),
            default="Usuarios",
            label_visibility="collapsed",
            key="admin_seccion",
        )

    if admin_seccion == "Usuarios":
        titulo_usuarios_col, accion_usuarios_col = st.columns([1, 0.28], gap="medium")
        with titulo_usuarios_col:
            st.markdown(
                '<div class="admin-section-heading">Usuarios y permisos</div>'
                '<div class="admin-section-copy">Administre perfiles, accesos, credenciales y estado de las cuentas.</div>',
                unsafe_allow_html=True,
            )
        with accion_usuarios_col:
            with st.container(key="admin_section_action"):
                st.button(
                    "Crear usuario",
                    type="primary",
                    key="btn_admin_open_create",
                    use_container_width=True,
                    on_click=abrir_creacion_usuario_admin,
                )
        cuenta_creada_flash = st.session_state.pop("_admin_cuenta_creada", None)
        if isinstance(cuenta_creada_flash, dict):
            st.markdown(
                '<div class="admin-created-result"><b>Cuenta creada correctamente</b><br>'
                f'Casillero: <b>{html.escape(str(cuenta_creada_flash.get("casillero") or ""))}</b> · '
                f'Usuario: {html.escape(str(cuenta_creada_flash.get("nombre") or ""))}</div>',
                unsafe_allow_html=True,
            )
            st.caption("Contraseña provisional. Entréguela de forma privada; se muestra una sola vez.")
            st.code(str(cuenta_creada_flash.get("clave") or ""), language="text")
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
            filtro_metricas = "" if root else "WHERE rol = 'cliente'"
            c.execute(
                f"""
                SELECT COUNT(*),
                       SUM(CASE WHEN activo = TRUE THEN 1 ELSE 0 END),
                       SUM(CASE WHEN rol IN ('admin', 'superadmin') THEN 1 ELSE 0 END)
                FROM usuarios {filtro_metricas}
                """
            )
            metricas_cuentas = c.fetchone() or (0, 0, 0)
            c.execute(
                "SELECT codigo_casillero, COUNT(*) FROM cotizaciones GROUP BY codigo_casillero"
            )
            cotizaciones_por_usuario = {str(cas): int(total or 0) for cas, total in c.fetchall()}
            c.execute(
                """
                SELECT codigo_casillero, COUNT(*), MAX(fecha_actualizacion)
                FROM paquetes GROUP BY codigo_casillero
                """
            )
            paquetes_por_usuario = {
                str(cas): (int(total or 0), ultima or "—")
                for cas, total, ultima in c.fetchall()
            }

        if filas:
            total_cuentas = int(metricas_cuentas[0] or 0)
            total_activas = int(metricas_cuentas[1] or 0)
            total_inactivas = total_cuentas - total_activas
            total_gestores = int(metricas_cuentas[2] or 0)
            with st.container(key="admin_metrics"):
                m_total, m_activas, m_inactivas = st.columns(3, gap="medium")
                m_total.metric("Usuarios", total_cuentas)
                m_activas.metric("Activas", total_activas)
                m_inactivas.metric("Inactivas", total_inactivas)
        else:
            st.info("No hay cuentas para mostrar.")

        st.markdown(
            """
            <style>
                .st-key-admin_metrics,
                .st-key-admin_management_mode,
                .st-key-admin_selector_row,
                .st-key-admin_user_workspace,
                .st-key-admin_create_area,
                .st-key-admin_directory { display: none !important; }
                .admin-users-inline-summary {
                    margin: 2px 0 12px;
                    color: #475569;
                    font-size: .78rem;
                    font-weight: 700;
                }
                .admin-users-inline-summary b { color: #0f172a; }
                .st-key-admin_user_filters {
                    margin-bottom: 8px;
                    padding: 10px 12px;
                    background: #ffffff;
                    border: 1px solid #dbe3ee;
                    border-radius: 8px;
                }
                .st-key-admin_user_table [data-testid="stDataFrame"] {
                    border: 1px solid #dbe3ee;
                    border-radius: 8px;
                    overflow: hidden;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )
        total_directorio = int(metricas_cuentas[0] or 0)
        activas_directorio = int(metricas_cuentas[1] or 0)
        inactivas_directorio = max(0, total_directorio - activas_directorio)
        st.markdown(
            f'<div class="admin-users-inline-summary"><b>{total_directorio}</b> usuarios · '
            f'<b>{activas_directorio}</b> activos · <b>{inactivas_directorio}</b> inactivos</div>',
            unsafe_allow_html=True,
        )
        flash_usuario = st.session_state.pop("_admin_usuario_flash", "")
        if flash_usuario:
            st.success(flash_usuario)

        with st.container(key="admin_user_filters"):
            filtro_buscar_col, filtro_rol_col, filtro_estado_col = st.columns([1.7, 0.8, 0.8], gap="small")
            with filtro_buscar_col:
                filtro_directorio = st.text_input(
                    "Buscar", key="admin_directory_search", placeholder="Nombre, casillero, DNI o correo"
                ).strip().lower()
            roles_filtro = ["Todos"] + sorted({str(r[9] or "cliente") for r in filas})
            with filtro_rol_col:
                rol_directorio = st.selectbox("Rol", roles_filtro, key="admin_directory_role")
            with filtro_estado_col:
                estado_directorio = st.selectbox(
                    "Estado", ["Todos", "Activa", "Inactiva"], key="admin_directory_status"
                )

        filas_directorio = []
        for fila in filas:
            estado_fila = "Activa" if bool(fila[10]) else "Inactiva"
            texto_busqueda = " ".join(str(v or "") for v in fila[:6]).lower()
            if filtro_directorio and filtro_directorio not in texto_busqueda:
                continue
            if rol_directorio != "Todos" and str(fila[9] or "cliente") != rol_directorio:
                continue
            if estado_directorio != "Todos" and estado_fila != estado_directorio:
                continue
            cas_fila = str(fila[1] or "")
            total_paquetes_fila, ultima_actividad_fila = paquetes_por_usuario.get(cas_fila, (0, "—"))
            filas_directorio.append(
                {
                    "Nombre": str(fila[2] or "Sin nombre"),
                    "Casillero": formatear_casillero(cas_fila),
                    "Correo": str(fila[4] or "—"),
                    "Rol": str(fila[9] or "cliente").title(),
                    "Estado": estado_fila,
                    "Cotizaciones": cotizaciones_por_usuario.get(cas_fila, 0),
                    "Paquetes": total_paquetes_fila,
                    "Última actividad": ultima_actividad_fila,
                    "_usuario": fila,
                }
            )

        with st.container(key="admin_user_table"):
            if filas_directorio:
                datos_tabla = {
                    clave: [registro[clave] for registro in filas_directorio]
                    for clave in (
                        "Nombre", "Casillero", "Correo", "Rol", "Estado",
                        "Cotizaciones", "Paquetes", "Última actividad",
                    )
                }
                version_tabla = int(st.session_state.get("_admin_user_table_version") or 0)
                evento_tabla = st.dataframe(
                    datos_tabla,
                    use_container_width=True,
                    hide_index=True,
                    height=min(520, 72 + len(filas_directorio) * 35),
                    on_select="rerun",
                    selection_mode="single-row",
                    key=f"admin_users_table_{version_tabla}",
                )
                st.caption("Seleccione una fila para editar el perfil, permisos o seguridad de la cuenta.")
                try:
                    filas_seleccionadas = list(evento_tabla.selection.rows)
                except (AttributeError, TypeError):
                    filas_seleccionadas = []
                if filas_seleccionadas and not st.session_state.get("_admin_dialog_crear"):
                    indice_seleccionado = int(filas_seleccionadas[0])
                    if 0 <= indice_seleccionado < len(filas_directorio):
                        dialogo_editar_usuario_admin(
                            filas_directorio[indice_seleccionado]["_usuario"], root=root
                        )
            else:
                st.info("No hay usuarios que coincidan con los filtros seleccionados.")

        if st.session_state.get("_admin_dialog_crear"):
            dialogo_crear_usuario_admin(root=root)

        with st.container(key="admin_management_mode"):
            modo_gestion_usuario = st.segmented_control(
                "Acción de usuarios",
                ["Editar cuenta", "Crear cuenta"],
                default="Editar cuenta" if filas else "Crear cuenta",
                key="admin_user_mode",
                label_visibility="collapsed",
            )
        if modo_gestion_usuario == "Crear cuenta":
            st.markdown(
                """
                <style>
                    .st-key-admin_selector_row,
                    .st-key-admin_user_workspace,
                    .st-key-admin_directory { display: none !important; }
                </style>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
                <style>.st-key-admin_create_area { display: none !important; }</style>
                """,
                unsafe_allow_html=True,
            )

        etiquetas = [f"{formatear_casillero(r[1])} — {r[2]}" for r in filas]
        if etiquetas:
            with st.container(key="admin_selector_row"):
                selector_col, resumen_col = st.columns([0.34, 0.66], gap="medium")
                with selector_col:
                    with st.container(key="admin_selector"):
                        st.markdown(
                            '<div class="admin-editor-title">Seleccionar usuario</div>'
                            '<div class="admin-editor-copy">Busque por nombre, casillero, DNI o correo.</div>',
                            unsafe_allow_html=True,
                        )
                        busqueda_usuario = st.text_input(
                            "Buscar usuario",
                            key="admin_user_search",
                            placeholder="Nombre, casillero, DNI o correo",
                            label_visibility="collapsed",
                        ).strip().lower()
                        registros_filtrados = [
                            (fila, etiqueta)
                            for fila, etiqueta in zip(filas, etiquetas)
                            if not busqueda_usuario
                            or busqueda_usuario in " ".join(str(valor or "") for valor in fila[:6]).lower()
                            or busqueda_usuario in etiqueta.lower()
                        ]
                        opciones_usuario = [etiqueta for _, etiqueta in registros_filtrados]
                        if not opciones_usuario:
                            st.caption("Sin coincidencias. Se mantiene el directorio completo.")
                            opciones_usuario = etiquetas
                        if st.session_state.get("admin_sel_user") not in opciones_usuario:
                            st.session_state["admin_sel_user"] = opciones_usuario[0]
                        elegido = st.selectbox(
                            "Cuenta a gestionar",
                            opciones_usuario,
                            key="admin_sel_user",
                            label_visibility="collapsed",
                        )
            idx = etiquetas.index(elegido)
            u = filas[idx]
            uid, cas_u, nom_u, dni_u, cor_u, tel_u, dep_u, ciu_u, dir_u, rol_u, act_u = u
            perm = permisos_de(cas_u)
            estado_cuenta = "Activa" if bool(act_u) else "Inactiva"
            rol_visible = html.escape(str(rol_u or "cliente"))
            partes_usuario = [p for p in str(nom_u or "").split() if p]
            iniciales_usuario = "".join(p[0].upper() for p in partes_usuario[:2]) or "US"
            clase_estado_cuenta = "" if bool(act_u) else " is-inactive"
            permisos_activos = sum(
                bool(perm.get(clave))
                for clave in (
                    "hub_china", "hub_eeuu", "hub_honduras", "mod_cotizador",
                    "mod_catalogo", "mod_cotizaciones", "mod_envios", "mod_fichas",
                )
            )
            with resumen_col:
                with st.container(key="admin_selected_summary"):
                    st.markdown(
                        '<div class="admin-selected-kicker">Cuenta seleccionada</div>'
                        f'<div class="admin-selected-name">{html.escape(str(nom_u or "Sin nombre"))}</div>'
                        f'<div class="admin-selected-meta">{html.escape(formatear_casillero(cas_u))} · '
                        f'{html.escape(str(cor_u or "Sin correo"))}</div>'
                        '<div class="admin-selected-access">'
                        f'<span>{html.escape(str(rol_u or "cliente").title())}</span>'
                        f'<span>{estado_cuenta}</span><span>{permisos_activos}/8 accesos activos</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
            with resumen_col, st.container(key="admin_user_workspace"):
                st.markdown(
                    '<div class="admin-account-head">'
                    '<div class="admin-account-identity">'
                    f'<div class="admin-account-avatar">{html.escape(iniciales_usuario)}</div><div>'
                    f'<div class="admin-account-name">{html.escape(str(nom_u or "Sin nombre"))}</div>'
                    f'<div class="admin-account-meta">{html.escape(formatear_casillero(cas_u))} · {html.escape(str(cor_u or "Sin correo"))}</div>'
                    '</div></div>'
                    f'<span class="admin-account-badge{clase_estado_cuenta}">{rol_visible} · {estado_cuenta}</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                tab_perfil, tab_permisos, tab_seguridad = st.tabs(
                    ["👤 Perfil", "🔐 Permisos", "🛡️ Seguridad"]
                )

                with tab_perfil:
                    st.markdown(
                        '<div class="admin-form-section"><span class="admin-form-step">1</span>'
                        'Identidad del cliente<small>Datos legales</small></div>',
                        unsafe_allow_html=True,
                    )
                    f1, f2 = st.columns(2, gap="medium")
                    with f1:
                        n_nom = st.text_input("Nombre completo", value=nom_u, key=f"adm_nom_{cas_u}")
                    with f2:
                        n_dni = st.text_input("DNI", value=dni_u, key=f"adm_dni_{cas_u}")
                    st.markdown(
                        '<div class="admin-form-section"><span class="admin-form-step">2</span>'
                        'Información de contacto<small>Acceso y notificaciones</small></div>',
                        unsafe_allow_html=True,
                    )
                    f3, f4 = st.columns(2, gap="medium")
                    with f3:
                        n_cor = st.text_input("Correo", value=cor_u, key=f"adm_cor_{cas_u}")
                    with f4:
                        n_tel = st.text_input("Teléfono", value=tel_u, key=f"adm_tel_{cas_u}")
                    st.markdown(
                        '<div class="admin-form-section"><span class="admin-form-step">3</span>'
                        'Ubicación y entrega<small>Dirección principal</small></div>',
                        unsafe_allow_html=True,
                    )
                    f5, f6 = st.columns(2, gap="medium")
                    with f5:
                        n_dep = st.selectbox(
                            "Departamento",
                            list(MUNICIPIOS_HONDURAS.keys()),
                            index=list(MUNICIPIOS_HONDURAS.keys()).index(dep_u) if dep_u in MUNICIPIOS_HONDURAS else 0,
                            key=f"adm_dep_{cas_u}",
                        )
                    munis = MUNICIPIOS_HONDURAS[n_dep]
                    with f6:
                        n_ciu = st.selectbox(
                            "Ciudad",
                            munis,
                            index=munis.index(ciu_u) if ciu_u in munis else 0,
                            key=f"adm_ciu_{cas_u}",
                        )
                    n_dir = st.text_area(
                        "Dirección exacta",
                        value=dir_u or "",
                        key=f"adm_dir_{cas_u}",
                        height=82,
                    )
                    st.markdown(
                        '<div class="admin-form-section"><span class="admin-form-step">4</span>'
                        'Acceso a la plataforma<small>Casillero, rol y estado</small></div>',
                        unsafe_allow_html=True,
                    )
                    f7, f8 = st.columns(2, gap="medium")
                    with f7:
                        n_cas = st.text_input(
                            "Código de casillero",
                            value=formatear_casillero(cas_u),
                            key=f"adm_cas_{cas_u}",
                        )
                    roles_disp = ["cliente", "admin"]
                    if root:
                        roles_disp = ["cliente", "admin", "superadmin"]
                    with f8:
                        n_rol = st.selectbox(
                            "Rol",
                            roles_disp,
                            index=roles_disp.index(rol_u) if rol_u in roles_disp else 0,
                            key=f"adm_rol_{cas_u}",
                            disabled=(rol_u == "superadmin" and not root),
                        )
                    n_act = st.toggle(
                        "Cuenta habilitada para iniciar sesión",
                        value=bool(act_u),
                        key=f"adm_act_{cas_u}",
                    )

                with tab_permisos:
                    st.caption("Los cambios modifican de inmediato lo que el cliente puede ver y utilizar.")
                    col_hubs, col_modulos = st.columns(2, gap="medium")
                    with col_hubs:
                        with st.container(border=True, key="admin_perm_hubs"):
                            st.markdown("##### Acceso por país")
                            st.caption("Habilita las áreas principales del portal.")
                            p_china = st.toggle("🇨🇳 China", value=bool(perm.get("hub_china")), key=f"adm_h_cn_{cas_u}")
                            p_eeuu = st.toggle("🇺🇸 Estados Unidos", value=bool(perm.get("hub_eeuu")), key=f"adm_h_us_{cas_u}")
                            p_hn = st.toggle("🇭🇳 Honduras", value=bool(perm.get("hub_honduras")), key=f"adm_h_hn_{cas_u}")
                    with col_modulos:
                        with st.container(border=True, key="admin_perm_modules"):
                            st.markdown("##### Herramientas habilitadas")
                            st.caption("Control individual de cada módulo operativo.")
                            p_cot = st.toggle("📐 Cotizador", value=bool(perm.get("mod_cotizador")), key=f"adm_m_cot_{cas_u}")
                            p_cat = st.toggle("🛍️ Catálogo", value=bool(perm.get("mod_catalogo")), key=f"adm_m_cat_{cas_u}")
                            p_hist = st.toggle("📄 Mis cotizaciones", value=bool(perm.get("mod_cotizaciones")), key=f"adm_m_hist_{cas_u}")
                            p_env = st.toggle("📦 Envíos", value=bool(perm.get("mod_envios")), key=f"adm_m_env_{cas_u}")
                            p_fic = st.toggle("🏷️ Fichas", value=bool(perm.get("mod_fichas")), key=f"adm_m_fic_{cas_u}")

                with tab_seguridad:
                    st.caption("Cambie las credenciales o retire una cuenta del sistema.")
                    with st.container(border=True, key="admin_security_box"):
                        st.markdown("##### Restablecer contraseña")
                        st.caption("Puede escribir una clave o dejar el campo vacío para generar una temporal.")
                        nueva_clave = st.text_input(
                            "Nueva contraseña",
                            type="password",
                            key=f"adm_new_pwd_{cas_u}",
                            placeholder="Dejar vacío para generar automáticamente",
                        )
                        if st.button("🔑 Restablecer credenciales", key=f"adm_reset_pwd_{uid}"):
                            clave = nueva_clave.strip() if nueva_clave else generar_clave_provisional()
                            with get_db() as conn:
                                conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(clave), uid))
                            st.success("Contraseña actualizada con almacenamiento seguro.")
                            st.caption("Copie esta clave ahora; se muestra únicamente en esta ejecución.")
                            st.code(clave, language="text")

                    if rol_u != "superadmin":
                        with st.container(key="admin_delete_zone"):
                            st.markdown("##### Eliminar cuenta")
                            st.caption("Esta acción también elimina permisos y registros asociados al casillero.")
                            confirmar_borrado = st.checkbox(
                                "Confirmo que deseo eliminar esta cuenta",
                                key=f"adm_del_confirm_{uid}",
                            )
                            if st.button(
                                "Eliminar cuenta definitivamente",
                                key=f"adm_del_user_{uid}",
                                disabled=not confirmar_borrado,
                            ):
                                with get_db() as conn:
                                    cur = conn.cursor()
                                    cur.execute(
                                        "SELECT (SELECT COUNT(*) FROM trazabilidad_paquetes WHERE codigo_casillero=?) + "
                                        "(SELECT COUNT(*) FROM acuerdos_pago WHERE codigo_casillero=?)",
                                        (cas_u, cas_u),
                                    )
                                    if int((cur.fetchone() or (0,))[0] or 0) > 0:
                                        st.error(
                                            "La cuenta tiene movimientos logísticos y no puede eliminarse. "
                                            "Desactívela para preservar la trazabilidad."
                                        )
                                        st.stop()
                                    for tabla in (
                                        "permisos_usuario", "direcciones_entrega", "carrito_catalogo",
                                        "notificaciones_cliente", "casos_mensajes", "casos_cliente",
                                        "eventos_tracking", "paquetes", "cotizaciones",
                                    ):
                                        cur.execute(f"DELETE FROM {tabla} WHERE codigo_casillero = ?", (cas_u,))
                                    cur.execute(
                                        "DELETE FROM config_sistema WHERE clave = ?",
                                        (clave_omision_anuncio(cas_u),),
                                    )
                                    cur.execute("DELETE FROM usuarios WHERE id = ?", (uid,))
                                cargar_paquetes_db.clear()
                                cargar_eventos_tracking_db.clear()
                                cargar_paquetes_admin.clear()
                                cargar_metricas_paquetes_admin.clear()
                                cargar_eventos_tracking_admin.clear()
                                cargar_cotizaciones_db.clear()
                                cargar_estados_cotizaciones_db.clear()
                                cargar_cotizaciones_confirmadas_admin.clear()
                                cargar_resumen_operativo_admin.clear()
                                st.success("Cuenta eliminada.")
                                st.rerun()

                with st.container(key="admin_save_bar"):
                    barra_info, barra_accion = st.columns([1.6, 1], gap="medium")
                    with barra_info:
                        st.caption("Guarda juntos el perfil, rol, estado y permisos configurados.")
                    with barra_accion:
                        guardar_cambios_usuario = st.button(
                            "Guardar cambios",
                            type="primary",
                            key="adm_save_user",
                            use_container_width=True,
                        )
                if guardar_cambios_usuario:
                    nuevo_cas = formatear_casillero(n_cas) or generar_codigo_casillero_dni(n_dni)
                    correo_editado = normalizar_correo(n_cor)
                    if not (n_nom.strip() and n_dni.strip() and correo_editado and n_tel.strip()):
                        st.error("Nombre, DNI, correo y teléfono son obligatorios.")
                    elif "@" not in correo_editado or "." not in correo_editado.rsplit("@", 1)[-1]:
                        st.error("Ingrese un correo electrónico válido.")
                    elif not nuevo_cas:
                        st.error("No fue posible generar un código de casillero válido.")
                    elif rol_u == "superadmin" and (n_rol != "superadmin" or not n_act) and not root:
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
                                (
                                    n_nom.strip(), n_dni.strip(), correo_editado, n_tel.strip(),
                                    n_dep, n_ciu, n_dir.strip(), nuevo_cas, n_rol, bool(n_act), uid,
                                ),
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

        with st.container(key="admin_create_area"):
            with st.expander(
                "Crear una cuenta nueva",
                expanded=(modo_gestion_usuario == "Crear cuenta"),
            ):
                st.markdown(
                    '<div class="admin-create-intro"><b>Alta de cliente</b><br>'
                    'Complete la identidad, contacto y acceso. El casillero se generará automáticamente desde el DNI.</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<div class="admin-form-section"><span class="admin-form-step">1</span>'
                    'Identidad y contacto<small>Campos obligatorios</small></div>',
                    unsafe_allow_html=True,
                )
                c1, c2 = st.columns(2, gap="medium")
                with c1:
                    c_nom = st.text_input("Nombre completo *", key="new_nom")
                with c2:
                    c_dni = st.text_input("DNI *", key="new_dni")
                codigo_casillero_previo = generar_codigo_casillero_dni(c_dni)
                if codigo_casillero_previo:
                    st.markdown(
                        f'<div class="admin-generated-code">Casillero generado: '
                        f'{html.escape(codigo_casillero_previo)}</div>',
                        unsafe_allow_html=True,
                    )
                c3, c4 = st.columns(2, gap="medium")
                with c3:
                    c_cor = st.text_input("Correo electrónico *", key="new_cor")
                with c4:
                    c_tel = st.text_input("Teléfono *", key="new_tel")

                st.markdown(
                    '<div class="admin-form-section"><span class="admin-form-step">2</span>'
                    'Ubicación principal<small>Datos de entrega</small></div>',
                    unsafe_allow_html=True,
                )
                c5, c6 = st.columns(2, gap="medium")
                with c5:
                    c_dep = st.selectbox("Departamento", list(MUNICIPIOS_HONDURAS.keys()), key="new_dep")
                with c6:
                    c_ciu = st.selectbox("Ciudad", MUNICIPIOS_HONDURAS[c_dep], key="new_ciu")
                c_dir = st.text_area("Dirección exacta", key="new_dir", height=82)

                st.markdown(
                    '<div class="admin-form-section"><span class="admin-form-step">3</span>'
                    'Credenciales y rol<small>Configuración inicial</small></div>',
                    unsafe_allow_html=True,
                )
                c7, c8 = st.columns(2, gap="medium")
                with c7:
                    c_pwd = st.text_input(
                        "Contraseña inicial",
                        type="password",
                        key="new_pwd",
                        placeholder="Vacío para generar una clave segura",
                    )
                with c8:
                    c_rol = st.selectbox(
                        "Rol inicial",
                        ["cliente", "admin"] if root else ["cliente"],
                        key="new_rol",
                    )
                crear_usuario = st.button(
                    "Crear cuenta y generar casillero",
                    type="primary",
                    key="adm_create_user",
                    use_container_width=True,
                )
                if crear_usuario:
                    correo_nuevo = normalizar_correo(c_cor)
                    if not (c_nom.strip() and c_dni.strip() and correo_nuevo and c_tel.strip()):
                        st.warning("Complete todos los campos obligatorios.")
                    elif "@" not in correo_nuevo or "." not in correo_nuevo.rsplit("@", 1)[-1]:
                        st.warning("Ingrese un correo electrónico válido.")
                    elif not codigo_casillero_previo:
                        st.warning("El DNI debe contener suficientes dígitos para generar el casillero.")
                    else:
                        n_cod = codigo_casillero_previo
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
                                        n_cod, c_nom.strip(), c_dni.strip(), correo_nuevo, c_tel.strip(),
                                        c_dep, c_ciu, c_dir.strip() or f"{c_ciu}, {c_dep}",
                                        hash_pwd(n_pwd), c_rol,
                                        obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S"),
                                    ),
                                )
                            asegurar_permisos_casillero(n_cod, c_rol)
                            st.session_state["_admin_cuenta_creada"] = {
                                "casillero": n_cod,
                                "nombre": c_nom.strip(),
                                "clave": n_pwd,
                            }
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Ya existe un casillero, DNI o correo con esos datos.")

    if admin_seccion == "Aprobaciones" and root:
        st.markdown(
            '<div class="admin-section-heading">Aprobación comercial y generación de tracking</div>'
            '<div class="admin-section-copy">La confirmación del cliente inicia la revisión; solo esta área puede crear envíos y etiquetas oficiales.</div>',
            unsafe_allow_html=True,
        )
        revisiones = cargar_cotizaciones_revision_admin()
        pendientes_revision = [
            r for r in revisiones
            if str(r[4]) not in ("aprobada_tracking_generado", "rechazada", "cancelada")
        ]
        ap1, ap2, ap3 = st.columns(3, gap="medium")
        ap1.metric("Pendientes de revisión", len(pendientes_revision))
        ap2.metric("Cotizaciones confirmadas", len(revisiones))
        ap3.metric("Envíos generados", len(cargar_envios_aprobados_admin()))
        if revisiones:
            opciones_revision = {
                f"CCM-COT-{int(r[0]):05d} · {formatear_casillero(r[1])} · {r[7]} · {str(r[4]).replace('_', ' ').title()}": r
                for r in revisiones
            }
            revision_etiqueta = st.selectbox(
                "Cotización confirmada", list(opciones_revision), key="admin_revision_cotizacion"
            )
            rev = opciones_revision[revision_etiqueta]
            ya_generada = str(rev[4]) == "aprobada_tracking_generado"
            st.info(
                f"Cliente: {rev[7]} · Casillero: {formatear_casillero(rev[1])} · "
                f"Total cotizado: ${float(rev[2] or 0):,.2f} · Confirmación: {rev[3]}"
            )
            condiciones_pago = ["Pago inmediato", "Anticipo", "Contra entrega", "Crédito aprobado", "Pago diferido"]
            estados_pago_revision = ["Pendiente", "Parcial", "Confirmado", "Diferido autorizado"]
            condicion_actual = rev[9] if rev[9] in condiciones_pago else "Pago inmediato"
            pago_actual = rev[11] if rev[11] in estados_pago_revision else "Pendiente"
            ar1, ar2, ar3 = st.columns(3, gap="medium")
            with ar1:
                condicion_revision = st.selectbox(
                    "Condición acordada", condiciones_pago,
                    index=condiciones_pago.index(condicion_actual), key=f"revision_condicion_{rev[0]}",
                )
            with ar2:
                estado_pago_revision = st.selectbox(
                    "Estado real del pago", estados_pago_revision,
                    index=estados_pago_revision.index(pago_actual), key=f"revision_pago_{rev[0]}",
                )
            with ar3:
                monto_revision = st.number_input(
                    "Monto acordado (USD)", min_value=0.0,
                    value=float(rev[2] or 0), step=1.0, key=f"revision_monto_{rev[0]}",
                )
            ar4, ar5 = st.columns(2, gap="medium")
            with ar4:
                vencimiento_revision = st.text_input(
                    "Vencimiento del acuerdo", value=str(rev[12] or ""),
                    placeholder="AAAA-MM-DD (opcional)", key=f"revision_vence_{rev[0]}",
                )
            with ar5:
                cantidad_revision = st.number_input(
                    "Cantidad de cajas / bultos", min_value=1, max_value=500,
                    value=1, step=1, key=f"revision_bultos_{rev[0]}", disabled=ya_generada,
                )
            proveedor_revision = st.text_input(
                "Proveedor o fabricante", key=f"revision_proveedor_{rev[0]}",
                placeholder="Nombre comercial del proveedor en China", disabled=ya_generada,
            )
            nota_cliente_revision = st.text_area(
                "Mensaje para el cliente", value=str(rev[13] or ""), height=80,
                key=f"revision_nota_cliente_{rev[0]}",
            )
            nota_interna_revision = st.text_area(
                "Nota interna", value=str(rev[14] or ""), height=70,
                key=f"revision_nota_interna_{rev[0]}",
            )
            ac1, ac2, ac3 = st.columns(3, gap="small")
            with ac1:
                if st.button(
                    "Guardar revisión", key=f"revision_guardar_{rev[0]}",
                    use_container_width=True, disabled=ya_generada,
                ):
                    ok, mensaje = actualizar_revision_cotizacion(
                        rev[0], "en_revision", condicion_revision, "En revisión",
                        estado_pago_revision, monto_revision, vencimiento_revision,
                        nota_cliente_revision, nota_interna_revision,
                    )
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.rerun()
            with ac2:
                if st.button(
                    "Solicitar corrección", key=f"revision_corregir_{rev[0]}",
                    use_container_width=True, disabled=ya_generada,
                ):
                    ok, mensaje = actualizar_revision_cotizacion(
                        rev[0], "requiere_correccion", condicion_revision, "Información pendiente",
                        estado_pago_revision, monto_revision, vencimiento_revision,
                        nota_cliente_revision or "CCM necesita información adicional para continuar.",
                        nota_interna_revision,
                    )
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.rerun()
            with ac3:
                if st.button(
                    "Rechazar", key=f"revision_rechazar_{rev[0]}",
                    use_container_width=True, disabled=ya_generada,
                ):
                    ok, mensaje = actualizar_revision_cotizacion(
                        rev[0], "rechazada", condicion_revision, "Rechazado",
                        estado_pago_revision, monto_revision, vencimiento_revision,
                        nota_cliente_revision or "La operación no fue aprobada por CCM.",
                        nota_interna_revision,
                    )
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.rerun()
            if st.button(
                "Aprobar acuerdo y generar trackings CCM", type="primary",
                key=f"revision_aprobar_{rev[0]}", use_container_width=True,
                disabled=ya_generada,
            ):
                if vencimiento_revision and not _fecha_es_valida(vencimiento_revision):
                    st.error("La fecha de vencimiento debe usar el formato AAAA-MM-DD.")
                elif not proveedor_revision.strip():
                    st.error("Indique el proveedor antes de generar las etiquetas.")
                elif estado_pago_revision not in ("Confirmado", "Diferido autorizado", "Parcial"):
                    st.error("El pago debe estar confirmado, parcial o diferido autorizado para aprobar.")
                else:
                    ok, mensaje, _ = aprobar_y_generar_tracking_cotizacion(
                        rev[0], condicion_revision, estado_pago_revision, monto_revision,
                        vencimiento_revision, cantidad_revision, proveedor_revision,
                        nota_cliente_revision, nota_interna_revision,
                    )
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.rerun()
        else:
            st.info("No existen cotizaciones confirmadas pendientes de revisión.")

        envios_emitidos = cargar_envios_aprobados_admin()
        if envios_emitidos:
            with st.expander("Envíos aprobados y etiquetas oficiales", expanded=False):
                opciones_envio_aprobado = {
                    f"{e[1]} · {e[8]} · {int(e[9] or 0)}/{int(e[4])} recibidos": e
                    for e in envios_emitidos
                }
                envio_aprobado_etiqueta = st.selectbox(
                    "Envío", list(opciones_envio_aprobado), key="admin_envio_etiquetas"
                )
                envio_aprobado = opciones_envio_aprobado[envio_aprobado_etiqueta]
                bultos_aprobados = cargar_bultos_envio_admin(envio_aprobado[0])
                with get_db() as conn:
                    datos_doc = conn.execute(
                        """
                        SELECT u.nombre_completo, u.telefono_principal, c.destino_entrega
                        FROM usuarios u JOIN cotizaciones c ON c.codigo_casillero=u.codigo_casillero
                        WHERE c.id=?
                        """,
                        (int(envio_aprobado[2]),),
                    ).fetchone() or (envio_aprobado[8], "", "Retiro en Almacén")
                for bulto in bultos_aprobados:
                    pdf_oficial = generar_pdf_etiqueta_oficial_bulto(
                        bulto[1], envio_aprobado[1], envio_aprobado[3], datos_doc[0],
                        datos_doc[1], envio_aprobado[6], bulto[3], envio_aprobado[4],
                        bulto[9], datos_doc[2], envio_aprobado[7], int(bulto[11] or 1),
                    )
                    col_etiqueta, col_descarga, col_estado_etiqueta = st.columns([2.2, 1, 1], gap="small")
                    with col_etiqueta:
                        st.markdown(
                            f"**{bulto[1]}** · Bulto {int(bulto[3])} de {int(envio_aprobado[4])} · "
                            f"{bulto[4]} · Etiqueta {bulto[5]}"
                        )
                    with col_descarga:
                        st.download_button(
                            "Descargar etiqueta", pdf_oficial,
                            f"Etiqueta_Oficial_{bulto[1]}.pdf", "application/pdf",
                            key=f"admin_dl_etiqueta_{bulto[1]}", use_container_width=True,
                            disabled=str(bulto[5]) != "Vigente",
                        )
                    with col_estado_etiqueta:
                        accion_etiqueta = "anular" if str(bulto[5]) == "Vigente" else "reemitir"
                        texto_accion_etiqueta = "Anular" if accion_etiqueta == "anular" else "Reemitir"
                        if st.button(
                            texto_accion_etiqueta,
                            key=f"admin_estado_etiqueta_{bulto[1]}_{bulto[11]}",
                            use_container_width=True,
                        ):
                            ok, mensaje = cambiar_estado_etiqueta(bulto[1], accion_etiqueta)
                            (st.success if ok else st.error)(mensaje)
                            if ok:
                                st.rerun()

    if admin_seccion == "Recepción" and root:
        st.markdown(
            '<div class="admin-section-heading">Recepción y escaneo en Shanghái</div>'
            '<div class="admin-section-copy">Escanee el tracking CCM impreso. Cada código admite una sola recepción confirmada.</div>',
            unsafe_allow_html=True,
        )
        scan1, scan2 = st.columns([2.2, 1], gap="small")
        with scan1:
            codigo_recepcion = st.text_input(
                "Tracking CCM", key="recepcion_tracking_ccm",
                placeholder="Escanee o escriba CCM-PKG-...",
            ).strip().upper()
        with scan2:
            st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
            buscar_recepcion = st.button(
                "Verificar etiqueta", type="primary", key="recepcion_buscar",
                use_container_width=True,
            )
        if buscar_recepcion:
            st.session_state["_recepcion_codigo_activo"] = codigo_recepcion
        codigo_activo_recepcion = st.session_state.get("_recepcion_codigo_activo", "")
        bulto_recepcion = buscar_bulto_ccm_admin(codigo_activo_recepcion) if codigo_activo_recepcion else None
        if codigo_activo_recepcion and bulto_recepcion:
            if str(bulto_recepcion[8]) == "Vigente":
                st.success(
                    f"Etiqueta válida: {bulto_recepcion[1]} · {bulto_recepcion[16]} · "
                    f"{bulto_recepcion[14] or 'Envío sin código'} · Bulto {bulto_recepcion[6]} de {bulto_recepcion[15] or 1}"
                )
            else:
                st.error(f"La etiqueta está {bulto_recepcion[8]} y no puede utilizarse para recepción.")
                if st.button(
                    "Registrar uso de etiqueta inválida",
                    key=f"recepcion_etiqueta_invalida_{bulto_recepcion[1]}",
                ):
                    registrar_excepcion_recepcion(
                        bulto_recepcion[1], "Etiqueta cancelada",
                        f"Se intentó recibir una etiqueta con estado {bulto_recepcion[8]}.",
                    )
                    st.success("Intento registrado en excepciones.")
                    st.rerun()
            if bool(bulto_recepcion[5]):
                st.warning("Este bulto ya figura como recibido. No se creará una segunda recepción.")
                if st.button(
                    "Registrar intento duplicado",
                    key=f"recepcion_duplicada_{bulto_recepcion[1]}",
                ):
                    registrar_excepcion_recepcion(
                        bulto_recepcion[1], "Recepción duplicada",
                        "Se escaneó nuevamente un bulto que ya tenía recepción confirmada.",
                    )
                    st.success("Intento duplicado registrado para auditoría.")
                    st.rerun()
            condicion_recepcion = st.selectbox(
                "Condición física",
                ["Sin daños visibles", "Caja golpeada", "Caja abierta", "Humedad", "Contenido incompleto", "Etiqueta dañada"],
                key=f"recepcion_condicion_{bulto_recepcion[1]}",
            )
            rr1, rr2, rr3, rr4 = st.columns(4, gap="small")
            with rr1:
                peso_recepcion = st.number_input("Peso real (kg)", min_value=0.0, step=0.1, key=f"recepcion_peso_{bulto_recepcion[1]}")
            with rr2:
                largo_recepcion = st.number_input("Largo (cm)", min_value=0.0, step=1.0, key=f"recepcion_largo_{bulto_recepcion[1]}")
            with rr3:
                ancho_recepcion = st.number_input("Ancho (cm)", min_value=0.0, step=1.0, key=f"recepcion_ancho_{bulto_recepcion[1]}")
            with rr4:
                alto_recepcion = st.number_input("Alto (cm)", min_value=0.0, step=1.0, key=f"recepcion_alto_{bulto_recepcion[1]}")
            zona_recepcion = st.text_input(
                "Zona física de almacenamiento *", key=f"recepcion_zona_{bulto_recepcion[1]}",
                placeholder="Ej. CHN-A3-R02",
            )
            foto_recepcion = st.text_input(
                "URL de fotografía *", key=f"recepcion_foto_{bulto_recepcion[1]}",
                placeholder="https://...",
            )
            observacion_recepcion = st.text_area(
                "Observaciones internas", key=f"recepcion_obs_{bulto_recepcion[1]}", height=75,
            )
            if st.button(
                "Confirmar recepción física", type="primary",
                key=f"recepcion_confirmar_{bulto_recepcion[1]}",
                use_container_width=True,
                disabled=bool(bulto_recepcion[5]) or str(bulto_recepcion[8]) != "Vigente",
            ):
                if not zona_recepcion.strip():
                    st.error("Indique la zona física donde quedará almacenado el bulto.")
                elif min(peso_recepcion, largo_recepcion, ancho_recepcion, alto_recepcion) <= 0:
                    st.error("Registre el peso y las tres dimensiones reales del bulto.")
                elif not foto_recepcion.strip():
                    st.error("Adjunte la URL de una fotografía de recepción.")
                elif foto_recepcion and not url_anuncio_segura(foto_recepcion):
                    st.error("La fotografía debe usar una URL pública HTTP o HTTPS válida.")
                else:
                    ok, mensaje, _ = registrar_recepcion_bodega(
                        bulto_recepcion[1], condicion_recepcion, peso_recepcion,
                        largo_recepcion, ancho_recepcion, alto_recepcion,
                        foto_recepcion, zona_recepcion, observacion_recepcion,
                    )
                    (st.success if ok else st.error)(mensaje)
                    if ok:
                        st.session_state.pop("_recepcion_codigo_activo", None)
                        st.rerun()
        elif codigo_activo_recepcion:
            st.error("Tracking CCM no reconocido. No se asignará automáticamente a ningún cliente.")
            categoria_excepcion = st.selectbox(
                "Tipo de excepción",
                ["Tracking desconocido", "Paquete sin etiqueta", "Código ilegible", "Etiqueta cancelada", "Bulto adicional", "Producto restringido"],
                key="recepcion_excepcion_categoria",
            )
            detalle_excepcion = st.text_area(
                "Detalle de la excepción", key="recepcion_excepcion_detalle", height=80,
            )
            foto_excepcion = st.text_input("URL de fotografía", key="recepcion_excepcion_foto")
            if st.button("Registrar en excepciones", key="recepcion_excepcion_guardar"):
                if not detalle_excepcion.strip():
                    st.warning("Describa la situación antes de registrar la excepción.")
                else:
                    registrar_excepcion_recepcion(
                        codigo_activo_recepcion, categoria_excepcion,
                        detalle_excepcion, foto_excepcion,
                    )
                    st.success("Excepción registrada sin asignar el paquete a un cliente.")
                    st.rerun()

        excepciones = cargar_excepciones_recepcion_admin()
        if excepciones:
            with st.expander(f"Excepciones de recepción · {len(excepciones)}", expanded=False):
                opciones_excepcion = {
                    f"#{int(e[0]):04d} · {e[2]} · {e[4]} · {e[1] or 'Sin código'}": e
                    for e in excepciones
                }
                excepcion_sel = st.selectbox(
                    "Excepción", list(opciones_excepcion), key="recepcion_excepcion_admin"
                )
                excepcion = opciones_excepcion[excepcion_sel]
                st.info(excepcion[3])
                estado_excepcion = st.selectbox(
                    "Estado", ["Abierta", "En investigación", "Identificada", "Resuelta", "Cerrada"],
                    index=["Abierta", "En investigación", "Identificada", "Resuelta", "Cerrada"].index(excepcion[4])
                    if excepcion[4] in ["Abierta", "En investigación", "Identificada", "Resuelta", "Cerrada"] else 0,
                    key=f"recepcion_exc_estado_{excepcion[0]}",
                )
                resolucion_excepcion = st.text_area(
                    "Resolución", value=str(excepcion[9] or ""),
                    key=f"recepcion_exc_res_{excepcion[0]}", height=80,
                )
                if st.button("Guardar resolución", key=f"recepcion_exc_save_{excepcion[0]}"):
                    fecha_exc = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE excepciones_recepcion SET estado=?, resolucion=?, responsable=?, "
                            "fecha_actualizacion=? WHERE id=?",
                            (estado_excepcion, resolucion_excepcion.strip(),
                             st.session_state.get("usuario") or "superadmin", fecha_exc, int(excepcion[0])),
                        )
                    cargar_excepciones_recepcion_admin.clear()
                    st.success("Excepción actualizada.")
                    st.rerun()

    if admin_seccion == "Control 360" and root:
        pintar_control_cliente_360()

    if admin_seccion == "Anuncios" and root:
        st.markdown(
            '<div class="admin-section-heading">Anuncios para clientes</div>'
            '<div class="admin-section-copy">Cree un aviso personalizado para la pantalla principal de todas las cuentas de cliente.</div>',
            unsafe_allow_html=True,
        )
        anuncio_actual = cargar_anuncio_portal()
        estado_anuncio = "Activo" if anuncio_actual.get("activo") else "Inactivo"
        clase_estado = "is-active" if anuncio_actual.get("activo") else "is-inactive"
        st.markdown(
            '<div class="admin-announcement-status">'
            '<div><b>Visibilidad global</b><br><span style="font-size:.76rem;">Solo aparece a usuarios normales autenticados en Inicio.</span></div>'
            f'<span class="admin-announcement-state {clase_estado}">{estado_anuncio}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        flash_anuncio = st.session_state.pop("_admin_anuncio_flash", None)
        if flash_anuncio:
            st.success(flash_anuncio)

        tipos_anuncio = ["Información", "Promoción", "Importante"]
        iconos_anuncio = ["📢", "ℹ️", "🚢", "📦", "🎉", "⚠️"]
        tipo_actual = anuncio_actual.get("tipo") if anuncio_actual.get("tipo") in tipos_anuncio else "Información"
        icono_actual = anuncio_actual.get("icono") if anuncio_actual.get("icono") in iconos_anuncio else "📢"
        version_formulario = hashlib.sha256(
            str(anuncio_actual.get("id") or "nuevo").encode("utf-8")
        ).hexdigest()[:10]
        with st.container(key="admin_announcement_editor"):
            st.markdown("#### Contenido del anuncio")
            st.caption("Al guardar se crea una nueva versión. Los clientes podrán omitirla individualmente.")
            with st.form(f"admin_announcement_form_{version_formulario}", clear_on_submit=False):
                a_estado, a_tipo, a_icono = st.columns([1.1, 1.2, .8], gap="medium")
                with a_estado:
                    anuncio_activo = st.toggle(
                        "Mostrar a clientes",
                        value=bool(anuncio_actual.get("activo")),
                        key=f"admin_anuncio_activo_{version_formulario}",
                    )
                with a_tipo:
                    anuncio_tipo = st.selectbox(
                        "Estilo",
                        tipos_anuncio,
                        index=tipos_anuncio.index(tipo_actual),
                        key=f"admin_anuncio_tipo_{version_formulario}",
                    )
                with a_icono:
                    anuncio_icono = st.selectbox(
                        "Icono",
                        iconos_anuncio,
                        index=iconos_anuncio.index(icono_actual),
                        key=f"admin_anuncio_icono_{version_formulario}",
                    )
                anuncio_titulo = st.text_input(
                    "Título *",
                    value=str(anuncio_actual.get("titulo") or ""),
                    max_chars=120,
                    placeholder="Ej. Próxima salida marítima",
                    key=f"admin_anuncio_titulo_{version_formulario}",
                )
                anuncio_mensaje = st.text_area(
                    "Mensaje *",
                    value=str(anuncio_actual.get("mensaje") or ""),
                    max_chars=1200,
                    height=130,
                    placeholder="Escriba la información que deben ver los clientes.",
                    key=f"admin_anuncio_mensaje_{version_formulario}",
                )
                st.markdown("##### Botón opcional")
                b_texto, b_url = st.columns([1, 1.7], gap="medium")
                with b_texto:
                    anuncio_boton_texto = st.text_input(
                        "Texto del botón",
                        value=str(anuncio_actual.get("boton_texto") or ""),
                        max_chars=60,
                        placeholder="Ver información",
                        key=f"admin_anuncio_boton_{version_formulario}",
                    )
                with b_url:
                    anuncio_boton_url = st.text_input(
                        "Enlace del botón",
                        value=str(anuncio_actual.get("boton_url") or ""),
                        max_chars=600,
                        placeholder="https://...",
                        key=f"admin_anuncio_url_{version_formulario}",
                    )
                guardar_anuncio = st.form_submit_button(
                    "Guardar anuncio",
                    type="primary",
                    use_container_width=True,
                )

            if guardar_anuncio:
                titulo_limpio = str(anuncio_titulo or "").strip()
                mensaje_limpio = str(anuncio_mensaje or "").strip()
                texto_boton_limpio = str(anuncio_boton_texto or "").strip()
                url_boton_raw = str(anuncio_boton_url or "").strip()
                url_boton_limpia = url_anuncio_segura(url_boton_raw)
                if not titulo_limpio or not mensaje_limpio:
                    st.error("Complete el título y el mensaje del anuncio.")
                elif bool(texto_boton_limpio) != bool(url_boton_raw):
                    st.error("Para mostrar el botón debe completar tanto el texto como el enlace.")
                elif url_boton_raw and not url_boton_limpia:
                    st.error("El enlace debe comenzar con https:// o http:// y tener un dominio válido.")
                else:
                    version_anuncio = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
                    nuevo_anuncio = {
                        "id": version_anuncio,
                        "activo": bool(anuncio_activo),
                        "tipo": anuncio_tipo,
                        "icono": anuncio_icono,
                        "titulo": titulo_limpio,
                        "mensaje": mensaje_limpio,
                        "boton_texto": texto_boton_limpio,
                        "boton_url": url_boton_limpia,
                    }
                    set_config_sistema(
                        ANUNCIO_PORTAL_CLAVE,
                        json.dumps(nuevo_anuncio, ensure_ascii=False),
                        "Anuncio global mostrado en el Inicio de las cuentas de cliente",
                    )
                    st.session_state["_admin_anuncio_flash"] = (
                        "Anuncio publicado para los clientes."
                        if anuncio_activo
                        else "Anuncio guardado como inactivo."
                    )
                    st.rerun()

        if anuncio_actual.get("titulo") and anuncio_actual.get("mensaje"):
            with st.container(key="admin_announcement_preview"):
                st.markdown('<div class="admin-preview-label">Vista del cliente</div>', unsafe_allow_html=True)
                st.markdown(html_anuncio_portal(anuncio_actual, vista_previa=True), unsafe_allow_html=True)
            if anuncio_actual.get("activo"):
                with st.container(key="admin_disable_announcement"):
                    if st.button("Desactivar anuncio", key="btn_disable_announcement", use_container_width=True):
                        anuncio_actual["activo"] = False
                        anuncio_actual["id"] = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
                        set_config_sistema(
                            ANUNCIO_PORTAL_CLAVE,
                            json.dumps(anuncio_actual, ensure_ascii=False),
                            "Anuncio global mostrado en el Inicio de las cuentas de cliente",
                        )
                        st.session_state["_admin_anuncio_flash"] = "Anuncio desactivado correctamente."
                        st.rerun()

    if admin_seccion == "Paquetes":
        st.markdown(
            '<div class="admin-section-heading">Control de carga y contenedores</div>'
            '<div class="admin-section-copy">Registre la recepción, vincule una cotización y mantenga informado al cliente.</div>',
            unsafe_allow_html=True,
        )
        paquetes_admin = cargar_paquetes_admin()
        metricas_paquetes = cargar_metricas_paquetes_admin()
        total_paquetes_admin = int(metricas_paquetes[0] or 0)
        total_recibidos_admin = int(metricas_paquetes[1] or 0)
        total_transito_admin = int(metricas_paquetes[2] or 0)
        costo_manipulacion_admin = float(metricas_paquetes[3] or 0)
        total_sin_verificar_admin = int(metricas_paquetes[4] or 0)
        total_incidencias_admin = int(metricas_paquetes[5] or 0)
        with st.container(key="admin_package_metrics"):
            pm1, pm2, pm3, pm4 = st.columns(4, gap="medium")
            pm1.metric("Paquetes", total_paquetes_admin)
            pm2.metric("Recibidos en China", total_recibidos_admin)
            pm3.metric("En travesía", total_transito_admin)
            pm4.metric("Manipulación", f"${costo_manipulacion_admin:,.2f}")
        with st.container(key="admin_risk_metrics"):
            pr1, pr2 = st.columns(2, gap="medium")
            pr1.metric("Pendientes de verificación física", total_sin_verificar_admin)
            pr2.metric("Incidencias o retenciones", total_incidencias_admin)

        cotizaciones_admin = cargar_cotizaciones_confirmadas_admin()
        opciones_cotizacion = ["Sin asociar"]
        cotizacion_por_etiqueta = {}
        for cot_id_adm, cot_cas_adm, cot_total_adm, cot_fecha_adm in cotizaciones_admin:
            etiqueta_cot = (
                f"CCM-COT-{int(cot_id_adm):05d} · {formatear_casillero(cot_cas_adm)} · "
                f"${float(cot_total_adm or 0):,.2f}"
            )
            opciones_cotizacion.append(etiqueta_cot)
            cotizacion_por_etiqueta[etiqueta_cot] = (
                int(cot_id_adm), formatear_casillero(cot_cas_adm), cot_fecha_adm
            )

        with st.container(border=True, key="admin_package_lookup"):
            st.markdown("#### Localización rápida")
            st.caption("Use el tracking para un bulto específico o el casillero para revisar todos los paquetes del cliente.")
            buscar_tracking_col, accion_tracking_col = st.columns([2.2, 1], gap="small")
            with buscar_tracking_col:
                st.text_input(
                    "Tracking específico",
                    key="admin_tracking_directo_input",
                    placeholder="Ej. CN-84920317",
                )
            with accion_tracking_col:
                st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                st.button(
                    "Buscar tracking", type="primary", key="admin_tracking_directo_btn",
                    use_container_width=True, on_click=abrir_tracking_en_editor_admin,
                )
            error_tracking = st.session_state.pop("_admin_tracking_busqueda_error", "")
            exito_tracking = st.session_state.pop("_admin_tracking_busqueda_ok", "")
            if error_tracking:
                st.error(error_tracking)
            elif exito_tracking:
                st.success(exito_tracking)

            clientes_paquetes = cargar_clientes_con_paquetes_admin()
            opciones_clientes_paquetes = {
                f"{formatear_casillero(c[0])} · {c[1]} · {int(c[2] or 0)} paquete(s)": c
                for c in clientes_paquetes
            }
            st.markdown("##### Paquetes agrupados por casillero")
            if opciones_clientes_paquetes:
                agrupado_col, agrupado_btn_col = st.columns([2.2, 1], gap="small")
                with agrupado_col:
                    cliente_paquetes_etiqueta = st.selectbox(
                        "Cliente / casillero", list(opciones_clientes_paquetes),
                        key="admin_paquetes_cliente_selector",
                    )
                with agrupado_btn_col:
                    st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                    if st.button(
                        "Mostrar paquetes", key="admin_paquetes_cliente_btn",
                        use_container_width=True,
                    ):
                        st.session_state["_admin_casillero_paquetes_activo"] = formatear_casillero(
                            opciones_clientes_paquetes[cliente_paquetes_etiqueta][0]
                        )
                        st.session_state["limite_paquetes_casillero_admin"] = 10
                casillero_agrupado = st.session_state.get("_admin_casillero_paquetes_activo")
                if casillero_agrupado:
                    paquetes_agrupados = cargar_paquetes_casillero_admin(casillero_agrupado)
                    st.markdown(
                        f"**{casillero_agrupado}** · {len(paquetes_agrupados)} tracking(s) independientes"
                    )
                    paquetes_grupo_render, total_grupo, limite_grupo = pagina_registros(
                        paquetes_agrupados, "limite_paquetes_casillero_admin", cantidad=10
                    )
                    for paquete_grupo in paquetes_grupo_render:
                        info_grupo, accion_grupo = st.columns([3, 1], gap="small")
                        with info_grupo:
                            st.markdown(
                                f"**{html.escape(str(paquete_grupo[0]))}** · "
                                f"{html.escape(str(paquete_grupo[4] or 'Sin estado'))}  \n"
                                f"{html.escape(str(paquete_grupo[2] or 'Sin descripción'))} · "
                                f"Folio `{html.escape(str(paquete_grupo[18] or 'Pendiente'))}`"
                            )
                        with accion_grupo:
                            st.button(
                                "Abrir editor", key=f"admin_abrir_tracking_{paquete_grupo[0]}",
                                use_container_width=True, on_click=abrir_tracking_en_editor_admin,
                                args=(str(paquete_grupo[0]),),
                            )
                    if limite_grupo < total_grupo:
                        st.button(
                            f"Mostrar 10 paquetes más ({limite_grupo} de {total_grupo})",
                            key="admin_mas_paquetes_casillero", use_container_width=True,
                            on_click=aumentar_limite_registros,
                            args=("limite_paquetes_casillero_admin", 10),
                        )
                    if not paquetes_agrupados:
                        st.info("Este casillero todavía no tiene paquetes registrados.")

        with st.container(key="admin_package_editor"):
            st.markdown("#### Registrar o actualizar paquete")
            st.caption("Busque por tracking, folio, casillero, contenedor o ubicación antes de editar.")
            filtro_paquete_admin = st.text_input(
                "Buscar paquete", key="admin_pkg_busqueda",
                placeholder="Tracking, folio interno, casillero, contenedor o zona",
            ).strip().lower()
            paquetes_editor = paquetes_admin
            if filtro_paquete_admin:
                paquetes_editor = buscar_paquetes_admin(filtro_paquete_admin)
            opcion_registro = st.selectbox(
                "Registro",
                ["Nuevo paquete"] + [str(p[0]) for p in paquetes_editor],
                key="admin_pkg_registro_editar",
            )
            paquete_editar = next(
                (p for p in paquetes_editor if str(p[0]) == opcion_registro),
                None,
            )
            sufijo_editor = hashlib.sha256(opcion_registro.encode("utf-8")).hexdigest()[:10]
            indice_cotizacion = 0
            if paquete_editar and paquete_editar[6] is not None:
                for indice_opcion, etiqueta_opcion in enumerate(opciones_cotizacion):
                    datos_opcion = cotizacion_por_etiqueta.get(etiqueta_opcion)
                    if datos_opcion and int(datos_opcion[0]) == int(paquete_editar[6]):
                        indice_cotizacion = indice_opcion
                        break
            seleccion_cot = st.selectbox(
                "Cotización confirmada asociada",
                opciones_cotizacion,
                index=indice_cotizacion,
                key=f"admin_pkg_cotizacion_{sufijo_editor}",
            )
            cot_seleccionada = cotizacion_por_etiqueta.get(seleccion_cot)
            clave_casillero_editor = f"admin_pkg_casillero_{sufijo_editor}"
            if cot_seleccionada and not paquete_editar:
                st.session_state[clave_casillero_editor] = cot_seleccionada[1]
            pkg1, pkg2 = st.columns(2, gap="medium")
            with pkg1:
                t_in = st.text_input(
                    "Tracking CCM *",
                    value=str(paquete_editar[0]) if paquete_editar else "",
                    key=f"admin_pkg_tracking_{sufijo_editor}",
                    placeholder="Ej. CN-84920317",
                    disabled=bool(paquete_editar),
                )
            with pkg2:
                c_in = st.text_input(
                    "Casillero *",
                    key=clave_casillero_editor,
                    value=(
                        cot_seleccionada[1]
                        if cot_seleccionada
                        else formatear_casillero(paquete_editar[1]) if paquete_editar else ""
                    ),
                    placeholder="Ej. CCM-15011985",
                    disabled=bool(paquete_editar),
                )
            ext1, ext2 = st.columns(2, gap="medium")
            with ext1:
                tracking_externo_in = st.text_input(
                    "Tracking externo del proveedor",
                    value=str(paquete_editar[25] or "") if paquete_editar else "",
                    key=f"admin_pkg_tracking_externo_{sufijo_editor}",
                    placeholder="Ej. SF123456789CN",
                )
            with ext2:
                proveedor_paquete_in = st.text_input(
                    "Proveedor / fabricante",
                    value=str(paquete_editar[29] or "") if paquete_editar else "",
                    key=f"admin_pkg_proveedor_{sufijo_editor}",
                )
            d_in = st.text_input(
                "Descripción de la carga *",
                value=str(paquete_editar[2] or "") if paquete_editar else "",
                key=f"admin_pkg_descripcion_{sufijo_editor}",
                placeholder="Ej. 4 cajas de porcelanato 60 × 120",
            )
            control1, control2, control3 = st.columns([1, 1, 1.4], gap="medium")
            with control1:
                cantidad_bultos_in = st.number_input(
                    "Bultos declarados *", min_value=1, max_value=100000,
                    value=int(paquete_editar[19] or 1) if paquete_editar else 1,
                    step=1, key=f"admin_pkg_bultos_{sufijo_editor}",
                )
            with control2:
                bultos_verificados_in = st.number_input(
                    "Bultos verificados *", min_value=0, max_value=100000,
                    value=int(paquete_editar[20] or 0) if paquete_editar else 0,
                    step=1, key=f"admin_pkg_bultos_ok_{sufijo_editor}",
                )
            with control3:
                estado_integridad_actual = str(paquete_editar[24] or "Pendiente") if paquete_editar else "Pendiente"
                estados_integridad = ["Pendiente", "Verificado", "Diferencia detectada", "Dañado", "En investigación"]
                estado_integridad_in = st.selectbox(
                    "Control físico", estados_integridad,
                    index=estados_integridad.index(estado_integridad_actual) if estado_integridad_actual in estados_integridad else 0,
                    key=f"admin_pkg_integridad_{sufijo_editor}",
                )
            custodia1, custodia2 = st.columns(2, gap="medium")
            with custodia1:
                responsable_actual_in = st.text_input(
                    "Responsable actual *", value=str(paquete_editar[21] or "") if paquete_editar else "",
                    key=f"admin_pkg_responsable_{sufijo_editor}", placeholder="Nombre del operador o equipo",
                )
            with custodia2:
                zona_almacen_in = st.text_input(
                    "Zona física / posición *", value=str(paquete_editar[22] or "") if paquete_editar else "",
                    key=f"admin_pkg_zona_{sufijo_editor}", placeholder="Ej. CHN-A3-R02",
                )
            pkg3, pkg4 = st.columns([1.4, 1], gap="medium")
            with pkg3:
                cont_in = st.text_input(
                    "ID del contenedor",
                    value=str(paquete_editar[3] or "") if paquete_editar else "",
                    key=f"admin_pkg_contenedor_{sufijo_editor}",
                    placeholder="Ej. CCM-CNT-014",
                )
            with pkg4:
                tipos_carga = ["Bulto individual", "40' High Cube", "40' estándar", "20' estándar", "Carga consolidada", "Palé"]
                tipo_actual = str(paquete_editar[7] or "") if paquete_editar else ""
                tipo_cont_in = st.selectbox(
                    "Tipo de carga",
                    tipos_carga,
                    index=tipos_carga.index(tipo_actual) if tipo_actual in tipos_carga else 0,
                    key=f"admin_pkg_tipo_contenedor_{sufijo_editor}",
                )
            estados_disponibles = list(ESTADOS_LOGISTICOS) + list(ESTADOS_LOGISTICOS_ESPECIALES)
            estado_actual_editor = str(paquete_editar[4] or "") if paquete_editar else ""
            e_in = st.selectbox(
                "Estado logístico",
                estados_disponibles,
                index=estados_disponibles.index(estado_actual_editor) if estado_actual_editor in estados_disponibles else 0,
                key=f"admin_pkg_estado_{sufijo_editor}",
            )
            pkg_ubicacion, pkg_eta = st.columns(2, gap="medium")
            with pkg_ubicacion:
                ubicacion_in = st.text_input(
                    "Ubicación actual",
                    value=str(paquete_editar[12] or "") if paquete_editar else "",
                    key=f"admin_pkg_ubicacion_{sufijo_editor}",
                    placeholder="Ej. Puerto de Shanghái, China",
                )
            with pkg_eta:
                eta_in = st.text_input(
                    "Llegada estimada",
                    value=str(paquete_editar[13] or "") if paquete_editar else "",
                    key=f"admin_pkg_eta_{sufijo_editor}",
                    placeholder="AAAA-MM-DD",
                )
            proximo_paso_in = st.text_input(
                "Próximo paso visible",
                value=str(paquete_editar[14] or "") if paquete_editar else "",
                key=f"admin_pkg_proximo_paso_{sufijo_editor}",
                placeholder=proximo_estado_logistico(e_in),
            )
            mensaje_cliente_in = st.text_area(
                "Mensaje de actualización para el cliente",
                key=f"admin_pkg_mensaje_cliente_{sufijo_editor}",
                placeholder="Ej. La carga fue inspeccionada y está lista para consolidación.",
                height=80,
            )
            incidencia_in = st.text_area(
                "Incidencia o acción requerida",
                value=str(paquete_editar[15] or "") if paquete_editar else "",
                key=f"admin_pkg_incidencia_{sufijo_editor}",
                placeholder="Déjelo vacío cuando el envío no tenga incidencias.",
                height=70,
            )
            nota_interna_in = st.text_area(
                "Nota interna (no visible para el cliente)",
                key=f"admin_pkg_nota_interna_{sufijo_editor}",
                height=70,
            )
            pkg5, pkg6, pkg7 = st.columns([1, 1, 1.1], gap="medium")
            with pkg5:
                recibido_estado_in = st.segmented_control(
                    "Recepción en China",
                    options=["Pendiente", "Recibido"],
                    default="Recibido" if paquete_editar and bool(paquete_editar[8]) else "Pendiente",
                    key=f"admin_pkg_recibido_{sufijo_editor}",
                )
                recibido_in = recibido_estado_in == "Recibido"
            with pkg6:
                pago_estado_in = st.segmented_control(
                    "Estado del pago",
                    options=["Pendiente", "Confirmado"],
                    default="Confirmado" if paquete_editar and bool(paquete_editar[9]) else "Pendiente",
                    key=f"admin_pkg_pago_{sufijo_editor}",
                )
                pago_in = pago_estado_in == "Confirmado"
            with pkg7:
                costo_manipulacion_in = st.number_input(
                    "Manipulación (USD)",
                    min_value=0.0,
                    max_value=1_000_000.0,
                    value=float(paquete_editar[10] or 0) if paquete_editar else 0.0,
                    step=0.50,
                    format="%.2f",
                    key=f"admin_pkg_costo_manipulacion_{sufijo_editor}",
                )
            st.caption("La recepción o el pago habilitan documentos por defecto; Control 360 permite autorizarlos o bloquearlos manualmente.")
            visible_cliente_in = st.toggle(
                "Visible en el portal del cliente",
                value=bool(paquete_editar[16]) if paquete_editar else True,
                key=f"admin_pkg_visible_cliente_{sufijo_editor}",
            )
            guardar_paquete = st.button(
                "Guardar actualización logística",
                type="primary",
                key=f"admin_pkg_guardar_{sufijo_editor}",
                use_container_width=True,
                disabled=not bool(paquete_editar),
            )
            if not paquete_editar:
                st.info(
                    "Los paquetes nuevos se generan desde Aprobaciones después de validar el acuerdo de pago. "
                    "Busque un tracking existente para editarlo."
                )

        if guardar_paquete:
            cas_paquete = formatear_casillero(c_in)
            cot_id_paquete = cot_seleccionada[0] if cot_seleccionada else None
            tracking_entrada = str(t_in or "").strip()
            campos_faltantes = []
            if not tracking_entrada:
                campos_faltantes.append("tracking")
            if not cas_paquete:
                campos_faltantes.append("casillero")
            if not d_in.strip():
                campos_faltantes.append("descripción")
            if not ubicacion_in.strip():
                campos_faltantes.append("ubicación actual")
            if not responsable_actual_in.strip():
                campos_faltantes.append("responsable actual")
            if not zona_almacen_in.strip():
                campos_faltantes.append("zona física")
            if campos_faltantes:
                st.warning(
                    "No se guardó el paquete. Complete "
                    + " y ".join(campos_faltantes)
                    + " con información válida y vuelva a intentarlo."
                )
            elif int(bultos_verificados_in) > int(cantidad_bultos_in):
                st.error("Los bultos verificados no pueden superar los bultos declarados.")
            elif estado_integridad_in == "Verificado" and int(bultos_verificados_in) != int(cantidad_bultos_in):
                st.error("Para marcar el control como Verificado, todos los bultos deben estar comprobados.")
            elif indice_estado_logistico(e_in) >= indice_estado_logistico("En Bodega China") and not recibido_in:
                st.error("Confirme la recepción física en China antes de asignar un estado logístico normal.")
            elif (
                indice_estado_logistico(e_in) >= indice_estado_logistico("En Consolidación")
                and (
                    estado_integridad_in != "Verificado"
                    or int(bultos_verificados_in) != int(cantidad_bultos_in)
                )
            ):
                st.error("Verifique todos los bultos antes de consolidar o movilizar la carga.")
            elif indice_estado_logistico(e_in) >= indice_estado_logistico("Asignado a Contenedor") and not cont_in.strip():
                st.error("Asigne un contenedor antes de avanzar el paquete a esa etapa logística.")
            elif cot_seleccionada and cas_paquete != cot_seleccionada[1]:
                st.error("El casillero no coincide con la cotización seleccionada.")
            else:
                f_act = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                eta_limpia = eta_in.strip()
                eta_valida = True
                if eta_limpia:
                    try:
                        datetime.strptime(eta_limpia, "%Y-%m-%d")
                    except ValueError:
                        eta_valida = False
                if not eta_valida:
                    st.error("La llegada estimada debe usar el formato AAAA-MM-DD.")
                else:
                    tracking_limpio = tracking_entrada if paquete_editar else tracking_entrada.upper()
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT 1 FROM usuarios WHERE codigo_casillero = ? AND rol = 'cliente' AND activo = TRUE",
                            (cas_paquete,),
                        )
                        if cur.fetchone() is None:
                            st.error("El casillero no pertenece a un cliente activo registrado.")
                            st.stop()
                        cur.execute(
                            """
                            SELECT estado, ubicacion_actual, eta, proximo_paso, incidencia,
                                   recibido_bodega, pago_confirmado, codigo_casillero, cotizacion_id,
                                   COALESCE(version, 1), codigo_interno, COALESCE(cantidad_bultos, 1),
                                   COALESCE(bultos_verificados, 0), responsable_actual, zona_almacen,
                                   COALESCE(estado_integridad, 'Pendiente'), descripcion, contenedor_id,
                                   ultima_verificacion, tracking
                            FROM paquetes WHERE UPPER(TRIM(tracking)) = UPPER(TRIM(?))
                            """,
                            (tracking_limpio,),
                        )
                        anterior = cur.fetchone()
                        duplicado_normalizado = cur.fetchone()
                        if duplicado_normalizado is not None:
                            st.error(
                                "Este tracking está duplicado por diferencias de mayúsculas o espacios. "
                                "No se permite editarlo hasta depurar los registros repetidos."
                            )
                            st.stop()
                        if anterior:
                            tracking_limpio = str(anterior[19])
                        estado_anterior = str(anterior[0] or "") if anterior else ""
                        if anterior and formatear_casillero(anterior[7]) != cas_paquete:
                            st.error(
                                "Este tracking ya pertenece a otro casillero y no puede reasignarse "
                                "desde una actualización normal."
                            )
                            st.stop()
                        version_esperada = int(paquete_editar[17] or 1) if paquete_editar else 1
                        if anterior and int(anterior[9] or 1) != version_esperada:
                            st.error(
                                "Otro operador actualizó este paquete mientras estaba abierto. "
                                "Recargue el registro para evitar sobrescribir cambios recientes."
                            )
                            st.stop()
                        if not transicion_logistica_valida(estado_anterior, e_in):
                            st.error(
                                f"No se permite retroceder de “{estado_anterior}” a “{e_in}”. "
                                "Use Incidencia si necesita corregir o revisar el proceso."
                            )
                            st.stop()
                        recibido_final = bool(recibido_in or (anterior and anterior[5]))
                        pago_final = bool(pago_in or (anterior and anterior[6]))
                        fecha_recepcion = f_act if recibido_final else None
                        proximo_paso = proximo_paso_in.strip() or proximo_estado_logistico(e_in)
                        codigo_interno = (
                            str(anterior[10] or "") if anterior else ""
                        ) or "CCM-PKG-" + hashlib.sha256(
                            f"{cas_paquete}|{tracking_limpio}".encode("utf-8")
                        ).hexdigest()[:10].upper()
                        ultima_verificacion = (
                            f_act if int(bultos_verificados_in) > 0 or estado_integridad_in == "Verificado"
                            else str(anterior[18] or "") if anterior else None
                        )
                        cur.execute(
                            """
                            INSERT INTO paquetes (
                                tracking, codigo_casillero, descripcion, contenedor_id, tipo_contenedor,
                                cotizacion_id, recibido_bodega, pago_confirmado, costo_manipulacion_usd,
                                fecha_recepcion, ubicacion_actual, eta, proximo_paso, incidencia,
                                visible_cliente, estado, fecha_actualizacion, codigo_interno,
                                cantidad_bultos, bultos_verificados, responsable_actual, zona_almacen,
                                ultima_verificacion, estado_integridad, version
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(tracking) DO UPDATE SET
                                codigo_casillero = excluded.codigo_casillero,
                                descripcion = excluded.descripcion,
                                contenedor_id = excluded.contenedor_id,
                                tipo_contenedor = excluded.tipo_contenedor,
                                cotizacion_id = COALESCE(excluded.cotizacion_id, paquetes.cotizacion_id),
                                recibido_bodega = excluded.recibido_bodega,
                                pago_confirmado = excluded.pago_confirmado,
                                costo_manipulacion_usd = excluded.costo_manipulacion_usd,
                                fecha_recepcion = COALESCE(paquetes.fecha_recepcion, excluded.fecha_recepcion),
                                ubicacion_actual = excluded.ubicacion_actual,
                                eta = excluded.eta,
                                proximo_paso = excluded.proximo_paso,
                                incidencia = excluded.incidencia,
                                visible_cliente = excluded.visible_cliente,
                                estado = excluded.estado,
                                fecha_actualizacion = excluded.fecha_actualizacion,
                                codigo_interno = COALESCE(paquetes.codigo_interno, excluded.codigo_interno),
                                cantidad_bultos = excluded.cantidad_bultos,
                                bultos_verificados = excluded.bultos_verificados,
                                responsable_actual = excluded.responsable_actual,
                                zona_almacen = excluded.zona_almacen,
                                ultima_verificacion = COALESCE(excluded.ultima_verificacion, paquetes.ultima_verificacion),
                                estado_integridad = excluded.estado_integridad,
                                version = paquetes.version + 1
                            WHERE paquetes.version = excluded.version
                            """,
                            (
                                tracking_limpio, cas_paquete, d_in.strip(), cont_in.strip(), tipo_cont_in,
                                cot_id_paquete, recibido_final, pago_final, float(costo_manipulacion_in),
                                fecha_recepcion, ubicacion_in.strip(), eta_limpia, proximo_paso,
                                incidencia_in.strip(), bool(visible_cliente_in), e_in, f_act,
                                codigo_interno, int(cantidad_bultos_in), int(bultos_verificados_in),
                                responsable_actual_in.strip(), zona_almacen_in.strip(),
                                ultima_verificacion, estado_integridad_in, version_esperada,
                            ),
                        )
                        if anterior and cur.rowcount != 1:
                            st.error(
                                "Otro operador guardó cambios en este paquete. "
                                "Recargue el registro antes de volver a intentarlo."
                            )
                            st.stop()
                        cur.execute(
                            "UPDATE paquetes SET tracking_externo=?, proveedor_nombre=? WHERE tracking=?",
                            (
                                tracking_externo_in.strip() or None,
                                proveedor_paquete_in.strip() or None,
                                tracking_limpio,
                            ),
                        )
                        datos_anteriores = {
                            "estado": anterior[0], "ubicacion": anterior[1], "eta": anterior[2],
                            "proximo_paso": anterior[3], "incidencia": anterior[4],
                            "recibido": bool(anterior[5]), "pago": bool(anterior[6]),
                            "cotizacion_id": anterior[8], "bultos": anterior[11],
                            "bultos_verificados": anterior[12], "responsable": anterior[13],
                            "zona": anterior[14], "integridad": anterior[15],
                            "descripcion": anterior[16], "contenedor": anterior[17],
                        } if anterior else {}
                        datos_nuevos = {
                            "estado": e_in, "ubicacion": ubicacion_in.strip(), "eta": eta_limpia,
                            "proximo_paso": proximo_paso, "incidencia": incidencia_in.strip(),
                            "recibido": recibido_final, "pago": pago_final,
                            "cotizacion_id": cot_id_paquete or (anterior[8] if anterior else None),
                            "bultos": int(cantidad_bultos_in),
                            "bultos_verificados": int(bultos_verificados_in),
                            "responsable": responsable_actual_in.strip(),
                            "zona": zona_almacen_in.strip(), "integridad": estado_integridad_in,
                            "descripcion": d_in.strip(), "contenedor": cont_in.strip(),
                            "codigo_interno": codigo_interno,
                            "tracking_externo": tracking_externo_in.strip(),
                            "proveedor": proveedor_paquete_in.strip(),
                        }
                        mensaje_evento = mensaje_cliente_in.strip() or f"Estado confirmado: {e_in}."
                        tipo_movimiento = "ALTA" if anterior is None else (
                            "VERIFICACION_FISICA"
                            if datos_anteriores.get("bultos_verificados") != int(bultos_verificados_in)
                            else "ACTUALIZACION_OPERATIVA"
                        )
                        registrar_trazabilidad_paquete(
                            cur, tracking_limpio, cas_paquete, tipo_movimiento,
                            estado_anterior, e_in, datos_anteriores, datos_nuevos,
                            mensaje_evento, nota_interna_in.strip(), bool(visible_cliente_in),
                            st.session_state.get("usuario") or "superadmin", f_act,
                        )
                        cur.execute(
                            """
                            INSERT INTO eventos_tracking (
                                tracking, codigo_casillero, estado, ubicacion, mensaje_cliente,
                                nota_interna, fecha_evento, creado_por, visible_cliente
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                tracking_limpio, cas_paquete, e_in, ubicacion_in.strip(),
                                mensaje_evento, nota_interna_in.strip(), f_act,
                                st.session_state.get("usuario") or "superadmin",
                                bool(visible_cliente_in),
                            ),
                        )
                    crear_notificacion_cliente(
                        cas_paquete, f"Actualización de {tracking_limpio}", mensaje_evento,
                        tipo="Seguimiento",
                        prioridad="Urgente" if e_in in ESTADOS_LOGISTICOS_ESPECIALES else "Normal",
                        tracking=tracking_limpio,
                    )
                    cargar_paquetes_db.clear()
                    cargar_eventos_tracking_db.clear()
                    cargar_trazabilidad_cliente_db.clear()
                    cargar_trazabilidad_paquete_db.clear()
                    cargar_paquetes_admin.clear()
                    buscar_paquetes_admin.clear()
                    buscar_tracking_exacto_admin.clear()
                    cargar_paquetes_casillero_admin.clear()
                    cargar_clientes_con_paquetes_admin.clear()
                    cargar_metricas_paquetes_admin.clear()
                    cargar_eventos_tracking_admin.clear()
                    cargar_resumen_operativo_admin.clear()
                    st.success("Actualización logística guardada y registrada en la bitácora.")
                    st.rerun()

        if paquetes_admin:
            with st.expander(f"Inventario y seguimiento · {total_paquetes_admin} registros"):
                st.dataframe(
                    {
                        "Tracking": [p[0] for p in paquetes_admin],
                        "Folio interno": [p[18] or "Pendiente" for p in paquetes_admin],
                        "Tracking externo": [p[25] or "Pendiente" for p in paquetes_admin],
                        "ID envío": [p[26] or "—" for p in paquetes_admin],
                        "Bulto": [p[27] or 1 for p in paquetes_admin],
                        "Casillero": [formatear_casillero(p[1]) for p in paquetes_admin],
                        "Contenedor": [p[3] or "—" for p in paquetes_admin],
                        "Tipo": [p[7] or "—" for p in paquetes_admin],
                        "Estado": [p[4] for p in paquetes_admin],
                        "Recibido": ["Sí" if p[8] else "No" for p in paquetes_admin],
                        "Pago": ["Sí" if p[9] else "No" for p in paquetes_admin],
                        "Costo": [f"${float(p[10] or 0):,.2f}" for p in paquetes_admin],
                        "Actualizado": [p[5] for p in paquetes_admin],
                        "Ubicación": [p[12] or "—" for p in paquetes_admin],
                        "ETA": [p[13] or "—" for p in paquetes_admin],
                        "Próximo paso": [p[14] or "—" for p in paquetes_admin],
                        "Incidencia": [p[15] or "—" for p in paquetes_admin],
                        "Bultos": [f"{int(p[20] or 0)}/{int(p[19] or 1)}" for p in paquetes_admin],
                        "Integridad": [p[24] or "Pendiente" for p in paquetes_admin],
                        "Responsable": [p[21] or "—" for p in paquetes_admin],
                        "Zona": [p[22] or "—" for p in paquetes_admin],
                        "Verificación": [p[23] or "—" for p in paquetes_admin],
                        "Visible": ["Sí" if p[16] else "No" for p in paquetes_admin],
                    },
                    use_container_width=True,
                    hide_index=True,
                )
            tracking_auditoria = st.selectbox(
                "Consultar bitácora de un tracking",
                [p[0] for p in paquetes_admin],
                key="admin_tracking_auditoria",
            )
            eventos_admin = cargar_eventos_tracking_admin(tracking_auditoria)
            trazabilidad_admin = cargar_trazabilidad_paquete_db(tracking_auditoria)
            integridad_ok, secuencia_error, total_movimientos = verificar_integridad_trazabilidad(
                tracking_auditoria
            )
            if integridad_ok:
                st.info(
                    f"Trazabilidad verificada: {total_movimientos} movimiento(s) encadenados sin alteraciones."
                )
            else:
                st.error(
                    f"La cadena de trazabilidad presenta una inconsistencia desde el movimiento #{secuencia_error}."
                )
            with st.expander(
                f"Trazabilidad absoluta · {len(trazabilidad_admin)} movimiento(s)", expanded=False
            ):
                if trazabilidad_admin:
                    st.dataframe(
                        {
                            "Secuencia": [int(t[0]) for t in trazabilidad_admin],
                            "Fecha": [t[9] for t in trazabilidad_admin],
                            "Movimiento": [t[1] for t in trazabilidad_admin],
                            "Anterior": [t[2] or "—" for t in trazabilidad_admin],
                            "Nuevo": [t[3] or "—" for t in trazabilidad_admin],
                            "Operador": [t[10] for t in trazabilidad_admin],
                            "Visible": ["Sí" if t[8] else "No" for t in trazabilidad_admin],
                            "Huella": [str(t[12])[:12] for t in trazabilidad_admin],
                        },
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.caption("Este paquete todavía no tiene movimientos de trazabilidad.")
            with st.expander(
                f"Bitácora de {tracking_auditoria} · {len(eventos_admin)} evento(s)",
                expanded=False,
            ):
                if eventos_admin:
                    st.dataframe(
                        {
                            "Fecha": [e[0] for e in eventos_admin],
                            "Estado": [e[1] for e in eventos_admin],
                            "Ubicación": [e[2] or "—" for e in eventos_admin],
                            "Mensaje cliente": [e[3] or "—" for e in eventos_admin],
                            "Nota interna": [e[4] or "—" for e in eventos_admin],
                            "Operador": [e[5] or "—" for e in eventos_admin],
                            "Visible": ["Sí" if e[6] else "No" for e in eventos_admin],
                        },
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("Este tracking aún no tiene eventos registrados.")

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
        st.markdown("##### Costos de manipulación en origen")
        man1, man2 = st.columns(2, gap="medium")
        with man1:
            n_man_cbm = st.number_input(
                "Manipulación por CBM (USD)",
                min_value=0.0,
                value=float(leer_config_moneda("MANIPULACION_CBM_USD", 0.0)),
                step=0.50,
            )
        with man2:
            n_man_pale = st.number_input(
                "Manipulación por palé (USD)",
                min_value=0.0,
                value=float(leer_config_moneda("MANIPULACION_PALE_USD", 0.0)),
                step=0.50,
            )
        if st.button("Guardar tarifas y fórmulas", type="primary"):
            set_tarifa("tarifa_libra", n_lb)
            set_tarifa("tarifa_m3", n_m3)
            set_tarifa("minimo_cobro_usd", n_min)
            set_tarifa("umbral_minimo_lb", n_umin)
            set_tarifa("umbral_paqueteria_lb", n_upaq)
            set_tarifa("divisor_peso_volumetrico", n_div)
            set_config_sistema("TASA_USD_HNL", n_tasa, "Tasa USD a lempira")
            set_config_sistema("COMISION_CCM_PORCENTAJE", n_com, "Comisión CCM sobre FOB")
            set_config_sistema("MANIPULACION_CBM_USD", n_man_cbm, "Costo operativo de manipulación por CBM en origen")
            set_config_sistema("MANIPULACION_PALE_USD", n_man_pale, "Costo operativo de manipulación por palé en origen")
            st.success("Parámetros globales actualizados.")

    if admin_seccion == "Sistema":
        st.markdown("#### Mantenimiento de base de datos")
        conteos = obtener_conteos_tablas()
        st.dataframe(
            {
                "Tabla": list(conteos.keys()),
                "Registros estimados" if USA_SUPABASE else "Registros": list(conteos.values()),
            },
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("#### Variables de entorno y configuración")
        env_keys = sorted(k for k in os.environ if k.startswith(("STREAMLIT_", "CCM_")) or k in ("PORT", "HOSTNAME", "HOME"))
        if env_keys:
            st.dataframe(
                {"Variable": env_keys, "Estado": ["Configurada" for _ in env_keys]},
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No hay variables STREAMLIT_/CCM_ en el entorno del proceso.")

        with get_db() as conn:
            filas_cfg = conn.execute(
                "SELECT clave, valor, descripcion FROM config_sistema "
                "WHERE clave NOT LIKE 'ANUNCIO_OMITIDO_%' ORDER BY clave"
            ).fetchall()
        claves_sensibles = ("secret", "token", "password", "passwd", "database_url", "api_key")
        filas_cfg = [
            fila for fila in filas_cfg
            if not any(fragmento in str(fila[0] or "").lower() for fragmento in claves_sensibles)
        ]
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
