import streamlit as st
import sqlite3
import hashlib
import random
import string
import csv
import io
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ---------------------------------------------------------
st.set_page_config(
    page_title="Centro de Cerámicas y Más | Logística",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "casilleros_ccm_v3.db"

# Inyección de estilos inspirados en interfaces ERP / PSKCloud
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    /* Barra lateral estilo ERP */
    [data-testid="stSidebar"] {
        background-color: #0b132b;
        border-right: 1px solid #1c2541;
    }
    [data-testid="stSidebar"] * {
        color: #e0e1dd !important;
    }
    
    /* Tarjetas de Métricas */
    .erp-card {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 1.2rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.04);
        margin-bottom: 1rem;
    }
    .erp-badge {
        background-color: #2563eb;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: bold;
    }
    
    /* Cotizador Box */
    .quote-box {
        background-color: #f8fafc;
        border: 1.5px solid #cbd5e1;
        border-radius: 12px;
        padding: 1.5rem;
        margin-top: 1rem;
    }
    .quote-total {
        font-size: 2rem;
        font-weight: 800;
        color: #16a34a;
    }
    
    /* Botones primarios */
    div.stButton > button:first-child {
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. GESTIÓN DE BASE DE DATOS
# ---------------------------------------------------------
def get_db():
    return sqlite3.connect(DB_NAME)

def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generar_pin():
    return "".join(random.choices(string.digits, k=6))

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        # Tabla de configuración de tarifas
        c.execute("""
            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor REAL
            )
        """)
        # Insertar tarifa inicial por defecto ($3.40 / lb)
        c.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('tarifa_libra', 3.40)")
        c.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('factor_volumen', 166.0)")
        
        # Tabla de usuarios y accesos
        c.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT UNIQUE,
                nombre TEXT NOT NULL,
                correo TEXT UNIQUE NOT NULL,
                telefono TEXT,
                dni TEXT,
                rol TEXT NOT NULL, /* 'admin' o 'cliente' */
                password_hash TEXT NOT NULL,
                fecha_creacion TEXT NOT NULL
            )
        """)
        
        # Tabla de cotizaciones
        c.execute("""
            CREATE TABLE IF NOT EXISTS cotizaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                codigo_casillero TEXT,
                alto REAL,
                ancho REAL,
                largo REAL,
                peso_lb REAL,
                volumen_m3 REAL,
                costo_total REAL,
                fecha TEXT NOT NULL,
                FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
            )
        """)
        
        # Crear Superusuario por defecto si no existe
        admin_correo = "heribertoardon1998@gmail.com"
        c.execute("SELECT id FROM usuarios WHERE correo = ?", (admin_correo,))
        if not c.fetchone():
            c.execute("""
                INSERT INTO usuarios (codigo_casillero, nombre, correo, telefono, dni, rol, password_hash, fecha_creacion)
                VALUES ('CCM-ADMIN-01', 'Domingo Heriberto Ardon', ?, '+504 0000-0000', 'ADMIN-01', 'admin', ?, ?)
            """, (admin_correo, hash_pw("Admin2026!"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

init_db()

# ---------------------------------------------------------
# 3. SERVICIO SMTP DE CORREOS
# ---------------------------------------------------------
def enviar_correo_credenciales(destinatario, nombre, codigo, password_plana, rol):
    try:
        remitente = st.secrets["EMAIL_REMITENTE"]
        password_smtp = st.secrets["EMAIL_PASSWORD"]
    except Exception:
        return False, "Secrets no configurados en Streamlit."

    asunto = f"🔐 Credenciales de Acceso - Centro de Cerámicas y Más [{codigo}]"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #1e293b;">
        <div style="background: #ffffff; max-width: 600px; margin: auto; padding: 25px; border-radius: 10px; border: 1px solid #e2e8f0;">
            <div style="background: #0b132b; color: white; padding: 15px; border-radius: 8px; text-align: center;">
                <h2 style="margin:0; color:#38bdf8;">CENTRO DE CERÁMICAS Y MÁS</h2>
                <p style="margin:0; font-size: 12px; color: #94a3b8;">Sistema de Gestión Logística & Casilleros</p>
            </div>
            
            <p>Estimado(a) <strong>{nombre}</strong>,</p>
            <p>Se ha generado su cuenta en la plataforma con el rol de <strong>{rol.upper()}</strong>. A continuación se detallan sus credenciales de inicio de sesión:</p>
            
            <div style="background: #eff6ff; border-left: 4px solid #2563eb; padding: 15px; border-radius: 6px; margin: 15px 0;">
                <p style="margin: 3px 0;">👤 <strong>Usuario / Correo:</strong> {destinatario}</p>
                <p style="margin: 3px 0;">🔑 <strong>PIN / Contraseña Temporal:</strong> <code style="font-size:16px; color:#1e40af; font-weight:bold;">{password_plana}</code></p>
                <p style="margin: 3px 0;">📦 <strong>Código de Casillero:</strong> {codigo}</p>
            </div>

            <div style="background-color: #fffbeb; border: 1.5px dashed #d97706; padding: 12px; border-radius: 6px; font-family: monospace; font-size: 12px;">
                <strong>DIRECCIÓN DE BODEGA EN CHINA (CONSIGNACIÓN):</strong><br>
                ATTN / RECEIVER: CHILAT / {codigo}<br>
                CLIENT NAME: {nombre}<br>
                DESTINO: HONDURAS
            </div>

            <p style="font-size: 12px; color: #64748b; margin-top: 20px;">
                Por seguridad, puede actualizar su contraseña una vez dentro del portal.
            </p>
        </div>
    </body>
    </html>
    """
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = f"CCM Logística <{remitente}>"
    msg["To"] = destinatario
    msg.attach(MIMEText(html, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(remitente, password_smtp)
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        return True, "Enviado exitosamente."
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------
# 4. GESTIÓN DE SESIÓN Y AUTENTICACIÓN
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
    st.session_state.usuario = None

def login_form():
    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.5, 1])
    with c2:
        st.markdown("""
        <div style="background:#0f172a; padding:20px; border-radius:12px; text-align:center; color:white; margin-bottom:1.5rem;">
            <h2 style="margin:0; color:#38bdf8;">📦 CCM LOGISTICS</h2>
            <p style="margin:0; font-size:0.85rem; color:#94a3b8;">Centro de Cerámicas y Más — Portal de Clientes y Administración</p>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("login_box"):
            correo_input = st.text_input("Correo Electrónico")
            pw_input = st.text_input("Contraseña o PIN", type="password")
            btn_login = st.form_submit_button("Iniciar Sesión", use_container_width=True)
            
            if btn_login:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, codigo_casillero, nombre, correo, rol, password_hash 
                        FROM usuarios WHERE correo = ?
                    """, (correo_input.strip(),))
                    user = cursor.fetchone()
                    
                    if user and user[5] == hash_pw(pw_input):
                        st.session_state.autenticado = True
                        st.session_state.usuario = {
                            "id": user[0],
                            "codigo": user[1],
                            "nombre": user[2],
                            "correo": user[3],
                            "rol": user[4]
                        }
                        st.rerun()
                    else:
                        st.error("Credenciales incorrectas. Verifique su correo o PIN.")

