import streamlit as st
import sqlite3
import csv
from datetime import datetime
import io
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 1. Configuración General
st.set_page_config(
    page_title="CCM Logistics Hub | Casilleros",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "casilleros_ccm_v2.db"

# 2. Estilos Visuales
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
        background-color: #F8FAFC;
    }
    
    #MainMenu, header, footer {visibility: hidden;}

    .brand-hero {
        background: linear-gradient(135deg, #0F172A 0%, #1E293B 40%, #1E3A8A 100%);
        border-radius: 20px;
        padding: 2.2rem 2rem;
        color: white;
        box-shadow: 0 15px 30px -10px rgba(15, 23, 42, 0.3);
        margin-bottom: 2rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
    }
    .brand-title {
        font-size: 2.2rem;
        font-weight: 800;
        letter-spacing: -0.5px;
        color: #FFFFFF;
        margin: 0;
    }
    .brand-subtitle {
        font-size: 0.95rem;
        color: #94A3B8;
        margin-top: 4px;
    }
    .badge-route {
        background: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.2);
        padding: 8px 16px;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 600;
        color: #38BDF8;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1.2rem;
        margin-bottom: 2rem;
    }
    .metric-box {
        background: white;
        border-radius: 16px;
        padding: 1.4rem;
        border: 1px solid #E2E8F0;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.02);
    }
    .metric-name {
        font-size: 0.8rem;
        color: #64748B;
        font-weight: 600;
        text-transform: uppercase;
    }
    .metric-number {
        font-size: 1.9rem;
        font-weight: 800;
        color: #0F172A;
        margin-top: 6px;
    }
    .airway-bill {
        background: #FFFFFF;
        border: 2px dashed #94A3B8;
        border-radius: 16px;
        padding: 2rem;
        position: relative;
        box-shadow: 0 10px 25px -5px rgba(0,0,0,0.04);
        margin: 1.5rem 0;
    }
    .bill-header {
        display: flex;
        justify-content: space-between;
        border-bottom: 2px solid #0F172A;
        padding-bottom: 1rem;
        margin-bottom: 1.2rem;
    }
    .bill-title {
        font-size: 1.2rem;
        font-weight: 800;
        color: #0F172A;
    }
    .barcode {
        font-family: 'Space Mono', monospace;
        letter-spacing: 4px;
        font-weight: 700;
        background: #F1F5F9;
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.85rem;
    }
    .bill-data {
        font-family: 'Space Mono', monospace;
        color: #1E293B;
        font-size: 0.95rem;
        line-height: 1.8;
    }
    .chinese-instructions {
        background: #FFFBEB;
        border-left: 4px solid #F59E0B;
        padding: 12px 16px;
        border-radius: 8px;
        margin-top: 1rem;
        color: #92400E;
        font-size: 0.9rem;
    }
    div.stButton > button:first-child {
        background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%) !important;
        color: white !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 0.75rem 1.8rem !important;
    }
</style>
""", unsafe_allow_html=True)

# 3. Base de Datos
def obtener_conexion():
    return sqlite3.connect(DB_NAME)

def inicializar_bd():
    with obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT UNIQUE NOT NULL,
                nombre_completo TEXT NOT NULL,
                rtn_dni TEXT NOT NULL,
                telefono TEXT NOT NULL,
                correo TEXT NOT NULL,
                departamento TEXT NOT NULL,
                municipio TEXT NOT NULL,
                direccion_entrega TEXT NOT NULL,
                fecha_registro TEXT NOT NULL
            )
        """)

def generar_codigo_automatico():
    with obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM clientes ORDER BY id DESC LIMIT 1")
        ultimo = cursor.fetchone()
        siguiente_id = 1 if ultimo is None else ultimo[0] + 1
        return f"CCM-HN-{siguiente_id:03d}"

def obtener_todos_los_clientes():
    with obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, codigo_casillero, nombre_completo, rtn_dni, telefono, correo, departamento, municipio, direccion_entrega, fecha_registro FROM clientes ORDER BY id DESC")
        return cursor.fetchall()

