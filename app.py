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
import html

st.set_page_config(
    page_title="Centro de Cerámicas y Más — Casillero & Catálogo China",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

try:
    from zoneinfo import ZoneInfo
    ZONA_HONDURAS = ZoneInfo("America/Tegucigalpa")
except Exception:
    ZONA_HONDURAS = timezone(timedelta(hours=-6), name="America/Tegucigalpa")

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DEL SISTEMA & ZONA HORARIA HONDURAS (UTC-6)
# ---------------------------------------------------------
DB_NAME = "ccm_maritime_enterprise.db"
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
    dt = ahora or obtener_tiempo_honduras()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZONA_HONDURAS)
    else:
        dt = dt.astimezone(ZONA_HONDURAS)
    return dt, dt.strftime("%Y-%m-%d %H:%M:%S")


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


def formatear_fecha_pantalla(fecha_raw):
    dt = parsear_fecha_cotizacion(fecha_raw)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else str(fecha_raw or "")


def cotizacion_vigente(fecha_raw, ahora=None):
    dt = parsear_fecha_cotizacion(fecha_raw)
    if dt is None:
        return False
    ahora = ahora or obtener_tiempo_honduras()
    return timedelta(0) <= (ahora - dt) <= VIGENCIA_COTIZACION


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


def es_cotizacion_confirmada(valor):
    try:
        return int(valor or 0) == 1
    except (TypeError, ValueError):
        return False


def cotizacion_visible_historial(fecha_raw, confirmada, ahora=None):
    return True if es_cotizacion_confirmada(confirmada) else cotizacion_vigente(fecha_raw, ahora)


def texto_estado_cotizacion(fecha_raw, confirmada, ahora=None):
    if es_cotizacion_confirmada(confirmada):
        return "Consolidada — permanente en el historial del casillero"
    return texto_vigencia_cotizacion(fecha_raw, ahora)


def clave_orden_cotizacion(fecha_raw, id_cot=0):
    dt = parsear_fecha_cotizacion(fecha_raw)
    ts = dt.timestamp() if dt is not None else 0.0
    try:
        cid = int(id_cot or 0)
    except (TypeError, ValueError):
        cid = 0
    return (-ts, -cid)


def ordenar_cotizaciones_desc(filas, idx_fecha=7, idx_id=0):
    return sorted(filas, key=lambda r: clave_orden_cotizacion(r[idx_fecha], r[idx_id]))


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
            {"id": "Mis Cotizaciones", "label": "Mis Cotizaciones"},
            {"id": "Catálogo", "label": "Catálogo"},
            {"id": "Cotizador", "label": "Cotizador"},
            {"id": "Mis Envíos", "label": "Envíos"},
            {"id": "Etiqueta", "label": "Fichas"},
        ],
    },
    "eeuu": {
        "label": "EE. UU.",
        "icon": "🇺🇸",
        "descripcion": "Gestión logística de compras y flete desde Estados Unidos",
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
ROLES_ADMIN = ("admin", "superadmin")
DNI_SUPERADMIN = "1301199800990"
NOMBRE_SUPERADMIN = "Domingo Heriberto Ardon"
CORREO_SUPERADMIN = "heribertoardon1998@gmail.com"
CLAVE_INICIAL_SUPERADMIN = "1301"
PERMISOS_ABIERTOS_TEMPORAL = True


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
                cur.execute("SELECT IFNULL(confirmada, 0) FROM cotizaciones WHERE id = ? AND codigo_casillero = ?", (cid, cas))
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


@st.cache_data(ttl=45, show_spinner=False)
def cargar_direcciones_db(casillero):
    cas = formatear_casillero(casillero or "")
    if not cas:
        return []
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, etiqueta, receptor_nombre, ciudad, direccion_exacta FROM direcciones_entrega WHERE codigo_casillero = ?",
            (cas,),
        )
        return cur.fetchall()


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
            lista.append({
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
            })
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
        cargar_cotizaciones_db.clear()
    except Exception:
        pass
    marcar_cotizacion_sesion_confirmada(cid, cas)
    return True


def firma_parametros_cotizador(al, an, la, peso_lb, destino, tipo_carga):
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
    st.session_state.pop("datos_pdf_confirmado", None)
    st.session_state.pop("_ccm_scroll_emit", None)
    st.session_state.pop("_ccm_emit_error", None)
    for clave in list(st.session_state.keys()):
        ks = str(clave)
        if ks.startswith("dl_pdf_fab_") or ks.startswith("btn_ver_mis_cotizaciones_"):
            st.session_state.pop(clave, None)


def sincronizar_emision_con_formulario(firma_actual):
    d_pdf = st.session_state.get("datos_pdf_confirmado")
    if not isinstance(d_pdf, dict):
        return False
    if firma_desde_emision(d_pdf) != tuple(firma_actual):
        invalidar_emision_visible_cotizador()
        return True
    return False


