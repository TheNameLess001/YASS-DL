import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Générateur Paie Yassir", layout="wide")

# ==========================================
# 1. OUTILS DE NETTOYAGE
# ==========================================

def clean_phone(val):
    if pd.isna(val) or val == "": return ""
    s = str(val).replace(" ", "").replace(".", "").replace("-", "").strip()
    s = re.sub(r'[^0-9\+]', '', s)
    if s.startswith("00"): s = "+" + s[2:]
    elif s.startswith("212"): s = "+" + s
    elif s.startswith("0") and len(s) == 10: s = "+212" + s[1:]
    if not s.startswith("+"): s = "+212" + s
    return s

def clean_name(val):
    if pd.isna(val): return ""
    return str(val).lower().strip()

def clean_rib(val):
    if pd.isna(val): return ""
    return str(val).replace(" ", "").strip()

def parse_money(val):
    if pd.isna(val) or val == "": return 0.0
    s = str(val).replace(" ", "").replace(",", ".")
    s = re.sub(r'[^0-9\.\-]', '', s)
    try: return float(s)
    except: return 0.0

def load_data(file):
    if not file: return pd.DataFrame()
    try:
        if file.name.endswith('.xlsx'):
            return pd.read_excel(file)
        file.seek(0)
        df = pd.read_csv(file)
        if len(df.columns) < 2:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    except Exception as e:
        st.error(f"Erreur lecture {file.name}: {e}")
        return pd.DataFrame()

# ==========================================
# 2. LOGIQUE MÉTIER
# ==========================================

