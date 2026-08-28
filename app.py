"""Centro de Cerámicas y Más — casillero marítimo China → Honduras.

App Streamlit autocontenida: flete, PDF, SQLite y AliExpress van en este archivo
para que Cloud funcione solo con `app.py` + `requirements.txt`.
"""

from __future__ import annotations

import html
import random
import string
import sys
import textwrap
import urllib.parse
from pathlib import Path

# Streamlit Cloud ejecuta app.py con un cwd que a veces no está en sys.path.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import streamlit.components.v1 as components

# --- lógica de casillero, flete, PDF y AliExpress (incluida para Streamlit Cloud) ---
# === config.py ===
"""Constantes de producto: hubs, municipios y cuentas semilla."""

OPCION_PREDETERMINADA = "🏬 Retirar en Almacén Principal (San Juan, Intibucá)"
PREFIJO_CASILLERO = "CCM-"
DB_NAME = "ccm_maritime_enterprise.db"

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

ALIAS_VISTA = {
    "Fichas": "Etiqueta",
    "Mis cotizaciones": "Mis Cotizaciones",
    "Mis envíos": "Mis Envíos",
    "Envíos": "Mis Envíos",
}

CAMPOS_FORM_DIRECCION = ("dir_etiqueta_in", "dir_receptor_in", "dir_tel_in", "dir_exacta_in")
CLAVES_WIDGET_PERFIL = (
    "perfil_nom",
    "perfil_tel",
    "perfil_dep",
    "perfil_ciu",
    "perfil_dir",
    "perfil_dni",
)

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
    "Yoro": ["Yoro", "Arenal", "El Negrito", "El Progreso", "Jocón", "Morazán", "Olanchito", "Santa Rita", "Sulaco", "Victoria", "Yorito"],
}

# === timeutil.py ===
"""Zona horaria de Honduras y vigencia de cotizaciones (24 h)."""

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    ZONA_HONDURAS = ZoneInfo("America/Tegucigalpa")
except Exception:
    ZONA_HONDURAS = timezone(timedelta(hours=-6), name="America/Tegucigalpa")

VIGENCIA_COTIZACION_HORAS = 24
VIGENCIA_COTIZACION = timedelta(hours=VIGENCIA_COTIZACION_HORAS)
FORMATOS_FECHA_COTIZACION = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
)

DIAS_SEMANA_ES = {
    0: "Lunes",
    1: "Martes",
    2: "Miércoles",
    3: "Jueves",
    4: "Viernes",
    5: "Sábado",
    6: "Domingo",
}
MESES_ES = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}


def obtener_tiempo_honduras() -> datetime:
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


def formatear_fecha_pantalla(fecha_raw) -> str:
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
    return sorted(filas, key=lambda r: clave_orden_cotizacion(r[idx_fecha], r[idx_id]))


def cotizacion_vigente(fecha_raw, ahora=None) -> bool:
    dt = parsear_fecha_cotizacion(fecha_raw)
    if dt is None:
        return False
    ahora = ahora or obtener_tiempo_honduras()
    edad = ahora - dt
    return timedelta(0) <= edad <= VIGENCIA_COTIZACION


def texto_vigencia_cotizacion(fecha_raw, ahora=None) -> str:
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


def es_cotizacion_confirmada(valor) -> bool:
    try:
        return int(valor or 0) == 1
    except (TypeError, ValueError):
        return False


def cotizacion_visible_historial(fecha_raw, confirmada, ahora=None) -> bool:
    if es_cotizacion_confirmada(confirmada):
        return True
    return cotizacion_vigente(fecha_raw, ahora)


def texto_estado_cotizacion(fecha_raw, confirmada, ahora=None) -> str:
    if es_cotizacion_confirmada(confirmada):
        return "Consolidada — permanente en el historial del casillero"
    return texto_vigencia_cotizacion(fecha_raw, ahora)


def proximo_cierre_contenedor(ahora=None) -> str:
    ahora = ahora or obtener_tiempo_honduras()
    dias = (4 - ahora.weekday()) % 7
    if dias == 0 and ahora.hour >= 17:
        dias = 7
    cierre = ahora + timedelta(days=dias)
    dia = DIAS_SEMANA_ES.get(cierre.weekday(), "")
    mes = MESES_ES.get(cierre.month, "")
    return f"{dia} {cierre.day} {mes} {cierre.year}"

# === quoting.py ===
"""Cálculo unificado de medidas, volumen y flete marítimo.

Toda conversión de unidades y toda tarifa (cotizador y catálogo) pasa por aquí.
No hay una segunda implementación de m³, lb/kg ni CBM facturable.
"""

from dataclasses import dataclass
from typing import Mapping

LB_POR_KG = 2.20462
FT3_POR_M3 = 35.3147
CM3_POR_M3 = 1_000_000.0
PULGADA_CM = 2.54

CONTENEDOR_40_ALTO_M = 2.69
CONTENEDOR_40_ANCHO_M = 2.35
CONTENEDOR_40_LARGO_M = 12.03
PESO_MAX_CONTENEDOR_HN_KG = 25_000.0
PESO_MAX_PAQUETERIA_LB = 99.0

TARIFA_DEFAULTS = {
    "tarifa_libra": 3.50,
    "tarifa_m3": 680.00,
    "minimo_cobro_usd": 10.00,
    "divisor_peso_volumetrico": 390.00,
    "umbral_minimo_lb": 3.00,
    "umbral_paqueteria_lb": PESO_MAX_PAQUETERIA_LB,
}


def peso_max_contenedor_hn_lb() -> float:
    return round(PESO_MAX_CONTENEDOR_HN_KG * LB_POR_KG, 2)


def max_alineado(min_v: float, max_v: float, step: float) -> float:
    n = int((max_v - min_v) / step + 1e-9)
    return round(min_v + n * step, 4)


def a_cm(valor: float, unidad_medida: str) -> float:
    """Convierte alto/ancho/largo a centímetros (unidad interna)."""
    if "Pulgadas" in unidad_medida:
        return float(valor) * PULGADA_CM
    if "Metros" in unidad_medida:
        return float(valor) * 100.0
    return float(valor)


def a_lb_kg(valor: float, unidad_peso: str) -> tuple[float, float]:
    """Devuelve (libras, kilogramos) a partir de la unidad de captura."""
    v = float(valor)
    if "Kilogramos" in unidad_peso:
        return v * LB_POR_KG, v
    return v, v / LB_POR_KG


def lb_a_kg(peso_lb: float) -> float:
    return float(peso_lb) / LB_POR_KG


def kg_a_lb(peso_kg: float) -> float:
    return float(peso_kg) * LB_POR_KG


def volumen_m3(alto_cm: float, ancho_cm: float, largo_cm: float) -> float:
    return (float(alto_cm) * float(ancho_cm) * float(largo_cm)) / CM3_POR_M3


def volumen_ft3(vol_m3: float) -> float:
    return float(vol_m3) * FT3_POR_M3


def limites_dimensiones(unidad_medida: str, comercial: bool = False) -> dict:
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


def limites_peso(unidad_peso: str, paqueteria: bool, umbral_paqueteria_lb: float | None = None) -> dict:
    if paqueteria:
        max_lb = float(umbral_paqueteria_lb or PESO_MAX_PAQUETERIA_LB)
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


