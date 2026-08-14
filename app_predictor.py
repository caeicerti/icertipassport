"""
iCerti - Predictor de Calificacion Energetica v5
- Solo RC visible de primeras, resto aparece tras consulta Catastro
- Combustibles bien escritos
- Sin instalaciones como opcion
- Letra predicha por modelo para cada recomendacion
Ejecutar: py -m streamlit run app_predictor.py
"""

import streamlit as st
import numpy as np
import pickle
import os
import requests
import xml.etree.ElementTree as ET

st.set_page_config(page_title="iCerti · Predictor Energético", page_icon="⚡", layout="centered")

st.markdown("""
<style>
    .stApp { background-color: #ffffff; }
    h1 { color: #1B5E20; } h2 { color: #1B5E20; } h3 { color: #2E7D32; }
    .stButton > button { background-color: #1B5E20; color: white; border-radius: 8px; border: none; font-weight: 600; }
    .stButton > button:hover { background-color: #2E7D32; }
    .rec-box { background: #F1F8E9; border-left: 4px solid #2E7D32; padding: 12px 16px;
               border-radius: 0 8px 8px 0; margin: 8px 0; display: flex;
               align-items: flex-start; justify-content: space-between; gap: 12px; }
    .catastro-box { background: #E8F5E9; border: 1.5px solid #2E7D32; border-radius: 10px; padding: 14px 18px; margin: 8px 0; }
    .info-box { background: #E3F2FD; border-left: 4px solid #1565C0; padding: 12px 16px; border-radius: 0 8px 8px 0; margin: 8px 0; }
</style>
""", unsafe_allow_html=True)

PROVINCIA_ZONA = {
    "ALAVA":"D1","ALBACETE":"D3","ALICANTE":"B4","ALMERIA":"A4","ASTURIAS":"C1",
    "AVILA":"E1","BADAJOZ":"C4","BARCELONA":"C2","BURGOS":"E1","CACERES":"C4",
    "CADIZ":"A3","CANTABRIA":"C1","CASTELLON":"B3","CIUDAD REAL":"D3","CORDOBA":"B4",
    "CUENCA":"D3","GIRONA":"C2","GRANADA":"C3","GUADALAJARA":"D3","GUIPUZCOA":"C1",
    "HUELVA":"A4","HUESCA":"D2","JAEN":"C4","LA RIOJA":"D2","LAS PALMAS":"A3",
    "LEON":"E1","LLEIDA":"D3","LUGO":"C1","MADRID":"D3","MALAGA":"A3","MURCIA":"B3",
    "NAVARRA":"D1","OURENSE":"C2","PALENCIA":"D1","PONTEVEDRA":"C1","SALAMANCA":"D2",
    "SANTA CRUZ DE TENERIFE":"A2","SEGOVIA":"D2","SEVILLA":"B4","SORIA":"E1",
    "TARRAGONA":"B3","TERUEL":"D2","TOLEDO":"C4","VALENCIA":"B3","VALLADOLID":"D2",
    "VIZCAYA":"C1","ZAMORA":"D2","ZARAGOZA":"D3","CEUTA":"A3","MELILLA":"A3",
    "BALEARES":"B3","ILLES BALEARS":"B3","A CORUÑA":"C1","CORUÑA":"C1",
}

def get_zona(provincia):
    if not provincia: return None
    p = provincia.upper().strip()
    for k, v in PROVINCIA_ZONA.items():
        if k in p or p in k: return v
    return None

