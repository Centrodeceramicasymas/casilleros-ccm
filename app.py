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
DB_NAME = "ccm_maritime_enterprise.db"
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
# Hubs y módulos base siguen abiertos; Envíos solo se ve en la barra al abrir Mis Cotizaciones.
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
    if not cotizacion_existe_en_casillero(id_cot):
        st.session_state.pop("datos_pdf_confirmado", None)
        st.session_state.pop("ultima_cot_id", None)
        return None
    if cotizacion_esta_confirmada(id_cot):
        return d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc")
    fecha_pdf = d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc")
    if cotizacion_vigente(fecha_pdf, ahora):
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


def confirmar_cotizacion_casillero(id_cot, casillero):
    try:
        cid = int(id_cot)
    except (TypeError, ValueError):
        return False
    cas = formatear_casillero(casillero or "")
    if not cas:
        return False
    _, ahora = estampa_tiempo_honduras()
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
        return cur.rowcount > 0


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


def selector_modalidad_entrega(opciones_modalidad):
    """Select de entrega en el cuerpo del Cotizador (fuera del header sticky, sin CSS sobre Baseweb)."""
    idx_mod = opciones_modalidad.index(st.session_state["modalidad_envio_seleccionada"])
    sel_kwargs = {"key": "sb_modalidad_entrega"}
    if "sb_modalidad_entrega" not in st.session_state:
        sel_kwargs["index"] = idx_mod
    elif st.session_state.get("sb_modalidad_entrega") not in opciones_modalidad:
        st.session_state["sb_modalidad_entrega"] = opciones_modalidad[idx_mod]
    mod_elegida = st.selectbox(
        "🏪 ¿Cómo deseas recibir tu compra?",
        opciones_modalidad,
        **sel_kwargs,
    )
    if mod_elegida != st.session_state["modalidad_envio_seleccionada"]:
        st.session_state["modalidad_envio_seleccionada"] = mod_elegida
        st.session_state.pop("datos_pdf_confirmado", None)
        st.rerun()


ALIAS_VISTA = {
    "Fichas": "Etiqueta",
    "Mis cotizaciones": "Mis Cotizaciones",
    "Mis envíos": "Mis Envíos",
    "Envíos": "Mis Envíos",
}


def ir_a(vista, hub="_omit"):
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
    st.rerun()


def ir_a_envios_de_cotizacion(id_cot):
    """Abre Envíos con la cotización consolidada en foco (PDF Tarifa y datos del envío)."""
    try:
        st.session_state["cotizacion_envio_foco"] = int(id_cot)
    except (TypeError, ValueError):
        st.session_state.pop("cotizacion_envio_foco", None)
    st.session_state["china_modulos_desbloqueados"] = True
    ir_a("Mis Envíos", hub="china")


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
    st.session_state["guia_omitida"] = True
    st.session_state["guia_paso"] = 0
    st.session_state["abrir_guia_rapida"] = False


def completar_guia_interactiva():
    st.session_state["guia_activa"] = False
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
        f'<div class="guia-globo">'
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


def pintar_coach_guia():
    detectar_avance_descarga_guia()
    aplicar_clase_guia_js()
    if not guia_esta_activa():
        return
    if st.session_state.get("hub") != "china":
        return
    with st.container(key="guia_coach"):
        st.markdown(html_globo_guia(), unsafe_allow_html=True)
        if st.button("Omitir Guía", type="secondary", key="btn_omitir_guia"):
            omitir_guia_interactiva()
            st.rerun()


def disparar_guia_china_si_aplica():
    """Activa la guía interactiva al entrar a China por primera vez en la sesión."""
    if st.session_state.get("hub") != "china":
        return
    if st.session_state.get("guia_china_auto_vista"):
        return
    if st.session_state.get("guia_omitida") or st.session_state.get("guia_completada"):
        st.session_state["guia_china_auto_vista"] = True
        return
    st.session_state["guia_china_auto_vista"] = True
    iniciar_guia_interactiva(1)


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


