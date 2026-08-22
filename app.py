import streamlit as st
import sqlite3
import csv
from datetime import datetime
import io
import urllib.parse

# 1. Configuración de la Aplicación
st.set_page_config(
    page_title="CCM Logistics Hub | Casilleros",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "casilleros_ccm.db"

# 2. CSS de Alto Impacto (Estilo Enterprise Logistics)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
        background-color: #F8FAFC;
    }
    
    /* Ocultar barra superior por defecto de streamlit */
    #MainMenu, header, footer {visibility: hidden;}

    /* Banner Principal */
    .brand-hero {
        background: linear-gradient(135deg, #0F172A 0%, #1E293B 40%, #1E3A8A 100%);
        border-radius: 20px;
        padding: 2.5rem 2rem;
        color: white;
        box-shadow: 0 15px 30px -10px rgba(15, 23, 42, 0.3);
        margin-bottom: 2rem;
        border: 1px solid rgba(255, 255, 255, 0.1);
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

    /* Tarjetas de Métricas */
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
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .metric-box:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05);
    }
    .metric-name {
        font-size: 0.8rem;
        color: #64748B;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-number {
        font-size: 1.9rem;
        font-weight: 800;
        color: #0F172A;
        margin-top: 6px;
    }

    /* Etiqueta de Bodega Estilo Courier */
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
    .bill-data strong {
        color: #0F172A;
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

    /* Estilización de Formularios e Inputs */
    .stTextInput input, .stTextArea textarea {
        border-radius: 10px !important;
        border: 1px solid #CBD5E1 !important;
        padding: 12px 14px !important;
        background-color: #FFFFFF !important;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #2563EB !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15) !important;
    }
    
    /* Botón Primario */
    div.stButton > button:first-child {
        background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%) !important;
        color: white !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 0.75rem 1.8rem !important;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25) !important;
        transition: all 0.2s ease !important;
    }
    div.stButton > button:first-child:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 20px rgba(37, 99, 235, 0.35) !important;
    }
