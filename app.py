import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Générateur de Paie Yassir", layout="wide")

# ==========================================
# 1. FONCTIONS UTILITAIRES
# ==========================================

def clean_phone(val):
    """Normalise le numéro de téléphone (+212...)."""
    if pd.isna(val) or val == "":
        return ""
    s = str(val).replace(" ", "").replace(".", "").strip()
    # Garder chiffres et +
    s = re.sub(r'[^0-9\+]', '', s)
    
    if s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("212"):
        s = "+" + s
    elif s.startswith("0") and len(s) == 10:
        s = "+212" + s[1:]
    
    # S'assurer qu'il commence par +212 si ce n'est pas le cas
    if not s.startswith("+"):
        s = "+212" + s
        
    return s

def clean_name(val):
    """Nettoie le nom du chauffeur."""
    if pd.isna(val): return ""
    return str(val).lower().strip()

def parse_money(val):
    """Convertit '1 234,56' ou '1234.56' en float."""
    if pd.isna(val) or val == "": return 0.0
    s = str(val).replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def load_data(file):
    """Charge un fichier CSV/Excel avec détection automatique."""
    if file.name.endswith('.xlsx'):
        return pd.read_excel(file)
    try:
        # Essai virgule
        df = pd.read_csv(file)
        if len(df.columns) < 2:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    except:
        return pd.DataFrame()

# ==========================================
# 2. LOGIQUE DE CALCUL
# ==========================================

def generate_report(df_data, df_avance, df_credit):
    
    # --- 1. Préparation Data ---
    # Normalisation des clés pour jointure
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    df_data['name_clean'] = df_data['driver name'].apply(clean_name)
    
    # Filtrer les commandes annulées (On garde Delivered et Returned pour le compte, à ajuster si besoin)
    # Selon l'analyse, il faut exclure "Cancelled" pour que les totaux matchent
    df_valid = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()
    
    # --- 2. Agrégation par Chauffeur ---
    
    # On groupe par téléphone (clé unique)
    groups = df_valid.groupby('phone_clean')
    
    report_rows = []
    
    for phone, group in groups:
        # Infos de base
        driver_name = group['driver name'].iloc[0]
        
        # --- Calculs des Colonnes ---
        
        # Total Orders
        total_orders = len(group)
        
        # Market vs Food (Logique : si restaurant name contient "market" ou services adapté)
        # Note: dans l'exemple Younes, tout était Food. On suppose Market si mot clé trouvé.
        is_market = group['restaurant name'].str.contains('market|shop', case=False, na=False)
        market_orders = is_market.sum()
        food_orders = total_orders - market_orders
        
        # Payment Methods
        is_deferred = group['Payment Method'].astype(str).str.contains('Deferred', case=False, na=False)
        is_payzone = group['Payment Method'].astype(str).str.contains('PAYZONE', case=False, na=False)
        is_cash = group['Payment Method'].astype(str).str.upper() == 'CASH'
        
        deferred_orders = is_deferred.sum()
        payzone_orders = is_payzone.sum()
        
        # Returned
        returned_orders = group['status'].astype(str).str.contains('Returned', case=False).sum()
        
        # Financier
        # Yassir driver payout : Somme totale
        payout = group['driver payout'].sum()
        
        # Yassir amount to restaurant : Somme pour NON-CASH (Payzone/Card)
        # (D'après l'analyse : 121 pour Younes correspondait uniquement à la commande Payzone)
        amt_rest_yassir = group.loc[~is_cash, 'amount to restaurant'].sum()
        
        # Yassir coupon discount : Somme pour CASH
        # (D'après l'analyse : 1553 vs 1613 total, la diff était le Payzone)
        coupon_cash = group.loc[is_cash, 'coupon discount'].sum()
        
        # Bonus
        bonus = group['Bonus Amount'].sum()
        
        # Driver delivery amount (Cash only?)
        # Younes avait 36.686. Payzone était 0. Donc somme totale = somme cash.
        delivery_amt = group.loc[is_cash, 'delivery amount'].sum()
        
        # Driver restaurant commission
        rest_comm = group['restaurant commission'].sum()
        
        # Driver service charge (Cash only)
        # Younes 145 (Cash) vs 150 (Total). Target 145.
        service_charge_cash = group.loc[is_cash, 'service charge'].sum()
        
        # Cash Collected (pour le calcul du solde)
        # On utilise la colonne 'Driver Cash Co'. 
        # Formule déduite : Solde = abs(Sum Cash Co) + Bonus - Avance + Credit
        cash_co_sum = group['Driver Cash Co'].sum()
        
        row = {
            'driver Phone': phone,
            'driver name': driver_name,
            'RIB': '', # À remplir manuellement ou via un fichier tiers
            '3pl driver name': 0, # Par défaut 0
            'Total Orders': total_orders,
            'Yassir Market Orders': market_orders,
            'Food Orders': food_orders,
            'Deferred Orders': deferred_orders,
            'Payzone Orders': payzone_orders,
            'Returned Orders': returned_orders,
            'Yassir driver payout': payout,
            'Yassir amount to restaurant': amt_rest_yassir,
            'Yassir coupon discount': coupon_cash,
            'Payment Guarantee': 0, # Pas de colonne trouvée, 0 par défaut
            'Bonus Value': bonus,
            'Recovered Amount': 0, # Par défaut
            'driver delivery amount': delivery_amt,
            'driver amount to restaurant': 0, # 0 dans l'exemple
            'driver restaurant commission': rest_comm,
            'driver service Charge': service_charge_cash,
            '_abs_cash_co': abs(cash_co_sum) # Intermédiaire pour calcul
        }
        report_rows.append(row)
        
    df_rep = pd.DataFrame(report_rows)
    
    # --- 3. Intégration Avance & Crédit ---
    
    if not df_avance.empty:
        df_avance['phone_clean'] = df_avance[' driver Phone '].apply(clean_phone) # Notez les espaces dans le nom de colonne CSV original
        av_grp = df_avance.groupby('phone_clean')['Avance'].sum()
        df_rep = df_rep.merge(av_grp, left_on='driver Phone', right_index=True, how='left')
        df_rep['Avance'] = df_rep['Avance'].fillna(0)
    else:
        df_rep['Avance'] = 0
        
    if not df_credit.empty:
        df_credit['phone_clean'] = df_credit[' driver Phone '].apply(clean_phone)
        # Nettoyage montant crédit (virgule)
        df_credit['amount_clean'] = df_credit['amount'].astype(str).apply(parse_money)
        cr_grp = df_credit.groupby('phone_clean')['amount_clean'].sum()
        df_rep = df_rep.merge(cr_grp, left_on='driver Phone', right_index=True, how='left')
        df_rep['amount_clean'] = df_rep['amount_clean'].fillna(0)
    else:
        df_rep['amount_clean'] = 0
        
    # --- 4. Calcul Final (Solde) ---
    # Formule : abs(CashCo) + Bonus + Credit - Avance
    # (Note: abs(CashCo) car CashCo est négatif dans Data pour l'argent collecté)
    df_rep['Total Amount (Driver Solde)'] = (
        df_rep['_abs_cash_co'] + 
        df_rep['Bonus Value'] + 
        df_rep['amount_clean'] - 
        df_rep['Avance']
    )
    
    # --- 5. Mise en forme finale ---
    # Renommer pour correspondre EXACTEMENT au fichier cible
    final_cols = {
        'Avance': 'Avance payé',
        'amount_clean': 'Credit Balance'
    }
    df_rep = df_rep.rename(columns=final_cols)
    
    # Ordre des colonnes cible
    target_columns = [
        'driver Phone', 'driver name', 'RIB', '3pl driver name', 'Total Orders', 
        'Yassir Market Orders', 'Food Orders', 'Deferred Orders', 'Payzone Orders', 
        'Returned Orders', 'Yassir driver payout', 'Yassir amount to restaurant', 
        'Yassir coupon discount', 'Payment Guarantee', 'Bonus Value', 'Credit Balance', 
        'Recovered Amount', 'Avance payé', 'driver delivery amount', 
        'driver amount to restaurant', 'driver restaurant commission', 
        'driver service Charge', 'Total Amount (Driver Solde)'
    ]
    
    # Ajouter les colonnes manquantes (ex: RIB) si absentes
    for c in target_columns:
        if c not in df_rep.columns:
            df_rep[c] = 0 if c != 'RIB' else ''
            
    return df_rep[target_columns]