@st.dialog("Editar perfil", width="large")
def dialogo_editar_perfil():
    perfil = cargar_perfil_usuario(st.session_state.get("casillero"))
    if not perfil:
        st.error("No se encontró el perfil de este casillero.")
        return
    st.caption("El correo y el código de casillero no se pueden modificar.")
    nom = st.text_input("Nombre completo", value=perfil["nombre"], key="perfil_nom")
    tel = st.text_input("Teléfono / WhatsApp", value=perfil["telefono"], key="perfil_tel")
    dep = st.text_input("Departamento", value=perfil["departamento"], key="perfil_dep")
    ciu = st.text_input("Ciudad", value=perfil["ciudad"], key="perfil_ciu")
    dir_e = st.text_area("Dirección de entrega", value=perfil["direccion"], key="perfil_dir")
    dni = st.text_input("Número de Identidad / DNI", value=perfil["dni"], key="perfil_dni")
    st.text_input("Correo electrónico", value=perfil["correo"], disabled=True, key="perfil_mail")
    st.text_input("Código de casillero", value=perfil["casillero"], disabled=True, key="perfil_cas")
    if st.button("💾 Guardar Cambios", type="primary", key="perfil_guardar", use_container_width=True):
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
    """Pantalla completa Más: cuenta, módulos restantes y cierre de sesión."""
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
        st.markdown(
            f'<div class="mas-card mas-cuenta">'
            f'<div class="mas-cuenta-nombre">{html.escape(nombre)}</div>'
            f'<div class="mas-cuenta-cas">Casillero {html.escape(cas)}</div>'
            f'<div class="mas-cuenta-mail">{html.escape(correo)}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("✏️  Editar perfil", key="mas_editar_perfil", use_container_width=True):
            abrir_dialogo_editar_perfil()
        st.markdown('<div class="mas-seccion">Módulos y operaciones</div>', unsafe_allow_html=True)
        if st.button("📦  Mis envíos", key="mas_envios", use_container_width=True):
            ir_a("Mis Envíos", hub="china")
        if st.button("📋  Fichas", key="mas_fichas", use_container_width=True):
            ir_a("Fichas", hub="china")
        if st.button("📄  Mis Cotizaciones", key="mas_cotizaciones", use_container_width=True):
            ir_a("Mis Cotizaciones", hub="china")
        if st.button("🛍️  Catálogo", key="mas_catalogo", use_container_width=True):
            ir_a("Catálogo", hub="china")
        if st.button("🧮  Cotizador", key="mas_cotizador", use_container_width=True):
            avanzar_guia_si(1, 2)
            ir_a("Cotizador", hub="china")
        st.markdown('<div class="mas-seccion">Sistema / Sesión</div>', unsafe_allow_html=True)
        if mostrar_btn_guia:
            if st.button("Guía", type="secondary", key="btn_guia_rapida", use_container_width=True):
                iniciar_guia_interactiva(1)
                st.rerun()
        if st.button("⏻  Cerrar sesión", type="secondary", key="btn_logout_cliente", use_container_width=True):
            ir_a("Cerrar")
        espaciador_barra_inferior("safe_mas")


def espaciador_barra_inferior(clave):
    """Reserva altura real al final de la vista para que el último botón quede sobre la píldora."""
    with st.container(key=clave):
        st.markdown("&nbsp;")


def pintar_barra_inferior(total_cotizaciones=0):
    """Píldora flotante iOS: Inicio, Catálogo, Mis cotizaciones, Cotizador y Más."""
    vista = st.session_state.get("vista_activa") or st.session_state.get("sub_tab_inicio") or "Inicio"
    inicio_activo = vista == "Inicio"
    catalogo_activo = vista == "Catálogo"
    cot_activo = vista == "Mis Cotizaciones"
    cotizador_activo = vista == "Cotizador"
    mas_activo = vista in ("Más", "Mis Envíos", "Etiqueta", "Configuración", "Consultas")
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
                if st.button(
                    f"{icono}\n{etiqueta}",
                    type="primary" if activo else "secondary",
                    key=f"bnav_{dest}",
                    use_container_width=True,
                ):
                    if dest == "inicio":
                        ir_a("Inicio", hub=None)
                    elif dest == "catalogo":
                        ir_a("Catálogo", hub="china")
                    elif dest == "cotizaciones":
                        ir_a("Mis Cotizaciones", hub="china")
                    elif dest == "cotizador":
                        avanzar_guia_si(1, 2)
                        ir_a("Cotizador", hub="china")
                    elif dest == "mas":
                        ir_a("Más")
    anclar_barra_inferior()


def anclar_barra_inferior():
    """Aplica position:fixed a la píldora sin mover nodos del DOM (evita errores de React)."""
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
                }
                const hueco = "calc(180px + env(safe-area-inset-bottom, 0px))";
                doc.querySelectorAll(".block-container, [data-testid='stMainBlockContainer'], .stMainBlockContainer, [data-testid='stAppViewBlockContainer']").forEach((el) => {
                  el.style.setProperty("padding-bottom", hueco, "important");
                });
                const chromeCss =
                  '#MainMenu, footer, [data-testid="stHeader"], [data-testid="stToolbar"],' +
                  '[data-testid="stDecoration"], [data-testid="stStatusWidget"], .stStatusWidget,' +
                  '.stDeployButton, [data-testid="stAppDeployButton"], [class*="stAppDeployButton"],' +
                  '[class*="viewerBadge"], [class*="ViewerBadge"], [data-testid="stAppHeader"], .stAppHeader,' +
                  '[data-testid="stToolbarActions"], [data-testid="stHostToolbar"], [data-testid="stHostHeader"],' +
                  '[data-testid="stBaseButton-header"], [data-testid="stBaseButton-headerNoPadding"],' +
                  'button[kind="header"], button[kind="headerNoPadding"], button[title="Deploy"],' +
                  'iframe[title*="streamlit status" i] { display:none !important; visibility:hidden !important;' +
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
                docs.forEach((rootDoc) => {
                  inyectarCss(rootDoc);
                  rootDoc.querySelectorAll(
                    '#MainMenu, footer, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"], .stStatusWidget, .stDeployButton, [data-testid="stAppDeployButton"], [class*="stAppDeployButton"], [class*="viewerBadge"], [class*="ViewerBadge"], [data-testid="stToolbarActions"], [data-testid="stHostToolbar"], [data-testid="stBaseButton-headerNoPadding"], button[title="Deploy"], iframe[title*="streamlit status" i]'
                  ).forEach((el) => {
                    el.style.setProperty("display", "none", "important");
                    el.style.setProperty("visibility", "hidden", "important");
                    el.style.setProperty("pointer-events", "none", "important");
                    el.style.setProperty("opacity", "0", "important");
                  });
                  const vista = rootDoc.defaultView || win;
                  rootDoc.querySelectorAll("button, a, iframe, div").forEach((el) => {
                    if (el.closest('[class~="st-key-bottom_nav"], .st-key-bottom_nav')) return;
                    const stilo = vista.getComputedStyle(el);
                    if (stilo.position !== "fixed" && stilo.position !== "sticky") return;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0 || r.width > 72 || r.height > 72) return;
                    if (r.right > vista.innerWidth - 96 && r.bottom > vista.innerHeight - 96) {
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
                const offset = Math.ceil(bottom + gap) + "px";
                const destinos = [doc.documentElement, doc.body, doc.querySelector(".stApp")];
                destinos.forEach((nodo) => {
                  if (nodo && nodo.style) nodo.style.setProperty("--header-offset", offset);
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


def desplazar_a_ancla(element_id):
    """Auto-scroll suave hasta un ancla, dejando el encabezado congelado por encima."""
    eid = str(element_id or "").replace("\\", "").replace('"', "")
    if not eid:
        return
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
            el.style.scrollMarginTop = margen + "px";
            el.scrollIntoView({{ behavior: "smooth", block: "start" }});
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
# 3. BASE DE DATOS SQLITE & UTILIDADES
# ---------------------------------------------------------
def hash_pwd(password):
    return hashlib.sha256(password.encode()).hexdigest()


def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)


def init_db():
    with get_db() as conn:
        c = conn.cursor()
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
                fecha_creacion TEXT NOT NULL
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

        admin_pass = hash_pwd("admin123")
        c.execute(
            """
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal,
                telefono_principal, departamento, ciudad, direccion_exacta,
                password_hash, rol, activo, fecha_creacion
            ) VALUES (
                'CCM-08011990', 'Super Administrador', '0801199000000', 'admin@ccm.hn',
                '+504 9999-0000', 'Intibucá', 'San Juan', 'Oficina Central CCM',
                ?, 'admin', 1, '2026-08-22 00:00:00'
            )
            """,
            (admin_pass,),
        )

        demo_pass = hash_pwd("cliente123")
        c.execute(
            """
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal,
                telefono_principal, departamento, ciudad, direccion_exacta,
                rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion
            ) VALUES (
                'CCM-15011985', 'María Elena López', '1501198500990', 'cliente@ccm.hn',
                '+504 9577-1099', 'Intibucá', 'San Juan', 'Barrio El Centro, frente al parque',
                'Cerámica & Acabados', 'Retiro en Bodega Central (San Juan, Intibucá)',
                ?, 'cliente', 1, '2026-08-22 00:00:00'
            )
            """,
            (demo_pass,),
        )
        super_pass = hash_pwd(CLAVE_INICIAL_SUPERADMIN)
        c.execute(
            """
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal,
                telefono_principal, departamento, ciudad, direccion_exacta,
                password_hash, rol, activo, fecha_creacion
            ) VALUES (
                'CCM-13011998', 'Domingo Heriberto Ardon', '1301199800990', 'heribertoardon1998@gmail.com',
                '+504 9577-1099', 'Intibucá', 'San Juan', 'Oficina Central CCM',
                ?, 'superadmin', 1, '2026-08-22 00:00:00'
            )
            """,
            (super_pass,),
        )
        c.execute(
            """
            INSERT OR IGNORE INTO paquetes (tracking, codigo_casillero, descripcion, contenedor_id, estado, fecha_actualizacion)
            VALUES ('CN-GZ-88421', 'CCM-15011985', 'Cajas de porcelanato 60x120', 'CCM-CNT-014', 'En Travesía Marítima', '2026-08-20 09:15:00')
            """
        )


init_db()


def get_tarifa(clave):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT valor FROM config_maritima WHERE clave = ?", (clave,))
        res = c.fetchone()
        return res[0] if res else 0.0


def set_tarifa(clave, valor):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO config_maritima (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )


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


migrar_prefijo_casillero()
asegurar_superadmin()
abrir_permisos_todos_los_usuarios()
restaurar_datos_operativos_cliente()
purgar_cotizaciones_no_confirmadas_vencidas()


def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return "".join(random.choice(caracteres) for _ in range(8))


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


def campo_numerico(label, lim_min, valor, lim_max, paso, clave, formato):
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
        )
    return st.number_input(
        label,
        min_value=float(lim_min),
        max_value=float(lim_max),
        value=float(valor),
        step=float(paso),
        format=formato,
        key=clave,
    )


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


def pintar_modulo_aliexpress_eeuu(casillero):
    st.markdown("#### 🇺🇸 EE. UU.")
    st.caption("Búsqueda y cotización AliExpress con destino / envío a Estados Unidos.")
    st.markdown(
        f'<div class="ae-casillero-chip">Casillero activo: {casillero}</div>',
        unsafe_allow_html=True,
    )
    if credenciales_configuradas():
        st.caption("API AliExpress Open Platform · moneda USD · país de destino US · idioma ES.")
    else:
        st.info(
            "Configure `ALIEXPRESS_APP_SECRET` (y opcionalmente `ALIEXPRESS_TRACKING_ID`) "
            "en secretos o variables de entorno para resultados en vivo. "
            "Mientras tanto se usa un catálogo de demostración con envío a EE. UU."
        )

    flash = st.session_state.pop("ae_flash", None)
    if flash:
        st.success(flash)

    meta_prev = st.session_state.get("ae_busqueda_meta") or {}
    if meta_prev.get("error"):
        st.error(meta_prev["error"])
    if meta_prev.get("aviso"):
        st.warning(meta_prev["aviso"])

    f1, f2, f3 = st.columns([1, 1, 1.2])
    with f1:
        min_usd = st.number_input(
            "Precio mín. (USD)",
            min_value=0.0,
            max_value=100000.0,
            value=0.0,
            step=1.0,
            key="ae_min_usd",
        )
    with f2:
        max_usd = st.number_input(
            "Precio máx. (USD)",
            min_value=0.0,
            max_value=100000.0,
            value=0.0,
            step=1.0,
            key="ae_max_usd",
        )
    with f3:
        orden = st.selectbox(
            "Ordenar por",
            ["Más vendidos", "Mejor precio", "Calificación"],
            key="ae_orden",
        )

    tab_kw, tab_img = st.tabs(["Búsqueda por Palabra Clave", "Búsqueda por Imagen"])
    with tab_kw:
        keyword = st.text_input(
            "Término de búsqueda",
            placeholder="Ej. piezas cnc, auriculares, herramientas",
            key="ae_keyword",
        )
        buscar_kw = st.button(
            "🔍 Buscar en AliExpress",
            type="primary",
            key="btn_ae_buscar_kw",
            use_container_width=True,
        )
        if buscar_kw:
            with st.spinner("Buscando productos en AliExpress..."):
                resultado = _ejecutar_busqueda_aliexpress(
                    "texto", keyword, None, min_usd, max_usd, orden
                )
            st.session_state["ae_resultados"] = resultado.get("productos") or []
            st.session_state["ae_busqueda_meta"] = resultado
            st.rerun()
    with tab_img:
        img_up = st.file_uploader(
            "Foto del producto (JPG o PNG)",
            type=["jpg", "jpeg", "png"],
            key="ae_img_upload",
        )
        buscar_img = st.button(
            "🔍 Buscar en AliExpress",
            type="primary",
            key="btn_ae_buscar_img",
            use_container_width=True,
        )
        if buscar_img:
            imagen_bytes = img_up.getvalue() if img_up else None
            with st.spinner("Buscando productos en AliExpress..."):
                resultado = _ejecutar_busqueda_aliexpress(
                    "imagen", "", imagen_bytes, min_usd, max_usd, orden
                )
            st.session_state["ae_resultados"] = resultado.get("productos") or []
            st.session_state["ae_busqueda_meta"] = resultado
            st.rerun()

    resultados = st.session_state.get("ae_resultados") or []
    meta = st.session_state.get("ae_busqueda_meta")
    if not meta:
        st.markdown(
            '<div class="hub-empty-box ae-empty-hint">'
            "<div style='font-weight:800;color:#0f172a;margin-bottom:6px;'>AliExpress · envío a EE. UU.</div>"
            "<div style='font-size:0.86rem;font-weight:600;'>"
            "Escriba un término (piezas CNC, auriculares, herramientas) o suba una foto del producto."
            "</div>"
            "<div style='font-size:0.78rem;margin-top:10px;color:#94a3b8;'>"
            "Los resultados muestran precio en USD, calificación y enlace de compra. "
            "Use Cotizar importación / Agregar a casillero para vincular el artículo a su casillero."
            "</div></div>",
            unsafe_allow_html=True,
        )
    elif not resultados:
        if not meta.get("error"):
            st.warning("No hay stock disponible para los filtros indicados.")
    else:
        fuente = (st.session_state.get("ae_busqueda_meta") or {}).get("fuente")
        n_prod = len(resultados)
        etiqueta_fuente = "AliExpress" if fuente == "api" else "demostración"
        st.caption(f"{n_prod} producto{'s' if n_prod != 1 else ''} · {etiqueta_fuente} · destino US · USD")
        n_cols = 3
        for fila in range(0, n_prod, n_cols):
            cols = st.columns(n_cols, gap="small")
            for offset, col in enumerate(cols):
                idx = fila + offset
                if idx >= n_prod:
                    break
                prod = resultados[idx]
                with col:
                    pintar_tarjeta_aliexpress(prod, casillero, idx)

    carrito = listar_carrito_ae(casillero)
    st.markdown("##### Artículos AliExpress en el casillero")
    if not carrito:
        st.caption("Aún no hay productos de AliExpress vinculados a este casillero.")
        return
    for sku, nombre, cantidad, precio, _imagen, fecha in carrito:
        st.markdown(
            f"- **{nombre}** · `{sku}` · {int(cantidad)} ud. · "
            f"**${float(precio):.2f} USD** · {fecha}"
        )


def pintar_tarjeta_aliexpress(prod, casillero, idx):
    titulo = (prod.get("titulo") or "Producto AliExpress").strip()
    if len(titulo) > 78:
        titulo_corto = titulo[:75].rstrip() + "…"
    else:
        titulo_corto = titulo
    precio = float(prod.get("precio_usd") or 0)
    calif = prod.get("calificacion")
    enlace = prod.get("enlace") or "https://www.aliexpress.com"
    imagen = prod.get("imagen_url") or ""
    calc = calcular_costo_puesto_honduras(
        precio,
        float(prod.get("peso_kg") or 0.8),
        float(prod.get("volumen_m3") or 0.004),
        1,
    )
    with st.container(border=True):
        if imagen:
            st.image(imagen, use_container_width=True)
        st.markdown(f"**{titulo_corto}**")
        if calif:
            st.caption(f"★ {calif:.1f} · ID {prod.get('product_id')}")
        else:
            st.caption(f"ID {prod.get('product_id')}")
        st.markdown(f'<div class="ae-price">${precio:.2f} USD</div>', unsafe_allow_html=True)
        st.caption(f"Est. puesto en Honduras: ${calc['total_estimado_usd']:.2f} USD")
        st.link_button("Ver en AliExpress", enlace, use_container_width=True)
        if st.button(
            "Cotizar importación / Agregar a casillero",
            key=f"ae_add_{prod.get('product_id')}_{idx}",
            use_container_width=True,
        ):
            sku = agregar_producto_ae_a_casillero(casillero, prod)
            st.session_state["ae_flash"] = (
                f"Artículo vinculado al casillero {casillero} (`{sku}`). "
                f"Precio AliExpress ${precio:.2f} USD."
            )
            st.rerun()


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
    if st.session_state.get("autenticado", False):
        return True

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
        st.session_state["rol"] = user_rec[4]
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

        vistas_validas = {"Inicio", "China", "EE. UU.", "Honduras", "Consultas", "Configuración", "Más", "Fichas"} | VISTAS_MODULO
        if vista_url in ALIAS_VISTA:
            vista_url = ALIAS_VISTA[vista_url]
        if vista_url in vistas_validas:
            st.session_state["sub_tab_inicio"] = vista_url
            st.session_state["vista_activa"] = vista_url
        if hub_url in HUBS:
            st.session_state["hub"] = hub_url
        elif vista_url in MODULOS_POR_ID:
            st.session_state["hub"] = MODULOS_POR_ID[vista_url]
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
        "modalidad_envio_seleccionada",
        "sb_modalidad_entrega",
        "sub_tab_inicio",
        "vista_activa",
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
        --sticky-h: 132px;
        --sticky-delivery: 0px;
        --header-offset: var(--sticky-h);
        --header-gap: 16px;
    }

    html, body {
        overflow-x: hidden !important;
        max-width: 100% !important;
        background-color: #f8fafc !important;
        background: #f8fafc !important;
        color: #0f172a !important;
        color-scheme: light !important;
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
        overflow-y: visible !important;
        height: auto !important;
        min-height: 100% !important;
        color-scheme: light !important;
        max-width: 100% !important;
    }

    #MainMenu, footer, header,
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
    button[kind="header"],
    button[kind="headerNoPadding"],
    button[title="Deploy"],
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
        padding-bottom: calc(180px + env(safe-area-inset-bottom, 0px)) !important;
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
        height: var(--header-offset) !important;
        min-height: var(--header-offset) !important;
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        pointer-events: none !important;
        visibility: hidden !important;
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
            --sticky-h: 150px;
            --sticky-delivery: 0px;
            --header-offset: var(--sticky-h);
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
            --sticky-h: 148px;
            --header-offset: var(--sticky-h);
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
            --sticky-h: 140px;
            --header-offset: var(--sticky-h);
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
            --sticky-h: 136px;
            --header-offset: var(--sticky-h);
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

    .ccm-bottom-safe,
    .st-key-safe_mas,
    .st-key-safe_cotizador,
    .st-key-safe_cotizador_fin,
    .st-key-safe_catalogo {
        display: block !important;
        height: 180px !important;
        min-height: 180px !important;
        width: 100% !important;
        pointer-events: none !important;
        opacity: 0 !important;
    }
    .st-key-catalogo_formulario {
        padding-bottom: 24px !important;
        margin-bottom: 8px !important;
    }
    .st-key-vista_mas {
        padding-top: 4px !important;
        padding-bottom: calc(80px + env(safe-area-inset-bottom, 0px)) !important;
    }
    .st-key-guia_foco_tarifa,
    .st-key-btn_confirmar_tarifa,
    .st-key-btn_logout_cliente,
    .st-key-btn_buscar_china,
    .st-key-btn_escanear_catalogo {
        scroll-margin-bottom: calc(180px + env(safe-area-inset-bottom, 0px)) !important;
        margin-bottom: 16px !important;
    }
    .st-key-btn_buscar_china div.stButton > button,
    .st-key-btn_escanear_catalogo div.stButton > button,
    .st-key-btn_buscar_china [data-testid^="stBaseButton"],
    .st-key-btn_escanear_catalogo [data-testid^="stBaseButton"] {
        min-height: 48px !important;
        height: 48px !important;
    }

    .mas-seccion {
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #64748b;
        margin: 16px 4px 8px 4px;
    }
    .mas-seccion-cuenta {
        margin-top: 2px;
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
        border-radius: 14px !important;
        min-height: 42px !important;
        height: 42px !important;
        font-weight: 700 !important;
        box-shadow: none !important;
        margin-bottom: 4px !important;
    }
    .st-key-mas_envios div.stButton > button,
    .st-key-mas_fichas div.stButton > button,
    .st-key-mas_cotizaciones div.stButton > button,
    .st-key-mas_catalogo div.stButton > button,
    .st-key-mas_cotizador div.stButton > button,
    .st-key-mas_envios [data-testid^="stBaseButton"],
    .st-key-mas_fichas [data-testid^="stBaseButton"],
    .st-key-mas_cotizaciones [data-testid^="stBaseButton"],
    .st-key-mas_catalogo [data-testid^="stBaseButton"],
    .st-key-mas_cotizador [data-testid^="stBaseButton"] {
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
        overflow: hidden;
        background:
            linear-gradient(135deg, rgba(11, 58, 145, 0.92) 0%, rgba(0, 74, 193, 0.88) 52%, rgba(29, 78, 216, 0.9) 100%),
            radial-gradient(circle at 88% 12%, rgba(255, 255, 255, 0.22) 0%, transparent 42%);
        border-radius: 16px;
        padding: 20px 18px 18px 18px;
        color: #ffffff;
        margin: 10px 0 14px 0;
        box-shadow: 0 14px 32px rgba(0, 74, 193, 0.28);
        box-sizing: border-box;
    }
    .promo-ad-kicker {
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #bfdbfe;
        margin: 0 0 8px 0;
    }
    .promo-ad-title {
        font-size: 1.22rem;
        font-weight: 800;
        line-height: 1.25;
        margin: 0 0 10px 0;
        color: #ffffff;
        letter-spacing: -0.02em;
    }
    .promo-ad-body {
        font-size: 0.88rem;
        font-weight: 500;
        line-height: 1.45;
        color: #e2e8f0;
        margin: 0 0 14px 0;
    }
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
        background: #ffffff;
        color: #004ac1;
        font-weight: 800;
        font-size: 0.95rem;
        text-decoration: none;
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.18);
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
        margin: 8px 0 14px 0 !important;
        box-shadow: 0 8px 18px rgba(245, 158, 11, 0.18) !important;
        box-sizing: border-box !important;
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

    .st-key-btn_guia_rapida button,
    .st-key-btn_guia_rapida button[kind="secondary"] {
        min-height: 44px !important;
        height: 44px !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 12px !important;
        box-shadow: none !important;
        opacity: 1 !important;
    }
    .st-key-btn_guia_rapida button *,
    .st-key-btn_guia_rapida button[kind="secondary"] * {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        fill: #0f172a !important;
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
    .cotizacion-badge-pendiente {
        display: inline-block;
        margin-left: 6px;
        background: #fffbeb;
        color: #b45309;
        border: 1.5px solid #f59e0b;
        border-radius: 999px;
        padding: 2px 9px;
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.01em;
        animation: cotPulseInsignia 1.2s ease-in-out infinite;
        vertical-align: middle;
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

    .st-key-btn_logout_cliente button,
    .st-key-btn_logout_cliente button[kind="secondary"] {
        min-height: 44px !important;
        height: 44px !important;
        max-height: none !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 12px !important;
        box-shadow: none !important;
        opacity: 1 !important;
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

    elif st.session_state["vista_actual"] == "recuperar":
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

    casillero = formatear_casillero(st.session_state["casillero"])
    if casillero != st.session_state["casillero"]:
        st.session_state["casillero"] = casillero
    ahora_hn = obtener_tiempo_honduras()
    purgar_cotizaciones_no_confirmadas_vencidas(ahora_hn)
    _limpiar_cotizacion_vencida_en_sesion(ahora_hn)
    nombre_completo = st.session_state["nombre"]
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

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT id, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd,
                   COALESCE(fecha_creacion, fecha), IFNULL(confirmada, 0)
            FROM cotizaciones
            WHERE codigo_casillero = ?
            ORDER BY fecha_creacion DESC, id DESC
            """,
            (casillero,),
        )
        lista_todas_cotizaciones = ordenar_cotizaciones_desc(c.fetchall())

        lista_mis_cotizaciones = ordenar_cotizaciones_desc(
            [row for row in lista_todas_cotizaciones if cotizacion_visible_historial(row[7], row[8], ahora_hn)]
        )
        total_cotizaciones = len(lista_mis_cotizaciones)

        c.execute(
            "SELECT id, etiqueta, receptor_nombre, ciudad, direccion_exacta FROM direcciones_entrega WHERE codigo_casillero = ?",
            (casillero,),
        )
        direcciones_guardadas = c.fetchall()

    opciones_modalidad = [OPCION_PREDETERMINADA]
    for d in direcciones_guardadas:
        opciones_modalidad.append(f"📍 {d[1]} - {d[3]}")
    opciones_modalidad.append("➕ Crear Nueva Dirección de Envío")

    if st.session_state["modalidad_envio_seleccionada"] not in opciones_modalidad:
        st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA

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
    disparar_guia_china_si_aplica()
    pintar_coach_guia()

    if st.session_state["sub_tab_inicio"] == "Inicio":
        hub_sel = st.session_state.get("hub")

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
                ):
                    ir_a("Inicio", hub=hub_id)
        elif hub_sel == "china":
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
            if st.button("📖 Catálogo 1688", key="btn_consultas_1688", use_container_width=True):
                ir_a("Catálogo", hub="china")
        with c_q2:
            if st.button("🇺🇸 AliExpress", key="btn_consultas_ae", use_container_width=True):
                ir_a("Inicio", hub="eeuu")

    if st.session_state["sub_tab_inicio"] == "Configuración":
        st.markdown("#### ⚙️ Configuración")
        st.caption("Datos de su casillero. La sesión se conserva al cambiar de vista.")
        st.info(
            f"**Casillero:** `{casillero}`  \n"
            f"**Nombre:** {st.session_state.get('nombre') or '—'}  \n"
            f"**Correo:** {st.session_state.get('usuario') or '—'}  \n"
            f"**Entrega:** {st.session_state.get('modalidad_envio_seleccionada') or OPCION_PREDETERMINADA}"
        )
        st.link_button(
            "💬 Actualizar datos por WhatsApp",
            "https://wa.me/50495771099?text=" + urllib.parse.quote(
                f"Hola, necesito actualizar los datos de mi casillero {casillero}."
            ),
            use_container_width=True,
        )

    if st.session_state["sub_tab_inicio"] == "Mis Cotizaciones":
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
                id_ancla = 'id="cotizacion-foco-pendiente"' if pendiente_foco else f'id="cotizacion-ccm-{id_cot_item}"'
                borde = "#f59e0b" if pendiente_foco else "#e2e8f0"
                fondo = "#fffbeb" if pendiente_foco else "#f8fafc"
                insignia = (
                    '<span class="cotizacion-badge-pendiente">⚠️ Pendiente de Confirmar</span>'
                    if pendiente_foco
                    else ""
                )
                st.markdown(
                    f"""
                <div {id_ancla} class="{clase_foco}" style="background:{fondo}; border:1.5px solid {borde}; border-radius:10px; padding:10px 14px; margin-bottom:10px; font-size:0.85rem;">
                    <b>🔖 CCM-COT-{id_cot_item:05d}</b> &bull; Fecha: {formatear_fecha_pantalla(fec_c)} {insignia}<br>
                    <small style="color:#475569;">📐 Medidas: {al_c:.1f}x{an_c:.1f}x{la_c:.1f} cm | Peso: {pe_lb_c:.1f} lbs | 💰 Total: <b>${tot_c:.2f} USD</b></small><br>
                    <small style="color:{color_estado}; font-weight:700;">{icono_estado} {estado_txt}</small>
                </div>
                """,
                    unsafe_allow_html=True,
                )
                if pendiente_foco and not scroll_pendiente_hecho:
                    desplazar_a_cotizacion_pendiente()
                    scroll_pendiente_hecho = True

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
                    destino_entrega=st.session_state["modalidad_envio_seleccionada"],
                    fecha_emision=fec_c,
                )
                if consolidada:
                    col_env, col_pdf = st.columns(2)
                    with col_env:
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
                            if st.button(
                                "📦 Ir a Envíos",
                                type="primary",
                                key=f"btn_ir_envios_{id_cot_item}",
                                use_container_width=True,
                            ):
                                avanzar_guia_si(6, completar=True)
                                ir_a_envios_de_cotizacion(id_cot_item)
                    with col_pdf:
                        st.download_button(
                            f"📥 Descargar PDF CCM-COT-{id_cot_item:05d}",
                            pdf_historial,
                            f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                            "application/pdf",
                            key=f"dl_cot_{id_cot_item}",
                            use_container_width=True,
                        )
                else:
                    col_conf, col_pdf = st.columns(2)
                    with col_conf:
                        confirmar_ctx = (
                            st.container(key=f"foco_confirmar_{id_cot_item}")
                            if pendiente_foco
                            else st.container()
                        )
                        with confirmar_ctx:
                            if st.button(
                                "Confirmar Cotización",
                                type="primary",
                                key=f"btn_confirmar_cot_{id_cot_item}",
                                use_container_width=True,
                            ):
                                if confirmar_cotizacion_casillero(id_cot_item, casillero):
                                    st.session_state["china_modulos_desbloqueados"] = True
                                    st.session_state["cotizacion_envio_foco"] = int(id_cot_item)
                                    if int(st.session_state.get("cotizacion_historial_foco") or 0) == int(id_cot_item):
                                        st.session_state.pop("cotizacion_historial_foco", None)
                                    avanzar_guia_si(5, 6)
                                    st.rerun()
                    with col_pdf:
                        st.download_button(
                            f"📥 PDF CCM-COT-{id_cot_item:05d}",
                            pdf_historial,
                            f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                            "application/pdf",
                            key=f"dl_cot_{id_cot_item}",
                            use_container_width=True,
                        )
                st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)
        else:
            st.info(
                "No hay cotizaciones vigentes ni consolidadas. Emita una tarifa en el Cotizador; "
                "tiene 24 horas para confirmarla y habilitar Envíos."
            )

    if st.session_state["sub_tab_inicio"] == "Cotizador" and st.session_state["modalidad_envio_seleccionada"] == "➕ Crear Nueva Dirección de Envío":
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
            for dir_item in direcciones_guardadas:
                id_dir, etiq, rec, ciu_d, dir_e = dir_item
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
                    if st.button("🗑️ Eliminar", key=f"del_dir_{id_dir}", type="secondary"):
                        with get_db() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "DELETE FROM direcciones_entrega WHERE id = ? AND codigo_casillero = ?",
                                (id_dir, casillero),
                            )
                            conn.commit()
                        st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
                        st.session_state.pop("datos_pdf_confirmado", None)
                        st.toast(f"🗑️ Dirección '{etiq}' eliminada.")
                        st.rerun()

        st.markdown("---")
        st.markdown("##### ➕ Agregar Nueva Dirección de Entrega")
        c_et1, c_et2 = st.columns(2)
        with c_et1:
            etiqueta_in = st.text_input("Etiqueta de la dirección *", placeholder="Ej: Mi Casa, Sucursal 2, Taller")
            receptor_in = st.text_input("Nombre de quien recibe *", value=nombre_completo)
        with c_et2:
            tel_dir_in = st.text_input("Teléfono de contacto *", value=tel_cli)
            dep_dir_in = st.selectbox(
                "Departamento *",
                list(MUNICIPIOS_HONDURAS.keys()),
                index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0,
                key="sb_dep_nueva_dir",
            )

        ciu_dir_in = st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_dir_in], key="sb_ciu_nueva_dir")
        dir_exacta_in = st.text_area(
            "Dirección exacta y referencias *",
            placeholder="Barrio, calle, número de casa, puntos clave...",
        )

        c_sv1, c_sv2 = st.columns(2)
        with c_sv1:
            if st.button("💾 Guardar Dirección", type="primary", key="btn_guardar_nueva_dir"):
                if etiqueta_in and receptor_in and tel_dir_in and ciu_dir_in and dir_exacta_in:
                    f_ahora = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO direcciones_entrega (codigo_casillero, etiqueta, receptor_nombre, telefono, departamento, ciudad, direccion_exacta, fecha_creacion)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                casillero,
                                etiqueta_in,
                                receptor_in,
                                tel_dir_in,
                                dep_dir_in,
                                ciu_dir_in,
                                dir_exacta_in,
                                f_ahora,
                            ),
                        )
                        conn.commit()
                    st.success(f"✅ Dirección '{etiqueta_in}' guardada.")
                    st.session_state["modalidad_envio_seleccionada"] = f"📍 {etiqueta_in} - {ciu_dir_in}"
                    st.session_state.pop("datos_pdf_confirmado", None)
                    st.rerun()
                else:
                    st.error("Completa todos los campos obligatorios (*).")
        with c_sv2:
            if st.button("Cancelar", type="secondary", key="btn_cancelar_dir"):
                st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
                st.session_state.pop("datos_pdf_confirmado", None)
                st.rerun()

    if st.session_state.get("hub") == "china" and st.session_state["sub_tab_inicio"] in VISTAS_MODULO:
        st.markdown(
            (
                '<div class="banner-clearance"></div>'
                '<div class="app-banner-card">'
                '<div class="app-banner-tag">¡Y YA ESTÁ DISPONIBLE!</div>'
                '<div class="app-banner-title">En el momento que sientes que cargas con '
                '<span class="app-banner-accent">libras extra</span> que te pesan...</div>'
                '<div class="app-banner-sub">¡Te das cuenta que tienen solución con fletes marítimos desde China!<br>'
                f'<b style="color:#db2777;letter-spacing:1px;">VIVE LIGERO</b> &bull; Casillero asignado: <b>{casillero}</b>'
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )

    if st.session_state["sub_tab_inicio"] == "Catálogo":
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
                        f"🇭🇳 **Puesto en Honduras:** ${calc['total_estimado_usd']:.2f} USD (~L {calc['total_estimado_hnl']:.2f} HNL)\n\n*(Destino: {st.session_state['modalidad_envio_seleccionada']})*"
                    )

                    msg_cot = f"Hola Centro de Cerámicas y Más, me interesa importar este producto: {prod['nombre']} (SKU: {prod['sku']}) para mi casillero {casillero}. Cantidad: {prod['moq']} uds. Destino/Entrega: {st.session_state['modalidad_envio_seleccionada']}. Enlace: {prod['url_proveedor']}"
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
        st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
        selector_modalidad_entrega(opciones_modalidad)
        st.info(
            f"📍 **Dirección / Destino de Entrega Seleccionado:** `{st.session_state['modalidad_envio_seleccionada']}` *(Se imprimirá en todos los formatos)*"
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
        tipo_kwargs = {"key": "sb_tipo_carga_select"}
        if "sb_tipo_carga_select" not in st.session_state:
            tipo_kwargs["index"] = 0
        tipo_carga = st.selectbox("Modalidad de Importación:", tipo_opts, **tipo_kwargs)

        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

        c_u1, c_u2 = st.columns(2)
        with c_u1:
            unidad_medida = st.selectbox(
                "Unidad de Medida:", ["Centímetros (cm)", "Pulgadas (in)", "Metros (m)"], key="sb_unidad_medida"
            )
        with c_u2:
            unidad_peso = st.selectbox("Unidad de Peso:", ["Libras (lb)", "Kilogramos (kg)"], key="sb_unidad_peso")

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

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        with st.container(key="guia_foco_tarifa"):
            if st.button(
                "🤝 Confirmar Tarifa & Emitir Documentos",
                type="primary",
                key="btn_confirmar_tarifa",
            ):
                ahora_emision, f_hoy_sql = estampa_tiempo_honduras()
                f_hoy_doc = ahora_emision.strftime("%d/%m/%Y %I:%M:%S %p")
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
                        (casillero, al_val, an_val, la_val, pe_lb, vol_m3_val, vol_ft3_val, tot, f_hoy_sql, f_hoy_sql),
                    )
                    id_generado = cur.lastrowid

                st.session_state["ultima_cot_id"] = id_generado
                st.session_state["datos_pdf_confirmado"] = {
                    "tipo_carga": modalidad_pdf,
                    "al": al_val,
                    "an": an_val,
                    "la": la_val,
                    "peso_lb": pe_lb,
                    "peso_kg": pe_kg,
                    "vol_m3": vol_m3_val,
                    "vol_ft3": vol_ft3_val,
                    "total_usd": tot,
                    "detalle_tarifa": detalle_pdf,
                    "id_cot": id_generado,
                    "destino_entrega": st.session_state["modalidad_envio_seleccionada"],
                    "fecha_hora_doc": f_hoy_doc,
                    "fecha_sql": f_hoy_sql,
                }
                st.session_state["china_modulos_desbloqueados"] = china_seguimiento_habilitado()
                avanzar_guia_si(2, 3)
                st.rerun()
        espaciador_barra_inferior("safe_cotizador")

        if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
            d_pdf = st.session_state["datos_pdf_confirmado"]
            id_emitida = d_pdf.get("id_cot")
            tarifa_consolidada = cotizacion_esta_confirmada(id_emitida, casillero)
            tarifa_sigue_visible = tarifa_consolidada or cotizacion_vigente(
                d_pdf.get("fecha_sql") or d_pdf.get("fecha_hora_doc"), ahora_hn
            )
            if not tarifa_sigue_visible:
                st.session_state.pop("datos_pdf_confirmado", None)
            else:
                mismo_destino = d_pdf.get("destino_entrega", "") == st.session_state["modalidad_envio_seleccionada"]
                mismo_alto = abs(d_pdf.get("al", 0.0) - al_val) < 0.01
                mismo_ancho = abs(d_pdf.get("an", 0.0) - an_val) < 0.01
                mismo_largo = abs(d_pdf.get("la", 0.0) - la_val) < 0.01
                mismo_peso = abs(d_pdf.get("peso_lb", 0.0) - pe_lb) < 0.01
                mismo_precio = abs(d_pdf.get("total_usd", 0.0) - tot) < 0.01

                if mismo_destino and mismo_alto and mismo_ancho and mismo_largo and mismo_peso and mismo_precio:
                    id_c = d_pdf.get("id_cot", 1)
                    dest_pdf = d_pdf.get("destino_entrega", st.session_state["modalidad_envio_seleccionada"])
                    fecha_doc = d_pdf.get("fecha_hora_doc", obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"))
                    estado_doc = texto_estado_cotizacion(
                        d_pdf.get("fecha_sql") or fecha_doc, 1 if tarifa_consolidada else 0, ahora_hn
                    )
                    if tarifa_consolidada:
                        titulo_emitida = (
                            f"Cotización CCM-COT-{id_c:05d} consolidada. El PDF Tarifa está en Envíos."
                        )
                        detalle_emitida = f"✅ {estado_doc}"
                    else:
                        titulo_emitida = (
                            f"Tarifa CCM-COT-{id_c:05d} emitida el {fecha_doc} para entrega en: {dest_pdf}"
                        )
                        detalle_emitida = (
                            f"⏳ {estado_doc}. Confírmela en Mis Cotizaciones para que no caduque; el PDF Tarifa quedará en Envíos."
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
                    )

                    with st.container(key="guia_foco_pdf_fab"):
                        if st.download_button(
                            "📥 PDF Fabricante",
                            pdf_fab,
                            f"Shipping_Label_Fabricante_{casillero}.pdf",
                            "application/pdf",
                            key=f"dl_pdf_fab_{id_c}",
                            use_container_width=True,
                        ):
                            avanzar_guia_si(3, 4)

                    with st.container(key="guia_foco_ver_cot"):
                        if st.button(
                            "Ver en Mis Cotizaciones",
                            type="primary",
                            key=f"btn_ver_mis_cotizaciones_{id_c}",
                            use_container_width=True,
                        ):
                            if guia_paso_actual() in (3, 4):
                                st.session_state["guia_paso"] = 5
                            ir_a_cotizacion_emitida(id_c)

                    texto_wa = f"Hola Centro de Cerámicas y Más, confirmo cotización CCM-COT-{id_c:05d} generada el {fecha_doc} del casillero {casillero}. Destino de Entrega: {dest_pdf}. Total: ${d_pdf.get('total_usd', 0):.2f} USD."
                    url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(texto_wa)
                    st.markdown(
                        f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; border-radius:12px; width:100%; height:48px; font-weight:bold; cursor:pointer; margin-top:8px; box-shadow: 0 4px 10px rgba(34, 197, 94, 0.25);">📲 Enviar a WhatsApp (+504 9577-1099)</button></a>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.session_state.pop("datos_pdf_confirmado", None)

        espaciador_barra_inferior("safe_cotizador_fin")

    elif st.session_state["sub_tab_inicio"] == "Mis Envíos":
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
                    pe_lb=pe_e,
                    pe_kg=pe_e / 2.20462,
                    vol_m3=vol_e,
                    destino_entrega=st.session_state["modalidad_envio_seleccionada"],
                    fecha_emision=fec_e,
                )
                st.download_button(
                    f"🏷️ Descargar Ficha CCM-COT-{id_e:05d}",
                    pdf_ficha_env,
                    f"Ficha_Bodega_{casillero}_COT{id_e:05d}.pdf",
                    "application/pdf",
                    key=f"dl_ficha_env_{id_e}",
                    use_container_width=True,
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
                    destino_entrega=st.session_state["modalidad_envio_seleccionada"],
                    fecha_emision=fec_e,
                )
                st.download_button(
                    f"📥 PDF Tarifa CCM-COT-{id_e:05d}",
                    pdf_tarifa_env,
                    f"Comprobante_Tarifa_{casillero}_COT{id_e:05d}.pdf",
                    "application/pdf",
                    key=f"dl_tarifa_env_{id_e}",
                    use_container_width=True,
                )
                st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)
        else:
            st.info("Confirme una cotización para consultar y descargar la Ficha y el PDF Tarifa en este módulo.")

    elif st.session_state["sub_tab_inicio"] == "Etiqueta":
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
                    destino_entrega=st.session_state["modalidad_envio_seleccionada"],
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

    pintar_barra_inferior(total_cotizaciones)

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
