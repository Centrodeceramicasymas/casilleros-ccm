with tab_cotizador:
        st.markdown('<div class="card-box">', unsafe_allow_html=True)
        st.markdown("#### 📐 Cotizador Flete Marítimo China ➔ Honduras")
        
        c1, c2, c3, c4 = st.columns(4)
        with c1: al = st.number_input("Alto (cm)", min_value=1.0, value=30.0, step=1.0)
        with c2: an = st.number_input("Ancho (cm)", min_value=1.0, value=30.0, step=1.0)
        with c3: la = st.number_input("Largo (cm)", min_value=1.0, value=40.0, step=1.0)
        with c4: pe = st.number_input("Peso (lb)", min_value=0.5, value=4.0, step=0.5)

        # Cálculo de volumen en metros cúbicos y pies cúbicos
        vol_m3 = (al * an * la) / 1_000_000.0
        vol_ft3 = vol_m3 * 35.3147
        
        t_lb = get_tarifa("tarifa_libra")       # $3.50 - $4.00
        t_m3 = get_tarifa("tarifa_m3")           # $680.00
        min_usd = get_tarifa("minimo_cobro_usd") # $10.00

        # Regla estricta de facturación:
        # 1. De 1 a 3 lbs: Tarifa mínima de $10 USD
        # 2. De 4 a 99 lbs: Cobro normal por libra ($3.50 - $4.00/lb) sin importar el tamaño
        # 3. De 100 lbs en adelante: Cobro comercial por Metro Cúbico ($680/CBM)
        if pe <= 3.0:
            tot = min_usd
            modalidad = f"Tarifa Mínima Fija (1-3 lbs): ${min_usd:.2f} USD"
        elif pe <= 99.0:
            tot = pe * t_lb
            modalidad = f"Cobro por Libra Estándar ({pe:.1f} lbs @ ${t_lb:.2f}/lb)"
        else:
            # 100 lbs o más: Se liquida por CBM (o peso tasable si supera la relación estándar)
            costo_cbm = vol_m3 * t_m3
            costo_peso_cbm = (pe / 880.0) * t_m3
            tot = max(costo_cbm, costo_peso_cbm)
            modalidad = f"Tarifa Comercial Mayorista por CBM (${t_m3:.2f}/m³)"

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric(
                label="Volumen en Metros Cúbicos", 
                value=f"{vol_m3:.4f} m³", 
                help="Fórmula: (Alto x Ancho x Largo en cm) / 1,000,000"
            )
        with m2:
            st.metric(
                label="Equivalencia en Pies Cúbicos", 
                value=f"{vol_ft3:.2f} ft³"
            )
        with m3:
            st.metric(
                label="Total Estimado (USD)", 
                value=f"${tot:.2f} USD", 
                help="Incluye flete marítimo, desaduanaje e impuestos."
            )

        st.info(f"📌 **Cálculo aplicado:** {modalidad}")
        st.markdown('</div>', unsafe_allow_html=True)
