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
        num = "+" + num
        
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
    """
    Tente de parser la date.
    Logique GAS repliquée : si heure < 3h du matin pour une commande, on recule d'un jour.
    """
    if pd.isna(val) or val == "":
        return pd.NaT
    
    # Si c'est déjà un objet datetime pandas/python
    if isinstance(val, (pd.Timestamp, datetime)):
        dt = val
    else:
        # Essai de conversion générique
        try:
            dt = pd.to_datetime(val, dayfirst=True) # dayfirst pour gérer le format FR courant
        except:
            return pd.NaT

    if pd.isna(dt): return pd.NaT

    # Logique métier spécifique au script original pour les commandes
    if not is_transaction:
        if dt.hour < 3:
            dt = dt - timedelta(days=1)
            
    return dt

# ==========================================
# 2. LOGIQUE MÉTIER (COMMISSION & HEADERS)
# ==========================================

def get_commission_map(df_commission):
    """Crée un dictionnaire {nom_restaurant: {variable, fixe, location}}."""
    if df_commission is None or df_commission.empty:
        return {}
    
    cols = [normalize_label(c) for c in df_commission.columns]
    
    # Identification des colonnes
    idx_restau = next((i for i, c in enumerate(cols) if any(x in c for x in ['restaurant', 'restau'])), -1)
    idx_var = next((i for i, c in enumerate(cols) if any(x in c for x in ['variable', 'commission'])), -1)
    idx_fixe = next((i for i, c in enumerate(cols) if any(x in c for x in ['fixe'])), -1)
    idx_loc = next((i for i, c in enumerate(cols) if any(x in c for x in ['location', 'adresse', 'zone', 'ville'])), -1)

    mapping = {}
    if idx_restau == -1:
        st.error("Colonne 'Restaurant' introuvable dans le fichier Commission.")
        return {}

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
                f = str(row[idx_fixe]).replace(',', '.') # nettoyage basique
                f = re.sub(r'[^0-9\.-]', '', f)
                fixe_val = float(f)
            except: pass
            
        loc_val = ""
        if idx_loc != -1 and pd.notna(row[idx_loc]):
            loc_val = str(row[idx_loc]).strip()

        mapping[clean_name] = {'variable': var_val, 'fixe': fixe_val, 'location': loc_val}
    
    return mapping

def identify_header_type(columns):
    """Détermine si c'est une Commande ou une Transaction."""
    norm_cols = [normalize_label(c) for c in columns]
    compact_cols = [normalize_compact(c) for c in columns]
    
    for i, c in enumerate(norm_cols):
        # Commande check
        if re.search(r'^n(o|um(ero)?)?\s*commande(\s*yassir)?$', c) or \
           c == "order id" or compact_cols[i] == "orderid":
            return "commande", columns[i]
            
        # Transaction check
        if c == "id transaction" or compact_cols[i] == "idtransaction":
            return "transaction", columns[i]
            
    return None, None

# ==========================================
# 3. FONCTIONS PRINCIPALES (IMPORT & UPDATE)
# ==========================================

