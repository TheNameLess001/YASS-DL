import streamlit as st
import pandas as pd
import numpy as np
import io

# Set page config
st.set_page_config(page_title="Driver Payout Calculator", layout="wide")

st.title("💰 Driver Payout Calculator")
st.markdown("""
This platform calculates the final payouts for delivery agents based on your specific business rules.
**Update:** Mandatory Date Filter added.
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
    """
    Loads a file and automatically attempts to detect if it is comma or semicolon separated.
    """
    if uploaded_file is None:
        return None
    try:
        if uploaded_file.name.endswith('.csv'):
            # Attempt 1: Try reading with comma
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=',')
            
            # Check: If we only got 1 column, it might be the wrong separator. Try semicolon.
            if df.shape[1] < 2:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep=';')
                
        else:
            # Excel files
            df = pd.read_excel(uploaded_file)
        
        # CLEANUP: Remove whitespace from column names immediately
        df.columns = df.columns.str.strip().str.replace('"', '') 
        return df
    except Exception as e:
        st.error(f"Error loading {uploaded_file.name}: {e}")
        return None

def clean_phone(phone):
    """Standardize phone numbers for merging."""
    if pd.isna(phone):
        return ""
    # Convert to string and remove all non-digit characters roughly
    s = str(phone).replace(" ", "").replace("-", "").replace(".", "").replace('"', '')
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
        d = load_file(f)
        if d is not None:
            df_list.append(d)
    
    if df_list:
        df = pd.concat(df_list, ignore_index=True)
        # Clean columns again
        df.columns = df.columns.str.strip().str.replace('"', '')
        
        st.success(f"Loaded {len(df)} orders from {len(uploaded_main_files)} files.")
        
        # --- Check for Critical Columns ---
        col_map = {c.lower(): c for c in df.columns}
        
        if 'driver phone' not in col_map:
            st.error(f"Column 'driver Phone' not found. Available columns: {list(df.columns)}")
            st.stop()
            
        phone_col = col_map['driver phone']
        name_col = col_map.get('driver name', 'driver name')
        
        # Check for Date Column
        date_col = None
        if 'order day' in col_map: date_col = col_map['order day']
        elif 'order date' in col_map: date_col = col_map['order date']
        elif 'created at' in col_map: date_col = col_map['created at']
        
        if not date_col:
            st.error("Could not find a Date column (e.g., 'order day'). Cannot filter by date.")
            st.stop()

        # --- DATE FILTER SECTION ---
        st.divider()
        st.subheader("2. Select Date Range (Obligatoire)")
        
        # Convert column to datetime
        # Trying dayfirst=True for formats like 23/01/2026
        df['temp_date'] = pd.to_datetime(df[date_col], dayfirst=True, errors='coerce')
        
        min_date = df['temp_date'].min()
        max_date = df['temp_date'].max()
        
        if pd.isna(min_date) or pd.isna(max_date):
             st.warning("Could not parse dates in the file. Showing all data.")
             date_range = None
        else:
            date_range = st.date_input(
                "Select Start and End Date",
                value=(),
                min_value=min_date.date(),
                max_value=max_date.date()
            )

        # Logic to wait for full range selection
        if not date_range or len(date_range) != 2:
            st.warning("⚠️ Please select both a Start Date and an End Date to proceed.")
            st.stop()
        
        start_date, end_date = date_range
        start_date = pd.Timestamp(start_date)
        end_date = pd.Timestamp(end_date)
        
        # Filter Data
        mask = (df['temp_date'] >= start_date) & (df['temp_date'] <= end_date)
        df_filtered = df.loc[mask].copy()
        
        st.success(f"Filtering from {start_date.date()} to {end_date.date()}. Rows: {len(df_filtered)}")
        
        if len(df_filtered) == 0:
            st.warning("No orders found in this date range.")
            st.stop()

        # --- Load Cash Co (15 Day) ---
        cash_co_ids = set()
        if uploaded_cash_co:
            df_cash = load_file(uploaded_cash_co)
            if df_cash is not None:
                if 'Restaurant ID' in df_cash.columns:
                    cash_co_ids = set(df_cash['Restaurant ID'].astype(str))
                    st.info(f"Loaded {len(cash_co_ids)} Cash Co (15-day) Restaurants.")
                else:
                    st.error("Could not find 'Restaurant ID' column in Cash Co file.")

        # --- Calculate Payouts ---
        st.subheader("Processing Orders...")
        
        # Apply calculation on FILTERED dataframe
        df_filtered['Calculated Payout'] = df_filtered.apply(lambda row: calculate_order_payout(row, cash_co_ids), axis=1)
        
        # Prepare for Aggregation
        df_filtered['clean_phone'] = df_filtered[phone_col].apply(clean_phone)
        df_filtered['clean_name'] = df_filtered[name_col].apply(clean_name)
        
        # Aggregation by Phone AND Name
        driver_stats = df_filtered.groupby(['clean_phone', 'clean_name', name_col]).agg({
            'order id': 'count',
            'Calculated Payout': 'sum'
        }).reset_index()
        
        driver_stats.rename(columns={'order id': 'Total Orders', 'Calculated Payout': 'Base Earnings'}, inplace=True)
        
        # --- Merge External Files ---
        
        # 1. Advances
        if uploaded_advance:
            df_adv = load_file(uploaded_advance)
            if df_adv is not None:
                adv_col_map = {c.lower(): c for c in df_adv.columns}
                if 'driver phone' in adv_col_map and 'avance' in adv_col_map:
                    p_col = adv_col_map['driver phone']
                    a_col = adv_col_map['avance']
                    
                    df_adv['clean_phone'] = df_adv[p_col].apply(clean_phone)
                    df_adv['Avance'] = pd.to_numeric(df_adv[a_col], errors='coerce').fillna(0)
                    
                    adv_grouped = df_adv.groupby('clean_phone')['Avance'].sum().reset_index()
                    adv_grouped.rename(columns={'Avance': 'Advance Amount'}, inplace=True)
                    
                    driver_stats = pd.merge(driver_stats, adv_grouped, on='clean_phone', how='left')
                else:
                    st.warning(f"Advance file missing columns (Need 'driver Phone', 'Avance'). Found: {list(df_adv.columns)}")
        
        # 2. Credits
        if uploaded_credit:
            df_cred = load_file(uploaded_credit)
            if df_cred is not None:
                cred_col_map = {c.lower(): c for c in df_cred.columns}
                if 'driver phone' in cred_col_map and 'amount' in cred_col_map:
                    p_col = cred_col_map['driver phone']
                    a_col = cred_col_map['amount']
                    
                    df_cred['clean_phone'] = df_cred[p_col].apply(clean_phone)
                    
                    if df_cred[a_col].dtype == object:
                        df_cred[a_col] = df_cred[a_col].str.replace(',', '.').astype(float)
                    
                    df_cred['Credit Amount'] = pd.to_numeric(df_cred[a_col], errors='coerce').fillna(0)
                    
                    cred_grouped = df_cred.groupby('clean_phone')['Credit Amount'].sum().reset_index()
                    
                    driver_stats = pd.merge(driver_stats, cred_grouped, on='clean_phone', how='left')
                else:
                    st.warning(f"Credit file missing columns (Need 'driver Phone', 'amount'). Found: {list(df_cred.columns)}")

        # 3. RIB
        if uploaded_rib:
            df_rib = load_file(uploaded_rib)
            if df_rib is not None:
                rib_col_map = {c.lower(): c for c in df_rib.columns}
                name_key = next((k for k in rib_col_map if "intitulé" in k), None)
                rib_key = next((k for k in rib_col_map if "rib" in k), None)

                if name_key and rib_key:
                    df_rib['clean_name'] = df_rib[rib_col_map[name_key]].apply(clean_name)
                    df_rib_clean = df_rib[['clean_name', rib_col_map[rib_key]]].drop_duplicates(subset=['clean_name'])
                    df_rib_clean.columns = ['clean_name', 'RIB']
                    
                    driver_stats = pd.merge(driver_stats, df_rib_clean, on='clean_name', how='left')
                else:
                    st.warning(f"RIB file missing columns (Need 'Intitulé du compte', 'RIB'). Found: {list(df_rib.columns)}")

        # --- Final Calculation ---
        driver_stats['Advance Amount'] = driver_stats.get('Advance Amount', 0).fillna(0)
        driver_stats['Credit Amount'] = driver_stats.get('Credit Amount', 0).fillna(0)
        
        driver_stats['Final Net Payout'] = driver_stats['Base Earnings'] - driver_stats['Advance Amount'] - driver_stats['Credit Amount']
        
        # Display
        rib_col = 'RIB' if 'RIB' in driver_stats.columns else None
        final_cols = [name_col, 'clean_phone', 'Total Orders', 'Base Earnings', 'Advance Amount', 'Credit Amount', 'Final Net Payout']
        if rib_col: final_cols.append(rib_col)
        
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
        
        # Optional: Details
        st.divider()
        st.subheader("Driver Detail View")
        selected_driver = st.selectbox("Select Driver", driver_stats[name_col].unique())
        if selected_driver:
            driver_orders = df_filtered[df_filtered[name_col] == selected_driver]
            st.write(f"Orders for {selected_driver} (Filtered Date Range):")
            cols_to_show = ['order id', 'order day', 'restaurant name', 'Payment Method', 'status', 'item total', 'driver payout', 'Bonus Amount', 'Calculated Payout']
            cols_to_show = [c for c in cols_to_show if c in driver_orders.columns]
            st.dataframe(driver_orders[cols_to_show])

    else:
        st.warning("No valid data found in uploaded files.")

else:
    st.info("Please upload the Order Export files to begin.")
