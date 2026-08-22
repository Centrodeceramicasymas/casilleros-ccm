import streamlit as st
import sqlite3
import hashlib
import random
import string
import csv
from datetime import datetime
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y ESTILOS ERP / CLOUD
# ---------------------------------------------------------
st.set_page_config(
    page_title="CCM Logistics Cloud",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "casilleros_ccm_enterprise.db"

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
        background-color: #f1f5f9;
    }
    
    #MainMenu, header, footer {visibility: hidden;}

    /* Navbar Superior */
    .top-navbar {
        background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%);
        padding: 0.9rem 1.5rem;
        border-radius: 10px;
        color: white;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .top-navbar h2 {
        margin: 0;
        font-size: 1.3rem;
        font-weight: 700;
        color: #38bdf8;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .user-pill {
        background: rgba(255, 255, 255, 0.1);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        border: 1px solid rgba(255, 255, 255, 0.15);
    }

    /* Tarjetas de Contenido */
    .erp-card {
        background: #ffffff;
        border-radius: 10px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        margin-bottom: 1.5rem;
    }
    
    /* Métricas */
    .kpi-metric {
        background: #ffffff;
        border-left: 4px solid #0284c7;
        border-radius: 8px;
        padding: 1rem 1.2rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        border-top: 1px solid #e2e8f0;
        border-right: 1px solid #e2e8f0;
        border-bottom: 1px solid #e2e8f0;
    }
    .kpi-title {
        font-size: 0.8rem;
        font-weight: 600;
        color: #64748b;
        text-transform: uppercase;
    }
    .kpi-val {
        font-size: 1.7rem;
        font-weight: 700;
        color: #0f172a;
        margin-top: 4px;
    }

    /* Etiqueta Consignación */
    .label-box {
        background: #fffbeb;
        border: 2px dashed #f59e0b;
        border-radius: 8px;
        padding: 1.2rem;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.88rem;
        color: #78350f;
    }

    /* Botones primarios */
    div.stButton > button:first-child {
        background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
        color: white !important;
        font-weight: 600 !important;
        border: none !important;
        border-radius: 6px !important;
        padding: 0.55rem 1.4rem !important;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. BASE DE DATOS Y AUTENTICACIÓN
# ---------------------------------------------------------
def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_db():
    return sqlite3.connect(DB_NAME)

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        # Tabla de usuarios (Auth)
        c.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT UNIQUE NOT NULL,
                nombre_completo TEXT NOT NULL,
                correo TEXT UNIQUE NOT NULL,
                telefono TEXT NOT NULL,
                direccion TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL,
                activo INTEGER DEFAULT 1,
                fecha_creacion TEXT NOT NULL
            )
        """)
        # Tabla de configuraciones operativas
        c.execute("""
            CREATE TABLE IF NOT EXISTS config (
                clave TEXT PRIMARY KEY,
                valor REAL NOT NULL
            )
        """)
        # Tabla de cotizaciones
        c.execute("""
            CREATE TABLE IF NOT EXISTS cotizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT NOT NULL,
                alto_cm REAL,
                ancho_cm REAL,
                largo_cm REAL,
                peso_lb REAL,
                volumen_m3 REAL,
                total_usd REAL,
                fecha TEXT NOT NULL
            )
        """)
        # Inicializar tarifa por defecto ($3.40 / lb)
        c.execute("INSERT OR IGNORE INTO config (clave, valor) VALUES ('tarifa_libra', 3.40)")
        c.execute("INSERT OR IGNORE INTO config (clave, valor) VALUES ('tarifa_cbm', 350.00)")
        
        # Crear superadmin si no existe
        admin_pass = hash_pass("admin123")
        c.execute("""
            INSERT OR IGNORE INTO usuarios (codigo_casillero, nombre_completo, correo, telefono, direccion, password_hash, rol, activo, fecha_creacion)
            VALUES ('CCM-ADMIN', 'Administrador Principal', 'admin@ccm.hn', '+504 0000-0000', 'San Juan Intibucá', ?, 'admin', 1, '2026-08-22 00:00:00')
        """, (admin_pass,))

init_db()

def get_config(clave):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT valor FROM config WHERE clave = ?", (clave,))
        res = c.fetchone()
        return res[0] if res else 0.0

def set_config(clave, valor):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE config SET valor = ? WHERE clave = ?", (valor, clave))

def generar_password_temporal():
    caracteres = string.ascii_letters + string.digits
    return ''.join(random.choice(caracteres) for _ in range(8))

def generar_codigo_casillero():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM usuarios WHERE rol = 'cliente' ORDER BY id DESC LIMIT 1")
        last = c.fetchone()
        next_id = 1 if not last else last[0] + 1
        return f"CCM-HN-{next_id:03d}"

# ---------------------------------------------------------
# 3. ENVÍO DE CORREOS SMTP (GMAIL)
# ---------------------------------------------------------
def enviar_credenciales_correo(destinatario, nombre, casillero, password_temp):
    try:
        remitente = st.secrets["EMAIL_REMITENTE"]
        password = st.secrets["EMAIL_PASSWORD"]
    except Exception:
        return False, "Credenciales no configuradas en Secrets."

    asunto = f"📦 Bienvenida a CCM Logistics - Credenciales de Casillero {casillero}"
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #1e293b;">
        <div style="background: #ffffff; max-width: 600px; margin: auto; padding: 25px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div style="background: #0f172a; color: #ffffff; padding: 15px; border-radius: 6px; text-align: center;">
                <h2 style="margin:0; color:#38bdf8;">CENTRO DE CERÁMICAS Y MÁS</h2>
                <p style="margin:4px 0 0 0; font-size:12px; color:#94a3b8;">Sistema de Casilleros e Importaciones</p>
            </div>
            
            <p>Estimado(a) <strong>{nombre}</strong>,</p>
            <p>Su casillero internacional ha sido creado. A continuación encontrará sus credenciales de acceso al portal y su dirección oficial de recepción en China:</p>
            
            <div style="background: #f1f5f9; padding: 14px; border-radius: 6px; margin: 15px 0;">
                <span style="font-size: 13px; color: #475569;">🔑 <b>DATOS DE ACCESO:</b></span><br>
                • <b>Usuario / Correo:</b> {destinatario}<br>
                • <b>Número de Casillero:</b> {casillero}<br>
                • <b>Contraseña Temporal:</b> <code style="background:#e2e8f0; padding:2px 6px; border-radius:4px;">{password_temp}</code>
            </div>

            <div style="background-color: #fffbeb; border: 2px dashed #f59e0b; padding: 15px; border-radius: 6px; font-family: monospace; font-size: 12px;">
                <strong>📦 SHIP TO / DIRECCIÓN EN CHINA (BODEGA CHILAT):</strong><br>
                ATTN / RECEIVER : CHILAT / {casillero}<br>
                CLIENT NAME     : {nombre}<br>
                COUNTRY         : HONDURAS<br>
                <hr style="border-top: 1px dashed #f59e0b; margin: 8px 0;">
                <strong>Instrucciones para el vendedor:</strong><br>
                亲爱的卖家，发货前请务必在每个外箱上牢固张贴唛头。外箱必须清晰标注客户代码：{casillero}。
            </div>
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
        return True, "Enviado exitosamente"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------
# 4. GESTIÓN DE SESIÓN
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False
    st.session_state["usuario"] = None
    st.session_state["rol"] = None
    st.session_state["casillero"] = None
    st.session_state["nombre"] = None

def cerrar_sesion():
    st.session_state["autenticado"] = False
    st.session_state["usuario"] = None
    st.session_state["rol"] = None
    st.session_state["casillero"] = None
    st.session_state["nombre"] = None
    st.rerun()

# ---------------------------------------------------------
# 5. PANTALLA 1: LANDING / AUTENTICACIÓN
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    st.markdown("""
    <div class="top-navbar">
        <h2>📦 CCM LOGISTICS &bull; PORTAL DE ENLACE</h2>
        <div class="user-pill">🇨🇳 China ➔ 🇭🇳 Honduras</div>
    </div>
    """, unsafe_allow_html=True)
    
    col_izq, col_der = st.columns([1, 1], gap="large")
    
    with col_izq:
        st.markdown("""
        <div class="erp-card">
            <h3>🔐 Iniciar Sesión</h3>
            <p style="color:#64748b; font-size:0.9rem;">Accede a tu panel para cotizar, revisar casillero o administrar el sistema.</p>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("form_login"):
            correo_login = st.text_input("Correo Electrónico o Casillero", placeholder="ejemplo@correo.com o CCM-HN-001")
            pass_login = st.text_input("Contraseña", type="password")
            btn_entrar = st.form_submit_button("Ingresar al Sistema")
            
        if btn_entrar:
            if correo_login and pass_login:
                p_hash = hash_pass(pass_login)
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("""
                        SELECT id, codigo_casillero, nombre_completo, correo, rol, activo 
                        FROM usuarios 
                        WHERE (correo = ? OR codigo_casillero = ?) AND password_hash = ?
                    """, (correo_login, correo_login, p_hash))
                    user = c.fetchone()
                    
                if user:
                    if user[5] == 0:
                        st.error("⛔ Esta cuenta se encuentra inactiva. Contacte a soporte.")
                    else:
                        st.session_state["autenticado"] = True
                        st.session_state["casillero"] = user[1]
                        st.session_state["nombre"] = user[2]
                        st.session_state["usuario"] = user[3]
                        st.session_state["rol"] = user[4]
                        st.rerun()
                else:
                    st.error("❌ Credenciales incorrectas. Verifique correo/casillero y contraseña.")
            else:
                st.warning("Complete todos los campos.")

    with col_der:
        st.markdown("""
        <div class="erp-card">
            <h3>✨ Crear Cuenta de Casillero</h3>
            <p style="color:#64748b; font-size:0.9rem;">Regístrate para recibir tus compras de China directamente en Honduras.</p>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("form_registro", clear_on_submit=True):
            r_nombre = st.text_input("Nombre Completo *", placeholder="Ej. Carlos Mendoza")
            r_correo = st.text_input("Correo Electrónico (Para recibir credenciales) *", placeholder="cliente@gmail.com")
            r_tel = st.text_input("Teléfono / WhatsApp *", placeholder="+504 9988-7766")
            r_dir = st.text_input("Dirección de Entrega en Honduras *", placeholder="Colonia, Calle, Ciudad")
            
            btn_registro = st.form_submit_button("🚀 Crear mi Casillero Gratis")
            
        if btn_registro:
            if r_nombre and r_correo and r_tel and r_dir:
                nuevo_casillero = generar_codigo_casillero()
                pass_temp = generar_password_temporal()
                p_hash = hash_pass(pass_temp)
                fecha_reg = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                try:
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("""
                            INSERT INTO usuarios (codigo_casillero, nombre_completo, correo, telefono, direccion, password_hash, rol, activo, fecha_creacion)
                            VALUES (?, ?, ?, ?, ?, ?, 'cliente', 1, ?)
                        """, (nuevo_casillero, r_nombre, r_correo, r_tel, r_dir, p_hash, fecha_reg))
                        
                    enviado, det = enviar_credenciales_correo(r_correo, r_nombre, nuevo_casillero, pass_temp)
                    
                    st.success(f"🎉 ¡Casillero **{nuevo_casillero}** creado con éxito!")
                    if enviado:
                        st.info(f"📧 Credenciales y ficha oficial enviadas a: **{r_correo}**")
                    else:
                        st.warning(f"⚠️ Casillero creado. Credenciales temporales: Usuario: **{r_correo}** | Clave: **{pass_temp}** (Aviso: {det})")
                except sqlite3.IntegrityError:
                    st.error("⚠️ El correo ingresado ya tiene un casillero registrado.")
            else:
                st.error("Por favor complete todos los campos obligatorios (*).")

# ---------------------------------------------------------
# 6. PORTAL DE CLIENTES
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    st.sidebar.markdown(f"### 👤 {st.session_state['nombre']}")
    st.sidebar.caption(f"Casillero: **{st.session_state['casillero']}**")
    st.sidebar.markdown("---")
    
    menu_cli = st.sidebar.radio(
        "Navegación",
        ["📊 Cotizador en Línea", "📦 Mi Casillero & Ficha", "📜 Historial de Cotizaciones"]
    )
    
    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar Sesión"):
        cerrar_sesion()

    st.markdown(f"""
    <div class="top-navbar">
        <h2>📦 CCM CLIENT PORTAL &bull; {st.session_state['casillero']}</h2>
        <div class="user-pill">{st.session_state['nombre']}</div>
    </div>
    """, unsafe_allow_html=True)

    if menu_cli == "📊 Cotizador en Línea":
        st.markdown("### 🧮 Módulo de Cotización en Tiempo Real")
        tarifa_lb = get_config("tarifa_libra")
        
        col_form, col_res = st.columns([1.2, 1], gap="large")
        
        with col_form:
            st.markdown('<div class="erp-card">', unsafe_allow_html=True)
            st.markdown("#### 📐 Parámetros del Paquete")
            c1, c2, c3 = st.columns(3)
            with c1:
                alto = st.number_input("Alto (cm)", min_value=1.0, value=20.0, step=1.0)
            with c2:
                ancho = st.number_input("Ancho (cm)", min_value=1.0, value=20.0, step=1.0)
            with c3:
                largo = st.number_input("Largo (cm)", min_value=1.0, value=20.0, step=1.0)
                
            peso_lb = st.number_input("Peso Real (Libras / lb)", min_value=0.5, value=5.0, step=0.5)
            
            # Cálculos en tiempo real
            volumen_m3 = (alto * ancho * largo) / 1_000_000.0
            total_usd = peso_lb * tarifa_lb
            
            btn_guardar_cot = st.button("💾 Guardar esta Cotización")
            st.markdown('</div>', unsafe_allow_html=True)
            
            if btn_guardar_cot:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO cotizaciones (codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd, fecha)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (st.session_state["casillero"], alto, ancho, largo, peso_lb, volumen_m3, total_usd, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                st.success("✅ Cotización guardada exitosamente en su historial.")

        with col_res:
            st.markdown('<div class="erp-card">', unsafe_allow_html=True)
            st.markdown("#### 💰 Resumen Financiero")
            
            st.markdown(f"""
            <div class="kpi-metric" style="margin-bottom:12px;">
                <div class="kpi-title">Volumen Estimado</div>
                <div class="kpi-val">{volumen_m3:.4f} m³</div>
            </div>
            <div class="kpi-metric" style="border-left-color:#16a34a;">
                <div class="kpi-title">Total Estimado a Pagar</div>
                <div class="kpi-val" style="color:#16a34a;">${total_usd:.2f} USD</div>
                <small style="color:#64748b;">Tarifa base aplicada: ${tarifa_lb:.2f} / lb</small>
            </div>
            """, unsafe_allow_html=True)
            
            st.caption("ℹ️ El cálculo final está sujeto a la verificación física y cubicación a la llegada en bodega China.")
            st.markdown('</div>', unsafe_allow_html=True)

    elif menu_cli == "📦 Mi Casillero & Ficha":
        st.markdown("### 🏷️ Instrucciones de Consignación Oficial")
        st.markdown(f"""
        <div class="label-box">
            <strong>SHIP TO / DIRECCIÓN DE BODEGA CHINA (CHILAT):</strong><br>
            ATTN / RECEIVER : CHILAT / {st.session_state['casillero']}<br>
            CLIENT NAME     : {st.session_state['nombre']}<br>
            COUNTRY         : HONDURAS<br>
            <hr style="border-top: 1px dashed #f59e0b; margin: 10px 0;">
            <strong>Mensaje en Chino Mandarín para su Proveedor (Alibaba / Taobao / 1688):</strong><br>
            亲爱的卖家，发货前请务必在每个外箱上牢固张贴唛头。外箱必须清晰标注客户代码：<strong>{st.session_state['casillero']}</strong>，否则仓库将拒收该包裹。
        </div>
        """, unsafe_allow_html=True)

    elif menu_cli == "📜 Historial de Cotizaciones":
        st.markdown("### 📜 Historial de Cotizaciones Realizadas")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd, fecha 
                FROM cotizaciones 
                WHERE codigo_casillero = ? 
                ORDER BY id DESC
            """, (st.session_state["casillero"],))
            rows = c.fetchall()
            
        if rows:
            data = [{
                "Dimensiones (Alto x Ancho x Largo)": f"{r[0]} x {r[1]} x {r[2]} cm",
                "Peso (lb)": f"{r[3]:.1f} lb",
                "Volumen (m³)": f"{r[4]:.4f} m³",
                "Total Cotizado": f"${r[5]:.2f} USD",
                "Fecha": r[6]
            } for r in rows]
            st.dataframe(data, use_container_width=True)
        else:
            st.info("No registra cotizaciones previas.")

