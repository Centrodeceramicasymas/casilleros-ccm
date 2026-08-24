from pathlib import Path

src = Path("/mnt/data/Pasted text.txt")
out = Path("/mnt/data/ccm_app_horizontal.py")

code = src.read_text(encoding="utf-8")

# 1) Reemplazar el CSS antiguo del menú horizontal
old_css_start = """    /* CONTENEDOR DE SCROLL HORIZONTAL ESTRICTO (AISLADO PARA MÓVIL Y PC) */"""
old_css_end = """    /* BANNER PUBLICITARIO */"""

start = code.find(old_css_start)
end = code.find(old_css_end)

if start == -1 or end == -1 or end <= start:
    raise RuntimeError("No se encontró el bloque CSS esperado.")

new_css = """    /* =========================================================
       MENÚ HORIZONTAL DESLIZABLE CON EL DEDO
       ========================================================= */

    .st-key-nav_scroll {
        width: 100% !important;
        max-width: 100% !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        -webkit-overflow-scrolling: touch !important;
        scrollbar-width: thin !important;
        margin-bottom: 8px !important;
        padding-bottom: 8px !important;
        touch-action: pan-x !important;
    }

    .st-key-nav_scroll::-webkit-scrollbar {
        height: 5px !important;
    }

    .st-key-nav_scroll::-webkit-scrollbar-track {
        background: transparent !important;
    }

    .st-key-nav_scroll::-webkit-scrollbar-thumb {
        background: rgba(148, 163, 184, 0.65) !important;
        border-radius: 20px !important;
    }

    /* Una sola fila, sin saltos */
    .st-key-nav_scroll [data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 8px !important;
        align-items: center !important;
        width: max-content !important;
        min-width: max-content !important;
    }

    /* Cada botón conserva su ancho */
    .st-key-nav_scroll [data-testid="stHorizontalBlock"] > div {
        flex: 0 0 125px !important;
        width: 125px !important;
        min-width: 125px !important;
        max-width: 125px !important;
    }

    .st-key-nav_scroll div.stButton > button {
        width: 125px !important;
        min-width: 125px !important;
        height: 44px !important;
        min-height: 44px !important;
        max-height: 44px !important;
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
    }

    .st-key-nav_scroll [data-testid="stHorizontalBlock"] > div > div {
        min-width: 0 !important;
    }

"""
code = code[:start] + new_css + code[end:]

# 2) Reemplazar el bloque de navegación actual
old_menu_start = """    # --- CONTENEDOR DE SCROLL HORIZONTAL ESTRICTO Y FORZADO ---"""
old_menu_end = """    st.markdown("""
search_start = code.find(old_menu_start)
if search_start == -1:
    raise RuntimeError("No se encontró el inicio del menú actual.")

# Buscar el siguiente st.markdown(""" correspondiente al buscador, usando el texto único
marker = """    st.markdown("""
search_end = code.find(marker, search_start + len(old_menu_start))
if search_end == -1:
    raise RuntimeError("No se encontró el final del menú actual.")

new_menu = """    # =========================================================
    # MENÚ HORIZONTAL DESLIZABLE
    # Se mantiene en una sola fila y se puede mover con el dedo
    # de izquierda a derecha o de derecha a izquierda.
    # =========================================================

    with st.container(key="nav_scroll"):

        c_nav_c, c_nav1, c_nav2, c_nav3, c_nav4 = st.columns(
            5,
            gap="small"
        )

        # MIS COTIZACIONES
        with c_nav_c:
            if st.button(
                "📄 Mis Cotiz.",
                type=(
                    "primary"
                    if st.session_state["ver_panel_cotizaciones"]
                    else "secondary"
                ),
                key="btn_toggle_cotizaciones"
            ):
                st.session_state["ver_panel_cotizaciones"] = not st.session_state[
                    "ver_panel_cotizaciones"
                ]
                st.rerun()

        # CATÁLOGO
        with c_nav1:
            if st.button(
                "🛍️ Catálogo",
                type=(
                    "primary"
                    if st.session_state["sub_tab_inicio"] == "Catálogo"
                    else "secondary"
                ),
                key="nav_top_cat"
            ):
                st.session_state["sub_tab_inicio"] = "Catálogo"
                st.session_state["ver_panel_cotizaciones"] = False
                st.rerun()

        # COTIZADOR
        with c_nav2:
            if st.button(
                "📐 Cotizador",
                type=(
                    "primary"
                    if st.session_state["sub_tab_inicio"] == "Cotizador"
                    else "secondary"
                ),
                key="nav_top_cot"
            ):
                st.session_state["sub_tab_inicio"] = "Cotizador"
                st.session_state["ver_panel_cotizaciones"] = False
                st.rerun()

        # ENVÍOS
        with c_nav3:
            if st.button(
                "📦 Envíos",
                type=(
                    "primary"
                    if st.session_state["sub_tab_inicio"] == "Mis Envíos"
                    else "secondary"
                ),
                key="nav_top_env"
            ):
                st.session_state["sub_tab_inicio"] = "Mis Envíos"
                st.session_state["ver_panel_cotizaciones"] = False
                st.rerun()

        # FICHAS
        with c_nav4:
            if st.button(
                "🏷️ Fichas",
                type=(
                    "primary"
                    if st.session_state["sub_tab_inicio"] == "Etiqueta"
                    else "secondary"
                ),
                key="nav_top_eti"
            ):
                st.session_state["sub_tab_inicio"] = "Etiqueta"
                st.session_state["ver_panel_cotizaciones"] = False
                st.rerun()

"""

code = code[:search_start] + new_menu + code[search_end:]

# Verificación básica
if "horizontal-scroll-wrapper" in code:
    raise RuntimeError("Todavía quedó CSS/referencia del contenedor anterior.")

if 'st.container(key="nav_scroll")' not in code:
    raise RuntimeError("No se insertó el nuevo contenedor.")

out.write_text(code, encoding="utf-8")

print(f"Archivo creado: {out}")
print(f"Tamaño: {out.stat().st_size:,} bytes")
print("Se conservaron todas las funciones del código original y se modificó únicamente el menú horizontal.")
