import streamlit as st
import pandas as pd
import numpy as np
import io

# Set page config
st.set_page_config(page_title="Driver Payout Calculator", layout="wide")

st.title("💰 Driver Payout Calculator")
st.markdown("""
This platform calculates the final payouts for delivery agents.
**Final Abattah Fix:**
1.  **Card (Payzone)**: Now deducts **Service Charge** (`Payout + Bonus - Svc`).
2.  **Cash**: `Payout + Bonus + Coupon - Commission`.
3.  **Deferred**: `Bonus + Coupon - Item - Svc`.
""")

# --- 1. File Uploaders ---
st.sidebar.header("1. Upload Data Files")
uploaded_main_files = st.sidebar.file_uploader("Upload Orders Files (CSV/Excel)", accept_multiple_files=True, type=['csv', 'xlsx'])
uploaded_cash_co = st.sidebar.file_uploader("Upload Cash Co / 15-Day Resto File (CASH-CO)", type=['csv', 'xlsx'])
uploaded_advance = st.sidebar.file_uploader("Upload Advances File (Avance Livreur)", type=['csv', 'xlsx'])
uploaded_credit = st.sidebar.file_uploader("Upload Credits File (Credit Livreur)", type=['csv', 'xlsx'])
uploaded_rib = st.sidebar.file_uploader("Upload Driver RIB File (Delivery guys RIB)", type=['csv', 'xlsx'])

# --- 2. Helper Functions ---
def load_file(uploaded_file):
    if uploaded_file is None: return None
    try:
        if uploaded_file.name.endswith('.csv'):
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=',')
            if df.shape[1] < 2:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep=';')
        else:
            df = pd.read_excel(uploaded_file)
        df.columns = df.columns.str.strip().str.replace('"', '') 
        return df
    except Exception as e:
        st.error(f"Error loading {uploaded_file.name}: {e}")
        return None

def clean_phone(phone):
    if pd.isna(phone): return ""
    return str(phone).replace(" ", "").replace("-", "").replace(".", "").replace('"', '')

def clean_name(name):
    if pd.isna(name): return ""
    return str(name).strip().lower()

def calculate_order_payout(row, cash_co_ids):
    # 1. Values
    item_total = float(row.get('item total', 0) or 0)
    driver_payout = float(row.get('driver payout', 0) or 0)
    bonus = float(row.get('Bonus Amount', 0) or 0)
    if pd.isna(bonus): bonus = 0
    service_charge = float(row.get('service charge', 0) or 0)
    resto_comm = float(row.get('restaurant commission', 0) or 0)
    
    # Coupon
    coupon = float(row.get('coupon discount', 0) or 0)
    if coupon == 0: coupon = float(row.get('Total Discount Amount', 0) or 0)
    if coupon == 0: coupon = float(row.get('Discount Amount', 0) or 0)
    
    # Flags
    status = str(row.get('status', '')).lower()
    returned_col = str(row.get('returned', '')).strip()
    is_returned = 'returned' in status or (len(returned_col) > 0 and returned_col.lower() != 'nan')
    
    services = str(row.get('services', '')).lower()
    is_yassir_market = 'yassir market' in services
    
    pay_method = str(row.get('Payment Method', '')).upper()
    is_cash = 'CASH' in pay_method
    is_card = 'CARD' in pay_method or 'CB' in pay_method or 'PAYZONE' in pay_method
    
    resto_id = str(row.get('Restaurant ID', '')).strip()
    is_cash_co = resto_id in cash_co_ids
    
    # --- LOGIC ---

    # 1. Returned: Pays Item Total
    if is_returned:
        return item_total, "Returned"

    # 2. Yassir Market: Payout + Bonus
    if is_yassir_market:
        return driver_payout + bonus, "Yassir Market"

    # 3. Cash Payment
    if is_cash:
        if not is_cash_co: # Instant
            # Matches Bader (-Comm-Svc) & Abattah (+Payout+Bonus+Coupon)
            # Formula: Payout + Bonus + Coupon - Commission - Service Charge
            # (Note: Earlier analysis suggested ignoring Svc for Abattah, but Bader requires it. 
            # If this result drifts from 1475, we can toggle Svc off for Cash)
            val = driver_payout + bonus + coupon - resto_comm - service_charge
            return val, "Cash Instant"
        else: # Deferred
            # Balance = Bonus + Coupon - Item - Svc
            # (Payout cancelled / included in debt calc)
            val = bonus + coupon - item_total - service_charge
            return val, "Cash 15-Day"

    # 4. Card Payment (Payzone)
    if is_card: 
        if not is_cash_co:
            # FIX: Subtract Service Charge
            val = driver_payout + bonus - service_charge
            return val, "Card Instant"
        else:
            val = driver_payout + bonus - service_charge
            return val, "Card 15-Day"

    # Fallback
    return driver_payout, "Fallback"

# --- 3. Main Execution ---

