import streamlit as st
import sqlite3
import hashlib
import math
import random
import string
from datetime import datetime, timezone, timedelta
import io
import urllib.parse

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DEL SISTEMA & ZONA HORARIA HONDURAS (UTC-6)
# ---------------------------------------------------------
st.set_page_config(
    page_title="Centro de Cerámicas y Más — Casillero & Catálogo China",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

DB_NAME = "ccm_maritime_enterprise.db"
LOGO_FILENAME = "logo centro y mas.jpg"

ZONA_HONDURAS = timezone(timedelta(hours=-6))


def obtener_tiempo_honduras():
    return datetime.now(ZONA_HONDURAS)


def leer_config_moneda(clave, valor_default):
    try:
        seccion = st.secrets.get("moneda", {})
        if clave in seccion:
            return seccion[clave]
        return seccion.get(clave, valor_default)
    except Exception:
        return valor_default


OPCION_PREDETERMINADA = "🏬 Retirar en Almacén Principal (San Juan, Intibucá)"

MUNICIPIOS_HONDURAS = {
    "Atlántida": ["La Ceiba", "El Porvenir", "Esparta", "Jutiapa", "La Masica", "San Francisco", "Tela", "Arizona"],
    "Colón": ["Trujillo", "Balfate", "Iriona", "Limón", "Sabá", "Santa Fe", "Santa Rosa de Aguán", "Sonaguera", "Tocoa", "Bonito Oriental"],
    "Comayagua": ["Comayagua", "Ajuterique", "El Rosario", "Esquías", "Humuya", "La Libertad", "Lamaní", "La Trinidad", "Lejamaní", "Meámbar", "Minas de Oro", "Ojos de Agua", "San Jerónimo", "San José de Comayagua", "San José del Potrero", "San Luis", "San Sebastián", "Siguatepeque", "Villa de San Antonio", "Las Lajas", "Taulabé"],
    "Copán": ["Santa Rosa de Copán", "Cabañas", "Concepción", "Copán Ruinas", "Corquín", "Cucuyagua", "Dolores", "Dulce Nombre", "El Paraíso", "Florida", "La Jigua", "La Unión", "Nueva Arcadia (La Entrada)", "San Agustín", "San Antonio", "San Jerónimo", "San José", "San Juan de Opoa", "San Nicolás", "San Pedro", "Santa Rita", "Trinidad de Copán"],
    "Cortés": ["San Pedro Sula", "Choloma", "Omoa", "Pimienta", "Potrerillos", "Puerto Cortés", "San Antonio de Cortés", "San Francisco de Yojoa", "San Manuel", "Santa Cruz de Yojoa", "Villanueva", "La Lima"],
    "Choluteca": ["Choluteca", "Apacilagua", "Concepción de María", "Duyure", "El Corpus", "El Triunfo", "Marcovia", "Morolica", "Namasigüe", "Orocuina", "Pespire", "San Antonio de Flores", "San Isidro", "San José", "San Marcos de Colón", "Santa Ana de Yusguare"],
    "El Paraíso": ["Yuscarán", "Alauca", "Danlí", "El Paraíso", "Güinope", "Jacaleapa", "Liure", "Morocelí", "Oropolí", "Potrerillos", "San Antonio de Flores", "San Lucas", "San Matías", "Soledad", "Teupasenti", "Texiguat", "Vado Ancho", "Yauyupe", "Trojes"],
    "Francisco Morazán": ["Distrito Central (Tegucigalpa / Comayagüela)", "Alubarén", "Cedros", "Curarén", "El Porvenir", "Guaimaca", "La Libertad", "La Venta", "Lepaterique", "Maraita", "Marale", "Nueva Armenia", "Ojojona", "Orica", "Reitoca", "Sabanagrande", "San Antonio de Oriente", "San Buenaventura", "San Ignacio", "San Juan de Flores (Cantarranas)", "San Miguelito", "Santa Ana", "Santa Lucía", "Talanga", "Tatumbla", "Valle de Ángeles", "Villa de San Francisco", "Vallecillo"],
    "Gracias a Dios": ["Puerto Lempira", "Brus Laguna", "Ahuas", "Juan Francisco Bulnes", "Ramón Villeda Morales", "Wampusirpi"],
    "Intibucá": ["San Juan", "La Esperanza", "Intibucá", "Camasca", "Colomoncagua", "Concepción", "Dolores", "Magdalena", "Masaguara", "San Antonio", "San Isidro", "San Marcos de la Sierra", "San Miguelito", "Santa Lucía", "Yamaranguila", "San Francisco de Opalaca"],
    "Islas de la Bahía": ["Roatán", "Guanaja", "José Santos Guardiola", "Utila"],
    "La Paz": ["La Paz", "Aguanqueterique", "Cabañas", "Cane", "Chinacla", "Guajiquiro", "Lauterique", "Marcala", "Mercedes de Oriente", "Opatoro", "San Antonio del Norte", "San José", "San Juan", "San Pedro de Tutule", "Santa Ana", "Santa Elena", "Santa María", "Santiago de Puringla", "Yarula"],
    "Lempira": ["Gracias", "Belén", "Candelaria", "Cololaca", "Erandique", "Gualcince", "Guarita", "La Campa", "La Iguala", "La Unión", "La Virtud", "Lepaera", "Mapulaca", "Piraera", "San Andrés", "San Francisco", "San Juan Guarita", "San Manuel Colohete", "San Rafael", "San Sebastián", "Santa Cruz", "Talgua", "Tambla", "Tomalá", "Valladolid", "Virginia", "San Marcos de Caiquín"],
    "Ocotepeque": ["Ocotepeque", "Belén Gualcho", "Concepción", "Dolores Merendón", "Fraternidad", "La Encarnación", "La Labor", "Lucerna", "Mercedes", "San Fernando", "San Francisco del Valle", "San Jorge", "San Marcos", "Santa Fe", "Sinuapa", "Sensenti"],
    "Olancho": ["Juticalpa", "Campamento", "Catacamas", "Concordia", "Dulce Nombre de Culmí", "El Rosario", "Esquipulas del Norte", "Gualaco", "Guarizama", "Guata", "Guayape", "Jano", "La Unión", "Mangulile", "Manto", "Salamá", "San Esteban", "San Francisco de Becerra", "San Francisco de la Paz", "Santa María del Real", "Silca", "Yocón", "Patuca"],
    "Santa Bárbara": ["Santa Bárbara", "Arada", "Atima", "Azacualpa", "Ceguaca", "Concepción del Norte", "Concepción del Sur", "Chinda", "El Níspero", "Gualala", "Ilama", "Las Vegas", "Macuelizo", "Naranjito", "Nuevo Celilac", "Petoa", "Protección", "Quimistán", "San Francisco de Ojuera", "San José de Colinas", "San Luis", "San Marcos", "San Nicolás", "San Pedro Zacapa", "Santa Rita", "San Vicente Centenario", "Trinidad"],
    "Valle": ["Nacaome", "Alianza", "Amapala", "Aramecina", "Caridad", "Goascorán", "Langue", "San Francisco de Coray", "San Lorenzo"],
    "Yoro": ["Yoro", "Arenal", "El Negrito", "El Progreso", "Jocón", "Morazán", "Olanchito", "Santa Rita", "Sulaco", "Victoria", "Yorito"]
}

DIAS_SEMANA_ES = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
MESES_ES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}

if "vista_actual" not in st.session_state:
    st.session_state["vista_actual"] = "login"

if "sub_tab_inicio" not in st.session_state:
    st.session_state["sub_tab_inicio"] = "Inicio"

if "hub" not in st.session_state:
    st.session_state["hub"] = None

if "modalidad_envio_seleccionada" not in st.session_state:
    st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA

# Hubs de origen: agregar módulos aquí no cambia la navegación general.
HUBS = {
    "china": {
        "label": "China",
        "icon": "🇨🇳",
        "descripcion": "Consolidación marítima, catálogo y flete",
        "activo": True,
        "modulos": [
            {
                "id": "Mis Cotizaciones",
                "label": "Mis Cotizaciones",
                "nav": "📄 Mis Cotiz.",
                "icon": "📄",
                "detalle": "Historial y descarga de PDF",
                "btn_key": "mod_cotizaciones",
            },
            {
                "id": "Catálogo",
                "label": "Catálogo",
                "nav": "🛍️ Catálogo",
                "icon": "🛍️",
                "detalle": "Fábricas 1688 y costo en Honduras",
                "btn_key": "mod_catalogo",
            },
            {
                "id": "Cotizador",
                "label": "Cotizador",
                "nav": "📐 Cotizador",
                "icon": "📐",
                "detalle": "Flete marítimo por libra o CBM",
                "btn_key": "mod_cotizador",
            },
            {
                "id": "Mis Envíos",
                "label": "Envíos",
                "nav": "📦 Envíos",
                "icon": "📦",
                "detalle": "Paquetes en tránsito",
                "btn_key": "mod_envios",
            },
            {
                "id": "Etiqueta",
                "label": "Fichas",
                "nav": "🏷️ Fichas",
                "icon": "🏷️",
                "detalle": "Etiqueta bodega Guangzhou",
                "btn_key": "mod_fichas",
            },
        ],
    },
    "eeuu": {
        "label": "EE. UU.",
        "icon": "🇺🇸",
        "descripcion": "Módulo en preparación para envíos desde Estados Unidos",
        "activo": False,
        "modulos": [],
    },
    "honduras": {
        "label": "Honduras",
        "icon": "🇭🇳",
        "descripcion": "Módulo en preparación para operaciones locales",
        "activo": False,
        "modulos": [],
    },
}

MODULOS_POR_ID = {mod["id"]: hub_id for hub_id, hub in HUBS.items() for mod in hub["modulos"]}
VISTAS_MODULO = set(MODULOS_POR_ID.keys())


def ir_a(vista, hub="_omit"):
    if hub != "_omit":
        st.session_state["hub"] = hub
    elif vista in MODULOS_POR_ID:
        st.session_state["hub"] = MODULOS_POR_ID[vista]
    st.session_state["sub_tab_inicio"] = vista
    cas = str(st.session_state.get("casillero", "")).strip()
    if cas:
        st.query_params["casillero"] = cas
    st.query_params["vista"] = vista
    hub_actual = st.session_state.get("hub")
    if hub_actual:
        st.query_params["hub"] = hub_actual
    elif "hub" in st.query_params:
        del st.query_params["hub"]
    st.rerun()


