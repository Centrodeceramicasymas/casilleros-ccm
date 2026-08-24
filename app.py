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
    initial_sidebar_state="expanded"
)

DB_NAME = "ccm_maritime_enterprise.db"
LOGO_FILENAME = "logo centro y mas.jpg"

if "tema_visual" not in st.session_state:
    st.session_state["tema_visual"] = "Oscuro (Dark)"

if "vista_actual" not in st.session_state:
    st.session_state["vista_actual"] = "login"

if "modulo_activo" not in st.session_state:
    st.session_state["modulo_activo"] = "📊 Dashboard General"

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
# 3. ESTILOS CSS ESTILO PSKLOUD / SERCARGO
# ---------------------------------------------------------
is_dark = (st.session_state["tema_visual"] == "Oscuro (Dark)")

bg_body = "#0d131f" if is_dark else "#f4f6f9"
text_main = "#ffffff" if is_dark else "#0f172a"
text_muted = "#94a3b8" if is_dark else "#64748b"
card_bg = "#151e2e" if is_dark else "#ffffff"
input_bg = "#1a2538" if is_dark else "#ffffff"
input_border = "#26354a" if is_dark else "#cbd5e1"
sidebar_bg = "#111827" if is_dark else "#1e293b"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@700&display=swap');

    .stApp {{
        background-color: {bg_body} !important;
        color: {text_main} !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
    }}
    
    #MainMenu, header, footer {{visibility: hidden;}}

    [data-testid="stSidebar"] {{
        background-color: {sidebar_bg} !important;
        border-right: 1px solid #1f2937 !important;
    }}

    .psk-topbar {{
        background: {card_bg};
        border: 1px solid {input_border};
        border-radius: 12px;
        padding: 10px 18px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1.2rem;
    }}
    .psk-badge-menu {{
        background-color: #22c55e;
        color: #ffffff;
        font-weight: 800;
        padding: 4px 12px;
        border-radius: 6px;
        font-size: 0.8rem;
        display: inline-flex;
        align-items: center;
        gap: 6px;
    }}
    .psk-kpi-card {{
        background: {card_bg};
        border: 1px solid {input_border};
        border-radius: 12px;
        padding: 16px;
        display: flex;
        align-items: center;
        gap: 14px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }}
    .psk-kpi-icon {{
        width: 44px;
        height: 44px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.4rem;
    }}
    .psk-kpi-title {{
        font-size: 0.78rem;
        font-weight: 600;
        color: {text_muted};
        text-transform: uppercase;
    }}
    .psk-kpi-value {{
        font-size: 1.35rem;
        font-weight: 800;
        color: {text_main};
        margin-top: 2px;
    }}

    div[data-baseweb="input"], div[data-baseweb="select"] > div {{
        background-color: {input_bg} !important;
        border: 1px solid {input_border} !important;
        border-radius: 10px !important;
        color: {text_main} !important;
    }}
    div[data-baseweb="input"] input {{
        color: {text_main} !important;
    }}

    div.stButton > button, div.stDownloadButton > button {{
        border-radius: 10px !important;
        font-weight: 700 !important;
    }}

    .card-box {{
        background-color: {card_bg};
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

def render_logo_header():
    if os.path.exists(LOGO_FILENAME):
        with open(LOGO_FILENAME, "rb") as f:
            encoded_img = base64.b64encode(f.read()).decode()
        img_html = f'<img src="data:image/jpeg;base64,{encoded_img}" alt="Centro de Cerámicas y Más" style="max-width:100%; max-height:100%; object-fit:contain;">'
    else:
        img_html = '<div style="font-size:3rem;">🏠</div>'

    st.markdown(f"""
    <div style="text-align: center; margin-top: 1rem; margin-bottom: 1.5rem;">
        <div style="width: 130px; height: 130px; margin: 0 auto; border-radius: 18px; padding: 8px; background-color: #ffffff; box-shadow: 0 6px 18px rgba(0,0,0,0.18); display: flex; align-items: center; justify-content: center;">
            {img_html}
        </div>
        <div style="font-size: 1.25rem; font-weight: 800; letter-spacing: 1px; color: {text_main}; margin-top: 12px;">CENTRO DE CERÁMICAS Y MÁS</div>
        <div style="font-size: 0.85rem; color: {text_muted}; margin-top: 2px;">Sistema Administrativo y Logístico China ➔ Honduras</div>
    </div>
    """, unsafe_allow_html=True)

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
# 6. PANTALLAS DE ACCESO (LOGIN / REGISTRO / RECUPERACIÓN)
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    col_vacia, col_theme = st.columns([4, 1.3])
    with col_theme:
        tema_elegido = st.selectbox("Tema", ["Oscuro (Dark)", "Blanco (Light)"], index=0 if is_dark else 1, label_visibility="collapsed")
        if tema_elegido != st.session_state["tema_visual"]:
            st.session_state["tema_visual"] = tema_elegido
            st.rerun()

    if st.session_state["vista_actual"] == "login":
        _, col_center, _ = st.columns([1, 1.25, 1])
        with col_center:
            render_logo_header()
            u_ident = st.text_input("Casillero (8 dígitos) o Correo", placeholder="Ej: 13011998 o correo@gmail.com", key="log_cas")
            u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña de acceso", key="log_pwd")

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            if st.button("Iniciar sesión", type="primary", key="btn_login_submit"):
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
                    st.warning("Ingrese su casillero y contraseña.")

            if st.button("Restablecer contraseña", type="secondary", key="btn_to_reset"):
                st.session_state["vista_actual"] = "recuperar"
                st.rerun()

            if st.button("Aperturar casillero", type="secondary", key="btn_to_register"):
                st.session_state["vista_actual"] = "registro"
                st.rerun()

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
                                url_wa = f"https://wa.me/50495771099?text={urllib.parse.quote('Hola, necesito asistencia con mi casillero ya registrado.')}"
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
# 7. ENTORNO EMPRESARIAL AUTENTICADO (SIDEBAR + TOPBAR + MÓDULOS)
# ---------------------------------------------------------
else:
    casillero = st.session_state["casillero"]
    nombre_cli = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "Honduras")
    rol_user = st.session_state["rol"]

    # --- SIDEBAR PROFESIONAL ESTILO PSKLOUD ---
    with st.sidebar:
        st.markdown(f"""
        <div style="text-align:center; padding: 10px 0; border-bottom: 1px solid #26354a; margin-bottom: 15px;">
            <div style="font-size: 1.4rem; font-weight: 800; color: #38bdf8; letter-spacing: 1px;">☁️ PSKLOUD CCM</div>
            <div style="font-size: 0.72rem; color: #94a3b8;">SISTEMA 3.0 &bull; PIN 51127</div>
            <div style="font-size: 0.8rem; font-weight: 700; color: #ffffff; margin-top: 6px;">👤 {nombre_cli}</div>
            <div style="font-size: 0.75rem; color: #22c55e;">CASILLERO: {casillero}</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<p style='font-size: 0.75rem; font-weight: 700; color: #64748b; text-transform: uppercase;'>MENÚ PRINCIPAL</p>", unsafe_allow_html=True)
        
        lista_modulos = [
            "📊 Dashboard General",
            "📐 Cotizador & Tarifas Marítimas",
            "📄 Documentos de Ventas & Facturas",
            "📦 Seguimiento de Paquetes & Tracking",
            "📍 Dirección en China & Etiquetas",
            "💰 Caja Chica & Pagos",
            "⚙️ Configuración del Sistema"
        ]

        st.session_state["modulo_activo"] = st.radio(
            "Navegación:",
            lista_modulos,
            index=lista_modulos.index(st.session_state["modulo_activo"]) if st.session_state["modulo_activo"] in lista_modulos else 0,
            label_visibility="collapsed"
        )

        st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
        if st.button("🚪 Cerrar Sesión", type="secondary"):
            logout()

    # --- TOPBAR SUPERIOR (BARRA HORIZONTAL CON MENÚ DESPLEGABLE) ---
    col_nav_menu, col_nav_space, col_nav_user = st.columns([2.5, 3, 2.5])
    with col_nav_menu:
        st.markdown(f"""
        <div style="display: flex; align-items: center; gap: 10px;">
            <span class="psk-badge-menu">☰ MENÚ</span>
            <span style="font-weight: 700; font-size: 0.95rem; color: {text_main};">{st.session_state['modulo_activo']}</span>
        </div>
        """, unsafe_allow_html=True)

    with col_nav_user:
        st.markdown(f"""
        <div style="text-align: right; font-size: 0.85rem; color: {text_muted};">
            <span style="background: #22c55e; color: white; padding: 2px 8px; border-radius: 12px; font-weight: bold; font-size: 0.75rem;">🟢 En Línea</span>
            <b>{casillero}</b> &bull; San Juan, Intibucá
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr style='margin: 10px 0 20px 0; border: 0.5px solid #26354a;'>", unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 1: DASHBOARD GENERAL (TARJETAS KPI Y GRÁFICOS)
    # ---------------------------------------------------------
    if st.session_state["modulo_activo"] == "📊 Dashboard General":
        # Métricas KPI estilo PSKLOUD
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(f"""
            <div class="psk-kpi-card">
                <div class="psk-kpi-icon" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8;">📄</div>
                <div>
                    <div class="psk-kpi-title">Facturas del Mes</div>
                    <div class="psk-kpi-value">L 602,906.01</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with k2:
            st.markdown(f"""
            <div class="psk-kpi-card">
                <div class="psk-kpi-icon" style="background: rgba(34, 197, 94, 0.15); color: #22c55e;">📑</div>
                <div>
                    <div class="psk-kpi-title">Cotizaciones del Mes</div>
                    <div class="psk-kpi-value">L 894,018.95</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with k3:
            st.markdown(f"""
            <div class="psk-kpi-card">
                <div class="psk-kpi-icon" style="background: rgba(168, 85, 247, 0.15); color: #a855f7;">👥</div>
                <div>
                    <div class="psk-kpi-title">Casilleros Activos</div>
                    <div class="psk-kpi-value">128</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with k4:
            st.markdown(f"""
            <div class="psk-kpi-card">
                <div class="psk-kpi-icon" style="background: rgba(249, 115, 22, 0.15); color: #f97316;">🚢</div>
                <div>
                    <div class="psk-kpi-title">En Travesía China</div>
                    <div class="psk-kpi-value">3 Contenedores</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)

        # Gráficos simulados de ventas y consolidación
        col_g1, col_g2 = st.columns([1.5, 1])
        with col_g1:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("#### 📈 Ventas Mensuales & Importaciones Consolidadas (Neto Lempiras)")
            st.line_chart({
                "Facturación Marítima (HNL)": [720000, 880000, 680000, 380000, 920000, 740000, 760000, 602906]
            })
            st.markdown('</div>', unsafe_allow_html=True)

        with col_g2:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("#### 🏆 10 Productos Más Importados")
            st.bar_chart({
                "CBM / Volumen": {
                    "Porcelanato": 85,
                    "Separadores Cerámica": 42,
                    "Herramientas": 38,
                    "Grifería": 30,
                    "Iluminación LED": 25
                }
            })
            st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 2: COTIZADOR & TARIFAS MARÍTIMAS
    # ---------------------------------------------------------
    elif st.session_state["modulo_activo"] == "📐 Cotizador & Tarifas Marítimas":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📐 Cotizador de Flete Marítimo China ➔ Honduras")
        
        t_lb = get_tarifa("tarifa_libra")
        t_m3 = get_tarifa("tarifa_m3")
        min_usd = get_tarifa("minimo_cobro_usd")

        tipo_carga = st.selectbox(
            "Seleccione la Modalidad de Carga:",
            [
                "📦 Paquetería Menor (Menos de 100 lbs / 1 a 45 kg)",
                "🚢 Carga Comercial por Metro Cúbico (100 lbs o más / 45 a 390 kg)"
            ],
            index=0
        )

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

        if "Paquetería Menor" in tipo_carga:
            st.caption("Dimensiones y peso del paquete menor (1 a 99 lbs):")
            c1, c2, c3, c4 = st.columns(4)
            with c1: al_val = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0, key="al_m")
            with c2: an_val = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0, key="an_m")
            with c3: la_val = st.number_input("Largo (cm)", min_value=1.0, value=40.0, step=1.0, key="la_m")
            with c4: 
                pe_lb = st.number_input("Peso Real (Libras / lb)", min_value=0.5, max_value=99.9, value=4.0, step=0.5, key="pe_m")
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

            m1, m2, m3 = st.columns(3)
            with m1: st.metric("Volumen Calculado (m³)", f"{vol_m3_val:.4f} m³")
            with m2: st.metric("Equivalencia (ft³)", f"{vol_ft3_val:.2f} ft³")
            with m3: st.metric("Total Estimado (USD)", f"${tot:.2f} USD")

            st.info(f"📌 **Detalle:** {desc}")
            modalidad_pdf = "Paquetería Menor (1 a 99 lbs)"
            detalle_pdf = desc

        else:
            st.caption("Carga comercial: 1 CBM cubre hasta 390 kg (859.8 lbs).")
            c1, c2, c3, c4 = st.columns(4)
            with c1: al_val = st.number_input("Alto (cm)", min_value=1.0, value=120.0, step=1.0, key="al_c")
            with c2: an_val = st.number_input("Ancho (cm)", min_value=1.0, value=120.0, step=1.0, key="an_c")
            with c3: la_val = st.number_input("Largo (cm)", min_value=1.0, value=120.0, step=1.0, key="la_c")
            with c4: 
                pe_lb = st.number_input("Peso Total (Libras / lb)", min_value=100.0, value=500.0, step=10.0, key="pe_c")
                pe_kg = pe_lb / 2.20462
                st.caption(f"Equivalente a: **{pe_kg:.2f} kg**")

            vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
            vol_ft3_val = vol_m3_val * 35.3147
            vol_m3_peso = pe_kg / 390.0
            cbm_facturable = max(vol_m3_val, vol_m3_peso)
            tot = cbm_facturable * t_m3

            m1, m2, m3 = st.columns(3)
            with m1: st.metric("Volumen Físico (m³)", f"{vol_m3_val:.4f} m³")
            with m2: st.metric("CBM Tasable Facturado", f"{cbm_facturable:.4f} CBM")
            with m3: st.metric("Total Estimado (USD)", f"${tot:.2f} USD")

            if pe_kg > 390.0:
                st.warning(f"⚖️ Peso de **{pe_kg:.2f} kg** supera 390 kg por CBM estándar, liquidándose a **{cbm_facturable:.4f} CBM**.")
            else:
                st.success(f"📌 Tarifa Comercial CBM (${t_m3:.2f}/m³).")

            modalidad_pdf = "Carga Comercial por Metro Cúbico (CBM)"
            detalle_pdf = f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"

        st.markdown("<hr style='margin: 20px 0; border: 0.5px solid #26354a;'>", unsafe_allow_html=True)
        st.markdown("#### ✅ Confirmación de Tarifa & Generación de Documentos")

        if st.button("🤝 Estoy de acuerdo con la tarifa y deseo confirmar", type="primary"):
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
            st.success(f"🎉 ¡Tarifa Confirmada! Control: **CCM-COT-{id_generado:05d}**")

        if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
            d_pdf = st.session_state["datos_pdf_confirmado"]
            id_c = d_pdf.get("id_cot", 1)
            
            col_d1, col_d2 = st.columns(2, gap="large")
            with col_d1:
                st.markdown(f"""
                <div style="background: {input_bg}; border: 2px solid #0052cc; border-radius: 12px; padding: 18px; text-align: center;">
                    <div style="font-size: 2rem;">📦🏭</div>
                    <h4 style="margin: 6px 0; color: #38bdf8;">DOCUMENTO 1: PARA EL FABRICANTE</h4>
                    <p style="font-size: 0.85rem; color: {text_muted};">
                        Envíe este PDF a su proveedor en China.<br>
                        (Incluye casillero <b>{casillero}</b> y medidas. <u>Sin precios ni tarifas</u>).
                    </p>
                </div>
                """, unsafe_allow_html=True)
                pdf_fab = generar_pdf_etiqueta_proveedor(casillero, nombre_cli, tel_cli, ciu_cli, d_pdf.get("al",0), d_pdf.get("an",0), d_pdf.get("la",0), d_pdf.get("peso_lb",0), d_pdf.get("peso_kg",0), d_pdf.get("vol_m3",0))
                st.download_button("📥 Descargar Etiqueta Fabricante (PDF)", pdf_fab, f"Shipping_Label_Fabricante_{casillero}.pdf", "application/pdf")

            with col_d2:
                url_wa = f"https://wa.me/50495771099?text={urllib.parse.quote(f'Hola CCM, confirmo cotización CCM-COT-{id_c:05d} del casillero {casillero}. Total: ${d_pdf.get(\"total_usd\",0):.2f} USD.')}"
                st.markdown(f"""
                <div style="background: {input_bg}; border: 2px solid #22c55e; border-radius: 12px; padding: 18px; text-align: center;">
                    <div style="font-size: 2rem;">📲📑</div>
                    <h4 style="margin: 6px 0; color: #22c55e;">DOCUMENTO 2: PARA NUESTRO WHATSAPP</h4>
                    <p style="font-size: 0.85rem; color: {text_muted};">
                        Descargue y envíe este comprobante con desglose de flete (<b>${d_pdf.get('total_usd',0):.2f} USD</b>).
                    </p>
                </div>
                """, unsafe_allow_html=True)
                pdf_conf = generar_pdf_confirmacion_cotizacion(casillero, nombre_cli, tel_cli, ciu_cli, d_pdf.get("tipo_carga",""), d_pdf.get("al",0), d_pdf.get("an",0), d_pdf.get("la",0), d_pdf.get("peso_lb",0), d_pdf.get("peso_kg",0), d_pdf.get("vol_m3",0), d_pdf.get("vol_ft3",0), d_pdf.get("total_usd",0), d_pdf.get("detalle_tarifa",""), id_c)
                st.download_button(f"📥 Descargar Comprobante con Tarifa (CCM-COT-{id_c:05d})", pdf_conf, f"Comprobante_Tarifa_{casillero}_COT{id_c:05d}.pdf", "application/pdf")
                st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:12px; border-radius:10px; width:100%; font-weight:bold; cursor:pointer; margin-top:6px;">📲 Enviar a WhatsApp (+504 9577-1099)</button></a>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 3: DOCUMENTOS DE VENTAS & FACTURAS
    # ---------------------------------------------------------
    elif st.session_state["modulo_activo"] == "📄 Documentos de Ventas & Facturas":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📄 Documentos de Ventas & Cotizaciones Registradas")
        st.caption("Historial de cotizaciones emitidas, facturas proforma y comprobantes autorizados:")

        with get_db() as conn:
            c = conn.cursor()
            if rol_user == "admin":
                c.execute("SELECT id, codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd, fecha FROM cotizaciones ORDER BY id DESC")
            else:
                c.execute("SELECT id, codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd, fecha FROM cotizaciones WHERE codigo_casillero = ? ORDER BY id DESC", (casillero,))
            rows = c.fetchall()

        if rows:
            st.dataframe(
                [{"No. Control": f"CCM-COT-{r[0]:05d}", "Casillero": r[1], "Dimensiones (cm)": f"{r[2]}x{r[3]}x{r[4]}", "Peso (lb)": f"{r[5]:.1f}", "Volumen (m³)": f"{r[6]:.4f}", "Total (USD)": f"${r[7]:.2f}", "Fecha": r[8]} for r in rows],
                use_container_width=True
            )
        else:
            st.info("No hay documentos de ventas ni cotizaciones registradas actualmente.")
        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 4: SEGUIMIENTO DE PAQUETES & TRACKING
    # ---------------------------------------------------------
    elif st.session_state["modulo_activo"] == "📦 Seguimiento de Paquetes & Tracking":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📦 Seguimiento de Envíos en Travesía")
        
        with get_db() as conn:
            c = conn.cursor()
            if rol_user == "admin":
                c.execute("SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes")
                paquetes = c.fetchall()
            else:
                c.execute("SELECT tracking, codigo_casillero, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?", (casillero,))
                paquetes = c.fetchall()

        if paquetes:
            for p in paquetes:
                st.markdown(f"""
                <div style="background:{input_bg}; border:1px solid {input_border}; border-radius:10px; padding:15px; margin-bottom:10px;">
                    <b>Tracking:</b> {p[0]} | <b>Casillero:</b> {p[1]} | <b>Contenedor:</b> {p[3]}<br>
                    <b>Estado Actual:</b> <span style="color:#38bdf8; font-weight:bold;">{p[4]}</span><br>
                    <small style="color:{text_muted};">Última actualización: {p[5]}</small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No hay paquetes registrados en tránsito en este momento.")

        if rol_user == "admin":
            st.markdown("<hr style='margin: 15px 0;'>", unsafe_allow_html=True)
            st.markdown("#### 🛠️ Actualizar Estado de Paquete (Admin)")
            t_in = st.text_input("Tracking de China")
            c_in = st.text_input("Casillero (8 dígitos)")
            e_in = st.selectbox("Estado", ["En Bodega China", "En Travesía Marítima", "En Desaduanaje Puerto Cortés", "Disponible en Bodega San Juan", "Entregado"])
            if st.button("Guardar Estado de Carga", type="primary"):
                if t_in and c_in:
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT OR REPLACE INTO paquetes (tracking, codigo_casillero, estado, fecha_actualizacion) VALUES (?, ?, ?, ?)", (t_in, c_in, e_in, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success("Paquete actualizado correctamente.")

        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 5: DIRECCIÓN EN CHINA & ETIQUETA
    # ---------------------------------------------------------
    elif st.session_state["modulo_activo"] == "📍 Dirección en China & Etiquetas":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 📍 Dirección Oficial de Bodega en Guangzhou, China")
        
        pdf_bytes = generar_pdf_etiqueta_proveedor(casillero, nombre_cli, tel_cli, ciu_cli)
        st.download_button("📄 Descargar Ficha de Envío Estándar (PDF)", pdf_bytes, f"Shipping_Label_{casillero}.pdf", "application/pdf")
        
        st.markdown(f"""
        <div class="china-address-box" style="margin-top:15px;">
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
<strong>INSTRUCTIONS FOR SUPPLIER:</strong><br>
"Please paste this shipping label firmly on each box before dispatching. <br>
Packages without the Client Code: {casillero} will NOT be accepted."<br>
============================================================
        </div>
        """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 6: CAJA CHICA & PAGOS
    # ---------------------------------------------------------
    elif st.session_state["modulo_activo"] == "💰 Caja Chica & Pagos":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 💰 Control de Pagos y Liquidaciones")
        st.write("Cuentas bancarias autorizadas en Honduras para liquidación de fletes marítimos:")
        st.markdown("""
        * **Banco Atlántida:** Cuenta de Cheques en Lempiras `1100-XXXX-XXXX`
        * **Banco de Occidente:** Cuenta en Lempiras `2100-XXXX-XXXX`
        * **BAC Credomatic:** Cuenta en Dólares / Lempiras `7400-XXXX-XXXX`
        """)
        st.info("Para registrar un comprobante de transferencia bancaria, envíelo directamente al WhatsApp **+504 9577-1099**.")
        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # MÓDULO 7: CONFIGURACIÓN DEL SISTEMA
    # ---------------------------------------------------------
    elif st.session_state["modulo_activo"] == "⚙️ Configuración del Sistema":
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### ⚙️ Configuración General y Tarifaria")

        if rol_user == "admin":
            st.markdown("#### Tarifas Base del Sistema")
            tar_lb_actual = get_tarifa("tarifa_libra")
            tar_m3_actual = get_tarifa("tarifa_m3")
            tar_min_actual = get_tarifa("minimo_cobro_usd")

            c_t1, c_t2, c_t3 = st.columns(3)
            with c_t1: n_t_lb = st.number_input("Tarifa por Libra (USD)", value=tar_lb_actual, step=0.25)
            with c_t2: n_t_m3 = st.number_input("Tarifa por Metro Cúbico (USD)", value=tar_m3_actual, step=10.0)
            with c_t3: n_t_min = st.number_input("Mínimo Base (USD)", value=tar_min_actual, step=1.0)

            if st.button("Actualizar Tarifas Globales", type="primary"):
                set_tarifa("tarifa_libra", n_t_lb)
                set_tarifa("tarifa_m3", n_t_m3)
                set_tarifa("minimo_cobro_usd", n_t_min)
                st.success("Tarifas actualizadas correctamente.")
        else:
            st.write(f"**Usuario:** {nombre_cli}")
            st.write(f"**Casillero Asignado:** {casillero}")
            st.write(f"**Teléfono:** {tel_cli}")
            st.write(f"**Ciudad de Entrega:** {ciu_cli}")

        st.markdown('</div>', unsafe_allow_html=True)