def contar_clientes():
    with obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clientes")
        return cursor.fetchone()[0]

# 4. Envío de Correo Electrónico
def enviar_correo_bienvenida(destinatario, nombre_cliente, codigo_casillero, telefono, depto, municipio, direccion):
    try:
        remitente = st.secrets["EMAIL_REMITENTE"]
        password = st.secrets["EMAIL_PASSWORD"]
    except Exception:
        return False, "Credenciales no configuradas en Secrets de Streamlit."

    asunto = f"📦 Apertura de Casillero Exitoso - {codigo_casillero} (Centro de Cerámicas y Más)"
    cuerpo_html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #1e293b;">
        <div style="background: #ffffff; max-width: 650px; margin: auto; padding: 25px; border-radius: 12px; border: 1px solid #e2e8f0;">
            <div style="background: #0f172a; color: #ffffff; padding: 18px; border-radius: 8px; text-align: center;">
                <h2 style="margin:0; color:#38bdf8;">CENTRO DE CERÁMICAS Y MÁS</h2>
                <p style="margin:4px 0 0 0; font-size:12px; color:#94a3b8;">Sistema de Casilleros e Importaciones Directas China ➔ Honduras</p>
            </div>
            
            <p>Estimado(a) <strong>{nombre_cliente}</strong>,</p>
            <p>Su casillero internacional ha sido activado con éxito. A continuación encontrará sus datos registrados y la <strong>Ficha Oficial de Consignación</strong> para sus proveedores:</p>
            
            <div style="text-align: center; margin: 15px 0;">
                <span style="font-size: 22px; font-weight: bold; color: #2563eb; background: #eff6ff; padding: 8px 18px; border-radius: 6px; border: 1px solid #bfdbfe;">
                    CÓDIGO ASIGNADO: {codigo_casillero}
                </span>
            </div>

            <div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 8px; margin-bottom: 15px; font-size: 13px;">
                <strong>📋 Datos de Entrega en Honduras:</strong><br>
                • <strong>Teléfono / WhatsApp:</strong> {telefono}<br>
                • <strong>Destino Final:</strong> {municipio}, {depto}<br>
                • <strong>Dirección de Entrega:</strong> {direccion}
            </div>

            <div style="background-color: #fffbeb; border: 2px dashed #d97706; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 13px;">
                <strong>📦 SHIP TO / DIRECCIÓN EN BODEGA CHINA (CHILAT):</strong><br>
                ATTN / RECEIVER : CHILAT / {codigo_casillero}<br>
                CLIENT NAME     : {nombre_cliente}<br>
                COUNTRY         : HONDURAS<br>
                TEL             : {telefono}<br>
                <hr style="border-top: 1px dashed #d97706; margin: 10px 0;">
                <strong>Instrucciones para el vendedor en China (Alibaba / Taobao):</strong><br>
                "Dear supplier, please ensure you paste our shipping label firmly on each box before dispatching. Packages without Client Code: {codigo_casillero} will be rejected."<br><br>
                <strong>中文说明:</strong><br>
                亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：{codigo_casillero}，否则仓库将拒收该包裹。
            </div>

            <p style="font-size:12px; color:#64748b; margin-top:20px; text-align: center;">
                Centro de Cerámicas y Más • Servicios Logísticos Internacionales
            </p>
        </div>
    </body>
    </html>
    """

    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = asunto
    mensaje["From"] = f"Centro de Cerámicas y Más <{remitente}>"
    mensaje["To"] = destinatario
    mensaje.attach(MIMEText(cuerpo_html, "html"))

    try:
        servidor = smtplib.SMTP("smtp.gmail.com", 587)
        servidor.starttls()
        servidor.login(remitente, password)
        servidor.sendmail(remitente, destinatario, mensaje.as_string())
        servidor.quit()
        return True, "Correo enviado exitosamente"
    except Exception as e:
        return False, str(e)

inicializar_bd()

# 5. Encabezado Principal
st.markdown("""
<div class="brand-hero">
    <div>
        <h1 class="brand-title">📦 CENTRO DE CERÁMICAS Y MÁS</h1>
        <div class="brand-subtitle">Plataforma Logística de Casilleros & Importaciones Consolidadas</div>
    </div>
    <div class="badge-route">
        🇨🇳 Yiwu / Guangzhou ➔ 🇭🇳 Honduras
    </div>