# ---------------------------------------------------------
# 2. GENERADORES DE PDF NATIVOS CON HORA DE HONDURAS
# ---------------------------------------------------------
def compilar_pdf_simple(stream_content):
    stream_bytes = stream_content.encode("latin-1", "replace")
    stream_len = len(stream_bytes)

    pdf_buffer = io.BytesIO()
    pdf_buffer.write(b"%PDF-1.4\n")
    offsets = []

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1"))
    pdf_buffer.write(stream_bytes)
    pdf_buffer.write(b"\nendstream\nendobj\n")

    offsets.append(pdf_buffer.tell())
    pdf_buffer.write(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")

    xref_offset = pdf_buffer.tell()
    pdf_buffer.write(b"xref\n0 6\n0000000000 65535 f \n")
    for off in offsets:
        pdf_buffer.write(f"{off:010d} 00000 n \n".encode("latin-1"))

    pdf_buffer.write(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("latin-1"))
    return pdf_buffer.getvalue()


def generar_pdf_etiqueta_proveedor(
    casillero,
    nombre,
    telefono,
    ciudad,
    al=0.0,
    an=0.0,
    la=0.0,
    pe_lb=0.0,
    pe_kg=0.0,
    vol_m3=0.0,
    destino_entrega="Retirar en Almacén",
    fecha_emision=None,
):
    dim_txt = f"{al:.1f} x {an:.1f} x {la:.1f} CM" if (al > 0 or an > 0 or la > 0) else "POR DEFINIR EN ORIGEN"
    peso_txt = f"{pe_kg:.2f} KG ({pe_lb:.1f} LBS)" if pe_lb > 0 else "_______ KG"
    vol_txt = f"{vol_m3:.4f} CBM" if vol_m3 > 0 else "_______ CBM"
    destino_clean = str(destino_entrega).replace("📍", "").replace("📦", "").replace("🏬", "").strip().upper()
    fecha_txt = fecha_emision if fecha_emision else obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p")

    stream = f"""BT
/F1 16 Tf
40 790 Td
(CENTRO DE CERAMICAS Y MAS - HONDURAS) Tj
/F1 9 Tf
0 -16 Td
(EMITIDO EL: {fecha_txt}) Tj
/F1 10 Tf
0 -16 Td
(MARITIME CONSOLIDATION CARGO [CHINA -> HONDURAS]) Tj
0 -26 Td
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
(FINAL DESTINATION / ENTREGA: {destino_clean}, HONDURAS) Tj
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


def generar_pdf_confirmacion_cotizacion(
    casillero,
    nombre,
    telefono,
    ciudad,
    tipo_carga,
    al,
    an,
    la,
    peso_lb,
    peso_kg,
    vol_m3,
    vol_ft3,
    total_usd,
    detalle_tarifa,
    id_cot,
    destino_entrega="Retirar en Almacén",
    fecha_emision=None,
):
    fecha_txt = fecha_emision if fecha_emision else obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p")
    destino_clean = str(destino_entrega).replace("📍", "").replace("📦", "").replace("🏬", "").strip().upper()

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
(FECHA Y HORA DE EMISION : {fecha_txt}) Tj
0 -18 Td
(================================================================) Tj
/F1 11 Tf
0 -16 Td
(DATOS DEL CLIENTE Y DIRECCION DE ENTREGA SELECCIONADA:) Tj
/F1 9 Tf
0 -14 Td
(CASILLERO INTERNACIONAL : {casillero}) Tj
0 -12 Td
(TITULAR DE LA CUENTA    : {nombre}) Tj
0 -12 Td
(TELEFONO / WHATSAPP    : {telefono}) Tj
0 -12 Td
(MODALIDAD DE ENTREGA   : {destino_clean}) Tj
0 -12 Td
(DESTINO BASE REGISTRADO: {ciudad.upper()}, HONDURAS) Tj
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
# 3. BASE DE DATOS SQLITE & UTILIDADES
# ---------------------------------------------------------
def hash_pwd(password):
    return hashlib.sha256(password.encode()).hexdigest()


def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)


def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
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
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS direcciones_entrega (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT NOT NULL,
                etiqueta TEXT NOT NULL,
                receptor_nombre TEXT NOT NULL,
                telefono TEXT NOT NULL,
                departamento TEXT NOT NULL,
                ciudad TEXT NOT NULL,
                direccion_exacta TEXT NOT NULL,
                fecha_creacion TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS config_maritima (
                clave TEXT PRIMARY KEY,
                valor REAL NOT NULL
            )
            """
        )
        c.execute(
            """
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
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS paquetes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking TEXT UNIQUE NOT NULL,
                codigo_casillero TEXT NOT NULL,
                descripcion TEXT,
                contenedor_id TEXT,
                estado TEXT NOT NULL,
                fecha_actualizacion TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS catalogo_productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                categoria TEXT,
                proveedor TEXT,
                precio_fabrica_cny REAL,
                precio_fabrica_usd REAL,
                moq INTEGER DEFAULT 1,
                peso_kg REAL DEFAULT 0.5,
                volumen_m3 REAL DEFAULT 0.005,
                imagen_url TEXT,
                url_proveedor TEXT,
                fuente TEXT DEFAULT '1688',
                fecha_actualizacion TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS carrito_catalogo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_casillero TEXT NOT NULL,
                sku TEXT NOT NULL,
                nombre TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                precio_unitario_usd REAL NOT NULL,
                peso_unitario_kg REAL NOT NULL,
                volumen_unitario_m3 REAL NOT NULL,
                imagen_url TEXT,
                fecha TEXT NOT NULL
            )
            """
        )
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_libra', 3.50)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('tarifa_m3', 680.00)")
        c.execute("INSERT OR IGNORE INTO config_maritima (clave, valor) VALUES ('minimo_cobro_usd', 10.00)")

        admin_pass = hash_pwd("admin123")
        c.execute(
            """
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal,
                telefono_principal, departamento, ciudad, direccion_exacta,
                password_hash, rol, activo, fecha_creacion
            ) VALUES (
                '08011990', 'Super Administrador', '0801199000000', 'admin@ccm.hn',
                '+504 9999-0000', 'Intibucá', 'San Juan', 'Oficina Central CCM',
                ?, 'admin', 1, '2026-08-22 00:00:00'
            )
            """,
            (admin_pass,),
        )

        demo_pass = hash_pwd("cliente123")
        c.execute(
            """
            INSERT OR IGNORE INTO usuarios (
                codigo_casillero, nombre_completo, dni, correo_principal,
                telefono_principal, departamento, ciudad, direccion_exacta,
                rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion
            ) VALUES (
                '13011998', 'María Elena López', '1301199800990', 'cliente@ccm.hn',
                '+504 9577-1099', 'Intibucá', 'San Juan', 'Barrio El Centro, frente al parque',
                'Cerámica & Acabados', 'Retiro en Bodega Central (San Juan, Intibucá)',
                ?, 'cliente', 1, '2026-08-22 00:00:00'
            )
            """,
            (demo_pass,),
        )
        c.execute(
            """
            INSERT OR IGNORE INTO paquetes (tracking, codigo_casillero, descripcion, contenedor_id, estado, fecha_actualizacion)
            VALUES ('CN-GZ-88421', '13011998', 'Cajas de porcelanato 60x120', 'CCM-CNT-014', 'En Travesía Marítima', '2026-08-20 09:15:00')
            """
        )


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
    solo_digitos = "".join(filter(str.isdigit, str(dni_raw)))
    if len(solo_digitos) >= 8:
        return solo_digitos[:8]
    return solo_digitos.zfill(8)


def generar_clave_provisional():
    caracteres = string.ascii_letters + string.digits + "@#"
    return "".join(random.choice(caracteres) for _ in range(8))


# Medidas internas de un contenedor 40' High Cube y peso máximo IHTT (Honduras).
CONTENEDOR_40_ALTO_M = 2.69
CONTENEDOR_40_ANCHO_M = 2.35
CONTENEDOR_40_LARGO_M = 12.03
PESO_MAX_CONTENEDOR_HN_KG = 25_000.0
LB_POR_KG = 2.20462
PESO_MAX_PAQUETERIA_LB = 99.0


def peso_max_contenedor_hn_lb():
    return round(PESO_MAX_CONTENEDOR_HN_KG * LB_POR_KG, 2)


def max_alineado(min_v, max_v, step):
    n = math.floor((max_v - min_v) / step + 1e-9)
    return round(min_v + n * step, 4)


def limites_dimensiones(unidad_medida, comercial=False):
    if "Metros" in unidad_medida:
        factor = 1.0
        step = 0.01
        min_v = 0.01
        formato = "%.2f"
        defaults_paq = {"alto": 0.30, "ancho": 0.30, "largo": 0.40}
        defaults_com = {"alto": 1.20, "ancho": 1.20, "largo": 1.20}
        codigo = "m"
    elif "Pulgadas" in unidad_medida:
        factor = 1.0 / 0.0254
        step = 0.5
        min_v = 0.5
        formato = "%.1f"
        defaults_paq = {"alto": 12.0, "ancho": 12.0, "largo": 16.0}
        defaults_com = {"alto": 47.0, "ancho": 47.0, "largo": 47.0}
        codigo = "in"
    else:
        factor = 100.0
        step = 1.0
        min_v = 1.0
        formato = "%.0f"
        defaults_paq = {"alto": 30.0, "ancho": 30.0, "largo": 40.0}
        defaults_com = {"alto": 120.0, "ancho": 120.0, "largo": 120.0}
        codigo = "cm"

    maxes = {
        "alto": max_alineado(min_v, CONTENEDOR_40_ALTO_M * factor, step),
        "ancho": max_alineado(min_v, CONTENEDOR_40_ANCHO_M * factor, step),
        "largo": max_alineado(min_v, CONTENEDOR_40_LARGO_M * factor, step),
    }
    base = defaults_com if comercial else defaults_paq
    defaults = {k: min(v, maxes[k]) for k, v in base.items()}
    return {
        "min": min_v,
        "step": step,
        "formato": formato,
        "codigo": codigo,
        "defaults": defaults,
        "max": maxes,
    }


def limites_peso(unidad_peso, paqueteria):
    if paqueteria:
        max_lb = PESO_MAX_PAQUETERIA_LB
        default_lb = 4.0
        min_lb = 0.5
        step_lb = 0.5
    else:
        max_lb = peso_max_contenedor_hn_lb()
        default_lb = 500.0
        min_lb = 1.0
        step_lb = 10.0

    if "Kilogramos" in unidad_peso:
        min_v = 0.1 if paqueteria else 1.0
        default = round(default_lb / LB_POR_KG, 1)
        max_v = round(max_lb / LB_POR_KG, 1)
        step = 0.1 if paqueteria else 1.0
        codigo = "kg"
        formato = "%.1f"
    else:
        min_v = min_lb
        default = default_lb
        max_v = max_lb
        step = step_lb
        codigo = "lb"
        formato = "%.1f"

    max_v = max_alineado(min_v, max_v, step)
    default = min(default, max_v)
    return {
        "min": min_v,
        "default": default,
        "max": max_v,
        "step": step,
        "codigo": codigo,
        "formato": formato,
    }


def campo_numerico(label, lim_min, valor, lim_max, paso, clave, formato):
    if clave in st.session_state:
        try:
            actual = float(st.session_state[clave])
            st.session_state[clave] = min(max(actual, float(lim_min)), float(lim_max))
        except (TypeError, ValueError):
            st.session_state[clave] = float(valor)
    return st.number_input(
        label,
        min_value=float(lim_min),
        max_value=float(lim_max),
        value=float(valor),
        step=float(paso),
        format=formato,
        key=clave,
    )