</style>
""", unsafe_allow_html=True)

# 3. Lógica de Base de Datos
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
        cursor.execute("SELECT id, codigo_casillero, nombre_completo, rtn_dni, telefono, departamento, municipio, direccion_entrega, fecha_registro FROM clientes ORDER BY id DESC")
        return cursor.fetchall()

def contar_clientes():
    with obtener_conexion() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clientes")
        return cursor.fetchone()[0]

inicializar_bd()

# 4. Hero Header Corporativo
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

# 5. Pestañas de Navegación Ejecutiva
tab1, tab2, tab3 = st.tabs([
    "✨  Apertura de Casillero", 
    "📊  Directorio & Métricas", 
    "🏷️  Generador de Guías"
])

# ---------------------------------------------------------
# PESTAÑA 1: APERTURA DE CASILLERO
# ---------------------------------------------------------
with tab1:
    codigo_siguiente = generar_codigo_automatico()
    
    st.markdown(f"#### 📝 Datos de Apertura — Código Asignado: **`{codigo_siguiente}`**")
    st.caption("Los códigos se generan de forma correlativa y única para garantizar la trazabilidad aduanal.")
    
    with st.form("form_alta", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            nombre = st.text_input("Nombre y Apellidos *", placeholder="Ej. Roberto Castillo")
            dni = st.text_input("DNI o RTN *", placeholder="Ej. 0501199201234")
            telefono = st.text_input("WhatsApp / Celular *", placeholder="Ej. +504 9988-7766")
        with c2:
            depto = st.text_input("Departamento *", placeholder="Ej. Cortés")
            ciudad = st.text_input("Municipio / Ciudad *", placeholder="Ej. San Pedro Sula")
            direccion = st.text_area("Dirección Exacta de Destino Final *", placeholder="Col. Moderna, 3ra calle, 14 avenida...")
            
        guardar = st.form_submit_button("🚀 Confirmar Apertura y Crear Ficha")
        
    if guardar:
        if not (nombre and dni and telefono and depto and ciudad and direccion):
            st.error("⚠️ Complete todos los campos obligatorios (*).")
        else:
            fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with obtener_conexion() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO clientes (
                            codigo_casillero, nombre_completo, rtn_dni, 
                            telefono, departamento, municipio, 
                            direccion_entrega, fecha_registro
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (codigo_siguiente, nombre, dni, telefono, depto, ciudad, direccion, fecha_hora))
                
                st.balloons()
                st.success(f"✅ ¡Casillero aperturado con éxito para {nombre}!")
                
                # Ficha Visual Airway Bill
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
                        <strong>CONTACT:</strong> {telefono}
                    </div>
                    <div class="chinese-instructions">
                        <strong>⚠️ 中文说明 (Para el proveedor chino en Alibaba / Taobao):</strong><br>
                        亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：<strong>{codigo_siguiente}</strong>，否则仓库将拒收该包裹。
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Botón de enlace directo a WhatsApp
                msg_whatsapp = (
                    f"📦 *CENTRO DE CERÁMICAS Y MÁS — FICHA DE CASILLERO*\n\n"
                    f"Estimado(a) {nombre}, su casillero ha sido activado:\n\n"
                    f"🔑 *CÓDIGO:* {codigo_siguiente}\n"
                    f"📍 *ATTN:* CHILAT / {codigo_siguiente}\n"
                    f"🏷️ *CLIENT:* {nombre}\n"
                    f"🇭🇳 *DESTINO:* HONDURAS\n\n"
                    f"⚠️ *Nota para el proveedor en China:*\n"
                    f"Dear supplier, please paste shipping mark with code: {codigo_siguiente} on every box."
                )
                url_wa = f"https://wa.me/{telefono.replace('+', '').replace(' ', '').replace('-', '')}?text={urllib.parse.quote(msg_whatsapp)}"
                st.markdown(f'<a href="{url_wa}" target="_blank"><button style="background-color:#22C55E; color:white; border:none; padding:10px 18px; border-radius:8px; font-weight:700; cursor:pointer;">📲 Enviar Ficha por WhatsApp</button></a>', unsafe_allow_html=True)
                
            except sqlite3.Error as e:
                st.error(f"❌ Error en base de datos: {e}")

# ---------------------------------------------------------
# PESTAÑA 2: DIRECTORIO Y MÉTRICAS
# ---------------------------------------------------------
with tab2:
    total_registrados = contar_clientes()
    
    # Métricas Dashboard
    st.markdown(f"""
    <div class="metric-grid">
        <div class="metric-box">
            <div class="metric-name">Total de Clientes</div>
            <div class="metric-number">{total_registrados}</div>
        </div>
        <div class="metric-box">
            <div class="metric-name">Hub Principal</div>
            <div class="metric-number" style="font-size:1.4rem;">🇨🇳 Chilat Warehouse</div>
        </div>
        <div class="metric-box">
            <div class="metric-name">Cobertura Nacional</div>
            <div class="metric-number" style="font-size:1.4rem;">🇭🇳 Todo Honduras</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    clientes = obtener_todos_los_clientes()
    if not clientes:
        st.info("📦 Aún no hay registros de clientes en la base de datos.")
    else:
        st.markdown("#### 🔍 Base de Datos Activa")
        busqueda = st.text_input("Buscador rápido (Nombre, código, DNI o WhatsApp):", placeholder="Escribe para filtrar...")
        
        filtrados = [
            c for c in clientes
            if busqueda.lower() in str(c[1]).lower() or 
               busqueda.lower() in str(c[2]).lower() or 
               busqueda.lower() in str(c[3]).lower() or
               busqueda.lower() in str(c[4]).lower()
        ]
        
        datos_tabla = [{
            "Código": c[1],
            "Cliente": c[2],
            "RTN / DNI": c[3],
            "WhatsApp": c[4],
            "Ciudad": c[6],
            "Departamento": c[5],
            "Dirección": c[7],
            "Fecha Registro": c[8]
        } for c in filtrados]
        
        st.dataframe(datos_tabla, use_container_width=True)
        
        # Botón de Descarga Excel
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["ID", "Código", "Nombre", "DNI", "Teléfono", "Departamento", "Municipio", "Dirección", "Fecha Registro"])
        for c in clientes:
            writer.writerow(c)
            
        st.download_button(
            label="📥 Exportar Base Completa a Excel (CSV)",
            data=csv_buffer.getvalue(),
            file_name=f"casilleros_ccm_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )

# ---------------------------------------------------------
# PESTAÑA 3: RE-GENERADOR DE ETIQUETA
# ---------------------------------------------------------
with tab3:
    st.markdown("#### 🏷️ Consultar y Reimprimir Ficha de Envío")
    clientes = obtener_todos_los_clientes()
    
    if not clientes:
        st.warning("No hay clientes registrados en el sistema.")
    else:
        mapeo = {f"{c[1]} — {c[2]} ({c[6]})": c for c in clientes}
        seleccion = st.selectbox("Selecciona un cliente de la lista:", list(mapeo.keys()))
        c = mapeo[seleccion]
        
        st.markdown(f"""
        <div class="airway-bill">
            <div class="bill-header">
                <div>
                    <span class="bill-title">SHIPPING LABEL / GUÍA DE IMPORTACIÓN</span><br>
                    <span style="font-size:0.8rem; color:#64748B;">CONSOLIDADO MARÍTIMO Y AÉREO</span>
                </div>
                <div class="barcode">||| {c[1]} |||</div>
            </div>
            <div class="bill-data">
                <strong>ATTN / RECEIVER:</strong> CHILAT / {c[1]}<br>
                <strong>CLIENT NAME:</strong> {c[2]}<br>
                <strong>RTN / DNI:</strong> {c[3]}<br>
                <strong>PHONE:</strong> {c[4]}<br>
                <strong>DESTINATION:</strong> {c[6]}, {c[5]} (HONDURAS)
            </div>
            <div class="chinese-instructions">
                <strong>⚠️ 中文说明 (Para el proveedor chino):</strong><br>
                亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：<strong>{c[1]}</strong>，否则仓库将拒收该包裹。
            </div>
        </div>
        """, unsafe_allow_html=True)