# ---------------------------------------------------------
# 7. PANEL DE SUPERUSUARIO (ADMINISTRADOR)
# ---------------------------------------------------------
elif st.session_state["rol"] == "admin":
    st.sidebar.markdown("### 🛠️ Superusuario")
    st.sidebar.caption("Panel de Control General")
    st.sidebar.markdown("---")
    
    menu_adm = st.sidebar.radio(
        "Módulos Administrativos",
        ["📊 Tablero & Control de Casilleros", "💲 Gestión de Tarifas", "📈 Auditoría & Cotizaciones"]
    )
    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar Sesión"):
        cerrar_sesion()

    st.markdown("""
    <div class="top-navbar">
        <h2>🛠️ CCM ENTERPRISE &bull; PANEL ADMINISTRATIVO</h2>
        <div class="user-pill">Acceso Maestro</div>
    </div>
    """, unsafe_allow_html=True)

    if menu_adm == "📊 Tablero & Control de Casilleros":
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'cliente'")
            total_clientes = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM cotizaciones")
            total_cotiz = c.fetchone()[0]
            
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f'<div class="kpi-metric"><div class="kpi-title">Casilleros Registrados</div><div class="kpi-val">{total_clientes}</div></div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="kpi-metric"><div class="kpi-title">Cotizaciones Emitidas</div><div class="kpi-val">{total_cotiz}</div></div>', unsafe_allow_html=True)
        with c3:
            tarifa_actual = get_config("tarifa_libra")
            st.markdown(f'<div class="kpi-metric"><div class="kpi-title">Tarifa por Libra</div><div class="kpi-val">${tarifa_actual:.2f}</div></div>', unsafe_allow_html=True)
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 👥 Directorio de Clientes y Gestión de Estados")
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id, codigo_casillero, nombre_completo, correo, telefono, direccion, activo, fecha_creacion FROM usuarios WHERE rol = 'cliente' ORDER BY id DESC")
            clientes = c.fetchall()
            
        if clientes:
            busqueda = st.text_input("Filtrar por Casillero, Nombre o Correo:", placeholder="Escriba para filtrar...")
            filtrados = [
                cli for cli in clientes 
                if busqueda.lower() in cli[1].lower() or busqueda.lower() in cli[2].lower() or busqueda.lower() in cli[3].lower()
            ]
            
            for cli in filtrados:
                with st.expander(f"📦 {cli[1]} — {cli[2]} ({'🟢 ACTIVO' if cli[6] == 1 else '🔴 BLOQUEADO'})"):
                    col_info, col_acciones = st.columns([2, 1])
                    with col_info:
                        st.write(f"**Correo:** {cli[3]}")
                        st.write(f"**Teléfono:** {cli[4]}")
                        st.write(f"**Dirección:** {cli[5]}")
                        st.write(f"**Fecha Registro:** {cli[7]}")
                    with col_acciones:
                        nuevo_estado = 0 if cli[6] == 1 else 1
                        txt_boton = "🔒 Bloquear Casillero" if cli[6] == 1 else "🔓 Desbloquear Casillero"
                        if st.button(txt_boton, key=f"btn_lock_{cli[0]}"):
                            with get_db() as conn:
                                cur = conn.cursor()
                                cur.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_estado, cli[0]))
                            st.rerun()
                            
                        if st.button("🔑 Reenviar Credenciales", key=f"btn_mail_{cli[0]}"):
                            nueva_clave = generar_password_temporal()
                            p_hash = hash_pass(nueva_clave)
                            with get_db() as conn:
                                cur = conn.cursor()
                                cur.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (p_hash, cli[0]))
                            ok, det = enviar_credenciales_correo(cli[3], cli[2], cli[1], nueva_clave)
                            if ok:
                                st.success("Correo enviado exitosamente.")
                            else:
                                st.warning(f"Clave actualizada a: {nueva_clave} (Fallo correo: {det})")

    elif menu_adm == "💲 Gestión de Tarifas":
        st.markdown("### 💲 Configuración Maestra de Tarifas")
        tarifa_actual = get_config("tarifa_libra")
        tarifa_cbm_actual = get_config("tarifa_cbm")
        
        with st.form("form_tarifas"):
            nueva_tarifa = st.number_input("Tarifa por Libra (USD / lb)", value=float(tarifa_actual), step=0.10)
            nueva_tarifa_cbm = st.number_input("Tarifa por Metro Cúbico (USD / m³)", value=float(tarifa_cbm_actual), step=10.0)
            btn_guardar_tarifa = st.form_submit_button("Actualizar Tarifas")
            
        if btn_guardar_tarifa:
            set_config("tarifa_libra", nueva_tarifa)
            set_config("tarifa_cbm", nueva_tarifa_cbm)
            st.success("✅ Tarifas actualizadas correctamente en todo el sistema.")

    elif menu_adm == "📈 Auditoría & Cotizaciones":
        st.markdown("### 📈 Auditoría de Cotizaciones Emitidas")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT c.id, c.codigo_casillero, u.nombre_completo, c.alto_cm, c.ancho_cm, c.largo_cm, c.peso_lb, c.volumen_m3, c.total_usd, c.fecha
                FROM cotizaciones c
                LEFT JOIN usuarios u ON c.codigo_casillero = u.codigo_casillero
                ORDER BY c.id DESC
            """)
            cotizaciones = c.fetchall()
            
        if cotizaciones:
            data = [{
                "ID": row[0],
                "Casillero": row[1],
                "Cliente": row[2] if row[2] else "N/A",
                "Dimensiones (cm)": f"{row[3]}x{row[4]}x{row[5]}",
                "Peso (lb)": row[6],
                "Volumen (m³)": f"{row[7]:.4f}",
                "Total (USD)": f"${row[8]:.2f}",
                "Fecha": row[9]
            } for row in cotizaciones]
            
            st.dataframe(data, use_container_width=True)
            
            csv_io = io.StringIO()
            writer = csv.writer(csv_io)
            writer.writerow(["ID", "Casillero", "Cliente", "Alto", "Ancho", "Largo", "Peso_lb", "Volumen_m3", "Total_USD", "Fecha"])
            for r in cotizaciones:
                writer.writerow(r)
                
            st.download_button("📥 Exportar Auditoría a Excel (CSV)", data=csv_io.getvalue(), file_name="auditoria_cotizaciones_ccm.csv", mime="text/csv")
        else:
            st.info("No hay cotizaciones registradas en el sistema.")