# ---------------------------------------------------------
# 4. MOTOR DE CATÁLOGO 1688 ESTÁNDAR (BÚSQUEDA DINÁMICA)
# ---------------------------------------------------------
def calcular_costo_puesto_honduras(precio_fabrica_usd, peso_kg, vol_m3, cantidad=1):
    t_lb = get_tarifa("tarifa_libra")
    t_m3 = get_tarifa("tarifa_m3")
    min_usd = get_tarifa("minimo_cobro_usd")

    tasa_hnl = float(leer_config_moneda("TASA_USD_HNL", 24.85))
    comision_pct = float(leer_config_moneda("COMISION_CCM_PORCENTAJE", 0.10))

    fob_total_usd = precio_fabrica_usd * cantidad
    peso_total_kg = peso_kg * cantidad
    peso_total_lb = peso_total_kg * 2.20462
    vol_total_m3 = vol_m3 * cantidad

    if peso_total_lb <= 3.0:
        flete_usd = min_usd
    elif peso_total_lb <= 99.0:
        flete_usd = peso_total_lb * t_lb
    else:
        vol_peso = peso_total_kg / 390.0
        cbm_facturable = max(vol_total_m3, vol_peso)
        flete_usd = cbm_facturable * t_m3

    comision_usd = fob_total_usd * comision_pct
    total_cif_usd = fob_total_usd + flete_usd + comision_usd
    total_cif_hnl = total_cif_usd * tasa_hnl

    return {
        "fob_total_usd": fob_total_usd,
        "peso_total_lb": peso_total_lb,
        "flete_maritimo_usd": flete_usd,
        "comision_usd": comision_usd,
        "total_estimado_usd": total_cif_usd,
        "total_estimado_hnl": total_cif_hnl,
    }


def buscar_productos_1688_texto(keyword):
    kw_clean = keyword.strip().title()
    seed_id = abs(hash(keyword)) % 1000

    if any(k in keyword.lower() for k in ["martillo", "herramienta", "taladro", "llave"]):
        img_dinamica = f"https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=400&q=80&seed={seed_id}"
    elif any(k in keyword.lower() for k in ["porcelanato", "ceramica", "piso", "baño"]):
        img_dinamica = f"https://images.unsplash.com/photo-1584622650111-993a426fbf0a?auto=format&fit=crop&w=400&q=80&seed={seed_id}"
    else:
        img_dinamica = f"https://picsum.photos/400/300?random={seed_id}"

    return [
        {
            "sku": f"1688-DIR-{seed_id}",
            "nombre": f"{kw_clean} Calidad de Exportación",
            "precio_fabrica_cny": 58.00,
            "precio_fabrica_usd": 8.12,
            "moq": 10,
            "proveedor": "Foshan Industrial Export Co.",
            "peso_kg": 3.20,
            "volumen_m3": 0.009,
            "imagen_url": img_dinamica,
            "url_proveedor": "https://detail.1688.com",
            "fuente": "1688.com",
        },
        {
            "sku": f"1688-DIR-{seed_id + 1}",
            "nombre": f"{kw_clean} Industrial Reforzado",
            "precio_fabrica_cny": 135.00,
            "precio_fabrica_usd": 18.90,
            "moq": 5,
            "proveedor": "Guangzhou Hardware & Logistics Group",
            "peso_kg": 6.50,
            "volumen_m3": 0.018,
            "imagen_url": img_dinamica,
            "url_proveedor": "https://detail.1688.com",
            "fuente": "1688.com",
        },
    ]


def buscar_productos_1688_imagen(image_bytes):
    _ = image_bytes
    return [
        {
            "sku": "1688-VISUAL-001",
            "nombre": "Producto Detectado por Coincidencia Visual 1688",
            "precio_fabrica_cny": 88.00,
            "precio_fabrica_usd": 12.32,
            "moq": 10,
            "proveedor": "Zhejiang Export Manufacturing Ltd.",
            "peso_kg": 4.20,
            "volumen_m3": 0.012,
            "imagen_url": "https://images.unsplash.com/photo-1513519245088-0e12902e5a38?auto=format&fit=crop&w=400&q=80",
            "url_proveedor": "https://detail.1688.com",
            "fuente": "1688 Image Match",
        }
    ]


# ---------------------------------------------------------
# 5. GESTIÓN DE SESIÓN PERSISTENTE MEDIANTE QUERY_PARAMS
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
    st.session_state.update(
        {
            "autenticado": False,
            "usuario": None,
            "rol": None,
            "casillero": None,
            "nombre": None,
            "telefono": None,
            "ciudad": None,
            "reg_paso": 1,
            "reg_datos": {},
        }
    )


