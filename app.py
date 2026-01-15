import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Générateur Paie Yassir (Complet)", layout="wide")

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
    """Nettoie le RIB pour l'affichage."""
    if pd.isna(val): return ""
    return str(val).replace(" ", "").strip()

def parse_money(val):
    """Convertit '1 234,56' en float."""
    if pd.isna(val) or val == "": return 0.0
    s = str(val).replace(" ", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def load_data(file):
    """Charge CSV ou Excel intelligemment."""
    if not file: return pd.DataFrame()
    try:
        if file.name.endswith('.xlsx'):
            return pd.read_excel(file)
        # Test CSV virgule puis point-virgule
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

def generate_report(df_data, df_avance, df_credit, df_ribs_supp):
    
    # --- A. Préparation des Données ---
    
    # 1. Nettoyage Data
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    
    # 2. Nettoyage RIBs Supplémentaires
    rib_mapping = {}
    if not df_ribs_supp.empty:
        # Trouver les colonnes automatiquement
        cols = [str(c).lower() for c in df_ribs_supp.columns]
        idx_phone = next((i for i, c in enumerate(cols) if 'phone' in c or 'téléphone' in c), -1)
        idx_rib = next((i for i, c in enumerate(cols) if 'rib' in c), -1)
        
        if idx_phone != -1 and idx_rib != -1:
            col_phone = df_ribs_supp.columns[idx_phone]
            col_rib = df_ribs_supp.columns[idx_rib]
            
            # Créer un dictionnaire {Telephone: RIB}
            df_ribs_supp['temp_phone'] = df_ribs_supp[col_phone].apply(clean_phone)
            rib_mapping = df_ribs_supp.set_index('temp_phone')[col_rib].to_dict()

    # 3. Filtrage (Exclure les Annulées)
    # On garde Delivered et Returned. On exclut 'Cancelled'.
    df_valid = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()
    
    # --- B. Agrégation par Livreur ---
    
    groups = df_valid.groupby('phone_clean')
    report_rows = []
    
    for phone, group in groups:
        driver_name = group['driver name'].iloc[0]
        
        # --- 1. Logique Commandes & Types ---
        total_orders = len(group)
        
        # Détection Market vs Food
        is_market = group['restaurant name'].str.contains('market|shop|carrefour|bim', case=False, na=False)
        market_orders = is_market.sum()
        food_orders = total_orders - market_orders
        
        # Détection Paiements
        payment_methods = group['Payment Method'].astype(str)
        
        # Différé = 'Deferred', 'Corporate', ou parfois vide si spécifié
        is_deferred = payment_methods.str.contains('Deferred|Corporate|Différé', case=False, na=False)
        is_payzone = payment_methods.str.contains('PAYZONE', case=False, na=False)
        # Cash est tout ce qui n'est ni Payzone, ni Différé (simplification, ou check explicite 'CASH')
        is_cash = payment_methods.str.upper().str.strip() == 'CASH'
        
        deferred_orders = is_deferred.sum()
        payzone_orders = is_payzone.sum()
        returned_orders = group['status'].str.contains('Returned', case=False).sum()
        
        # --- 2. Logique Financière ---
        
        payout = group['driver payout'].sum()
        
        # Yassir amount to restaurant : Uniquement pour les commandes NON Cash (Payzone/Deferred)
        # Car en Cash, le livreur paie le resto avec le cash client.
        # En Payzone/Différé, Yassir doit payer le resto, donc c'est comptabilisé ici.
        amt_rest_yassir = group.loc[~is_cash, 'amount to restaurant'].sum()
        
        # Coupon Discount : Uniquement pertinent en CASH (remboursement au livreur)
        coupon_cash = group.loc[is_cash, 'coupon discount'].sum()
        
        bonus = group['Bonus Amount'].sum()
        
        # Delivery Amount & Service Charge (Flux Cash uniquement)
        delivery_amt = group.loc[is_cash, 'delivery amount'].sum()
        service_charge_cash = group.loc[is_cash, 'service charge'].sum()
        rest_comm = group['restaurant commission'].sum()
        
        # Cash Collecté (Valeur absolue pour le calcul)
        cash_co_sum = abs(group['Driver Cash Co'].sum())
        
        # --- 3. Récupération du RIB ---
        # Priorité : Fichier RIB supplémentaire > Fichier Data > Vide
        final_rib = rib_mapping.get(phone, "")
        if not final_rib and 'RIB' in group.columns:
            # Essayer de trouver un RIB non nul dans le groupe
            potential_ribs = group['RIB'].dropna().unique()
            if len(potential_ribs) > 0:
                final_rib = potential_ribs[0]
        
        # Construction de la ligne
        row = {
            'driver Phone': phone,
            'driver name': driver_name,
            'RIB': clean_rib(final_rib),
            '3pl driver name': 0,
            'Total Orders': total_orders,
            'Yassir Market Orders': market_orders,
            'Food Orders': food_orders,
            'Deferred Orders': deferred_orders, # Colonne Paiement Différé
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
            '_abs_cash_co': cash_co_sum # Intermédiaire
        }
        report_rows.append(row)
        
    df_rep = pd.DataFrame(report_rows)
    if df_rep.empty: return pd.DataFrame()

    # --- C. Fusion Avance & Crédit ---
    
    # Fusion Avance
    if not df_avance.empty:
        df_avance['phone_clean'] = df_avance.iloc[:, -1].apply(clean_phone) # Souvent dernière colonne
        # Sécurité : chercher colonne phone par nom si possible
        cols_av = [str(c).lower() for c in df_avance.columns]
        idx_ph = next((i for i,c in enumerate(cols_av) if 'phone' in c), -1)
        if idx_ph != -1:
            df_avance['phone_clean'] = df_avance.iloc[:, idx_ph].apply(clean_phone)
            
        av_grp = df_avance.groupby('phone_clean')['Avance'].sum()
        df_rep = df_rep.merge(av_grp, left_on='driver Phone', right_index=True, how='left').fillna({'Avance': 0})
    else:
        df_rep['Avance'] = 0
        
    # Fusion Crédit
    if not df_credit.empty:
        # Chercher colonne phone
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
    # Solde = CashCollecté (dû par livreur) + Credit - Avance + Bonus - (Gains Payout + Remboursements) ??
    # NON, formule validée précédemment : Solde (ce que Yassir doit au livreur ou inverse)
    # Basé sur ton fichier cible : Solde = |CashCo| + Bonus + Credit - Avance
    
    df_rep['Total Amount (Driver Solde)'] = (
        df_rep['_abs_cash_co'] + 
        df_rep['Bonus Value'] + 
        df_rep['Credit Balance'] - 
        df_rep['Avance']
    )
    
    # Renommage final
    df_rep = df_rep.rename(columns={'Avance': 'Avance payé'})
    
    # Colonnes finales ordonnées
    target_cols = [
        'driver Phone', 'driver name', 'RIB', '3pl driver name', 'Total Orders', 
        'Yassir Market Orders', 'Food Orders', 'Deferred Orders', 'Payzone Orders', 
        'Returned Orders', 'Yassir driver payout', 'Yassir amount to restaurant', 
        'Yassir coupon discount', 'Payment Guarantee', 'Bonus Value', 'Credit Balance', 
        'Recovered Amount', 'Avance payé', 'driver delivery amount', 
        'driver amount to restaurant', 'driver restaurant commission', 
        'driver service Charge', 'Total Amount (Driver Solde)'
    ]
    
    # Compléter colonnes manquantes
    for c in target_cols:
        if c not in df_rep.columns: df_rep[c] = 0
        
    return df_rep[target_cols]

# ==========================================
# 3. INTERFACE
# ==========================================

st.title("📊 Générateur de Rapport Yassir (Avancé)")

with st.expander("ℹ️ Guide d'utilisation", expanded=True):
    st.write("""
    1. Chargez le fichier **Data** (obligatoire).
    2. Chargez les fichiers **Avance** et **Crédit** si disponibles.
    3. Chargez le fichier **RIBs Livreur** pour compléter les RIBs manquants.
    4. Le système détecte automatiquement les commandes **Différées/Corporate** via la colonne 'Payment Method'.
    """)

col1, col2 = st.columns(2)

with col1:
    st.subheader("📁 Fichier Principal")
    f_data = st.file_uploader("Importer Data (CSV/Excel)", type=['csv', 'xlsx'], key="data")
    
    st.subheader("💳 Fichiers Financiers")
    f_avance = st.file_uploader("Importer Avances", type=['csv', 'xlsx'], key="av")
    f_credit = st.file_uploader("Importer Crédits", type=['csv', 'xlsx'], key="cr")

with col2:
    st.subheader("🏦 Données Supplémentaires")
    f_ribs = st.file_uploader("Importer RIBs manquants (Optionnel)", type=['csv', 'xlsx'], key="rib")
    st.caption("Ce fichier doit contenir une colonne Téléphone et une colonne RIB.")

if st.button("🚀 Lancer le Calcul", type="primary"):
    if f_data:
        with st.spinner("Traitement des fichiers en cours..."):
            # Chargement
            df_d = load_data(f_data)
            df_a = load_data(f_avance)
            df_c = load_data(f_credit)
            df_r = load_data(f_ribs)
            
            if not df_d.empty:
                # Génération
                df_res = generate_report(df_d, df_a, df_c, df_r)
                
                st.success(f"Calcul terminé ! {len(df_res)} livreurs traités.")
                
                # Stats rapides
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Total Payout", f"{df_res['Yassir driver payout'].sum():,.2f}")
                col_b.metric("Commandes Différées", int(df_res['Deferred Orders'].sum()))
                col_c.metric("Solde Total", f"{df_res['Total Amount (Driver Solde)'].sum():,.2f}")

                st.dataframe(df_res.head(10))
                
                # Export
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_res.to_excel(writer, index=False, sheet_name='Calcul')
                    # Ajustement largeur colonnes
                    worksheet = writer.sheets['Calcul']
                    worksheet.set_column('A:A', 15) # Tel
                    worksheet.set_column('B:B', 25) # Nom
                    worksheet.set_column('C:C', 30) # RIB
                    
                st.download_button(
                    "💾 Télécharger le rapport Excel",
                    data=buffer.getvalue(),
                    file_name=f"Calcul_Paie_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.ms-excel"
                )
            else:
                st.error("Le fichier Data semble vide.")
    else:
        st.warning("Merci de charger le fichier Data.")
