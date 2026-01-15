import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Calcul Paie Livreur - Logique Certifiée", layout="wide")

# ==========================================
# 1. OUTILS DE NETTOYAGE
# ==========================================

def clean_phone(val):
    if pd.isna(val) or val == "": return ""
    # On force la conversion en string pour éviter les erreurs
    s = str(val)
    # Nettoyage strict : ne garder que les chiffres
    s = re.sub(r'[^0-9]', '', s)
    
    # Formatage +212
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
        # Gestion séparateur point-virgule si nécessaire
        if len(df.columns) < 2:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    except: return pd.DataFrame()

# ==========================================
# 2. MOTEUR DE CALCUL
# ==========================================

def generate_report(df_data, df_avance, df_credit, df_ribs, df_restos_diff):
    
    # --- 1. NETTOYAGE & PRÉPARATION ---
    
    # Nettoyage Téléphone (Clé de jointure)
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    
    # Nettoyage Noms Restaurants (pour comparaison)
    df_data['resto_clean'] = df_data['restaurant name'].astype(str).str.lower().str.strip()
    
    # Préparation Liste Restos Différés (Set pour rapidité)
    deferred_set = set()
    if not df_restos_diff.empty:
        # On suppose que le nom est dans la 1ère colonne
        col_name = df_restos_diff.columns[0]
        deferred_set = set(df_restos_diff[col_name].astype(str).str.lower().str.strip())

    # Conversion des colonnes financières en chiffres
    cols_money = ['driver payout', 'amount to restaurant', 'coupon discount', 
                  'Driver Cash Co', 'Bonus Amount', 'Payment Guarantee', 'Recovered Amount']
    for c in cols_money:
        if c in df_data.columns: df_data[c] = df_data[c].apply(parse_money)
        else: df_data[c] = 0.0

    # Dictionnaire des RIBs
    rib_map = {}
    if not df_ribs.empty:
        # On cherche colonne Tel et RIB
        c_tel = next((c for c in df_ribs.columns if 'phone' in str(c).lower()), df_ribs.columns[0])
        c_rib = next((c for c in df_ribs.columns if 'rib' in str(c).lower()), df_ribs.columns[1])
        
        df_ribs['p'] = df_ribs[c_tel].apply(clean_phone)
        rib_map = df_ribs.set_index('p')[c_rib].to_dict()

    # Filtre : On ignore les commandes annulées
    df = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()

    # --- 2. CALCUL PAR LIVREUR ---
    rows = []
    
    for phone, group in df.groupby('phone_clean'):
        if not phone: continue # Sécurité ligne vide
        
        name = group['driver name'].iloc[0]
        
        # --- LOGIQUE DE PAIEMENT ---
        
        # 1. Identifier le type de chaque commande
        pay_method = group['Payment Method'].astype(str)
        
        is_payzone = pay_method.str.contains('PAYZONE', case=False, na=False)
        is_meth_def = pay_method.str.contains('Deferred|Corporate|Différé', case=False, na=False)
        is_resto_def = group['resto_clean'].isin(deferred_set)
        
        # "No-Pay" = Le livreur n'a PAS sorti d'argent pour payer le resto
        is_no_pay = is_payzone | is_meth_def | is_resto_def
        
        # --- CALCUL SOLDE OPS (CASH CO CORRIGÉ) ---
        
        raw_cash_co = group['Driver Cash Co'].sum()
        
        # Correction : On rajoute le montant du resto au Cash Co pour les commandes No-Pay
        # (Car le système l'a déduit alors que le livreur ne l'a pas payé)
        correction = group.loc[is_no_pay, 'amount to restaurant'].sum()
        
        corrected_cash_co = raw_cash_co + correction
        
        # Ce que Yassir doit au livreur sur les Ops = -1 * CashCo Corrigé
        solde_ops = -1 * corrected_cash_co
        
        # --- AUTRES VALEURS ---
        
        # Commissions (Total)
        payout_total = group['driver payout'].sum()
        
        # Montant que Yassir doit payer aux restos (Info)
        amt_rest_yassir = group.loc[is_no_pay, 'amount to restaurant'].sum()
        
        # Coupons remboursables (Uniquement sur commandes Cash)
        coupon_cash = group.loc[~is_no_pay, 'coupon discount'].sum()
        
        # Bonus & Autres
        bonus = group['Bonus Amount'].sum()
        guarantee = group['Payment Guarantee'].sum()
        recovered = group['Recovered Amount'].sum()
        
        # RIB
        rib = rib_map.get(phone, "")
        if not rib and 'RIB' in group.columns:
            possible_ribs = group['RIB'].dropna().unique()
            if len(possible_ribs) > 0: rib = possible_ribs[0]

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
            'Payment Guarantee': guarantee,
            'Recovered Amount': recovered,
            '_Solde_Ops': solde_ops
        })

    res = pd.DataFrame(rows)
    if res.empty: return pd.DataFrame()

    # --- 3. FUSION AVANCE / CREDIT ---
    
    # AVANCE
    if not df_avance.empty:
        c_av_ph = next((c for c in df_avance.columns if 'phone' in str(c).lower()), df_avance.columns[-1])
        c_av_mt = next((c for c in df_avance.columns if 'avance' in str(c).lower()), df_avance.columns[1])
        
        df_avance['p'] = df_avance[c_av_ph].apply(clean_phone)
        df_avance['m'] = df_avance[c_av_mt].apply(parse_money)
        
        grp_av = df_avance.groupby('p')['m'].sum().rename('Avance payé')
        res = res.merge(grp_av, left_on='driver Phone', right_index=True, how='left')
    
    # CREDIT
    if not df_credit.empty:
        c_cr_ph = next((c for c in df_credit.columns if 'phone' in str(c).lower()), df_credit.columns[-1])
        c_cr_mt = next((c for c in df_credit.columns if 'amount' in str(c).lower()), df_credit.columns[1])
        
        df_credit['p'] = df_credit[c_cr_ph].apply(clean_phone)
        df_credit['m'] = df_credit[c_cr_mt].apply(parse_money)
        
        grp_cr = df_credit.groupby('p')['m'].sum().rename('Credit Balance')
        res = res.merge(grp_cr, left_on='driver Phone', right_index=True, how='left')

    # Remplir les vides par 0
    res['Avance payé'] = res.get('Avance payé', 0).fillna(0)
    res['Credit Balance'] = res.get('Credit Balance', 0).fillna(0)

    # --- 4. SOLDE FINAL ---
    res['Total Amount (Driver Solde)'] = (
        res['_Solde_Ops'] + 
        res['Bonus Value'] + 
        res['Credit Balance'] + 
        res['Payment Guarantee'] + 
        res['Recovered Amount'] - 
        res['Avance payé']
    )

    # Sélection colonnes finales
    cols_order = ['driver Phone','driver name','RIB','Total Orders','Payzone/Deferred',
            'Yassir driver payout','Yassir amount to restaurant','Yassir coupon discount',
            'Payment Guarantee','Bonus Value','Credit Balance','Recovered Amount',
            'Avance payé','Total Amount (Driver Solde)']
            
    return res[[c for c in cols_order if c in res.columns]]

