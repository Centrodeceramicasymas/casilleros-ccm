import streamlit as st
import sqlite3
import hashlib
import random
import string
from datetime import datetime
import io
import os
import base64
import math
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DEL SISTEMA
# ---------------------------------------------------------
st.set_page_config(
    page_title="Centro de Cerámicas y Más — Cloud Logistics",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "ccm_maritime_enterprise.db"
LOGO_FILENAME = "logo centro y mas.jpg"

if "tema_visual" not in st.session_state:
    st.session_state["tema_visual"] = "Oscuro (Dark)"

if "vista_actual" not in st.session_state:
    st.session_state["vista_actual"] = "login"

if "seccion_activa" not in st.session_state:
    st.session_state["seccion_activa"] = "📐 Cotizador Marítimo"

# ---------------------------------------------------------
# 2. GENERADORES DE PDF NATIVOS
# ---------------------------------------------------------
def compilar_pdf_simple(stream_content):
    stream_bytes = stream_content.encode('latin-1', 'replace')
    stream_len = len(stream_bytes)
    
    pdf_buffer = io.BytesIO()
    pdf_buffer.write(b"%PDF-1.4\n")
    offsets = []
    
    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    
    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    
    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n")
    
    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode('latin-1'))
    pdf_buffer.write(stream_bytes)
    pdf_buffer.write(b"\nendstream\nendobj\n")
    
    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")
    
    xref_offset = pdf_buffer.tell()
    pdf_buffer.write(b"xref\n0 6\n0000000000 65535 f \n")
    for off in offsets:
        pdf_buffer.write(f"{off:010d} 00000 n \n".encode('latin-1'))
        
    pdf_buffer.write(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode('latin-1'))
    return pdf_buffer.getvalue()

def generar_pdf_etiqueta_proveedor(casillero, nombre, telefono, ciudad, al=0.0, an=0.0, la=0.0, pe_lb=0.0, pe_kg=0.0, vol_m3=0.0):
    dim_txt = f"{al:.1f} x {an:.1f} x {la:.1f} CM" if (al > 0 or an > 0 or la > 0) else "POR DEFINIR EN ORIGEN"
    peso_txt = f"{pe_kg:.2f} KG ({pe_lb:.1f} LBS)" if pe_lb > 0 else "_______ KG"
    vol_txt = f"{vol_m3:.4f} CBM" if vol_m3 > 0 else "_______ CBM"

    stream = f"""BT
/F1 16 Tf
40 790 Td
(CENTRO DE CERAMICAS Y MAS - HONDURAS) Tj
/F1 10 Tf
0 -18 Td
(MARITIME CONSOLIDATION CARGO [CHINA -> HONDURAS]) Tj
0 -30 Td
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
(FINAL DESTINATION: {ciudad.upper()}, HONDURAS) Tj
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

def generar_pdf_confirmacion_cotizacion(casillero, nombre, telefono, ciudad, tipo_carga, al, an, la, peso_lb, peso_kg, vol_m3, vol_ft3, total_usd, detalle_tarifa, id_cot):
    fecha_hoy = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    stream = f"""BT
/F1 15 Tf
40 790 Td
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
(FECHA Y HORA DE EMISION : {fecha_hoy}) Tj
0 -18 Td
(================================================================) Tj
/F1 11 Tf
0 -16 Td
(DATOS DEL CLIENTE Y CASILLERO ASIGNADO:) Tj
/F1 9 Tf
0 -14 Td
(CASILLERO INTERNACIONAL : {casillero}) Tj
0 -12 Td
(TITULAR DE LA CUENTA    : {nombre}) Tj
0 -12 Td
(TELEFONO / WHATSAPP    : {telefono}) Tj
0 -12 Td
(DESTINO FINAL          : {ciudad.upper()}, HONDURAS) Tj
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
# 3. ESTILOS CSS CON MODAL PSKLOUD (FONDO FINANCIERO)
# ---------------------------------------------------------
is_dark = (st.session_state["tema_visual"] == "Oscuro (Dark)")

bg_body = "#05070c" if is_dark else "#f1f5f9"
text_main = "#ffffff" if is_dark else "#0f172a"
text_muted = "#94a3b8" if is_dark else "#64748b"
input_bg = "#111827" if is_dark else "#ffffff"
input_border = "#1f2937" if is_dark else "#cbd5e1"
input_color = "#ffffff" if is_dark else "#0f172a"

