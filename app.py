import streamlit as st
import pandas as pd
import numpy as np
import io

# Set page config
st.set_page_config(page_title="Driver Payout Calculator", layout="wide")

st.title("💰 Driver Payout Calculator")
st.markdown("""
This platform calculates the final payouts for delivery agents based on your specific business rules.
**Updates:** Adapted to specific file formats (CASH-CO, Avance, Credit, RIB).
""")

# --- 1. File Uploaders ---
st.sidebar.header("1. Upload Data Files")

uploaded_main_files = st.sidebar.file_uploader("Upload Orders Files (CSV/Excel)", accept_multiple_files=True, type=['csv', 'xlsx'])
uploaded_cash_co = st.sidebar.file_uploader("Upload Cash Co / 15-Day Resto File (CASH-CO)", type=['csv', 'xlsx'])
uploaded_advance = st.sidebar.file_uploader("Upload Advances File (Avance Livreur)", type=['csv', 'xlsx'])
uploaded_credit = st.sidebar.file_uploader("Upload Credits File (Credit Livreur)", type=['csv', 'xlsx'])
uploaded_rib = st.sidebar.file_uploader("Upload Driver RIB File (Delivery guys RIB)", type=['csv', 'xlsx'])

# --- 2. Helper Functions ---

def load_file(uploaded_file, sep=None):
    if uploaded_file is None:
        return None
    try:
        # If csv, try to detect separator or use provided
        if uploaded_file.name.endswith('.csv'):
            if sep:
                return pd.read_csv(uploaded_file, sep=sep)
            try:
                # Default try with comma
                return pd.read_csv(uploaded_file)
            except:
                # Fallback to semicolon
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, sep=';')
        else:
            return pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Error loading {uploaded_file.name}: {e}")
        return None

def clean_phone(phone):
    """Standardize phone numbers for merging."""
    if pd.isna(phone):
        return ""
    s = str(phone).replace(" ", "").replace("-", "").replace(".", "")
    return s

def clean_name(name):
    """Normalize names for merging (lowercase, strip)."""
    if pd.isna(name):
        return ""
    return str(name).strip().lower()

def calculate_order_payout(row, cash_co_ids):
    """
    Applies the 4 forms of logic to calculate payout for a single order.
    """
    # Extract values, handling NaNs
    item_total = float(row.get('item total', 0) or 0)
    driver_payout = float(row.get('driver payout', 0) or 0)
    bonus = float(row.get('Bonus Amount', 0) or 0)
    if pd.isna(bonus): bonus = 0
    service_charge = float(row.get('service charge', 0) or 0)
    resto_comm = float(row.get('restaurant commission', 0) or 0)
    
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
    
    # Form 1: Returned
    if is_returned:
        return item_total

    # Form 2: Yassir Market
    if is_yassir_market:
        return driver_payout + bonus

    # Form 3 & 4: Cash Payment
    if is_cash:
        if is_instant:
            # Form 3: Partner Instant (Driver pays Resto, gets paid by Customer)
            # Formula: (Total D - Resto Comm - Service Charge + Driver Payout) + BONUS
            total_d = item_total + driver_payout + service_charge
            return (total_d - resto_comm - service_charge + driver_payout) + bonus
        else:
            # Form 4: Cash Co (Driver pays nothing to Resto)
            # Formula: Driver Payout + Bonus
            return driver_payout + bonus

    # Form 5: Card Payment
    if is_card: 
        if is_instant:
            # Case 5a: Partner Instant (Driver pays Resto with own cash)
            # Formula: (Driver Payout + Item Total - Resto Comm - Service Charge) + BONUS
            return (driver_payout + item_total - resto_comm - service_charge) + bonus
        else:
            # Case 5b: 15-Day Payment
            # Formula: Driver Payout + Bonus
            return driver_payout + bonus

    # Fallback
    return driver_payout

# --- 3. Main Execution ---

