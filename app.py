import streamlit as st
import sqlite3
import csv
from datetime import datetime
import io

# 1. Configuración de página
st.set_page_config(
    page_title="Centro de Cerámicas y Más | Casilleros",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "casilleros_ccm.db"

# 2. Estilos Visuales Premium (CSS)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    /* Encabezado Principal */
    .hero-banner {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #1e3a8a 100%);
        color: #ffffff;
        padding: 2.2rem 2rem;
        border-radius: 16px;
        box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.3);
        margin-bottom: 2rem;
        text-align: center;
    }
    .hero-banner h1 {
        font-size: 2.3rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.5px;
        color: #f8fafc;
    }
    .hero-banner p {
        font-size: 1rem;
        color: #94a3b8;
        margin: 6px 0 0 0;
    }

    /* Tarjetas Métricas */
    .kpi-container {
        display: flex;
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    .kpi-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1.2rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        border-top: 4px solid #2563eb;
    }
    .kpi-title {
        font-size: 0.85rem;
        color: #64748b;
        text-transform: uppercase;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    .kpi-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0f172a;
        margin-top: 4px;
    }

    /* Etiqueta Shipping Label Realista */
    .shipping-box {
        background-color: #ffffff;
        border: 2px dashed #cbd5e1;
        border-radius: 12px;
        padding: 1.8rem;
        font-family: 'JetBrains Mono', monospace;
        color: #1e293b;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
        position: relative;
    }
    .shipping-box::before {
        content: "AIR FREIGHT / EXPRESS";
        position: absolute;
        top: 15px;
        right: 20px;
        font-size: 0.75rem;
        font-weight: 700;
        background: #f1f5f9;
        padding: 4px 8px;
        border-radius: 4px;
        color: #475569;
    }
    .code-badge {
        font-size: 1.4rem;
        font-weight: 700;
        color: #2563eb;
        background: #eff6ff;
        padding: 4px 10px;
        border-radius: 6px;
        display: inline-block;
        margin-bottom: 0.5rem;
    }

    /* Botón Formulario */
    .stButton>button {
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.6rem 1.4rem;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    .stButton>button:hover {
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
        transform: translateY(-1px);
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

# 4. Encabezado General
st.markdown("""
<div class="hero-banner">
    <h1>📦 CENTRO DE CERÁMICAS Y MÁS</h1>
    <p>Sistema Operativo de Casilleros e Importaciones Directas China ➔ Honduras</p>
</div>
""", unsafe_allow_html=True)

# 5. Barra Lateral
st.sidebar.markdown("### 🛠️ Panel de Gestión")
opcion = st.sidebar.radio(
    "Selecciona una vista:",
    ["📝 Registro de Clientes", "📊 Directorio & Reportes", "🏷️ Reimpresión de Etiquetas"]
)
st.sidebar.markdown("---")
st.sidebar.caption("⚡ CCM Logística v2.0 • Conexión Segura")

# 6. MÓDULO 1: Registro
if opcion == "📝 Registro de Clientes":
    st.markdown("### 📝 Alta de Nuevo Casillero")
    siguiente_codigo = generar_codigo_automatico()
    
    st.info(f"✨ El próximo cliente recibirá automáticamente el código: **{siguiente_codigo}**")
    
    with st.form("form_alta_cliente", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre Completo *", placeholder="Ej. Carlos Mendoza")
            rtn = st.text_input("DNI / RTN *", placeholder="Ej. 0801-1995-12345")
            telefono = st.text_input("WhatsApp / Teléfono *", placeholder="Ej. +504 9876-5432")
        with col2:
            depto = st.text_input("Departamento *", placeholder="Ej. Cortés")
            municipio = st.text_input("Municipio / Ciudad *", placeholder="Ej. San Pedro Sula")
            direccion = st.text_area("Dirección Exacta de Retiro / Entrega *", placeholder="Bo. Guamilito, 7ma avenida...")
            
        btn_crear = st.form_submit_button("🚀 Generar Casillero y Crear Ficha")
        
    if btn_crear:
        if not (nombre and rtn and telefono and depto and municipio and direccion):
            st.error("⚠️ Por favor completa todos los campos requeridos (*).")
        else:
            fecha_ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with obtener_conexion() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO clientes (
                        codigo_casillero, nombre_completo, rtn_dni, 
                        telefono, departamento, municipio, 
                        direccion_entrega, fecha_registro
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (siguiente_codigo, nombre, rtn, telefono, depto, municipio, direccion, fecha_ahora))
                
            st.balloons()
            st.success(f"🎉 Casillero asignado exitosamente a **{nombre}**")
            
            # Ficha visual
            st.markdown(f"""
            <div class="shipping-box">
                <div class="code-badge">{siguiente_codigo}</div><br>
                <strong>CLIENT NAME:</strong> {nombre}<br>
                <strong>ATTN / RECEIVER:</strong> CHILAT / {siguiente_codigo}<br>
                <strong>DESTINATION:</strong> HONDURAS<br>
                <strong>CONTACT:</strong> {telefono}<br>
                <hr style="border-top: 1px dashed #cbd5e1; margin: 12px 0;">
                <span style="font-size: 0.85rem; color: #64748b;">
                中文说明 (Mensaje para proveedor chino):<br>
                "亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：{siguiente_codigo}，否则仓库将拒收该包裹。"
                </span>
            </div>
            """, unsafe_allow_html=True)

# 7. MÓDULO 2: Directorio & Métricas
elif opcion == "📊 Directorio & Reportes":
    total = contar_clientes()
    
    # Tarjetas KPI
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""<div class="kpi-card"><div class="kpi-title">Casilleros Activos</div><div class="kpi-value">{total}</div></div>""", unsafe_allow_html=True)
    with col2:
        st.markdown("""<div class="kpi-card"><div class="kpi-title">Bodega Origen</div><div class="kpi-value" style="font-size:1.4rem;">🇨🇳 Chilat (China)</div></div>""", unsafe_allow_html=True)
    with col3:
        st.markdown("""<div class="kpi-card"><div class="kpi-title">Destino Final</div><div class="kpi-value" style="font-size:1.4rem;">🇭🇳 Honduras</div></div>""", unsafe_allow_html=True)
        
    st.markdown("<br>", unsafe_allow_html=True)
    
    clientes = obtener_todos_los_clientes()
    if not clientes:
        st.info("📦 Aún no hay clientes registrados.")
    else:
        busqueda = st.text_input("🔍 Filtro rápido (Nombre, código o teléfono):", "")
        filtrados = [
            c for c in clientes
            if busqueda.lower() in str(c[1]).lower() or busqueda.lower() in str(c[2]).lower() or busqueda.lower() in str(c[4]).lower()
        ]
        
        datos = [{
            "Código": c[1],
            "Cliente": c[2],
            "DNI / RTN": c[3],
            "WhatsApp": c[4],
            "Ubicación": f"{c[6]}, {c[5]}",
            "Dirección": c[7],
            "Fecha": c[8]
        } for c in filtrados]
        
        st.dataframe(datos, use_container_width=True)
        
        # Descarga
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["ID", "Código", "Nombre", "DNI", "Teléfono", "Departamento", "Municipio", "Dirección", "Fecha Registro"])
        for c in clientes:
            writer.writerow(c)
            
        st.download_button(
            label="📥 Descargar Base de Datos a Excel (CSV)",
            data=buffer.getvalue(),
            file_name=f"casilleros_ccm_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )

# 8. MÓDULO 3: Reimpresión
elif opcion == "🏷️ Reimpresión de Etiquetas":
    st.markdown("### 🏷️ Consulta y Reimpresión")
    clientes = obtener_todos_los_clientes()
    
    if not clientes:
        st.warning("No hay clientes en el registro.")
    else:
        mapeo = {f"{c[1]} — {c[2]}": c for c in clientes}
        sel = st.selectbox("Selecciona un casillero registrado:", list(mapeo.keys()))
        c = mapeo[sel]
        
        st.markdown(f"""
        <div class="shipping-box">
            <div class="code-badge">{c[1]}</div><br>
            <strong>CLIENT NAME:</strong> {c[2]}<br>
            <strong>ATTN / RECEIVER:</strong> CHILAT / {c[1]}<br>
            <strong>DESTINATION:</strong> HONDURAS<br>
            <strong>PHONE:</strong> {c[4]}<br>
            <hr style="border-top: 1px dashed #cbd5e1; margin: 12px 0;">
            <span style="font-size: 0.85rem; color: #64748b;">
            中文说明 (Instrucciones para el vendedor):<br>
            "亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：{c[1]}，否则仓库将拒收该包裹。"
            </span>
        </div>
        """, unsafe_allow_html=True)
