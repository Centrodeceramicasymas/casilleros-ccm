import streamlit as st
import sqlite3
import hashlib
import random
import string
import csv
from datetime import datetime, date, timedelta
import io
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DEL SISTEMA & UI RESPONSIVA
# ---------------------------------------------------------
st.set_page_config(
    page_title="CCM Maritime Cloud Hub",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "ccm_maritime_enterprise.db"

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
        background-color: #f8fafc;
    }
    
    #MainMenu, header, footer {visibility: hidden;}

    /* Top Bar */
    .app-topbar {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #0369a1 100%);
        padding: 1.2rem 1.6rem;
        border-radius: 14px;
        color: white;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 15px rgba(15, 23, 42, 0.15);
    }
    .app-topbar h1 {
        margin: 0;
        font-size: 1.4rem;
        font-weight: 800;
        color: #38bdf8;
    }
    .topbar-badge {
        background: rgba(255, 255, 255, 0.12);
        padding: 6px 14px;
        border-radius: 9999px;
        font-size: 0.82rem;
        font-weight: 600;
        border: 1px solid rgba(255, 255, 255, 0.2);
    }

    /* Cards */
    .card-box {
        background: white;
        border-radius: 14px;
        padding: 1.4rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        margin-bottom: 1.2rem;
    }

    /* Metric Cards */
    .stat-card {
        background: white;
        border-radius: 12px;
        padding: 1.2rem;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #0284c7;
        box-shadow: 0 2px 5px rgba(0,0,0,0.03);
    }
    .stat-title {
        font-size: 0.78rem;
        font-weight: 700;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .stat-value {
        font-size: 1.7rem;
        font-weight: 800;
        color: #0f172a;
        margin-top: 4px;
    }

    /* Dirección Casillero China */
    .china-address-box {
        background: #f8fafc;
        border: 2px dashed #0284c7;
        border-radius: 12px;
        padding: 1.5rem;
        font-family: 'Space Mono', monospace;
        font-size: 0.9rem;
        color: #0f172a;
        position: relative;
    }

    /* QR Code Container */
    .qr-badge-box {
        background: #eff6ff;
        border: 2px solid #3b82f6;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        margin-bottom: 1rem;
    }
    .timer-alert {
        background: #fef2f2;
        border: 1px solid #f87171;
        color: #991b1b;
        padding: 8px 12px;
        border-radius: 8px;
        font-weight: 700;
        font-size: 0.85rem;
        display: inline-block;
        margin-top: 8px;
    }

    /* Botones Globales */
    div.stButton > button:first-child {
        border-radius: 8px !important;
        font-weight: 700 !important;
        padding: 0.6rem 1.4rem !important;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. MODELO DE DATOS (SQLITE)
# ---------------------------------------------------------
def hash_pwd(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        # Clientes y Usuarios
        c.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT UNIQUE NOT NULL,
                nombre_completo TEXT NOT NULL,
                dni TEXT NOT NULL,
                rtn TEXT,
                fecha_nacimiento TEXT,
                genero TEXT,
                correo_principal TEXT UNIQUE NOT NULL,
                correo_secundario TEXT,
                telefono_principal TEXT NOT NULL,
                telefono_secundario TEXT,
                departamento TEXT NOT NULL,
                ciudad TEXT NOT NULL,
                direccion_exacta TEXT NOT NULL,
                razon_social TEXT,
                rtn_facturacion TEXT,
                rubro_carga TEXT,
                segmento TEXT,
                canal_captacion TEXT,
                modalidad_entrega TEXT,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL,
                activo INTEGER DEFAULT 1,
                fecha_creacion TEXT NOT NULL
            )
        """)
        # Parámetros y Tarifas
        c.execute("""
            CREATE TABLE IF NOT EXISTS config_maritima (
                clave TEXT PRIMARY KEY,
                valor REAL NOT NULL
            )
        """)
        # Cotizaciones Marítimas
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
        # Cargas y Contenedores
        c.execute("""
            CREATE TABLE IF NOT EXISTS paquetes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking TEXT UNIQUE NOT NULL,
                codigo_casillero TEXT NOT NULL,
                descripcion TEXT,
                contenedor_id TEXT,
                estado TEXT NOT NULL,
                peso_lb REAL,
                fecha_actualizacion TEXT NOT NULL
            )
        """)
        
        # Parámetros por defecto actualizados al modelo China - Honduras
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_libra', 3.50)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_ft3', 19.25)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_m3', 680.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('minimo_cobro_usd', 10.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('recargo_aduanal_usd', 0.00)")
        
        # Superadministrador
        admin_pass = hash_pwd("admin123")
        c.execute("""
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal, 
                telefono_principal, departamento, ciudad, direccion_exacta, 
                password_hash, rol, activo, fecha_creacion
            ) VALUES (
                'CCM-ADMIN', 'Super Administrador', '0801199000000', 'admin@ccm.hn',
                '+504 9999-0000', 'Intibucá', 'San Juan', 'Oficina Principal CCM',
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
    return ''.join(random.choice(caracteres) for _ in range(9))

# ---------------------------------------------------------
# 3. NOTIFICACIONES SMTP (GMAIL)
# ---------------------------------------------------------
def enviar_credenciales_maritimas(destinatario, nombre, casillero, password_temp):
    try:
        remitente = st.secrets["EMAIL_REMITENTE"]
        password = st.secrets["EMAIL_PASSWORD"]
    except Exception:
        return False, "Credenciales no configuradas en Secrets."

    asunto = f"🚢 Apertura de Casillero Marítimo en China Exitosa: {casillero}"
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #0f172a;">
        <div style="background: #ffffff; max-width: 650px; margin: auto; padding: 25px; border-radius: 10px; border: 1px solid #e2e8f0;">
            <div style="background: #0f172a; color: #ffffff; padding: 18px; border-radius: 8px; text-align: center;">
                <h2 style="margin:0; color:#38bdf8;">CENTRO DE CERÁMICAS Y MÁS</h2>
                <p style="margin:4px 0 0 0; font-size:13px; color:#94a3b8;">Servicio Logístico & Consolidación Marítima China ➔ Honduras</p>
            </div>
            
            <p style="margin-top:20px;">Estimado(a) <strong>{nombre}</strong>,</p>
            <p>Le damos la bienvenida a nuestro servicio de importación marítima directa. Su casillero internacional ha sido activado:</p>
            
            <div style="background: #f1f5f9; padding: 14px; border-radius: 6px; margin: 15px 0;">
                <span style="font-size: 13px; color: #475569;">🔑 <b>CREDENCIALES DE ACCESO AL PORTAL:</b></span><br>
                • <b>Usuario / Casillero:</b> {casillero} (o {destinatario})<br>
                • <b>Contraseña Provisional:</b> <code style="background:#e2e8f0; padding:2px 6px; border-radius:4px; font-weight:bold;">{password_temp}</code>
            </div>

            <div style="background-color: #fffbeb; border: 2px dashed #f59e0b; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 12px; white-space: pre-wrap;">
============================================================
              CENTRO DE CERÁMICAS Y MÁS — HONDURAS
                  MARITIME CONSOLIDATION CARGO
============================================================
CLIENT CODE / CASILLERO : {casillero}
CLIENT NAME / NOMBRE    : {nombre}
DESTINATION COUNTRY     : HONDURAS (PUERTO CORTÉS / INTIBUCÁ)
------------------------------------------------------------
SHIP TO / DIRECCIÓN EN CHINA (CHILAT WAREHOUSE):
ATTN / RECEIVER : CHILAT / {casillero}
ADDRESS (EN)    : CHILAT Logistics Warehouse, District B, Port Area
ADDRESS (中文)   : 广东省广州市白云区集运仓 / 转 {casillero}
WAREHOUSE TEL   : +86 138 0000 0000
------------------------------------------------------------
INSTRUCTIONS FOR SUPPLIER (Copiar y pegar al vendedor):
"Dear supplier, please ensure you paste our shipping label firmly 
on the exterior of each box before dispatching. Our warehouse will 
NOT accept packages without the Client Code: {casillero} clearly visible."

中文说明 (Para el vendedor en China):
"亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。
外箱必须清晰标注客户代码：{casillero}，否则仓库将拒收该包裹。"
============================================================
            </div>

            <p style="font-size:12px; color:#64748b; margin-top:20px;">
                Por seguridad, recuerde cambiar su contraseña una vez ingrese por primera vez al portal.
            </p>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = f"CCM Maritime Logistics <{remitente}>"
    msg["To"] = destinatario
    msg.attach(MIMEText(html, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(remitente, password)
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        return True, "Enviado con éxito"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------
# 4. GESTIÓN DE SESIÓN & ESTADO DEL WIZARD
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.update({
        "autenticado": False,
        "usuario": None,
        "rol": None,
        "casillero": None,
        "nombre": None,
        "reg_paso": 1,
        "reg_datos": {},
        "qr_timestamp": datetime.now()
    })

def logout():
    for k in ["autenticado", "usuario", "rol", "casillero", "nombre"]:
        st.session_state[k] = None
    st.session_state["autenticado"] = False
    st.rerun()

# ---------------------------------------------------------
# 5. MÓDULO 1: AUTENTICACIÓN & WIZARD DE 5 PASOS
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    st.markdown("""
    <div class="app-topbar">
        <h1>🚢 CCM LOGISTICS &bull; SERVICIO MARÍTIMO</h1>
        <div class="topbar-badge">China Hub ➔ Honduras Hub</div>
    </div>
    """, unsafe_allow_html=True)

    tab_login, tab_registro, tab_recuperar = st.tabs([
        "🔐 Iniciar Sesión", 
        "📝 Apertura de Casillero (5 Pasos)", 
        "🔄 Recuperar Contraseña"
    ])

    # 1.1 Formulario de Login
    with tab_login:
        c_centrar = st.columns([1, 1.2, 1])[1]
        with c_centrar:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("### 🔑 Acceso al Portal")
            st.caption("Ingrese sus credenciales de casillero marítimo o correo registrado.")
            
            with st.form("form_login"):
                u_ident = st.text_input("Casillero o Correo Electrónico", placeholder="CCM-HN-0001 o cliente@gmail.com")
                u_pass = st.text_input("Contraseña", type="password", placeholder="••••••••")
                btn_login = st.form_submit_button("Ingresar a mi Cuenta")

            if btn_login:
                if u_ident and u_pass:
                    p_hash = hash_pwd(u_pass)
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("""
                            SELECT id, codigo_casillero, nombre_completo, correo_principal, rol, activo 
                            FROM usuarios 
                            WHERE (correo_principal = ? OR codigo_casillero = ?) AND password_hash = ?
                        """, (u_ident, u_ident, p_hash))
                        user = c.fetchone()
                        
                    if user:
                        if user[5] == 0:
                            st.error("⛔ Cuenta suspendida o inactiva. Contacte al administrador.")
                        else:
                            st.session_state["autenticado"] = True
                            st.session_state["casillero"] = user[1]
                            st.session_state["nombre"] = user[2]
                            st.session_state["usuario"] = user[3]
                            st.session_state["rol"] = user[4]
                            st.rerun()
                    else:
                        st.error("❌ Credenciales inválidas. Compruebe casillero/correo y contraseña.")
                else:
                    st.warning("Por favor complete ambos campos.")
            
            st.markdown("""
            <div style="text-align: center; margin-top: 15px;">
                <small style="color: #64748b;">🔒 Conexión Encriptada • Centro de Cerámicas y Más</small>
            </div>
            """, unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

    # 1.2 Registro de Clientes (Asistente en 5 Pasos)
    with tab_registro:
        st.markdown("### 📋 Asistente de Apertura de Casillero Marítimo en China")
        
        # Barra de progreso
        paso_actual = st.session_state["reg_paso"]
        st.progress(paso_actual / 5.0, text=f"Paso {paso_actual} de 5: " + [
            "Identidad Personal", "Información de Contacto", "Dirección de Entrega", "Facturación Legal", "Preferencias de Retiro"
        ][paso_actual - 1])
        
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        
        # PASO 1: Identidad
        if paso_actual == 1:
            st.markdown("#### Paso 1: Identidad Personal")
            p1_nombre = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nombre", ""), placeholder="Ej. Roberto Alexander Castillo")
            c_d1, c_d2 = st.columns(2)
            with c_d1:
                p1_dni = st.text_input("Documento de Identidad (DNI) *", value=st.session_state["reg_datos"].get("dni", ""), placeholder="0801199512345")
            with c_d2:
                p1_rtn = st.text_input("RTN (Opcional)", value=st.session_state["reg_datos"].get("rtn", ""), placeholder="08011995123451")
            
            c_f1, c_f2 = st.columns(2)
            with c_f1:
                p1_fecha_nac = st.date_input("Fecha de Nacimiento *", value=date(1995, 1, 1), min_value=date(1940, 1, 1), max_value=date(2010, 1, 1))
            with c_f2:
                p1_genero = st.selectbox("Género *", ["Masculino", "Femenino", "Otro"])

            if st.button("Siguiente ➔ Paso 2"):
                if p1_nombre and p1_dni:
                    st.session_state["reg_datos"].update({
                        "nombre": p1_nombre, "dni": p1_dni, "rtn": p1_rtn,
                        "fecha_nac": str(p1_fecha_nac), "genero": p1_genero
                    })
                    st.session_state["reg_paso"] = 2
                    st.rerun()
                else:
                    st.error("Por favor llene los campos obligatorios (*).")

        # PASO 2: Contacto
        elif paso_actual == 2:
            st.markdown("#### Paso 2: Información de Contacto")
            c_c1, c_c2 = st.columns(2)
            with c_c1:
                p2_correo_p = st.text_input("Correo Electrónico Principal *", value=st.session_state["reg_datos"].get("correo_p", ""), placeholder="usuario@gmail.com")
                p2_tel_p = st.text_input("Teléfono / WhatsApp Principal *", value=st.session_state["reg_datos"].get("tel_p", ""), placeholder="+504 9988-7766")
            with c_c2:
                p2_correo_s = st.text_input("Correo Secundario (Opcional)", value=st.session_state["reg_datos"].get("correo_s", ""), placeholder="respaldo@correo.com")
                p2_tel_s = st.text_input("Teléfono Secundario (Opcional)", value=st.session_state["reg_datos"].get("tel_s", ""), placeholder="+504 2233-4455")
                
            c_nav1, c_nav2 = st.columns(2)
            with c_nav1:
                if st.button("⬅️ Volver al Paso 1"):
                    st.session_state["reg_paso"] = 1
                    st.rerun()
            with c_nav2:
                if st.button("Siguiente ➔ Paso 3"):
                    if p2_correo_p and p2_tel_p:
                        st.session_state["reg_datos"].update({
                            "correo_p": p2_correo_p, "correo_s": p2_correo_s,
                            "tel_p": p2_tel_p, "tel_s": p2_tel_s
                        })
                        st.session_state["reg_paso"] = 3
                        st.rerun()
                    else:
                        st.error("Ingrese su correo principal y teléfono celular.")

        # PASO 3: Dirección Local
        elif paso_actual == 3:
            st.markdown("#### Paso 3: Dirección de Entrega en Honduras")
            c_dir1, c_dir2 = st.columns(2)
            with c_dir1:
                p3_depto = st.selectbox("Departamento *", [
                    "Intibucá", "Cortés", "Francisco Morazán", "Atlántida", "Choluteca", "Comayagua",
                    "Copán", "El Paraíso", "La Paz", "Lempira", "Ocotepeque", "Olancho", "Santa Bárbara",
                    "Valle", "Yoro", "Colón", "Islas de la Bahía", "Gracias a Dios"
                ])
            with c_dir2:
                p3_ciudad = st.text_input("Ciudad / Municipio *", value=st.session_state["reg_datos"].get("ciudad", ""), placeholder="Ej. San Juan / San Pedro Sula")
                
            p3_direccion = st.text_area("Dirección Exacta (Colonia, Barrio, Referencia) *", value=st.session_state["reg_datos"].get("direccion", ""), placeholder="Barrio El Centro, frente a plaza...")

            c_nav1, c_nav2 = st.columns(2)
            with c_nav1:
                if st.button("⬅️ Volver al Paso 2"):
                    st.session_state["reg_paso"] = 2
                    st.rerun()
            with c_nav2:
                if st.button("Siguiente ➔ Paso 4"):
                    if p3_ciudad and p3_direccion:
                        st.session_state["reg_datos"].update({
                            "depto": p3_depto, "ciudad": p3_ciudad, "direccion": p3_direccion
                        })
                        st.session_state["reg_paso"] = 4
                        st.rerun()
                    else:
                        st.error("Complete la ciudad y dirección exacta.")

        # PASO 4: Facturación
        elif paso_actual == 4:
            st.markdown("#### Paso 4: Datos de Facturación Legal (Régimen SAR)")
            p4_razon = st.text_input("Nombre o Razón Social (Opcional si es a título personal)", value=st.session_state["reg_datos"].get("razon", ""), placeholder="Ej. Inversiones del Norte S. de R.L.")
            p4_rtn_fac = st.text_input("RTN para Facturación Fiscal (Opcional)", value=st.session_state["reg_datos"].get("rtn_fac", ""), placeholder="08019001234567")

            c_nav1, c_nav2 = st.columns(2)
            with c_nav1:
                if st.button("⬅️ Volver al Paso 3"):
                    st.session_state["reg_paso"] = 3
                    st.rerun()
            with c_nav2:
                if st.button("Siguiente ➔ Paso 5"):
                    st.session_state["reg_datos"].update({
                        "razon": p4_razon, "rtn_fac": p4_rtn_fac
                    })
                    st.session_state["reg_paso"] = 5
                    st.rerun()

        # PASO 5: Preferencias & Envío Final
        elif paso_actual == 5:
            st.markdown("#### Paso 5: Preferencias de Retiro y Perfil de Carga")
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                p5_rubro = st.selectbox("Rubro Principal de Carga *", ["Ferretería & Construcción", "Electrónicos & Tecnología", "Ropa & Calzado", "Repuestos Automotrices", "Cosméticos & Cuidado Personal", "Varios / Misceláneo"])
                p5_segmento = st.radio("Segmento *", ["Personal", "Corporativo / Mayorista"], horizontal=True)
            with c_p2:
                p5_canal = st.selectbox("¿Cómo nos conoció? *", ["Redes Sociales (Facebook/Instagram)", "Recomendación de un Amigo", "Publicidad Web", "Cliente Directo CCM"])
                p5_modalidad = st.radio("Modalidad de Entrega Preferida *", ["Retiro en Bodega Central (San Juan)", "Envío Nacional con Forza a Domicilio"], horizontal=True)

            c_nav1, c_nav2 = st.columns(2)
            with c_nav1:
                if st.button("⬅️ Volver al Paso 4"):
                    st.session_state["reg_paso"] = 4
                    st.rerun()
            with c_nav2:
                btn_finalizar = st.button("🚀 Confirmar Apertura y Recibir Credenciales")
                
            if btn_finalizar:
                d = st.session_state["reg_datos"]
                nuevo_casillero = generar_codigo_casillero()
                clave_temp = generar_clave_provisional()
                p_hash = hash_pwd(clave_temp)
                fecha_reg = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                try:
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT INTO usuarios (
                                codigo_casillero, nombre_completo, dni, rtn, fecha_nacimiento, genero,
                                correo_principal, correo_secundario, telefono_principal, telefono_secundario,
                                departamento, ciudad, direccion_exacta, razon_social, rtn_facturacion,
                                rubro_carga, segmento, canal_captacion, modalidad_entrega, password_hash, rol, activo, fecha_creacion
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)
                        """, (
                            nuevo_casillero, d["nombre"], d["dni"], d.get("rtn", ""), d["fecha_nac"], d["genero"],
                            d["correo_p"], d.get("correo_s", ""), d["tel_p"], d.get("tel_s", ""),
                            d["depto"], d["ciudad"], d["direccion"], d.get("razon", ""), d.get("rtn_fac", ""),
                            p5_rubro, p5_segmento, p5_canal, p5_modalidad, p_hash, fecha_reg
                        ))
                    
                    enviado, det = enviar_credenciales_maritimas(d["correo_p"], d["nombre"], nuevo_casillero, clave_temp)
                    
                    st.balloons()
                    st.success(f"🎉 ¡Casillero Marítimo en China **{nuevo_casillero}** generado exitosamente!")
                    
                    if enviado:
                        st.info(f"📧 Sus credenciales han sido despachadas a: **{d['correo_p']}**")
                    else:
                        st.warning(f"⚠️ Credenciales generadas: Casillero: **{nuevo_casillero}** | Clave Temporal: **{clave_temp}** (Detalle SMTP: {det})")
                        
                    # Resetear asistente
                    st.session_state["reg_paso"] = 1
                    st.session_state["reg_datos"] = {}
                except sqlite3.IntegrityError:
                    st.error("⚠️ El correo electrónico ingresado ya se encuentra registrado con otro casillero.")
                    
        st.markdown('</div>', unsafe_allow_html=True)

    # 1.3 Recuperación
    with tab_recuperar:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("### 🔄 Restablecer Acceso")
        st.caption("Ingrese su correo registrado para recibir una nueva clave temporal.")
        rec_correo = st.text_input("Correo Electrónico Registrado")
        if st.button("Enviar Instrucciones"):
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id, codigo_casillero, nombre_completo FROM usuarios WHERE correo_principal = ?", (rec_correo,))
                u = c.fetchone()
            if u:
                nueva_clave = generar_clave_provisional()
                p_hash = hash_pwd(nueva_clave)
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (p_hash, u[0]))
                enviar_credenciales_maritimas(rec_correo, u[2], u[1], nueva_clave)
                st.success("✅ Clave provisional enviada por correo electrónico.")
            else:
                st.error("No se encontró ninguna cuenta asociada a este correo.")
        st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 6. MÓDULO 2: PORTAL DEL CLIENTE (RESPONSIVE / APP)
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    casillero = st.session_state["casillero"]
    nombre_cli = st.session_state["nombre"]
    
    st.markdown(f"""
    <div class="app-topbar">
        <div>
            <h1>🚢 CCM MARITIME &bull; {casillero}</h1>
            <div style="font-size:0.85rem; color:#94a3b8;">Titular: {nombre_cli}</div>
        </div>
        <div class="topbar-badge">🟢 Casillero Activo</div>
    </div>
    """, unsafe_allow_html=True)

    # Barra de Navegación Inferior / Pestañas Móviles
    tab_inicio, tab_codigo, tab_cotizador, tab_perfil, tab_soporte = st.tabs([
        "🏠 Inicio", 
        "📱 Mi Código QR", 
        "📐 Cotizador Marítimo", 
        "👤 Mi Perfil & Casillero", 
        "💬 Soporte"
    ])

    # 2.1 Pestaña Inicio
    with tab_inicio:
        # Carrusel / Anuncio Marítimo
        st.markdown("""
        <div style="background: linear-gradient(90deg, #1e3a8a 0%, #0284c7 100%); color:white; padding:1.2rem; border-radius:12px; margin-bottom:1.2rem;">
            <h4 style="margin:0;">🚢 Próximo Zarpe Consolidado China ➔ Honduras</h4>
            <p style="margin:4px 0 0 0; font-size:0.9rem;">Ventana de consolidación en almacén CHILAT: <b>18 a 25 días</b> &bull; Tiempo estimado de tránsito marítimo: <b>35 a 45 días</b>.</p>
        </div>
        """, unsafe_allow_html=True)

        # Contadores de Carga
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM paquetes WHERE codigo_casillero = ? AND estado != 'Entregado'", (casillero,))
            en_transito = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM paquetes WHERE codigo_casillero = ? AND estado = 'Disponible en Bodega Central'", (casillero,))
            disponibles = c.fetchone()[0]

        c_stat1, c_stat2 = st.columns(2)
        with c_stat1:
            st.markdown(f'<div class="stat-card"><div class="stat-title">Paquetes En Tránsito</div><div class="stat-value">{en_transito}</div></div>', unsafe_allow_html=True)
        with c_stat2:
            st.markdown(f'<div class="stat-card" style="border-left-color:#16a34a;"><div class="stat-title">Disponibles para Retiro</div><div class="stat-value" style="color:#16a34a;">{disponibles}</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 🔍 Rastreo de Carga")
        trk_input = st.text_input("Buscar Tracking:", placeholder="Ingrese número de tracking o guía de China...")
        if trk_input:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE tracking = ? AND codigo_casillero = ?", (trk_input, casillero))
                paq = c.fetchone()
            if paq:
                st.info(f"📦 **Estado:** {paq[3]} | **Contenedor:** {paq[2]} | **Última actualización:** {paq[4]}")
            else:
                st.warning("No se encontró ningún paquete asociado a su casillero con ese tracking.")

    # 2.2 Pestaña Mi Código QR (Retiro con temporizador)
    with tab_codigo:
        st.markdown('<div class="card-box" style="text-align:center;">', unsafe_allow_html=True)
        st.markdown("### 📱 Código Dinámico de Retiro en Bodega")
        st.caption("Presente este código en ventanilla para retirar sus paquetes en San Juan, Intibucá.")
        
        # Simulación de QR dinámico con token temporal
        token_dinamico = hashlib.md5(f"{casillero}-{datetime.now().minute // 15}".encode()).hexdigest()[:8].upper()
        
        st.markdown(f"""
        <div class="qr-badge-box">
            <div style="font-size:3.5rem; letter-spacing:8px; font-weight:800; color:#1e3a8a; font-family:'Space Mono';">
                [ {token_dinamico} ]
            </div>
            <div style="font-size:1.1rem; font-weight:700; color:#0f172a; margin-top:8px;">
                TITULAR: {casillero}
            </div>
            <div class="timer-alert">
                ⏳ Válido durante 15 minutos &bull; Renovación automática
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🔄 Regenerar Código"):
                st.session_state["qr_timestamp"] = datetime.now()
                st.rerun()
        with col_btn2:
            st.button("👥 Autorizar Retiro por Terceros")
            
        st.markdown('</div>', unsafe_allow_html=True)

    # 2.3 Pestaña Cotizador Marítimo Avanzado
    with tab_cotizador:
        st.markdown("### 📐 Módulo de Cotización Marítima China ➔ Honduras")
        
        tarifa_lb = get_tarifa("tarifa_libra")
        tarifa_m3 = get_tarifa("tarifa_m3")
        minimo_usd = get_tarifa("minimo_cobro_usd")
        recargo_usd = get_tarifa("recargo_aduanal_usd")

        c_cot1, c_cot2 = st.columns([1.2, 1], gap="large")
        
        with c_cot1:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("#### 📦 Dimensiones y Peso de la Carga")
            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                alto = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0)
            with col_d2:
                ancho = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0)
            with col_d3:
                largo = st.number_input("Largo (cm)", min_value=1.0, value=40.0, step=1.0)
                
            peso = st.number_input("Peso Real (Libras / lb)", min_value=0.5, value=10.0, step=0.5)
            
            # Fórmulas de volumen
            vol_m3 = (alto * ancho * largo) / 1_000_000.0
            vol_ft3 = vol_m3 * 35.3147
            
            # Lógica tarifaria:
            # 1. 1-3 lbs: Mínimo fijo ($10 USD)
            # 2. 4-99 lbs (< 0.5 CBM): Por libra ($3.50 - $4.00)
            # 3. >= 100 lbs o >= 0.5 CBM: Por Metro Cúbico ($680/CBM proporcional)
            if peso <= 3.0:
                total_calculado = minimo_usd
                modo_calculo = f"Tarifa Mínima Base (1-3 lbs): ${minimo_usd:.2f} USD"
            elif peso < 100.0 and vol_m3 < 0.5:
                total_calculado = peso * tarifa_lb
                modo_calculo = f"Tarifa por Libra ({peso:.1f} lbs @ ${tarifa_lb:.2f}/lb)"
            else:
                costo_cbm = vol_m3 * tarifa_m3
                costo_peso_cbm = (peso / 880.0) * tarifa_m3
                total_calculado = max(costo_cbm, costo_peso_cbm)
                modo_calculo = f"Tarifa Comercial / Volumétrica (CBM Base ${tarifa_m3:.2f})"

            total_calculado += recargo_usd

            if st.button("💾 Guardar Cotización"):
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO cotizaciones (codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, volumen_ft3, total_usd, fecha)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (casillero, alto, ancho, largo, peso, vol_m3, vol_ft3, total_calculado, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                st.success("✅ Cotización registrada en su historial.")
                
            st.markdown('</div>', unsafe_allow_html=True)

        with c_cot2:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("#### 💰 Desglose del Cálculo Marítimo")
            
            st.markdown(f"""
            <div class="stat-card" style="margin-bottom:10px;">
                <div class="stat-title">Volumen en Metros Cúbicos</div>
                <div class="stat-value">{vol_m3:.4f} m³</div>
            </div>
            <div class="stat-card" style="margin-bottom:10px; border-left-color:#f59e0b;">
                <div class="stat-title">Modalidad Aplicada</div>
                <div class="stat-value" style="font-size:1.1rem; color:#d97706; margin-top:6px;">{modo_calculo}</div>
            </div>
            <div class="stat-card" style="border-left-color:#16a34a;">
                <div class="stat-title">Total Flete Marítimo + Aduana</div>
                <div class="stat-value" style="color:#16a34a;">${total_calculado:.2f} USD</div>
                <small style="color:#64748b;">Entrega en Bodega Central (San Juan, Intibucá).</small>
            </div>
            """, unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

    # 2.4 Pestaña Mi Perfil & Dirección en China
    with tab_perfil:
        st.markdown("### 📍 Ficha Técnica de Envío a Bodega en China")
        st.caption("Entregue esta ficha con sus instrucciones a su vendedor en Alibaba, 1688 o Made-in-China:")
        
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
<strong>WAREHOUSE TEL   :</strong> +86 138 0000 0000<br>
------------------------------------------------------------<br>
<strong>INSTRUCTIONS FOR SUPPLIER (Copiar y pegar al vendedor):</strong><br>
"Dear supplier, please ensure you paste our shipping label firmly <br>
on the exterior of each box before dispatching. Our warehouse will <br>
NOT accept packages without the Client Code: {casillero} clearly visible."<br>
<br>
<strong>中文说明 (Para el vendedor en China):</strong><br>
"亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。<br>
外箱必须清晰标注客户代码：{casillero}，否则仓库将拒收该包裹。"<br>
============================================================
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚪 Cerrar Sesión"):
            logout()

    # 2.5 Pestaña Soporte
    with tab_soporte:
        st.markdown("### 💬 Atención al Cliente & Asistencia")
        st.write("¿Tiene consultas sobre sus compras en China, cotizaciones o el contenedor en tránsito?")
        msg_soporte = f"Hola Centro de Cerámicas y Más, mi nombre es {nombre_cli} titular del casillero marítimo {casillero}. Necesito asistencia con:"
        url_wa = f"https://wa.me/50499990000?text={urllib.parse.quote(msg_soporte)}"
        st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:12px 20px; border-radius:8px; font-weight:bold; cursor:pointer;">📲 Chatear con Soporte por WhatsApp</button></a>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 7. MÓDULO 3: PANEL DE SUPERUSUARIO (ADMINISTRADOR)
# ---------------------------------------------------------
elif st.session_state["rol"] == "admin":
    st.markdown("""
    <div class="app-topbar">
        <h1>🛠️ CCM MARITIME &bull; CONTROL MAESTRO</h1>
        <div class="topbar-badge">Superusuario / Despacho China</div>
    </div>
    """, unsafe_allow_html=True)

    tab_adm_tarifas, tab_adm_casilleros, tab_adm_contenedores, tab_adm_auditoria = st.tabs([
        "💲 Tarifación Marítima", 
        "👥 Control de Casilleros", 
        "🚢 Contenedores & Carga", 
        "📈 Reportes & Auditoría"
    ])

    # 3.1 Tarifas Marítimas
    with tab_adm_tarifas:
        st.markdown("### 💲 Gestión de Tarifas Marítimas Globales")
        
        with st.form("form_tarifas_admin"):
            c_t1, c_t2, c_t3 = st.columns(3)
            with c_t1:
                t_lb = st.number_input("Tarifa por Libra (USD/lb)", value=float(get_tarifa("tarifa_libra")), step=0.10)
            with c_t2:
                t_ft3 = st.number_input("Tarifa por Pie Cúbico (USD/ft³)", value=float(get_tarifa("tarifa_ft3")), step=1.0)
            with c_t3:
                t_m3 = st.number_input("Tarifa por Metro Cúbico (USD/m³)", value=float(get_tarifa("tarifa_m3")), step=10.0)
                
            c_t4, c_t5 = st.columns(2)
            with c_t4:
                t_min = st.number_input("Mínimo de Cobro por Envío (USD)", value=float(get_tarifa("minimo_cobro_usd")), step=1.0)
            with c_t5:
                t_rec = st.number_input("Recargo de Trámite Aduanal (USD)", value=float(get_tarifa("recargo_aduanal_usd")), step=1.0)
                
            if st.form_submit_button("Guardar Parámetros de Tarifación"):
                set_tarifa("tarifa_libra", t_lb)
                set_tarifa("tarifa_ft3", t_ft3)
                set_tarifa("tarifa_m3", t_m3)
                set_tarifa("minimo_cobro_usd", t_min)
                set_tarifa("recargo_aduanal_usd", t_rec)
                st.success("✅ Tarifas actualizadas en tiempo real para todos los clientes.")

    # 3.2 Control de Casilleros
    with tab_adm_casilleros:
        st.markdown("### 👥 Directorio y Seguridad de Casilleros")
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT id, codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, ciudad, activo, fecha_creacion 
                FROM usuarios WHERE rol = 'cliente' ORDER BY id DESC
            """)
            clientes_db = c.fetchall()

        if clientes_db:
            filtro = st.text_input("Buscar por Casillero, DNI, Nombre o Correo:", "")
            for cli in clientes_db:
                if filtro.lower() in cli[1].lower() or filtro.lower() in cli[2].lower() or filtro.lower() in cli[3].lower():
                    with st.expander(f"📦 {cli[1]} — {cli[2]} ({'🟢 ACTIVO' if cli[7] == 1 else '🔴 BLOQUEADO'})"):
                        col_i1, col_i2 = st.columns([2, 1])
                        with col_i1:
                            st.write(f"**DNI:** {cli[3]} | **Teléfono:** {cli[5]}")
                            st.write(f"**Correo:** {cli[4]} | **Ciudad:** {cli[6]}")
                            st.write(f"**Fecha Registro:** {cli[8]}")
                        with col_i2:
                            nuevo_estado = 0 if cli[7] == 1 else 1
                            txt_act = "🔒 Bloquear" if cli[7] == 1 else "🔓 Desbloquear"
                            if st.button(txt_act, key=f"lock_{cli[0]}"):
                                with get_db() as conn:
                                    cur = conn.cursor()
                                    cur.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_estado, cli[0]))
                                st.rerun()
                                
                            if st.button("🔑 Reenviar Acceso", key=f"res_{cli[0]}"):
                                n_clave = generar_clave_provisional()
                                with get_db() as conn:
                                    cur = conn.cursor()
                                    cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(n_clave), cli[0]))
                                enviar_credenciales_maritimas(cli[4], cli[2], cli[1], n_clave)
                                st.success("Credenciales enviadas.")

    # 3.3 Contenedores y Carga
    with tab_adm_contenedores:
        st.markdown("### 🚢 Registro y Actualización de Consolidados Marítimos China")
        
        with st.form("form_carga_maritima"):
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                trk_in = st.text_input("Número de Tracking / Guía de China *")
                cas_in = st.text_input("Casillero Asignado * (Ej. CCM-HN-0001)")
            with c_p2:
                cnt_in = st.text_input("Contenedor ID * (Ej. CONT-CHN-2026-01)")
                est_in = st.selectbox("Estado del Envío *", [
                    "Recibido en Bodega China (CHILAT)",
                    "Consolidado en Contenedor",
                    "Zarpado / En Travesía Marítima",
                    "Arribado a Puerto Cortés",
                    "En Desaduanaje / Liquidación",
                    "Disponible en Bodega Central (San Juan)",
                    "Entregado al Cliente / Despachado por Forza"
                ])
            desc_in = st.text_input("Descripción de la Carga", placeholder="Caja de repuestos / Herramientas")
            peso_in = st.number_input("Peso (lb)", min_value=0.1, value=5.0)
            
            if st.form_submit_button("📦 Registrar / Actualizar Paquete"):
                if trk_in and cas_in and cnt_in:
                    fecha_up = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT OR REPLACE INTO paquetes (tracking, codigo_casillero, descripcion, contenedor_id, estado, peso_lb, fecha_actualizacion)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (trk_in, cas_in, desc_in, cnt_in, est_in, peso_in, fecha_up))
                    st.success(f"✅ Paquete con tracking **{trk_in}** actualizado a estado: **{est_in}**")
                else:
                    st.error("Complete los campos obligatorios (*).")

    # 3.4 Reportes & Auditoría
    with tab_adm_auditoria:
        st.markdown("### 📈 Auditoría de Cotizaciones y Movimientos")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT c.id, c.codigo_casillero, u.nombre_completo, c.peso_lb, c.volumen_m3, c.volumen_ft3, c.total_usd, c.fecha
                FROM cotizaciones c
                LEFT JOIN usuarios u ON c.codigo_casillero = u.codigo_casillero
                ORDER BY c.id DESC
            """)
            audit_data = c.fetchall()

        if audit_data:
            dt = [{
                "ID": r[0], "Casillero": r[1], "Cliente": r[2], "Peso (lb)": r[3],
                "m³": f"{r[4]:.4f}", "ft³": f"{r[5]:.2f}", "Total (USD)": f"${r[6]:.2f}", "Fecha": r[7]
            } for r in audit_data]
            st.dataframe(dt, use_container_width=True)
            
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(["ID", "Casillero", "Cliente", "Peso_lb", "Volumen_m3", "Volumen_ft3", "Total_USD", "Fecha"])
            for r in audit_data:
                writer.writerow(r)
            st.download_button("📥 Exportar Reporte a Excel (CSV)", data=csv_buf.getvalue(), file_name="auditoria_maritima_ccm.csv", mime="text/csv")
        else:
            st.info("No hay registros de cotizaciones todavía.")

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🚪 Cerrar Sesión Administrativa"):
        logout()
