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
# 1. CONFIGURACIÓN DEL SISTEMA
# ---------------------------------------------------------
st.set_page_config(
    page_title="CCM Maritime Cloud Hub",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "ccm_maritime_enterprise.db"

# Estado de tema visual (Dark / Light)
if "tema_visual" not in st.session_state:
    st.session_state["tema_visual"] = "Oscuro (Dark)"

if "vista_actual" not in st.session_state:
    st.session_state["vista_actual"] = "login"  # 'login', 'registro', 'recuperar'

# ---------------------------------------------------------
# 2. SELECTOR DE TEMA Y ESTILOS CSS INYECTADOS
# ---------------------------------------------------------
is_dark = (st.session_state["tema_visual"] == "Oscuro (Dark)")

# Paleta de colores según tema
bg_color = "#0b0f19" if is_dark else "#f4f6f9"
card_bg = "#111827" if is_dark else "#ffffff"
text_color = "#f9fafb" if is_dark else "#0f172a"
input_bg = "#1f2937" if is_dark else "#f8fafc"
input_border = "#374151" if is_dark else "#cbd5e1"
input_text = "#ffffff" if is_dark else "#0f172a"
btn_secondary_bg = "#1f2937" if is_dark else "#e2e8f0"
btn_secondary_color = "#e5e7eb" if is_dark else "#334155"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');
    
    html, body, [class*="css"], .stApp {{
        font-family: 'Plus Jakarta Sans', sans-serif;
        background-color: {bg_color} !important;
        color: {text_color} !important;
    }}
    
    #MainMenu, header, footer {{visibility: hidden;}}
    
    /* Contenedor Login estilo Sercargo */
    .sercargo-auth-container {{
        max-width: 440px;
        margin: 0 auto;
        padding: 2rem 1.5rem;
        background: transparent;
        text-align: center;
    }}

    .sercargo-logo-box {{
        margin-bottom: 2rem;
        text-align: center;
    }}
    .sercargo-logo-box svg {{
        width: 140px;
        height: auto;
    }}
    .brand-text {{
        font-size: 1.5rem;
        font-weight: 800;
        letter-spacing: 2px;
        color: #ffffff;
        margin-top: 8px;
    }}

    /* Inputs estilizados */
    .stTextInput > div > div > input, 
    .stNumberInput > div > div > input,
    .stSelectbox > div > div > div {{
        background-color: {input_bg} !important;
        color: {input_text} !important;
        border: 1px solid {input_border} !important;
        border-radius: 12px !important;
        padding: 14px 16px !important;
        font-size: 0.95rem !important;
    }}
    .stTextInput > div > div > input:focus {{
        border-color: #0052cc !important;
        box-shadow: 0 0 0 2px rgba(0, 82, 204, 0.2) !important;
    }}

    label {{
        color: {text_color} !important;
        font-size: 0.88rem !important;
        font-weight: 600 !important;
        margin-bottom: 4px !important;
    }}

    /* Botón Primario Azul (Iniciar Sesión) */
    .btn-login-primary button {{
        background: #0052cc !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 12px !important;
        padding: 14px 20px !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
        width: 100% !important;
        margin-top: 10px !important;
        box-shadow: 0 4px 12px rgba(0, 82, 204, 0.3) !important;
        transition: all 0.2s ease !important;
    }}
    .btn-login-primary button:hover {{
        background: #0043a8 !important;
        transform: translateY(-1px);
    }}

    /* Botones Secundarios Negros / Grises */
    .btn-secondary-action button {{
        background: {btn_secondary_bg} !important;
        color: {btn_secondary_color} !important;
        border: none !important;
        border-radius: 12px !important;
        padding: 12px 20px !important;
        font-weight: 600 !important;
        font-size: 0.92rem !important;
        width: 100% !important;
        margin-top: 8px !important;
    }}

    /* Top Bar & Cards */
    .app-topbar {{
        background: {card_bg};
        padding: 1.2rem 1.6rem;
        border-radius: 14px;
        color: {text_color};
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1.5rem;
        border: 1px solid {input_border};
    }}
    .card-box {{
        background: {card_bg};
        border-radius: 14px;
        padding: 1.4rem;
        border: 1px solid {input_border};
        margin-bottom: 1.2rem;
    }}
    .stat-card {{
        background: {card_bg};
        border-radius: 12px;
        padding: 1.2rem;
        border: 1px solid {input_border};
        border-left: 4px solid #0052cc;
    }}
    .stat-title {{
        font-size: 0.78rem;
        font-weight: 700;
        color: #94a3b8;
        text-transform: uppercase;
    }}
    .stat-value {{
        font-size: 1.6rem;
        font-weight: 800;
        color: {text_color};
        margin-top: 4px;
    }}
    .china-address-box {{
        background: {input_bg};
        border: 2px dashed #0052cc;
        border-radius: 12px;
        padding: 1.4rem;
        font-family: 'Space Mono', monospace;
        font-size: 0.88rem;
        color: {text_color};
    }}
</style>
""", unsafe_allow_html=True)

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
                peso_lb REAL,
                fecha_actualizacion TEXT NOT NULL
            )
        """)
        
        # Parámetros China - Honduras
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_libra', 3.50)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_m3', 680.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('minimo_cobro_usd', 10.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('recargo_aduanal_usd', 0.00)")
        
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
    return ''.join(random.choice(caracteres) for _ in range(9))

def enviar_credenciales_maritimas(destinatario, nombre, casillero, password_temp):
    try:
        remitente = st.secrets["EMAIL_REMITENTE"]
        password = st.secrets["EMAIL_PASSWORD"]
    except Exception:
        return False, "Credenciales no configuradas en Secrets."

    asunto = f"🚢 Apertura de Casillero Marítimo en China Exitosa: {casillero}"
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0b0f19; padding: 20px; color: #f9fafb;">
        <div style="background: #111827; max-width: 600px; margin: auto; padding: 25px; border-radius: 12px; border: 1px solid #374151;">
            <h2 style="color:#38bdf8; text-align:center;">CENTRO DE CERÁMICAS Y MÁS</h2>
            <p>Estimado(a) <strong>{nombre}</strong>,</p>
            <p>Su casillero marítimo internacional ha sido activado:</p>
            <p>• <b>Casillero:</b> {casillero}<br>• <b>Contraseña:</b> {password_temp}</p>
        </div>
    </body>
    </html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = f"CCM Logistics <{remitente}>"
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
# 4. GESTIÓN DE SESIÓN
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.update({
        "autenticado": False,
        "usuario": None,
        "rol": None,
        "casillero": None,
        "nombre": None,
        "reg_paso": 1,
        "reg_datos": {}
    })

def logout():
    for k in ["autenticado", "usuario", "rol", "casillero", "nombre"]:
        st.session_state[k] = None
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.rerun()

# ---------------------------------------------------------
# 5. SELECTOR DE TEMA SUPERIOR (FLOTANTE / DISCRETO)
# ---------------------------------------------------------
c_top_l, c_top_r = st.columns([4, 1.2])
with c_top_r:
    tema_sel = st.selectbox(
        "Modo de Visualización",
        ["Oscuro (Dark)", "Blanco (Light)"],
        index=0 if is_dark else 1,
        key="selector_tema_global",
        label_visibility="collapsed"
    )
    if tema_sel != st.session_state["tema_visual"]:
        st.session_state["tema_visual"] = tema_sel
        st.rerun()

# ---------------------------------------------------------
# 6. PANTALLAS PÚBLICAS (LOGIN / REGISTRO / RECUPERAR)
# ---------------------------------------------------------
if not st.session_state["autenticado"]:

    # 6.1 VISTA LOGIN (IDÉNTICA A LA IMAGEN)
    if st.session_state["vista_actual"] == "login":
        c_left, c_center, c_right = st.columns([1, 1.3, 1])
        with c_center:
            st.markdown("""
            <div class="sercargo-logo-box">
                <svg viewBox="0 0 100 60" fill="none" xmlns="http://www.w3.org/2000/svg" style="width:110px; height:auto;">
                    <path d="M15 15 H85 L70 30 H30 L15 15Z" fill="#ffffff"/>
                    <path d="M85 45 H15 L30 30 H70 L85 45Z" fill="#0052cc"/>
                </svg>
                <div class="brand-text">CCM LOGISTICS</div>
            </div>
            """, unsafe_allow_html=True)

            u_ident = st.text_input("Casillero", placeholder="929966 o CCM-HN-0001", key="login_cas")
            u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña de acceso al casillero", key="login_pass")

            st.markdown('<div class="btn-login-primary">', unsafe_allow_html=True)
            if st.button("Iniciar sesión", key="btn_iniciar_sesion"):
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
                            st.error("⛔ Cuenta suspendida. Contacte al administrador.")
                        else:
                            st.session_state["autenticado"] = True
                            st.session_state["casillero"] = user[1]
                            st.session_state["nombre"] = user[2]
                            st.session_state["usuario"] = user[3]
                            st.session_state["rol"] = user[4]
                            st.rerun()
                    else:
                        st.error("❌ Credenciales inválidas.")
                else:
                    st.warning("Complete todos los campos.")
            st.markdown('</div>', unsafe_allow_html=True)

            st.markdown('<div class="btn-secondary-action">', unsafe_allow_html=True)
            if st.button("Restablecer contraseña", key="btn_ir_restablecer"):
                st.session_state["vista_actual"] = "recuperar"
                st.rerun()

            if st.button("Aperturar casillero", key="btn_ir_aperturar"):
                st.session_state["vista_actual"] = "registro"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # 6.2 VISTA REGISTRO (5 PASOS)
    elif st.session_state["vista_actual"] == "registro":
        c_left, c_center, c_right = st.columns([1, 1.8, 1])
        with c_center:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("### 📋 Apertura de Casillero Marítimo en China")
            
            paso = st.session_state["reg_paso"]
            st.progress(paso / 5.0, text=f"Paso {paso} de 5")

            if paso == 1:
                st.markdown("#### Identidad Personal")
                nombre = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nombre", ""))
                dni = st.text_input("Identidad (DNI) *", value=st.session_state["reg_datos"].get("dni", ""))
                if st.button("Siguiente ➔"):
                    if nombre and dni:
                        st.session_state["reg_datos"].update({"nombre": nombre, "dni": dni})
                        st.session_state["reg_paso"] = 2
                        st.rerun()
                    else:
                        st.error("Llene los campos obligatorios.")

            elif paso == 2:
                st.markdown("#### Datos de Contacto")
                correo = st.text_input("Correo Electrónico *", value=st.session_state["reg_datos"].get("correo", ""))
                tel = st.text_input("Teléfono / WhatsApp *", value=st.session_state["reg_datos"].get("tel", ""))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("⬅️ Atrás"):
                        st.session_state["reg_paso"] = 1
                        st.rerun()
                with c2:
                    if st.button("Siguiente ➔"):
                        if correo and tel:
                            st.session_state["reg_datos"].update({"correo": correo, "tel": tel})
                            st.session_state["reg_paso"] = 3
                            st.rerun()

            elif paso == 3:
                st.markdown("#### Dirección de Entrega en Honduras")
                depto = st.selectbox("Departamento *", ["Intibucá", "Cortés", "Francisco Morazán", "Comayagua", "Copán", "Atlántida", "Choluteca", "Olancho"])
                ciudad = st.text_input("Municipio / Ciudad *", value=st.session_state["reg_datos"].get("ciudad", ""))
                dir_ex = st.text_area("Dirección Exacta *", value=st.session_state["reg_datos"].get("dir", ""))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("⬅️ Atrás"):
                        st.session_state["reg_paso"] = 2
                        st.rerun()
                with c2:
                    if st.button("Siguiente ➔"):
                        if ciudad and dir_ex:
                            st.session_state["reg_datos"].update({"depto": depto, "ciudad": ciudad, "dir": dir_ex})
                            st.session_state["reg_paso"] = 4
                            st.rerun()

            elif paso == 4:
                st.markdown("#### Facturación (Opcional)")
                razon = st.text_input("Razón Social (Opcional)", value=st.session_state["reg_datos"].get("razon", ""))
                rtn_fac = st.text_input("RTN Fiscal (Opcional)", value=st.session_state["reg_datos"].get("rtn_fac", ""))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("⬅️ Atrás"):
                        st.session_state["reg_paso"] = 3
                        st.rerun()
                with c2:
                    if st.button("Siguiente ➔"):
                        st.session_state["reg_datos"].update({"razon": razon, "rtn_fac": rtn_fac})
                        st.session_state["reg_paso"] = 5
                        st.rerun()

            elif paso == 5:
                st.markdown("#### Preferencias Finales")
                rubro = st.selectbox("Rubro de Carga", ["Ferretería & Construcción", "Electrónica", "Ropa & Calzado", "Repuestos", "General"])
                modalidad = st.radio("Modalidad de Entrega", ["Retiro en Bodega Central (San Juan)", "Envío con Forza a Domicilio"])
                
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("⬅️ Atrás"):
                        st.session_state["reg_paso"] = 4
                        st.rerun()
                with c2:
                    if st.button("🚀 Crear Casillero"):
                        d = st.session_state["reg_datos"]
                        n_cas = generar_codigo_casillero()
                        n_clave = generar_clave_provisional()
                        fecha_reg = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        with get_db() as conn:
                            cur = conn.cursor()
                            cur.execute("""
                                INSERT INTO usuarios (
                                    codigo_casillero, nombre_completo, dni, correo_principal, 
                                    telefono_principal, departamento, ciudad, direccion_exacta, 
                                    razon_social, rtn_facturacion, rubro_carga, modalidad_entrega, 
                                    password_hash, rol, activo, fecha_creacion
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)
                            """, (n_cas, d["nombre"], d["dni"], d["correo"], d["tel"], d["depto"], d["ciudad"], d["dir"], d["razon"], d["rtn_fac"], rubro, modalidad, hash_pwd(n_clave), fecha_reg))
                        
                        enviar_credenciales_maritimas(d["correo"], d["nombre"], n_cas, n_clave)
                        st.success(f"🎉 ¡Casillero Creado! Código: **{n_cas}** | Clave: **{n_clave}**")
                        st.session_state["reg_paso"] = 1
                        st.session_state["reg_datos"] = {}

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Volver al inicio de sesión"):
                st.session_state["vista_actual"] = "login"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # 6.3 VISTA RECUPERAR
    elif st.session_state["vista_actual"] == "recuperar":
        c_left, c_center, c_right = st.columns([1, 1.3, 1])
        with c_center:
            st.markdown('<div class="card-box">', unsafe_allow_html=True)
            st.markdown("### 🔄 Restablecer Acceso")
            rec_mail = st.text_input("Correo Registrado")
            if st.button("Enviar Clave Temporal", key="btn_env_tmp"):
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("SELECT id, codigo_casillero, nombre_completo FROM usuarios WHERE correo_principal = ?", (rec_mail,))
                    u = c.fetchone()
                if u:
                    n_clave = generar_clave_provisional()
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (hash_pwd(n_clave), u[0]))
                    enviar_credenciales_maritimas(rec_mail, u[2], u[1], n_clave)
                    st.success("✅ Clave enviada al correo.")
                else:
                    st.error("Correo no encontrado.")
            
            if st.button("Volver al login"):
                st.session_state["vista_actual"] = "login"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 7. PORTAL DEL CLIENTE (AUTENTICADO)
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    casillero = st.session_state["casillero"]
    nombre_cli = st.session_state["nombre"]

    st.markdown(f"""
    <div class="app-topbar">
        <div>
            <h2 style="margin:0; font-size:1.3rem; color:#38bdf8;">🚢 CCM MARITIME &bull; {casillero}</h2>
            <div style="font-size:0.85rem; opacity:0.8;">Titular: {nombre_cli}</div>
        </div>
        <div style="background:#0052cc; color:white; padding:6px 14px; border-radius:20px; font-size:0.8rem; font-weight:bold;">🟢 Activo</div>
    </div>
    """, unsafe_allow_html=True)

    tab_inicio, tab_cotizador, tab_perfil = st.tabs(["🏠 Inicio & Cargas", "📐 Cotizador Marítimo", "📍 Ficha de Envío China"])

    with tab_inicio:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM paquetes WHERE codigo_casillero = ? AND estado != 'Entregado'", (casillero,))
            en_trans = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM paquetes WHERE codigo_casillero = ? AND estado = 'Disponible en Bodega Central'", (casillero,))
            disp = c.fetchone()[0]

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f'<div class="stat-card"><div class="stat-title">En Tránsito</div><div class="stat-value">{en_trans}</div></div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="stat-card" style="border-left-color:#16a34a;"><div class="stat-title">Listos para Retiro</div><div class="stat-value">{disp}</div></div>', unsafe_allow_html=True)

    with tab_cotizador:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### Cotizador Marítimo China ➔ Honduras")
        col1, col2, col3, col4 = st.columns(4)
        with col1: al = st.number_input("Alto (cm)", value=30.0)
        with col2: an = st.number_input("Ancho (cm)", value=30.0)
        with col3: la = st.number_input("Largo (cm)", value=40.0)
        with col4: pe = st.number_input("Peso (lb)", value=10.0)

        vol_m3 = (al * an * la) / 1_000_000.0
        t_lb = get_tarifa("tarifa_libra")
        t_m3 = get_tarifa("tarifa_m3")

        if pe <= 3.0:
            tot = 10.0
        elif pe < 100.0 and vol_m3 < 0.5:
            tot = pe * t_lb
        else:
            tot = max(vol_m3 * t_m3, (pe / 880.0) * t_m3)

        st.metric("Total Estimado (USD)", f"${tot:.2f} USD", help="Flete marítimo + internación aduanal")
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_perfil:
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
        if st.button("🚪 Cerrar Sesión"):
            logout()