def _tarifa(tarifas: Mapping[str, float], clave: str) -> float:
    if clave in tarifas and tarifas[clave] not in (None, ""):
        return float(tarifas[clave])
    return float(TARIFA_DEFAULTS[clave])


@dataclass(frozen=True)
class ResultadoFlete:
    flete_usd: float
    detalle: str
    modalidad: str
    cbm_facturable: float
    paqueteria: bool


def calcular_flete_maritimo(
    peso_lb: float,
    peso_kg: float,
    vol_m3: float,
    tarifas: Mapping[str, float],
    forzar_paqueteria: bool | None = None,
) -> ResultadoFlete:
    """Única fórmula de flete: mínimo / libra / CBM.

    Si `forzar_paqueteria` es True/False respeta la modalidad del cotizador.
    Si es None (catálogo), el umbral de libras decide la rama.
    """
    t_lb = _tarifa(tarifas, "tarifa_libra")
    t_m3 = _tarifa(tarifas, "tarifa_m3")
    min_usd = _tarifa(tarifas, "minimo_cobro_usd")
    umbral_min = _tarifa(tarifas, "umbral_minimo_lb")
    umbral_paq = _tarifa(tarifas, "umbral_paqueteria_lb")
    divisor = _tarifa(tarifas, "divisor_peso_volumetrico")

    if forzar_paqueteria is None:
        paqueteria = float(peso_lb) <= umbral_paq
    else:
        paqueteria = bool(forzar_paqueteria)

    if paqueteria:
        if peso_lb <= umbral_min:
            flete = min_usd
            detalle = f"Tarifa Mínima Base (1 a {umbral_min:.0f} lbs): ${min_usd:.2f} USD"
        else:
            flete = peso_lb * t_lb
            detalle = f"Tarifa por Libra: {peso_lb:.1f} lbs x ${t_lb:.2f}/lb"
        modalidad = f"Paquetería Menor (1 a {umbral_paq:.0f} lbs)"
        cbm = float(vol_m3)
    else:
        vol_peso = float(peso_kg) / divisor
        cbm = max(float(vol_m3), vol_peso)
        flete = cbm * t_m3
        detalle = f"{cbm:.4f} CBM @ ${t_m3:.2f}/m3"
        modalidad = "Carga Comercial por Metro Cúbico (CBM)"

    return ResultadoFlete(
        flete_usd=float(flete),
        detalle=detalle,
        modalidad=modalidad,
        cbm_facturable=float(cbm),
        paqueteria=paqueteria,
    )


def calcular_costo_puesto_honduras(
    precio_fabrica_usd: float,
    peso_kg: float,
    vol_m3: float,
    cantidad: int,
    tarifas: Mapping[str, float],
    tasa_hnl: float,
    comision_pct: float,
) -> dict:
    fob_total_usd = float(precio_fabrica_usd) * int(cantidad)
    peso_total_kg = float(peso_kg) * int(cantidad)
    peso_total_lb = kg_a_lb(peso_total_kg)
    vol_total_m3 = float(vol_m3) * int(cantidad)
    flete = calcular_flete_maritimo(peso_total_lb, peso_total_kg, vol_total_m3, tarifas)
    comision_usd = fob_total_usd * float(comision_pct)
    total_cif_usd = fob_total_usd + flete.flete_usd + comision_usd
    return {
        "fob_total_usd": fob_total_usd,
        "peso_total_lb": peso_total_lb,
        "flete_maritimo_usd": flete.flete_usd,
        "comision_usd": comision_usd,
        "total_estimado_usd": total_cif_usd,
        "total_estimado_hnl": total_cif_usd * float(tasa_hnl),
        "detalle_tarifa": flete.detalle,
        "modalidad": flete.modalidad,
    }

# === documents.py ===
"""Generación nativa de PDF (etiqueta fabricante y comprobante de tarifa)."""

import io
from functools import lru_cache
from pathlib import Path


RUTAS_LOGO = (
    Path(__file__).resolve().parent / "assets" / "logo_ccm_print.jpg",
    Path(__file__).resolve().parent / "assets" / "logo_ccm.png",
    Path(__file__).resolve().parent / "logo_ccm_print.jpg",
)


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


def compilar_pdf_simple(stream_content: str) -> bytes:
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

# === catalog.py ===
"""Catálogo 1688 (búsqueda simulada por texto e imagen)."""

def buscar_productos_1688_texto(keyword: str) -> list[dict]:
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


def buscar_productos_1688_imagen(image_bytes) -> list[dict]:
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

# === db.py ===
"""SQLite, casilleros, permisos y tarifas."""

import hashlib
import sqlite3



def hash_pwd(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)


def nucleo_casillero_desde_id(valor) -> str:
    texto = str(valor or "").strip().upper()
    if texto.startswith(PREFIJO_CASILLERO):
        texto = texto[len(PREFIJO_CASILLERO) :]
    digitos = "".join(filter(str.isdigit, texto))
    if len(digitos) >= 8:
        return digitos[:8]
    if digitos:
        return digitos.zfill(8)
    return ""


def generar_codigo_casillero_dni(dni_raw) -> str:
    nucleo = nucleo_casillero_desde_id(dni_raw)
    if not nucleo:
        return ""
    return f"{PREFIJO_CASILLERO}{nucleo}"


def formatear_casillero(codigo) -> str:
    return generar_codigo_casillero_dni(codigo) or str(codigo or "").strip()


def codigo_casillero_desde_usuario(codigo, dni) -> str:
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


def es_rol_admin(rol=None) -> bool:
    return rol in ROLES_ADMIN


def es_superadmin(rol=None) -> bool:
    return rol == "superadmin"


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


def get_tarifa(clave: str) -> float:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT valor FROM config_maritima WHERE clave = ?", (clave,))
        res = c.fetchone()
        return res[0] if res else 0.0


def set_tarifa(clave: str, valor: float):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO config_maritima (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, valor),
        )
        conn.commit()


def mapa_tarifas() -> dict:
    claves = (
        "tarifa_libra",
        "tarifa_m3",
        "minimo_cobro_usd",
        "divisor_peso_volumetrico",
        "umbral_minimo_lb",
        "umbral_paqueteria_lb",
    )
    return {k: float(get_tarifa(k) or 0) for k in claves}


def get_config_sistema(clave: str, valor_default=""):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT valor FROM config_sistema WHERE clave = ?", (clave,))
            row = c.fetchone()
            return row[0] if row else valor_default
    except Exception:
        return valor_default