def generate_report(df_data, df_avance, df_credit, df_ribs_supp, df_restos_diff):
    
    # 1. Nettoyage
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    df_data['resto_clean'] = df_data['restaurant name'].apply(clean_name)
    
    money_cols = ['driver payout', 'amount to restaurant', 'coupon discount', 
                  'delivery amount', 'service charge', 'restaurant commission',
                  'Driver Cash Co', 'Bonus Amount']
    for c in money_cols:
        if c in df_data.columns:
            df_data[c] = df_data[c].apply(parse_money)
        else:
            df_data[c] = 0.0

    # 2. Restos Différés
    deferred_restos_set = set()
    if not df_restos_diff.empty:
        cols = [str(c).lower() for c in df_restos_diff.columns]
        idx_name = next((i for i, c in enumerate(cols) if 'name' in c or 'nom' in c or 'restaurant' in c), 0)
        col_name = df_restos_diff.columns[idx_name]
        deferred_restos_set = set(df_restos_diff[col_name].apply(clean_name).dropna().unique())

    # 3. RIBs
    rib_mapping = {}
    if not df_ribs_supp.empty:
        cols = [str(c).lower() for c in df_ribs_supp.columns]
        idx_phone = next((i for i, c in enumerate(cols) if 'phone' in c or 'téléphone' in c), -1)
        idx_rib = next((i for i, c in enumerate(cols) if 'rib' in c), -1)
        if idx_phone != -1 and idx_rib != -1:
            col_phone = df_ribs_supp.columns[idx_phone]
            col_rib = df_ribs_supp.columns[idx_rib]
            df_ribs_supp['temp_phone'] = df_ribs_supp[col_phone].apply(clean_phone)
            rib_mapping = df_ribs_supp.set_index('temp_phone')[col_rib].to_dict()

    # 4. Filtre
    df_valid = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()
    
    groups = df_valid.groupby('phone_clean')
    report_rows = []
    
    for phone, group in groups:
        driver_name = group['driver name'].iloc[0]
        
        # --- TYPOLOGIE ---
        # 1. Non-Cash (Le livreur n'a pas l'argent)
        # Payzone
        is_payzone = group['Payment Method'].astype(str).str.contains('PAYZONE', case=False, na=False)
        # Différé Méthode
        is_meth_def = group['Payment Method'].astype(str).str.contains('Deferred|Corporate|Différé', case=False, na=False)
        # Différé Resto
        is_resto_def = group['resto_clean'].isin(deferred_restos_set)
        
        # Global Non-Cash (Pour le calcul Commission à verser)
        # Attention: Si Resto Différé mais Client Cash -> Livreur a le cash. 
        # Mais le livreur n'a pas payé le resto.
        # Logique demandée : "Paiement différé = le livreur ne paie pas la commande mais doit avoir sa commission".
        # Si le client paie Cash, le livreur a le Cash. Il se paie dessus.
        # Donc "Non-Cash" strict pour le versement = Payzone OU Corporate.
        is_strict_non_cash = is_payzone | is_meth_def
        
        # Commandes Cash (Le livreur a le cash)
        is_cash = (~is_strict_non_cash) & (group['Payment Method'].astype(str).str.upper().str.strip() == 'CASH')
        
        # --- CALCULS ---
        
        # Payout (Commission)
        # A verser uniquement pour les commandes où il n'a pas de cash (Payzone/Corporate)
        # Pour le Cash, il l'a déjà pris à la source.
        # Note : Le fichier final demande "Yassir driver payout" (Total).
        total_payout = group['driver payout'].sum()
        payout_to_pay = group.loc[is_strict_non_cash, 'driver payout'].sum()
        
        # Resto Amount
        # A verser aux restos par Yassir (Payzone + Corporate + Resto Différé)
        is_yassir_pay_resto = is_payzone | is_meth_def | is_resto_def
        amt_rest_yassir = group.loc[is_yassir_pay_resto, 'amount to restaurant'].sum()
        
        # Coupon
        # A rembourser au livreur uniquement si Cash (car il a encaissé moins)
        coupon_reimb = group.loc[is_cash, 'coupon discount'].sum()
        
        # Bonus
        bonus = group['Bonus Amount'].sum()
        
        # Delivery Amount (Info)
        del_amt = group.loc[is_cash, 'delivery amount'].sum()
        
        # Service Charge (Info)
        serv_chg = group.loc[is_cash, 'service charge'].sum()
        
        # Counts
        nb_def = (is_meth_def | is_resto_def).sum()
        nb_payz = is_payzone.sum()
        
        # RIB
        final_rib = rib_mapping.get(phone, "")
        if not final_rib and 'RIB' in group.columns:
            r = group['RIB'].dropna().unique()
            if len(r) > 0: final_rib = r[0]
            
        # Colonnes placeholder (si pas dans Data)
        pay_guarantee = group['Payment Guarantee'].sum() if 'Payment Guarantee' in group.columns else 0
        recovered = group['Recovered Amount'].sum() if 'Recovered Amount' in group.columns else 0

        row = {
            'driver Phone': phone,
            'driver name': driver_name,
            'RIB': clean_rib(final_rib),
            'Total Orders': len(group),
            'Deferred Orders': nb_def,
            'Payzone Orders': nb_payz,
            'Yassir driver payout': total_payout,      # Affiche Total
            'Yassir amount to restaurant': amt_rest_yassir,
            'Yassir coupon discount': coupon_reimb,    # Affiche part remboursable
            'Payment Guarantee': pay_guarantee,
            'Bonus Value': bonus,
            'Recovered Amount': recovered,
            'driver delivery amount': del_amt,
            'driver service Charge': serv_chg,
            # Valeurs cachées pour calcul solde
            '_payout_to_pay': payout_to_pay
        }
        report_rows.append(row)
        
    df_rep = pd.DataFrame(report_rows)
    if df_rep.empty: return pd.DataFrame()

    # --- FUSION AVANCE / CREDIT ---
    
    # Avance (A DÉDUIRE)
    if not df_avance.empty:
        cols = [str(c).lower() for c in df_avance.columns]
        idx = next((i for i,c in enumerate(cols) if 'phone' in c), -1)
        col_t = df_avance.columns[idx] if idx != -1 else df_avance.columns[-1]
        df_avance['phone_clean'] = df_avance[col_t].apply(clean_phone)
        col_a = next((c for c in df_avance.columns if 'avance' in str(c).lower()), None)
        if col_a:
            df_avance['av_val'] = df_avance[col_a].apply(parse_money)
            grp = df_avance.groupby('phone_clean')['av_val'].sum()
            df_rep = df_rep.merge(grp, left_on='driver Phone', right_index=True, how='left')
            df_rep.rename(columns={'av_val': 'Avance payé'}, inplace=True)
            
    if 'Avance payé' not in df_rep.columns: df_rep['Avance payé'] = 0
    df_rep['Avance payé'] = df_rep['Avance payé'].fillna(0)
    
    # Crédit (A AJOUTER)
    if not df_credit.empty:
        cols = [str(c).lower() for c in df_credit.columns]
        idx = next((i for i,c in enumerate(cols) if 'phone' in c), -1)
        col_t = df_credit.columns[idx] if idx != -1 else df_credit.columns[1]
        df_credit['phone_clean'] = df_credit[col_t].apply(clean_phone)
        col_c = next((c for c in df_credit.columns if 'amount' in str(c).lower()), None)
        if col_c:
            df_credit['cr_val'] = df_credit[col_c].apply(parse_money)
            grp = df_credit.groupby('phone_clean')['cr_val'].sum()
            df_rep = df_rep.merge(grp, left_on='driver Phone', right_index=True, how='left')
            df_rep.rename(columns={'cr_val': 'Credit Balance'}, inplace=True)

    if 'Credit Balance' not in df_rep.columns: df_rep['Credit Balance'] = 0
    df_rep['Credit Balance'] = df_rep['Credit Balance'].fillna(0)
    
    # --- SOLDE FINAL ---
    # Solde = (Commissions Non-Cash) + (Coupon Cash) + Bonus + Credit + Garantie - Avance
    df_rep['Total Amount (Driver Solde)'] = (
        df_rep['_payout_to_pay'] + 
        df_rep['Yassir coupon discount'] + 
        df_rep['Bonus Value'] + 
        df_rep['Credit Balance'] +
        df_rep['Payment Guarantee'] +
        df_rep['Recovered Amount'] - 
        df_rep['Avance payé']
    )
    
    cols_order = [
        'driver Phone', 'driver name', 'RIB', 
        'Total Orders', 'Deferred Orders', 'Payzone Orders', 
        'Yassir driver payout', 'Yassir amount to restaurant', 'Yassir coupon discount',
        'Payment Guarantee', 'Bonus Value', 'Credit Balance', 'Recovered Amount', 'Avance payé', 
        'driver delivery amount', 'driver service Charge', 'Total Amount (Driver Solde)'
    ]
    
    # Compléter colonnes manquantes
    for c in cols_order:
        if c not in df_rep.columns: df_rep[c] = 0
        
    return df_rep[cols_order]

