import streamlit as st
import sqlite3
import csv
from datetime import datetime
import io

st.set_page_config(
    page_title="Centro de Cerámicas y Más - Casilleros",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "casilleros_ccm.db"

st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        color: #1E3A8A;
        font-weight: 700;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #4B5563;
        text-align: center;
        margin-bottom: 2rem;
    }
    .shipping-label {
        background-color: #FFFBEB;
        border: 2px dashed #D97706;
        padding: 20px;
        border-radius: 12px;
        font-family: monospace;
    }
</style>
""", unsafe_allow_html=True)

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

st.markdown('<div class="main-header">📦 CENTRO DE CERÁMICAS Y MÁS</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Sistema de Gestión de Casilleros e Importaciones China - Honduras</div>', unsafe_allow_html=True)

st.sidebar.title("Navegación")
opcion = st.sidebar.radio(
    "Selecciona un módulo:",
    ["📝 Registrar Nuevo Cliente", "📊 Dashboard & Lista de Clientes", "🏷️ Generador de Etiqueta"]
)

if opcion == "📝 Registrar Nuevo Cliente":
    st.subheader("📋 Registro de Cliente y Asignación de Casillero")
    nuevo_codigo = generar_codigo_automatico()
    st.info(f"🔑 Código asignado para el siguiente registro: **{nuevo_codigo}**")
    
    with st.form("form_registro", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre Completo *", placeholder="Ej. Juan Pérez")
            rtn_dni = st.text_input("DNI / RTN *", placeholder="Ej. 0801199012345")
            telefono = st.text_input("Teléfono / WhatsApp *", placeholder="Ej. +504 9999-9999")
        with col2:
            depto = st.text_input("Departamento *", placeholder="Ej. Francisco Morazán")
            municipio = st.text_input("Municipio / Ciudad *", placeholder="Ej. Tegucigalpa")
            direccion = st.text_area("Dirección Exacta de Entrega / Retiro *", placeholder="Col. Las Colinas...")
        guardar = st.form_submit_button("🚀 Registrar Cliente")
        
    if guardar:
        if not (nombre and rtn_dni and telefono and depto and municipio and direccion):
            st.error("❌ Por favor llena todos los campos.")
        else:
            fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with obtener_conexion() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO clientes (
                            codigo_casillero, nombre_completo, rtn_dni, 
                            telefono, departamento, municipio, 
                            direccion_entrega, fecha_registro
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (nuevo_codigo, nombre, rtn_dni, telefono, depto, municipio, direccion, fecha_actual))
                
                st.balloons()
                st.success(f"✅ ¡Cliente registrado con éxito! Código: **{nuevo_codigo}**")
                ficha_texto = f"""📋 FICHA OFICIAL DE CASILLERO — CENTRO DE CERÁMICAS Y MÁS
------------------------------------------------------------
Estimado(a) {nombre}, su casillero ha sido aperturado con éxito:

* CÓDIGO ASIGNADO: {nuevo_codigo}
* TELÉFONO: {telefono}

============================================================
📦 ETIQUETA / SHIPPING LABEL (Entregar a su proveedor en China)
============================================================
SHIP TO / DIRECCIÓN EN CHINA (CHILAT WAREHOUSE):
ATTN / RECEIVER : CHILAT / {nuevo_codigo}
CLIENT NAME     : {nombre}
COUNTRY         : HONDURAS
------------------------------------------------------------
Instrucciones obligatorias para su vendedor:
"Dear supplier, please ensure you paste our shipping label firmly on the exterior of each box before dispatching. Our warehouse will NOT accept packages without the Client Code: {nuevo_codigo} clearly visible."

中文说明:
"亲爱的卖家，发货前请务必在每个外箱上牢固张贴我们的唛头。外箱必须清晰标注客户代码：{nuevo_codigo}，否则仓库将拒收该包裹。"
============================================================
"""
                st.code(ficha_texto, language="text")
            except sqlite3.Error as e:
                st.error(f"❌ Error: {e}")

elif opcion == "📊 Dashboard & Lista de Clientes":
    st.subheader("📊 Panel de Control y Base de Datos")
    total_clientes = contar_clientes()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="👥 Total Clientes", value=total_clientes)
    with col2:
        st.metric(label="🇨🇳 Destino", value="China (Chilat)")
    with col3:
        st.metric(label="🇭🇳 País", value="Honduras")
        
    clientes = obtener_todos_los_clientes()
    if not clientes:
        st.warning("⚠️ No hay clientes registrados aún.")
    else:
        busqueda = st.text_input("🔍 Buscar cliente por nombre, código o teléfono:", "")
        clientes_filtrados = [
            c for c in clientes 
            if busqueda.lower() in str(c[1]).lower() or busqueda.lower() in str(c[2]).lower() or busqueda.lower() in str(c[4]).lower()
        ]
        datos_tabla = [{
            "ID": c[0], "Código": c[1], "Nombre": c[2], "DNI/RTN": c[3],
            "Teléfono": c[4], "Ubicación": f"{c[6]}, {c[5]}", "Dirección": c[7], "Fecha": c[8]
        } for c in clientes_filtrados]
        
        st.dataframe(datos_tabla, use_container_width=True)
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Código", "Nombre", "DNI", "Teléfono", "Departamento", "Municipio", "Dirección", "Fecha"])
        for c in clientes:
            writer.writerow(c)
            
        st.download_button(
            label="📥 Descargar Base de Datos a Excel (CSV)",
            data=output.getvalue(),
            file_name=f"clientes_casilleros.csv",
            mime="text/csv"
        )

elif opcion == "🏷️ Generador de Etiqueta":
    st.subheader("🏷️ Re-generar Etiqueta para Cliente Existente")
    clientes = obtener_todos_los_clientes()
    if not clientes:
        st.warning("No hay clientes registrados.")
    else:
        opciones = {f"{c[1]} - {c[2]}": c for c in clientes}
        seleccion = st.selectbox("Selecciona un cliente:", list(opciones.keys()))
        c = opciones[seleccion]
        st.markdown(f"""
        <div class="shipping-label">
            <h3>📦 ETIQUETA / SHIPPING LABEL</h3>
            <p><strong>ATTN / RECEIVER:</strong> CHILAT / {c[1]}</p>
            <p><strong>CLIENT NAME:</strong> {c[2]}</p>
            <p><strong>TEL:</strong> {c[4]}</p>
            <p><strong>COUNTRY:</strong> HONDURAS</p>
        </div>
        """, unsafe_allow_html=True)