def process_import(df_main, files_to_import, commission_map, df_livreur=None):
    """Traite l'importation des fichiers."""
    
    log_report = []
    new_rows = []
    
    # 1. Création des sets de clés existantes pour dédoublonnage
    existing_commands = set()
    existing_transactions = set()
    
    # Repérage des colonnes clés dans le fichier maitre
    main_cols_norm = [normalize_label(c) for c in df_main.columns]
    
    # Recherche ID Commande dans Main
    idx_cmd = next((i for i, c in enumerate(main_cols_norm) if identify_header_type([df_main.columns[i]])[0] == "commande"), -1)
    if idx_cmd != -1:
        existing_commands = set(df_main.iloc[:, idx_cmd].dropna().apply(normalize_key))

    # Recherche ID Transaction dans Main
    idx_trans = next((i for i, c in enumerate(main_cols_norm) if identify_header_type([df_main.columns[i]])[0] == "transaction"), -1)
    if idx_trans != -1:
        existing_transactions = set(df_main.iloc[:, idx_trans].dropna().apply(normalize_key))

    total_imported = 0
    
    # Colonnes Fixes requises
    REQUIRED_COLS = ["Commission", "Variable %", "Fixe (DT)", "Location", "amount", 
                     "id_transaction", "Monnaie", "Avance", "Nom du fichier", 
                     "Date d'import", "Statut", "Utilisateur", "driver Phone", "Annulée?", "Status", "driver name", "RIB"]

    # S'assurer que df_main a toutes les colonnes requises
    for col in REQUIRED_COLS:
        if col not in df_main.columns:
            df_main[col] = pd.NA

    progress_bar = st.progress(0)
    
    for idx_file, uploaded_file in enumerate(files_to_import):
        filename = uploaded_file.name
        
        # Lecture fichier
        try:
            if filename.endswith('.csv'):
                # Détection séparateur simple
                try:
                    temp_df = pd.read_csv(uploaded_file, sep=None, engine='python')
                except:
                    uploaded_file.seek(0)
                    temp_df = pd.read_csv(uploaded_file, sep=',')
            else:
                temp_df = pd.read_excel(uploaded_file)
        except Exception as e:
            log_report.append(f"❌ {filename} : Erreur lecture ({str(e)})")
            continue
            
        if temp_df.empty or len(temp_df) < 1:
            log_report.append(f"⚠️ {filename} : Vide")
            continue

        # Identification Type
        type_file, key_col_name = identify_header_type(temp_df.columns)
        
        if not type_file:
            log_report.append(f"⚠️ {filename} : Ignoré (Pas de colonne ID Commande ou Transaction)")
            continue
            
        # Normalisation headers fichier source pour mapping
        src_map = {normalize_label(c): c for c in temp_df.columns}
        
        imported_count = 0
        skipped_count = 0
        
        # Préparation mapping colonnes (Source -> Colonnes Standards)
        col_montant = next((c for c in temp_df.columns if normalize_label(c) in ["montant total","montant","total","total ttc"]), None)
        col_remise = next((c for c in temp_df.columns if normalize_label(c) in ["remise", "discount"]), None)
        col_restau = next((c for c in temp_df.columns if normalize_label(c) in ['restaurant','restau','restaur','nom du restaurant','restaurant name']), None)
        
        for _, row in temp_df.iterrows():
            # Check ID
            raw_id = row[key_col_name]
            norm_id = normalize_key(raw_id)
            
            if not norm_id:
                skipped_count += 1
                continue
                
            if type_file == "commande" and norm_id in existing_commands:
                skipped_count += 1
                continue
            if type_file == "transaction" and norm_id in existing_transactions:
                skipped_count += 1
                continue
                
            # Création de la nouvelle ligne (dictionnaire)
            new_row = {col: pd.NA for col in df_main.columns}
            
            # 1. Remplissage basique (mapping par nom)
            for src_col in temp_df.columns:
                norm_src = normalize_label(src_col)
                
                # Mapping spécial Date
                if norm_src in ["issue date", "issue_date", "date"]:
                    target_col = "Date" # Standardiser vers "Date"
                    val = parse_date_custom(row[src_col], is_transaction=(type_file=="transaction"))
                    if val is not pd.NaT:
                         new_row[target_col] = val
                    
                    # Cas spécial Transaction: issue date -> order day si existe
                    if type_file == "transaction" and norm_src in ["issue date", "issue_date"]:
                        if "order day" in df_main.columns:
                            new_row["order day"] = val
                
                # Mapping spécial Phone
                elif norm_src in ["cin", "driver phone"]:
                    new_row["driver Phone"] = normalize_driver_phone(row[src_col])
                    
                # Mapping spécial Nom driver
                elif norm_src in ["driver name", "nom livreur"]:
                    new_row["driver name"] = normalize_driver_name(row[src_col])
                
                # Mapping direct si colonne existe dans Main (par nom approximatif)
                else:
                    # Chercher correspondance dans main
                    found = False
                    for main_c in df_main.columns:
                        if normalize_label(main_c) == norm_src:
                            new_row[main_c] = row[src_col]
                            found = True
                            break
                    # Si colonne inconnue dans Main mais présente dans Source, on pourrait l'ajouter
                    # Pour simplifier ici, on ne garde que ce qui matche df_main ou les colonnes fixes
            
            # 2. Colonnes Méta
            new_row["Nom du fichier"] = filename
            new_row["Date d'import"] = datetime.now()
            new_row["Statut"] = "Importé"
            new_row["Utilisateur"] = "Streamlit User"
            if type_file == "transaction":
                new_row["Annulée?"] = "Delivered" # Logique du script original pour transaction
                new_row["Status"] = "Delivered"

            # 3. Calcul Commission
            montant = float(row[col_montant]) if col_montant and pd.notna(row[col_montant]) else 0.0
            remise = float(row[col_remise]) if col_remise and pd.notna(row[col_remise]) else 0.0
            # Nettoyage NaN venant de float conversion
            if np.isnan(montant): montant = 0.0
            if np.isnan(remise): remise = 0.0
            
            net = montant - remise
            
            restau_name = str(row[col_restau]).lower().strip() if col_restau and pd.notna(row[col_restau]) else ""
            com_info = commission_map.get(restau_name, {'variable': 0, 'fixe': 0, 'location': ""})
            
            new_row["Commission"] = net * com_info['variable']
            new_row["Variable %"] = com_info['variable'] * 100
            new_row["Fixe (DT)"] = com_info['fixe']
            new_row["Location"] = com_info['location']
            
            # Ajout aux listes de contrôle pour le fichier courant (auto-dédoublonnage)
            if type_file == "commande": existing_commands.add(norm_id)
            if type_file == "transaction": existing_transactions.add(norm_id)
            
            new_rows.append(new_row)
            imported_count += 1
            total_imported += 1

        log_report.append(f"✅ {filename} : +{imported_count} lignes / ⛔ {skipped_count} doublons")
        
        # Mise à jour barre progression
        progress_bar.progress((idx_file + 1) / len(files_to_import))

    # Concaténation finale
    if new_rows:
        df_new = pd.DataFrame(new_rows)
        # Alignement des colonnes
        df_concat = pd.concat([df_main, df_new], ignore_index=True)
    else:
        df_concat = df_main

    # 4. Post-traitement : Mapping RIB Livreur
    if df_livreur is not None and not df_livreur.empty:
        # Création map Nom -> RIB
        liv_cols = [normalize_label(c) for c in df_livreur.columns]
        idx_liv_nom = next((i for i, c in enumerate(liv_cols) if c in ["driver name", "nom livreur"]), -1)
        idx_liv_rib = next((i for i, c in enumerate(liv_cols) if c == "rib"), -1)
        
        if idx_liv_nom != -1 and idx_liv_rib != -1:
            rib_map = {}
            col_nom_liv = df_livreur.columns[idx_liv_nom]
            col_rib_liv = df_livreur.columns[idx_liv_rib]
            
            for _, r in df_livreur.iterrows():
                n = normalize_driver_name(r[col_nom_liv])
                if n: rib_map[n] = r[col_rib_liv]
            
            # Appliquer sur df_concat
            # On cherche les colonnes cibles
            main_cols_norm = [normalize_label(c) for c in df_concat.columns]
            idx_target_nom = next((i for i, c in enumerate(main_cols_norm) if c == "driver name"), -1)
            idx_target_rib = next((i for i, c in enumerate(main_cols_norm) if c == "rib"), -1)
            
            if idx_target_nom != -1 and idx_target_rib != -1:
                col_t_nom = df_concat.columns[idx_target_nom]
                col_t_rib = df_concat.columns[idx_target_rib]
                
                # Fonction appliquée ligne par ligne pour update RIB si vide ou diff
                def update_rib(row):
                    n = normalize_driver_name(row[col_t_nom])
                    if n in rib_map:
                        return rib_map[n]
                    return row[col_t_rib]
                
                df_concat[col_t_rib] = df_concat.apply(update_rib, axis=1)

    return df_concat, log_report