# ==========================================
# 3. INTERFACE UTILISATEUR
# ==========================================

col1, col2 = st.columns(2)

with col1:
    f_d = st.file_uploader("1. DATA (CSV/Excel)", type=['csv','xlsx'])
    f_a = st.file_uploader("2. AVANCE", type=['csv','xlsx'])
    f_c = st.file_uploader("3. CREDIT", type=['csv','xlsx'])

with col2:
    f_r = st.file_uploader("4. RESTOS DIFFÉRÉS (Liste)", type=['csv','xlsx'])
    f_rib = st.file_uploader("5. RIBs", type=['csv','xlsx'])

if st.button("CALCULER"):
    if f_d:
        with st.spinner("Traitement en cours..."):
            # Chargement des données
            d = load_data(f_d)
            a = load_data(f_a)
            c = load_data(f_c)
            # ICI LE CHANGEMENT DE NOM DE VARIABLE : df_restos au lieu de re
            df_restos = load_data(f_r) 
            r_rib = load_data(f_rib)
            
            if not d.empty:
                # Appel fonction avec le bon nom
                final = generate_report(d, a, c, r_rib, df_restos)
                
                st.metric("Total à Payer", f"{final['Total Amount (Driver Solde)'].sum():,.2f}")
                st.dataframe(final)
                
                # Export Excel
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    final.to_excel(writer, index=False)
                st.download_button("Télécharger Excel", buffer.getvalue(), "Paie_Finale.xlsx")
            else:
                st.error("Le fichier Data est vide.")
    else:
        st.warning("Veuillez charger au moins le fichier Data.")