def set_config_sistema(clave: str, valor, descripcion=""):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO config_sistema (clave, valor, descripcion) VALUES (?, ?, ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor,
                descripcion = COALESCE(excluded.descripcion, config_sistema.descripcion)
            """,
            (clave, str(valor), descripcion),
        )


def leer_config_moneda(clave: str, valor_default):
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
        import streamlit as st

        seccion = st.secrets.get("moneda", {})
        if clave in seccion:
            return seccion[clave]
        return seccion.get(clave, valor_default)
    except Exception:
        return valor_default


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


def permisos_de(casillero, rol="cliente"):
    cas = formatear_casillero(casillero or "")
    base = permisos_default(rol)
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
            asegurar_permisos_casillero(cas, rol)
            return permisos_default(rol)
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


def usuario_puede_hub(hub_id, casillero=None, rol="cliente"):
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True

    col = HUB_PERMISO_COL.get(hub_id)
    if not col:
        return False
    return bool(permisos_de(casillero, rol).get(col, 0))


def usuario_puede_modulo(mod_id, casillero=None, rol="cliente"):
    if PERMISOS_ABIERTOS_TEMPORAL:
        return True

    col = MODULO_PERMISO_COL.get(mod_id)
    if not col:
        return False
    return bool(permisos_de(casillero, rol).get(col, 0))


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


def bootstrap():
    init_db()
    migrar_prefijo_casillero()
    asegurar_superadmin()
    abrir_permisos_todos_los_usuarios()
    restaurar_datos_operativos_cliente()
    purgar_cotizaciones_no_confirmadas_vencidas()

# === aliexpress.py ===
"""Cliente AliExpress Open Platform (búsqueda por texto e imagen)."""

import base64
import hashlib
import hmac
import io
import os
from datetime import datetime, timedelta, timezone

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
        import requests
    except ImportError as exc:
        raise AliExpressError(
            "Falta la librería `requests`. Agréguela a requirements.txt y reinicie la app.",
            codigo="no_requests",
        ) from exc
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
        claves
        & {
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
                return _resultado(
                    demo,
                    error=exc2.mensaje,
                    aviso="Se muestran resultados de demostración.",
                    fuente="demo",
                    metodo="aliexpress.affiliate.product.query",
                )
        else:
            return _resultado(
                demo,
                error=exc.mensaje,
                aviso="Se muestran resultados de demostración.",
                fuente="demo",
                metodo="aliexpress.affiliate.product.query",
            )

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
            payload = _llamar_con_reintentos("aliexpress.ds.image.search", biz, image_bytes=archivo)
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


def ejecutar_busqueda(modo, keyword, imagen_bytes, min_usd, max_usd, orden):
    if modo == "imagen":
        return buscar_aliexpress_imagen(imagen_bytes, min_usd=min_usd, max_usd=max_usd, orden=orden)
    return buscar_aliexpress_texto(keyword, min_usd=min_usd, max_usd=max_usd, orden=orden)

st.set_page_config(
    page_title="Centro de Cerámicas y Más — Casillero & Catálogo China",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

bootstrap()


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


CSS_APP = r"""@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@700&display=swap');

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
.st-key-safe_envios,
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
.stMainBlockContainer:has(.st-key-vista_envios),
.block-container:has(.st-key-vista_fichas),
[data-testid="stMainBlockContainer"]:has(.st-key-vista_fichas),
.stMainBlockContainer:has(.st-key-vista_fichas) {
    padding-bottom: calc(200px + env(safe-area-inset-bottom, 0px)) !important;
}
.st-key-vista_envios,
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
[data-testid="stElementContainer"]:has(> .st-key-safe_fichas),
[data-testid="stLayoutWrapper"]:has(> .st-key-safe_envios),
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
.st-key-btn_buscar_china,
.st-key-btn_escanear_catalogo {
    width: 100% !important;
    max-width: 100% !important;
    display: block !important;
    scroll-margin-bottom: calc(var(--ccm-nav-clearance) + 16px) !important;
    margin-bottom: 0 !important;
    margin-left: 0 !important;
    margin-right: 0 !important;
}
.st-key-guia_foco_tarifa div.stButton,
.st-key-btn_confirmar_tarifa div.stButton,
.st-key-btn_buscar_china div.stButton,
.st-key-btn_escanear_catalogo div.stButton {
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
.st-key-btn_buscar_china div.stButton > button,
.st-key-btn_escanear_catalogo div.stButton > button,
.st-key-btn_buscar_china [data-testid^="stBaseButton"],
.st-key-btn_escanear_catalogo [data-testid^="stBaseButton"] {
    width: 100% !important;
    max-width: 100% !important;
    min-height: 48px !important;
    height: 48px !important;
    max-height: none !important;
    box-sizing: border-box !important;
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
        linear-gradient(135deg, rgba(11, 58, 145, 0.92) 0%, rgba(0, 74, 193, 0.88) 52%, rgba(29, 78, 216, 0.9) 100%),
        radial-gradient(circle at 88% 12%, rgba(255, 255, 255, 0.22) 0%, transparent 42%);
    border-radius: 16px;
    padding: 20px 18px 18px 18px;
    color: #ffffff;
    margin: 14px 0 18px 0;
    box-shadow: 0 14px 32px rgba(0, 74, 193, 0.28);
    box-sizing: border-box;
    scroll-margin-top: calc(var(--header-offset, 208px) + 12px);
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
[class*="st-key-docs_env_"],
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
"""


def inyectar_estilos():
    st.markdown(f"<style>\n{CSS_APP}\n</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session defaults
# ---------------------------------------------------------------------------
for _clave, _valor in (
    ("vista_actual", "login"),
    ("sub_tab_inicio", "Inicio"),
    ("hub", None),
    ("mostrar_guia", False),
    ("modalidad_envio_seleccionada", OPCION_PREDETERMINADA),
):
    st.session_state.setdefault(_clave, _valor)

st.session_state.setdefault("vista_activa", st.session_state.get("sub_tab_inicio", "Inicio"))

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


def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return "".join(random.choice(caracteres) for _ in range(8))


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


# ---------------------------------------------------------------------------
# Cotizaciones en sesión + SQLite
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Direcciones de envío (memoria de sesión + SQLite)
# ---------------------------------------------------------------------------
def al_cambiar_modalidad_entrega():
    invalidar_emision_visible_cotizador()
    nueva = st.session_state.get("sb_modalidad_entrega")
    if nueva:
        st.session_state["modalidad_envio_seleccionada"] = nueva


def seleccionar_modalidad_entrega(opcion):
    st.session_state["modalidad_envio_seleccionada"] = opcion
    st.session_state["_mod_entrega_pendiente"] = opcion


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
    direcciones_sesion(casillero).append(
        {
            "id": id_dir_nuevo,
            "etiqueta": etiqueta,
            "receptor": receptor,
            "telefono": tel,
            "departamento": dep,
            "ciudad": ciu,
            "direccion": dir_exacta,
        }
    )
    seleccionar_modalidad_entrega(f"📍 {etiqueta} - {ciu}")
    st.session_state["destino_entrega_activo"] = f"📍 {etiqueta} - {ciu}"
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
                cur.execute(
                    "DELETE FROM direcciones_entrega WHERE id = ? AND codigo_casillero = ?",
                    (id_dir, casillero),
                )
                conn.commit()
            cargar_direcciones_db.clear()
        except Exception:
            pass
    lista = direcciones_sesion(casillero)
    lista[:] = [e for e in lista if not (e.get("etiqueta") == etiqueta and e.get("ciudad") == ciudad)]
    opcion_borrada = f"📍 {etiqueta} - {ciudad}"
    if st.session_state.get("destino_entrega_activo") == opcion_borrada:
        st.session_state["destino_entrega_activo"] = OPCION_PREDETERMINADA
    if st.session_state.get("modalidad_envio_seleccionada") == opcion_borrada:
        seleccionar_modalidad_entrega(OPCION_PREDETERMINADA)


def cancelar_nueva_direccion():
    seleccionar_modalidad_entrega(st.session_state.get("destino_entrega_activo") or OPCION_PREDETERMINADA)
    st.session_state["_dir_form_reset"] = True
    st.session_state.pop("_dir_form_error", None)
    st.session_state.pop("datos_pdf_confirmado", None)


def destino_para_documentos():
    """Destino imprimible en PDFs y fichas: nunca la opción «Crear Nueva Dirección»."""
    mod = st.session_state.get("modalidad_envio_seleccionada")
    if mod and mod != "➕ Crear Nueva Dirección de Envío":
        return mod
    return st.session_state.get("destino_entrega_activo") or OPCION_PREDETERMINADA


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
    if mod_elegida != "➕ Crear Nueva Dirección de Envío":
        st.session_state["destino_entrega_activo"] = mod_elegida
    if st.session_state.get("_mod_entrega_lista") and previa is not None and mod_elegida != previa:
        invalidar_emision_visible_cotizador()
    st.session_state["_mod_entrega_lista"] = True


# ---------------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------------
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
          }};
          aplicar();
          setTimeout(aplicar, 80);
          setTimeout(aplicar, 280);
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


# ---------------------------------------------------------------------------
# Perfil
# ---------------------------------------------------------------------------
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
        {**actual, "nombre": nombre, "dni": dni, "telefono": telefono, "departamento": departamento, "ciudad": ciudad, "direccion": direccion}
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
                ok, msg = persistir_perfil_usuario(perfil["casillero"], nom, tel, dep, ciu, dir_e, dni)
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
                st.button("Guía", type="secondary", key="btn_guia_rapida", use_container_width=True, on_click=iniciar_guia_desde_mas)
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
    st.markdown(f'<style>:root {{ --ccm-cot-badge: "{n_badge}"; }}</style>', unsafe_allow_html=True)
    items = (
        ("inicio", "🏠", "Inicio", inicio_activo, ir_a_inicio),
        ("catalogo", "🔍", "Catálogo", catalogo_activo, ir_a_catalogo),
        ("cotizaciones", "📄", "Cotiz.", cot_activo, ir_a_mis_cotizaciones),
        ("cotizador", "🧮", "Cotizador", cotizador_activo, ir_a_cotizador),
        ("mas", "☰", "Más", mas_activo, ir_a_mas),
    )
    with st.container(key="bottom_nav"):
        cols = st.columns(5, gap="small")
        for col, (dest, icono, etiqueta, activo, handler) in zip(cols, items):
            with col:
                st.button(
                    f"{icono}\n{etiqueta}",
                    type="primary" if activo else "secondary",
                    key=f"bnav_{dest}",
                    use_container_width=True,
                    on_click=handler,
                )
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


def logout():
    for k in (
        "autenticado", "usuario", "rol", "casillero", "nombre", "dni", "telefono",
        "departamento", "ciudad", "direccion_exacta", "flash_perfil",
        "datos_pdf_confirmado", "ultima_cot_id", "cotizaciones", "_cot_emit_snapshot",
        "_seq_cot", "_ccm_rerun_app", "_ccm_scroll_emit", "_ccm_emit_error",
        "_mod_entrega_lista", "_mod_entrega_pendiente", "modalidad_envio_seleccionada",
        "sb_modalidad_entrega", "direcciones_usuario", "_dir_form_error",
        "_dir_form_exito", "_dir_form_reset", "dir_etiqueta_in", "dir_receptor_in",
        "dir_tel_in", "dir_exacta_in", "sub_tab_inicio", "vista_activa", "hub",
        "china_modulos_desbloqueados", "cotizacion_envio_foco", "cotizacion_historial_foco",
        "abrir_guia_rapida", "guia_china_auto_vista", "guia_activa", "guia_paso",
        "guia_omitida", "guia_completada", "mostrar_guia", "destino_entrega_activo",
    ):
        st.session_state.pop(k, None)
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.query_params.clear()
    st.rerun()


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
                    cas, sku, producto.get("titulo") or sku, int(cantidad or 1),
                    float(producto.get("precio_usd") or 0),
                    float(producto.get("peso_kg") or 0.8),
                    float(producto.get("volumen_m3") or 0.004),
                    producto.get("imagen_url") or "", fecha,
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


def pintar_tarjeta_aliexpress(prod, casillero, idx):
    titulo = (prod.get("titulo") or "Producto AliExpress").strip()
    titulo_corto = titulo[:75].rstrip() + "…" if len(titulo) > 78 else titulo
    precio = float(prod.get("precio_usd") or 0)
    tarifas = mapa_tarifas()
    tasa_hnl = float(leer_config_moneda("TASA_USD_HNL", 24.85))
    comision_pct = float(leer_config_moneda("COMISION_CCM_PORCENTAJE", 0.10))
    calc = calcular_costo_puesto_honduras(
        precio, float(prod.get("peso_kg") or 0.8), float(prod.get("volumen_m3") or 0.004),
        1, tarifas, tasa_hnl, comision_pct,
    )
    with st.container(border=True):
        if prod.get("imagen_url"):
            st.image(prod["imagen_url"], use_container_width=True)
        st.markdown(f"**{titulo_corto}**")
        st.caption(f"★ {prod['calificacion']:.1f} · ID {prod.get('product_id')}" if prod.get("calificacion") else f"ID {prod.get('product_id')}")
        st.markdown(f'<div class="ae-price">${precio:.2f} USD</div>', unsafe_allow_html=True)
        st.caption(f"Est. puesto en Honduras: ${calc['total_estimado_usd']:.2f} USD")
        st.link_button("Ver en AliExpress", prod.get("enlace") or "https://www.aliexpress.com", use_container_width=True)
        if st.button("Cotizar importación / Agregar a casillero", key=f"ae_add_{prod.get('product_id')}_{idx}", use_container_width=True):
            sku = agregar_producto_ae_a_casillero(casillero, prod)
            st.session_state["ae_flash"] = f"Artículo vinculado al casillero {casillero} (`{sku}`)."
            st.rerun()


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
        min_usd = st.number_input("Precio mín. (USD)", min_value=0.0, max_value=100000.0, value=0.0, step=1.0, key="ae_min_usd")
    with f2:
        max_usd = st.number_input("Precio máx. (USD)", min_value=0.0, max_value=100000.0, value=0.0, step=1.0, key="ae_max_usd")
    with f3:
        orden = st.selectbox("Ordenar por", ["Más vendidos", "Mejor precio", "Calificación"], key="ae_orden")
    tab_kw, tab_img = st.tabs(["Búsqueda por Palabra Clave", "Búsqueda por Imagen"])
    with tab_kw:
        keyword = st.text_input("Término de búsqueda", placeholder="Ej. piezas cnc, auriculares, herramientas", key="ae_keyword")
        if st.button("🔍 Buscar en AliExpress", type="primary", key="btn_ae_buscar_kw", use_container_width=True):
            with st.spinner("Buscando productos en AliExpress..."):
                resultado = ejecutar_busqueda("texto", keyword, None, min_usd, max_usd, orden)
            st.session_state["ae_resultados"] = resultado.get("productos") or []
            st.session_state["ae_busqueda_meta"] = resultado
            st.rerun()
    with tab_img:
        img_up = st.file_uploader("Foto del producto (JPG o PNG)", type=["jpg", "jpeg", "png"], key="ae_img_upload")
        if st.button("🔍 Buscar en AliExpress", type="primary", key="btn_ae_buscar_img", use_container_width=True):
            imagen_bytes = img_up.getvalue() if img_up else None
            with st.spinner("Buscando productos en AliExpress..."):
                resultado = ejecutar_busqueda("imagen", "", imagen_bytes, min_usd, max_usd, orden)
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
    elif resultados:
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
                with col:
                    pintar_tarjeta_aliexpress(resultados[idx], casillero, idx)
    elif not meta.get("error"):
        st.warning("No hay stock disponible para los filtros indicados.")
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


def pdf_tarifa_fila(casillero, nombre, telefono, ciudad, fila, destino, tipo="Cotización Histórica"):
    id_cot, al_c, an_c, la_c, pe_lb_c, vol_m3_c, tot_c, fec_c, _conf = fila
    return generar_pdf_confirmacion_cotizacion(
        casillero=casillero,
        nombre=nombre,
        telefono=telefono,
        ciudad=ciudad,
        tipo_carga=tipo,
        al=al_c,
        an=an_c,
        la=la_c,
        peso_lb=pe_lb_c,
        peso_kg=lb_a_kg(pe_lb_c),
        vol_m3=vol_m3_c,
        vol_ft3=volumen_ft3(vol_m3_c),
        total_usd=tot_c,
        detalle_tarifa="Tarifa Calculada Sistema CCM",
        id_cot=id_cot,
        destino_entrega=destino,
        fecha_emision=fec_c,
    )


def pdf_ficha_fila(casillero, nombre, telefono, ciudad, fila, destino):
    id_cot, al_c, an_c, la_c, pe_lb_c, vol_m3_c, _tot, fec_c, _conf = fila
    return generar_pdf_etiqueta_proveedor(
        casillero=casillero,
        nombre=nombre,
        telefono=telefono,
        ciudad=ciudad,
        al=al_c,
        an=an_c,
        la=la_c,
        pe_lb=pe_lb_c,
        pe_kg=lb_a_kg(pe_lb_c),
        vol_m3=vol_m3_c,
        destino_entrega=destino,
        fecha_emision=fec_c,
    )


# ---------------------------------------------------------------------------
# Estilos + restauración de sesión
# ---------------------------------------------------------------------------
inyectar_estilos()
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


# ---------------------------------------------------------------------------
# Login / registro / recuperación
# ---------------------------------------------------------------------------
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
        u_ident = st.text_input("Casillero, DNI o correo", placeholder="Ej: CCM-13011998 o correo@gmail.com", key="log_cas")
        u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña", key="log_pwd")
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
            dni = st.text_input("Número de Identidad (DNI - 13 dígitos) *", value=st.session_state["reg_datos"].get("dni", ""))
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
            dep_reg = st.selectbox("Departamento *", list(MUNICIPIOS_HONDURAS.keys()), index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0, key="sb_dep_reg")
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
            rub = st.selectbox("Rubro Principal", ["Ferretería & Construcción", "Cerámica & Acabados", "Electrónica", "Ropa & Calzado", "Repuestos", "General"])
            mod = st.radio(
                "Modalidad de Entrega",
                ["Retiro en Bodega Central (San Juan, Intibucá)", "Envío con Forza a Domicilio"],
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
                            st.error("Ya existe un casillero registrado con este DNI o correo.")
                        else:
                            cur.execute(
                                "INSERT INTO usuarios (codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)",
                                (n_cod, d["nom"], d["dni"], d["cor"], d["tel"], d["dep"], d["ciu"], d["dir"], rub, mod, hash_pwd(n_pwd), f_crea),
                            )
                            conn.commit()
                            asegurar_permisos_casillero(n_cod, "cliente")
                            st.session_state["reg_exito"] = {"nombre": d["nom"], "correo": d["cor"], "casillero": n_cod, "password": n_pwd}
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
                    conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(nueva_p), u[0]))
                st.success(f"✅ Nueva clave: **{nueva_p}**")
            else:
                st.error("Correo no registrado.")
        if st.button("Volver al Login", type="secondary"):
            st.session_state["vista_actual"] = "login"
            st.rerun()


# ---------------------------------------------------------------------------
# Portal del cliente
# ---------------------------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    if st.session_state.get("hub") and not usuario_puede_hub(st.session_state["hub"]):
        st.session_state["hub"] = None
        st.session_state["sub_tab_inicio"] = "Inicio"
        st.session_state["vista_activa"] = "Inicio"
        st.rerun()
    st.session_state.pop("_ccm_rerun_app", None)
    casillero = formatear_casillero(st.session_state["casillero"])
    st.session_state["casillero"] = casillero
    ahora_hn = obtener_tiempo_honduras()
    purgar_cotizaciones_no_confirmadas_vencidas(ahora_hn)
    _limpiar_cotizacion_vencida_en_sesion(ahora_hn)
    hidratar_cotizaciones_sesion(casillero)
    nombre_completo = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "San Juan, Intibucá")
    partes_nombre = nombre_completo.strip().split()
    nombre_display = " ".join(partes_nombre[:2]) if partes_nombre else "Cliente"
    hora_actual = ahora_hn.hour
    if 5 <= hora_actual < 12:
        saludo_horario = "Buenos días"
    elif 12 <= hora_actual < 19:
        saludo_horario = "Buenas tardes"
    else:
        saludo_horario = "Buenas noches"
    fecha_hora_texto = f"{DIAS_SEMANA_ES.get(ahora_hn.weekday(), '')}, {ahora_hn.day} {MESES_ES.get(ahora_hn.month, '')} {ahora_hn.year} &bull; {ahora_hn.strftime('%I:%M %p')}"
    lista_todas_cotizaciones, lista_mis_cotizaciones = filas_cotizaciones_casillero(casillero, ahora_hn)
    total_cotizaciones = len(lista_mis_cotizaciones)
    direcciones_guardadas = direcciones_sesion(casillero)
    opciones_modalidad = opciones_entrega_desde_sesion(casillero)
    if st.session_state["modalidad_envio_seleccionada"] not in opciones_modalidad:
        st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
    destino_activo = destino_para_documentos()

    with st.container(key="sticky_top_header"):
        st.markdown(
            html_encabezado_institucional(
                f'<div class="app-greeting-title">{saludo_horario}, {nombre_display}</div>'
                f'<div class="app-greeting-sub"><span class="app-header-casillero">Casillero: <b>{casillero}</b></span>'
                f'<span class="app-header-sep"> &bull; </span><span class="app-header-cots">{total_cotizaciones} Cotizaciones</span></div>'
                f'<div class="app-header-time">🕒 {fecha_hora_texto}</div>'
            ),
            unsafe_allow_html=True,
        )
    sincronizar_altura_encabezado_fijo()
    detectar_avance_descarga_guia()
    aplicar_clase_guia_js()

    vista = st.session_state["sub_tab_inicio"]

    if vista == "Inicio":
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

    if vista == "Más":
        pintar_vista_mas()

    if vista == "Consultas":
        cas_txt = formatear_casillero(casillero)
        msg_wa = urllib.parse.quote(f"Hola Centro de Cerámicas y Más, tengo una consulta de mi casillero {cas_txt}.")
        st.markdown("#### 🔍 Consultas")
        st.link_button("💬 Preguntar por WhatsApp", f"https://wa.me/50495771099?text={msg_wa}", use_container_width=True)
        c_q1, c_q2 = st.columns(2)
        with c_q1:
            st.button("📖 Catálogo 1688", key="btn_consultas_1688", use_container_width=True, on_click=ir_a_catalogo)
        with c_q2:
            st.button("🇺🇸 AliExpress", key="btn_consultas_ae", use_container_width=True, on_click=ir_a, args=("Inicio", "eeuu"))

    if vista == "Configuración":
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

    if vista == "Mis Cotizaciones":
        with st.container(key="vista_historial"):
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
                        key=lambda r: (0 if int(r[0]) == foco_hist else 1, *clave_orden_cotizacion(r[7], r[0])),
                    )
                scroll_pendiente_hecho = False
                for cot in lista_mis_cotizaciones:
                    id_cot_item, al_c, an_c, la_c, pe_lb_c, vol_m3_c, tot_c, fec_c, conf_c = cot
                    consolidada = es_cotizacion_confirmada(conf_c)
                    estado_txt = texto_estado_cotizacion(fec_c, conf_c, ahora_hn)
                    es_foco_hist = bool(foco_hist and int(id_cot_item) == foco_hist)
                    pendiente_foco = es_foco_hist and not consolidada
                    id_ancla = 'id="cotizacion-foco-pendiente"' if pendiente_foco else f'id="cotizacion-ccm-{id_cot_item}"'
                    with st.container(key=f"tarjeta_cot_{id_cot_item}"):
                        st.markdown(
                            f'<div {id_ancla} class="cot-card-body">'
                            f'<div class="cot-card-id">🔖 CCM-COT-{id_cot_item:05d} • {formatear_fecha_pantalla(fec_c)}</div>'
                            f'<div>📐 {al_c:.1f}x{an_c:.1f}x{la_c:.1f} cm | {pe_lb_c:.1f} lbs | <b>${tot_c:.2f} USD</b></div>'
                            f'<div>{estado_txt}</div></div>',
                            unsafe_allow_html=True,
                        )
                        pdf_historial = pdf_tarifa_fila(casillero, nombre_completo, tel_cli, ciu_cli, cot, destino_activo)
                        if consolidada:
                            st.button("📦 Ir a Envíos", type="primary", key=f"btn_ir_envios_{id_cot_item}", use_container_width=True, on_click=ir_a_envios_de_cotizacion, args=(id_cot_item,))
                        else:
                            st.button("Confirmar Cotización", type="primary", key=f"btn_confirmar_cot_{id_cot_item}", use_container_width=True, on_click=on_confirmar_cot_historial, args=(id_cot_item, casillero))
                        st.download_button(
                            f"📥 PDF CCM-COT-{id_cot_item:05d}",
                            pdf_historial,
                            f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                            "application/pdf",
                            key=f"dl_cot_{id_cot_item}",
                            use_container_width=True,
                        )
                        if pendiente_foco and not scroll_pendiente_hecho:
                            desplazar_a_ancla("cotizacion-foco-pendiente")
                            scroll_pendiente_hecho = True
            else:
                st.info("No hay cotizaciones vigentes ni consolidadas. Emita una tarifa en el Cotizador.")
            espaciador_barra_inferior("safe_historial")

    if vista == "Cotizador" and destino_activo == "➕ Crear Nueva Dirección de Envío":
        with st.container(key="vista_cotizador"):
            with st.container(key="formulario_direcciones"):
                selector_modalidad_entrega(opciones_modalidad)
                st.markdown("#### 📍 Administrar Direcciones de Envío")
                st.markdown(f"**{OPCION_PREDETERMINADA}** — prederminada (no se elimina).")
                if direcciones_guardadas:
                    for idx_dir, dir_item in enumerate(direcciones_guardadas):
                        etiq = dir_item.get("etiqueta", "")
                        rec = dir_item.get("receptor", "")
                        ciu_d = dir_item.get("ciudad", "")
                        dir_e = dir_item.get("direccion", "")
                        id_dir = dir_item.get("id")
                        col_info_d, col_btn_del = st.columns([3.8, 1])
                        with col_info_d:
                            st.markdown(f"**🏷️ {etiq}** · Recibe: {rec} · {ciu_d} · {dir_e}")
                        with col_btn_del:
                            if st.button("🗑️ Eliminar", key=f"del_dir_{id_dir or f'ses_{idx_dir}'}", type="secondary"):
                                eliminar_direccion_usuario(casillero, etiq, ciu_d, id_dir)
                                seleccionar_modalidad_entrega(OPCION_PREDETERMINADA)
                                st.session_state.pop("datos_pdf_confirmado", None)
                                st.toast(f"🗑️ Dirección '{etiq}' eliminada.")
                                st.rerun()
                st.markdown("##### ➕ Agregar Nueva Dirección de Entrega")
                if st.session_state.pop("_dir_form_reset", None):
                    for _campo_dir in CAMPOS_FORM_DIRECCION:
                        st.session_state.pop(_campo_dir, None)
                st.session_state.setdefault("dir_receptor_in", nombre_completo)
                st.session_state.setdefault("dir_tel_in", tel_cli)
                st.text_input("Etiqueta de la dirección *", key="dir_etiqueta_in", placeholder="Ej: Mi Casa, Sucursal 2")
                st.text_input("Nombre de quien recibe *", key="dir_receptor_in")
                st.text_input("Teléfono de contacto *", key="dir_tel_in")
                dep_dir_in = st.selectbox("Departamento *", list(MUNICIPIOS_HONDURAS.keys()), index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0, key="sb_dep_nueva_dir")
                st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_dir_in], key="sb_ciu_nueva_dir")
                st.text_area("Dirección exacta y referencias *", key="dir_exacta_in")
                error_dir = st.session_state.pop("_dir_form_error", None)
                if error_dir:
                    st.error(error_dir)
                st.button("💾 Guardar Dirección", type="primary", key="btn_guardar_nueva_dir", use_container_width=True, on_click=guardar_nueva_direccion, args=(casillero,))
                st.button("Cancelar", type="secondary", key="btn_cancelar_dir", use_container_width=True, on_click=cancelar_nueva_direccion)

    if vista == "Catálogo":
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
                tarifas = mapa_tarifas()
                tasa_hnl = float(leer_config_moneda("TASA_USD_HNL", 24.85))
                comision_pct = float(leer_config_moneda("COMISION_CCM_PORCENTAJE", 0.10))
                for prod in resultados_1688:
                    calc = calcular_costo_puesto_honduras(
                        prod["precio_fabrica_usd"], prod["peso_kg"], prod["volumen_m3"], prod["moq"],
                        tarifas, tasa_hnl, comision_pct,
                    )
                    c_img, c_det = st.columns([1, 1.8])
                    with c_img:
                        st.image(prod["imagen_url"], use_container_width=True)
                    with c_det:
                        st.markdown(f"**{prod['nombre']}**")
                        st.caption(f"🏭 {prod['proveedor']} | SKU: `{prod['sku']}`")
                        st.success(f"🇭🇳 Puesto en Honduras: ${calc['total_estimado_usd']:.2f} USD (~L {calc['total_estimado_hnl']:.2f})")
                        st.link_button("🔗 Ver en 1688", prod["url_proveedor"], use_container_width=True)
            espaciador_barra_inferior("safe_catalogo")

    elif vista == "Cotizador" and destino_activo != "➕ Crear Nueva Dirección de Envío":
        with st.container(key="vista_cotizador"):
            st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
            selector_modalidad_entrega(opciones_modalidad)
            st.markdown(
                f'<div class="destino-seleccionado-card">'
                f'<div class="destino-seleccionado-kicker">📍 Destino de Entrega Seleccionado</div>'
                f'<div class="destino-seleccionado-dir">{destino_activo}</div>'
                    f'<div class="destino-seleccionado-nota">(Se imprimirá en todos los formatos y fichas de bodega)</div>'
                    f"</div>",
                unsafe_allow_html=True,
            )
            exito_dir = st.session_state.pop("_dir_form_exito", None)
            if exito_dir:
                st.success(f"✅ {exito_dir}")

            tarifas = mapa_tarifas()
            umbral_paq = float(tarifas.get("umbral_paqueteria_lb") or 99.0)
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
                unidad_medida = st.selectbox("Unidad de Medida:", ["Centímetros (cm)", "Pulgadas (in)", "Metros (m)"], key="sb_unidad_medida", on_change=invalidar_emision_visible_cotizador)
            with c_u2:
                unidad_peso = st.selectbox("Unidad de Peso:", ["Libras (lb)", "Kilogramos (kg)"], key="sb_unidad_peso", on_change=invalidar_emision_visible_cotizador)

            es_paqueteria = "Paquetería Menor" in tipo_carga
            dim = limites_dimensiones(unidad_medida, comercial=not es_paqueteria)
            pes = limites_peso(unidad_peso, paqueteria=es_paqueteria, umbral_paqueteria_lb=umbral_paq)
            etiqueta_medida = unidad_medida.split()[1].strip("()")
            etiqueta_peso = unidad_peso.split()[1].strip("()")
            st.caption(
                f"Tope: contenedor 40' HC ({CONTENEDOR_40_ALTO_M:.2f}×{CONTENEDOR_40_ANCHO_M:.2f}×{CONTENEDOR_40_LARGO_M:.2f} m). "
                f"Peso máximo legal HN: {PESO_MAX_CONTENEDOR_HN_KG:,.0f} kg ({peso_max_contenedor_hn_lb():,.0f} lb)."
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

            al_val = a_cm(al_input, unidad_medida)
            an_val = a_cm(an_input, unidad_medida)
            la_val = a_cm(la_input, unidad_medida)
            pe_lb, pe_kg = a_lb_kg(pe_input, unidad_peso)
            vol_m3_val = volumen_m3(al_val, an_val, la_val)
            vol_ft3_val = volumen_ft3(vol_m3_val)
            flete = calcular_flete_maritimo(pe_lb, pe_kg, vol_m3_val, tarifas, forzar_paqueteria=es_paqueteria)
            tot = flete.flete_usd
            modalidad_pdf = flete.modalidad
            detalle_pdf = flete.detalle

            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Volumen (m³)", f"{vol_m3_val:.4f} m³")
            with m2:
                st.metric("CBM facturable" if not es_paqueteria else "Pies cúbicos", f"{flete.cbm_facturable:.4f}" if not es_paqueteria else f"{vol_ft3_val:.2f} ft³")
            with m3:
                st.metric("Total Estimado", f"${tot:.2f} USD")

            firma_actual = firma_parametros_cotizador(al_val, an_val, la_val, pe_lb, destino_activo, modalidad_pdf)
            sincronizar_emision_con_formulario(firma_actual)
            st.session_state["_cot_emit_snapshot"] = {
                "al": al_val, "an": an_val, "la": la_val,
                "peso_lb": pe_lb, "peso_kg": pe_kg,
                "vol_m3": vol_m3_val, "vol_ft3": vol_ft3_val,
                "total_usd": tot, "tipo_carga": modalidad_pdf,
                "detalle_tarifa": detalle_pdf, "destino": destino_activo,
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
                st.error("No se pudo guardar la tarifa en la base de datos. Puede descargar el formato con la emisión en memoria.")

            d_pdf = st.session_state.get("datos_pdf_confirmado")
            if isinstance(d_pdf, dict):
                try:
                    id_c = int(d_pdf.get("id_cot") or 0)
                except (TypeError, ValueError):
                    id_c = 0
                if id_c:
                    dest_pdf = d_pdf.get("destino_entrega", destino_activo)
                    fecha_doc = d_pdf.get("fecha_hora_doc", obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"))
                    tarifa_consolidada = cotizacion_esta_confirmada(id_c, casillero)
                    estado_doc = texto_estado_cotizacion(d_pdf.get("fecha_sql") or fecha_doc, 1 if tarifa_consolidada else 0, ahora_hn)
                    st.success(f"Tarifa CCM-COT-{id_c:05d} emitida. {estado_doc}")
                    pdf_fab = generar_pdf_etiqueta_proveedor(
                        casillero=casillero, nombre=nombre_completo, telefono=tel_cli, ciudad=ciu_cli,
                        al=d_pdf.get("al", 0), an=d_pdf.get("an", 0), la=d_pdf.get("la", 0),
                        pe_lb=d_pdf.get("peso_lb", 0), pe_kg=d_pdf.get("peso_kg", 0), vol_m3=d_pdf.get("vol_m3", 0),
                        destino_entrega=dest_pdf, fecha_emision=fecha_doc,
                    ) or b""
                    with st.container(key="acciones_emit_cotizador"):
                        st.markdown('<div id="ccm-acciones-emit"></div>', unsafe_allow_html=True)
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
                        st.button(
                            "📋 Ir a Mis Cotizaciones",
                            type="primary",
                            key=f"btn_ver_mis_cotizaciones_{id_c}",
                            use_container_width=True,
                            on_click=ir_a_historial_guia,
                            args=(id_c,),
                        )
                    if st.session_state.pop("_ccm_scroll_emit", None):
                        desplazar_a_ancla("ccm-acciones-emit", alinear="end")
            espaciador_barra_inferior("safe_cotizador_fin")

    elif vista == "Mis Envíos":
        with st.container(key="vista_envios"):
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📦 Mis Paquetes en Tránsito")
            with get_db() as conn:
                paquetes = conn.execute(
                    "SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?",
                    (casillero,),
                ).fetchall()
            if paquetes:
                for p in paquetes:
                    st.markdown(f"**Tracking:** {p[0]} · Contenedor {p[2]} · **{p[3]}**")
            else:
                st.info("No tienes paquetes registrados en travesía.")
            st.markdown("#### 📄 Documentos de cotizaciones confirmadas")
            cotizaciones_despacho = ordenar_cotizaciones_desc([row for row in lista_mis_cotizaciones if es_cotizacion_confirmada(row[8])])
            try:
                foco_envios = int(st.session_state.get("cotizacion_envio_foco") or 0)
            except (TypeError, ValueError):
                foco_envios = 0
            if foco_envios:
                cotizaciones_despacho = sorted(cotizaciones_despacho, key=lambda r: (0 if int(r[0]) == foco_envios else 1, *clave_orden_cotizacion(r[7], r[0])))
            if cotizaciones_despacho:
                for cot_env in cotizaciones_despacho:
                    id_e = cot_env[0]
                    st.markdown(f"**CCM-COT-{id_e:05d}** · {formatear_fecha_pantalla(cot_env[7])} · ${cot_env[6]:.2f} USD")
                    with st.container(key=f"docs_env_{id_e}"):
                        st.download_button(
                            f"🏷️ Descargar Ficha CCM-COT-{id_e:05d}",
                            pdf_ficha_fila(casillero, nombre_completo, tel_cli, ciu_cli, cot_env, destino_activo),
                            f"Ficha_Bodega_{casillero}_COT{id_e:05d}.pdf",
                            "application/pdf",
                            key=f"dl_ficha_env_{id_e}",
                            use_container_width=True,
                        )
                        st.download_button(
                            f"📥 PDF Tarifa CCM-COT-{id_e:05d}",
                            pdf_tarifa_fila(casillero, nombre_completo, tel_cli, ciu_cli, cot_env, destino_activo, tipo="Cotización Confirmada"),
                            f"Comprobante_Tarifa_{casillero}_COT{id_e:05d}.pdf",
                            "application/pdf",
                            key=f"dl_tarifa_env_{id_e}",
                            use_container_width=True,
                        )
            else:
                st.info("Confirme una cotización para descargar la Ficha y el PDF Tarifa.")
            espaciador_barra_inferior("safe_envios")

    elif vista == "Etiqueta":
        with st.container(key="vista_fichas"):
            st.markdown("#### 📋 Fichas")
            cotizaciones_ficha = ordenar_cotizaciones_desc([row for row in lista_mis_cotizaciones if es_cotizacion_confirmada(row[8])])
            if cotizaciones_ficha:
                for cot_f in cotizaciones_ficha:
                    id_f = cot_f[0]
                    st.markdown(f"**CCM-COT-{id_f:05d}** · {formatear_fecha_pantalla(cot_f[7])}")
                    st.download_button(
                        f"🏷️ Descargar Ficha CCM-COT-{id_f:05d}",
                        pdf_ficha_fila(casillero, nombre_completo, tel_cli, ciu_cli, cot_f, destino_activo),
                        f"Ficha_Bodega_{casillero}_COT{id_f:05d}.pdf",
                        "application/pdf",
                        key=f"dl_ficha_mod_{id_f}",
                        use_container_width=True,
                    )
            else:
                st.info("Confirme una cotización para descargar su ficha de bodega.")
            espaciador_barra_inferior("safe_fichas")

    pintar_barra_inferior(total_cotizaciones)


# ---------------------------------------------------------------------------
# Panel administrativo
# ---------------------------------------------------------------------------
elif es_rol_admin(st.session_state.get("rol")):
    root = es_superadmin(st.session_state.get("rol"))
    titulo = "Panel de Superadministrador" if root else "Panel Administrativo"
    st.markdown(html_encabezado_institucional(f'<div class="app-greeting-title">{titulo}</div>'), unsafe_allow_html=True)
    tab_u, tab_p, tab_t, tab_s = st.tabs(["👥 Usuarios y permisos", "📦 Paquetes", "⚙️ Tarifas y fórmulas", "🗄️ Sistema"])

    with tab_u:
        with get_db() as conn:
            if root:
                filas = conn.execute(
                    "SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rol, activo FROM usuarios ORDER BY rol DESC, nombre_completo"
                ).fetchall()
            else:
                filas = conn.execute(
                    "SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rol, activo FROM usuarios WHERE rol = 'cliente' ORDER BY nombre_completo"
                ).fetchall()
        if filas:
            st.dataframe(
                {
                    "Casillero": [formatear_casillero(r[1]) for r in filas],
                    "Nombre": [r[2] for r in filas],
                    "Correo": [r[4] for r in filas],
                    "Rol": [r[9] for r in filas],
                    "Activo": ["Sí" if r[10] else "No" for r in filas],
                },
                use_container_width=True,
                hide_index=True,
            )
        etiquetas = [f"{formatear_casillero(r[1])} — {r[2]}" for r in filas]
        if etiquetas:
            elegido = st.selectbox("Cuenta a gestionar", etiquetas, key="admin_sel_user")
            u = filas[etiquetas.index(elegido)]
            uid, cas_u, nom_u, dni_u, cor_u, tel_u, dep_u, ciu_u, dir_u, rol_u, act_u = u
            perm = permisos_de(cas_u, rol_u)
            n_nom = st.text_input("Nombre completo", value=nom_u, key=f"adm_nom_{cas_u}")
            n_dni = st.text_input("DNI", value=dni_u, key=f"adm_dni_{cas_u}")
            n_cor = st.text_input("Correo", value=cor_u, key=f"adm_cor_{cas_u}")
            n_tel = st.text_input("Teléfono", value=tel_u, key=f"adm_tel_{cas_u}")
            n_act = st.checkbox("Cuenta activa", value=bool(act_u), key=f"adm_act_{cas_u}")
            p_china = st.checkbox("Hub China", value=bool(perm.get("hub_china")), key=f"adm_h_cn_{cas_u}")
            p_eeuu = st.checkbox("Hub EE. UU.", value=bool(perm.get("hub_eeuu")), key=f"adm_h_us_{cas_u}")
            p_hn = st.checkbox("Hub Honduras", value=bool(perm.get("hub_honduras")), key=f"adm_h_hn_{cas_u}")
            p_cot = st.checkbox("Cotizador", value=bool(perm.get("mod_cotizador")), key=f"adm_m_cot_{cas_u}")
            p_cat = st.checkbox("Catálogo", value=bool(perm.get("mod_catalogo")), key=f"adm_m_cat_{cas_u}")
            p_hist = st.checkbox("Mis Cotizaciones", value=bool(perm.get("mod_cotizaciones")), key=f"adm_m_hist_{cas_u}")
            p_env = st.checkbox("Envíos", value=bool(perm.get("mod_envios")), key=f"adm_m_env_{cas_u}")
            p_fic = st.checkbox("Fichas", value=bool(perm.get("mod_fichas")), key=f"adm_m_fic_{cas_u}")
            if st.button("Guardar perfil y permisos", type="primary", key="adm_save_user"):
                with get_db() as conn:
                    conn.execute(
                        "UPDATE usuarios SET nombre_completo=?, dni=?, correo_principal=?, telefono_principal=?, activo=? WHERE id=?",
                        (n_nom, n_dni, n_cor, n_tel, 1 if n_act else 0, uid),
                    )
                guardar_permisos(cas_u, {
                    "hub_china": p_china, "hub_eeuu": p_eeuu, "hub_honduras": p_hn,
                    "mod_cotizador": p_cot, "mod_catalogo": p_cat, "mod_cotizaciones": p_hist,
                    "mod_envios": p_env, "mod_fichas": p_fic,
                })
                st.success("Cambios guardados.")
                st.rerun()

    with tab_p:
        t_in = st.text_input("Tracking de China")
        c_in = st.text_input("Casillero asignado")
        d_in = st.text_input("Descripción de la carga")
        cont_in = st.text_input("ID de contenedor")
        e_in = st.selectbox("Estado", ["En Bodega China", "En Travesía Marítima", "En Desaduanaje", "Disponible en Bodega Central", "Entregado"])
        if st.button("Actualizar Paquete", type="primary"):
            if t_in and c_in:
                f_act = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                with get_db() as conn:
                    conn.execute(
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
        with get_db() as conn:
            tablas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            conteos = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tablas}
        st.dataframe({"Tabla": list(conteos.keys()), "Registros": list(conteos.values())}, use_container_width=True, hide_index=True)

    if st.button("Cerrar sesión", type="secondary", key="btn_logout_admin"):
        logout()

else:
    st.error("Rol no reconocido. Inicie sesión de nuevo.")
    if st.button("Volver al login"):
        logout()