def update_locations_only(df_data, commission_map):
    """Fonction outil : Mettre à jour les locations manquantes."""
    cols_norm = [normalize_label(c) for c in df_data.columns]
    idx_restau = next((i for i, c in enumerate(cols_norm) if c in ['restaurant name','nom du restaurant','restaurant']), -1)
    idx_loc = next((i for i, c in enumerate(cols_norm) if c in ['location','adresse','zone','ville']), -1)
    
    if idx_restau == -1:
        return df_data, "Colonne Restaurant introuvable."
    
    # Si colonne Location n'existe pas, on l'ajoute
    col_loc_name = "Location"
    if idx_loc != -1:
        col_loc_name = df_data.columns[idx_loc]
    else:
        df_data[col_loc_name] = pd.NA

    col_restau_name = df_data.columns[idx_restau]
    count = 0
    
    for i, row in df_data.iterrows():
        restau = str(row[col_restau_name]).lower().strip()
        current_loc = str(row[col_loc_name]) if pd.notna(row[col_loc_name]) else ""
        
        # On met à jour si la map a une info et que c'est pertinent (ou écrasement)
        # Le script GAS faisait une mise à jour systématique si trouvé dans map
        if restau in commission_map:
            new_loc = commission_map[restau]['location']
            if new_loc and new_loc != current_loc:
                df_data.at[i, col_loc_name] = new_loc
                count += 1
                
    return df_data, f"{count} locations mises à jour."

