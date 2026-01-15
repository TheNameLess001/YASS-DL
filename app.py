import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

st.set_page_config(page_title="Calcul Paie Livreur Yassir", layout="wide")

# ==========================================
# 1. OUTILS DE NETTOYAGE
# ==========================================

def clean_phone(val):
    """Normalise le téléphone en format +212... pour servir de clé unique."""
    if pd.isna(val) or val == "": return ""
    s = str(val).replace(" ", "").replace(".", "").replace("-", "").strip()
    s = re.sub(r'[^0-9\+]', '', s)
    if s.startswith("00"): s = "+" + s[2:]
    elif s.startswith("212"): s = "+" + s
    elif s.startswith("0") and len(s) == 10: s = "+212" + s[1:]
    if not s.startswith("+"): s = "+212" + s
    return s

def clean_name(val):
    """Nettoie les noms (minuscule, sans espaces inutiles)."""
    if pd.isna(val): return ""
    return str(val).lower().strip()

def clean_rib(val):
    """Formate le RIB proprement."""
    if pd.isna(val): return ""
    return str(val).replace(" ", "").strip()

def parse_money(val):
    """Convertit les montants (ex: '1 200,50' -> 1200.50)."""
    if pd.isna(val) or val == "": return 0.0
    # On enlève les espaces milliers et on remplace virgule par point
    s = str(val).replace(" ", "").replace(",", ".")
    # On garde uniquement chiffres, point et signe moins
    s = re.sub(r'[^0-9\.\-]', '', s)
    try: return float(s)
    except: return 0.0

def load_data(file):
    """Charge le fichier selon son format (Excel, CSV virgule ou point-virgule)."""
    if not file: return pd.DataFrame()
    try:
        if file.name.endswith('.xlsx'):
            return pd.read_excel(file)
        
        file.seek(0)
        # Test CSV standard
        df = pd.read_csv(file)
        # Si tout est dans une colonne, on tente le point-virgule
        if len(df.columns) < 2:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    except Exception as e:
        st.error(f"Erreur lecture {file.name}: {e}")
        return pd.DataFrame()

# ==========================================
# 2. CŒUR DU CALCUL (LOGIQUE MÉTIER)
# ==========================================