if uploaded_main_files:
    # Load Main Data
    df_list = []
    for f in uploaded_main_files:
        # Main export usually uses semicolon
        d = load_file(f, sep=';')
        if d is not None:
            df_list.append(d)
    
    if df_list:
        df = pd.concat(df_list, ignore_index=True)
        st.success(f"Loaded {len(df)} orders from {len(uploaded_main_files)} files.")
        
        # --- Load Cash Co (15 Day) ---
        cash_co_ids = set()
        if uploaded_cash_co:
            df_cash = load_file(uploaded_cash_co)
            if df_cash is not None:
                # Clean column names
                df_cash.columns = df_cash.columns.str.strip()
                if 'Restaurant ID' in df_cash.columns:
                    cash_co_ids = set(df_cash['Restaurant ID'].astype(str))
                    st.info(f"Loaded {len(cash_co_ids)} Cash Co (15-day) Restaurants.")
                else:
                    st.error("Could not find 'Restaurant ID' column in Cash Co file.")

        # --- Calculate Payouts ---
        st.subheader("Processing Orders...")
        
        # Apply calculation
        df['Calculated Payout'] = df.apply(lambda row: calculate_order_payout(row, cash_co_ids), axis=1)
        
        # Prepare for Aggregation
        df['clean_phone'] = df['driver Phone'].apply(clean_phone)
        df['clean_name'] = df['driver name'].apply(clean_name)
        
        # Aggregation by Phone AND Name (to keep name in result)
        driver_stats = df.groupby(['clean_phone', 'clean_name', 'driver name']).agg({
            'order id': 'count',
            'Calculated Payout': 'sum'
        }).reset_index()
        
        driver_stats.rename(columns={'order id': 'Total Orders', 'Calculated Payout': 'Base Earnings'}, inplace=True)
        
        # --- Merge External Files ---
        
        # 1. Advances (Avance Livreur)
        if uploaded_advance:
            # Usually semicolon separated based on snippet
            df_adv = load_file(uploaded_advance, sep=';')
            if df_adv is not None:
                # Clean columns: ' driver Phone ' -> 'driver Phone'
                df_adv.columns = df_adv.columns.str.strip()
                
                if 'driver Phone' in df_adv.columns and 'Avance' in df_adv.columns:
                    df_adv['clean_phone'] = df_adv['driver Phone'].apply(clean_phone)
                    df_adv['Avance'] = pd.to_numeric(df_adv['Avance'], errors='coerce').fillna(0)
                    
                    adv_grouped = df_adv.groupby('clean_phone')['Avance'].sum().reset_index()
                    adv_grouped.rename(columns={'Avance': 'Advance Amount'}, inplace=True)
                    
                    driver_stats = pd.merge(driver_stats, adv_grouped, on='clean_phone', how='left')
                else:
                    st.warning("Advance file must have 'driver Phone' and 'Avance' columns.")
        
        # 2. Credits (Credit Livreur)
        if uploaded_credit:
            # Usually semicolon separated
            df_cred = load_file(uploaded_credit, sep=';')
            if df_cred is not None:
                df_cred.columns = df_cred.columns.str.strip()
                
                if 'driver Phone' in df_cred.columns and 'amount' in df_cred.columns:
                    df_cred['clean_phone'] = df_cred['driver Phone'].apply(clean_phone)
                    
                    # Handle comma decimal if present (e.g. 27,558)
                    if df_cred['amount'].dtype == object:
                        df_cred['amount'] = df_cred['amount'].str.replace(',', '.').astype(float)
                    
                    df_cred['Credit Amount'] = pd.to_numeric(df_cred['amount'], errors='coerce').fillna(0)
                    
                    cred_grouped = df_cred.groupby('clean_phone')['Credit Amount'].sum().reset_index()
                    
                    driver_stats = pd.merge(driver_stats, cred_grouped, on='clean_phone', how='left')
                else:
                    st.warning("Credit file must have 'driver Phone' and 'amount' columns.")

        # 3. RIB (Delivery guys RIB)
        if uploaded_rib:
            # Usually comma separated
            df_rib = load_file(uploaded_rib) # Auto-detect or comma
            if df_rib is not None:
                df_rib.columns = df_rib.columns.str.strip()
                # Snippet shows: 'Intitulé du compte' (Name), 'RIB'
                if 'Intitulé du compte' in df_rib.columns and 'RIB' in df_rib.columns:
                    df_rib['clean_name'] = df_rib['Intitulé du compte'].apply(clean_name)
                    
                    # Deduplicate: If same name has multiple RIBs, take first
                    df_rib_clean = df_rib[['clean_name', 'RIB']].drop_duplicates(subset=['clean_name'])
                    
                    # Merge on NAME since RIB file has no phone
                    driver_stats = pd.merge(driver_stats, df_rib_clean, on='clean_name', how='left')
                else:
                    st.warning("RIB file must have 'Intitulé du compte' and 'RIB' columns.")

        # --- Final Calculation ---
        # Fill NaNs
        driver_stats['Advance Amount'] = driver_stats.get('Advance Amount', 0).fillna(0)
        driver_stats['Credit Amount'] = driver_stats.get('Credit Amount', 0).fillna(0)
        
        # Net Payout
        driver_stats['Final Net Payout'] = driver_stats['Base Earnings'] - driver_stats['Advance Amount'] - driver_stats['Credit Amount']
        
        # Formatting for Display
        final_cols = ['driver name', 'clean_phone', 'Total Orders', 'Base Earnings', 'Advance Amount', 'Credit Amount', 'Final Net Payout', 'RIB']
        # Only select cols that exist
        final_cols = [c for c in final_cols if c in driver_stats.columns]
        
        st.subheader("Final Driver Payouts")
        st.dataframe(driver_stats[final_cols])
        
        # Download
        csv = driver_stats[final_cols].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Payout Report as CSV",
            data=csv,
            file_name='driver_payouts_final.csv',
            mime='text/csv',
        )
        
        # Optional: Show details
        st.divider()
        st.subheader("Driver Detail View")
        selected_driver = st.selectbox("Select Driver", driver_stats['driver name'].unique())
        if selected_driver:
            driver_orders = df[df['driver name'] == selected_driver]
            st.write(f"Orders for {selected_driver}:")
            cols_to_show = ['order id', 'order time', 'restaurant name', 'Payment Method', 'status', 'item total', 'driver payout', 'Bonus Amount', 'Calculated Payout']
            cols_to_show = [c for c in cols_to_show if c in driver_orders.columns]
            st.dataframe(driver_orders[cols_to_show])

    else:
        st.warning("No valid data found in uploaded files.")

else:
    st.info("Please upload the Order Export files to begin.")
