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
    page_title="Centro de Cerámicas y Más — App",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "ccm_maritime_enterprise.db"
LOGO_FILENAME = "logo centro y mas.jpg"

if "vista_actual" not in st.session_state:
    st.session_state["vista_actual"] = "login"

if "tab_app" not in st.session_state:
    st.session_state["tab_app"] = "Inicio"

if "sub_tab_inicio" not in st.session_state:
    st.session_state["sub_tab_inicio"] = "Cotizador"

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
# 3. BASE DE DATOS SQLITE & UTILIDADES
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
# 4. GESTIÓN DE SESIÓN
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
# 5. ESTILOS CSS ESTILO APLICACIÓN MÓVIL EXACTA
# ---------------------------------------------------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@700&display=swap');

    .stApp {
        background-color: #f8fafc !important;
        color: #0f172a !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
    }
    
    #MainMenu, header, footer {visibility: hidden;}

    /* CONTENEDOR MÓVIL CENTRAL */
    .block-container {
        max-width: 480px !important;
        padding-top: 0rem !important;
        padding-bottom: 5rem !important;
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
        margin: 0 auto !important;
    }

    /* HEADER AZUL SUPERIOR */
    .app-header-blue {
        background-color: #004ac1;
        padding: 18px 16px 14px 16px;
        border-radius: 0 0 20px 20px;
        color: #ffffff;
        margin: -1rem -0.8rem 1rem -0.8rem;
        box-shadow: 0 4px 14px rgba(0, 74, 193, 0.25);
    }
    .app-header-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
    }
    .app-greeting-title {
        font-size: 1.15rem;
        font-weight: 800;
        margin: 0;
        color: #ffffff;
    }
    .app-greeting-sub {
        font-size: 0.8rem;
        color: #bfdbfe;
        margin-top: 2px;
    }
    .app-header-logo {
        background: #ffffff;
        color: #004ac1;
        font-size: 1.4rem;
        font-weight: 900;
        width: 42px;
        height: 42px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    }
    .app-header-icons {
        display: flex;
        align-items: center;
        gap: 12px;
        font-size: 1.25rem;
    }

    /* BUSCADOR BLANCO */
    .app-search-bar {
        background: #ffffff;
        border-radius: 25px;
        padding: 10px 16px;
        display: flex;
        align-items: center;
        gap: 10px;
        color: #64748b;
        font-size: 0.88rem;
        margin-bottom: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }

    /* SUBHEADER DE MODALIDAD */
    .app-delivery-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        color: #ffffff;
        font-size: 0.82rem;
        padding-top: 4px;
    }
    .app-delivery-left {
        display: flex;
        align-items: center;
        gap: 8px;
        font-weight: 700;
    }

    /* BANNER PUBLICITARIO VIVE LIGERO */
    .app-banner-card {
        background: linear-gradient(135deg, #1e293b, #0f172a);
        border-radius: 16px;
        padding: 18px;
        color: #ffffff;
        margin-bottom: 1.2rem;
        box-shadow: 0 6px 18px rgba(0,0,0,0.12);
        position: relative;
        overflow: hidden;
    }
    .app-banner-tag {
        background: #ec4899;
        color: #ffffff;
        font-size: 0.72rem;
        font-weight: 800;
        padding: 3px 8px;
        border-radius: 6px;
        display: inline-block;
        margin-bottom: 8px;
    }
    .app-banner-title {
        font-size: 1.1rem;
        font-weight: 800;
        line-height: 1.3;
        margin-bottom: 6px;
    }
    .app-banner-sub {
        font-size: 0.78rem;
        color: #cbd5e1;
    }

    /* TARJETAS DE PRODUCTOS EN CUADRÍCULA */
    .app-section-title {
        font-size: 1.05rem;
        font-weight: 800;
        color: #0f172a;
        margin-bottom: 12px;
    }
    .app-product-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 12px;
        text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.04);
        margin-bottom: 12px;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    .app-product-price {
        font-size: 1.1rem;
        font-weight: 800;
        color: #0f172a;
        margin: 6px 0 2px 0;
    }
    .app-product-name {
        font-size: 0.78rem;
        color: #64748b;
        line-height: 1.25;
        height: 34px;
        overflow: hidden;
        margin-bottom: 10px;
    }

    /* CAJAS GENERALES */
    .card-box {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 1.2rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
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

    /* INPUTS */
    div[data-baseweb="input"], div[data-baseweb="select"] > div {
        background-color: #f1f5f9 !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 10px !important;
        overflow: hidden !important;
        padding: 2px 6px !important;
    }
    div[data-baseweb="input"] input {
        color: #0f172a !important;
        font-size: 0.9rem !important;
    }

    /* BOTONES */
    div.stButton > button, div.stDownloadButton > button {
        width: 100% !important;
        border-radius: 8px !important;
        padding: 10px 14px !important;
        font-size: 0.9rem !important;
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 6. PANTALLA DE ACCESO PÚBLICA (LOGIN / REGISTRO)
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    if st.session_state["vista_actual"] == "login":
        st.markdown(f"""
        <div class="app-header-blue" style="margin-bottom: 2rem; border-radius: 0 0 25px 25px;">
            <div class="app-header-row">
                <div>
                    <h2 class="app-greeting-title">Centro de Cerámicas y Más</h2>
                    <div class="app-greeting-sub">Consolidación Marítima China ➔ Honduras</div>
                </div>
                <div class="app-header-logo">🏠</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 🔐 Iniciar Sesión en su Casillero")
        u_ident = st.text_input("Número de casillero o correo", placeholder="Ej: 13011998 o correo@gmail.com", key="log_cas")
        u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña", key="log_pwd")

        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        if st.button("➔ Ingresar a mi Casillero", type="primary", key="btn_login_submit"):
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
        st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state["vista_actual"] == "registro":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📋 Apertura de Casillero en China")
        paso = st.session_state["reg_paso"]
        st.progress(paso / 4.0, text=f"Paso {paso} de 4")

        if paso == 1:
            nom = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nom", ""))
            dni = st.text_input("Número de Identidad (DNI - 13 dígitos) *", value=st.session_state["reg_datos"].get("dni", ""), placeholder="Ej: 1301199800990")
            if dni:
                st.caption(f"ℹ️ Su casillero asignado será: **{generar_codigo_casillero_dni(dni)}**")
            if st.button("Siguiente ➔", type="primary"):
                if nom and dni and len(''.join(filter(str.isdigit, dni))) >= 8:
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
# 7. PORTAL DEL CLIENTE (INTERFAZ VISUAL IDÉNTICA A LA CAPTURA)
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    casillero = st.session_state["casillero"]
    nombre_cli = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "San Juan, Intibucá")

    # --- 1. HEADER AZUL SUPERIOR (IDÉNTICO A LA CAPTURA) ---
    st.markdown(f"""
    <div class="app-header-blue">
        <div class="app-header-row">
            <div>
                <h3 class="app-greeting-title">Hola, {nombre_cli.split()[0]}</h3>
                <div class="app-greeting-sub">Tienes 21,280 puntos &bull; Casillero {casillero}</div>
            </div>
            <div class="app-header-logo">🏠</div>
            <div class="app-header-icons">
                <span style="position:relative; cursor:pointer;">
                    🛒<span style="position:absolute; top:-6px; right:-8px; background:#ef4444; color:white; font-size:0.65rem; padding:1px 5px; border-radius:10px; font-weight:800;">0</span>
                </span>
                <span style="cursor:pointer;">🔔</span>
            </div>
        </div>
        <div class="app-search-bar">
            <span>🔍</span>
            <span>Compra tus productos o cotiza fletes...</span>
        </div>
        <div class="app-delivery-row">
            <div class="app-delivery-left">
                <span>🏪</span>
                <span>¿Cómo deseas comprar?</span>
            </div>
            <div style="text-align:right;">
                <div style="font-weight:700;">Servicio a Domicilio ▾</div>
                <div style="font-size:0.7rem; opacity:0.9;">Centro de Cerámicas y Más</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- 2. BARRA DE NAVEGACIÓN SUPERIOR (TABS DE COMPRA) ---
    st.markdown("""
    <div style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; color:#475569; border-bottom:2px solid #e2e8f0; padding-bottom:8px; margin-bottom:14px; overflow-x:auto;">
        <span style="color:#004ac1; border-bottom:3px solid #004ac1; padding-bottom:8px;">Comprar |</span>
        <span>por categoría</span>
        <span>con tu casillero</span>
        <span>con tus cotizaciones</span>
    </div>
    """, unsafe_allow_html=True)

    # --- 3. BANNER PROMOCIONAL "VIVE LIGERO" ---
    st.markdown(f"""
    <div class="app-banner-card">
        <div class="app-banner-tag">¡Y YA ESTÁ DISPONIBLE!</div>
        <div class="app-banner-title">
            En el momento que sientes que cargas con <span style="color:#38bdf8;">libras extra</span> que te pesan...
        </div>
        <div class="app-banner-sub">
            ¡Te das cuenta que tienen solución con fletes marítimos desde China!<br>
            <b style="color:#ec4899; font-size:1rem; letter-spacing:1px;">VIVE LIGERO</b> &bull; Casillero asignado: <b>{casillero}</b>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- 4. SECCIÓN INTERACTIVA: SUB-PESTAÑAS DE TRABAJO ---
    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        if st.button("📐 Cotizador", type="primary" if st.session_state["sub_tab_inicio"] == "Cotizador" else "secondary"):
            st.session_state["sub_tab_inicio"] = "Cotizador"
            st.rerun()
    with col_t2:
        if st.button("📦 Mis Envíos", type="primary" if st.session_state["sub_tab_inicio"] == "Mis Envíos" else "secondary"):
            st.session_state["sub_tab_inicio"] = "Mis Envíos"
            st.rerun()
    with col_t3:
        if st.button("🏷️ Etiqueta China", type="primary" if st.session_state["sub_tab_inicio"] == "Etiqueta" else "secondary"):
            st.session_state["sub_tab_inicio"] = "Etiqueta"
            st.rerun()

    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

    # SUB-PESTAÑA 1: COTIZADOR MARÍTIMO
    if st.session_state["sub_tab_inicio"] == "Cotizador":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
        
        t_lb = get_tarifa("tarifa_libra")       # $3.50
        t_m3 = get_tarifa("tarifa_m3")           # $680.00
        min_usd = get_tarifa("minimo_cobro_usd") # $10.00

        tipo_carga = st.selectbox(
            "Modalidad de Importación:",
            [
                "📦 Paquetería Menor (1 a 99 lbs)",
                "🚢 Carga Comercial por CBM (100 a 860 lbs / 390 kg)"
            ],
            index=0
        )

        if "Paquetería Menor" in tipo_carga:
            c1, c2, c3, c4 = st.columns(4)
            with c1: al_val = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0)
            with c2: an_val = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0)
            with c3: la_val = st.number_input("Largo (cm)", min_value=1.0, value=40.0, step=1.0)
            with c4: 
                pe_lb = st.number_input("Peso (lb)", min_value=0.5, max_value=99.9, value=4.0, step=0.5)
                pe_kg = pe_lb / 2.20462

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147

            if pe_lb <= 3.0:
                tot = min_usd
                desc = f"Tarifa Mínima Base (1 a 3 lbs): ${min_usd:.2f} USD"
            else:
                tot = pe_lb * t_lb
                desc = f"Tarifa por Libra: {pe_lb:.1f} lbs x ${t_lb:.2f}/lb"

            st.metric("Total Estimado (USD)", f"${tot:.2f} USD", help=f"Volumen: {vol_m3_val:.4f} m³ ({vol_ft3_val:.2f} ft³)")
            modalidad_pdf = "Paquetería Menor (1 a 99 lbs)"
            detalle_pdf = desc

        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1: al_val = st.number_input("Alto (cm)", min_value=1.0, value=120.0, step=1.0)
            with c2: an_val = st.number_input("Ancho (cm)", min_value=1.0, value=120.0, step=1.0)
            with c3: la_val = st.number_input("Largo (cm)", min_value=1.0, value=120.0, step=1.0)
            with c4: 
                pe_lb = st.number_input("Peso Total (lb)", min_value=100.0, value=500.0, step=10.0)
                pe_kg = pe_lb / 2.20462

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147
            vol_m3_peso = pe_kg / 390.0
            cbm_facturable = max(vol_m3_val, vol_m3_peso)
            tot = cbm_facturable * t_m3

            st.metric("Total Estimado CBM (USD)", f"${tot:.2f} USD", help=f"Volumen tasado: {cbm_facturable:.4f} CBM")
            modalidad_pdf = "Carga Comercial por Metro Cúbico (CBM)"
            detalle_pdf = f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"

        if st.button("🤝 Confirmar Tarifa & Emitir Documentos", type="primary"):
            f_hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO cotizaciones (codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, volumen_ft3, total_usd, fecha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (casillero, al_val, an_val, la_val, pe_lb, vol_m3_val, vol_ft3_val, tot, f_hoy))
                id_generado = cur.lastrowid
            
            st.session_state["ultima_cot_id"] = id_generado
            st.session_state["datos_pdf_confirmado"] = {
                "tipo_carga": modalidad_pdf, "al": al_val, "an": an_val, "la": la_val,
                "peso_lb": pe_lb, "peso_kg": pe_kg, "vol_m3": vol_m3_val, "vol_ft3": vol_ft3_val,
                "total_usd": tot, "detalle_tarifa": detalle_pdf, "id_cot": id_generado
            }
            st.success(f"🎉 Cotización CCM-COT-{id_generado:05d} confirmada.")

        if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
            d_pdf = st.session_state["datos_pdf_confirmado"]
            id_c = d_pdf.get("id_cot", 1)
            
            pdf_fab = generar_pdf_etiqueta_proveedor(casillero, nombre_cli, tel_cli, ciu_cli, d_pdf.get("al",0), d_pdf.get("an",0), d_pdf.get("la",0), d_pdf.get("peso_lb",0), d_pdf.get("peso_kg",0), d_pdf.get("vol_m3",0))
            pdf_conf = generar_pdf_confirmacion_cotizacion(casillero, nombre_cli, tel_cli, ciu_cli, d_pdf.get("tipo_carga",""), d_pdf.get("al",0), d_pdf.get("an",0), d_pdf.get("la",0), d_pdf.get("peso_lb",0), d_pdf.get("peso_kg",0), d_pdf.get("vol_m3",0), d_pdf.get("vol_ft3",0), d_pdf.get("total_usd",0), d_pdf.get("detalle_tarifa",""), id_c)
            
            c_doc1, c_doc2 = st.columns(2)
            with c_doc1:
                st.download_button("📥 PDF Fabricante", pdf_fab, f"Shipping_Label_Fabricante_{casillero}.pdf", "application/pdf")
            with c_doc2:
                st.download_button(f"📥 PDF Tarifa", pdf_conf, f"Comprobante_Tarifa_{casillero}_COT{id_c:05d}.pdf", "application/pdf")
            
            texto_wa = f"Hola Centro de Cerámicas y Más, confirmo cotización CCM-COT-{id_c:05d} del casillero {casillero}. Total: ${d_pdf.get('total_usd',0):.2f} USD."
            url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(texto_wa)
            st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:10px; border-radius:8px; width:100%; font-weight:bold; cursor:pointer; margin-top:6px;">📲 Enviar a WhatsApp (+504 9577-1099)</button></a>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # SUB-PESTAÑA 2: MIS ENVÍOS
    elif st.session_state["sub_tab_inicio"] == "Mis Envíos":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 📦 Mis Paquetes en Tránsito")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?", (casillero,))
            paquetes = c.fetchall()

        if paquetes:
            for p in paquetes:
                st.markdown(f"""
                <div style="background:#f1f5f9; border:1px solid #cbd5e1; border-radius:10px; padding:12px; margin-bottom:8px;">
                    <b>Tracking:</b> {p[0]} | <b>Contenedor:</b> {p[2]}<br>
                    <b>Estado:</b> <span style="color:#004ac1; font-weight:bold;">{p[3]}</span><br>
                    <small style="color:#64748b;">Actualizado: {p[4]}</small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No tienes paquetes registrados en travesía.")
        st.markdown('</div>', unsafe_allow_html=True)

    # SUB-PESTAÑA 3: ETIQUETA CHINA
    elif st.session_state["sub_tab_inicio"] == "Etiqueta":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 🏷️ Ficha de Envío Bodega Guangzhou")
        pdf_bytes = generar_pdf_etiqueta_proveedor(casillero, nombre_cli, tel_cli, ciu_cli)
        st.download_button("📄 Descargar Etiqueta para Proveedor (PDF)", pdf_bytes, f"Shipping_Label_{casillero}.pdf", "application/pdf")
        
        st.markdown(f"""
        <div class="china-address-box" style="margin-top:10px;">
<strong>CLIENT CODE / CASILLERO:</strong> {casillero}<br>
<strong>ATTN:</strong> CHILAT / {casillero}<br>
<strong>ADDRESS:</strong> CHILAT Logistics Warehouse, District B, Port Area, Guangzhou<br>
<strong>ADDRESS (中文):</strong> 广东省广州市白云区集运仓 / 转 {casillero}
        </div>
        """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # --- 5. PRODUCTOS RECOMENDADOS (IDÉNTICO A LA SECCIÓN INFERIOR DE LA CAPTURA) ---
    st.markdown('<div class="app-section-title">También podría interesarte</div>', unsafe_allow_html=True)
    
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.markdown("""
        <div class="app-product-card">
            <div style="font-size:2rem;">📦</div>
            <div class="app-product-price">L 289.59</div>
            <div class="app-product-name">Separadores Cerámica 2mm (Bolsa 500 pcs)</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("➕ Agregar", key="p1"):
            st.toast("✅ Añadido a cotización")
    with col_p2:
        st.markdown("""
        <div class="app-product-card">
            <div style="font-size:2rem;">🧱</div>
            <div class="app-product-price">L 588.06</div>
            <div class="app-product-name">Adhesivo Porcelanato Alto Tráfico 25 Kg</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("➕ Agregar", key="p2"):
            st.toast("✅ Añadido a cotización")
    with col_p3:
        st.markdown("""
        <div class="app-product-card">
            <div style="font-size:2rem;">🔧</div>
            <div class="app-product-price">L 282.80</div>
            <div class="app-product-name">Niveladores de Piso Kit Pro (100 clips)</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("➕ Agregar", key="p3"):
            st.toast("✅ Añadido a cotización")

    # --- 6. BARRA INFERIOR DE NAVEGACIÓN (BOTTOM NAV BAR) ---
    st.markdown("<hr style='margin:20px 0 10px 0; border:0.5px solid #e2e8f0;'>", unsafe_allow_html=True)
    c_nav1, c_nav2, c_nav3, c_nav4, c_nav5 = st.columns(5)
    with c_nav1:
        if st.button("🏠\nInicio", key="bnav1"):
            st.session_state["tab_app"] = "Inicio"
            st.rerun()
    with c_nav2:
        if st.button("🛍️\nPedidos", key="bnav2"):
            st.session_state["sub_tab_inicio"] = "Mis Envíos"
            st.rerun()
    with c_nav3:
        url_wa = "https://wa.me/50495771099"
        st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background:transparent; color:#004ac1; border:none; width:100%; font-size:0.75rem; font-weight:bold; cursor:pointer;">🆘<br>Ayuda</button></a>', unsafe_allow_html=True)
    with c_nav4:
        if st.button("🪟\nServicios", key="bnav4"):
            st.session_state["sub_tab_inicio"] = "Cotizador"
            st.rerun()
    with c_nav5:
        if st.button("👤\nSalir", key="bnav5"):
            logout()

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
