import streamlit as st
import pandas as pd
import numpy as np
import io

# Set page config
st.set_page_config(page_title="Driver Payout Calculator", layout="wide")

st.title("💰 Driver Payout Calculator")
st.markdown("""
This platform calculates the final payouts for delivery agents.
**Update:** Displays **Base Earnings** (before deductions) and **Final Net Payout** separately to help verify manual calculations.
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
    if uploaded_file is None:
        return None
    try:
        if uploaded_file.name.endswith('.csv'):
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=',')
            # If separator detection failed (only 1 col), try semicolon
            if df.shape[1] < 2:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep=';')
        else:
            df = pd.read_excel(uploaded_file)
        
        # Clean columns immediately
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
    # Extract values
    item_total = float(row.get('item total', 0) or 0)
    driver_payout = float(row.get('driver payout', 0) or 0)
    bonus = float(row.get('Bonus Amount', 0) or 0)
    if pd.isna(bonus): bonus = 0
    service_charge = float(row.get('service charge', 0) or 0)
    resto_comm = float(row.get('restaurant commission', 0) or 0)
    
    # Coupon handling
    coupon = float(row.get('coupon discount', 0) or 0)
    if coupon == 0:
        coupon = float(row.get('Total Discount Amount', 0) or 0)
    
    # Extract Status/Meta
    status = str(row.get('status', '')).lower()
    returned_col = str(row.get('returned', '')).strip()
    services = str(row.get('services', '')).lower()
    pay_method = str(row.get('Payment Method', '')).upper()
    resto_id = str(row.get('Restaurant ID', ''))
    
    # Flags
    is_returned = 'returned' in status or returned_col != ''
    is_yassir_market = 'yassir market' in services
    is_cash = 'CASH' in pay_method
    is_card = 'CARD' in pay_method or 'CB' in pay_method
    is_15_day = resto_id in cash_co_ids
    is_instant = not is_15_day

    # --- Logic Application ---
    
    # 1. Returned Orders: Always pays Item Total
    if is_returned:
        return item_total, "Returned"

    # 2. Yassir Market
    if is_yassir_market:
        return driver_payout + bonus, "Yassir Market"

    # 3. Cash Payment
    if is_cash:
        if is_instant:
            # Case 3: Cash + Instant Restaurant
            # Balance = Earnings - (Net Cash Held - Coupon)
            # Result: Bonus + Coupon - Service - Commission
            val = bonus + coupon - service_charge - resto_comm
            return val, "Cash Instant"
        else:
            # Case 4: Cash + 15 Day (Cash Co)
            # Balance = Earnings - (Cash Held - Coupon)
            # Result: Bonus + Coupon - Item Total - Service
            val = bonus + coupon - item_total - service_charge
            return val, "Cash 15-Day"

    # 4. Card Payment
    if is_card: 
        if is_instant:
            # Case 5a: Partner Instant
            val = (driver_payout + item_total - resto_comm - service_charge) + bonus
            return val, "Card Instant"
        else:
            # Case 5b: 15-Day Payment
            val = driver_payout + bonus
            return val, "Card 15-Day"

    # Fallback
    return driver_payout, "Fallback"

# --- 3. Main Execution ---

if uploaded_main_files:
    # Load Main Data
    df_list = []
    for f in uploaded_main_files:
        d = load_file(f)
        if d is not None:
            df_list.append(d)
    
    if df_list:
        df = pd.concat(df_list, ignore_index=True)
        df.columns = df.columns.str.strip().str.replace('"', '')
        
        initial_count = len(df)
        
        # --- 0. FILTER: Delete non-delivered ---
        if 'status' in df.columns:
            status_mask = df['status'].astype(str).str.lower() == 'delivered'
            returned_mask = df['status'].astype(str).str.lower().str.contains('returned')
            if 'returned' in df.columns:
                 returned_col_mask = df['returned'].notna() & (df['returned'].astype(str).str.strip() != '')
                 returned_mask = returned_mask | returned_col_mask
            
            df = df[status_mask | returned_mask].copy()
            st.info(f"Filtered Non-Delivered Orders: {initial_count} -> {len(df)} remaining.")
        
        # --- 1. Load Cash Co & Add Resto Type ---
        cash_co_ids = set()
        if uploaded_cash_co:
            df_cash = load_file(uploaded_cash_co)
            if df_cash is not None and 'Restaurant ID' in df_cash.columns:
                cash_co_ids = set(df_cash['Restaurant ID'].astype(str))
                st.info(f"Loaded {len(cash_co_ids)} Cash Co Restaurants.")

        if 'Restaurant ID' in df.columns:
            df['Resto Type'] = df['Restaurant ID'].astype(str).apply(
                lambda x: 'Cash Co (15 Days)' if x in cash_co_ids else 'Instant Payment'
            )
        else:
            df['Resto Type'] = 'Unknown'

        # --- 2. Critical Columns Check ---
        col_map = {c.lower(): c for c in df.columns}
        if 'driver phone' not in col_map:
            st.error(f"Column 'driver Phone' not found. Available: {list(df.columns)}")
            st.stop()
            
        phone_col = col_map['driver phone']
        name_col = col_map.get('driver name', 'driver name')
        
        # --- 3. Date Filter ---
        date_col = None
        for c in ['order day', 'order date', 'created at']:
            if c in col_map: date_col = col_map[c]; break
        
        if date_col:
            st.divider()
            st.subheader("2. Select Date Range (Obligatoire)")
            df['temp_date'] = pd.to_datetime(df[date_col], dayfirst=True, errors='coerce')
            
            min_d, max_d = df['temp_date'].min(), df['temp_date'].max()
            
            if pd.notna(min_d):
                date_range = st.date_input("Select Range", value=(), min_value=min_d.date(), max_value=max_d.date())
                if not date_range or len(date_range) != 2:
                    st.warning("Please select a Start and End date.")
                    st.stop()
                
                s_date, e_date = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
                df = df[(df['temp_date'] >= s_date) & (df['temp_date'] <= e_date)].copy()
                st.success(f"Filtered Date Range: {len(df)} orders.")
            else:
                st.warning("Date parsing failed. Showing all data.")

        # --- 4. Calculate ---
        st.subheader("Processing...")
        
        # Apply Logic and split result into Value and Type
        results = df.apply(lambda row: calculate_order_payout(row, cash_co_ids), axis=1)
        df['Calculated Payout'] = results.apply(lambda x: x[0])
        df['Calculation Type'] = results.apply(lambda x: x[1])
        
        df['clean_phone'] = df[phone_col].apply(clean_phone)
        df['clean_name'] = df[name_col].apply(clean_name)
        
        # Aggregate
        driver_stats = df.groupby(['clean_phone', 'clean_name', name_col]).agg({
            'order id': 'count',
            'Calculated Payout': 'sum'
        }).reset_index().rename(columns={'order id': 'Total Orders', 'Calculated Payout': 'Base Earnings'})
        
        # --- 5. Merge External ---
        if uploaded_advance:
            df_adv = load_file(uploaded_advance)
            if df_adv is not None:
                acm = {c.lower(): c for c in df_adv.columns}
                if 'driver phone' in acm and 'avance' in acm:
                    df_adv['clean_phone'] = df_adv[acm['driver phone']].apply(clean_phone)
                    df_adv['Avance'] = pd.to_numeric(df_adv[acm['avance']], errors='coerce').fillna(0)
                    adv_g = df_adv.groupby('clean_phone')['Avance'].sum().reset_index()
                    driver_stats = pd.merge(driver_stats, adv_g, on='clean_phone', how='left')

        if uploaded_credit:
            df_cred = load_file(uploaded_credit)
            if df_cred is not None:
                ccm = {c.lower(): c for c in df_cred.columns}
                if 'driver phone' in ccm and 'amount' in ccm:
                    df_cred['clean_phone'] = df_cred[ccm['driver phone']].apply(clean_phone)
                    amt_col = ccm['amount']
                    if df_cred[amt_col].dtype == object:
                         df_cred[amt_col] = df_cred[amt_col].str.replace(',', '.').astype(float)
                    df_cred['Credit Amount'] = pd.to_numeric(df_cred[amt_col], errors='coerce').fillna(0)
                    cred_g = df_cred.groupby('clean_phone')['Credit Amount'].sum().reset_index()
                    driver_stats = pd.merge(driver_stats, cred_g, on='clean_phone', how='left')

        if uploaded_rib:
            df_rib = load_file(uploaded_rib)
            if df_rib is not None:
                rcm = {c.lower(): c for c in df_rib.columns}
                nk = next((k for k in rcm if "intitulé" in k), None)
                rk = next((k for k in rcm if "rib" in k), None)
                if nk and rk:
                    df_rib['clean_name'] = df_rib[rcm[nk]].apply(clean_name)
                    df_rib_c = df_rib[['clean_name', rcm[rk]]].drop_duplicates('clean_name')
                    df_rib_c.columns = ['clean_name', 'RIB']
                    driver_stats = pd.merge(driver_stats, df_rib_c, on='clean_name', how='left')

        # --- 6. Final Net ---
        if 'Advance Amount' not in driver_stats.columns:
            driver_stats['Advance Amount'] = 0.0
        else:
            driver_stats['Advance Amount'] = driver_stats['Advance Amount'].fillna(0)
            
        if 'Credit Amount' not in driver_stats.columns:
            driver_stats['Credit Amount'] = 0.0
        else:
            driver_stats['Credit Amount'] = driver_stats['Credit Amount'].fillna(0)
        
        # Calculate Final
        driver_stats['Final Net Payout'] = driver_stats['Base Earnings'] - driver_stats['Advance Amount'] - driver_stats['Credit Amount']
        
        # Show
        rib_c = 'RIB' if 'RIB' in driver_stats.columns else None
        cols = [name_col, 'clean_phone', 'Total Orders', 'Base Earnings', 'Advance Amount', 'Credit Amount', 'Final Net Payout']
        if rib_c: cols.append(rib_c)
        cols = [c for c in cols if c in driver_stats.columns]
        
        st.subheader("Final Driver Payouts")
        st.dataframe(driver_stats[cols])
        
        st.download_button("Download CSV", driver_stats[cols].to_csv(index=False).encode('utf-8'), "driver_payouts.csv", "text/csv")
        
        # Details
        st.divider()
        st.subheader("🔍 Driver Detail View")
        sel = st.selectbox("Select Driver to Inspect", driver_stats[name_col].unique())
        if sel:
            # Show Stats
            stats = driver_stats[driver_stats[name_col] == sel].iloc[0]
            st.write(f"**Base Earnings:** {stats['Base Earnings']:.2f} MAD")
            st.write(f"**- Advance:** {stats['Advance Amount']:.2f} MAD")
            st.write(f"**- Credit:** {stats['Credit Amount']:.2f} MAD")
            st.write(f"**= Net Payout:** {stats['Final Net Payout']:.2f} MAD")
            
            # Show Orders
            d_ord = df[df[name_col] == sel]
            st.write("---")
            st.write(f"Order Details for **{sel}**:")
            show_cols = ['order id', 'order day', 'restaurant name', 'Resto Type', 'Payment Method', 'status', 'item total', 'driver payout', 'Bonus Amount', 'Calculation Type', 'Calculated Payout']
            show_cols = [c for c in show_cols if c in d_ord.columns]
            st.dataframe(d_ord[show_cols])

else:
    st.info("Please upload files.")