if uploaded_main_files:
    # Load Main Data
    df_list = []
    for f in uploaded_main_files:
        d = load_file(f)
        if d is not None: df_list.append(d)
    
    if df_list:
        df = pd.concat(df_list, ignore_index=True)
        df.columns = df.columns.str.strip().str.replace('"', '')
        
        # --- 0. FILTER ---
        if 'status' in df.columns:
            status_mask = df['status'].astype(str).str.lower() == 'delivered'
            returned_mask = df['status'].astype(str).str.lower().str.contains('returned')
            if 'returned' in df.columns:
                 returned_col_mask = df['returned'].notna() & (df['returned'].astype(str).str.strip() != '') & (df['returned'].astype(str).str.lower() != 'nan')
                 returned_mask = returned_mask | returned_col_mask
            
            df = df[status_mask | returned_mask].copy()
        
        # Load Cash Co
        cash_co_ids = set()
        if uploaded_cash_co:
            df_cash = load_file(uploaded_cash_co)
            if df_cash is not None:
                id_col = next((c for c in df_cash.columns if 'id' in c.lower()), None)
                if id_col: cash_co_ids = set(df_cash[id_col].astype(str).str.strip())

        # --- Calculate ---
        st.subheader("Processing...")
        results = df.apply(lambda row: calculate_order_payout(row, cash_co_ids), axis=1)
        df['Calculated Payout'] = results.apply(lambda x: x[0])
        df['Calculation Type'] = results.apply(lambda x: x[1])
        
        # Clean Keys
        if 'driver Phone' not in df.columns: st.error("Missing 'driver Phone'"); st.stop()
        df['clean_phone'] = df['driver Phone'].apply(clean_phone)
        df['clean_name'] = df['driver name'].apply(clean_name)
        
        # Aggregate
        name_col = 'driver name'
        driver_stats = df.groupby(['clean_phone', 'clean_name', name_col]).agg({
            'order id': 'count',
            'Calculated Payout': 'sum'
        }).reset_index().rename(columns={'order id': 'Total Orders', 'Calculated Payout': 'Base Earnings'})
        
        # --- Merge External ---
        if uploaded_advance:
            df_adv = load_file(uploaded_advance)
            if df_adv is not None:
                acm = {c.lower(): c for c in df_adv.columns}
                if 'driver phone' in acm and 'avance' in acm:
                    df_adv['clean_phone'] = df_adv[acm['driver phone']].apply(clean_phone)
                    df_adv['Avance'] = pd.to_numeric(df_adv[acm['avance']], errors='coerce').fillna(0)
                    g = df_adv.groupby('clean_phone')['Avance'].sum().reset_index().rename(columns={'Avance': 'Advance Amount'})
                    driver_stats = pd.merge(driver_stats, g, on='clean_phone', how='left')

        if uploaded_credit:
            df_cred = load_file(uploaded_credit)
            if df_cred is not None:
                ccm = {c.lower(): c for c in df_cred.columns}
                if 'driver phone' in ccm and 'amount' in ccm:
                    df_cred['clean_phone'] = df_cred[ccm['driver phone']].apply(clean_phone)
                    amt_col = ccm['amount']
                    if df_cred[amt_col].dtype == object: df_cred[amt_col] = df_cred[amt_col].str.replace(',', '.').astype(float)
                    df_cred['Credit Amount'] = pd.to_numeric(df_cred[amt_col], errors='coerce').fillna(0)
                    g = df_cred.groupby('clean_phone')['Credit Amount'].sum().reset_index()
                    driver_stats = pd.merge(driver_stats, g, on='clean_phone', how='left')

        if uploaded_rib:
            df_rib = load_file(uploaded_rib)
            if df_rib is not None:
                rcm = {c.lower(): c for c in df_rib.columns}
                nk = next((k for k in rcm if "intitulé" in k), None)
                rk = next((k for k in rcm if "rib" in k), None)
                if nk and rk:
                    df_rib['clean_name'] = df_rib[rcm[nk]].apply(clean_name)
                    r = df_rib[['clean_name', rcm[rk]]].drop_duplicates('clean_name')
                    r.columns = ['clean_name', 'RIB']
                    driver_stats = pd.merge(driver_stats, r, on='clean_name', how='left')

        # --- Final Net ---
        for col in ['Advance Amount', 'Credit Amount']:
            if col not in driver_stats.columns: driver_stats[col] = 0.0
            else: driver_stats[col] = driver_stats[col].fillna(0)
            
        driver_stats['Final Net Payout'] = driver_stats['Base Earnings'] - driver_stats['Advance Amount'] - driver_stats['Credit Amount']
        
        # Show
        rib_c = 'RIB' if 'RIB' in driver_stats.columns else None
        cols = [name_col, 'clean_phone', 'Total Orders', 'Base Earnings', 'Advance Amount', 'Credit Amount', 'Final Net Payout']
        if rib_c: cols.append(rib_c)
        
        st.subheader("Final Driver Payouts")
        st.dataframe(driver_stats[cols])
        st.download_button("Download CSV", driver_stats[cols].to_csv(index=False).encode('utf-8'), "driver_payouts.csv", "text/csv")
        
        # Details
        st.divider()
        sel = st.selectbox("Select Driver for Details", driver_stats[name_col].unique())
        if sel:
            d_ord = df[df[name_col] == sel]
            st.write(f"Orders for **{sel}**:")
            show = ['order id', 'restaurant name', 'Payment Method', 'Calculation Type', 'Calculated Payout', 'item total', 'driver payout', 'Bonus Amount', 'coupon discount', 'service charge', 'restaurant commission']
            show = [c for c in show if c in d_ord.columns]
            st.dataframe(d_ord[show])

else:
    st.info("Please upload files.")