btn_sec_bg = "#161e2e" if is_dark else "#e2e8f0"
btn_sec_text = "#e2e8f0" if is_dark else "#1e293b"
btn_sec_border = "#27354a" if is_dark else "#cbd5e1"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@700&display=swap');

    .stApp {{
        background-color: {bg_body} !important;
        color: {text_main} !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
    }}
    
    #MainMenu, header, footer {{visibility: hidden;}}

    /* FONDO ESTILO PSKLOUD / CALCULADORA PARA LOGIN */
    .psk-bg-wallpaper {{
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.94), rgba(15, 23, 42, 0.82)),
                    url('https://images.unsplash.com/photo-1554224155-8d04cb21cd6c?auto=format&fit=crop&w=2000&q=80') center center / cover no-repeat;
        position: fixed;
        top: 0; left: 0; width: 100vw; height: 100vh;
        z-index: 0;
        pointer-events: none;
    }}

    /* VENTANA MODAL PSKLOUD */
    .psk-login-modal {{
        background: #ffffff;
        border-radius: 12px;
        box-shadow: 0 20px 45px rgba(0,0,0,0.55);
        overflow: hidden;
        border: 1px solid #e2e8f0;
        margin-top: 1.5rem;
        position: relative;
        z-index: 1;
    }}
    .psk-login-header {{
        background: linear-gradient(90deg, #1d4ed8, #2563eb);
        color: #ffffff;
        padding: 14px 20px;
        font-weight: 700;
        font-size: 1.05rem;
        letter-spacing: 0.5px;
    }}
    .psk-login-body {{
        padding: 24px;
        background: #ffffff;
    }}
    .psk-login-footer {{
        background: #0f172a;
        color: #94a3b8;
        font-size: 0.68rem;
        text-align: center;
        padding: 14px 18px;
        line-height: 1.4;
    }}

    /* INPUTS */
    div[data-baseweb="input"], div[data-baseweb="select"] > div {{
        background-color: {input_bg} !important;
        border: 1px solid {input_border} !important;
        border-radius: 10px !important;
        overflow: hidden !important;
        padding: 4px 6px !important;
    }}
    div[data-baseweb="input"] input {{
        color: {input_color} !important;
        background-color: transparent !important;
        font-size: 0.95rem !important;
    }}

    /* BOTONES */
    div.stButton > button, div.stDownloadButton > button {{
        width: 100% !important;
        border-radius: 8px !important;
        padding: 11px 18px !important;
        font-size: 0.95rem !important;
        font-weight: 700 !important;
        transition: all 0.2s ease-in-out !important;
    }}

    .btn-psk-green div.stButton > button {{
        background-color: #22c55e !important;
        color: #ffffff !important;
        border: none !important;
        box-shadow: 0 4px 12px rgba(34, 197, 94, 0.35) !important;
    }}
    .btn-psk-green div.stButton > button:hover {{
        background-color: #16a34a !important;
        transform: translateY(-1px);
    }}

    .card-box {{
        background-color: {input_bg};
        border: 1px solid {input_border};
        border-radius: 14px;
        padding: 1.5rem;
        margin-bottom: 1rem;
    }}
    .china-address-box {{
        background-color: {'#090d16' if is_dark else '#f8fafc'};
        border: 2px dashed #0052cc;
        border-radius: 12px;
        padding: 1.4rem;
        font-family: 'Space Mono', monospace;
        font-size: 0.88rem;
        color: {text_main};
    }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 4. BASE DE DATOS SQLITE & UTILIDADES
# ---------------------------------------------------------
def hash_pwd(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
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
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS config_maritima (
                clave TEXT PRIMARY KEY,
                valor REAL NOT NULL
            )
        """)
        c.execute("""
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
                fecha TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS paquetes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking TEXT UNIQUE NOT NULL,
                codigo_casillero TEXT NOT NULL,
                descripcion TEXT,
                contenedor_id TEXT,
                estado TEXT NOT NULL,
                fecha_actualizacion TEXT NOT NULL
            )
        """)
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_libra', 3.50)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_m3', 680.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('minimo_cobro_usd', 10.00)")
        
        admin_pass = hash_pwd("admin123")
        c.execute("""
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal, 
                telefono_principal, departamento, ciudad, direccion_exacta, 
                password_hash, rol, activo, fecha_creacion
            ) VALUES (
                '08011990', 'Super Administrador', '0801199000000', 'admin@ccm.hn',
                '+504 9999-0000', 'Intibucá', 'San Juan', 'Oficina Central CCM',
                ?, 'admin', 1, '2026-08-22 00:00:00'
            )
        """, (admin_pass,))

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
        c.execute("UPDATE config_maritima SET valor = ? WHERE clave = ?", (valor, clave))

def generar_codigo_casillero_dni(dni_raw):
    solo_digitos = ''.join(filter(str.isdigit, str(dni_raw)))
    if len(solo_digitos) >= 8:
        return solo_digitos[:8]
    return solo_digitos.zfill(8)

def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return ''.join(random.choice(caracteres) for _ in range(8))

# ---------------------------------------------------------
# 5. GESTIÓN DE SESIÓN
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.update({
        "autenticado": False,
        "usuario": None,
        "rol": None,
        "casillero": None,
        "nombre": None,
        "telefono": None,
        "ciudad": None,
        "reg_paso": 1,
        "reg_datos": {}
    })

def logout():
    for k in ["autenticado", "usuario", "rol", "casillero", "nombre", "telefono", "ciudad", "datos_pdf_confirmado", "ultima_cot_id"]:
        st.session_state.pop(k, None)
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.rerun()

# ---------------------------------------------------------
# 6. PANTALLA DE ACCESO (VENTANA MODAL PSKLOUD IDÉNTICA)
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    st.markdown('<div class="psk-bg-wallpaper"></div>', unsafe_allow_html=True)

    if st.session_state["vista_actual"] == "login":
        _, col_modal, _ = st.columns([1, 1.45, 1])
        with col_modal:
            st.markdown("""
            <div class="psk-login-modal">
                <div class="psk-login-header">
                    Inicio de sesión
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Campos del formulario con estructura PSKLOUD
            col_pin, col_serial = st.columns(2)
            with col_pin:
                pin_in = st.text_input("PIN (Sistema)", value="51127", key="pin_in")
            with col_serial:
                st.text_input("Serial", value="CCM-HN-2026", disabled=True, key="serial_in")

            emp_in = st.text_input("EMPRESA", value="Centro de Cerámicas y Más", disabled=True, key="emp_in")
            agencia_in = st.selectbox("AGENCIA", ["Agencia Principal (San Juan, Intibucá)", "Bodega Operativa China (CHILAT Guangzhou)", "Recepción Puerto Cortés"], key="ag_in")

            col_u, col_p = st.columns(2)
            with col_u:
                u_ident = st.text_input("USUARIO (Casillero / Correo)", placeholder="Ej: 13011998 o correo@gmail.com", key="log_cas")
            with col_p:
                u_pass = st.text_input("CONTRASEÑA", type="password", placeholder="Contraseña", key="log_pwd")

            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)
            
            st.markdown('<div class="btn-psk-green">', unsafe_allow_html=True)
            if st.button("➔] INICIAR SESIÓN", type="primary", key="btn_login_submit"):
                if u_ident and u_pass:
                    p_hash = hash_pwd(u_pass)
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("SELECT id, codigo_casillero, nombre_completo, correo_principal, rol, activo, telefono_principal, ciudad FROM usuarios WHERE (correo_principal = ? OR codigo_casillero = ?) AND password_hash = ?", (u_ident, u_ident, p_hash))
                        user = c.fetchone()
                        
                    if user:
                        if user[5] == 0:
                            st.error("⛔ Cuenta inactiva. Contacte al soporte.")
                        else:
                            st.session_state["autenticado"] = True
                            st.session_state["casillero"] = user[1]
                            st.session_state["nombre"] = user[2]
                            st.session_state["usuario"] = user[3]
                            st.session_state["rol"] = user[4]
                            st.session_state["telefono"] = user[6]
                            st.session_state["ciudad"] = user[7]
                            st.session_state.pop("datos_pdf_confirmado", None)
                            st.rerun()
                    else:
                        st.error("❌ Credenciales inválidas.")
                else:
                    st.warning("Ingrese su usuario (casillero/correo) y contraseña.")
            st.markdown('</div>', unsafe_allow_html=True)

            c_bot1, c_bot2 = st.columns(2)
            with c_bot1:
                if st.button("Restablecer Contraseña", type="secondary", key="btn_to_reset"):
                    st.session_state["vista_actual"] = "recuperar"
                    st.rerun()
            with c_bot2:
                if st.button("Aperturar Casillero", type="secondary", key="btn_to_register"):
                    st.session_state["vista_actual"] = "registro"
                    st.rerun()

            st.markdown("""
            <div style="background: #0f172a; color: #94a3b8; font-size: 0.68rem; text-align: center; padding: 14px 18px; border-radius: 0 0 12px 12px; margin-top: 15px; line-height: 1.4;">
                Este software está protegido por la ley de derecho de autor. Todos los derechos morales, intelectuales, de explotación, así como la marca y logotipo son propiedad exclusiva del fabricante.<br>
                <b>Copyright 1999 - 2026 By Nodgard Seguias / Grupo Premium Soft &bull; Centro de Cerámicas y Más &bull; Versión del sistema: 3.0</b>
            </div>
            """, unsafe_allow_html=True)

    elif st.session_state["vista_actual"] == "registro":
        _, col_reg, _ = st.columns([1, 1.8, 1])
        with col_reg:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("### 📋 Apertura de Casillero en China — Centro de Cerámicas y Más")
            paso = st.session_state["reg_paso"]
            st.progress(paso / 4.0, text=f"Paso {paso} de 4")

            if paso == 1:
                nom = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nom", ""))
                dni = st.text_input("Número de Identidad (DNI - 13 dígitos) *", value=st.session_state["reg_datos"].get("dni", ""), placeholder="Ej: 1301199800990")
                if dni:
                    st.caption(f"ℹ️ Su casillero asignado será: **{generar_codigo_casillero_dni(dni)}** (Primeros 8 dígitos de su ID)")
                if st.button("Siguiente ➔", type="primary"):
                    if nom and dni and len(''.join(filter(str.isdigit, dni))) >= 8:
                        st.session_state["reg_datos"].update({"nom": nom, "dni": dni})
                        st.session_state["reg_paso"] = 2
                        st.rerun()
                    else:
                        st.error("Ingrese un DNI válido con al menos 8 dígitos.")

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
                dep = st.selectbox("Departamento *", ["Intibucá", "Cortés", "Francisco Morazán", "Comayagua", "Copán", "Atlántida", "Choluteca", "Lempira", "Santa Bárbara", "Yoro", "La Paz"])
                ciu = st.text_input("Municipio / Ciudad *", value=st.session_state["reg_datos"].get("ciu", ""))
                dir_e = st.text_area("Dirección Exacta de Entrega *", value=st.session_state["reg_datos"].get("dir", ""))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("⬅️ Atrás", type="secondary"):
                        st.session_state["reg_paso"] = 2
                        st.rerun()
                with c2:
                    if st.button("Siguiente ➔", type="primary"):
                        if ciu and dir_e:
                            st.session_state["reg_datos"].update({"dep": dep, "ciu": ciu, "dir": dir_e})
                            st.session_state["reg_paso"] = 4
                            st.rerun()
                        else:
                            st.error("Complete la dirección.")

            elif paso == 4:
                rub = st.selectbox("Rubro Principal", ["Ferretería & Construcción", "Cerámica & Acabados", "Electrónica", "Ropa & Calzado", "Repuestos", "General"])
                mod = st.radio("Modalidad de Entrega", ["Retiro en Bodega Central (San Juan, Intibucá)", "Envío con Forza a Domicilio"])
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
                        f_crea = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        with get_db() as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT codigo_casillero FROM usuarios WHERE correo_principal = ? OR dni = ? OR codigo_casillero = ?", (d["cor"], d["dni"], n_cod))
                            if cur.fetchone():
                                url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote("Hola, necesito asistencia con mi casillero ya registrado.")
                                st.warning("⚠️ Ya existe un casillero registrado con este DNI o correo.")
                                st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:10px; border-radius:8px; width:100%; font-weight:bold; cursor:pointer;">📲 Consultar por WhatsApp (+504 9577-1099)</button></a>', unsafe_allow_html=True)
                            else:
                                cur.execute("INSERT INTO usuarios (codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)", (n_cod, d["nom"], d["dni"], d["cor"], d["tel"], d["dep"], d["ciu"], d["dir"], rub, mod, hash_pwd(n_pwd), f_crea))
                                conn.commit()
                                st.success("🎉 ¡Casillero Creado!")
                                st.info(f"🔑 **Casillero:** `{n_cod}` | 🔒 **Contraseña:** `{n_pwd}`")
                                st.session_state["reg_paso"] = 1
                                st.session_state["reg_datos"] = {}

            if st.button("Volver al Login", type="secondary"):
                st.session_state["vista_actual"] = "login"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state["vista_actual"] == "recuperar":
        _, col_rec, _ = st.columns([1, 1.25, 1])
        with col_rec:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
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
            st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 7. PORTAL DEL CLIENTE (SISTEMA INTERNO CON MENÚ DESPLEGABLE)
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    casillero = st.session_state["casillero"]
    nombre_cli = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "Honduras")

    # ENCABEZADO SUPERIOR
    st.markdown(f"""
    <div style="background:{input_bg}; padding:1rem 1.2rem; border-radius:12px; border:1px solid {input_border}; display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
        <div>
            <h3 style="margin:0; color:#0052cc;">🏠 CENTRO DE CERÁMICAS Y MÁS &bull; Casillero {casillero}</h3>
            <div style="font-size:0.85rem; color:{text_muted};">Titular: {nombre_cli} &bull; {ciu_cli}</div>
        </div>
        <div style="background:#0052cc; color:white; padding:4px 12px; border-radius:20px; font-weight:bold; font-size:0.8rem;">🟢 Casillero Activo</div>
    </div>
    """, unsafe_allow_html=True)

    # MENÚ DESPLEGABLE DE NAVEGACIÓN SUPERIOR
    col_menu, col_logout = st.columns([4, 1])
    with col_menu:
        opciones_navegacion = [
            "📦 Mis Envíos",
            "📐 Cotizador Marítimo",
            "📍 Etiqueta & Ficha de Envío (PDF)"
        ]
        
        idx_default = opciones_navegacion.index(st.session_state["seccion_activa"]) if st.session_state["seccion_activa"] in opciones_navegacion else 1

        seccion_seleccionada = st.selectbox(
            "☰ Menú de Navegación:",
            opciones_navegacion,
            index=idx_default,
            key="sb_navegacion_superior"
        )

        if seccion_seleccionada != st.session_state["seccion_activa"]:
            st.session_state["seccion_activa"] = seccion_seleccionada
            st.rerun()

    with col_logout:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("🚪 Cerrar Sesión", type="secondary", key="btn_logout_top"):
            logout()

    st.markdown("<hr style='margin: 10px 0 20px 0; border: 0.5px solid #26354a;'>", unsafe_allow_html=True)

    # -----------------------------------------------------
    # VISTA 1: MIS ENVÍOS
    # -----------------------------------------------------
    if st.session_state["seccion_activa"] == "📦 Mis Envíos":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📦 Mis Envíos & Paquetes en Travesía")
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?", (casillero,))
            paquetes = c.fetchall()

        if paquetes:
            for p in paquetes:
                st.markdown(f"""
                <div style="background:{'#161e2e' if is_dark else '#f8fafc'}; border:1px solid {input_border}; border-radius:10px; padding:15px; margin-bottom:10px;">
                    <b>Tracking:</b> {p[0]} | <b>Contenedor:</b> {p[2]}<br>
                    <b>Estado:</b> <span style="color:#0052cc; font-weight:bold;">{p[3]}</span><br>
                    <small style="color:{text_muted};">Actualizado: {p[4]}</small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No tienes paquetes registrados en tránsito en este momento.")
        st.markdown('</div>', unsafe_allow_html=True)

    # -----------------------------------------------------
    # VISTA 2: COTIZADOR MARÍTIMO
    # -----------------------------------------------------
    elif st.session_state["seccion_activa"] == "📐 Cotizador Marítimo":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📐 Cotizador Flete Marítimo China ➔ Honduras")
        
        t_lb = get_tarifa("tarifa_libra")       # $3.50
        t_m3 = get_tarifa("tarifa_m3")           # $680.00
        min_usd = get_tarifa("minimo_cobro_usd") # $10.00

        tipo_carga = st.selectbox(
            "Seleccione la Modalidad de Carga:",
            [
                "📦 Paquetería Menor (Menos de 100 lbs / 1 a 45 kg)",
                "🚢 Carga Comercial por Metro Cúbico (100 lbs o más / 45 a 390 kg)"
            ],
            index=0,
            key="sb_tipo_carga_cot"
        )

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

        if "Paquetería Menor" in tipo_carga:
            st.caption("Ingrese las dimensiones y peso real del paquete menor (1 a 99 lbs):")
            c1, c2, c3, c4 = st.columns(4)
            with c1: al_val = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0, key="al_menor")
            with c2: an_val = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0, key="an_menor")
            with c3: la_val = st.number_input("Largo (cm)", min_value=1.0, value=40.0, step=1.0, key="la_menor")
            with c4: 
                pe_lb = st.number_input("Peso Real (Libras / lb)", min_value=0.5, max_value=99.9, value=4.0, step=0.5, key="pe_menor")
                pe_kg = pe_lb / 2.20462
                st.caption(f"Equivalente a: **{pe_kg:.2f} kg**")

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147

            if pe_lb <= 3.0:
                tot = min_usd
                desc = f"Tarifa Mínima Base (1 a 3 lbs): ${min_usd:.2f} USD"
            else:
                tot = pe_lb * t_lb
                desc = f"Tarifa por Libra: {pe_lb:.1f} lbs x ${t_lb:.2f}/lb"

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            with m1: st.metric("Volumen Calculado (m³)", f"{vol_m3_val:.4f} m³", help="(Alto x Ancho x Largo en cm) / 1,000,000")
            with m2: st.metric("Equivalencia en Pies Cúbicos", f"{vol_ft3_val:.2f} ft³")
            with m3: st.metric("Total Estimado (USD)", f"${tot:.2f} USD", help="Flete marítimo e internación aduanal incluida.")

            st.info(f"📌 **Detalle:** {desc} (Aplica para paquetes de 1 a 99 lbs).")
            detalle_pdf = desc
            modalidad_pdf = "Paquetería Menor (1 a 99 lbs)"

        else:
            st.caption("Carga comercial: 1 Metro Cúbico (CBM) cubre hasta 390 kg (859.8 lbs). Cada fracción adicional de 390 kg liquida como CBM adicional.")
            c1, c2, c3, c4 = st.columns(4)
            with c1: al_val = st.number_input("Alto (cm)", min_value=1.0, value=120.0, step=1.0, key="al_com")
            with c2: an_val = st.number_input("Ancho (cm)", min_value=1.0, value=120.0, step=1.0, key="an_com")
            with c3: la_val = st.number_input("Largo (cm)", min_value=1.0, value=120.0, step=1.0, key="la_com")
            with c4: 
                pe_lb = st.number_input(
                    "Peso Total (Libras / lb)", 
                    min_value=100.0, 
                    value=500.0, 
                    step=10.0, 
                    key="pe_com",
                    help="Conversión: 390 kg = 859.8 lbs."
                )
                pe_kg = pe_lb / 2.20462
                st.caption(f"Equivalente a: **{pe_kg:.2f} kg**")

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147

            vol_m3_peso = pe_kg / 390.0
            cbm_facturable = max(vol_m3_val, vol_m3_peso)
            tot = cbm_facturable * t_m3

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            with m1: st.metric("Volumen Físico (m³)", f"{vol_m3_val:.4f} m³", help="(Alto x Ancho x Largo en cm) / 1,000,000")
            with m2: st.metric("CBM Tasable Facturado", f"{cbm_facturable:.4f} CBM", help="Mayor entre volumen físico y relación 1 CBM = 390 kg")
            with m3: st.metric("Total Estimado (USD)", f"${tot:.2f} USD", help="Tarifa de $680.00 por CBM con desaduanaje incluido.")

            if pe_kg > 390.0:
                st.warning(f"⚖️ **Aviso de Peso Excedente:** El peso de **{pe_kg:.2f} kg ({pe_lb:.1f} lbs)** supera el límite de 390 kg por CBM, liquidándose a **{cbm_facturable:.4f} CBM**.")
            else:
                st.success(f"📌 **Cálculo aplicado:** Tarifa Comercial CBM (${t_m3:.2f}/m³).")

            detalle_pdf = f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"
            modalidad_pdf = "Carga Comercial por Metro Cúbico (CBM)"

        st.markdown("<hr style='margin: 20px 0; border: 0.5px solid #374151;'>", unsafe_allow_html=True)
        st.markdown("#### ✅ Confirmación de Tarifa & Generación de Documentos")

        if st.button("🤝 Estoy de acuerdo con la tarifa y deseo confirmar", type="primary", key="btn_confirmar_tarifa"):
            f_hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO cotizaciones (codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, volumen_ft3, total_usd, fecha)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (casillero, al_val, an_val, la_val, pe_lb, vol_m3_val, vol_ft3_val, tot, f_hoy))
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
                "id_cot": id_generado
            }
            st.success(f"🎉 ¡Tarifa Confirmada con Éxito! Número de Control: **CCM-COT-{id_generado:05d}**")

        if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
            d_pdf = st.session_state["datos_pdf_confirmado"]
            id_c = d_pdf.get("id_cot", 1)
            al_g = d_pdf.get("al", 0.0)
            an_g = d_pdf.get("an", 0.0)
            la_g = d_pdf.get("la", 0.0)
            pe_lb_g = d_pdf.get("peso_lb", 0.0)
            pe_kg_g = d_pdf.get("peso_kg", 0.0)
            vol_m3_g = d_pdf.get("vol_m3", 0.0)
            vol_ft3_g = d_pdf.get("vol_ft3", 0.0)
            tot_usd_g = d_pdf.get("total_usd", 0.0)
            det_g = d_pdf.get("detalle_tarifa", "")
            tipo_g = d_pdf.get("tipo_carga", "Carga")

            st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
            col_doc1, col_doc2 = st.columns(2, gap="large")

            with col_doc1:
                st.markdown(f"""
                <div style="background: {'#161e2e' if is_dark else '#ffffff'}; border: 2px solid #0052cc; border-radius: 12px; padding: 18px; text-align: center;">
                    <div style="font-size: 2rem;">📦🏭</div>
                    <h4 style="margin: 6px 0; color: #38bdf8;">DOCUMENTO 1: PARA EL FABRICANTE</h4>
                    <p style="font-size: 0.85rem; color: {text_muted};">
                        <b>Envíe este PDF a su proveedor en China.</b><br>
                        (Incluye casillero <b>{casillero}</b>, dimensiones <b>{al_g:.1f}x{an_g:.1f}x{la_g:.1f} cm</b> y peso. <u>Sin precios</u>).
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                pdf_fabricante_bytes = generar_pdf_etiqueta_proveedor(casillero, nombre_cli, tel_cli, ciu_cli, al_g, an_g, la_g, pe_lb_g, pe_kg_g, vol_m3_g)
                st.download_button(
                    label="📥 Descargar Etiqueta para Fabricante (PDF)",
                    data=pdf_fabricante_bytes,
                    file_name=f"Shipping_Label_Fabricante_{casillero}.pdf",
                    mime="application/pdf",
                    key="btn_dl_fab"
                )

            with col_doc2:
                texto_wa = f"Hola Centro de Cerámicas y Más, confirmo cotización CCM-COT-{id_c:05d} del casillero {casillero}. Total: ${tot_usd_g:.2f} USD."
                url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(texto_wa)

                st.markdown(f"""
                <div style="background: {'#161e2e' if is_dark else '#ffffff'}; border: 2px solid #22c55e; border-radius: 12px; padding: 18px; text-align: center;">
                    <div style="font-size: 2rem;">📲📑</div>
                    <h4 style="margin: 6px 0; color: #22c55e;">DOCUMENTO 2: PARA NUESTRO WHATSAPP</h4>
                    <p style="font-size: 0.85rem; color: {text_muted};">
                        <b>Descargue este comprobante y envíelo a nuestro WhatsApp.</b><br>
                        (Contiene control <b>CCM-COT-{id_c:05d}</b>, medidas y flete: <b>${tot_usd_g:.2f} USD</b>).
                    </p>
                </div>
                """, unsafe_allow_html=True)

                pdf_confirmacion_bytes = generar_pdf_confirmacion_cotizacion(casillero, nombre_cli, tel_cli, ciu_cli, tipo_g, al_g, an_g, la_g, pe_lb_g, pe_kg_g, vol_m3_g, vol_ft3_g, tot_usd_g, det_g, id_c)
                st.download_button(
                    label=f"📥 Descargar Comprobante con Tarifa (CCM-COT-{id_c:05d})",
                    data=pdf_confirmacion_bytes,
                    file_name=f"Comprobante_Tarifa_{casillero}_COT{id_c:05d}.pdf",
                    mime="application/pdf",
                    key="btn_dl_confirmacion"
                )

                st.markdown(f"""
                <a href="{url_wa}" target="_blank" style="text-decoration:none;">
                    <div style="background-color: #22c55e; color: white; padding: 12px 16px; border-radius: 10px; font-weight: bold; text-align: center; margin-top: 6px; font-size: 0.92rem;">
                        📲 Enviar Comprobante al WhatsApp (+504 9577-1099)
                    </div>
                </a>
                """, unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # -----------------------------------------------------
    # VISTA 3: ETIQUETA & FICHA DE ENVÍO
    # -----------------------------------------------------
    elif st.session_state["seccion_activa"] == "📍 Etiqueta & Ficha de Envío (PDF)":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 🏷️ Etiqueta de Envío Oficial para su Proveedor")
        st.write("Descargue este archivo PDF y envíeselo directamente a su proveedor en Alibaba, 1688 o Made-in-China para que lo pegue en cada caja:")

        pdf_bytes = generar_pdf_etiqueta_proveedor(casillero, nombre_cli, tel_cli, ciu_cli)

        st.download_button(
            label="📄 Descargar Etiqueta de Envío en PDF (Para Proveedor)",
            data=pdf_bytes,
            file_name=f"Shipping_Label_{casillero}.pdf",
            mime="application/pdf",
            key="btn_dl_dir_tab"
        )
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(f"""
        <div class="china-address-box">
============================================================<br>
              CENTRO DE CERÁMICAS Y MÁS — HONDURAS<br>
                  MARITIME CONSOLIDATION CARGO<br>
============================================================<br>
<strong>CLIENT CODE / CASILLERO :</strong> {casillero}<br>
<strong>CLIENT NAME / NOMBRE    :</strong> {nombre_cli}<br>
<strong>DESTINATION COUNTRY     :</strong> HONDURAS (PUERTO CORTÉS / INTIBUCÁ)<br>
------------------------------------------------------------<br>
<strong>SHIP TO / DIRECCIÓN EN CHINA (CHILAT WAREHOUSE):</strong><br>
<strong>ATTN / RECEIVER :</strong> CHILAT / {casillero}<br>
<strong>ADDRESS (EN)    :</strong> CHILAT Logistics Warehouse, District B, Port Area<br>
<strong>ADDRESS (中文)   :</strong> 广东省广州市白云区集运仓 / 转 {casillero}<br>
------------------------------------------------------------<br>
<strong>INSTRUCTIONS FOR SUPPLIER (Copiar y pegar al vendedor):</strong><br>
"Dear supplier, please ensure you paste our shipping label firmly <br>
on the exterior of each box before dispatching. Our warehouse will <br>
NOT accept packages without the Client Code: {casillero} clearly visible."<br>
============================================================
        </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------
# 8. PANEL ADMINISTRATIVO
# ---------------------------------------------------------
elif st.session_state["rol"] == "admin":
    st.markdown("## 🛠️ Panel Maestro — Administrador")
    tab_u, tab_p = st.tabs(["👥 Directorio de Clientes", "📦 Registrar Paquetes"])

    with tab_u:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT codigo_casillero, nombre_completo, correo_principal, telefono_principal, ciudad FROM usuarios WHERE rol = 'cliente'")
            st.dataframe(c.fetchall(), use_container_width=True)

    with tab_p:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        t_in = st.text_input("Tracking de China")
        c_in = st.text_input("Casillero Asignado (8 dígitos)")
        e_in = st.selectbox("Estado", ["En Bodega China", "En Travesía Marítima", "En Desaduanaje", "Disponible en Bodega Central", "Entregado"])
        if st.button("Actualizar Paquete", type="primary"):
            if t_in and c_in:
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT OR REPLACE INTO paquetes (tracking, codigo_casillero, estado, fecha_actualizacion) VALUES (?, ?, ?, ?)", (t_in, c_in, e_in, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                st.success("Paquete actualizado.")
        st.markdown('</div>', unsafe_allow_html=True)

    if st.button("Cerrar Sesión Admin", type="secondary"):
        logout()
