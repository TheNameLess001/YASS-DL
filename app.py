import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Générateur Paie Yassir (Expert)", layout="wide")

# ==========================================
# 1. FONCTIONS DE NETTOYAGE
# ==========================================

def clean_phone(val):
    """Normalise le téléphone (+212...)."""
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
# 2. MOTEUR DE CALCUL
# ==========================================

def generate_report(df_data, df_avance, df_credit, df_ribs_supp, df_restos_diff):
    
    # --- A. Préparation des Données ---
    
    # 1. Nettoyage Data
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    df_data['resto_clean'] = df_data['restaurant name'].apply(clean_name)
    
    # 2. Préparation Liste Restos Différés
    deferred_restos_set = set()
    if not df_restos_diff.empty:
        # On cherche la colonne qui contient les noms
        # On suppose que c'est la première ou celle qui contient "name"/"nom"
        cols = [str(c).lower() for c in df_restos_diff.columns]
        idx_name = next((i for i, c in enumerate(cols) if 'name' in c or 'nom' in c or 'restaurant' in c), 0)
        
        # On charge tous les noms normalisés dans un SET pour recherche rapide
        col_name = df_restos_diff.columns[idx_name]
        deferred_restos_set = set(df_restos_diff[col_name].apply(clean_name).dropna().unique())

    # 3. Préparation RIBs
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

    # 4. Filtrage (Exclure Cancelled)
    df_valid = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()
    
    # --- B. Agrégation par Livreur ---
    
    groups = df_valid.groupby('phone_clean')
    report_rows = []
    
    for phone, group in groups:
        driver_name = group['driver name'].iloc[0]
        
        # --- LOGIQUE COMMANDES ---
        total_orders = len(group)
        
        # Market vs Food
        is_market = group['restaurant name'].str.contains('market|shop|carrefour|bim', case=False, na=False)
        market_orders = is_market.sum()
        food_orders = total_orders - market_orders
        
        # --- LOGIQUE PAIEMENT & DIFFÉRÉ ---
        
        # 1. Différé via Méthode de Paiement (Corporate/Deferred)
        payment_methods = group['Payment Method'].astype(str)
        is_method_deferred = payment_methods.str.contains('Deferred|Corporate|Différé', case=False, na=False)
        
        # 2. Différé via Fichier Resto
        # On vérifie si le nom du resto est dans notre liste
        is_resto_deferred = group['resto_clean'].isin(deferred_restos_set)
        
        # Une commande est différée si l'un OU l'autre est vrai
        is_globally_deferred = is_method_deferred | is_resto_deferred
        
        # Payzone
        is_payzone = payment_methods.str.contains('PAYZONE', case=False, na=False)
        
        # Cash = Ce qui n'est ni Payzone ni Différé
        # (Si un resto est différé, le livreur ne paie pas, donc ce n'est pas un flux Cash sortant pour lui)
        is_cash = (~is_payzone) & (~is_globally_deferred) & (payment_methods.str.upper().str.strip() == 'CASH')
        
        # Comptes
        deferred_orders = is_globally_deferred.sum()
        payzone_orders = is_payzone.sum()
        returned_orders = group['status'].str.contains('Returned', case=False).sum()
        
        # --- LOGIQUE FINANCIÈRE ---
        
        payout = group['driver payout'].sum()
        
        # Yassir amount to restaurant :
        # On inclut Payzone + TOUS les Différés (Corporate ou via Fichier Resto)
        # Car dans ces cas, le livreur ne paie pas le resto, c'est Yassir qui paie.
        amt_rest_yassir = group.loc[is_payzone | is_globally_deferred, 'amount to restaurant'].sum()
        
        # Coupon Discount : Uniquement pertinent en CASH
        coupon_cash = group.loc[is_cash, 'coupon discount'].sum()
        
        bonus = group['Bonus Amount'].sum()
        
        # Delivery Amount & Service Charge (Flux Cash uniquement)
        delivery_amt = group.loc[is_cash, 'delivery amount'].sum()
        service_charge_cash = group.loc[is_cash, 'service charge'].sum()
        rest_comm = group['restaurant commission'].sum()
        
        # Cash Collecté (Valeur absolue)
        # Note: Le 'Driver Cash Co' du fichier Data intègre déjà normalement la logique du système.
        # Si le système est bien paramétré, il sait que tel resto est différé.
        # On garde la valeur du fichier.
        cash_co_sum = abs(group['Driver Cash Co'].sum())
        
        # Récupération RIB
        final_rib = rib_mapping.get(phone, "")
        if not final_rib and 'RIB' in group.columns:
            potential_ribs = group['RIB'].dropna().unique()
            if len(potential_ribs) > 0: final_rib = potential_ribs[0]
        
        row = {
            'driver Phone': phone,
            'driver name': driver_name,
            'RIB': clean_rib(final_rib),
            '3pl driver name': 0,
            'Total Orders': total_orders,
            'Yassir Market Orders': market_orders,
            'Food Orders': food_orders,
            'Deferred Orders': deferred_orders, # Inclut mtn les restos du fichier
            'Payzone Orders': payzone_orders,
            'Returned Orders': returned_orders,
            'Yassir driver payout': payout,
            'Yassir amount to restaurant': amt_rest_yassir,
            'Yassir coupon discount': coupon_cash,
            'Payment Guarantee': 0,
            'Bonus Value': bonus,
            'Recovered Amount': 0,
            'driver delivery amount': delivery_amt,
            'driver amount to restaurant': 0,
            'driver restaurant commission': rest_comm,
            'driver service Charge': service_charge_cash,
            '_abs_cash_co': cash_co_sum
        }
        report_rows.append(row)
        
    df_rep = pd.DataFrame(report_rows)
    if df_rep.empty: return pd.DataFrame()

    # --- C. Fusion Avance & Crédit ---
    
    if not df_avance.empty:
        # Recherche auto colonne phone
        cols_av = [str(c).lower() for c in df_avance.columns]
        idx_ph = next((i for i,c in enumerate(cols_av) if 'phone' in c), -1)
        if idx_ph != -1:
            df_avance['phone_clean'] = df_avance.iloc[:, idx_ph].apply(clean_phone)
        else:
            df_avance['phone_clean'] = df_avance.iloc[:, -1].apply(clean_phone)
            
        av_grp = df_avance.groupby('phone_clean')['Avance'].sum()
        df_rep = df_rep.merge(av_grp, left_on='driver Phone', right_index=True, how='left').fillna({'Avance': 0})
    else:
        df_rep['Avance'] = 0
        
    if not df_credit.empty:
        cols_cr = [str(c).lower() for c in df_credit.columns]
        idx_ph = next((i for i,c in enumerate(cols_cr) if 'phone' in c), -1)
        col_amt = next((c for c in df_credit.columns if 'amount' in str(c).lower()), None)
        
        if idx_ph != -1 and col_amt:
            df_credit['phone_clean'] = df_credit.iloc[:, idx_ph].apply(clean_phone)
            df_credit['amt_clean'] = df_credit[col_amt].apply(parse_money)
            cr_grp = df_credit.groupby('phone_clean')['amt_clean'].sum()
            df_rep = df_rep.merge(cr_grp, left_on='driver Phone', right_index=True, how='left')
            df_rep = df_rep.rename(columns={'amt_clean': 'Credit Balance'})
            df_rep['Credit Balance'] = df_rep['Credit Balance'].fillna(0)
    else:
         df_rep['Credit Balance'] = 0
    
    if 'Credit Balance' not in df_rep.columns: df_rep['Credit Balance'] = 0

    # --- D. Calcul Solde Final ---
    df_rep['Total Amount (Driver Solde)'] = (
        df_rep['_abs_cash_co'] + 
        df_rep['Bonus Value'] + 
        df_rep['Credit Balance'] - 
        df_rep['Avance']
    )
    
    df_rep = df_rep.rename(columns={'Avance': 'Avance payé'})
    
    target_cols = [
        'driver Phone', 'driver name', 'RIB', '3pl driver name', 'Total Orders', 
        'Yassir Market Orders', 'Food Orders', 'Deferred Orders', 'Payzone Orders', 
        'Returned Orders', 'Yassir driver payout', 'Yassir amount to restaurant', 
        'Yassir coupon discount', 'Payment Guarantee', 'Bonus Value', 'Credit Balance', 
        'Recovered Amount', 'Avance payé', 'driver delivery amount', 
        'driver amount to restaurant', 'driver restaurant commission', 
        'driver service Charge', 'Total Amount (Driver Solde)'
    ]
    
    for c in target_cols:
        if c not in df_rep.columns: df_rep[c] = 0
        
    return df_rep[target_cols]

