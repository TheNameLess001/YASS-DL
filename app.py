import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Calcul Paie Livreur - Logique Certifiée", layout="wide")

# --- OUTILS ---
def clean_phone(val):
    if pd.isna(val) or val == "": return ""
    s = re.sub(r'[^0-9]', '', str(val))
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

# --- MOTEUR DE CALCUL ---
def generate_report(df_data, df_avance, df_credit, df_ribs, df_restos_diff):
    
    # 1. Nettoyage
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    df_data['resto_clean'] = df_data['restaurant name'].astype(str).str.lower().str.strip()
    
    # 2. Restos Différés (Liste)
    deferred_set = set()
    if not df_restos_diff.empty:
        # Cherche la 1ere colonne
        col_name = df_restos_diff.columns[0]
        deferred_set = set(df_restos_diff[col_name].astype(str).str.lower().str.strip())

    # 3. Conversion Chiffres
    cols_money = ['driver payout', 'amount to restaurant', 'coupon discount', 
                  'Driver Cash Co', 'Bonus Amount', 'Payment Guarantee', 'Recovered Amount']
    for c in cols_money:
        if c in df_data.columns: df_data[c] = df_data[c].apply(parse_money)
        else: df_data[c] = 0.0

    # 4. RIBs
    rib_map = {}
    if not df_ribs.empty:
        # Suppose col 0 = Tel, col 1 = RIB
        df_ribs['p'] = df_ribs.iloc[:,0].apply(clean_phone)
        rib_map = df_ribs.set_index('p').iloc[:,1].to_dict()

    # 5. Filtrage (Exclure Cancelled)
    df = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()

    # 6. Agrégation
    rows = []
    for phone, group in df.groupby('phone_clean'):
        name = group['driver name'].iloc[0]
        
        # Identification Types
        pay_method = group['Payment Method'].astype(str)
        is_payzone = pay_method.str.contains('PAYZONE', case=False, na=False)
        is_meth_def = pay_method.str.contains('Deferred|Corporate|Différé', case=False, na=False)
        is_resto_def = group['resto_clean'].isin(deferred_set)
        
        # "No-Pay" : Le livreur n'a pas sorti d'argent pour le resto
        is_no_pay = is_payzone | is_meth_def | is_resto_def
        
        # --- CŒUR DU CALCUL (CORRECTION CASH CO) ---
        raw_cash_co = group['Driver Cash Co'].sum()
        
        # On ajoute le prix du resto au Cash Co pour les commandes No-Pay
        # (Car le Cash Co brut a déduit ce montant à tort)
        correction = group.loc[is_no_pay, 'amount to restaurant'].sum()
        
        corrected_cash_co = raw_cash_co + correction
        
        # Le solde dû par Yassir est l'inverse du Cash Co corrigé
        # (Si Cash Co corrigé est -100, Yassir doit +100)
        solde_ops = -1 * corrected_cash_co
        
        # Autres colonnes pour affichage
        bonus = group['Bonus Amount'].sum()
        payout_total = group['driver payout'].sum()
        amt_rest_yassir = group.loc[is_no_pay, 'amount to restaurant'].sum()
        coupon_cash = group.loc[~is_no_pay, 'coupon discount'].sum() # Approx pour affichage
        
        rib = rib_map.get(phone, "")
        if not rib and 'RIB' in group.columns:
            r = group['RIB'].dropna().unique()
            if len(r)>0: rib = r[0]

        rows.append({
            'driver Phone': phone,
            'driver name': name,
            'RIB': str(rib).replace(" ", ""),
            'Total Orders': len(group),
            'Payzone/Deferred': is_no_pay.sum(),
            'Yassir driver payout': payout_total,
            'Yassir amount to restaurant': amt_rest_yassir,
            'Yassir coupon discount': coupon_cash,
            'Bonus Value': bonus,
            'Payment Guarantee': group['Payment Guarantee'].sum(),
            'Recovered Amount': group['Recovered Amount'].sum(),
            '_Solde_Ops': solde_ops
        })

    res = pd.DataFrame(rows)
    if res.empty: return pd.DataFrame()

    # 7. Fusion Avance / Crédit
    if not df_avance.empty:
        # Cherche colonne phone (souvent la dernière ou celle avec 'phone')
        c_av_ph = next((c for c in df_avance.columns if 'phone' in c.lower()), df_avance.columns[-1])
        c_av_mt = next((c for c in df_avance.columns if 'avance' in c.lower()), df_avance.columns[1])
        df_avance['p'] = df_avance[c_av_ph].apply(clean_phone)
        df_avance['m'] = df_avance[c_av_mt].apply(parse_money)
        res = res.merge(df_avance.groupby('p')['m'].sum().rename('Avance payé'), left_on='driver Phone', right_index=True, how='left')
    
    if not df_credit.empty:
        c_cr_ph = next((c for c in df_credit.columns if 'phone' in c.lower()), df_credit.columns[-1])
        c_cr_mt = next((c for c in df_credit.columns if 'amount' in c.lower()), df_credit.columns[1])
        df_credit['p'] = df_credit[c_cr_ph].apply(clean_phone)
        df_credit['m'] = df_credit[c_cr_mt].apply(parse_money)
        res = res.merge(df_credit.groupby('p')['m'].sum().rename('Credit Balance'), left_on='driver Phone', right_index=True, how='left')

    res['Avance payé'] = res.get('Avance payé', 0).fillna(0)
    res['Credit Balance'] = res.get('Credit Balance', 0).fillna(0)

    # 8. SOLDE FINAL
    res['Total Amount (Driver Solde)'] = (
        res['_Solde_Ops'] + 
        res['Bonus Value'] + 
        res['Credit Balance'] + 
        res['Payment Guarantee'] + 
        res['Recovered Amount'] - 
        res['Avance payé']
    )

    # Colonnes finales
    cols = ['driver Phone','driver name','RIB','Total Orders','Payzone/Deferred',
            'Yassir driver payout','Yassir amount to restaurant','Yassir coupon discount',
            'Payment Guarantee','Bonus Value','Credit Balance','Recovered Amount',
            'Avance payé','Total Amount (Driver Solde)']
            
    return res[[c for c in cols if c in res.columns]]

# --- INTERFACE ---
col1, col2 = st.columns(2)
f_d = col1.file_uploader("1. DATA", type=['csv','xlsx'])
f_a = col1.file_uploader("2. AVANCE", type=['csv','xlsx'])
f_c = col1.file_uploader("3. CREDIT", type=['csv','xlsx'])
f_r = col2.file_uploader("4. RESTOS DIFF (Noms)", type=['csv','xlsx'])
f_rib = col2.file_uploader("5. RIBs", type=['csv','xlsx'])

if st.button("CALCULER"):
    if f_d:
        with st.spinner("Calcul en cours..."):
            d = load_data(f_d)
            a = load_data(f_a)
            c = load_data(f_c)
            r = load_data(f_rib)
            re = load_data(f_r)
            if not d.empty:
                final = generate_report(d,a,c,r,re)
                st.metric("Total à Payer", f"{final['Total Amount (Driver Solde)'].sum():,.2f}")
                st.dataframe(final)
                
                # Export
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    final.to_excel(writer, index=False)
                st.download_button("Télécharger Excel", buffer.getvalue(), "Paie_Juste.xlsx")