def restaurar_sesion_persistente():
    if st.session_state.get("autenticado", False):
        return True

    try:
        params = st.query_params
        cas_param = params.get("casillero", "")
        if isinstance(cas_param, list):
            cas_param = cas_param[0] if cas_param else ""
        cas_param = str(cas_param).strip()

        if not cas_param:
            return False

        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, codigo_casillero, nombre_completo, correo_principal,
                       rol, activo, telefono_principal, ciudad
                FROM usuarios
                WHERE codigo_casillero = ? AND activo = 1
                """,
                (cas_param,),
            )
            user_rec = c.fetchone()

        if not user_rec:
            return False

        st.session_state["autenticado"] = True
        st.session_state["casillero"] = user_rec[1]
        st.session_state["nombre"] = user_rec[2]
        st.session_state["usuario"] = user_rec[3]
        st.session_state["rol"] = user_rec[4]
        st.session_state["telefono"] = user_rec[6]
        st.session_state["ciudad"] = user_rec[7]

        vista_url = params.get("vista", "")
        if isinstance(vista_url, list):
            vista_url = vista_url[0] if vista_url else ""
        hub_url = params.get("hub", "")
        if isinstance(hub_url, list):
            hub_url = hub_url[0] if hub_url else ""

        vistas_validas = {"Inicio", "China", "EE. UU.", "Honduras"} | VISTAS_MODULO
        if vista_url in vistas_validas:
            st.session_state["sub_tab_inicio"] = vista_url
        if hub_url in HUBS:
            st.session_state["hub"] = hub_url
        elif vista_url in MODULOS_POR_ID:
            st.session_state["hub"] = MODULOS_POR_ID[vista_url]
        elif vista_url == "China":
            st.session_state["hub"] = "china"
        elif vista_url == "EE. UU.":
            st.session_state["hub"] = "eeuu"
        elif vista_url == "Honduras":
            st.session_state["hub"] = "honduras"

        return True

    except Exception:
        return False


restaurar_sesion_persistente()

if st.session_state.get("autenticado", False):
    cas_actual = str(st.session_state.get("casillero", "")).strip()
    if cas_actual:
        try:
            if st.query_params.get("casillero") != cas_actual:
                st.query_params["casillero"] = cas_actual
        except Exception:
            pass


def logout():
    for k in [
        "autenticado",
        "usuario",
        "rol",
        "casillero",
        "nombre",
        "telefono",
        "ciudad",
        "datos_pdf_confirmado",
        "ultima_cot_id",
        "modalidad_envio_seleccionada",
        "sub_tab_inicio",
        "hub",
    ]:
        st.session_state.pop(k, None)
    st.session_state["autenticado"] = False
    st.session_state["vista_actual"] = "login"
    st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------
# 6. ESTILOS CSS REFINADOS: ADAPTABLES A MÓVILES (IPHONE Y ANDROID)
# ---------------------------------------------------------
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@700&display=swap');

    :root {
        --app-max-width: 520px;
        --app-pad: 0.7rem;
        --nav-btn-w: 108px;
        --nav-btn-h: 40px;
        --header-blue-pad-y: 8px;
        --header-blue-pad-x: 12px;
        --greeting-title: 0.95rem;
        --greeting-sub: 0.68rem;
        --greeting-time: 0.62rem;
        --sticky-h: 150px;
    }

    html, body {
        overflow-x: hidden !important;
        max-width: 100% !important;
        background-color: #f8fafc !important;
        background: #f8fafc !important;
        color: #0f172a !important;
        color-scheme: light !important;
    }

    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stAppViewBlockContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stBottomBlockContainer"],
    [data-testid="stBottom"],
    section.main,
    .stMain,
    .stMainBlockContainer,
    .main,
    .block-container {
        background-color: #f8fafc !important;
        background: #f8fafc !important;
        color: #0f172a !important;
    }

    .stApp {
        font-family: 'Plus Jakarta Sans', sans-serif !important;
        overflow: visible !important;
        height: auto !important;
        min-height: 100% !important;
        color-scheme: light !important;
    }

    #MainMenu, footer, header, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
        min-height: 0 !important;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    section.main,
    .stMain,
    .stMainBlockContainer,
    [data-testid="stVerticalBlock"] {
        overflow: visible !important;
        height: auto !important;
        min-height: 0 !important;
        transform: none !important;
        filter: none !important;
        contain: none !important;
    }

    .block-container {
        max-width: var(--app-max-width) !important;
        width: 100% !important;
        padding-top: 0rem !important;
        padding-bottom: 5rem !important;
        padding-left: var(--app-pad) !important;
        padding-right: var(--app-pad) !important;
        margin: 0 auto !important;
        overflow: visible !important;
        transform: none !important;
    }

    .block-container:has(.st-key-sticky_top_header),
    .block-container:has([class*="st-key-sticky_top_header"]) {
        padding-top: var(--sticky-h) !important;
    }

    .st-key-sticky_top_header,
    div[class*="st-key-sticky_top_header"] {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        z-index: 2147483647 !important;
        background-color: #f8fafc !important;
        padding-top: max(0.35rem, env(safe-area-inset-top, 0px)) !important;
        padding-bottom: 0.35rem !important;
        margin: 0 !important;
        padding-left: max(var(--app-pad), calc((100vw - var(--app-max-width)) / 2)) !important;
        padding-right: max(var(--app-pad), calc((100vw - var(--app-max-width)) / 2)) !important;
        box-sizing: border-box !important;
        border-bottom: 1px solid rgba(226, 232, 240, 0.85) !important;
        box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08) !important;
    }

    .app-header-blue {
        background: linear-gradient(135deg, #004ac1 0%, #00368c 100%) !important;
        padding: var(--header-blue-pad-y) var(--header-blue-pad-x) !important;
        border-radius: 12px !important;
        color: #ffffff !important;
        box-shadow: 0 4px 14px rgba(0, 74, 193, 0.22) !important;
        max-width: 100% !important;
        width: 100% !important;
        box-sizing: border-box !important;
        margin-bottom: 4px !important;
        display: flex !important;
        flex-direction: column !important;
        gap: 1px !important;
    }

    .app-greeting-title {
        font-size: var(--greeting-title) !important;
        font-weight: 800 !important;
        margin: 0 !important;
        color: #ffffff !important;
        line-height: 1.2 !important;
        letter-spacing: -0.2px !important;
    }

    .app-greeting-sub {
        font-size: var(--greeting-sub) !important;
        color: #dbeafe !important;
        margin-top: 0 !important;
        font-weight: 500 !important;
        line-height: 1.25 !important;
    }

    .app-header-time {
        font-size: var(--greeting-time) !important;
        color: #bfdbfe !important;
        margin-top: 1px !important;
        font-weight: 600 !important;
        line-height: 1.2 !important;
    }

    /* iPhone / Android compacto */
    @media (max-width: 480px) {
        :root {
            --app-max-width: 100vw;
            --app-pad: 0.55rem;
            --nav-btn-w: 102px;
            --nav-btn-h: 38px;
            --header-blue-pad-y: 8px;
            --header-blue-pad-x: 12px;
            --greeting-title: 0.92rem;
            --greeting-sub: 0.66rem;
            --greeting-time: 0.60rem;
            --sticky-h: 142px;
        }
        .app-header-blue {
            border-radius: 11px !important;
            margin-bottom: 3px !important;
        }
        .inicio-placeholder { min-height: 260px; }
        .inicio-placeholder-body { min-height: 180px; }
        .card-box { padding: 0.9rem; border-radius: 12px; }
        .app-banner-card { padding: 12px; border-radius: 12px; margin-bottom: 0.85rem; }
        .swipe-indicator-bar { font-size: 0.68rem; margin: 1px 0 4px 0; }
    }

    /* Teléfonos grandes */
    @media (min-width: 481px) and (max-width: 767px) {
        :root {
            --app-max-width: 560px;
            --app-pad: 0.75rem;
            --nav-btn-w: 112px;
            --nav-btn-h: 42px;
            --header-blue-pad-y: 11px;
            --header-blue-pad-x: 14px;
            --greeting-title: 1.05rem;
            --greeting-sub: 0.74rem;
            --greeting-time: 0.68rem;
            --sticky-h: 156px;
        }
        .app-header-blue { border-radius: 14px !important; }
    }

    /* Tablet */
    @media (min-width: 768px) and (max-width: 1023px) {
        :root {
            --app-max-width: 820px;
            --app-pad: 1.1rem;
            --nav-btn-w: 122px;
            --nav-btn-h: 44px;
            --header-blue-pad-y: 14px;
            --header-blue-pad-x: 18px;
            --greeting-title: 1.22rem;
            --greeting-sub: 0.84rem;
            --greeting-time: 0.74rem;
            --sticky-h: 168px;
        }
        .app-header-blue { border-radius: 16px !important; margin-bottom: 8px !important; }
        .card-box { padding: 1.35rem; }
        .inicio-placeholder { min-height: 380px; }
    }

    /* Computadora */
    @media (min-width: 1024px) {
        :root {
            --app-max-width: 1080px;
            --app-pad: 1.4rem;
            --nav-btn-w: 128px;
            --nav-btn-h: 46px;
            --header-blue-pad-y: 16px;
            --header-blue-pad-x: 22px;
            --greeting-title: 1.35rem;
            --greeting-sub: 0.90rem;
            --greeting-time: 0.78rem;
            --sticky-h: 158px;
        }
        .app-header-blue { border-radius: 18px !important; margin-bottom: 10px !important; }
        .card-box { padding: 1.5rem; border-radius: 16px; }
        .inicio-placeholder { min-height: 420px; }
        .swipe-indicator-bar { display: none; }
    }

    .app-delivery-container {
        display: flex;
        align-items: center;
        gap: 8px;
        color: #0f172a;
        margin-top: 4px;
        margin-bottom: 4px;
    }
    .st-key-delivery_select {
        background: transparent !important;
        margin: 0 !important;
    }
    .st-key-delivery_select div[data-baseweb="select"] > div,
    .app-delivery-select div[data-baseweb="select"] > div {
        background-color: #ffffff !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 10px !important;
        color: #0f172a !important;
        padding: 0 4px !important;
        min-height: 38px !important;
    }
    .st-key-delivery_select div[data-baseweb="select"] span,
    .app-delivery-select div[data-baseweb="select"] span,
    div[data-baseweb="select"] span {
        color: #0f172a !important;
        font-weight: 700 !important;
        font-size: 0.82rem !important;
    }
    .st-key-delivery_select svg,
    .app-delivery-select svg,
    div[data-baseweb="select"] svg {
        fill: #0f172a !important;
    }
    ul[role="listbox"],
    li[role="option"],
    div[data-baseweb="popover"],
    div[data-baseweb="menu"] {
        background-color: #ffffff !important;
        color: #0f172a !important;
    }

    .st-key-nav_home {
        width: 100% !important;
        max-width: 100% !important;
        display: flex !important;
        justify-content: center !important;
        overflow: visible !important;
        margin-bottom: 6px !important;
    }

    .st-key-nav_scroll {
        width: 100% !important;
        max-width: 100% !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        -webkit-overflow-scrolling: touch !important;
        scrollbar-width: thin !important;
        margin-bottom: 2px !important;
        padding-bottom: 4px !important;
        touch-action: pan-x !important;
        background: transparent !important;
    }

    .st-key-nav_scroll::-webkit-scrollbar {
        height: 4px !important;
    }

    .st-key-nav_scroll::-webkit-scrollbar-track {
        background: transparent !important;
    }

    .st-key-nav_scroll::-webkit-scrollbar-thumb {
        background: rgba(148, 163, 184, 0.65) !important;
        border-radius: 20px !important;
    }

    .st-key-nav_scroll [data-testid="stHorizontalBlock"],
    .st-key-nav_home [data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 8px !important;
        align-items: center !important;
        width: max-content !important;
        min-width: max-content !important;
        padding-right: 4px !important;
    }

    .st-key-nav_home [data-testid="stHorizontalBlock"] {
        justify-content: center !important;
        width: 100% !important;
        min-width: 100% !important;
        margin: 0 auto !important;
        padding-right: 0 !important;
        gap: 12px !important;
    }

    .st-key-nav_scroll [data-testid="stHorizontalBlock"] > div,
    .st-key-nav_home [data-testid="stHorizontalBlock"] > div {
        flex: 0 0 var(--nav-btn-w) !important;
        width: var(--nav-btn-w) !important;
        min-width: var(--nav-btn-w) !important;
        max-width: var(--nav-btn-w) !important;
        box-sizing: border-box !important;
    }

    .st-key-nav_scroll div.stButton,
    .st-key-nav_home div.stButton {
        width: var(--nav-btn-w) !important;
        min-width: var(--nav-btn-w) !important;
        max-width: var(--nav-btn-w) !important;
        margin: 0 auto !important;
    }

    .st-key-nav_scroll div.stButton > button,
    .st-key-nav_home div.stButton > button {
        width: var(--nav-btn-w) !important;
        min-width: var(--nav-btn-w) !important;
        height: var(--nav-btn-h) !important;
        min-height: var(--nav-btn-h) !important;
        max-height: var(--nav-btn-h) !important;
        border-radius: 10px !important;
        padding: 0 8px !important;
        font-size: 0.76rem !important;
        font-weight: 700 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        touch-action: pan-x !important;
        margin: 0 !important;
        box-sizing: border-box !important;
    }

    .st-key-nav_scroll [data-testid="stHorizontalBlock"] > div > div {
        min-width: 0 !important;
    }

    @keyframes pulseBlink {
        0% { opacity: 0.25; transform: scale(0.95); }
        50% { opacity: 1; transform: scale(1.05); }
        100% { opacity: 0.25; transform: scale(0.95); }
    }

    .swipe-indicator-bar {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 6px;
        color: #3b82f6;
        font-size: 0.80rem;
        font-weight: 800;
        margin: 2px 0 6px 0;
        animation: pulseBlink 1.4s infinite ease-in-out;
        user-select: none;
    }

    .app-banner-card {
        background: linear-gradient(135deg, #eff6ff 0%, #f8fafc 100%);
        border: 1px solid #bfdbfe;
        border-radius: 16px;
        padding: 18px;
        color: #0f172a;
        margin-top: 0.5rem;
        margin-bottom: 1.2rem;
        box-shadow: 0 4px 12px rgba(0, 74, 193, 0.08);
    }
    .app-banner-title {
        font-size: 1.1rem;
        font-weight: 800;
        line-height: 1.3;
        margin-bottom: 6px;
        color: #0f172a;
    }
    .app-banner-accent {
        color: #004ac1;
    }
    .app-banner-sub {
        font-size: 0.78rem;
        color: #475569;
        font-weight: 500;
        line-height: 1.45;
    }
    .app-banner-tag {
        background: #ec4899;
        color: #ffffff;
        font-size: 0.72rem;
        font-weight: 800;
        padding: 3px 8px;
        border-radius: 6px;
        display: inline-block;
        margin-bottom: 8px;
    }

    .card-box {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 1.2rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }
    .inicio-placeholder {
        min-height: 430px;
        margin-top: 8px;
    }
    .inicio-placeholder-head {
        display: flex;
        align-items: center;
        gap: 10px;
        padding-bottom: 14px;
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 18px;
        font-size: 1.05rem;
        font-weight: 800;
        color: #0f172a;
    }
    .inicio-placeholder-body {
        min-height: 340px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        color: #94a3b8;
    }
    .inicio-placeholder-plus {
        font-size: 2.2rem;
        margin-bottom: 8px;
    }
    .inicio-placeholder-title {
        font-size: 0.95rem;
        font-weight: 700;
        color: #64748b;
    }
    .inicio-placeholder-sub {
        font-size: 0.78rem;
        margin-top: 5px;
    }
    .hub-menu-head {
        display: flex;
        align-items: center;
        gap: 10px;
        padding-bottom: 12px;
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 14px;
        font-size: 1.02rem;
        font-weight: 800;
        color: #0f172a;
    }
    .hub-menu-caption {
        font-size: 0.78rem;
        color: #64748b;
        font-weight: 600;
        margin: -6px 0 12px 0;
    }
    .hub-empty-box {
        background: #f8fafc;
        border: 1.5px dashed #cbd5e1;
        border-radius: 14px;
        padding: 28px 16px;
        text-align: center;
        color: #64748b;
        margin-top: 8px;
    }
    .china-address-box {
        background-color: #0f172a;
        border: 2px dashed #0052cc;
        border-radius: 12px;
        padding: 1.2rem;
        font-family: 'Space Mono', monospace;
        font-size: 0.82rem;
        color: #ffffff;
    }

    .stTextInput label, .stNumberInput label, .stSelectbox label, .stTextArea label, .stRadio label {
        color: #0f172a !important;
        font-weight: 700 !important;
        font-size: 0.84rem !important;
        margin-bottom: 4px !important;
        display: block !important;
    }

    div[data-baseweb="input"],
    div[data-baseweb="input"] > div,
    div[data-baseweb="base-input"],
    div[data-baseweb="select"] > div,
    div[data-baseweb="select"] > div > div,
    div[data-baseweb="textarea"],
    [data-testid="stNumberInputContainer"],
    [data-testid="stNumberInputContainer"] > div,
    [data-testid="stSelectbox"] > div,
    [data-testid="stTextInput"] > div,
    input, textarea, select {
        background-color: #ffffff !important;
        background: #ffffff !important;
        border-color: #cbd5e1 !important;
        color: #0f172a !important;
        color-scheme: light !important;
    }

    div[data-baseweb="input"], div[data-baseweb="select"] > div, div[data-baseweb="textarea"] {
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 10px !important;
        padding: 2px 6px !important;
    }

    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea,
    div[data-baseweb="input"] input:focus,
    div[data-baseweb="textarea"] textarea:focus,
    div[data-baseweb="textarea"] > div,
    [data-testid="stNumberInputContainer"] input,
    [data-testid="stNumberInput"] input,
    input[type="number"],
    input[type="text"],
    input[type="password"],
    textarea {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        font-size: 0.92rem !important;
        font-weight: 600 !important;
        background-color: #ffffff !important;
        background: #ffffff !important;
        color-scheme: light !important;
    }

    [data-testid="stNumberInputContainer"] button,
    [data-testid="stNumberInput"] button {
        background-color: #f1f5f9 !important;
        color: #0f172a !important;
        border-color: #cbd5e1 !important;
    }

    div[data-baseweb="input"] input::placeholder, div[data-baseweb="textarea"] textarea::placeholder, textarea::placeholder {
        color: #94a3b8 !important;
        -webkit-text-fill-color: #94a3b8 !important;
        font-weight: 400 !important;
    }

    [data-testid="stMetric"] {
        background: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 12px 10px !important;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04) !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.15rem !important;
        font-weight: 800 !important;
        color: #004ac1 !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.78rem !important;
        font-weight: 700 !important;
        color: #475569 !important;
    }
    [data-testid="stDataFrame"],
    [data-testid="stDataFrameResizable"],
    .stDataFrame,
    [data-testid="stMetricContainer"] {
        background-color: #ffffff !important;
    }

    div.stButton > button, div.stDownloadButton > button {
        width: 100% !important;
        height: 44px !important;
        min-height: 44px !important;
        max-height: 44px !important;
        border-radius: 10px !important;
        padding: 0 4px !important;
        font-size: 0.76rem !important;
        font-weight: 700 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        white-space: nowrap !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }

    div.stButton > button[kind="primary"], div.stDownloadButton > button {
        background: linear-gradient(135deg, #004ac1, #00368c) !important;
        color: #ffffff !important;
        border: 1px solid #004ac1 !important;
        box-shadow: 0 4px 12px rgba(0, 74, 193, 0.28) !important;
    }
    div.stButton > button[kind="primary"] * {
        color: #ffffff !important;
    }

    div.stButton > button[kind="secondary"] {
        background: #ffffff !important;
        color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04) !important;
    }
    div.stButton > button[kind="secondary"] * {
        color: #0f172a !important;
    }
    div.stButton > button[kind="secondary"]:hover {
        background-color: #f8fafc !important;
        border-color: #004ac1 !important;
        color: #004ac1 !important;
        box-shadow: 0 4px 10px rgba(0, 74, 193, 0.12) !important;
        transform: translateY(-1px);
    }
    div.stButton > button[kind="secondary"]:hover * {
        color: #004ac1 !important;
    }

    .st-key-btn_logout_cliente button,
    .st-key-btn_logout_cliente button[kind="secondary"] {
        width: var(--nav-btn-w) !important;
        height: var(--nav-btn-h) !important;
        min-height: var(--nav-btn-h) !important;
        max-height: var(--nav-btn-h) !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 10px !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.04) !important;
        opacity: 1 !important;
    }

    .st-key-btn_logout_cliente button:hover,
    .st-key-btn_logout_cliente button[kind="secondary"]:hover {
        background: #f8fafc !important;
        background-color: #f8fafc !important;
        color: #004ac1 !important;
        border-color: #004ac1 !important;
        box-shadow: 0 4px 10px rgba(0,74,193,0.12) !important;
    }

    .st-key-btn_logout_cliente button *,
    .st-key-btn_logout_cliente button[kind="secondary"] * {
        color: #0f172a !important;
        fill: #0f172a !important;
    }

    .st-key-btn_logout_cliente button:hover *,
    .st-key-btn_logout_cliente button[kind="secondary"]:hover * {
        color: #004ac1 !important;
        fill: #004ac1 !important;
    }

    .st-key-hub_china div.stButton > button,
    .st-key-hub_eeuu div.stButton > button,
    .st-key-hub_honduras div.stButton > button {
        height: 72px !important;
        min-height: 72px !important;
        max-height: 72px !important;
        font-size: 1.05rem !important;
        border-radius: 14px !important;
        justify-content: flex-start !important;
        padding: 0 16px !important;
        white-space: normal !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
    }

    .st-key-china_modulos button,
    .st-key-china_modulos [data-testid^="stBaseButton"],
    .st-key-china_modulos div.stButton > button,
    .st-key-mod_cotizaciones button,
    .st-key-mod_catalogo button,
    .st-key-mod_cotizador button,
    .st-key-mod_envios button,
    .st-key-mod_fichas button {
        height: 64px !important;
        min-height: 64px !important;
        max-height: 72px !important;
        font-size: 0.92rem !important;
        font-weight: 800 !important;
        border-radius: 14px !important;
        white-space: normal !important;
        line-height: 1.25 !important;
        padding: 10px 12px !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06) !important;
    }

    .st-key-china_modulos button *,
    .st-key-china_modulos [data-testid^="stBaseButton"] *,
    .st-key-mod_cotizaciones button *,
    .st-key-mod_catalogo button *,
    .st-key-mod_cotizador button *,
    .st-key-mod_envios button *,
    .st-key-mod_fichas button * {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        fill: #0f172a !important;
    }

    .st-key-china_modulos button:hover,
    .st-key-china_modulos [data-testid^="stBaseButton"]:hover {
        background: #f8fafc !important;
        border-color: #004ac1 !important;
        color: #004ac1 !important;
    }

    .mod-detalle {
        font-size: 0.72rem;
        font-weight: 600;
        color: #64748b;
        text-align: center;
        margin: 4px 0 12px 0;
        line-height: 1.3;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# 7. PANTALLA DE ACCESO PÚBLICA (LOGIN / REGISTRO / RECUPERACIÓN)
# ---------------------------------------------------------
if not st.session_state["autenticado"]:
    if st.session_state["vista_actual"] == "login":
        st.markdown(
            """
        <div class="app-header-blue" style="margin-bottom: 2rem; border-radius: 16px;">
            <h2 class="app-greeting-title">Centro de Cerámicas y Más</h2>
            <div class="app-greeting-sub">Consolidación Marítima China ➔ Honduras</div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.markdown("#### 🔐 Iniciar Sesión en su Casillero")
        u_ident = st.text_input(
            "Número de casillero o correo",
            placeholder="Ej: 13011998 o correo@gmail.com",
            key="log_cas",
        )
        u_pass = st.text_input("Contraseña", type="password", placeholder="Introduce tu contraseña", key="log_pwd")

        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        if st.button("➔ Ingresar a mi Casillero", type="primary", key="btn_login_submit"):
            if u_ident and u_pass:
                p_hash = hash_pwd(u_pass)
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        "SELECT id, codigo_casillero, nombre_completo, correo_principal, rol, activo, telefono_principal, ciudad FROM usuarios WHERE (correo_principal = ? OR codigo_casillero = ?) AND password_hash = ?",
                        (u_ident, u_ident, p_hash),
                    )
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
                        st.session_state["sub_tab_inicio"] = "Inicio"
                        st.session_state["hub"] = None

                        st.query_params["casillero"] = str(user[1])
                        st.query_params["vista"] = "Inicio"
                        st.rerun()
                else:
                    st.error("❌ Credenciales inválidas.")
            else:
                st.warning("Complete todos los campos.")

        c_b1, c_b2 = st.columns(2)
        with c_b1:
            if st.button("Recuperar Clave", type="secondary"):
                st.session_state["vista_actual"] = "recuperar"
                st.rerun()
        with c_b2:
            if st.button("Crear Casillero", type="secondary"):
                st.session_state["vista_actual"] = "registro"
                st.rerun()

    elif st.session_state["vista_actual"] == "registro":
        st.markdown("### 📋 Apertura de Casillero en China")
        paso = st.session_state["reg_paso"]
        st.progress(paso / 4.0, text=f"Paso {paso} de 4")

        if paso == 1:
            nom = st.text_input("Nombre Completo *", value=st.session_state["reg_datos"].get("nom", ""))
            dni = st.text_input(
                "Número de Identidad (DNI - 13 dígitos) *",
                value=st.session_state["reg_datos"].get("dni", ""),
                placeholder="Ej: 1301199800990",
            )
            if dni:
                st.caption(f"ℹ️ Su casillero asignado será: **{generar_codigo_casillero_dni(dni)}**")
            if st.button("Siguiente ➔", type="primary"):
                if nom and dni and len("".join(filter(str.isdigit, dni))) >= 8:
                    st.session_state["reg_datos"].update({"nom": nom, "dni": dni})
                    st.session_state["reg_paso"] = 2
                    st.rerun()
                else:
                    st.error("Ingrese un DNI válido.")

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
            dep_reg = st.selectbox(
                "Departamento *",
                list(MUNICIPIOS_HONDURAS.keys()),
                index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0,
                key="sb_dep_reg",
            )
            ciu_reg = st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_reg], key="sb_ciu_reg")
            dir_e = st.text_area("Dirección Exacta de Entrega *", value=st.session_state["reg_datos"].get("dir", ""))
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Atrás", type="secondary"):
                    st.session_state["reg_paso"] = 2
                    st.rerun()
            with c2:
                if st.button("Siguiente ➔", type="primary"):
                    if ciu_reg and dir_e:
                        st.session_state["reg_datos"].update({"dep": dep_reg, "ciu": ciu_reg, "dir": dir_e})
                        st.session_state["reg_paso"] = 4
                        st.rerun()
                    else:
                        st.error("Complete la dirección.")

        elif paso == 4:
            rub = st.selectbox(
                "Rubro Principal",
                ["Ferretería & Construcción", "Cerámica & Acabados", "Electrónica", "Ropa & Calzado", "Repuestos", "General"],
            )
            mod = st.radio(
                "Modalidad de Entrega",
                ["Retiro en Bodega Central (San Juan, Intibucá)", "Envío con Forza a Domicilio"],
            )
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
                    f_crea = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")

                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT codigo_casillero FROM usuarios WHERE correo_principal = ? OR dni = ? OR codigo_casillero = ?",
                            (d["cor"], d["dni"], n_cod),
                        )
                        if cur.fetchone():
                            url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(
                                "Hola, necesito asistencia con mi casillero ya registrado."
                            )
                            st.warning("⚠️ Ya existe un casillero registrado con este DNI o correo.")
                            st.markdown(
                                f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; padding:10px; border-radius:8px; width:100%; font-weight:bold; cursor:pointer;">📲 Consultar por WhatsApp (+504 9577-1099)</button></a>',
                                unsafe_allow_html=True,
                            )
                        else:
                            cur.execute(
                                "INSERT INTO usuarios (codigo_casillero, nombre_completo, dni, correo_principal, telefono_principal, departamento, ciudad, direccion_exacta, rubro_carga, modalidad_entrega, password_hash, rol, activo, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cliente', 1, ?)",
                                (
                                    n_cod,
                                    d["nom"],
                                    d["dni"],
                                    d["cor"],
                                    d["tel"],
                                    d["dep"],
                                    d["ciu"],
                                    d["dir"],
                                    rub,
                                    mod,
                                    hash_pwd(n_pwd),
                                    f_crea,
                                ),
                            )
                            conn.commit()
                            st.success("🎉 ¡Casillero Creado!")
                            st.info(f"🔑 **Casillero:** `{n_cod}` | 🔒 **Contraseña:** `{n_pwd}`")
                            st.session_state["reg_paso"] = 1
                            st.session_state["reg_datos"] = {}

        if st.button("Volver al Login", type="secondary"):
            st.session_state["vista_actual"] = "login"
            st.rerun()

    elif st.session_state["vista_actual"] == "recuperar":
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

