# ---------------- COTIZADOR CON REGLA MARÍTIMA 390 KG (860 LBS) = 1 CBM ----------------
    with tab_cotizador:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
        
        t_lb = get_tarifa("tarifa_libra")       # $3.50
        t_m3 = get_tarifa("tarifa_m3")           # $680.00
        min_usd = get_tarifa("minimo_cobro_usd") # $10.00

        tipo_carga = st.radio(
            "Tipo de Carga a Importar:",
            ["Paquetería Menor (Hasta 45 kg / 99 lbs)", "Carga Comercial por Metro Cúbico (100 lbs o más)"],
            horizontal=True
        )

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

        if tipo_carga == "Paquetería Menor (Hasta 45 kg / 99 lbs)":
            c1, c2 = st.columns(2)
            with c1:
                pe_lb = st.number_input("Peso del Paquete (Libras / lb)", min_value=0.5, max_value=99.9, value=4.0, step=0.5)
                pe_kg = pe_lb / 2.20462
                st.caption(f"Equivalente a: **{pe_kg:.2f} kg**")
            with c2:
                if pe_lb <= 3.0:
                    tot = min_usd
                    desc = f"Tarifa Mínima Base (1 a 3 lbs): **${min_usd:.2f} USD**"
                else:
                    tot = pe_lb * t_lb
                    desc = f"Tarifa por Libra: **{pe_lb:.1f} lbs x ${t_lb:.2f}/lb**"
                st.metric("Total Estimado (USD)", f"${tot:.2f} USD", help="Flete marítimo e internación aduanal incluida.")

            st.info(f"📌 **Detalle:** {desc} (Aplica para paquetes menores a 100 lbs / 45 kg).")

        else:
            st.caption("Regla de Carga Comercial: 1 CBM ($680.00) cubre hasta **390 kg (860 lbs)**. Si excede 390 kg, el peso adicional genera CBM tasables proporcionales:")
            c1, c2, c3, c4 = st.columns(4)
            with c1: al = st.number_input("Alto (cm)", min_value=1.0, value=120.0, step=1.0)
            with c2: an = st.number_input("Ancho (cm)", min_value=1.0, value=120.0, step=1.0)
            with c3: la = st.number_input("Largo (cm)", min_value=1.0, value=120.0, step=1.0)
            with c4: 
                pe_com_lb = st.number_input(
                    "Peso Total (Libras / lb)", 
                    min_value=100.0, 
                    value=860.0, 
                    step=10.0,
                    help="Relación marítima: 1 CBM equivale a máximo 860 lbs (390 kg)."
                )
                pe_com_kg = pe_com_lb / 2.20462
                st.caption(f"Equivalente a: **{pe_com_kg:.2f} kg**")

            # 1. Volumen físico
            vol_fisico_m3 = (al * an * la) / 1_000_000.0
            vol_ft3 = vol_fisico_m3 * 35.3147

            # 2. Volumen tasable por peso (1 CBM por cada 390 kg / 860 lbs)
            vol_tasable_peso = pe_com_kg / 390.0  # Equivalente a pe_com_lb / 860.0

            # 3. El CBM final facturable es el mayor entre las dimensiones físicas y el peso
            cbm_facturable = max(vol_fisico_m3, vol_tasable_peso)
            tot = cbm_facturable * t_m3

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric(
                    "CBM Facturable", 
                    f"{cbm_facturable:.4f} m³", 
                    help=f"Volumen físico: {vol_fisico_m3:.4f} m³ | Volumen por peso ({pe_com_kg:.1f} kg / 390 kg): {vol_tasable_peso:.4f} m³"
                )
            with m2:
                st.metric("Equivalencia en Pies Cúbicos", f"{vol_ft3:.2f} ft³")
            with m3:
                st.metric("Total Estimado (USD)", f"${tot:.2f} USD", help=f"{cbm_facturable:.4f} CBM x ${t_m3:.2f} USD")

            if vol_tasable_peso > vol_fisico_m3:
                st.warning(f"⚖️ **Recargo por peso aplicado:** La carga pesa **{pe_com_kg:.1f} kg ({pe_com_lb:.1f} lbs)**, superando el límite de 390 kg (860 lbs) por metro cúbico. Se tasa a **{cbm_facturable:.2f} CBM**.")
            else:
                st.success(f"📌 **Cálculo aplicado:** Tarifa por Dimensiones Físicas ({vol_fisico_m3:.4f} m³ @ ${t_m3:.2f}/CBM).")

        st.markdown('</div>', unsafe_allow_html=True)