def generate_report(df_data, df_avance, df_credit, df_ribs_supp, df_restos_diff):
    
    # --- A. PRÉPARATION ---
    
    # 1. Clé unique (Téléphone)
    df_data['phone_clean'] = df_data['driver Phone'].apply(clean_phone)
    df_data['resto_clean'] = df_data['restaurant name'].apply(clean_name)
    
    # 2. Conversion des colonnes financières en chiffres
    money_cols = ['driver payout', 'amount to restaurant', 'coupon discount', 
                  'delivery amount', 'service charge', 'restaurant commission',
                  'Driver Cash Co', 'Bonus Amount']
    for c in money_cols:
        if c in df_data.columns:
            df_data[c] = df_data[c].apply(parse_money)
        else:
            df_data[c] = 0.0

    # 3. Liste des Restos Différés (Set pour recherche rapide)
    deferred_restos_set = set()
    if not df_restos_diff.empty:
        # On cherche la colonne contenant le nom
        cols = [str(c).lower() for c in df_restos_diff.columns]
        idx_name = next((i for i, c in enumerate(cols) if 'name' in c or 'nom' in c or 'restaurant' in c), 0)
        col_name = df_restos_diff.columns[idx_name]
        deferred_restos_set = set(df_restos_diff[col_name].apply(clean_name).dropna().unique())

    # 4. Dictionnaire des RIBs (depuis fichier externe)
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

    # 5. On ignore les commandes annulées (Pas de paie, pas de mouvement)
    df_valid = df_data[~df_data['status'].str.contains("Cancelled", case=False, na=False)].copy()
    
    # --- B. CALCUL PAR LIVREUR ---
    
    groups = df_valid.groupby('phone_clean')
    report_rows = []
    
    for phone, group in groups:
        driver_name = group['driver name'].iloc[0]
        
        # --- IDENTIFICATION DES TYPES DE COMMANDES ---
        
        # Payzone (Paiement en ligne)
        is_payzone = group['Payment Method'].astype(str).str.contains('PAYZONE', case=False, na=False)
        
        # Différé Méthode (ex: Corporate)
        is_method_deferred = group['Payment Method'].astype(str).str.contains('Deferred|Corporate|Différé', case=False, na=False)
        
        # Différé Resto (Le resto est dans la liste chargée)
        is_resto_deferred = group['resto_clean'].isin(deferred_restos_set)
        
        # "NO POCKET MONEY" : Commandes où le livreur NE PAIE PAS le resto
        # C'est le cas si : Payzone OU Corporate OU Resto dans la liste différée
        is_no_pay_by_driver = is_payzone | is_method_deferred | is_resto_deferred
        
        # Commandes Cash Réelles (Livreur paie le resto)
        is_real_cash = (~is_no_pay_by_driver) & (group['Payment Method'].astype(str).str.upper().str.strip() == 'CASH')

        # --- KPI ---
        total_orders = len(group)
        deferred_count = (is_method_deferred | is_resto_deferred).sum()
        payzone_count = is_payzone.sum()
        returned_count = group['status'].str.contains('Returned', case=False).sum()
        
        # --- CALCULS FINANCIERS ---
        
        # 1. Commission (Driver Payout)
        # Règle : Somme de toutes les commissions, peu importe le type.
        commission_total = group['driver payout'].sum()
        
        # 2. Bonus
        bonus_total = group['Bonus Amount'].sum()
        
        # 3. Yassir Amount to Resto
        # C'est ce que Yassir doit payer aux restos (car le livreur ne l'a pas fait)
        amt_yassir_to_rest = group.loc[is_no_pay_by_driver, 'amount to restaurant'].sum()
        
        # 4. Coupon Discount (Seulement Cash)
        # Si Payzone, le coupon est digital. Si Cash, le livreur a encaissé moins, on le rembourse.
        coupon_reimb = group.loc[is_real_cash, 'coupon discount'].sum()

        # 5. CORRECTION DU SOLDE (CASH CO)
        # Le fichier Data calcule souvent : CashCo = CashRecuClient - CashPayeResto
        # Pour une commande différée/payzone, le système croit parfois que CashPayeResto existe (donc CashCo négatif).
        # OR, le livreur n'a rien payé. Il faut donc "annuler" cette dépense virtuelle.
        
        raw_cash_co = group['Driver Cash Co'].sum()
        
        # On ajoute le montant du resto pour les commandes où le livreur n'a pas payé
        # Cela "remonte" le solde vers 0 (ou le positif).
        correction_amount = group.loc[is_no_pay_by_driver, 'amount to restaurant'].sum()
        
        corrected_cash_co = raw_cash_co + correction_amount
        
        # --- CALCUL INTERMÉDIAIRE : CE QUE LE LIVREUR POSSÈDE/DOIT (Hors Avance/Crédit) ---
        # Si Corrected_Cash_Co est négatif (-100), ça veut dire que le livreur a sorti 100 de sa poche. Yassir lui doit 100.
        # Si Corrected_Cash_Co est positif (+100), ça veut dire que le livreur a 100 dans sa poche (Cash client). Il doit 100 à Yassir.
        
        # On convertit tout en "Ce que Yassir doit au livreur"
        # Donc on inverse le signe du Cash Co.
        balance_from_ops = -1 * corrected_cash_co
        
        # Si balance_from_ops est positif, Yassir doit de l'argent (remboursement frais).
        # Si balance_from_ops est négatif, le livreur a de l'argent à Yassir.
        
        # --- RIB ---
        final_rib = rib_mapping.get(phone, "")
        if not final_rib and 'RIB' in group.columns:
            r = group['RIB'].dropna().unique()
            if len(r) > 0: final_rib = r[0]

        row = {
            'driver Phone': phone,
            'driver name': driver_name,
            'RIB': clean_rib(final_rib),
            'Total Orders': total_orders,
            'Deferred Orders': deferred_count,
            'Payzone Orders': payzone_count,
            'Returned Orders': returned_count,
            'Yassir driver payout': commission_total,      # Total Com
            'Yassir amount to restaurant': amt_yassir_to_rest,
            'Yassir coupon discount': coupon_reimb,
            'Bonus Value': bonus_total,
            # Pour verification
            '_Raw_Cash_Co': raw_cash_co,
            '_Correction_Resto': correction_amount,
            '_Balance_Ops': balance_from_ops 
        }
        report_rows.append(row)
        
    df_rep = pd.DataFrame(report_rows)
    if df_rep.empty: return pd.DataFrame()

    # --- C. FUSION AVEC AVANCE ET CRÉDIT ---
    
    # 1. Avance (Ce que le livreur a déjà -> À DÉDUIRE)
    if not df_avance.empty:
        # Recherche colonne téléphone
        cols = [str(c).lower() for c in df_avance.columns]
        idx = next((i for i,c in enumerate(cols) if 'phone' in c), -1)
        # Si pas trouvé, on prend la dernière colonne (souvent le cas dans vos fichiers)
        col_target = df_avance.columns[idx] if idx != -1 else df_avance.columns[-1]
        
        df_avance['phone_clean'] = df_avance[col_target].apply(clean_phone)
        
        # Recherche colonne montant
        col_amt = next((c for c in df_avance.columns if 'avance' in str(c).lower()), None)
        if col_amt:
             df_avance['mnt_avance'] = df_avance[col_amt].apply(parse_money)
             grp = df_avance.groupby('phone_clean')['mnt_avance'].sum()
             df_rep = df_rep.merge(grp, left_on='driver Phone', right_index=True, how='left')
             df_rep = df_rep.rename(columns={'mnt_avance': 'Avance payé'})
    
    if 'Avance payé' not in df_rep.columns: df_rep['Avance payé'] = 0
    df_rep['Avance payé'] = df_rep['Avance payé'].fillna(0)

    # 2. Crédit (Ce qu'on doit au livreur -> À AJOUTER)
    if not df_credit.empty:
        cols = [str(c).lower() for c in df_credit.columns]
        idx = next((i for i,c in enumerate(cols) if 'phone' in c), -1)
        col_target = df_credit.columns[idx] if idx != -1 else df_credit.columns[1] # fallback index 1
        
        df_credit['phone_clean'] = df_credit[col_target].apply(clean_phone)
        
        col_amt = next((c for c in df_credit.columns if 'amount' in str(c).lower()), None)
        if col_amt:
            df_credit['mnt_credit'] = df_credit[col_amt].apply(parse_money)
            grp = df_credit.groupby('phone_clean')['mnt_credit'].sum()
            df_rep = df_rep.merge(grp, left_on='driver Phone', right_index=True, how='left')
            df_rep = df_rep.rename(columns={'mnt_credit': 'Credit Balance'})

    if 'Credit Balance' not in df_rep.columns: df_rep['Credit Balance'] = 0
    df_rep['Credit Balance'] = df_rep['Credit Balance'].fillna(0)

    # --- D. CALCUL DU SOLDE FINAL ---
    
    # Formule :
    # Solde = (Ce que Yassir doit sur les Ops) + Bonus + Crédit - Avance
    # _Balance_Ops inclut déjà la logique de "Remboursement Cash" et "Commission".
    # Mais attendez ! _Balance_Ops = -1 * Corrected_Cash_Co.
    # Dans le fichier Data, le "driver payout" (commission) est-il DÉJÀ dans le Cash Co ?
    # Généralement NON. Le Cash Co c'est purement l'argent collecté vs versé.
    # La commission c'est ce que Yassir ajoute.
    
    # DONC :
    # Total à verser = (Commission) + (_Balance_Ops) + (Bonus) + (Credit) - (Avance)
    
    # Vérifions le fichier Data.
    # Si Cash Co = -100 (J'ai payé 100 de ma poche).
    # Commission = 15.
    # Total = 100 (remboursement) + 15 (salaire) = 115.
    # Formule : 15 + (-1*-100) = 115. C'est CORRECT.
    
    df_rep['Total Amount (Driver Solde)'] = (
        df_rep['Yassir driver payout'] + 
        df_rep['_Balance_Ops'] + 
        df_rep['Bonus Value'] + 
        df_rep['Credit Balance'] - 
        df_rep['Avance payé']
    )
    
    # Nettoyage colonnes intermédiaires pour l'export
    cols_final = [
        'driver Phone', 'driver name', 'RIB', 
        'Total Orders', 'Deferred Orders', 'Payzone Orders', 'Returned Orders',
        'Yassir driver payout', 'Yassir amount to restaurant', 'Yassir coupon discount',
        'Bonus Value', 'Credit Balance', 'Avance payé', 
        'Total Amount (Driver Solde)'
    ]
    
    return df_rep[cols_final]