def consultar_catastro(rc):
    rc = rc.strip().replace(" ","")
    if len(rc) < 14: return None, "La referencia catastral debe tener al menos 14 caracteres."
    try:
        url = f"https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC?Provincia=&Municipio=&RC={rc}"
        r = requests.get(url, timeout=8)
        if r.status_code != 200: return None, f"Error HTTP {r.status_code}."
        root = ET.fromstring(r.content)
        ns = 'http://www.catastro.meh.es/'
        def find(tag):
            el = root.find(f'.//{{{ns}}}{tag}')
            return el.text.strip() if el is not None and el.text else None
        datos = {}
        if find('np'): datos['provincia'] = find('np')
        if find('nm'): datos['municipio'] = find('nm')
        tv, nv, pnp = find('tv'), find('nv'), find('pnp')
        if tv and nv: datos['direccion'] = ' '.join(filter(None,[tv,nv,f"Nº {pnp}" if pnp else None]))
        ant = find('ant')
        if ant:
            try: datos['año_construccion'] = int(ant)
            except: pass
        sfc = find('sfc')
        if sfc:
            try: datos['superficie'] = float(sfc)
            except: pass
        if not datos.get('provincia'): return None, "No se encontraron datos para esta referencia catastral."
        return datos, None
    except requests.exceptions.Timeout:
        return None, "Tiempo de espera agotado."
    except Exception as e:
        return None, f"Error: {str(e)}"

def transmitancias_año(año):
    if año < 1979:   return 1.80, 5.70
    elif año < 2007: return 1.20, 3.50
    elif año < 2013: return 0.73, 3.30
    elif año < 2020: return 0.56, 2.50
    else:            return 0.35, 1.80

def normativa_año(año):
    if año < 1979:   return 'Anterior a NBE'
    elif año < 2007: return 'NBE-CT-79'
    elif año < 2013: return 'CTE-2006'
    elif año < 2020: return 'CTE-2013'
    else:            return 'CTE-2019'

VENTANAS_U = {
    "Aluminio estándar sin climalit": 5.70,
    "Madera sin climalit":            3.00,
    "Aluminio con climalit":          3.30,
    "Madera con climalit":            2.50,
    "Invisible (RPT doble acrist.)":  1.80,
}

COMBUSTIBLES_DISPLAY = ["Gas natural","Gasóleo C","Electricidad","Biomasa pellet","Biomasa otros","GLP (gas licuado)","Carbón"]
COMBUSTIBLES_MODELO  = {"Gas natural":"GasNatural","Gasóleo C":"GasoleoC","Electricidad":"Electricidad",
                         "Biomasa pellet":"BiomasaPellet","Biomasa otros":"BiomasaOtros","GLP (gas licuado)":"GLP","Carbón":"Carbon"}
FACTORES_EMISIONES   = {"GasNatural":0.20,"GasoleoC":0.29,"Electricidad":0.27,"BiomasaPellet":0.02,"BiomasaOtros":0.02,"GLP":0.25,"Carbon":0.34}
RENDIMIENTOS         = {"Sin instalaciones":0.50,"Caldera Estándar":0.77,"Caldera de Condensación":0.95,
                         "Bomba de Calor Aerotermia":3.50,"Caldera Biomasa":0.85,"Radiadores Eléctricos":1.00,
                         "Suelo Radiante Eléctrico":1.00,"Suelo Radiante con Caldera":0.90}
COLORES = {'A':('#E8F5E9','#1B5E20'),'B':('#E3F2FD','#0D47A1'),'C':('#E8EAF6','#283593'),
           'D':('#FFFDE7','#F57F17'),'E':('#FFF3E0','#E65100'),'F':('#FCE4EC','#880E4F'),'G':('#FFEBEE','#B71C1C')}

