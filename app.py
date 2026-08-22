import streamlit as st
import sqlite3
import hashlib
import random
import string
from datetime import datetime
import io
import os
import base64
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DEL SISTEMA
# ---------------------------------------------------------
st.set_page_config(
    page_title="Centro de Cerámicas y Más — Casillero China",
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

# ---------------------------------------------------------
# 2. GENERADOR DE PDF NATIVO
# ---------------------------------------------------------
def generar_pdf_nativo(casillero, nombre, telefono, ciudad):
    stream_content = f"""BT
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
0 -18 Td
(PACKAGE DETAILS: BOX [   ] OF [   ] | WEIGHT: _______ KG | VOL: _______ CBM) Tj
0 -22 Td
(----------------------------------------------------------------) Tj
/F1 9 Tf
0 -15 Td
(INSTRUCTIONS FOR SUPPLIER [ALIBABA / MADE-IN-CHINA / 1688]:) Tj
0 -13 Td
(1. Paste this shipping label firmly on at least 2 sides of every box.) Tj
0 -12 Td
(2. Packages received without the Client Code will NOT be processed.) Tj
0 -12 Td
(3. Send domestic tracking number to the buyer immediately upon dispatch.) Tj
ET"""
    
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

# ---------------------------------------------------------
# 3. DEFINICIÓN DE COLORES & CSS CORREGIDO
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

    div[data-baseweb="input"] {{
        background-color: {input_bg} !important;
        border: 1px solid {input_border} !important;
        border-radius: 12px !important;
        overflow: hidden !important;
        padding: 4px 6px !important;
    }}
    div[data-baseweb="input"] input {{
        color: {input_color} !important;
        background-color: transparent !important;
        font-size: 0.95rem !important;
    }}
    div[data-baseweb="input"] input::placeholder {{
        color: {text_muted} !important;
        opacity: 0.7 !important;
    }}

    .stTextInput label, .stSelectbox label, .stTextArea label, .stRadio label {{
        color: {text_main} !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
        margin-bottom: 2px !important;
    }}

    div.stButton > button, div.stDownloadButton > button {{
        width: 100% !important;
        border-radius: 12px !important;
        padding: 12px 18px !important;
        font-size: 0.95rem !important;
        font-weight: 700 !important;
        transition: all 0.2s ease-in-out !important;
        margin-top: 4px !important;
        margin-bottom: 4px !important;
    }}

    div.stButton > button[kind="primary"], .btn-login-blue div.stButton > button, div.stDownloadButton > button {{
        background-color: #0052cc !important;
        color: #ffffff !important;
        border: none !important;
        box-shadow: 0 4px 14px rgba(0, 82, 204, 0.4) !important;
    }}
    div.stButton > button[kind="primary"]:hover, .btn-login-blue div.stButton > button:hover, div.stDownloadButton > button:hover {{
        background-color: #0040a8 !important;
        transform: translateY(-1px);
    }}

    div.stButton > button[kind="secondary"], .btn-action-sec div.stButton > button {{
        background-color: {btn_sec_bg} !important;
        color: {btn_sec_text} !important;
        border: 1px solid {btn_sec_border} !important;
    }}

    .theme-dropdown div[data-baseweb="select"] {{
        background-color: {input_bg} !important;
        border: 1px solid {input_border} !important;
        border-radius: 10px !important;
    }}

    .logo-container {{
        text-align: center;
        margin-top: 1rem;
        margin-bottom: 1.5rem;
    }}
    .logo-image-box {{
        width: 140px;
        height: 140px;
        margin: 0 auto;
        border-radius: 18px;
        padding: 8px;
        background-color: #ffffff;
        box-shadow: 0 6px 18px rgba(0,0,0,0.18);
        display: flex;
        align-items: center;
        justify-content: center;
    }}
    .logo-image-box img {{
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
    }}
    .brand-title {{
        font-size: 1.25rem;
        font-weight: 800;
        letter-spacing: 1px;
        color: {text_main};
        margin-top: 12px;
    }}
    .brand-subtitle {{
        font-size: 0.85rem;
        color: {text_muted};
        margin-top: 2px;
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
                'CCM-ADMIN', 'Super Administrador', '0801199000000', 'admin@ccm.hn',
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

def generar_codigo_casillero():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM usuarios WHERE rol = 'cliente' ORDER BY id DESC LIMIT 1")
        last = c.fetchone()
        next_id = 1 if not last else last[0] + 1
        return f"CCM-HN-{next_id:04d}"

def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return ''.join(random.choice(caracteres) for _ in range(8))

def render_logo_header():
    if os.path.exists(LOGO_FILENAME):
        with open(LOGO_FILENAME, "rb") as f:
            encoded_img = base64.b64encode(f.read()).decode()
        img_html = f'<img src="data:image/jpeg;base64,{encoded_img}" alt="Centro de Cerámicas y Más">'
    else:
        img_html = '<div style="font-size:3rem;">🏠</div>'

    st.markdown(f"""
    <div class="logo-container">
        <div class="logo-image-box">
            {img_html}
        </div>
        <div class="brand-title">CENTRO DE CERÁMICAS Y MÁS</div>
        <div class="brand-subtitle">Servicio de Consolidación Marítima China ➔ Honduras</div>
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
    for k in ["autenticado", "usuario", "rol", "casillero", "nombre", "telefono", "ciudad"]:
        st.session_state[k] = None
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.rerun()

# ---------------------------------------------------------
# 6. HEADER SUPERIOR CON SELECTOR DE TEMA
# ---------------------------------------------------------
col_vacia, col_theme = st.columns([4, 1.3])
with col_theme:
    st.markdown('<div class="theme-dropdown">', unsafe_allow_html=True)
    tema_elegido = st.selectbox(
        "Tema",
        ["Oscuro (Dark)", "Blanco (Light)"],
        index=0 if is_dark else 1,
        label_visibility="collapsed"
    )
    if tema_elegido != st.session_state["tema_visual"]:
        st.session_state["tema_visual"] = tema_elegido
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 7. PANTALLAS DE ACCESO
# ---------------------------------------------------------
if not st.session_state["autenticado"]:

    # 7.1 VISTA LOGIN
    if st.session_state["vista_actual"] == "login":
        _, col_center, _ = st.columns([1, 1.25, 1])
        with col_center:
            render_logo_header()

            u_ident = st.text_input("Casillero o Correo", placeholder="CCM-HN-0001 o correo@gmail.com", key="log_cas")
            u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña de acceso", key="log_pwd")

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            
            st.markdown('<div class="btn-login-blue">', unsafe_allow_html=True)
            if st.button("Iniciar sesión", type="primary", key="btn_login_submit"):
                if u_ident and u_pass:
                    p_hash = hash_pwd(u_pass)
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("""
                            SELECT id, codigo_casillero, nombre_completo, correo_principal, rol, activo, telefono_principal, ciudad 
                            FROM usuarios 
                            WHERE (correo_principal = ? OR codigo_casillero = ?) AND password_hash = ?
                        """, (u_ident, u_ident, p_hash))
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
                            st.rerun()
                    else:
                        st.error("❌ Credenciales inválidas.")
                else:
                    st.warning("Ingrese su casillero y contraseña.")
            st.markdown('</div>', unsafe_allow_html=True)

            st.markdown('<div class="btn-action-sec">', unsafe_allow_html=True)
            if st.button("Restablecer contraseña", type="secondary", key="btn_to_reset"):
                st.session_state["vista_actual"] = "recuperar"
                st.rerun()

            if st.button("Aperturar casillero", type="secondary", key="btn_to_register"):
                st.session_state["vista_actual"] = "registro"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # 7.2 VISTA REGISTRO (CON VALIDACIÓN DE DUPLICADOS Y WHATSAPP)
    elif st.session_state["vista_actual"] == "registro":
        _, col_reg, _ = st.columns([1, 1.8, 1])
        with col_reg:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("### 📋 Apertura de Casillero en China — Centro de Cerámicas y Más")
            
            paso = st.session_state["reg_paso"]
            st.progress(paso / 4.0, text=f"Paso {paso} de 4")

            if paso == 1:
                st.markdown("#### 1. Datos Personales")
                nom = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nom", ""))
                dni = st.text_input("Número de Identidad (DNI) *", value=st.session_state["reg_datos"].get("dni", ""))
                if st.button("Siguiente ➔", type="primary"):
                    if nom and dni:
                        st.session_state["reg_datos"].update({"nom": nom, "dni": dni})
                        st.session_state["reg_paso"] = 2
                        st.rerun()
                    else:
                        st.error("Complete los campos obligatorios (*).")

            elif paso == 2:
                st.markdown("#### 2. Contacto")
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
                            st.error("Ingrese correo y teléfono.")

            elif paso == 3:
                st.markdown("#### 3. Dirección en Honduras")
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
                            st.error("Llene la dirección completa.")

            elif paso == 4:
                st.markdown("#### 4. Preferencias y Confirmación")
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
                        n_cod = generar_codigo_casillero()
                        n_pwd = generar_clave_provisional()
                        f_crea = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        try:
                            with get_db() as conn:
                                cur = conn.cursor()
                                
                                cur.execute("""
                                    SELECT codigo_casillero, correo_principal, dni 
                                    FROM usuarios 
                                    WHERE correo_principal = ? OR dni = ?
                                """, (d["cor"], d["dni"]))
                                usuario_existente = cur.fetchone()

                                if usuario_existente:
                                    msg_wa = f"Hola Centro de Cerámicas y Más, intenté aperturar mi casillero pero me indica que mis datos (DNI: {d['dni']} / Correo: {d['cor']}) ya están registrados. Necesito ayuda."
                                    url_wa = f"https://wa.me/50495771099?text={urllib.parse.quote(msg_wa)}"
                                    
                                    st.warning("⚠️ **Estimado cliente:** Ya existe una cuenta de casillero registrada con este correo electrónico o número de identidad (DNI).")
                                    
                                    st.markdown(f"""
                                    <div style="background: rgba(34, 197, 94, 0.1); border: 1px solid #22c55e; border-radius: 10px; padding: 15px; text-align: center; margin-top: 10px; margin-bottom: 15px;">
                                        <p style="margin: 0 0 10px 0; font-size: 0.9rem; color: {'#ffffff' if is_dark else '#0f172a'};">
                                            Para mayor información o consultar el acceso a su casillero, por favor contáctenos directamente:
                                        </p>
                                        <a href="{url_wa}" target="_blank" style="text-decoration: none;">
                                            <div style="background-color: #22c55e; color: white; padding: 10px 18px; border-radius: 8px; font-weight: bold; display: inline-block; font-size: 0.9rem;">
                                                📲 Escribir por WhatsApp al +504 9577-1099
                                            </div>
                                        </a>
                                    </div>
                                    """, unsafe_allow_html=True)
                                else:
                                    cur.execute("""
                                        INSERT INTO usuarios (
                                            codigo_casillero, nombre_completo, dni, correo_principal,
                                            telefono_principal, departamento, ciudad, direccion_exacta,
                                            rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)
                                    """, (
                                        n_cod, d["nom"], d["dni"], d["cor"], 
                                        d["tel"], d["dep"], d["ciu"], d["dir"], 
                                        rub, mod, hash_pwd(n_pwd), f_crea
                                    ))
                                    conn.commit()

                                    st.balloons()
                                    st.success("🎉 ¡Casillero Creado Exitosamente!")
                                    st.info(f"🔑 **Casillero Asignado:** `{n_cod}`\n\n🔒 **Contraseña Temporal:** `{n_pwd}`\n\n*Guarde estos datos para iniciar sesión.*")
                                    
                                    st.session_state["reg_paso"] = 1
                                    st.session_state["reg_datos"] = {}

                        except sqlite3.IntegrityError:
                            msg_wa = f"Hola, tuve un problema al crear mi casillero. Mi DNI es {d.get('dni', '')}."
                            url_wa = f"https://wa.me/50495771099?text={urllib.parse.quote(msg_wa)}"
                            st.error("⚠️ El casillero o documento ya se encuentra registrado.")
                            st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:10px; border-radius:8px; width:100%; font-weight:bold; cursor:pointer;">📲 Consultar por WhatsApp (+504 9577-1099)</button></a>', unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"❌ Ocurrió un error inesperado: {e}")

            st.markdown("<div style='margin-top:15px;'></div>", unsafe_allow_html=True)
            if st.button("Volver al inicio de sesión", type="secondary"):
                st.session_state["vista_actual"] = "login"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # 7.3 VISTA RECUPERAR
    elif st.session_state["vista_actual"] == "recuperar":
        _, col_rec, _ = st.columns([1, 1.25, 1])
        with col_rec:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("### 🔄 Restablecer Contraseña")
            r_mail = st.text_input("Correo Registrado")
            if st.button("Generar Nueva Contraseña", type="primary"):
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("SELECT id, codigo_casillero FROM usuarios WHERE correo_principal = ?", (r_mail,))
                    u = c.fetchone()
                if u:
                    nueva_p = generar_clave_provisional()
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(nueva_p), u[0]))
                    st.success(f"✅ Nueva clave provisional generada: **{nueva_p}**")
                else:
                    st.error("Correo no registrado.")

            if st.button("Volver al Login", type="secondary"):
                st.session_state["vista_actual"] = "login"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 8. PORTAL DEL CLIENTE
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    casillero = st.session_state["casillero"]
    nombre_cli = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 0000-0000")
    ciu_cli = st.session_state.get("ciudad", "Honduras")

    st.markdown(f"""
    <div style="background:{input_bg}; padding:1.2rem; border-radius:12px; border:1px solid {input_border}; display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
        <div>
            <h3 style="margin:0; color:#0052cc;">🏠 CENTRO DE CERÁMICAS Y MÁS &bull; {casillero}</h3>
            <div style="font-size:0.85rem; color:{text_muted};">Titular: {nombre_cli}</div>
        </div>
        <div style="background:#0052cc; color:white; padding:4px 12px; border-radius:20px; font-weight:bold; font-size:0.8rem;">🟢 Casillero Activo</div>
    </div>
    """, unsafe_allow_html=True)

    tab_cargas, tab_cotizador, tab_direccion = st.tabs(["📦 Mis Envíos", "📐 Cotizador Marítimo", "📍 Etiqueta & Ficha de Envío (PDF)"])

    with tab_cargas:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?", (casillero,))
            paquetes = c.fetchall()

        if paquetes:
            for p in paquetes:
                st.markdown(f"""
                <div class="card-box">
                    <b>Tracking:</b> {p[0]} | <b>Contenedor:</b> {p[2]}<br>
                    <b>Estado:</b> <span style="color:#0052cc; font-weight:bold;">{p[3]}</span><br>
                    <small style="color:{text_muted};">Actualizado: {p[4]}</small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No tienes paquetes registrados en tránsito en este momento.")

    with tab_cotizador:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
        
        c1, c2, c3, c4 = st.columns(4)
        with c1: al = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0)
        with c2: an = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0)
        with c3: la = st.number_input("Largo (cm)", min_value=1.0, value=40.0, step=1.0)
        with c4: pe = st.number_input("Peso (lb)", min_value=0.5, value=10.0, step=0.5)

        vol_m3 = (al * an * la) / 1_000_000.0
        vol_ft3 = vol_m3 * 35.3147
        
        t_lb = get_tarifa("tarifa_libra")
        t_m3 = get_tarifa("tarifa_m3")
        min_usd = get_tarifa("minimo_cobro_usd")

        if pe <= 3.0:
            tot = min_usd
            modalidad = f"Tarifa Mínima Fija (1-3 lbs): ${min_usd:.2f} USD"
        elif pe < 100.0 and vol_m3 < 0.5:
            tot = pe * t_lb
            modalidad = f"Cobro por Libra ({pe:.1f} lbs @ ${t_lb:.2f}/lb)"
        else:
            costo_cbm = vol_m3 * t_m3
            costo_peso_cbm = (pe / 880.0) * t_m3
            tot = max(costo_cbm, costo_peso_cbm)
            modalidad = f"Tarifa Comercial por CBM (${t_m3:.2f}/m³)"

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric(
                label="Volumen en Metros Cúbicos", 
                value=f"{vol_m3:.4f} m³", 
                help="Fórmula: (Alto x Ancho x Largo en cm) / 1,000,000"
            )
        with m2:
            st.metric(
                label="Equivalencia en Pies Cúbicos", 
                value=f"{vol_ft3:.2f} ft³"
            )
        with m3:
            st.metric(
                label="Total Estimado (USD)", 
                value=f"${tot:.2f} USD", 
                help="Incluye flete marítimo, desaduanaje e impuestos."
            )

        st.info(f"📌 **Cálculo aplicado:** {modalidad}")
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_direccion:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 🏷️ Etiqueta de Envío Oficial para su Proveedor")
        st.write("Descargue este archivo PDF y envíeselo directamente a su proveedor en Alibaba, 1688 o Made-in-China para que lo pegue en cada caja:")

        pdf_bytes = generar_pdf_nativo(casillero, nombre_cli, tel_cli, ciu_cli)

        st.download_button(
            label="📄 Descargar Etiqueta de Envío en PDF (Para Proveedor)",
            data=pdf_bytes,
            file_name=f"Shipping_Label_{casillero}.pdf",
            mime="application/pdf"
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

        if st.button("🚪 Cerrar Sesión", type="secondary"):
            logout()

# ---------------------------------------------------------
# 9. PANEL ADMINISTRATIVO
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
        c_in = st.text_input("Casillero Asignado (CCM-HN-XXXX)")
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