</div>
""", unsafe_allow_html=True)

# 6. Navegación por Pestañas
tab1, tab2, tab3 = st.tabs([
    "✨  Apertura de Casillero", 
    "📊  Directorio & Métricas", 
    "🏷️  Generador de Guías"
])

# --- PESTAÑA 1: FORMULARIO DE REGISTRO ---
with tab1:
    codigo_siguiente = generar_codigo_automatico()
    st.markdown(f"#### 📝 Registro de Cliente — Código Asignado: **`{codigo_siguiente}`**")
    
    with st.form("form_alta", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre Completo *", placeholder="Ej. Roberto Castillo")
            dni = st.text_input("DNI o RTN *", placeholder="Ej. 0501199201234")
            telefono = st.text_input("WhatsApp / Celular *", placeholder="Ej. +504 9988-7766")
            correo = st.text_input("Correo Electrónico (Para recibir ficha) *", placeholder="cliente@gmail.com")
        with col2:
            depto = st.text_input("Departamento *", placeholder="Ej. Cortés")
            ciudad = st.text_input("Municipio / Ciudad *", placeholder="Ej. San Pedro Sula")
            direccion = st.text_area("Dirección Exacta de Destino Final *", placeholder="Col. Moderna, 3ra calle...")
            
        guardar = st.form_submit_button("🚀 Confirmar Registro y Enviar Ficha")
        
    if guardar:
        if not (nombre and dni and telefono and correo and depto and ciudad and direccion):
            st.error("⚠️ Complete todos los campos obligatorios (*).")
        else:
            fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with obtener_conexion() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO clientes (
                            codigo_casillero, nombre_completo, rtn_dni, 
                            telefono, correo, departamento, municipio, 
                            direccion_entrega, fecha_registro
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (codigo_siguiente, nombre, dni, telefono, correo, depto, ciudad, direccion, fecha_hora))
                
                st.balloons()
                st.success(f"✅ ¡Casillero aperturado con éxito para **{nombre}**!")
                
                # Envío de correo
                enviado, detalle = enviar_correo_bienvenida(correo, nombre, codigo_siguiente, telefono, depto, ciudad, direccion)
                if enviado:
                    st.info(f"📧 Se envió la ficha completa al correo: **{correo}**")
                else:
                    st.warning(f"⚠️ Cliente guardado, pero no se pudo enviar correo: {detalle}")
                
                # Ficha en pantalla
                st.markdown(f"""
                <div class="airway-bill">
                    <div class="bill-header">
                        <div>
                            <span class="bill-title">SHIPPING INSTRUCTIONS / FICHA OFICIAL</span><br>
                            <span style="font-size:0.8rem; color:#64748B;">CCM WAREHOUSE CONSOLIDATION</span>
                        </div>
                        <div class="barcode">||| {codigo_siguiente} |||</div>
                    </div>
                    <div class="bill-data">
                        <strong>ATTN / RECEIVER:</strong> CHILAT / {codigo_siguiente}<br>
                        <strong>CLIENT NAME:</strong> {nombre}<br>
                        <strong>DESTINATION:</strong> HONDURAS (CA)<br>
                        <strong>CONTACT:</strong> {telefono}<br>
                        <strong>EMAIL:</strong> {correo}
                    </div>
                    <div class="chinese-instructions">
                        <strong>⚠️ 中文说明 (Para el proveedor chino):</strong><br>
                        亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：<strong>{codigo_siguiente}</strong>，否则仓库将拒收该包裹。
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # WhatsApp
                msg_whatsapp = (
                    f"📦 *CENTRO DE CERÁMICAS Y MÁS — FICHA DE CASILLERO*\n\n"
                    f"Estimado(a) {nombre}, su casillero ha sido activado:\n\n"
                    f"🔑 *CÓDIGO:* {codigo_siguiente}\n"
                    f"📍 *ATTN:* CHILAT / {codigo_siguiente}\n"
                    f"🏷️ *CLIENT:* {nombre}\n"
                    f"🇭🇳 *DESTINO:* HONDURAS"
                )
                url_wa = f"https://wa.me/{telefono.replace('+', '').replace(' ', '').replace('-', '')}?text={urllib.parse.quote(msg_whatsapp)}"
                st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background-color:#22C55E; color:white; border:none; padding:10px 18px; border-radius:8px; font-weight:700; cursor:pointer;">📲 Enviar Ficha por WhatsApp</button></a>', unsafe_allow_html=True)
                
            except sqlite3.Error as e:
                st.error(f"❌ Error en base de datos: {e}")