def predecir(mc, me, zona, uso, año, norm, comp, sup, cons, emis, u_fach, u_vent,
             cal_tipo, cal_vec, cal_rend, acs_tipo, sol_t, sol_fv, dem_cal, dem_ref, dem_acs, ep_cal, ep_ref, ep_acs):
    encoders = mc['encoders']; cat_cols = mc['cat_cols']
    ic = {'zona_climatica':zona,'uso_edificio':uso,'normativa_vigente':norm,'calefac_tipo':cal_tipo,
          'calefac_vector':cal_vec,'acs_tipo':acs_tipo,'solar_termica':sol_t,'solar_fotovoltaica':sol_fv}
    fe = []
    for col in cat_cols:
        val = str(ic.get(col,'Desconocido')); le = encoders[col]
        fe.append(le.transform([val])[0] if val in le.classes_ else 0)
    x = np.array([[fe[0],fe[1],año,fe[2],comp,sup,cons,emis,u_fach,u_vent,fe[3],fe[4],cal_rend,fe[5],fe[6],fe[7],dem_cal,dem_ref,dem_acs,ep_cal,ep_ref,ep_acs]])
    pc = mc['model'].predict(x)[0]; pe = me['model'].predict(x)[0]
    prc = mc['model'].predict_proba(x)[0].max()*100; pre = me['model'].predict_proba(x)[0].max()*100
    return pc, pe, prc, pre

def badge(letra):
    bg,fg = COLORES.get(letra,('#F5F5F5','#333'))
    return f'<span style="background:{bg};color:{fg};font-size:26px;font-weight:bold;padding:4px 14px;border-radius:8px;display:inline-block;line-height:1.3;">{letra}</span>'

@st.cache_resource
def cargar_modelos():
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base,'modelo_consumo_ep.pkl'),'rb') as f: m1 = pickle.load(f)
        with open(os.path.join(base,'modelo_emisiones_co2.pkl'),'rb') as f: m2 = pickle.load(f)
        return m1, m2, True
    except FileNotFoundError:
        return None, None, False

modelo_consumo, modelo_emisiones, modelos_ok = cargar_modelos()

# ── INTERFAZ ──────────────────────────────────────────────────────
st.markdown("# ⚡ Predictor de Calificación Energética")
st.markdown("Introduce la referencia catastral de tu inmueble para comenzar.")
st.divider()

if not modelos_ok:
    st.error("⚠️ Modelos no encontrados. Ejecuta primero `py modelo_predictor.py`.")
    st.stop()

rc_input = st.text_input("Referencia catastral *", placeholder="Ej: 6310358XJ9761S0000TA",
    help="La encuentras en el recibo del IBI, en la escritura o en sede.catastro.gob.es")

catastro_ok = False
provincia_auto = zona_auto = año_auto = superficie_auto = None

if rc_input and len(rc_input.strip()) >= 14:
    with st.spinner("Consultando el Catastro..."):
        datos, error = consultar_catastro(rc_input)
    if error:
        st.error(f"⚠️ {error}")
    elif datos:
        provincia_auto = datos.get('provincia','')
        zona_auto      = get_zona(provincia_auto)
        año_auto       = datos.get('año_construccion')
        superficie_auto= datos.get('superficie')
        catastro_ok    = True
        st.markdown(f"""
        <div class="catastro-box">
            <b>✅ Datos obtenidos del Catastro</b><br>
            📍 <b>Dirección:</b> {datos.get('direccion','—')}<br>
            🏛️ <b>Provincia:</b> {provincia_auto} &nbsp;·&nbsp;
            🌡️ <b>Zona climática:</b> <b style="color:#1B5E20;">{zona_auto or '—'}</b><br>
            📅 <b>Año construcción:</b> {año_auto or '—'} &nbsp;·&nbsp;
            📐 <b>Superficie catastral:</b> {f"{superficie_auto:.0f} m²" if superficie_auto else '—'}
        </div>""", unsafe_allow_html=True)