# ==========================================
# 3. INTERFACE STREAMLIT
# ==========================================

st.title("📊 Générateur de Rapport Livreur (Yassir)")

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Fichiers Données")
    f_data = st.file_uploader("Fichier Data (Data 1314...)", type=['csv', 'xlsx'])
    
with col2:
    st.subheader("2. Fichiers Financiers")
    f_avance = st.file_uploader("Fichier Avance", type=['csv', 'xlsx'])
    f_credit = st.file_uploader("Fichier Crédit", type=['csv', 'xlsx'])

if st.button("🚀 Générer le Rapport Calcul", type="primary"):
    if f_data:
        with st.spinner("Calcul en cours..."):
            # Chargement
            df_d = load_data(f_data)
            df_a = load_data(f_avance) if f_avance else pd.DataFrame()
            df_c = load_data(f_credit) if f_credit else pd.DataFrame()
            
            if not df_d.empty:
                # Génération
                df_result = generate_report(df_d, df_a, df_c)
                
                # Aperçu
                st.success(f"Rapport généré pour {len(df_result)} livreurs !")
                st.dataframe(df_result.head())
                
                # Export Excel (meilleur pour les formats chiffres)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_result.to_excel(writer, index=False, sheet_name='Feuille1')
                    
                st.download_button(
                    label="💾 Télécharger le fichier Calcul (Excel)",
                    data=buffer.getvalue(),
                    file_name=f"Calcul_Genere_{datetime.now().strftime('%d_%m_%Y')}.xlsx",
                    mime="application/vnd.ms-excel"
                )
            else:
                st.error("Erreur : Le fichier Data est vide ou illisible.")
    else:
        st.warning("Veuillez charger au moins le fichier Data.")
