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


def es_rol_admin(rol=None):
    return (rol if rol is not None else st.session_state.get("rol")) in ROLES_ADMIN


def es_superadmin(rol=None):
    return (rol if rol is not None else st.session_state.get("rol")) == "superadmin"


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
                vals["hub_china"],
                vals["hub_eeuu"],
                vals["hub_honduras"],
                vals["mod_cotizador"],
                vals["mod_catalogo"],
                vals["mod_cotizaciones"],
                vals["mod_envios"],
                vals["mod_fichas"],
            ),
        )


def abrir_permisos_todos_los_usuarios():
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE permisos_usuario SET
                hub_china=1, hub_eeuu=1, hub_honduras=1,
                mod_cotizador=1, mod_catalogo=1, mod_cotizaciones=1, mod_envios=1, mod_fichas=1
            """
        )
        conn.commit()


def permisos_de(casillero=None):
    cas = formatear_casillero(casillero or st.session_state.get("casillero", ""))
    base = permisos_default(st.session_state.get("rol", "cliente"))
    if not cas:
        return base
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
            return permisos_default(st.session_state.get("rol", "cliente"))
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
        return dict(zip(claves, [int(v or 0) for v in row]))
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
                int(datos.get("hub_china", 0)),
                int(datos.get("hub_eeuu", 0)),
                int(datos.get("hub_honduras", 0)),
                int(datos.get("mod_cotizador", 0)),
                int(datos.get("mod_catalogo", 0)),
                int(datos.get("mod_cotizaciones", 0)),
                int(datos.get("mod_envios", 0)),
                int(datos.get("mod_fichas", 0)),
            ),
        )


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
                    correo_principal = ?, password_hash = ?, rol = 'superadmin', activo = 1
                WHERE id = ?
                """,
                (NOMBRE_SUPERADMIN, DNI_SUPERADMIN, cas_root, CORREO_SUPERADMIN, hash_root, existente[0]),
            )
        else:
            c.execute(
                """
                INSERT INTO usuarios (
                    codigo_casillero, nombre_completo, dni, correo_principal,
                    telefono_principal, departamento, ciudad, direccion_exacta,
                    password_hash, rol, activo, fecha_creacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'superadmin', 1, ?)
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
                    vals["hub_china"],
                    vals["hub_eeuu"],
                    vals["hub_honduras"],
                    vals["mod_cotizador"],
                    vals["mod_catalogo"],
                    vals["mod_cotizaciones"],
                    vals["mod_envios"],
                    vals["mod_fichas"],
                ),
            )
            if rol in ROLES_ADMIN or PERMISOS_ABIERTOS_TEMPORAL:
                c.execute(
                    """
                    UPDATE permisos_usuario SET hub_china=1, hub_eeuu=1, hub_honduras=1,
                        mod_cotizador=1, mod_catalogo=1, mod_cotizaciones=1, mod_envios=1, mod_fichas=1
                    WHERE codigo_casillero = ?
                    """,
                    (cas,),
                )
        conn.commit()


def restaurar_datos_operativos_cliente():
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


migrar_prefijo_casillero()
asegurar_superadmin()
abrir_permisos_todos_los_usuarios()
restaurar_datos_operativos_cliente()
purgar_cotizaciones_no_confirmadas_vencidas()


def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return "".join(random.choice(caracteres) for _ in range(8))


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

    maxes = {
        "alto": max_alineado(min_v, CONTENEDOR_40_ALTO_M * factor, step),
        "ancho": max_alineado(min_v, CONTENEDOR_40_ANCHO_M * factor, step),
        "largo": max_alineado(min_v, CONTENEDOR_40_LARGO_M * factor, step),
    }
    base = defaults_com if comercial else defaults_paq
    defaults = {k: min(v, maxes[k]) for k, v in base.items()}
    return {
        "min": min_v,
        "step": step,
        "formato": formato,
        "codigo": codigo,
        "defaults": defaults,
        "max": maxes,
    }


def limites_peso(unidad_peso, paqueteria):
    if paqueteria:
        max_lb = float(get_tarifa("umbral_paqueteria_lb") or PESO_MAX_PAQUETERIA_LB)
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
        return st.number_input(
            label,
            min_value=float(lim_min),
            max_value=float(lim_max),
            step=float(paso),
            format=formato,
            key=clave,
            **kwargs,
        )
    return st.number_input(
        label,
        min_value=float(lim_min),
        max_value=float(lim_max),
        value=float(valor),
        step=float(paso),
        format=formato,
        key=clave,
        **kwargs,
    )


PREFIJO_CASILLERO = "CCM-"


def nucleo_casillero_desde_id(valor):
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


# ---------------------------------------------------------
# 7. ROUTING Y VISTAS DE LA APLICACIÓN
# ---------------------------------------------------------
if not st.session_state.get("autenticado", False):
    if st.session_state.get("vista_actual") == "login":
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
                p_hash = hash_pwd(u_pass)
                claves = coincidencias_casillero(u_ident)
                placeholders = ",".join("?" * len(claves))
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        f"""
                        SELECT id, codigo_casillero, nombre_completo, correo_principal, rol, activo, telefono_principal, ciudad
                        FROM usuarios
                        WHERE (correo_principal = ? OR dni = ? OR codigo_casillero IN ({placeholders})) AND password_hash = ?
                        """,
                        (u_ident, u_ident, *claves, p_hash),
                    )
                    user = c.fetchone()

                if user:
                    if user[5] == 0:
                        st.error("⛔ Cuenta inactiva. Contacte al soporte.")
                    else:
                        st.session_state["autenticado"] = True
                        st.session_state["rol"] = user[4]
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

    elif st.session_state.get("vista_actual") == "registro":
        st.markdown("### 📋 Apertura de Casillero en China")
        if st.session_state.get("reg_exito"):
            creado = st.session_state["reg_exito"]
            st.markdown(
                f"""
                <div class="reg-confirm-card">
                    <h4>🎉 Casillero y correo confirmados</h4>
                    <div>Guarde estos datos para iniciar sesión:</div>
                    <div>👤 {creado.get("nombre", "")}</div>
                    <div>📧 Correo: <b>{creado.get("correo", "")}</b></div>
                    <div>🔑 Casillero: <b>{creado.get("casillero", "")}</b></div>
                    <div>🔒 Contraseña: <b>{creado.get("password", "")}</b></div>
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

        paso = st.session_state.get("reg_paso", 1)
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

                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT codigo_casillero FROM usuarios WHERE correo_principal = ? OR dni = ? OR codigo_casillero IN ({})".format(
                                ",".join("?" * len(coincidencias_casillero(n_cod)))
                            ),
                            (d["cor"], d["dni"], *coincidencias_casillero(n_cod)),
                        )
                        if cur.fetchone():
                            url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(
                                "Hola, necesito asistencia con mi casillero ya registrado."
                            )
                            st.markdown(
                                '<div class="reg-warn-card">⚠️ Ya existe un casillero registrado con este DNI o correo. Use otro correo o consulte a soporte.</div>',
                                unsafe_allow_html=True,
                            )
                            st.markdown(
                                f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:10px; border-radius:8px; width:100%; font-weight:bold; cursor:pointer;">📲 Consultar por WhatsApp (+504 9577-1099)</button></a>',
                                unsafe_allow_html=True,
                            )
                        else:
                            cur.execute(
                                "INSERT INTO usuarios (codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)",
                                (
                                    n_cod,
                                    d["nom"],
                                    d["dni"],
                                    d["cor"],
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
                            conn.commit()
                            asegurar_permisos_casillero(n_cod, "cliente")
                            st.session_state["reg_exito"] = {
                                "nombre": d["nom"],
                                "correo": d["cor"],
                                "casillero": n_cod,
                                "password": n_pwd,
                            }
                            st.session_state["reg_paso"] = 1
                            st.session_state["reg_datos"] = {}
                            st.rerun()

        if st.button("Volver al Login", type="secondary"):
            st.session_state["vista_actual"] = "login"
            st.rerun()

    elif st.session_state.get("vista_actual") == "recuperar":
        st.markdown("### 🔄 Restablecer Contraseña")
        r_mail = st.text_input("Correo Registrado")
        if st.button("Generar Nueva Contraseña", type="primary"):
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM usuarios WHERE correo_principal = ?", (r_mail,))
                u = c.fetchone()
            if u:
                nueva_p = generar_clave_provisional()
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(nueva_p), u[0]))
                st.success(f"✅ Nueva clave: **{nueva_p}**")
            else:
                st.error("Correo no registrado.")
        if st.button("Volver al Login", type="secondary"):
            st.session_state["vista_actual"] = "login"
            st.rerun()