if catastro_ok:
    zona_final = zona_auto or "D3"
    st.divider()
    st.markdown("## 🏠 Datos del inmueble")
    c1, c2 = st.columns(2)
    with c1:
        uso_edificio = st.selectbox("Tipo de edificio",
            ['Vivienda Individual en Bloque','Vivienda Unifamiliar','Bloque de Viviendas','Terciario'])
    with c2:
        año_construccion = st.number_input("Año de construcción",
            min_value=1900, max_value=2026, value=int(año_auto) if año_auto else 1978, step=1)

    normativa = normativa_año(año_construccion)
    u_fachada, _ = transmitancias_año(año_construccion)
    st.caption(f"📋 Normativa derivada: **{normativa}** · U fachada estimada: **{u_fachada} W/m²K**")

    st.divider()
    st.markdown("## 🔧 Instalaciones actuales")
    c3, c4 = st.columns(2)
    with c3:
        calefac_tipo = st.selectbox("Sistema de calefacción",
            ['Sin instalaciones','Caldera Estándar','Caldera de Condensación',
             'Bomba de Calor Aerotermia','Caldera Biomasa','Radiadores Eléctricos',
             'Suelo Radiante Eléctrico','Suelo Radiante con Caldera'])
        combustible = st.selectbox("Combustible / fuente de energía", COMBUSTIBLES_DISPLAY)
        calefac_vector = COMBUSTIBLES_MODELO[combustible]
    with c4:
        acs_tipo = st.selectbox("Sistema de agua caliente (ACS)",
            ['Sin instalaciones','Caldera Estándar','Caldera de Condensación',
             'Bomba de Calor Aerotermia','Calentador Eléctrico','Caldera Biomasa','Termosifón Solar'])
        solar_termica      = st.radio("¿Tiene solar térmica?",  ['NO','SI'], horizontal=True)
        solar_fotovoltaica = st.radio("¿Tiene fotovoltaica?",   ['NO','SI'], horizontal=True)

    calefac_rendimiento = RENDIMIENTOS.get(calefac_tipo, 0.77)

    st.divider()
    st.markdown("## 🪟 Ventanas")
    tipo_ventana   = st.selectbox("Tipo de ventana", list(VENTANAS_U.keys()))
    env_u_ventanas = VENTANAS_U[tipo_ventana]
    st.caption(f"Transmitancia estimada: **{env_u_ventanas} W/m²K**")

    consumo_ep_valor = st.number_input("Consumo EP no renovable conocido (kWh/m²año) — opcional",
        min_value=0.0, max_value=2000.0, value=0.0, step=1.0,
        help="Si tienes un CEE previo puedes introducirlo. Si no, déjalo en 0.")
    emisiones_co2_valor = st.number_input("Emisiones CO₂ conocidas (kgCO₂/m²año) — opcional",
        min_value=0.0, max_value=500.0, value=0.0, step=0.5)

    if consumo_ep_valor == 0:
        base = {'A3':80,'A4':90,'B3':110,'B4':120,'C1':150,'C2':155,'C3':145,'C4':160,
                'D1':180,'D2':185,'D3':190,'E1':220,'A1':70,'A2':75,'B1':100,'B2':105}
        consumo_ep_valor = max(30, min(base.get(zona_final,150) * (1+(1978-año_construccion)*0.003), 600))
    if emisiones_co2_valor == 0:
        emisiones_co2_valor = consumo_ep_valor * FACTORES_EMISIONES.get(calefac_vector, 0.25)

    sup  = float(superficie_auto) if superficie_auto else 85.0
    comp = 1.5
    dc   = consumo_ep_valor * 0.55; dr = consumo_ep_valor * 0.05; da = consumo_ep_valor * 0.18
    ec   = consumo_ep_valor * 0.71; er = consumo_ep_valor * 0.05; ea = consumo_ep_valor * 0.24

    st.divider()
    if st.button("⚡ Predecir calificación energética", type="primary", use_container_width=True):

        pred_c, pred_e, prob_c, prob_e = predecir(
            modelo_consumo, modelo_emisiones, zona_final, uso_edificio, año_construccion,
            normativa, comp, sup, consumo_ep_valor, emisiones_co2_valor, u_fachada,
            env_u_ventanas, calefac_tipo, calefac_vector, calefac_rendimiento,
            acs_tipo, solar_termica, solar_fotovoltaica, dc, dr, da, ec, er, ea)

        st.markdown("---")
        st.markdown("## 📊 Resultado de la predicción")
        r1, r2 = st.columns(2)
        with r1:
            bg,fg = COLORES.get(pred_c,('#F5F5F5','#333'))
            st.markdown(f'<div style="background:{bg};border-radius:12px;padding:20px;text-align:center;"><p style="color:{fg};font-size:13px;margin:0;font-weight:600;">Consumo Energía Primaria</p><p style="color:{fg};font-size:72px;font-weight:bold;margin:0;line-height:1.1;">{pred_c}</p><p style="color:{fg};font-size:12px;margin:4px 0 0;">~{consumo_ep_valor:.0f} kWh/m²·año</p><p style="color:{fg};font-size:11px;margin:4px 0 0;opacity:0.75;">Confianza: {prob_c:.0f}%</p></div>', unsafe_allow_html=True)
        with r2:
            bg2,fg2 = COLORES.get(pred_e,('#F5F5F5','#333'))
            st.markdown(f'<div style="background:{bg2};border-radius:12px;padding:20px;text-align:center;"><p style="color:{fg2};font-size:13px;margin:0;font-weight:600;">Emisiones CO₂</p><p style="color:{fg2};font-size:72px;font-weight:bold;margin:0;line-height:1.1;">{pred_e}</p><p style="color:{fg2};font-size:12px;margin:4px 0 0;">~{emisiones_co2_valor:.0f} kgCO₂/m²·año</p><p style="color:{fg2};font-size:11px;margin:4px 0 0;opacity:0.75;">Confianza: {prob_e:.0f}%</p></div>', unsafe_allow_html=True)

        st.markdown(f'<div class="info-box" style="margin-top:12px;">🌡️ <b>Zona:</b> {zona_final} &nbsp;·&nbsp; 📋 <b>Normativa:</b> {normativa} &nbsp;·&nbsp; 🪟 <b>U ventanas:</b> {env_u_ventanas} W/m²K &nbsp;·&nbsp; 🧱 <b>U fachada est.:</b> {u_fachada} W/m²K</div>', unsafe_allow_html=True)

        st.markdown("### 💡 Cómo mejorar tu calificación")
        st.markdown("Letra estimada si aplicas cada mejora:")
        letra_peor = max(pred_c, pred_e)
        mejoras = []
        if 'Aerotermia' not in calefac_tipo and 'Bomba de Calor' not in calefac_tipo:
            mejoras.append({'t':'🔥 Sustituir por Bomba de Calor Aerotérmica','d':'Mayor impacto posible. Reduce el consumo de calefacción un 60-70%.',
                'p':{'calefac_tipo':'Bomba de Calor Aerotermia','calefac_vector':'Electricidad','calefac_rendimiento':3.50,'consumo_ep_valor':consumo_ep_valor*0.40,'emisiones_co2_valor':emisiones_co2_valor*0.45}})
        if solar_termica == 'NO':
            mejoras.append({'t':'☀️ Instalar paneles solares térmicos para ACS','d':'Cubre entre el 40-70% de la demanda de agua caliente sanitaria.',
                'p':{'solar_termica':'SI','consumo_ep_valor':consumo_ep_valor*0.85,'emisiones_co2_valor':emisiones_co2_valor*0.85}})
        if solar_fotovoltaica == 'NO':
            mejoras.append({'t':'☀️ Instalar placas fotovoltaicas para autoconsumo','d':'Reduce el consumo eléctrico neto. Con batería el impacto es mayor.',
                'p':{'solar_fotovoltaica':'SI','consumo_ep_valor':consumo_ep_valor*0.80,'emisiones_co2_valor':emisiones_co2_valor*0.80}})
        if 'sin climalit' in tipo_ventana:
            mejoras.append({'t':'🪟 Cambio a doble acristalamiento con RPT','d':'Elimina puentes térmicos y reduce pérdidas de calor significativamente.',
                'p':{'env_u_ventanas':1.80}})
        elif 'climalit' in tipo_ventana and 'RPT' not in tipo_ventana:
            mejoras.append({'t':'🪟 Mejora a ventana invisible con RPT','d':'Paso adicional en eficiencia, especialmente en zonas frías.',
                'p':{'env_u_ventanas':1.80}})
        if letra_peor in ['E','F','G']:
            mejoras.append({'t':'🧱 Añadir aislamiento de fachada exterior (SATE)','d':'Reduce la demanda de calefacción y refrigeración sin obras interiores.',
                'p':{'u_fachada':0.40,'consumo_ep_valor':consumo_ep_valor*0.75,'emisiones_co2_valor':emisiones_co2_valor*0.75}})
        if letra_peor in ['C','D','E','F','G']:
            mejoras.append({'t':'🌡️ Termostato inteligente programable','d':'Reduce el consumo real entre un 15-25% ajustando a horarios de uso.',
                'p':{'consumo_ep_valor':consumo_ep_valor*0.85,'emisiones_co2_valor':emisiones_co2_valor*0.85}})

        for m in mejoras[:5]:
            p = m['p']
            pc_m, pe_m, _, _ = predecir(modelo_consumo, modelo_emisiones, zona_final, uso_edificio,
                año_construccion, normativa, comp, sup,
                p.get('consumo_ep_valor',consumo_ep_valor), p.get('emisiones_co2_valor',emisiones_co2_valor),
                p.get('u_fachada',u_fachada), p.get('env_u_ventanas',env_u_ventanas),
                p.get('calefac_tipo',calefac_tipo), p.get('calefac_vector',calefac_vector),
                p.get('calefac_rendimiento',calefac_rendimiento), acs_tipo, solar_termica,
                p.get('solar_fotovoltaica',solar_fotovoltaica), dc, dr, da, ec, er, ea)
            st.markdown(f'<div class="rec-box"><div style="flex:1;"><b>{m["t"]}</b><br><span style="color:#555;font-size:13px;">{m["d"]}</span></div><div style="text-align:center;flex-shrink:0;min-width:80px;"><p style="font-size:10px;color:#888;margin:0 0 4px;">Con esta mejora</p>{badge(pc_m)} {badge(pe_m)}</div></div>', unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("## 🎯 ¿Qué quieres hacer ahora?")
        b1, b2 = st.columns(2)
        with b1:
            st.markdown('<div style="background:#F1F8E9;border:1.5px solid #2E7D32;border-radius:12px;padding:20px;text-align:center;"><p style="font-size:28px;margin:0;">📄</p><p style="font-size:16px;font-weight:600;color:#1B5E20;margin:8px 0;">Certificado Energético oficial</p><p style="font-size:13px;color:#555;margin:0 0 12px;">Trámite completo con técnico certificado</p><a href="https://icerti.es/producto/certificado-energetico/" target="_blank" style="background:#1B5E20;color:white;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600;">Contratar CEE →</a></div>', unsafe_allow_html=True)
        with b2:
            st.markdown('<div style="background:#FFF8E1;border:1.5px solid #F59E0B;border-radius:12px;padding:20px;text-align:center;"><p style="font-size:28px;margin:0;">📊</p><p style="font-size:16px;font-weight:600;color:#E65100;margin:8px 0;">iCerti Passport Energy</p><p style="font-size:13px;color:#555;margin:0 0 4px;">Análisis detallado con recomendaciones personalizadas</p><p style="font-size:22px;font-weight:bold;color:#1B5E20;margin:0 0 8px;">20 €</p><a href="https://icerti.es/contacto/" target="_blank" style="background:#F59E0B;color:white;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600;">Solicitar informe →</a></div>', unsafe_allow_html=True)

st.markdown("---")
st.markdown('<p style="text-align:center;color:#888;font-size:12px;">iCerti · Predictor basado en IA entrenada con +20.000 certificados propios · <a href="https://icerti.es" style="color:#2E7D32;">icerti.es</a> · Esta estimación no sustituye al certificado oficial</p>', unsafe_allow_html=True)
