import streamlit as st
import pandas as pd
import numpy as np
import unicodedata
import re
from datetime import datetime, timedelta
import io

# Configuration de la page
st.set_page_config(page_title="Importateur Yassir - Streamlit", layout="wide")

# ==========================================
# 1. FONCTIONS DE NORMALISATION (UTILS)
# ==========================================

def normalize_label(s):
    """Normalise une chaîne (minuscule, sans accents, trim)."""
    if pd.isna(s) or s == "":
        return ""
    s = str(s).lower().strip()
    # Gestion des espaces multiples et caractères invisibles
    s = unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode("utf-8")
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return s

def normalize_compact(s):
    """Comme normalize_label mais supprime tous les espaces/symboles."""
    s = normalize_label(s)
    return re.sub(r'[^a-z0-9]', '', s)

def normalize_key(s):
    """Normalise les clés (ID commande, Transaction) pour comparaison."""
    if pd.isna(s): return ""
    s = str(s).strip()
    # Enlève .0 à la fin si c'est un float converti en string
    s = re.sub(r'\.0+$', '', s)
    return normalize_compact(s)

def normalize_driver_phone(val, default_indicatif="+212"):
    """Formatage téléphone driver (+212...)."""
    if pd.isna(val) or val == "":
        return ""
    val = str(val).strip()
    num = re.sub(r'[^\d+]', '', val)
    
    if num.startswith("00"):
        num = "+" + num[2:]
    
    if num.startswith("212"):
        if not num.startswith("+"): num = "+" + num
        return num
        
    if not num.startswith("+"):
        # Enlève le premier 0
        num = re.sub(r'^0+', '', num)
        num = default_indicatif + num
        
    return num.replace("+00", "+")

def normalize_driver_name(name):
    """Formatage nom driver."""
    if pd.isna(name): return ""
    return str(name).lower().strip()

def parse_date_custom(val, is_transaction=False):
    """Tente de parser la date."""
    if pd.isna(val) or val == "":
        return pd.NaT
    
    if isinstance(val, (pd.Timestamp, datetime)):
        dt = val
    else:
        try:
            # Essaie format jour/mois/année (courant dans vos fichiers)
            dt = pd.to_datetime(val, dayfirst=True, errors='coerce')
        except:
            return pd.NaT

    if pd.isna(dt): return pd.NaT

    # Logique métier : si commande < 3h du matin, recule d'un jour
    if not is_transaction:
        if dt.hour < 3 and dt.hour >= 0:
            dt = dt - timedelta(days=1)
            
    return dt

def clean_money_value(val):
    """Convertit '27,558' ou '100' en float."""
    if pd.isna(val) or val == "":
        return 0.0
    s = str(val).replace(',', '.').strip()
    s = re.sub(r'[^0-9\.\-]', '', s) # Garde chiffres, point et signe moins
    try:
        return float(s)
    except:
        return 0.0

def load_file_auto_separator(uploaded_file):
    """Charge un CSV en détectant le séparateur (; ou ,)."""
    try:
        # Si c'est un Excel
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file)
        
        # Si c'est un CSV, on lit les premières lignes pour deviner
        content = uploaded_file.getvalue().decode('utf-8', errors='ignore')
        first_line = content.split('\n')[0]
        
        sep = ','
        if first_line.count(';') > first_line.count(','):
            sep = ';'
            
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=sep)
    except Exception as e:
        st.error(f"Erreur lecture {uploaded_file.name}: {e}")
        return pd.DataFrame()

# ==========================================
# 2. LOGIQUE MÉTIER
# ==========================================

def get_commission_map(df_commission):
    if df_commission is None or df_commission.empty:
        return {}
    cols = [normalize_label(c) for c in df_commission.columns]
    idx_restau = next((i for i, c in enumerate(cols) if any(x in c for x in ['restaurant', 'restau'])), -1)
    
    if idx_restau == -1: return {}
    
    # Mapping colonnes optionnelles
    idx_var = next((i for i, c in enumerate(cols) if any(x in c for x in ['variable', 'commission'])), -1)
    idx_fixe = next((i for i, c in enumerate(cols) if any(x in c for x in ['fixe'])), -1)
    idx_loc = next((i for i, c in enumerate(cols) if any(x in c for x in ['location', 'adresse', 'zone'])), -1)

    mapping = {}
    for row in df_commission.itertuples(index=False):
        raw_name = str(row[idx_restau]) if pd.notna(row[idx_restau]) else ""
        clean_name = raw_name.lower().strip()
        if not clean_name: continue

        var_val = 0.0
        if idx_var != -1 and pd.notna(row[idx_var]):
            try:
                v = str(row[idx_var]).replace('%', '').replace(',', '.')
                vf = float(v)
                var_val = vf / 100 if vf > 1 else vf
            except: pass
            
        fixe_val = 0.0
        if idx_fixe != -1 and pd.notna(row[idx_fixe]):
            try:
                f = str(row[idx_fixe]).replace(',', '.')
                f = re.sub(r'[^0-9\.-]', '', f)
                fixe_val = float(f)
            except: pass
            
        loc_val = str(row[idx_loc]).strip() if idx_loc != -1 and pd.notna(row[idx_loc]) else ""
        mapping[clean_name] = {'variable': var_val, 'fixe': fixe_val, 'location': loc_val}
    return mapping