# ==========================================
# 4. INTERFACE STREAMLIT
# ==========================================

st.title("🚀 Importateur Excel/CSV (Style Yassir)")

st.sidebar.header("📁 Fichiers de Référence")
st.sidebar.markdown("Chargez d'abord l'état actuel de vos données.")

uploaded_data = st.sidebar.file_uploader("1. Fichier Data (Base de données actuelle)", type=['xlsx', 'csv'])
uploaded_comm = st.sidebar.file_uploader("2. Fichier Commission", type=['xlsx', 'csv'])
uploaded_livreur = st.sidebar.file_uploader("3. Fichier Livreur (Optionnel)", type=['xlsx', 'csv'])

# Initialisation Session State
if 'main_df' not in st.session_state:
    st.session_state['main_df'] = pd.DataFrame()
if 'comm_map' not in st.session_state:
    st.session_state['comm_map'] = {}
if 'livreur_df' not in st.session_state:
    st.session_state['livreur_df'] = pd.DataFrame()

# Chargement des données sidebar
if st.sidebar.button("Charger les références"):
    if uploaded_data:
        try:
            if uploaded_data.name.endswith('.csv'):
                st.session_state['main_df'] = pd.read_csv(uploaded_data)
            else:
                st.session_state['main_df'] = pd.read_excel(uploaded_data)
            st.sidebar.success(f"Data chargée : {len(st.session_state['main_df'])} lignes")
        except Exception as e:
            st.sidebar.error(f"Erreur Data : {e}")

    if uploaded_comm:
        try:
            df_c = pd.read_excel(uploaded_comm) if uploaded_comm.name.endswith('.xlsx') else pd.read_csv(uploaded_comm)
            st.session_state['comm_map'] = get_commission_map(df_c)
            st.sidebar.success(f"Commissions chargées : {len(st.session_state['comm_map'])} restos")
        except Exception as e:
            st.sidebar.error(f"Erreur Comms : {e}")
            
    if uploaded_livreur:
        try:
            st.session_state['livreur_df'] = pd.read_excel(uploaded_livreur) if uploaded_livreur.name.endswith('.xlsx') else pd.read_csv(uploaded_livreur)
            st.sidebar.success("Livreurs chargés")
        except: pass

st.divider()

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📥 Nouveaux Fichiers à Importer")
    new_files = st.file_uploader("Sélectionnez les fichiers (Excel/CSV)", accept_multiple_files=True)

    if st.button("🚀 Lancer l'importation", type="primary"):
        if st.session_state['main_df'].empty:
            st.warning("Veuillez d'abord charger le fichier Data dans la barre latérale.")
        elif not new_files:
            st.warning("Aucun nouveau fichier sélectionné.")
        else:
            with st.spinner("Traitement en cours..."):
                updated_df, report = process_import(
                    st.session_state['main_df'], 
                    new_files, 
                    st.session_state['comm_map'],
                    st.session_state['livreur_df']
                )
                
                st.session_state['main_df'] = updated_df
                
                st.success("Importation terminée !")
                st.expander("Voir le rapport détaillé").write("\n".join(report))

with col2:
    st.subheader("🔧 Outils")
    if st.button("🔄 Mettre à jour les Locations"):
        if st.session_state['main_df'].empty:
            st.warning("Pas de données chargées.")
        elif not st.session_state['comm_map']:
            st.warning("Pas de fichier Commission chargé.")
        else:
            updated_df, msg = update_locations_only(st.session_state['main_df'], st.session_state['comm_map'])
            st.session_state['main_df'] = updated_df
            st.info(msg)

st.divider()

st.subheader("📊 Résultat (Aperçu)")
if not st.session_state['main_df'].empty:
    st.dataframe(st.session_state['main_df'].head(50))
    
    # Export
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        st.session_state['main_df'].to_excel(writer, index=False, sheet_name='Data')
        
    st.download_button(
        label="💾 Télécharger le fichier Data mis à jour (Excel)",
        data=buffer.getvalue(),
        file_name=f"Data_Updated_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.ms-excel"
    )
else:
    st.info("Aucune donnée à afficher.")