def emitir_tarifa_desde_snapshot():
    snap = st.session_state.get("_cot_emit_snapshot")
    casillero = formatear_casillero(st.session_state.get("casillero") or "")
    if not isinstance(snap, dict) or not casillero:
        return
    firma_snap = snap.get("firma_params") or firma_parametros_cotizador(
        snap.get("al"), snap.get("an"), snap.get("la"),
        snap.get("peso_lb"), snap.get("destino"), snap.get("tipo_carga"),
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    casillero, snap.get("al"), snap.get("an"), snap.get("la"),
                    snap.get("peso_lb"), snap.get("vol_m3"), snap.get("vol_ft3"),
                    snap.get("total_usd"), f_hoy_sql, f_hoy_sql,
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
        ids_borrar = [cid for cid, fecha, conf in cur.fetchall() if not es_cotizacion_confirmada(conf) and not cotizacion_vigente(fecha, ahora)]
        if not ids_borrar:
            return 0
        cur.executemany("DELETE FROM cotizaciones WHERE id = ?", [(cid,) for cid in ids_borrar])
        conn.commit()
        return len(ids_borrar)


def al_cambiar_modalidad_entrega():
    invalidar_emision_visible_cotizador()
    nueva = st.session_state.get("sb_modalidad_entrega")
    if nueva:
        st.session_state["modalidad_envio_seleccionada"] = nueva


def seleccionar_modalidad_entrega(opcion):
    st.session_state["modalidad_envio_seleccionada"] = opcion
    st.session_state["_mod_entrega_pendiente"] = opcion


CAMPOS_FORM_DIRECCION = ("dir_etiqueta_in", "dir_receptor_in", "dir_tel_in", "dir_exacta_in")


def direcciones_sesion(casillero):
    bolsa = st.session_state.setdefault("direcciones_usuario", {})
    if casillero not in bolsa:
        try:
            filas = cargar_direcciones_db(casillero)
        except Exception:
            filas = []
        bolsa[casillero] = [
            {"id": d[0], "etiqueta": d[1], "receptor": d[2], "ciudad": d[3], "direccion": d[4]}
            for d in filas
        ]
    return bolsa[casillero]


def opciones_entrega_desde_sesion(casillero):
    opciones = [OPCION_PREDETERMINADA]
    for e in direcciones_sesion(casillero):
        opciones.append(f"📍 {e['etiqueta']} - {e['ciudad']}")
    opciones.append("➕ Crear Nueva Dirección de Envío")
    return opciones


def guardar_nueva_direccion(casillero):
    etiqueta = (st.session_state.get("dir_etiqueta_in") or "").strip()
    receptor = (st.session_state.get("dir_receptor_in") or "").strip()
    tel = (st.session_state.get("dir_tel_in") or "").strip()
    dep = (st.session_state.get("sb_dep_nueva_dir") or "").strip()
    ciu = (st.session_state.get("sb_ciu_nueva_dir") or "").strip()
    dir_exacta = (st.session_state.get("dir_exacta_in") or "").strip()
    if not (etiqueta and receptor and tel and dep and ciu and dir_exacta):
        st.session_state["_dir_form_error"] = "Completa todos los campos obligatorios (*)."
        return
    f_ahora = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
    id_dir_nuevo = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO direcciones_entrega (codigo_casillero, etiqueta, receptor_nombre, telefono, departamento, ciudad, direccion_exacta, fecha_creacion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (casillero, etiqueta, receptor, tel, dep, ciu, dir_exacta, f_ahora),
            )
            id_dir_nuevo = cur.lastrowid
            conn.commit()
        cargar_direcciones_db.clear()
    except Exception:
        id_dir_nuevo = None

    direcciones_sesion(casillero).append({
        "id": id_dir_nuevo,
        "etiqueta": etiqueta,
        "receptor": receptor,
        "telefono": tel,
        "departamento": dep,
        "ciudad": ciu,
        "direccion": dir_exacta,
    })
    seleccionar_modalidad_entrega(f"📍 {etiqueta} - {ciu}")
    st.session_state["_dir_form_exito"] = f"Dirección '{etiqueta}' guardada y seleccionada como destino."
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)
    st.toast(f"✅ Dirección '{etiqueta}' guardada y seleccionada.")


def eliminar_direccion_usuario(casillero, etiqueta, ciudad, id_dir=None):
    if id_dir:
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM direcciones_entrega WHERE id = ? AND codigo_casillero = ?", (id_dir, casillero))
                conn.commit()
            cargar_direcciones_db.clear()
        except Exception:
            pass
    lista = direcciones_sesion(casillero)
    lista[:] = [e for e in lista if not (e.get("etiqueta") == etiqueta and e.get("ciudad") == ciudad)]
    if st.session_state.get("modalidad_envio_seleccionada") == f"📍 {etiqueta} - {ciudad}":
        seleccionar_modalidad_entrega(OPCION_PREDETERMINADA)


def cancelar_nueva_direccion():
    seleccionar_modalidad_entrega(OPCION_PREDETERMINADA)
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)


def selector_modalidad_entrega(opciones_modalidad):
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
    if st.session_state.get("_mod_entrega_lista") and previa is not None and mod_elegida != previa:
        invalidar_emision_visible_cotizador()
    st.session_state["_mod_entrega_lista"] = True


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
    {"paso": 1, "titulo": "Acceso al Cotizador", "texto": "Pulse <b>Cotizador</b> en la barra inferior para calcular el flete de su carga."},
    {"paso": 2, "titulo": "Emisión de la tarifa", "texto": "Complete medidas y peso. Luego pulse <b>Confirmar Tarifa &amp; Emitir Documentos</b> para generar la tarifa."},
    {"paso": 3, "titulo": "Documento del proveedor", "texto": "Descarga este archivo y envíalo a tu proveedor en China para rotular el paquete."},
    {"paso": 4, "titulo": "Traslado al historial", "texto": "Pasa a tus cotizaciones para asegurar tu tarifa antes de 24 horas."},
    {"paso": 5, "titulo": "Consolidación de tarifa", "texto": "Pulse <b>Confirmar Cotización</b> en la tarifa resaltada para dejarla permanente en su casillero."},
    {"paso": 6, "titulo": "Gestión en Envíos", "texto": "¡Listo! Ahora puedes dar seguimiento a tu paquete y descargar tu ficha/etiqueta y comprobante de tarifa."},
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
    aplicar_perfil_en_sesion({
        **actual,
        "nombre": nombre,
        "dni": dni,
        "telefono": telefono,
        "departamento": departamento,
        "ciudad": ciudad,
        "direccion": direccion,
    })
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