# ---------------------------------------------------------
# 8. PANEL ADMINISTRADOR
# ---------------------------------------------------------
elif st.session_state["rol"] == "admin":
    st.markdown("""
    <div class="app-topbar">
        <h2 style="margin:0; font-size:1.3rem; color:#38bdf8;">🛠️ CONTROL ADMINISTRATIVO — CCM LOGISTICS</h2>
    </div>
    """, unsafe_allow_html=True)

    tab_tar, tab_users, tab_pack = st.tabs(["💲 Tarifas", "👥 Casilleros", "📦 Paquetes"])

    with tab_tar:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        n_lb = st.number_input("Tarifa Libra (USD)", value=float(get_tarifa("tarifa_libra")), step=0.1)
        n_m3 = st.number_input("Tarifa CBM (USD)", value=float(get_tarifa("tarifa_m3")), step=10.0)
        if st.button("Guardar Tarifas"):
            set_tarifa("tarifa_libra", n_lb)
            set_tarifa("tarifa_m3", n_m3)
            st.success("Tarifas actualizadas.")
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_users:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT codigo_casillero, nombre_completo, correo_principal, telefono_principal FROM usuarios WHERE rol = 'cliente'")
            st.dataframe(c.fetchall(), use_container_width=True)

    with tab_pack:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        t_in = st.text_input("Tracking")
        c_in = st.text_input("Casillero (CCM-HN-XXXX)")
        e_in = st.selectbox("Estado", ["En Bodega China", "En Travesía Marítima", "Disponible en Bodega Central", "Entregado"])
        if st.button("Actualizar Paquete"):
            if t_in and c_in:
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT OR REPLACE INTO paquetes (tracking, codigo_casillero, estado, fecha_actualizacion) VALUES (?, ?, ?, ?)", (t_in, c_in, e_in, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                st.success("Paquete registrado.")
        st.markdown('</div>', unsafe_allow_html=True)

    if st.button("🚪 Cerrar Sesión Administrador"):
        logout()