def identify_header_type(columns):
    """Détermine si c'est une Commande ou une Transaction."""
    norm_cols = [normalize_label(c) for c in columns]
    compact_cols = [normalize_compact(c) for c in columns]
    
    for i, c in enumerate(norm_cols):
        # Commande
        if re.search(r'^n(o|um(ero)?)?\s*commande(\s*yassir)?$', c) or \
           c == "order id" or compact_cols[i] == "orderid":
            return "commande", columns[i]
            
        # Transaction (id_transaction, transaction id, etc)
        if "id transaction" in c or compact_cols[i] in ["idtransaction", "transactionid"]:
            return "transaction", columns[i]
            
    return None, None

# ==========================================
# 3. IMPORTATION
# ==========================================

def process_import(df_main, files_to_import, commission_map):
    log_report = []
    new_rows = []
    
    # 1. Repérage des colonnes clés dans Data
    main_cols_norm = [normalize_label(c) for c in df_main.columns]
    
    existing_commands = set()
    existing_transactions = set()
    
    # Chercher la colonne Order ID
    idx_cmd = next((i for i, c in enumerate(main_cols_norm) if c in ["order id", "n commande"]), -1)
    if idx_cmd != -1:
        existing_commands = set(df_main.iloc[:, idx_cmd].dropna().astype(str).apply(normalize_key))

    # Chercher la colonne Transaction ID
    idx_trans = next((i for i, c in enumerate(main_cols_norm) if c in ["transaction id", "id transaction"]), -1)
    if idx_trans != -1:
        existing_transactions = set(df_main.iloc[:, idx_trans].dropna().astype(str).apply(normalize_key))

    # 2. Colonnes requises (ajout si manquantes)
    REQUIRED_COLS = [
        "Commission", "Variable %", "Fixe (DT)", "Location", "amount", 
        "Transaction ID", "Monnaie", "Avance", "Nom du fichier", 
        "Date d'import", "Statut", "Utilisateur", "driver Phone", 
        "Annulée?", "Status", "driver name", "RIB", "Date"
    ]
    for col in REQUIRED_COLS:
        if col not in df_main.columns:
            df_main[col] = pd.NA

    progress_bar = st.progress(0)
    
    for idx_file, uploaded_file in enumerate(files_to_import):
        filename = uploaded_file.name
        temp_df = load_file_auto_separator(uploaded_file)
            
        if temp_df.empty or len(temp_df) < 1:
            log_report.append(f"⚠️ {filename} : Vide ou illisible")
            continue

        type_file, key_col_name = identify_header_type(temp_df.columns)
        
        if not type_file:
            log_report.append(f"⚠️ {filename} : Ignoré (Pas de colonne ID trouvée)")
            continue
            
        imported_count = 0
        skipped_count = 0
        
        # Mapping des colonnes sources
        # On pré-calcule les index des colonnes utiles dans le fichier source
        cols_src_norm = [normalize_label(c) for c in temp_df.columns]
        
        # Helper pour trouver le nom exact de la colonne source
        def find_src_col(patterns):
            for i, c_norm in enumerate(cols_src_norm):
                if c_norm in patterns: return temp_df.columns[i]
                for p in patterns:
                    if p in c_norm: return temp_df.columns[i]
            return None

        col_montant = find_src_col(["montant", "amount", "total"])
        col_remise = find_src_col(["remise", "discount"])
        col_restau = find_src_col(["restaurant name", "restau"])
        col_avance = find_src_col(["avance"]) # Spécifique fichier Avance
        col_phone_src = find_src_col(["driver phone", "cin"])
        col_date_src = find_src_col(["issue date", "issue_date", "date"])
        
        for _, row in temp_df.iterrows():
            raw_id = row[key_col_name]
            norm_id = normalize_key(raw_id)
            
            if not norm_id:
                skipped_count += 1
                continue
                
            # Dédoublonnage
            if type_file == "commande" and norm_id in existing_commands:
                skipped_count += 1
                continue
            if type_file == "transaction" and norm_id in existing_transactions:
                skipped_count += 1
                continue
                
            # --- Construction de la nouvelle ligne ---
            new_row = {col: pd.NA for col in df_main.columns}
            
            # 1. Copie générique des données (mapping par nom approximatif)
            for src_col in temp_df.columns:
                norm_src = normalize_label(src_col)
                # On cherche si ce nom existe dans df_main
                for main_col in df_main.columns:
                    if normalize_label(main_col) == norm_src:
                        new_row[main_col] = row[src_col]

            # 2. Mappings spécifiques forcés
            # ID
            if type_file == "commande":
                new_row["order id"] = raw_id # Supposant que "order id" existe
            else:
                new_row["Transaction ID"] = raw_id
            
            # Date
            if col_date_src:
                val_date = parse_date_custom(row[col_date_src], is_transaction=(type_file=="transaction"))
                if pd.notna(val_date):
                    new_row["Date"] = val_date
                    if "order day" in df_main.columns and type_file == "transaction":
                        new_row["order day"] = val_date

            # Driver Phone
            if col_phone_src:
                new_row["driver Phone"] = normalize_driver_phone(row[col_phone_src])
                
            # Avance (Spécifique fichier Avance)
            if col_avance:
                new_row["Avance"] = clean_money_value(row[col_avance])
                
            # Amount / Montant (Spécifique fichier Credit)
            if col_montant:
                new_row["amount"] = clean_money_value(row[col_montant])

            # Meta-données
            new_row["Nom du fichier"] = filename
            new_row["Date d'import"] = datetime.now()
            new_row["Statut"] = "Importé"
            
            if type_file == "transaction":
                new_row["Annulée?"] = "Delivered"
                new_row["Status"] = "Delivered"

            # 3. Calculs Commission (seulement si Resto présent)
            if col_restau and pd.notna(row[col_restau]):
                restau_name = str(row[col_restau]).lower().strip()
                com_info = commission_map.get(restau_name, {'variable': 0, 'fixe': 0, 'location': ""})
                
                # Montant pour commission
                m = clean_money_value(row[col_montant]) if col_montant else 0.0
                r = clean_money_value(row[col_remise]) if col_remise else 0.0
                net = m - r
                
                new_row["Commission"] = net * com_info['variable']
                new_row["Variable %"] = com_info['variable'] * 100
                new_row["Fixe (DT)"] = com_info['fixe']
                new_row["Location"] = com_info['location']

            # Mise à jour des sets de contrôle
            if type_file == "commande": existing_commands.add(norm_id)
            if type_file == "transaction": existing_transactions.add(norm_id)
            
            new_rows.append(new_row)
            imported_count += 1

        log_report.append(f"✅ {filename} : +{imported_count} lignes / ⛔ {skipped_count} doublons")
        progress_bar.progress((idx_file + 1) / len(files_to_import))

    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df_concat = pd.concat([df_main, df_new], ignore_index=True)
    else:
        df_concat = df_main

    return df_concat, log_report