# --- PESTAÑA 2: DIRECTORIO ---
with tab2:
    total_registrados = contar_clientes()
    st.markdown(f"""
    <div class="metric-grid">
        <div class="metric-box"><div class="metric-name">Total Clientes</div><div class="metric-number">{total_registrados}</div></div>
        <div class="metric-box"><div class="metric-name">Bodega Origen</div><div class="metric-number" style="font-size:1.4rem;">🇨🇳 Chilat Warehouse</div></div>
        <div class="metric-box"><div class="metric-name">Destino</div><div class="metric-number" style="font-size:1.4rem;">🇭🇳 Honduras</div></div>
    </div>
    """, unsafe_allow_html=True)
    
    clientes = obtener_todos_los_clientes()
    if not clientes:
        st.info("📦 Aún no hay registros en el sistema.")
    else:
        busqueda = st.text_input("Buscador rápido:", placeholder="Filtrar por nombre, código, teléfono o correo...")
        filtrados = [
            c for c in clientes
            if busqueda.lower() in str(c[1]).lower() or 
               busqueda.lower() in str(c[2]).lower() or 
               busqueda.lower() in str(c[4]).lower() or
               busqueda.lower() in str(c[5]).lower()
        ]
        
        datos_tabla = [{
            "Código": c[1], "Cliente": c[2], "RTN/DNI": c[3], "WhatsApp": c[4],
            "Correo": c[5], "Ubicación": f"{c[7]}, {c[6]}", "Dirección": c[8], "Fecha": c[9]
        } for c in filtrados]
        
        st.dataframe(datos_tabla, use_container_width=True)
        
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["ID", "Código", "Nombre", "DNI", "Teléfono", "Correo", "Departamento", "Municipio", "Dirección", "Fecha"])
        for c in clientes:
            writer.writerow(c)
            
        st.download_button("📥 Descargar Base a Excel (CSV)", data=csv_buffer.getvalue(), file_name="casilleros_ccm.csv", mime="text/csv")

# --- PESTAÑA 3: ETIQUETAS ---
with tab3:
    st.markdown("#### 🏷️ Re-enviar o Consultar Ficha")
    clientes = obtener_todos_los_clientes()
    if clientes:
        mapeo = {f"{c[1]} — {c[2]}": c for c in clientes}
        sel = st.selectbox("Selecciona un cliente:", list(mapeo.keys()))
        c = mapeo[sel]
        
        st.markdown(f"""
        <div class="airway-bill">
            <div class="bill-header">
                <div><span class="bill-title">SHIPPING LABEL</span></div>
                <div class="barcode">||| {c[1]} |||</div>
            </div>
            <div class="bill-data">
                <strong>ATTN / RECEIVER:</strong> CHILAT / {c[1]}<br>
                <strong>CLIENT NAME:</strong> {c[2]}<br>
                <strong>PHONE:</strong> {c[4]}<br>
                <strong>EMAIL:</strong> {c[5]}
            </div>
        </div>
        """, unsafe_allow_html=True)