elif st.session_state.get("rol") == "cliente":
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
    purgar_cotizaciones_no_confirmadas_vencidas(ahora_hn)
    _limpiar_cotizacion_vencida_en_sesion(ahora_hn)
    hidratar_cotizaciones_sesion(casillero)
    nombre_completo = st.session_state.get("nombre", "Cliente")
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

    lista_todas_cotizaciones, lista_mis_cotizaciones = filas_cotizaciones_casillero(casillero, ahora_hn)
    total_cotizaciones = len(lista_mis_cotizaciones)
    direcciones_guardadas = direcciones_sesion(casillero)
    opciones_modalidad = opciones_entrega_desde_sesion(casillero)
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
            elif hub_sel == "china":
                pintar_coach_guia()
                hub_china = HUBS["china"]
                st.markdown(f"#### {hub_china['icon']} {hub_china['label']}")
                st.caption("Consolidación marítima China ➔ Honduras")
                pintar_banner_promocional_china(casillero)
            elif hub_sel == "eeuu":
                pintar_modulo_aliexpress_eeuu(casillero)
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
            st.button("🇺🇸 AliExpress", key="btn_consultas_ae", use_container_width=True, on_click=ir_a, args=("Inicio", "eeuu"))

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
            st.caption(
                "Las tarifas no confirmadas caducan a las 24 horas (hora de Honduras) y se eliminan. "
                "Al confirmar, la cotización queda consolidada de forma permanente. "
                "Use Ir a Envíos para abrir el seguimiento y el PDF Tarifa de esa cotización."
            )

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
                scroll_pendiente_hecho = False
                for cot in lista_mis_cotizaciones:
                    id_cot_item, al_c, an_c, la_c, pe_lb_c, vol_m3_c, tot_c, fec_c, conf_c = cot
                    consolidada = es_cotizacion_confirmada(conf_c)
                    estado_txt = texto_estado_cotizacion(fec_c, conf_c, ahora_hn)
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
                        else ""
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
            else:
                st.info(
                    "No hay cotizaciones vigentes ni consolidadas. Emita una tarifa en el Cotizador; "
                    "tiene 24 horas para confirmarla y habilitar Envíos."
                )
            espaciador_barra_inferior("safe_historial")

    if st.session_state["sub_tab_inicio"] == "Cotizador" and st.session_state["modalidad_envio_seleccionada"] == "➕ Crear Nueva Dirección de Envío":
        with st.container(key="vista_cotizador"):
            with st.container(key="formulario_direcciones"):
                selector_modalidad_entrega(opciones_modalidad)
                st.markdown("#### 📍 Administrar Direcciones de Envío")

                st.markdown(
                    f"""
                <div style="background:#f1f5f9; border:1.5px solid #cbd5e1; border-radius:8px; padding:10px 12px; margin-bottom:8px; font-size:0.85rem;">
                    <b>{OPCION_PREDETERMINADA}</b> <span style="background:#004ac1; color:white; font-size:0.7rem; padding:2px 8px; border-radius:12px; font-weight:bold; margin-left:6px;">⭐ Predeterminada (Fija)</span><br>
                    <small style="color:#64748b;">📍 Bodega Central Centro de Cerámicas y Más &bull; San Juan, Intibucá (No se puede eliminar)</small>
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
                        col_info_d, col_btn_del = st.columns([3.8, 1])
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
                        with col_btn_del:
                            if st.button("🗑️ Eliminar", key=f"del_dir_{id_dir or f'ses_{idx_dir}'}", type="secondary"):
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
                st.button(
                    "💾 Guardar Dirección",
                    type="primary",
                    key="btn_guardar_nueva_dir",
                    use_container_width=True,
                    on_click=guardar_nueva_direccion,
                    args=(casillero,),
                )
                st.button(
                    "Cancelar",
                    type="secondary",
                    key="btn_cancelar_dir",
                    use_container_width=True,
                    on_click=cancelar_nueva_direccion,
                )

    if st.session_state["sub_tab_inicio"] == "Catálogo":
        with st.container(key="vista_catalogo"):
            with st.container(key="catalogo_formulario"):
                st.markdown("#### 🛍️ Búsqueda en Fábricas de China (1688 Direct)")

                modo_busq = st.radio("Modalidad de búsqueda:", ["🔎 Por Nombre / Palabras", "📷 Por Foto / Imagen"], horizontal=True)

                resultados_1688 = []
                if modo_busq == "🔎 Por Nombre / Palabras":
                    kw = st.text_input("Producto a buscar:", placeholder="Ej: porcelanato 60x120, grifería, taladro...")
                    if st.button(
                        "Buscar Productos en China ➔",
                        type="primary",
                        key="btn_buscar_china",
                        use_container_width=True,
                    ) and kw:
                        with st.spinner("Consultando catálogo de 1688..."):
                            resultados_1688 = buscar_productos_1688_texto(kw)
                else:
                    img_up = st.file_uploader("Sube una foto del producto:", type=["jpg", "png", "jpeg", "webp"])
                    if img_up and st.button(
                        "Escanear Coincidencia Visual ➔",
                        type="primary",
                        key="btn_escanear_catalogo",
                        use_container_width=True,
                    ):
                        with st.spinner("Buscando por reconocimiento visual..."):
                            resultados_1688 = buscar_productos_1688_imagen(img_up.getvalue())

            if resultados_1688:
                st.markdown("---")
                for prod in resultados_1688:
                    calc = calcular_costo_puesto_honduras(
                        prod["precio_fabrica_usd"], prod["peso_kg"], prod["volumen_m3"], prod["moq"]
                    )

                    c_img, c_det = st.columns([1, 1.8])
                    with c_img:
                        st.image(prod["imagen_url"], use_container_width=True)
                    with c_det:
                        st.markdown(f"**{prod['nombre']}**")
                        st.caption(f"🏭 {prod['proveedor']} | SKU: `{prod['sku']}`")
                        st.markdown(
                            f"💰 **Fábrica:** ¥{prod['precio_fabrica_cny']:.2f} CNY (~${prod['precio_fabrica_usd']:.2f} USD) | **MOQ:** {prod['moq']} uds."
                        )
                        st.success(
                            f"🇭🇳 **Puesto en Honduras:** ${calc['total_estimado_usd']:.2f} USD (~L {calc['total_estimado_hnl']:.2f} HNL)\n\n*(Destino: {destino_para_documentos()})*"
                        )

                        msg_cot = f"Hola Centro de Cerámicas y Más, me interesa importar este producto: {prod['nombre']} (SKU: {prod['sku']}) para mi casillero {casillero}. Cantidad: {prod['moq']} uds. Destino/Entrega: {destino_para_documentos()}. Enlace: {prod['url_proveedor']}"
                        url_wa_p = "https://wa.me/50495771099?text=" + urllib.parse.quote(msg_cot)

                        c_b1, c_b2 = st.columns(2)
                        with c_b1:
                            st.markdown(
                                f'<a href="{prod["url_proveedor"]}" target="_blank"><button style="background:white; border:1.5px solid #cbd5e1; border-radius:8px; width:100%; height:44px; font-weight:bold; cursor:pointer;">🔗 Ver en 1688</button></a>',
                                unsafe_allow_html=True,
                            )
                        with c_b2:
                            st.markdown(
                                f'<a href="{url_wa_p}" target="_blank"><button style="background:#22c55e; color:white; border:none; border-radius:8px; width:100%; height:44px; font-weight:bold; cursor:pointer;">📲 Cotizar WhatsApp</button></a>',
                                unsafe_allow_html=True,
                            )
                    st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
            espaciador_barra_inferior("safe_catalogo")

    elif (
        st.session_state["sub_tab_inicio"] == "Cotizador"
        and st.session_state["modalidad_envio_seleccionada"] != "➕ Crear Nueva Dirección de Envío"
    ):
        with st.container(key="vista_cotizador"):
            st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
            selector_modalidad_entrega(opciones_modalidad)
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
            exito_dir = st.session_state.pop("_dir_form_exito", None)
            if exito_dir:
                st.success(f"✅ {exito_dir}")
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
                pe_lb = pe_input * 2.20462
                pe_kg = pe_input
            else:
                pe_lb = pe_input
                pe_kg = pe_input / 2.20462

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147

            if es_paqueteria:
                if pe_lb <= umbral_min:
                    tot = min_usd
                    desc = f"Tarifa Mínima Base (1 a {umbral_min:.0f} lbs): ${min_usd:.2f} USD"
                else:
                    tot = pe_lb * t_lb
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
                detalle_pdf = f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"

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
                )
            if pulso_confirmar and not isinstance(st.session_state.get("datos_pdf_confirmado"), dict):
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
                    tarifa_sigue_visible = tarifa_consolidada or cotizacion_vigente(
                        d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc"), ahora_hn
                    )
                    if not tarifa_sigue_visible and int(st.session_state.get("ultima_cot_id") or 0) == int(id_c):
                        tarifa_sigue_visible = True
                    if not tarifa_sigue_visible:
                        st.session_state.pop("datos_pdf_confirmado", None)
                    else:
                        dest_pdf = d_pdf.get("destino_entrega", st.session_state["modalidad_envio_seleccionada"])
                        fecha_doc = d_pdf.get("fecha_hora_doc", obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"))
                        estado_doc = texto_estado_cotizacion(
                            d_pdf.get("fecha_sql") or fecha_doc, 1 if tarifa_consolidada else 0, ahora_hn
                        )
                        if tarifa_consolidada:
                            titulo_emitida = (
                                f"Cotización CCM-COT-{id_c:05d} consolidada. El PDF Tarifa está listo."
                            )
                            detalle_emitida = f"✅ {estado_doc}"
                        else:
                            titulo_emitida = (
                                f"Tarifa CCM-COT-{id_c:05d} · Pendiente de Confirmar"
                            )
                            detalle_emitida = (
                                f"⏳ {estado_doc}. Código de seguimiento CCM-COT-{id_c:05d}. "
                                "Descargue el formato para el fabricante o vaya a Mis Cotizaciones para confirmarla antes de 24 horas."
                            )

                        st.markdown(
                            f"""
                        <div style="background: linear-gradient(135deg, #f0fdf4, #dcfce7); border-left: 5px solid #22c55e; border-radius: 12px; padding: 16px; margin: 15px 0; box-shadow: 0 4px 12px rgba(34, 197, 94, 0.15);">
                            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px;">
                                <span style="font-size: 1.4rem;">🎉</span>
                                <h4 style="color: #166534; margin: 0; font-size: 1.05rem; font-weight: 800;">{titulo_emitida}</h4>
                            </div>
                            <div style="color:#166534; font-size:0.88rem; font-weight:700; margin-top:4px;">{detalle_emitida}</div>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )

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
            st.markdown("#### 📦 Mis Paquetes en Tránsito")
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?",
                    (casillero,),
                )
                paquetes = c.fetchall()

            if paquetes:
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
            else:
                st.info("No tienes paquetes registrados en travesía.")

            st.markdown("#### 📄 Documentos de cotizaciones confirmadas")
            st.caption("Descargue la ficha de bodega y el PDF Tarifa de cada cotización consolidada.")
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
                for cot_env in cotizaciones_despacho:
                    id_e, al_e, an_e, la_e, pe_e, vol_e, tot_e, fec_e, conf_e = cot_env
                    es_foco = foco_envios and int(id_e) == foco_envios
                    borde = "#004ac1" if es_foco else "#e2e8f0"
                    fondo = "#eff6ff" if es_foco else "#f8fafc"
                    if es_foco:
                        st.markdown('<div id="cotizacion-envio-foco"></div>', unsafe_allow_html=True)
                        st.success(
                            f"CCM-COT-{id_e:05d} está lista para seguimiento. "
                            "Descargue la Ficha y el PDF Tarifa de esta cotización consolidada."
                        )
                        desplazar_a_ancla("cotizacion-envio-foco")
                    id_ancla_env = f'id="cotizacion-env-{id_e}"'
                    st.markdown(
                        f"""
                    <div {id_ancla_env} style="background:{fondo}; border:1.5px solid {borde}; border-radius:10px; padding:10px 14px; margin-bottom:8px; font-size:0.85rem;">
                        <b>🔖 CCM-COT-{id_e:05d}</b> &bull; Fecha: {formatear_fecha_pantalla(fec_e)}{" &bull; <span style='color:#004ac1;font-weight:800;'>En seguimiento</span>" if es_foco else ""}<br>
                        <small style="color:#475569;">📐 Medidas: {al_e:.1f}x{an_e:.1f}x{la_e:.1f} cm | Peso: {pe_e:.1f} lbs | 💰 Total: <b>${tot_e:.2f} USD</b></small><br>
                        <small style="color:#1d4ed8; font-weight:700;">✅ Consolidada — permanente en el historial del casillero</small>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )
                    pdf_ficha_env = generar_pdf_etiqueta_proveedor(
                        casillero=casillero,
                        nombre=nombre_completo,
                        telefono=tel_cli,
                        ciudad=ciu_cli,
                        al=al_e,
                        an=an_e,
                        la=la_e,
                        pe_lb=pe_lb_c,
                        pe_kg=pe_lb_c / 2.20462 if 'pe_lb_c' in locals() else pe_e / 2.20462,
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
                for cot_f in cotizaciones_ficha:
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
            else:
                st.info("Confirme una cotización para descargar su ficha de bodega.")
            espaciador_barra_inferior("safe_fichas")

    pintar_barra_inferior(total_cotizaciones)

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
    st.error("Rol no reconocido o sesión no iniciada. Inicie sesión de nuevo.")
    if st.button("Volver al login"):
        logout()