# ==========================================
# 4. INTERFACE
# ==========================================

st.title("🚀 Importateur Yassir (Avance/Crédit/Data)")

st.sidebar.header("📁 Fichiers Maîtres")
uploaded_data = st.sidebar.file_uploader("1. Fichier Data (Ex: Data 1314012026.csv)", type=['xlsx', 'csv'])
uploaded_comm = st.sidebar.file_uploader("2. Fichier Commission (Optionnel)", type=['xlsx', 'csv'])

if 'main_df' not in st.session_state: st.session_state['main_df'] = pd.DataFrame()
if 'comm_map' not in st.session_state: st.session_state['comm_map'] = {}

if st.sidebar.button("Charger les Maîtres"):
    if uploaded_data:
        st.session_state['main_df'] = load_file_auto_separator(uploaded_data)
        st.sidebar.success(f"Data chargée : {len(st.session_state['main_df'])} lignes")
    if uploaded_comm:
        st.session_state['comm_map'] = get_commission_map(load_file_auto_separator(uploaded_comm))
        st.sidebar.success(f"Commissions chargées")

st.divider()

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("📥 Importer Avances & Crédits")
    st.markdown("Sélectionnez `Avance Livreur...` et `Credit Livreur...` ici.")
    new_files = st.file_uploader("Fichiers à traiter", accept_multiple_files=True)

    if st.button("🚀 Lancer l'import", type="primary"):
        if st.session_state['main_df'].empty:
            st.error("Chargez d'abord le fichier 'Data' dans la barre latérale gauche !")
        elif not new_files:
            st.warning("Aucun fichier sélectionné.")
        else:
            with st.spinner("Analyse et fusion..."):
                updated_df, report = process_import(
                    st.session_state['main_df'], 
                    new_files, 
                    st.session_state['comm_map']
                )
                st.session_state['main_df'] = updated_df
                st.success("Terminé !")
                st.info("\n".join(report))

with col2:
    st.subheader("Téléchargement")
    if not st.session_state['main_df'].empty:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            st.session_state['main_df'].to_excel(writer, index=False, sheet_name='Data')
        
        st.download_button(
            label="💾 Télécharger Data mise à jour",
            data=buffer.getvalue(),
            file_name=f"Data_Updated_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.ms-excel"
        )