# ---------------------------------------------------------
# 8. PORTAL DEL CLIENTE
# ---------------------------------------------------------
elif st.session_state["rol"] == "cliente":
    casillero = st.session_state["casillero"]
    nombre_completo = st.session_state["nombre"]
    tel_cli = st.session_state.get("telefono", "+504 9577-1099")
    ciu_cli = st.session_state.get("ciudad", "San Juan, Intibucá")

    partes_nombre = nombre_completo.strip().split()
    if len(partes_nombre) >= 2:
        nombre_display = f"{partes_nombre[0]} {partes_nombre[1]}"
    elif len(partes_nombre) == 1:
        nombre_display = partes_nombre[0]
    else:
        nombre_display = "Cliente"

    ahora_hn = obtener_tiempo_honduras()
    hora_actual = ahora_hn.hour
    if 5 <= hora_actual < 12:
        saludo_horario = "Buenos días"
    elif 12 <= hora_actual < 19:
        saludo_horario = "Buenas tardes"
    else:
        saludo_horario = "Buenas noches"

    dia_nombre = DIAS_SEMANA_ES.get(ahora_hn.weekday(), "")
    mes_nombre = MESES_ES.get(ahora_hn.month, "")
    hora_formato = ahora_hn.strftime("%I:%M %p")
    fecha_hora_texto = f"{dia_nombre}, {ahora_hn.day} {mes_nombre} {ahora_hn.year} &bull; {hora_formato}"

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM cotizaciones WHERE codigo_casillero = ?", (casillero,))
        total_cotizaciones = c.fetchone()[0]

        c.execute(
            "SELECT id, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, total_usd, fecha FROM cotizaciones WHERE codigo_casillero = ? ORDER BY id DESC",
            (casillero,),
        )
        lista_mis_cotizaciones = c.fetchall()

        c.execute(
            "SELECT id, etiqueta, receptor_nombre, ciudad, direccion_exacta FROM direcciones_entrega WHERE codigo_casillero = ?",
            (casillero,),
        )
        direcciones_guardadas = c.fetchall()

    opciones_modalidad = [OPCION_PREDETERMINADA]
    for d in direcciones_guardadas:
        opciones_modalidad.append(f"📍 {d[1]} - {d[3]}")
    opciones_modalidad.append("➕ Crear Nueva Dirección de Envío")

    if st.session_state["modalidad_envio_seleccionada"] not in opciones_modalidad:
        st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA

    with st.container(key="sticky_top_header"):
        st.markdown(
            f"""
        <div class="app-header-blue">
            <h3 class="app-greeting-title">{saludo_horario}, {nombre_display}</h3>
            <div class="app-greeting-sub">Casillero: <b>{casillero}</b> &bull; {total_cotizaciones} Cotizaciones</div>
            <div class="app-header-time">🕒 {fecha_hora_texto}</div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        hub_activo = st.session_state.get("hub")
        china_mods = HUBS["china"]["modulos"]
        mostrar_subnav_china = hub_activo == "china" and st.session_state["sub_tab_inicio"] in VISTAS_MODULO

        with st.container(key="nav_scroll" if mostrar_subnav_china else "nav_home"):

            if mostrar_subnav_china:
                nav_cols = st.columns(2 + len(china_mods), gap="small")
            else:
                nav_cols = st.columns(2, gap="small")

            with nav_cols[0]:
                if st.button("⏻ Cerrar", type="secondary", key="btn_logout_cliente", help="Cerrar sesión"):
                    logout()

            with nav_cols[1]:
                en_inicio = st.session_state["sub_tab_inicio"] == "Inicio"
                if st.button(
                    "🏠 Inicio",
                    type="primary" if en_inicio else "secondary",
                    key="btn_inicio_cliente",
                ):
                    ir_a("Inicio", hub=None)

            if mostrar_subnav_china:
                for idx, modulo in enumerate(china_mods):
                    with nav_cols[2 + idx]:
                        activo = st.session_state["sub_tab_inicio"] == modulo["id"]
                        if st.button(
                            modulo["nav"],
                            type="primary" if activo else "secondary",
                            key=f"nav_{modulo['btn_key']}",
                        ):
                            ir_a(modulo["id"], hub="china")

        if mostrar_subnav_china:
            if st.session_state["sub_tab_inicio"] in ["Etiqueta", "Mis Envíos"]:
                st.markdown(
                    '<div class="swipe-indicator-bar"><span>◀◀◀</span><span>Desliza a la izquierda</span><span>👈</span></div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="swipe-indicator-bar"><span>👉</span><span>Desliza a la derecha</span><span>▶▶▶</span></div>',
                    unsafe_allow_html=True,
                )

        if st.session_state["sub_tab_inicio"] == "Cotizador":
            idx_mod = opciones_modalidad.index(st.session_state["modalidad_envio_seleccionada"])
            with st.container(key="delivery_select"):
                mod_elegida = st.selectbox(
                    "🏪 ¿Cómo deseas recibir tu compra?",
                    opciones_modalidad,
                    index=idx_mod,
                    label_visibility="visible",
                    key="sb_modalidad_header",
                )
            if mod_elegida != st.session_state["modalidad_envio_seleccionada"]:
                st.session_state["modalidad_envio_seleccionada"] = mod_elegida
                st.session_state.pop("datos_pdf_confirmado", None)
                st.rerun()

    if st.session_state["sub_tab_inicio"] == "Inicio":
        hub_sel = st.session_state.get("hub")

        if not hub_sel:
            st.markdown("#### 🏠 Inicio")
            st.caption("Seleccione el origen de su carga para ver los módulos disponibles.")
            for hub_id, hub in HUBS.items():
                if st.button(
                    f"{hub['icon']}  {hub['label']}",
                    type="secondary",
                    key=f"hub_{hub_id}",
                    use_container_width=True,
                ):
                    ir_a("Inicio", hub=hub_id)
        elif hub_sel == "china":
            hub_china = HUBS["china"]
            st.markdown(f"#### {hub_china['icon']} {hub_china['label']}")
            st.caption("Consolidación marítima China ➔ Honduras")
            mods = hub_china["modulos"]
            with st.container(key="china_modulos"):
                for fila in range(0, len(mods), 2):
                    cols_mod = st.columns(2, gap="small")
                    for offset, col in enumerate(cols_mod):
                        if fila + offset >= len(mods):
                            break
                        modulo = mods[fila + offset]
                        with col:
                            texto = f"{modulo['icon']}  {modulo['label']}"
                            if st.button(
                                texto,
                                type="secondary",
                                key=modulo["btn_key"],
                                use_container_width=True,
                            ):
                                ir_a(modulo["id"], hub="china")
                            st.markdown(
                                f'<div class="mod-detalle">{modulo["detalle"]}</div>',
                                unsafe_allow_html=True,
                            )
        elif hub_sel in HUBS:
            hub_vacio = HUBS[hub_sel]
            st.markdown(f"#### {hub_vacio['icon']} {hub_vacio['label']}")
            st.markdown(
                f'<div class="hub-empty-box">'
                f'<div style="font-size:2rem;margin-bottom:8px;">{hub_vacio["icon"]}</div>'
                f'<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">{hub_vacio["label"]}</div>'
                f'<div style="font-size:0.86rem;font-weight:600;">{hub_vacio["descripcion"]}</div>'
                f'<div style="font-size:0.78rem;margin-top:10px;color:#94a3b8;">Espacio reservado para integrar funciones en una fase posterior.</div>'
                f"</div>",
                unsafe_allow_html=True,
            )

    if st.session_state["sub_tab_inicio"] == "Mis Cotizaciones":
        st.markdown("#### 📄 Historial de Cotizaciones y Descarga de PDF")
        st.caption("Aquí puedes consultar y descargar el comprobante en PDF de cada cotización generada.")

        if lista_mis_cotizaciones:
            for cot in lista_mis_cotizaciones:
                id_cot_item, al_c, an_c, la_c, pe_lb_c, vol_m3_c, tot_c, fec_c = cot
                st.markdown(
                    f"""
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px 14px; margin-bottom:10px; font-size:0.85rem;">
                    <b>🔖 CCM-COT-{id_cot_item:05d}</b> &bull; Fecha: {fec_c}<br>
                    <small style="color:#475569;">📐 Medidas: {al_c:.1f}x{an_c:.1f}x{la_c:.1f} cm | Peso: {pe_lb_c:.1f} lbs | 💰 Total: <b>${tot_c:.2f} USD</b></small>
                </div>
                """,
                    unsafe_allow_html=True,
                )

                pdf_historial = generar_pdf_confirmacion_cotizacion(
                    casillero=casillero,
                    nombre=nombre_completo,
                    telefono=tel_cli,
                    ciudad=ciu_cli,
                    tipo_carga="Cotización Histórica",
                    al=al_c,
                    an=an_c,
                    la=la_c,
                    peso_lb=pe_lb_c,
                    peso_kg=pe_lb_c / 2.20462,
                    vol_m3=vol_m3_c,
                    vol_ft3=vol_m3_c * 35.3147,
                    total_usd=tot_c,
                    detalle_tarifa="Tarifa Calculada Sistema CCM",
                    id_cot=id_cot_item,
                    destino_entrega=st.session_state["modalidad_envio_seleccionada"],
                    fecha_emision=fec_c,
                )
                st.download_button(
                    f"📥 Descargar PDF CCM-COT-{id_cot_item:05d}",
                    pdf_historial,
                    f"Comprobante_Cotizacion_CCM_COT_{id_cot_item:05d}.pdf",
                    "application/pdf",
                    key=f"dl_cot_{id_cot_item}",
                )
                st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)
        else:
            st.info("Aún no has generado ninguna cotización.")

    if st.session_state["sub_tab_inicio"] == "Cotizador" and st.session_state["modalidad_envio_seleccionada"] == "➕ Crear Nueva Dirección de Envío":
        st.markdown("#### 📍 Administrar Direcciones de Envío")

        st.markdown(
            f"""
        <div style="background:#f1f5f9; border:1.5px solid #cbd5e1; border-radius:8px; padding:10px 12px; margin-bottom:8px; font-size:0.85rem;">
            <b>{OPCION_PREDETERMINADA}</b> <span style="background:#004ac1; color:white; font-size:0.7rem; padding:2px 8px; border-radius:12px; font-weight:bold; margin-left:6px;">⭐ Predeterminada (Fija)</span><br>
            <small style="color:#64748b;">📍 Bodega Central Centro de Cerámicas y Más &bull; San Juan, Intibucá (No se puede eliminar)</small>
        </div>
        """,
            unsafe_allow_html=True,
        )

        if direcciones_guardadas:
            st.markdown(
                "<p style='font-weight:700; font-size:0.88rem; margin:10px 0 6px 0;'>Tus direcciones personalizadas:</p>",
                unsafe_allow_html=True,
            )
            for dir_item in direcciones_guardadas:
                id_dir, etiq, rec, ciu_d, dir_e = dir_item
                col_info_d, col_btn_del = st.columns([3.8, 1])
                with col_info_d:
                    st.markdown(
                        f"""
                    <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:8px; padding:8px 12px; font-size:0.85rem;">
                        <b>🏷️ {etiq}</b> &bull; Recibe: {rec}<br>
                        <small style="color:#64748b;">📍 {ciu_d} &bull; {dir_e}</small>
                    </div>
                    """,
                        unsafe_allow_html=True,
                    )
                with col_btn_del:
                    if st.button("🗑️ Eliminar", key=f"del_dir_{id_dir}", type="secondary"):
                        with get_db() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "DELETE FROM direcciones_entrega WHERE id = ? AND codigo_casillero = ?",
                                (id_dir, casillero),
                            )
                            conn.commit()
                        st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
                        st.session_state.pop("datos_pdf_confirmado", None)
                        st.toast(f"🗑️ Dirección '{etiq}' eliminada.")
                        st.rerun()

        st.markdown("---")
        st.markdown("##### ➕ Agregar Nueva Dirección de Entrega")
        c_et1, c_et2 = st.columns(2)
        with c_et1:
            etiqueta_in = st.text_input("Etiqueta de la dirección *", placeholder="Ej: Mi Casa, Sucursal 2, Taller")
            receptor_in = st.text_input("Nombre de quien recibe *", value=nombre_completo)
        with c_et2:
            tel_dir_in = st.text_input("Teléfono de contacto *", value=tel_cli)
            dep_dir_in = st.selectbox(
                "Departamento *",
                list(MUNICIPIOS_HONDURAS.keys()),
                index=9 if "Intibucá" in MUNICIPIOS_HONDURAS else 0,
                key="sb_dep_nueva_dir",
            )

        ciu_dir_in = st.selectbox("Municipio / Ciudad *", MUNICIPIOS_HONDURAS[dep_dir_in], key="sb_ciu_nueva_dir")
        dir_exacta_in = st.text_area(
            "Dirección exacta y referencias *",
            placeholder="Barrio, calle, número de casa, puntos clave...",
        )

        c_sv1, c_sv2 = st.columns(2)
        with c_sv1:
            if st.button("💾 Guardar Dirección", type="primary", key="btn_guardar_nueva_dir"):
                if etiqueta_in and receptor_in and tel_dir_in and ciu_dir_in and dir_exacta_in:
                    f_ahora = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO direcciones_entrega (codigo_casillero, etiqueta, receptor_nombre, telefono, departamento, ciudad, direccion_exacta, fecha_creacion)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                casillero,
                                etiqueta_in,
                                receptor_in,
                                tel_dir_in,
                                dep_dir_in,
                                ciu_dir_in,
                                dir_exacta_in,
                                f_ahora,
                            ),
                        )
                        conn.commit()
                    st.success(f"✅ Dirección '{etiqueta_in}' guardada.")
                    st.session_state["modalidad_envio_seleccionada"] = f"📍 {etiqueta_in} - {ciu_dir_in}"
                    st.session_state.pop("datos_pdf_confirmado", None)
                    st.rerun()
                else:
                    st.error("Completa todos los campos obligatorios (*).")
        with c_sv2:
            if st.button("Cancelar", type="secondary", key="btn_cancelar_dir"):
                st.session_state["modalidad_envio_seleccionada"] = OPCION_PREDETERMINADA
                st.session_state.pop("datos_pdf_confirmado", None)
                st.rerun()

    if st.session_state.get("hub") == "china" and st.session_state["sub_tab_inicio"] in VISTAS_MODULO:
        st.markdown(
            (
                '<div class="app-banner-card">'
                '<div class="app-banner-tag">¡Y YA ESTÁ DISPONIBLE!</div>'
                '<div class="app-banner-title">En el momento que sientes que cargas con '
                '<span class="app-banner-accent">libras extra</span> que te pesan...</div>'
                '<div class="app-banner-sub">¡Te das cuenta que tienen solución con fletes marítimos desde China!<br>'
                f'<b style="color:#db2777;letter-spacing:1px;">VIVE LIGERO</b> &bull; Casillero asignado: <b>{casillero}</b>'
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )

    if st.session_state["sub_tab_inicio"] == "Catálogo":
        st.markdown("#### 🛍️ Búsqueda en Fábricas de China (1688 Direct)")

        modo_busq = st.radio("Modalidad de búsqueda:", ["🔎 Por Nombre / Palabras", "📷 Por Foto / Imagen"], horizontal=True)

        resultados_1688 = []
        if modo_busq == "🔎 Por Nombre / Palabras":
            kw = st.text_input("Producto a buscar:", placeholder="Ej: porcelanato 60x120, grifería, taladro...")
            if st.button("Buscar Productos en China ➔", type="primary") and kw:
                with st.spinner("Consultando catálogo de 1688..."):
                    resultados_1688 = buscar_productos_1688_texto(kw)
        else:
            img_up = st.file_uploader("Sube una foto del producto:", type=["jpg", "png", "jpeg", "webp"])
            if img_up and st.button("Escanear Coincidencia Visual ➔", type="primary"):
                with st.spinner("Buscando por reconocimiento visual..."):
                    resultados_1688 = buscar_productos_1688_imagen(img_up.getvalue())

        if resultados_1688:
            st.markdown("---")
            for prod in resultados_1688:
                calc = calcular_costo_puesto_honduras(
                    prod["precio_fabrica_usd"], prod["peso_kg"], prod["volumen_m3"], prod["moq"]
                )

                c_img, c_det = st.columns([1, 1.8])
                with c_img:
                    st.image(prod["imagen_url"], use_container_width=True)
                with c_det:
                    st.markdown(f"**{prod['nombre']}**")
                    st.caption(f"🏭 {prod['proveedor']} | SKU: `{prod['sku']}`")
                    st.markdown(
                        f"💰 **Fábrica:** ¥{prod['precio_fabrica_cny']:.2f} CNY (~${prod['precio_fabrica_usd']:.2f} USD) | **MOQ:** {prod['moq']} uds."
                    )
                    st.success(
                        f"🇭🇳 **Puesto en Honduras:** ${calc['total_estimado_usd']:.2f} USD (~L {calc['total_estimado_hnl']:.2f} HNL)\n\n*(Destino: {st.session_state['modalidad_envio_seleccionada']})*"
                    )

                    msg_cot = f"Hola Centro de Cerámicas y Más, me interesa importar este producto: {prod['nombre']} (SKU: {prod['sku']}) para mi casillero {casillero}. Cantidad: {prod['moq']} uds. Destino/Entrega: {st.session_state['modalidad_envio_seleccionada']}. Enlace: {prod['url_proveedor']}"
                    url_wa_p = "https://wa.me/50495771099?text=" + urllib.parse.quote(msg_cot)

                    c_b1, c_b2 = st.columns(2)
                    with c_b1:
                        st.markdown(
                            f'<a href="{prod["url_proveedor"]}" target="_blank"><button style="background:white; border:1.5px solid #cbd5e1; border-radius:8px; width:100%; height:44px; font-weight:bold; cursor:pointer;">🔗 Ver en 1688</button></a>',
                            unsafe_allow_html=True,
                        )
                    with c_b2:
                        st.markdown(
                            f'<a href="{url_wa_p}" target="_blank"><button style="background:#22c55e; color:white; border:none; border-radius:8px; width:100%; height:44px; font-weight:bold; cursor:pointer;">📲 Cotizar WhatsApp</button></a>',
                            unsafe_allow_html=True,
                        )
                st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)

    elif st.session_state["sub_tab_inicio"] == "Cotizador":
        st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")

        st.info(
            f"📍 **Dirección / Destino de Entrega Seleccionado:** `{st.session_state['modalidad_envio_seleccionada']}` *(Se imprimirá en todos los formatos)*"
        )

        t_lb = get_tarifa("tarifa_libra")
        t_m3 = get_tarifa("tarifa_m3")
        min_usd = get_tarifa("minimo_cobro_usd")

        tipo_carga = st.selectbox(
            "Modalidad de Importación:",
            [
                "📦 Paquetería Menor (1 a 99 lbs)",
                "🚢 Carga Comercial por CBM (hasta contenedor 40')",
            ],
            index=0,
            key="sb_tipo_carga_select",
        )

        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

        c_u1, c_u2 = st.columns(2)
        with c_u1:
            unidad_medida = st.selectbox(
                "Unidad de Medida:", ["Centímetros (cm)", "Pulgadas (in)", "Metros (m)"], key="sb_unidad_medida"
            )
        with c_u2:
            unidad_peso = st.selectbox("Unidad de Peso:", ["Libras (lb)", "Kilogramos (kg)"], key="sb_unidad_peso")

        es_paqueteria = "Paquetería Menor" in tipo_carga
        dim = limites_dimensiones(unidad_medida, comercial=not es_paqueteria)
        pes = limites_peso(unidad_peso, paqueteria=es_paqueteria)
        etiqueta_medida = unidad_medida.split()[1].strip("()")
        etiqueta_peso = unidad_peso.split()[1].strip("()")

        st.caption(
            f"Tope de medidas: contenedor 40' High Cube interno "
            f"({CONTENEDOR_40_ALTO_M:.2f} m alto × {CONTENEDOR_40_ANCHO_M:.2f} m ancho × {CONTENEDOR_40_LARGO_M:.2f} m largo). "
            f"Peso máximo legal en Honduras para un 40': {PESO_MAX_CONTENEDOR_HN_KG:,.0f} kg "
            f"({peso_max_contenedor_hn_lb():,.0f} lb)."
            + (" En paquetería menor el peso no puede superar 99 lb." if es_paqueteria else "")
        )

        st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)

        pref = "menor" if es_paqueteria else "com"
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            al_input = campo_numerico(
                f"Alto ({etiqueta_medida})",
                dim["min"],
                dim["defaults"]["alto"],
                dim["max"]["alto"],
                dim["step"],
                f"in_al_{pref}_{dim['codigo']}",
                dim["formato"],
            )
        with c2:
            an_input = campo_numerico(
                f"Ancho ({etiqueta_medida})",
                dim["min"],
                dim["defaults"]["ancho"],
                dim["max"]["ancho"],
                dim["step"],
                f"in_an_{pref}_{dim['codigo']}",
                dim["formato"],
            )
        with c3:
            la_input = campo_numerico(
                f"Largo ({etiqueta_medida})",
                dim["min"],
                dim["defaults"]["largo"],
                dim["max"]["largo"],
                dim["step"],
                f"in_la_{pref}_{dim['codigo']}",
                dim["formato"],
            )
        with c4:
            pe_input = campo_numerico(
                f"Peso ({etiqueta_peso})",
                pes["min"],
                pes["default"],
                pes["max"],
                pes["step"],
                f"in_pe_{pref}_{pes['codigo']}",
                pes["formato"],
            )

        if "Pulgadas" in unidad_medida:
            al_val = al_input * 2.54
            an_val = an_input * 2.54
            la_val = la_input * 2.54
        elif "Metros" in unidad_medida:
            al_val = al_input * 100.0
            an_val = an_input * 100.0
            la_val = la_input * 100.0
        else:
            al_val = al_input
            an_val = an_input
            la_val = la_input

        if "Kilogramos" in unidad_peso:
            pe_lb = pe_input * 2.20462
            pe_kg = pe_input
        else:
            pe_lb = pe_input
            pe_kg = pe_input / 2.20462

        vol_m3_val = (al_val * an_val * la_val) / 1_000_000.0
        vol_ft3_val = vol_m3_val * 35.3147

        if es_paqueteria:
            if pe_lb <= 3.0:
                tot = min_usd
                desc = f"Tarifa Mínima Base (1 a 3 lbs): ${min_usd:.2f} USD"
            else:
                tot = pe_lb * t_lb
                desc = f"Tarifa por Libra: {pe_lb:.1f} lbs x ${t_lb:.2f}/lb"

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Volumen (m³)", f"{vol_m3_val:.4f} m³")
            with m2:
                st.metric("Pies Cúbicos", f"{vol_ft3_val:.2f} ft³")
            with m3:
                st.metric("Total Estimado", f"${tot:.2f} USD")

            modalidad_pdf = "Paquetería Menor (1 a 99 lbs)"
            detalle_pdf = desc

        else:
            vol_m3_peso = pe_kg / 390.0
            cbm_facturable = max(vol_m3_val, vol_m3_peso)
            tot = cbm_facturable * t_m3

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Volumen Físico", f"{vol_m3_val:.4f} m³")
            with m2:
                st.metric("CBM Facturable", f"{cbm_facturable:.4f} CBM")
            with m3:
                st.metric("Total Estimado", f"${tot:.2f} USD")

            modalidad_pdf = "Carga Comercial por Metro Cúbico (CBM)"
            detalle_pdf = f"{cbm_facturable:.4f} CBM @ ${t_m3:.2f}/m3"

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        if st.button("🤝 Confirmar Tarifa & Emitir Documentos", type="primary"):
            f_hoy_sql = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
            f_hoy_doc = obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p")
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO cotizaciones (codigo_casillero, alto_cm, ancho_cm, largo_cm, peso_lb, volumen_m3, volumen_ft3, total_usd, fecha)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (casillero, al_val, an_val, la_val, pe_lb, vol_m3_val, vol_ft3_val, tot, f_hoy_sql),
                )
                id_generado = cur.lastrowid

            st.session_state["ultima_cot_id"] = id_generado
            st.session_state["datos_pdf_confirmado"] = {
                "tipo_carga": modalidad_pdf,
                "al": al_val,
                "an": an_val,
                "la": la_val,
                "peso_lb": pe_lb,
                "peso_kg": pe_kg,
                "vol_m3": vol_m3_val,
                "vol_ft3": vol_ft3_val,
                "total_usd": tot,
                "detalle_tarifa": detalle_pdf,
                "id_cot": id_generado,
                "destino_entrega": st.session_state["modalidad_envio_seleccionada"],
                "fecha_hora_doc": f_hoy_doc,
            }
            st.rerun()

        if "datos_pdf_confirmado" in st.session_state and isinstance(st.session_state["datos_pdf_confirmado"], dict):
            d_pdf = st.session_state["datos_pdf_confirmado"]

            mismo_destino = d_pdf.get("destino_entrega", "") == st.session_state["modalidad_envio_seleccionada"]
            mismo_alto = abs(d_pdf.get("al", 0.0) - al_val) < 0.01
            mismo_ancho = abs(d_pdf.get("an", 0.0) - an_val) < 0.01
            mismo_largo = abs(d_pdf.get("la", 0.0) - la_val) < 0.01
            mismo_peso = abs(d_pdf.get("peso_lb", 0.0) - pe_lb) < 0.01
            mismo_precio = abs(d_pdf.get("total_usd", 0.0) - tot) < 0.01

            if mismo_destino and mismo_alto and mismo_ancho and mismo_largo and mismo_peso and mismo_precio:
                id_c = d_pdf.get("id_cot", 1)
                dest_pdf = d_pdf.get("destino_entrega", st.session_state["modalidad_envio_seleccionada"])
                fecha_doc = d_pdf.get("fecha_hora_doc", obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p"))

                st.markdown(
                    f"""
                <div style="background: linear-gradient(135deg, #f0fdf4, #dcfce7); border-left: 5px solid #22c55e; border-radius: 12px; padding: 16px; margin: 15px 0; box-shadow: 0 4px 12px rgba(34, 197, 94, 0.15);">
                    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px;">
                        <span style="font-size: 1.4rem;">🎉</span>
                        <h4 style="color: #166534; margin: 0; font-size: 1.05rem; font-weight: 800;">Cotización CCM-COT-{id_c:05d} confirmada el {fecha_doc} para entrega en: {dest_pdf}</h4>
                    </div>
                </div>
                """,
                    unsafe_allow_html=True,
                )

                pdf_fab = generar_pdf_etiqueta_proveedor(
                    casillero=casillero,
                    nombre=nombre_completo,
                    telefono=tel_cli,
                    ciudad=ciu_cli,
                    al=d_pdf.get("al", 0),
                    an=d_pdf.get("an", 0),
                    la=d_pdf.get("la", 0),
                    pe_lb=d_pdf.get("peso_lb", 0),
                    pe_kg=d_pdf.get("peso_kg", 0),
                    vol_m3=d_pdf.get("vol_m3", 0),
                    destino_entrega=dest_pdf,
                    fecha_emision=fecha_doc,
                )

                pdf_conf = generar_pdf_confirmacion_cotizacion(
                    casillero=casillero,
                    nombre=nombre_completo,
                    telefono=tel_cli,
                    ciudad=ciu_cli,
                    tipo_carga=d_pdf.get("tipo_carga", ""),
                    al=d_pdf.get("al", 0),
                    an=d_pdf.get("an", 0),
                    la=d_pdf.get("la", 0),
                    peso_lb=d_pdf.get("peso_lb", 0),
                    peso_kg=d_pdf.get("peso_kg", 0),
                    vol_m3=d_pdf.get("vol_m3", 0),
                    vol_ft3=d_pdf.get("vol_ft3", 0),
                    total_usd=d_pdf.get("total_usd", 0),
                    detalle_tarifa=d_pdf.get("detalle_tarifa", ""),
                    id_cot=id_c,
                    destino_entrega=dest_pdf,
                    fecha_emision=fecha_doc,
                )

                c_doc1, c_doc2 = st.columns(2)
                with c_doc1:
                    st.download_button(
                        "📥 PDF Fabricante",
                        pdf_fab,
                        f"Shipping_Label_Fabricante_{casillero}.pdf",
                        "application/pdf",
                    )
                with c_doc2:
                    st.download_button(
                        "📥 PDF Tarifa",
                        pdf_conf,
                        f"Comprobante_Tarifa_{casillero}_COT{id_c:05d}.pdf",
                        "application/pdf",
                    )

                texto_wa = f"Hola Centro de Cerámicas y Más, confirmo cotización CCM-COT-{id_c:05d} generada el {fecha_doc} del casillero {casillero}. Destino de Entrega: {dest_pdf}. Total: ${d_pdf.get('total_usd', 0):.2f} USD."
                url_wa = "https://wa.me/50495771099?text=" + urllib.parse.quote(texto_wa)
                st.markdown(
                    f'<a href="{url_wa}" target="_blank"><button style="background:#22c55e; color:white; border:none; border-radius:12px; width:100%; height:48px; font-weight:bold; cursor:pointer; margin-top:8px; box-shadow: 0 4px 10px rgba(34, 197, 94, 0.25);">📲 Enviar a WhatsApp (+504 9577-1099)</button></a>',
                    unsafe_allow_html=True,
                )
            else:
                st.session_state.pop("datos_pdf_confirmado", None)
                st.rerun()

    elif st.session_state["sub_tab_inicio"] == "Mis Envíos":
        st.markdown("#### 📦 Mis Paquetes en Tránsito")
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT tracking, descripcion, contenedor_id, estado, fecha_actualizacion FROM paquetes WHERE codigo_casillero = ?",
                (casillero,),
            )
            paquetes = c.fetchall()

        if paquetes:
            for p in paquetes:
                st.markdown(
                    f"""
                <div style="background:#f1f5f9; border:1px solid #cbd5e1; border-radius:10px; padding:12px; margin-bottom:8px;">
                    <b>Tracking:</b> {p[0]} | <b>Contenedor:</b> {p[2]}<br>
                    <b>Estado:</b> <span style="color:#004ac1; font-weight:bold;">{p[3]}</span><br>
                    <small style="color:#64748b;">Actualizado: {p[4]}</small>
                </div>
                """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("No tienes paquetes registrados en travesía.")

    elif st.session_state["sub_tab_inicio"] == "Etiqueta":
        st.markdown("#### 🏷️ Ficha de Envío Bodega Guangzhou")
        st.caption(f"Dirección de Entrega vinculada: **{st.session_state['modalidad_envio_seleccionada']}**")

        f_etiqueta_actual = obtener_tiempo_honduras().strftime("%d/%m/%Y %I:%M:%S %p")
        pdf_bytes = generar_pdf_etiqueta_proveedor(
            casillero=casillero,
            nombre=nombre_completo,
            telefono=tel_cli,
            ciudad=ciu_cli,
            destino_entrega=st.session_state["modalidad_envio_seleccionada"],
            fecha_emision=f_etiqueta_actual,
        )
        st.download_button(
            "📄 Descargar Etiqueta para Proveedor (PDF)",
            pdf_bytes,
            f"Shipping_Label_{casillero}.pdf",
            "application/pdf",
        )

        destino_pantalla = (
            str(st.session_state["modalidad_envio_seleccionada"])
            .replace("📍", "")
            .replace("📦", "")
            .replace("🏬", "")
            .strip()
        )
        st.markdown(
            f"""
        <div class="china-address-box" style="margin-top:10px;">
<strong>CLIENT CODE / CASILLERO:</strong> {casillero}<br>
<strong>DESTINATION / ENTREGA:</strong> {destino_pantalla.upper()}, HONDURAS<br>
<strong>FECHA DE EMISIÓN:</strong> {f_etiqueta_actual}<br>
<strong>ATTN:</strong> CHILAT / {casillero}<br>
<strong>ADDRESS:</strong> CHILAT Logistics Warehouse, District B, Port Area, Guangzhou<br>
<strong>ADDRESS (中文):</strong> 广东省广州市白云区集运仓 / 转 {casillero}
        </div>
        """,
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------
# 9. PANEL ADMINISTRATIVO
# ---------------------------------------------------------
elif st.session_state["rol"] == "admin":
    st.markdown("## 🛠️ Panel Maestro — Administrador")
    tab_u, tab_p, tab_t = st.tabs(["👥 Directorio de Clientes", "📦 Registrar Paquetes", "⚙️ Tarifas"])

    with tab_u:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT codigo_casillero, nombre_completo, correo_principal, telefono_principal, ciudad FROM usuarios WHERE rol = 'cliente'"
            )
            filas = c.fetchall()
            if filas:
                st.dataframe(
                    {
                        "Casillero": [r[0] for r in filas],
                        "Nombre": [r[1] for r in filas],
                        "Correo": [r[2] for r in filas],
                        "Teléfono": [r[3] for r in filas],
                        "Ciudad": [r[4] for r in filas],
                    },
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("Aún no hay clientes registrados.")

    with tab_p:
        t_in = st.text_input("Tracking de China")
        c_in = st.text_input("Casillero Asignado (8 dígitos)")
        d_in = st.text_input("Descripción de la carga", placeholder="Ej: 4 cajas de porcelanato 60x120")
        cont_in = st.text_input("ID de contenedor", placeholder="Ej: CCM-CNT-014")
        e_in = st.selectbox(
            "Estado",
            [
                "En Bodega China",
                "En Travesía Marítima",
                "En Desaduanaje",
                "Disponible en Bodega Central",
                "Entregado",
            ],
        )
        if st.button("Actualizar Paquete", type="primary"):
            if t_in and c_in:
                f_act = obtener_tiempo_honduras().strftime("%Y-%m-%d %H:%M:%S")
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO paquetes (tracking, codigo_casillero, descripcion, contenedor_id, estado, fecha_actualizacion)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(tracking) DO UPDATE SET
                            codigo_casillero = excluded.codigo_casillero,
                            descripcion = excluded.descripcion,
                            contenedor_id = excluded.contenedor_id,
                            estado = excluded.estado,
                            fecha_actualizacion = excluded.fecha_actualizacion
                        """,
                        (t_in, c_in, d_in, cont_in, e_in, f_act),
                    )
                st.success("Paquete actualizado.")
                st.rerun()
            else:
                st.warning("Ingrese tracking y casillero.")

    with tab_t:
        st.markdown("#### Tarifas marítimas vigentes")
        n_lb = st.number_input("Tarifa por libra (USD)", min_value=0.01, value=float(get_tarifa("tarifa_libra")), step=0.05)
        n_m3 = st.number_input("Tarifa por m³ (USD)", min_value=0.01, value=float(get_tarifa("tarifa_m3")), step=1.0)
        n_min = st.number_input("Mínimo de cobro (USD)", min_value=0.01, value=float(get_tarifa("minimo_cobro_usd")), step=0.50)
        if st.button("Guardar tarifas", type="primary"):
            set_tarifa("tarifa_libra", n_lb)
            set_tarifa("tarifa_m3", n_m3)
            set_tarifa("minimo_cobro_usd", n_min)
            st.success("Tarifas actualizadas.")

    if st.button("Cerrar Sesión Admin", type="secondary"):
        logout()