# Si no ha iniciado sesión, mostrar pantalla de Login
if not st.session_state.autenticado:
    login_form()
    st.stop()

# ---------------------------------------------------------
# 5. PANEL PRINCIPAL (POST-LOGIN)
# ---------------------------------------------------------
usuario_actual = st.session_state.usuario

# Barra Lateral con Estilo ERP
with st.sidebar:
    st.markdown(f"""
    <div style="padding:10px 0; border-bottom:1px solid #1c2541; margin-bottom:1rem;">
        <span class="erp-badge">{usuario_actual['rol'].upper()}</span>
        <h4 style="margin:6px 0 0 0; color:#ffffff;">{usuario_actual['nombre']}</h4>
        <small style="color:#94a3b8;">{usuario_actual['codigo']}</small>
    </div>
    """, unsafe_allow_html=True)
    
    if usuario_actual['rol'] == 'admin':
        menu = st.radio(
            "Módulos Administrativos",
            ["📊 Dashboard & Métricas", "👥 Gestión de Usuarios", "⚙️ Parámetros & Tarifas", "🧮 Cotizador Pro"]
        )
    else:
        menu = st.radio(
            "Portal de Usuario",
            ["🧮 Calculadora & Cotizaciones", "🏷️ Mi Ficha de Bodega (China)", "📜 Historial de Mis Cotizaciones"]
        )
        
    st.markdown("---")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        st.session_state.autenticado = False
        st.session_state.usuario = None
        st.rerun()

