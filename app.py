"""Centro de Cerámicas y Más — casillero marítimo China → Honduras.

Punto de entrada Streamlit. La lógica de medidas, flete, PDF y AliExpress
vive en `ccm/`; aquí solo queda la orquestación de vistas y session_state.
"""

from __future__ import annotations

import html
import random
import string
import textwrap
import urllib.parse
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from ccm.aliexpress import credenciales_configuradas, ejecutar_busqueda
from ccm.catalog import buscar_productos_1688_imagen, buscar_productos_1688_texto
from ccm.config import (
    ALIAS_VISTA,
    CAMPOS_FORM_DIRECCION,
    CLAVES_WIDGET_PERFIL,
    HUBS,
    MODULOS_POR_ID,
    MUNICIPIOS_HONDURAS,
    OPCION_PREDETERMINADA,
    PASOS_GUIA_INTERACTIVA,
    VISTAS_MODULO,
)
from ccm.db import (
    asegurar_permisos_casillero,
    bootstrap,
    coincidencias_casillero,
    es_rol_admin,
    es_superadmin,
    formatear_casillero,
    generar_codigo_casillero_dni,
    get_db,
    get_tarifa,
    guardar_permisos,
    hash_pwd,
    leer_config_moneda,
    mapa_tarifas,
    permisos_de,
    purgar_cotizaciones_no_confirmadas_vencidas,
    set_config_sistema,
    set_tarifa,
    usuario_puede_hub,
    usuario_puede_modulo,
)
from ccm.documents import generar_pdf_confirmacion_cotizacion, generar_pdf_etiqueta_proveedor
from ccm.quoting import (
    CONTENEDOR_40_ALTO_M,
    CONTENEDOR_40_ANCHO_M,
    CONTENEDOR_40_LARGO_M,
    PESO_MAX_CONTENEDOR_HN_KG,
    a_cm,
    a_lb_kg,
    calcular_costo_puesto_honduras,
    calcular_flete_maritimo,
    lb_a_kg,
    limites_dimensiones,
    limites_peso,
    peso_max_contenedor_hn_lb,
    volumen_ft3,
    volumen_m3,
)
from ccm.timeutil import (
    DIAS_SEMANA_ES,
    MESES_ES,
    cotizacion_vigente,
    cotizacion_visible_historial,
    es_cotizacion_confirmada,
    estampa_tiempo_honduras,
    formatear_fecha_pantalla,
    clave_orden_cotizacion,
    obtener_tiempo_honduras,
    ordenar_cotizaciones_desc,
    proximo_cierre_contenedor,
    texto_estado_cotizacion,
)

st.set_page_config(
    page_title="Centro de Cerámicas y Más — Casillero & Catálogo China",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

bootstrap()

CSS_PATH = Path(__file__).resolve().parent / "assets" / "app.css"


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


def inyectar_estilos():
    css = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.is_file() else ""
    if css:
        st.markdown(f"<style>\n{css}\n</style>", unsafe_allow_html=True)


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
        f"Próximo cierre: <b>{cierre}</b>."
        f"</div>"
        f'<a class="promo-ad-cta" href="{url_wa}" target="_blank" rel="noopener noreferrer">Consultar Promoción</a>'
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
    with st.container(key="bottom_nav_pin"):
        components.html(
            """
            <script>
            (function () {
              const doc = window.parent.document;
              const anclar = () => {
                const nav = doc.querySelector('[class~="st-key-bottom_nav"]') || doc.querySelector(".st-key-bottom_nav");
                if (!nav) return;
                nav.style.setProperty("position", "fixed", "important");
                nav.style.setProperty("bottom", "20px", "important");
                nav.style.setProperty("left", "50%", "important");
                nav.style.setProperty("transform", "translateX(-50%)", "important");
                nav.style.setProperty("z-index", "9999", "important");
                nav.style.setProperty("width", "min(96vw, 520px)", "important");
              };
              anclar();
              setTimeout(anclar, 80);
              setTimeout(anclar, 280);
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
              const medir = () => {
                let bottom = 0;
                doc.querySelectorAll('[class~="st-key-sticky_top_header"], .st-key-sticky_top_header').forEach((nodo) => {
                  const r = nodo.getBoundingClientRect();
                  if (r.bottom > bottom) bottom = r.bottom;
                });
                if (bottom < 40) return;
                const offset = Math.ceil(Math.max(bottom + 16, 208)) + "px";
                [doc.documentElement, doc.body, doc.querySelector(".stApp")].forEach((nodo) => {
                  if (nodo && nodo.style) nodo.style.setProperty("--header-offset", offset);
                });
              };
              win.__ccmMedirHeader = medir;
              medir();
              setTimeout(medir, 80);
              setTimeout(medir, 280);
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
            const el = window.parent.document.getElementById("{eid}");
            if (el) el.scrollIntoView({{ behavior: "smooth", block: "{bloque}" }});
          }};
          setTimeout(ir, 180);
          setTimeout(ir, 480);
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
        "guia_omitida", "guia_completada", "mostrar_guia",
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
    if not credenciales_configuradas():
        st.info("Configure `ALIEXPRESS_APP_SECRET` para resultados en vivo. Mientras tanto se usa un catálogo de demostración.")
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
        keyword = st.text_input("Término de búsqueda", placeholder="Ej. piezas cnc, auriculares", key="ae_keyword")
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
        st.caption("Escriba un término o suba una foto del producto.")
    elif resultados:
        n_cols = 3
        for fila in range(0, len(resultados), n_cols):
            cols = st.columns(n_cols, gap="small")
            for offset, col in enumerate(cols):
                idx = fila + offset
                if idx >= len(resultados):
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
        st.markdown(f"- **{nombre}** · `{sku}` · {int(cantidad)} ud. · **${float(precio):.2f} USD** · {fecha}")


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
    destino_activo = st.session_state["modalidad_envio_seleccionada"]

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
                st.info(hub_vacio["descripcion"])

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
        st.info(
            f"**Casillero:** `{casillero}`  \n"
            f"**Nombre:** {st.session_state.get('nombre') or '—'}  \n"
            f"**Correo:** {st.session_state.get('usuario') or '—'}"
        )

    if vista == "Mis Cotizaciones":
        with st.container(key="vista_historial"):
            pintar_banner_promocional_china(casillero)
            st.markdown("#### 📄 Historial de Cotizaciones y Descarga de PDF")
            st.caption("Las tarifas no confirmadas caducan a las 24 horas. Al confirmar, quedan permanentes.")
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
                f'<div class="destino-seleccionado-dir">{destino_activo}</div></div>',
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