# ==========================================
# 3. INTERFACE
# ==========================================

st.title("📊 Générateur de Rapport Yassir (Expert)")

st.info("Ce module gère : Data, Avances, Crédits, RIBs manquants et Restaurants Différés.")

col1, col2 = st.columns(2)

with col1:
    st.subheader("📁 Données Principales")
    f_data = st.file_uploader("1. Fichier Data (CSV/Excel)", type=['csv', 'xlsx'], key="data")
    
    st.subheader("💳 Finances Livreur")
    f_avance = st.file_uploader("2. Fichier Avances", type=['csv', 'xlsx'], key="av")
    f_credit = st.file_uploader("3. Fichier Crédits", type=['csv', 'xlsx'], key="cr")

with col2:
    st.subheader("⚙️ Configuration")
    f_restos = st.file_uploader("4. Liste Restos Différés (Important)", type=['csv', 'xlsx'], key="resto")
    f_ribs = st.file_uploader("5. Fichier RIBs (Optionnel)", type=['csv', 'xlsx'], key="rib")

if st.button("🚀 Lancer le Calcul", type="primary"):
    if f_data:
        with st.spinner("Analyse croisée des 5 fichiers..."):
            # Chargement
            df_d = load_data(f_data)
            df_a = load_data(f_avance)
            df_c = load_data(f_credit)
            df_r = load_data(f_ribs)
            df_rest = load_data(f_restos)
            
            if not df_d.empty:
                # Génération
                df_res = generate_report(df_d, df_a, df_c, df_r, df_rest)
                
                st.success(f"Traitement terminé : {len(df_res)} livreurs.")
                
                # KPIs
                c1, c2, c3 = st.columns(3)
                c1.metric("Commandes Différées", int(df_res['Deferred Orders'].sum()))
                c2.metric("Montant Resto (Yassir)", f"{df_res['Yassir amount to restaurant'].sum():,.2f}")
                c3.metric("Solde à Payer", f"{df_res['Total Amount (Driver Solde)'].sum():,.2f}")

                st.dataframe(df_res.head())
                
                # Export
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_res.to_excel(writer, index=False, sheet_name='Calcul')
                    worksheet = writer.sheets['Calcul']
                    worksheet.set_column('A:A', 15)
                    worksheet.set_column('B:B', 25)
                    worksheet.set_column('C:C', 30)
                    
                st.download_button(
                    "💾 Télécharger le rapport complet",
                    data=buffer.getvalue(),
                    file_name=f"Rapport_Paie_Yassir_{datetime.now().strftime('%d%m%Y')}.xlsx",
                    mime="application/vnd.ms-excel"
                )
            else:
                st.error("Fichier Data vide.")
    else:
        st.warning("Le fichier Data est requis.")
