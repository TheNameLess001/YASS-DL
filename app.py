import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Calcul Paie - Logique Exacte", layout="wide")

# ==========================================
# 1. OUTILS DE NETTOYAGE
# ==========================================

def clean_phone(val):
    if pd.isna(val) or val == "": return ""
    s = str(val)
    s = re.sub(r'[^0-9]', '', s)
    if s.startswith("00"): s = s[2:]
    if s.startswith("212"): s = "+" + s
    elif s.startswith("0"): s = "+212" + s[1:]
    elif not s.startswith("+"): s = "+212" + s
    return s

def parse_money(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    s = str(val).replace(" ", "").replace(",", ".")
    s = re.sub(r'[^0-9\.\-]', '', s)
    try: return float(s)
    except: return 0.0

def load_data(file):
    if not file: return pd.DataFrame()
    try:
        if file.name.endswith('.xlsx'): return pd.read_excel(file)
        file.seek(0)
        df = pd.read_csv(file)
        if len(df.columns) < 2:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    except: return pd.DataFrame()

# ==========================================
# 2. MOTEUR DE CALCUL
# ==========================================

def generate_report(df_data, df_avance, df_credit, df_ribs, df_restos_diff):
    
    # 1. PRÉPARATION
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    df_data['resto_clean'] = df_data['restaurant name'].astype(str).str.lower().str.strip()
    
    # Conversion Argent
    cols_money = ['driver payout', 'amount to restaurant', 'coupon discount', 
                  'Bonus Amount', 'Payment Guarantee', 'Recovered Amount']
    for c in cols_money:
        if c in df_data.columns: df_data[c] = df_data[c].apply(parse_money)
        else: df_data[c] = 0.0

    # Liste Restos Différés
    deferred_set = set()
    if not df_restos_diff.empty:
        col_name = df_restos_diff.columns[0]
        deferred_set = set(df_restos_diff[col_name].astype(str).str.lower().str.strip())

    # RIBs
    rib_map = {}
    if not df_ribs.empty:
        c_tel = next((c for c in df_ribs.columns if 'phone' in str(c).lower()), df_ribs.columns[0])
        c_rib = next((c for c in df_ribs.columns if 'rib' in str(c).lower()), df_ribs.columns[1])
        df_ribs['p'] = df_ribs[c_tel].apply(clean_phone)
        rib_map = df_ribs.set_index('p')[c_rib].to_dict()

    # Filtre Annulé
    df = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()

    # 2. CALCUL PAR LIVREUR
    rows = []
    
    for phone, group in df.groupby('phone_clean'):
        if not phone: continue
        name = group['driver name'].iloc[0]
        
        # --- TYPOLOGIE ---
        pay_method = group['Payment Method'].astype(str)
        is_payzone = pay_method.str.contains('PAYZONE', case=False, na=False)
        is_meth_def = pay_method.str.contains('Deferred|Corporate|Différé', case=False, na=False)
        is_resto_def = group['resto_clean'].isin(deferred_set)
        
        # Différé (Livreur encaisse Cash mais paie 0 Resto)
        is_deferred = is_meth_def | is_resto_def
        
        # Cash Normal (Livreur encaisse Cash et paie Resto)
        is_cash_normal = (~is_payzone) & (~is_deferred)
        
        # --- CALCULS EXACTS (SELON SS.CSV) ---
        
        # 1. Commissions : On paie seulement PAYZONE
        # (Car en Cash et en Différé, il a gardé le cash, donc il s'est payé tout seul)
        comm_payzone = group.loc[is_payzone, 'driver payout'].sum()
        
        # 2. Coupons : On rembourse seulement sur le CASH NORMAL
        # (C'est là où il a perdu de l'argent de sa poche)
        coupon_reimb = group.loc[is_cash_normal, 'coupon discount'].sum()
        
        # 3. Bonus, Garantie, Recovered
        bonus = group['Bonus Amount'].sum()
        guarantee = group['Payment Guarantee'].sum() if 'Payment Guarantee' in group.columns else 0
        recovered = group['Recovered Amount'].sum() if 'Recovered Amount' in group.columns else 0
        
        # 4. Somme de base (Gains positifs)
        solde_base = comm_payzone + coupon_reimb + bonus + guarantee + recovered
        
        # Infos pour affichage
        payout_total = group['driver payout'].sum()
        amt_rest_yassir = group.loc[is_payzone | is_deferred, 'amount to restaurant'].sum()
        
        rib = rib_map.get(phone, "")
        if not rib and 'RIB' in group.columns:
            r = group['RIB'].dropna().unique()
            if len(r)>0: rib = r[0]

        rows.append({
            'driver Phone': phone,
            'driver name': name,
            'RIB': str(rib).replace(" ", ""),
            'Total Orders': len(group),
            'Deferred Orders': is_deferred.sum(),
            'Payzone Orders': is_payzone.sum(),
            
            # Colonnes informatives
            'Yassir driver payout': payout_total, 
            'Yassir amount to restaurant': amt_rest_yassir,
            'Yassir coupon discount': coupon_reimb,
            'Payment Guarantee': guarantee,
            'Bonus Value': bonus,
            'Recovered Amount': recovered,
            
            # Colonne cachée pour le calcul
            '_Solde_Base': solde_base
        })

    res = pd.DataFrame(rows)
    if res.empty: return pd.DataFrame()

    # 3. FUSION AVANCE / CREDIT
    if not df_avance.empty:
        c_av_ph = next((c for c in df_avance.columns if 'phone' in str(c).lower()), df_avance.columns[-1])
        c_av_mt = next((c for c in df_avance.columns if 'avance' in str(c).lower()), df_avance.columns[1])
        df_avance['p'] = df_avance[c_av_ph].apply(clean_phone)
        df_avance['m'] = df_avance[c_av_mt].apply(parse_money)
        res = res.merge(df_avance.groupby('p')['m'].sum().rename('Avance payé'), left_on='driver Phone', right_index=True, how='left')

    if not df_credit.empty:
        c_cr_ph = next((c for c in df_credit.columns if 'phone' in str(c).lower()), df_credit.columns[-1])
        c_cr_mt = next((c for c in df_credit.columns if 'amount' in str(c).lower()), df_credit.columns[1])
        df_credit['p'] = df_credit[c_cr_ph].apply(clean_phone)
        df_credit['m'] = df_credit[c_cr_mt].apply(parse_money)
        res = res.merge(df_credit.groupby('p')['m'].sum().rename('Credit Balance'), left_on='driver Phone', right_index=True, how='left')

    res['Avance payé'] = res.get('Avance payé', 0).fillna(0)
    res['Credit Balance'] = res.get('Credit Balance', 0).fillna(0)

    # 4. SOLDE FINAL
    res['Total Amount (Driver Solde)'] = (
        res['_Solde_Base'] + 
        res['Credit Balance'] - 
        res['Avance payé']
    )

    cols = ['driver Phone','driver name','RIB','Total Orders','Deferred Orders','Payzone Orders',
            'Yassir driver payout','Yassir amount to restaurant','Yassir coupon discount',
            'Payment Guarantee','Bonus Value','Credit Balance','Recovered Amount',
            'Avance payé','Total Amount (Driver Solde)']
            
    return res[[c for c in cols if c in res.columns]]

# ==========================================
# INTERFACE
# ==========================================

col1, col2 = st.columns(2)
with col1:
    f_d = st.file_uploader("1. DATA", type=['csv','xlsx'])
    f_a = st.file_uploader("2. AVANCE", type=['csv','xlsx'])
    f_c = st.file_uploader("3. CREDIT", type=['csv','xlsx'])
with col2:
    f_r = st.file_uploader("4. RESTOS DIFFÉRÉS", type=['csv','xlsx'])
    f_rib = st.file_uploader("5. RIBs", type=['csv','xlsx'])

if st.button("CALCULER"):
    if f_d:
        with st.spinner("Calcul en cours..."):
            d = load_data(f_d)
            a = load_data(f_a)
            c = load_data(f_c)
            # Correction variable shadowing ici
            df_restos = load_data(f_r)
            r_rib = load_data(f_rib)
            
            if not d.empty:
                final = generate_report(d, a, c, r_rib, df_restos)
                st.metric("Total à Verser", f"{final['Total Amount (Driver Solde)'].sum():,.2f}")
                st.dataframe(final)
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    final.to_excel(writer, index=False)
                st.download_button("Télécharger Excel", buffer.getvalue(), "Paie_Finale.xlsx")
            else:
                st.error("Fichier Data vide.")