# ---------------------------------------------------------
# 6. VISTAS DEL SUPERUSUARIO (ADMIN)
# ---------------------------------------------------------
if usuario_actual['rol'] == 'admin':
    
    if menu == "📊 Dashboard & Métricas":
        st.title("📊 Panel de Control y Operaciones")
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'cliente'")
            total_clientes = c.fetchone()[0]
            c.execute("SELECT COUNT(*), IFNULL(SUM(costo_total), 0) FROM cotizaciones")
            total_cotiz, suma_cotiz = c.fetchone()
            
        c1, c2, c3 = st.columns(3)
        c1.metric("Clientes Registrados", total_clientes)
        c2.metric("Cotizaciones Generadas", total_cotiz)
        c3.metric("Volumen Estimado Cotizado", f"${suma_cotiz:,.2f}")
        
        st.markdown("### 📋 Registro General de Operaciones")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT c.id, c.codigo_casillero, u.nombre, c.peso_lb, c.volumen_m3, c.costo_total, c.fecha 
                FROM cotizaciones c
                LEFT JOIN usuarios u ON c.usuario_id = u.id
                ORDER BY c.id DESC
            """)
            rows = c.fetchall()
            
        if rows:
            data = [{
                "ID": r[0], "Casillero": r[1], "Cliente": r[2], "Peso (lb)": f"{r[3]} lb",
                "Volumen": f"{r[4]:.4f} m³", "Total ($)": f"${r[5]:.2f}", "Fecha": r[6]
            } for r in rows]
            st.dataframe(data, use_container_width=True)
        else:
            st.info("Aún no hay cotizaciones registradas en la base de datos.")

    elif menu == "👥 Gestión de Usuarios":
        st.title("👥 Gestión y Alta de Usuarios")
        
        tab_crear, tab_listar = st.tabs(["➕ Registrar Nuevo Usuario", "📋 Directorio de Usuarios"])
        
        with tab_crear:
            with st.form("form_alta_usuario", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    nombre = st.text_input("Nombre Completo *")
                    correo = st.text_input("Correo Electrónico *")
                    telefono = st.text_input("Teléfono / WhatsApp *")
                with c2:
                    dni = st.text_input("DNI / RTN *")
                    rol = st.selectbox("Rol Asignado", ["cliente", "admin"])
                    
                btn_crear = st.form_submit_button("Crear Usuario y Enviar Credenciales", use_container_width=True)
                
            if btn_crear:
                if not (nombre and correo and telefono and dni):
                    st.error("Todos los campos marcados con (*) son obligatorios.")
                else:
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("SELECT COUNT(*) FROM usuarios")
                        count = c.fetchone()[0] + 1
                        nuevo_codigo = f"CCM-HN-{count:03d}" if rol == 'cliente' else f"CCM-ADM-{count:02d}"
                        pin_generado = generar_pin()
                        
                        try:
                            c.execute("""
                                INSERT INTO usuarios (codigo_casillero, nombre, correo, telefono, dni, rol, password_hash, fecha_creacion)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, (nuevo_codigo, nombre, correo.strip(), telefono, dni, rol, hash_pw(pin_generado), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                            
                            enviado, log_correo = enviar_correo_credenciales(correo.strip(), nombre, nuevo_codigo, pin_generado, rol)
                            
                            st.success(f"✅ Usuario creado con éxito. Código asignado: **{nuevo_codigo}**")
                            if enviado:
                                st.info(f"📧 Credenciales enviadas automáticamente a **{correo}**.")
                            else:
                                st.warning(f"⚠️ El usuario se creó pero el correo falló: {log_correo}")
                        except sqlite3.IntegrityError:
                            st.error("Ya existe un usuario con ese correo electrónico.")

        with tab_listar:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id, codigo_casillero, nombre, correo, telefono, dni, rol, fecha_creacion FROM usuarios ORDER BY id DESC")
                users = c.fetchall()
            
            data_u = [{
                "ID": u[0], "Código": u[1], "Nombre": u[2], "Correo": u[3],
                "Teléfono": u[4], "DNI": u[5], "Rol": u[6], "Fecha": u[7]
            } for u in users]
            st.dataframe(data_u, use_container_width=True)

    elif menu == "⚙️ Parámetros & Tarifas":
        st.title("⚙️ Gestión Dinámica de Tarifas")
        st.caption("Modifique las listas de precios en tiempo real sin reiniciar el sistema.")
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT valor FROM configuracion WHERE clave = 'tarifa_libra'")
            t_libra = c.fetchone()[0]
            
        with st.form("form_tarifas"):
            nueva_tarifa = st.number_input("Tarifa Base por Libra ($ USD)", min_value=0.50, max_value=50.00, value=float(t_libra), step=0.10)
            btn_guardar_tarifa = st.form_submit_button("Actualizar Tarifas")
            
            if btn_guardar_tarifa:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE configuracion SET valor = ? WHERE clave = 'tarifa_libra'", (nueva_tarifa,))
                st.success(f"Tarifa actualizada correctamente a ${nueva_tarifa:.2f} / lb.")
                st.rerun()

# ---------------------------------------------------------
# 7. VISTAS DEL CLIENTE Y COTIZADOR
# ---------------------------------------------------------
if usuario_actual['rol'] == 'cliente' or menu == "🧮 Cotizador Pro":
    
    if menu in ["🧮 Calculadora & Cotizaciones", "🧮 Cotizador Pro"]:
        st.title("🧮 Cotizador de Flete Internacional")
        st.caption("Calcule al instante el volumen y costo estimado de flete marítimo/aéreo desde China a Honduras.")
        
        # Obtener tarifa vigente
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT valor FROM configuracion WHERE clave = 'tarifa_libra'")
            tarifa_actual = c.fetchone()[0]
            
        st.info(f"💡 Tarifa vigente aplicada: **${tarifa_actual:.2f} USD / libra**")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### 📏 Dimensiones del Bulto (cm)")
            alto = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0)
            ancho = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0)
            largo = st.number_input("Largo (cm)", min_value=1.0, value=30.0, step=1.0)
            
        with c2:
            st.markdown("##### ⚖️ Peso")
            peso = st.number_input("Peso en Libras (lb)", min_value=0.5, value=5.0, step=0.5)
            
        # Cálculos en tiempo real
        volumen_m3 = (alto * ancho * largo) / 1_000_000.0
        costo_estimado = peso * tarifa_actual
        
        st.markdown(f"""
        <div class="quote-box">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <span style="color:#64748b; font-size:0.9rem;">VOLUMEN CALCULADO:</span><br>
                    <strong style="font-size:1.4rem; color:#0f172a;">{volumen_m3:.4f} m³</strong>
                </div>
                <div style="text-align:right;">
                    <span style="color:#64748b; font-size:0.9rem;">TOTAL ESTIMADO DE FLETE:</span><br>
                    <span class="quote-total">${costo_estimado:.2f} USD</span>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Guardar y Registrar Cotización", use_container_width=True):
            with get_db() as conn:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO cotizaciones (usuario_id, codigo_casillero, alto, ancho, largo, peso_lb, volumen_m3, costo_total, fecha)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    usuario_actual['id'], usuario_actual['codigo'],
                    alto, ancho, largo, peso, volumen_m3, costo_estimado,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
            st.success("✅ Cotización guardada en su historial operativo.")

    elif menu == "🏷️ Mi Ficha de Bodega (China)":
        st.title("🏷️ Instrucciones de Envío a Bodega en China")
        
        st.markdown(f"""
        <div class="erp-card" style="border: 2px dashed #94a3b8; padding: 2rem;">
            <h3 style="margin-top:0; color:#0f172a;">FICHA OFICIAL DE CONSIGNACIÓN</h3>
            <p style="font-family: monospace; font-size: 1rem; line-height: 1.8;">
                <strong>ATTN / RECEIVER:</strong> CHILAT / {usuario_actual['codigo']}<br>
                <strong>CLIENT NAME:</strong> {usuario_actual['nombre']}<br>
                <strong>COUNTRY / DESTINATION:</strong> HONDURAS (CA)<br>
                <strong>CONTACT:</strong> {usuario_actual['correo']}
            </p>
            <div style="background-color:#fffbeb; border-left:4px solid #f59e0b; padding:12px; border-radius:6px; margin-top:1rem; color:#92400e;">
                <strong>⚠️ Mensaje en Chino para el Proveedor de Alibaba / Taobao:</strong><br>
                亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：<strong>{usuario_actual['codigo']}</strong>，否则仓库将拒收该包裹。
            </div>
        </div>
        """, unsafe_allow_html=True)

    elif menu == "📜 Historial de Mis Cotizaciones":
        st.title("📜 Historial de Cotizaciones")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT id, alto, ancho, largo, peso_lb, volumen_m3, costo_total, fecha 
                FROM cotizaciones 
                WHERE usuario_id = ? 
                ORDER BY id DESC
            """, (usuario_actual['id'],))
            mis_cots = c.fetchall()
            
        if mis_cots:
            data_m = [{
                "# Cotización": m[0], "Dimensiones (cm)": f"{m[1]}x{m[2]}x{m[3]}",
                "Peso": f"{m[4]} lb", "Volumen": f"{m[5]:.4f} m³",
                "Total Cotizado": f"${m[6]:.2f}", "Fecha": m[7]
            } for m in mis_cots]
            st.dataframe(data_m, use_container_width=True)
        else:
            st.info("Aún no tienes cotizaciones guardadas.")