# ==========================================
# 3. INTERFACE
# ==========================================

st.title("Calcul Paie Livreur (Logique Validée)")

with col1:
    f_data = st.file_uploader("1. DATA", type=['csv', 'xlsx'])
    f_av = st.file_uploader("2. AVANCE", type=['csv', 'xlsx'])
    f_cr = st.file_uploader("3. CREDIT", type=['csv', 'xlsx'])

with col2:
    f_rest = st.file_uploader("4. RESTOS DIFFÉRÉS", type=['csv', 'xlsx'])
    f_rib = st.file_uploader("5. RIBs", type=['csv', 'xlsx'])

if st.button("Lancer", type="primary"):
    if f_data:
        df_d = load_data(f_data)
        df_a = load_data(f_av)
        df_c = load_data(f_cr)
        df_r = load_data(f_rib)
        df_re = load_data(f_rest)
        
        if not df_d.empty:
            res = generate_report(df_d, df_a, df_c, df_r, df_re)
            st.metric("Total à Verser", f"{res['Total Amount (Driver Solde)'].sum():,.2f}")
            st.dataframe(res)
            
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine='xlsxwriter') as w:
                res.to_excel(w, index=False)
            st.download_button("Télécharger Excel", b.getvalue(), "Paie_Finale.xlsx")