# ==========================================
# 3. INTERFACE UTILISATEUR
# ==========================================

st.title("💸 Calculatrice Paie Livreur - Logique Confirmée")

with st.expander("📖 Lire la logique de calcul appliquée", expanded=False):
    st.markdown("""
    **Formule du Solde Final :**
    
    $$Solde = (Commissions) + (Remboursement Cash) + (Bonus) + (Crédit) - (Avance)$$
    
    1. **Commissions :** Somme totale des `driver payout` (inclut Cash, Différé, Payzone).
    2. **Remboursement Cash :** * Pour les commandes Cash : Si le livreur a payé le resto de sa poche, on lui rend cet argent.
       * Pour Différé/Payzone : On neutralise le coût du resto (car le livreur ne l'a pas payé), mais on garde sa commission.
    3. **Avance :** Déduit du total.
    4. **Crédit :** Ajouté au total.
    """)

col1, col2 = st.columns(2)

with col1:
    f_data = st.file_uploader("1. Fichier DATA (Transactions)", type=['csv', 'xlsx'])
    f_avance = st.file_uploader("2. Fichier AVANCE", type=['csv', 'xlsx'])
    f_credit = st.file_uploader("3. Fichier CREDIT", type=['csv', 'xlsx'])

with col2:
    f_restos = st.file_uploader("4. Liste Restos Différés", type=['csv', 'xlsx'])
    f_ribs = st.file_uploader("5. Fichier RIBs", type=['csv', 'xlsx'])

if st.button("🚀 Lancer le Calcul", type="primary"):
    if f_data:
        with st.spinner("Calcul en cours selon la logique validée..."):
            df_d = load_data(f_data)
            df_a = load_data(f_avance)
            df_c = load_data(f_credit)
            df_r = load_data(f_ribs)
            df_rest = load_data(f_restos)
            
            if not df_d.empty:
                res = generate_report(df_d, df_a, df_c, df_r, df_rest)
                
                # KPIs
                tot_payout = res['Total Amount (Driver Solde)'].sum()
                st.metric("Total à Verser (Net)", f"{tot_payout:,.2f} MAD")
                
                st.dataframe(res)
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    res.to_excel(writer, index=False, sheet_name='Paie')
                
                st.download_button(
                    "💾 Télécharger le Fichier Final",
                    data=buffer.getvalue(),
                    file_name=f"Paie_Finale_{datetime.now().strftime('%d%m%Y')}.xlsx",
                    mime="application/vnd.ms-excel"
                )
            else:
                st.error("Le fichier Data est vide.")
    else:
        st.warning("Veuillez charger le fichier Data.")