# ---------------------------------------------------------
# 2. GENERADORES DE PDF NATIVOS CON HORA DE HONDURAS
# ---------------------------------------------------------
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
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS permisos_usuario (
                codigo_casillero TEXT PRIMARY KEY,
                hub_china INTEGER NOT NULL DEFAULT 1,
                hub_eeuu INTEGER NOT NULL DEFAULT 1,
                hub_honduras INTEGER NOT NULL DEFAULT 1,
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
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_libra', 3.50)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_m3', 680.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('minimo_cobro_usd', 10.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('divisor_peso_volumetrico', 390.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('umbral_minimo_lb', 3.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('umbral_paqueteria_lb', 99.00)")
        c.execute("INSERT OR IGNORE INTO config_sistema (clave, valor, descripcion) VALUES ('TASA_USD_HNL', '24.85', 'Tasa USD a lempira')")
        c.execute("INSERT OR IGNORE INTO config_sistema (clave, valor, descripcion) VALUES ('COMISION_CCM_PORCENTAJE', '0.10', 'Comisión CCM sobre FOB')")

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
        conn.commit()


init_db()


@st.cache_data(ttl=120, show_spinner=False)
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
        conn.commit()
    get_tarifa.clear()


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
    texto = str(valor or "").strip().upper()
    if texto.startswith(PREFIJO_CASILLERO):
        texto = texto[len(PREFIJO_CASILLERO):]
    digitos = "".join(filter(str.isdigit, texto))
    if len(digitos) >= 8:
        return digitos[:8]
    if digitos:
        return digitos.zfill(8)
    return ""


def generar_codigo_casillero_dni(dni_raw):
    nucleo = nucleo_casillero_desde_id(dni_raw)
    return f"{PREFIJO_CASILLERO}{nucleo}" if nucleo else ""


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
                cas, vals["hub_china"], vals["hub_eeuu"], vals["hub_honduras"],
                vals["mod_cotizador"], vals["mod_catalogo"], vals["mod_cotizaciones"],
                vals["mod_envios"], vals["mod_fichas"],
            ),
        )


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
            "hub_china", "hub_eeuu", "hub_honduras", "mod_cotizador",
            "mod_catalogo", "mod_cotizaciones", "mod_envios", "mod_fichas"
        )
        return dict(zip(claves, [int(v or 0) for v in row]))
    except Exception:
        return base


def usuario_puede_hub(hub_id, casillero=None):
    return True if PERMISOS_ABIERTOS_TEMPORAL else bool(permisos_de(casillero).get(f"hub_{hub_id}", 0))


def usuario_puede_modulo(mod_id, casillero=None):
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True
    mapping = {
        "Cotizador": "mod_cotizador",
        "Catálogo": "mod_catalogo",
        "Mis Cotizaciones": "mod_cotizaciones",
        "Mis Envíos": "mod_envios",
        "Etiqueta": "mod_fichas",
    }
    col = mapping.get(mod_id)
    return bool(permisos_de(casillero).get(col, 0)) if col else False


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
                int(datos.get("hub_china", 0)), int(datos.get("hub_eeuu", 0)), int(datos.get("hub_honduras", 0)),
                int(datos.get("mod_cotizador", 0)), int(datos.get("mod_catalogo", 0)), int(datos.get("mod_cotizaciones", 0)),
                int(datos.get("mod_envios", 0)), int(datos.get("mod_fichas", 0)),
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
            c.execute("UPDATE usuarios SET dni = ?, codigo_casillero = ? WHERE id = ?", (nuevo_dni, nuevo_cas, uid))

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
                    cas_root, NOMBRE_SUPERADMIN, DNI_SUPERADMIN, CORREO_SUPERADMIN,
                    "+504 9577-1099", "Intibucá", "San Juan", "Oficina Central CCM",
                    hash_root, obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
        conn.commit()


def migrar_prefijo_casillero():
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
        factor, step, min_v, formato, codigo = 1.0, 0.01, 0.01, "%.2f", "m"
        defaults_paq = {"alto": 0.30, "ancho": 0.30, "largo": 0.40}
        defaults_com = {"alto": 1.20, "ancho": 1.20, "largo": 1.20}
    elif "Pulgadas" in unidad_medida:
        factor, step, min_v, formato, codigo = 1.0 / 0.0254, 0.5, 0.5, "%.1f", "in"
        defaults_paq = {"alto": 12.0, "ancho": 12.0, "largo": 16.0}
        defaults_com = {"alto": 47.0, "ancho": 47.0, "largo": 47.0}
    else:
        factor, step, min_v, formato, codigo = 100.0, 1.0, 1.0, "%.0f", "cm"
        defaults_paq = {"alto": 30.0, "ancho": 30.0, "largo": 40.0}
        defaults_com = {"alto": 120.0, "ancho": 120.0, "largo": 120.0}

    maxes = {
        "alto": max_alineado(min_v, CONTENEDOR_40_ALTO_M * factor, step),
        "ancho": max_alineado(min_v, CONTENEDOR_40_ANCHO_M * factor, step),
        "largo": max_alineado(min_v, CONTENEDOR_40_LARGO_M * factor, step),
    }
    base = defaults_com if comercial else defaults_paq
    defaults = {k: min(v, maxes[k]) for k, v in base.items()}
    return {"min": min_v, "step": step, "formato": formato, "codigo": codigo, "defaults": defaults, "max": maxes}


def limites_peso(unidad_peso, paqueteria):
    if paqueteria:
        max_lb = float(get_tarifa("umbral_paqueteria_lb") or PESO_MAX_PAQUETERIA_LB)
        default_lb, min_lb, step_lb = 4.0, 0.5, 0.5
    else:
        max_lb = peso_max_contenedor_hn_lb()
        default_lb, min_lb, step_lb = 500.0, 1.0, 10.0

    if "Kilogramos" in unidad_peso:
        min_v = 0.1 if paqueteria else 1.0
        default = round(default_lb / LB_POR_KG, 1)
        max_v = round(max_lb / LB_POR_KG, 1)
        step = 0.1 if paqueteria else 1.0
        codigo, formato = "kg", "%.1f"
    else:
        min_v, default, max_v, step = min_lb, default_lb, max_lb, step_lb
        codigo, formato = "lb", "%.1f"

    max_v = max_alineado(min_v, max_v, step)
    default = min(default, max_v)
    return {"min": min_v, "default": default, "max": max_v, "step": step, "codigo": codigo, "formato": formato}


def campo_numerico(label, lim_min, valor, lim_max, paso, clave, formato, on_change=None):
    kwargs = {"on_change": on_change} if on_change is not None else {}
    if clave in st.session_state:
        try:
            actual = float(st.session_state[clave])
            limitado = min(max(actual, float(lim_min)), float(lim_max))
            if limitado != actual:
                st.session_state[clave] = limitado
        except (TypeError, ValueError):
            st.session_state[clave] = float(valor)
        return st.number_input(
            label, min_value=float(lim_min), max_value=float(lim_max),
            step=float(paso), format=formato, key=clave, **kwargs
        )
    return st.number_input(
        label, min_value=float(lim_min), max_value=float(lim_max),
        value=float(valor), step=float(paso), format=formato, key=clave, **kwargs
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


# ---------------------------------------------------------
# 5. GESTIÓN DE SESIÓN PERSISTENTE MEDIANTE QUERY_PARAMS
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.update({
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
    })


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
        "autenticado", "usuario", "rol", "casillero", "nombre", "dni", "telefono",
        "departamento", "ciudad", "direccion_exacta", "flash_perfil",
        "datos_pdf_confirmado", "ultima_cot_id", "cotizaciones", "_cot_emit_snapshot",
        "_seq_cot", "_ccm_rerun_app", "_ccm_scroll_emit", "_ccm_emit_error",
        "_mod_entrega_lista", "_mod_entrega_pendiente", "modalidad_envio_seleccionada",
        "sb_modalidad_entrega", "direcciones_usuario", "_dir_form_error", "_dir_form_exito",
        "_dir_form_reset", "dir_etiqueta_in", "dir_receptor_in", "dir_tel_in",
        "dir_exacta_in", "sub_tab_inicio", "vista_activa", "hub",
        "china_modulos_desbloqueados", "cotizacion_envio_foco", "cotizacion_historial_foco",
        "abrir_guia_rapida", "guia_china_auto_vista", "guia_activa", "guia_paso",
        "guia_omitida", "guia_completada", "mostrar_guia",
    ]:
        st.session_state.pop(k, None)
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------
# 6. PANTALLA DE ACCESO PÚBLICA (LOGIN / REGISTRO / RECUPERACIÓN)
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
                            url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote("Hola, necesito asistencia con mi casillero ya registrado.")
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
                                    n_cod, d["nom"], d["dni"], d["cor"], d["tel"],
                                    d["dep"], d["ciu"], d["dir"], rub, mod,
                                    hash_pwd(n_pwd), f_crea,
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
# 7. PORTAL DEL CLIENTE
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
    if st.session_state.get("sub_tab_inicio") in VISTAS_MODULO and not usuario_puede_modulo(st.session_state["sub_tab_inicio"]):
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
    nombre_completo = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "San Juan, Intibucá")

    partes_nombre = nombre_completo.strip().split()
    nombre_display = f"{partes_nombre[0]} {partes_nombre[1]}" if len(partes_nombre) >= 2 else (partes_nombre[0] if partes_nombre else "Cliente")

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
    detectar_avance_descarga_guia()
    aplicar_clase_guia_js()

    if st.session_state["sub_tab_inicio"] == "Inicio":
        hub_sel = st.session_state.get("hub")
        with st.container(key="vista_inicio"):
            if not hub_sel:
                st.markdown("#### 🏠 Inicio")
                st.caption("Seleccione el origen de su carga para ver los módulos disponibles.")
                for hub_id, hub in HUBS.items():
                    if not usuario_puede_hub(hub_id):
                        continue
                    st.button(
                        f"{hub['icon']}  {hub['label']}",
                        type="secondary",
                        key=f"hub_{hub_id}",
                        use_container_width=True,
                        on_click=ir_a,
                        args=("Inicio", hub_id),
                    )
            elif hub_sel == "china":
                pintar_coach_guia()
                hub_china = HUBS["china"]
                st.markdown(f"#### {hub_china['icon']} {hub_china['label']}")
                st.caption("Consolidación marítima China ➔ Honduras")
                pintar_banner_promocional_china(casillero)
            elif hub_sel in HUBS:
                hub_vacio = HUBS[hub_sel]
                st.markdown(f"#### {hub_vacio['icon']} {hub_vacio['label']}")
                st.markdown(
                    f'<div class="hub-empty-box">'
                    f'<div style="font-size:2rem;margin-bottom:8px;">{hub_vacio["icon"]}</div>'
                    f'<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">{hub_vacio["label"]}</div>'
                    f'<div style="font-size:0.86rem;font-weight:600;">{hub_vacio["descripcion"]}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

    if st.session_state["sub_tab_inicio"] == "Más":
        pintar_vista_mas()

    if st.session_state["sub_tab_inicio"] == "Consultas":
        cas_txt = formatear_casillero(casillero)
        msg_wa = urllib.parse.quote(f"Hola Centro de Cerámicas y Más, tengo una consulta de mi casillero {cas_txt}.")
        st.markdown("#### 🔍 Consultas")
        st.caption("Pregunte a CCM por WhatsApp o busque productos para importar.")
        st.link_button("💬 Preguntar por WhatsApp", f"https://wa.me/50495771099?text={msg_wa}", use_container_width=True)
        st.button("📖 Catálogo 1688", key="btn_consultas_1688", use_container_width=True, on_click=ir_a_catalogo)

    if st.session_state["sub_tab_inicio"] == "Configuración":
        st.markdown("#### ⚙️ Configuración")
        st.caption("Datos de su casillero. La sesión se conserva al cambiar de vista.")
        st.info(
            f"**Casillero:** `{casillero}`  \n"
            f"**Nombre:** {st.session_state.get('nombre') or '—'}  \n"
            f"**Correo:** {st.session_state.get('usuario') or '—'}  \n"
            f"**Entrega:** {st.session_state.get('modalidad_envio_seleccionada') or OPCION_PREDETERMINADA}"
        )

    if st.session_state["sub_tab_inicio"] == "Mis Cotizaciones":
        with st.container(key="vista_historial"):
            st.markdown('<div class="ccm-vista-historial" aria-hidden="true"></div>', unsafe_allow_html=True)
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📄 Historial de Cotizaciones y Descarga de PDF")
            st.caption(
                "Las tarifas no confirmadas caducan a las 24 horas (hora de Honduras) y se eliminan. "
                "Al confirmar, la cotización queda consolidada de forma permanente."
            )

            if lista_mis_cotizaciones:
                try:
                    foco_hist = int(st.session_state.get("cotizacion_historial_foco") or 0)
                except (TypeError, ValueError):
                    foco_hist = 0
                if foco_hist:
                    lista_mis_cotizaciones = sorted(
                        lista_mis_cotizaciones,
                        key=lambda r: (0 if int(r[0]) == foco_hist else 1, *clave_orden_cotizacion(r[7], r[0])),
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
                    insignia = '<span class="cotizacion-badge-pendiente">⚠️ Pendiente de Confirmar</span>' if not consolidada else ""

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
                            destino_entrega=st.session_state["modalidad_envio_seleccionada"],
                            fecha_emision=fec_c,
                        )
                        if consolidada:
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
                st.info("No hay cotizaciones vigentes ni consolidadas.")
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
                    st.markdown("<p style='font-weight:700; font-size:0.88rem; margin:10px 0 6px 0;'>Tus direcciones personalizadas:</p>", unsafe_allow_html=True)
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
                                seleccionar_modalidad_entrega(OPCION_PREDETERMINADA)
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
                st.text_input("Etiqueta de la dirección *", key="dir_etiqueta_in", placeholder="Ej: Mi Casa, Sucursal 2, Taller")
                st.text_input("Nombre de quien recibe *", key="dir_receptor_in")
                st.text_input("Teléfono de contacto *", key="dir_tel_in")
                dep_dir_in = st.selectbox("Departamento *", list(MUNICIPIOS_HONDURAS.keys()), index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0, key="sb_dep_nueva_dir")
                st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_dir_in], key="sb_ciu_nueva_dir")
                st.text_area("Dirección exacta y referencias *", key="dir_exacta_in", placeholder="Barrio, calle, número de casa, puntos clave...")
                error_dir = st.session_state.pop("_dir_form_error", None)
                if error_dir:
                    st.error(error_dir)
                st.button("💾 Guardar Dirección", type="primary", key="btn_guardar_nueva_dir", use_container_width=True, on_click=guardar_nueva_direccion, args=(casillero,))
                st.button("Cancelar", type="secondary", key="btn_cancelar_dir", use_container_width=True, on_click=cancelar_nueva_direccion)

    if st.session_state["sub_tab_inicio"] == "Catálogo":
        with st.container(key="vista_catalogo"):
            with st.container(key="catalogo_formulario"):
                st.markdown("#### 🛍️ Búsqueda en Fábricas de China (1688 Direct)")
                modo_busq = st.radio("Modalidad de búsqueda:", ["🔎 Por Nombre / Palabras", "📷 Por Foto / Imagen"], horizontal=True)

                resultados_1688 = []
                if modo_busq == "🔎 Por Nombre / Palabras":
                    kw = st.text_input("Producto a buscar:", placeholder="Ej: porcelanato 60x120, grifería, taladro...")
                    if st.button("Buscar Productos en China ➔", type="primary", key="btn_buscar_china", use_container_width=True) and kw:
                        with st.spinner("Consultando catálogo de 1688..."):
                            resultados_1688 = buscar_productos_1688_texto(kw)
                else:
                    img_up = st.file_uploader("Sube una foto del producto:", type=["jpg", "png", "jpeg", "webp"])
                    if img_up and st.button("Escanear Coincidencia Visual ➔", type="primary", key="btn_escanear_catalogo", use_container_width=True):
                        with st.spinner("Buscando por reconocimiento visual..."):
                            resultados_1688 = buscar_productos_1688_imagen(img_up.getvalue())

            if resultados_1688:
                st.markdown("---")
                for prod in resultados_1688:
                    calc = calcular_costo_puesto_honduras(prod["precio_fabrica_usd"], prod["peso_kg"], prod["volumen_m3"], prod["moq"])
                    c_img, c_det = st.columns([1, 1.8])
                    with c_img:
                        st.image(prod["imagen_url"], use_container_width=True)
                    with c_det:
                        st.markdown(f"**{prod['nombre']}**")
                        st.caption(f"🏭 {prod['proveedor']} | SKU: `{prod['sku']}`")
                        st.markdown(f"💰 **Fábrica:** ¥{prod['precio_fabrica_cny']:.2f} CNY (~${prod['precio_fabrica_usd']:.2f} USD) | **MOQ:** {prod['moq']} uds.")
                        st.success(f"🇭🇳 **Puesto en Honduras:** ${calc['total_estimado_usd']:.2f} USD (~L {calc['total_estimado_hnl']:.2f} HNL)\n\n*(Destino: {st.session_state['modalidad_envio_seleccionada']})*")
                        msg_cot = f"Hola Centro de Cerámicas y Más, me interesa importar este producto: {prod['nombre']} (SKU: {prod['sku']}) para mi casillero {casillero}. Cantidad: {prod['moq']} uds. Destino/Entrega: {st.session_state['modalidad_envio_seleccionada']}."
                        url_wa_p = "https://wa.me/50495771099?text=" + urllib.parse.quote(msg_cot)
                        c_b1, c_b2 = st.columns(2)
                        with c_b1:
                            st.markdown(f'<a href="{prod["url_proveedor"]}" target="_blank"><button style="background:white; border:1.5px solid #cbd5e1; border-radius:8px; width:100%; height:44px; font-weight:bold; cursor:pointer;">🔗 Ver en 1688</button></a>', unsafe_allow_html=True)
                        with c_b2:
                            st.markdown(f'<a href="{url_wa_p}" target="_blank"><button style="background:#22c55e; color:white; border:none; border-radius:8px; width:100%; height:44px; font-weight:bold; cursor:pointer;">📲 Cotizar WhatsApp</button></a>', unsafe_allow_html=True)
                    st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
            espaciador_barra_inferior("safe_catalogo")

    elif st.session_state["sub_tab_inicio"] == "Cotizador" and st.session_state["modalidad_envio_seleccionada"] != "➕ Crear Nueva Dirección de Envío":
        with st.container(key="vista_cotizador"):
            st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
            selector_modalidad_entrega(opciones_modalidad)
            st.markdown(
                f"""
                <div class="destino-seleccionado-card">
                    <div class="destino-seleccionado-kicker">📍 Destino de Entrega Seleccionado</div>
                    <div class="destino-seleccionado-dir">{st.session_state['modalidad_envio_seleccionada']}</div>
                    <div class="destino-seleccionado-nota">(Se imprimirá en todos los formatos y fichas de bodega)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            exito_dir = st.session_state.pop("_dir_form_exito", None)
            if exito_dir:
                st.success(f"✅ {exito_dir}")

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
                f"Tope de medidas: contenedor 40' High Cube ({CONTENEDOR_40_ALTO_M:.2f} m alto × {CONTENEDOR_40_ANCHO_M:.2f} m ancho × {CONTENEDOR_40_LARGO_M:.2f} m largo). "
                f"Peso máximo legal: {PESO_MAX_CONTENEDOR_HN_KG:,.0f} kg ({peso_max_contenedor_hn_lb():,.0f} lb)."
                + (f" En paquetería menor el peso no puede superar {umbral_paq:.0f} lb." if es_paqueteria else "")
            )

            pref = "menor" if es_paqueteria else "com"
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                al_input = campo_numerico(f"Alto ({etiqueta_medida})", dim["min"], dim["defaults"]["alto"], dim["max"]["alto"], dim["step"], f"in_al_{pref}_{dim['codigo']}", dim["formato"], on_change=invalidar_emision_visible_cotizador)
            with c2:
                an_input = campo_numerico(f"Ancho ({etiqueta_medida})", dim["min"], dim["defaults"]["ancho"], dim["max"]["ancho"], dim["step"], f"in_an_{pref}_{dim['codigo']}", dim["formato"], on_change=invalidar_emision_visible_cotizador)
            with c3:
                la_input = campo_numerico(f"Largo ({etiqueta_medida})", dim["min"], dim["defaults"]["largo"], dim["max"]["largo"], dim["step"], f"in_la_{pref}_{dim['codigo']}", dim["formato"], on_change=invalidar_emision_visible_cotizador)
            with c4:
                pe_input = campo_numerico(f"Peso ({etiqueta_peso})", pes["min"], pes["default"], pes["max"], pes["step"], f"in_pe_{pref}_{pes['codigo']}", pes["formato"], on_change=invalidar_emision_visible_cotizador)

            if "Pulgadas" in unidad_medida:
                al_val, an_val, la_val = al_input * 2.54, an_input * 2.54, la_input * 2.54
            elif "Metros" in unidad_medida:
                al_val, an_val, la_val = al_input * 100.0, an_input * 100.0, la_input * 100.0
            else:
                al_val, an_val, la_val = al_input, an_input, la_input

            if "Kilogramos" in unidad_peso:
                pe_lb, pe_kg = pe_input * 2.20462, pe_input
            else:
                pe_lb, pe_kg = pe_input, pe_input / 2.20462

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147

            if es_paqueteria:
                if pe_lb <= umbral_min:
                    tot = min_usd
                    desc = f"Tarifa Mínima Base (1 a {umbral_min:.0f} lbs): ${min_usd:.2f} USD"
                else:
                    tot = pe_lb * t_lb
                    desc = f"Tarifa por Libra: {pe_lb:.1f} lbs x ${t_lb:.2f}/lb"
                modalidad_pdf = f"Paquetería Menor (1 a {umbral_paq:.0f} lbs)"
            else:
                vol_m3_peso = pe_kg / divisor_vol
                cbm_facturable = max(vol_m3_val, vol_m3_peso)
                tot = cbm_facturable * t_m3
                desc = f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"
                modalidad_pdf = "Carga Comercial por Metro Cúbico (CBM)"

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Volumen (m³)", f"{vol_m3_val:.4f} m³")
            with m2:
                st.metric("Pies Cúbicos", f"{vol_ft3_val:.2f} ft³")
            with m3:
                st.metric("Total Estimado", f"${tot:.2f} USD")

            firma_actual = firma_parametros_cotizador(
                al_val, an_val, la_val, pe_lb,
                st.session_state["modalidad_envio_seleccionada"], modalidad_pdf
            )
            sincronizar_emision_con_formulario(firma_actual)
            st.session_state["_cot_emit_snapshot"] = {
                "al": al_val, "an": an_val, "la": la_val, "peso_lb": pe_lb, "peso_kg": pe_kg,
                "vol_m3": vol_m3_val, "vol_ft3": vol_ft3_val, "total_usd": tot,
                "tipo_carga": modalidad_pdf, "detalle_tarifa": desc,
                "destino": st.session_state["modalidad_envio_seleccionada"],
                "firma_params": list(firma_actual),
            }
            with st.container(key="guia_foco_tarifa"):
                st.button(
                    "🤝 Confirmar Tarifa & Emitir Documentos",
                    type="primary",
                    key="btn_confirmar_tarifa",
                    use_container_width=True,
                    on_click=emitir_tarifa_desde_snapshot,
                )

            if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
                d_pdf = st.session_state["datos_pdf_confirmado"]
                try:
                    id_c = int(d_pdf.get("id_cot") or 0)
                except (TypeError, ValueError):
                    id_c = 0
                if id_c:
                    tarifa_consolidada = cotizacion_esta_confirmada(id_c, casillero)
                    dest_pdf = d_pdf.get("destino_entrega", st.session_state["modalidad_envio_seleccionada"])
                    fecha_doc = d_pdf.get("fecha_hora_doc", obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"))
                    pdf_fab = generar_pdf_etiqueta_proveedor(
                        casillero=casillero, nombre=nombre_completo, telefono=tel_cli, ciudad=ciu_cli,
                        al=d_pdf.get("al", 0), an=d_pdf.get("an", 0), la=d_pdf.get("la", 0),
                        pe_lb=d_pdf.get("peso_lb", 0), pe_kg=d_pdf.get("peso_kg", 0),
                        vol_m3=d_pdf.get("vol_m3", 0), destino_entrega=dest_pdf, fecha_emision=fecha_doc,
                    )
                    with st.container(key="acciones_emit_cotizador"):
                        st.download_button(
                            "🏷️ Descargar Formato / Documento para el Fabricante",
                            pdf_fab,
                            f"Shipping_Label_Fabricante_{casillero}.pdf",
                            "application/pdf",
                            key=f"dl_pdf_fab_{id_c}",
                            use_container_width=True,
                        )
                        st.button(
                            "📋 Ir a Mis Cotizaciones",
                            type="primary",
                            key=f"btn_ver_mis_cotizaciones_{id_c}",
                            use_container_width=True,
                            on_click=ir_a_historial_guia,
                            args=(id_c,),
                        )
            espaciador_barra_inferior("safe_cotizador_fin")

    elif st.session_state["sub_tab_inicio"] == "Mis Envíos":
        with st.container(key="vista_envios"):
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📦 Mis Paquetes en Tránsito")
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?", (casillero,))
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
            cotizaciones_despacho = ordenar_cotizaciones_desc([row for row in lista_mis_cotizaciones if es_cotizacion_confirmada(row[8])])
            if cotizaciones_despacho:
                for cot_env in cotizaciones_despacho:
                    id_e, al_e, an_e, la_e, pe_e, vol_e, tot_e, fec_e, conf_e = cot_env
                    st.markdown(
                        f"""
                        <div style="background:#f8fafc; border:1.5px solid #e2e8f0; border-radius:10px; padding:10px 14px; margin-bottom:8px; font-size:0.85rem;">
                            <b>🔖 CCM-COT-{id_e:05d}</b> &bull; Fecha: {formatear_fecha_pantalla(fec_e)}<br>
                            <small style="color:#475569;">📐 Medidas: {al_e:.1f}x{an_e:.1f}x{la_e:.1f} cm | Peso: {pe_e:.1f} lbs | 💰 Total: <b>${tot_e:.2f} USD</b></small><br>
                            <small style="color:#1d4ed8; font-weight:700;">✅ Consolidada — permanente en el historial del casillero</small>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    pdf_ficha_env = generar_pdf_etiqueta_proveedor(
                        casillero=casillero, nombre=nombre_completo, telefono=tel_cli, ciudad=ciu_cli,
                        al=al_e, an=an_e, la=la_e, pe_lb=pe_e, pe_kg=pe_e / 2.20462, vol_m3=vol_e,
                        destino_entrega=st.session_state["modalidad_envio_seleccionada"], fecha_emision=fec_e,
                    )
                    pdf_tarifa_env = generar_pdf_confirmacion_cotizacion(
                        casillero=casillero, nombre=nombre_completo, telefono=tel_cli, ciudad=ciu_cli,
                        tipo_carga="Cotización Confirmada", al=al_e, an=an_e, la=la_e, peso_lb=pe_e,
                        peso_kg=pe_e / 2.20462, vol_m3=vol_e, vol_ft3=vol_e * 35.3147, total_usd=tot_e,
                        detalle_tarifa="Tarifa Calculada Sistema CCM", id_cot=id_e,
                        destino_entrega=st.session_state["modalidad_envio_seleccionada"], fecha_emision=fec_e,
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
            espaciador_barra_inferior("safe_envios")

    elif st.session_state["sub_tab_inicio"] == "Etiqueta":
        with st.container(key="vista_fichas"):
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📋 Fichas")
            cotizaciones_ficha = ordenar_cotizaciones_desc([row for row in lista_mis_cotizaciones if es_cotizacion_confirmada(row[8])])
            if cotizaciones_ficha:
                for cot_f in cotizaciones_ficha:
                    id_f, al_f, an_f, la_f, pe_f, vol_f, tot_f, fec_f, _ = cot_f
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
                        casillero=casillero, nombre=nombre_completo, telefono=tel_cli, ciudad=ciu_cli,
                        al=al_f, an=an_f, la=la_f, pe_lb=pe_f, pe_kg=pe_f / 2.20462, vol_m3=vol_f,
                        destino_entrega=st.session_state["modalidad_envio_seleccionada"], fecha_emision=fec_f,
                    )
                    st.download_button(
                        f"🏷️ Descargar Ficha CCM-COT-{id_f:05d}",
                        pdf_ficha_mod,
                        f"Ficha_Bodega_{casillero}_COT{id_f:05d}.pdf",
                        "application/pdf",
                        key=f"dl_ficha_mod_{id_f}",
                        use_container_width=True,
                    )
            espaciador_barra_inferior("safe_fichas")

    pintar_barra_inferior(total_cotizaciones)

# ---------------------------------------------------------
# 8. PANEL ADMINISTRATIVO / SUPERADMINISTRADOR
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

    tab_u, tab_p, tab_t, tab_s = st.tabs(["👥 Usuarios y permisos", "📦 Paquetes", "⚙️ Tarifas y fórmulas", "🗄️ Sistema"])

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
            n_dep = st.selectbox("Departamento", list(MUNICIPIOS_HONDURAS.keys()), index=list(MUNICIPIOS_HONDURAS.keys()).index(dep_u) if dep_u in MUNICIPIOS_HONDURAS else 0, key=f"adm_dep_{cas_u}")
            munis = MUNICIPIOS_HONDURAS[n_dep]
            n_ciu = st.selectbox("Ciudad", munis, index=munis.index(ciu_u) if ciu_u in munis else 0, key=f"adm_ciu_{cas_u}")
            n_dir = st.text_area("Dirección", value=dir_u or "", key=f"adm_dir_{cas_u}")
            n_cas = st.text_input("Casillero", value=formatear_casillero(cas_u), key=f"adm_cas_{cas_u}")
            n_act = st.checkbox("Cuenta activa", value=bool(act_u), key=f"adm_act_{cas_u}")
            roles_disp = ["cliente", "admin", "superadmin"] if root else ["cliente", "admin"]
            n_rol = st.selectbox("Rol", roles_disp, index=roles_disp.index(rol_u) if rol_u in roles_disp else 0, key=f"adm_rol_{cas_u}", disabled=(rol_u == "superadmin" and not root))

            if st.button("Guardar perfil y permisos", type="primary", key="adm_save_user"):
                nuevo_cas = formatear_casillero(n_cas) or generar_codigo_casillero_dni(n_dni)
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
                guardar_permisos(nuevo_cas, perm)
                st.success("Cambios guardados.")
                st.rerun()

    with tab_p:
        t_in = st.text_input("Tracking de China")
        c_in = st.text_input("Casillero asignado", placeholder="Ej: CCM-15011985 o DNI del cliente")
        d_in = st.text_input("Descripción de la carga", placeholder="Ej: 4 cajas de porcelanato 60x120")
        cont_in = st.text_input("ID de contenedor", placeholder="Ej: CCM-CNT-014")
        e_in = st.selectbox("Estado", ["En Bodega China", "En Travesía Marítima", "En Desaduanaje", "Disponible en Bodega Central", "Entregado"])
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
            conteos = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tablas}
        st.dataframe({"Tabla": list(conteos.keys()), "Registros": list(conteos.values())}, use_container_width=True, hide_index=True)

    if st.button("Cerrar sesión", type="secondary", key="btn_logout_admin"):
        logout()

else:
    st.error("Rol no reconocido. Inicie sesión de nuevo.")
    if st.button("Volver al login"):
        logout()
