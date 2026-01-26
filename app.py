import streamlit as st
import pandas as pd
import numpy as np
import io

# Set page config
st.set_page_config(page_title="Driver Payout Calculator", layout="wide")

st.title("💰 Driver Payout Calculator")
st.markdown("""
This platform calculates the final payouts for delivery agents based on your specific business rules.
**Updates:** Bonus added to 'Cash/Instant' and 'Card/Instant' logic.
""")

# --- 1. File Uploaders ---
st.sidebar.header("1. Upload Data Files")

uploaded_main_files = st.sidebar.file_uploader("Upload Orders Files (CSV/Excel)", accept_multiple_files=True, type=['csv', 'xlsx'])
uploaded_cash_co = st.sidebar.file_uploader("Upload Cash Co / 15-Day Resto File", type=['csv', 'xlsx'])
uploaded_advance = st.sidebar.file_uploader("Upload Advances File", type=['csv', 'xlsx'])
uploaded_credit = st.sidebar.file_uploader("Upload Credits File", type=['csv', 'xlsx'])
uploaded_rib = st.sidebar.file_uploader("Upload Driver RIB File", type=['csv', 'xlsx'])

# --- 2. Helper Functions ---

def load_file(uploaded_file):
    if uploaded_file is None:
        return None
    try:
        if uploaded_file.name.endswith('.csv'):
            # Try reading with semi-colon first (common in the sample), then comma
            try:
                return pd.read_csv(uploaded_file, sep=';')
            except:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, sep=',')
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
            # Formula: Total D - Resto Comm - Service Charge + Driver Payout + BONUS
            # Total D = Item Total + Driver Payout + Service Charge
            total_d = item_total + driver_payout + service_charge
            return (total_d - resto_comm - service_charge + driver_payout) + bonus
        else:
            # Form 4: Cash Co (Driver pays nothing to Resto)
            # Formula: Driver Payout + Bonus
            return driver_payout + bonus

    # Form 5: Card Payment (Implied from text)
    if is_card: # Or any non-cash method
        if is_instant:
            # Case 5a: Partner Instant (Driver pays Resto with own cash)
            # Formula: Driver Payout + Item Total - Resto Comm - Service Charge + BONUS
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
        st.success(f"Loaded {len(df)} orders from {len(uploaded_main_files)} files.")
        
        # Load Cash Co IDs
        cash_co_ids = set()
        if uploaded_cash_co:
            df_cash = load_file(uploaded_cash_co)
            # Assume first column is ID
            if df_cash is not None:
                # Make sure we convert to string to match logic
                cash_co_ids = set(df_cash.iloc[:, 0].astype(str))
                st.info(f"Loaded {len(cash_co_ids)} Cash Co (15-day) Restaurants.")

        # --- Calculate Payouts ---
        st.subheader("Processing Orders...")
        
        # Apply calculation
        df['Calculated Payout'] = df.apply(lambda row: calculate_order_payout(row, cash_co_ids), axis=1)
        
        # Group by Driver
        # We use 'driver Phone' as unique key, cleaning it first
        df['clean_phone'] = df['driver Phone'].apply(clean_phone)
        
        # Aggregation
        driver_stats = df.groupby(['clean_phone', 'driver name']).agg({
            'order id': 'count',
            'Calculated Payout': 'sum'
        }).reset_index()
        driver_stats.rename(columns={'order id': 'Total Orders', 'Calculated Payout': 'Base Earnings'}, inplace=True)
        
        # --- Merge External Files ---
        
        # 1. Advances
        if uploaded_advance:
            df_adv = load_file(uploaded_advance)
            if df_adv is not None:
                # Expecting columns like Phone/Name and Amount
                df_adv['clean_phone'] = df_adv.iloc[:, 0].apply(clean_phone)
                df_adv['Advance Amount'] = pd.to_numeric(df_adv.iloc[:, 1], errors='coerce').fillna(0)
                
                # Group in case of duplicates
                adv_grouped = df_adv.groupby('clean_phone')['Advance Amount'].sum().reset_index()
                
                driver_stats = pd.merge(driver_stats, adv_grouped, on='clean_phone', how='left')
        
        # 2. Credits
        if uploaded_credit:
            df_cred = load_file(uploaded_credit)
            if df_cred is not None:
                df_cred['clean_phone'] = df_cred.iloc[:, 0].apply(clean_phone)
                df_cred['Credit Amount'] = pd.to_numeric(df_cred.iloc[:, 1], errors='coerce').fillna(0)
                
                cred_grouped = df_cred.groupby('clean_phone')['Credit Amount'].sum().reset_index()
                
                driver_stats = pd.merge(driver_stats, cred_grouped, on='clean_phone', how='left')

        # 3. RIB
        if uploaded_rib:
            df_rib = load_file(uploaded_rib)
            if df_rib is not None:
                df_rib['clean_phone'] = df_rib.iloc[:, 0].apply(clean_phone)
                # Assume RIB is col 1, maybe Bank Name col 2
                # Ensure we don't crash if file has fewer columns
                cols_to_take = min(3, len(df_rib.columns))
                df_rib = df_rib.iloc[:, :cols_to_take] 
                
                # Rename for clarity
                new_cols = ['clean_phone', 'RIB', 'Bank']
                df_rib.columns = new_cols[:cols_to_take]
                
                # Deduplicate RIBs
                df_rib = df_rib.drop_duplicates(subset=['clean_phone'])
                
                driver_stats = pd.merge(driver_stats, df_rib, on='clean_phone', how='left')

        # --- Final Calculation ---
        # Fill NaNs
        driver_stats['Advance Amount'] = driver_stats.get('Advance Amount', 0).fillna(0)
        driver_stats['Credit Amount'] = driver_stats.get('Credit Amount', 0).fillna(0)
        
        # Net Payout = Base Earnings - Advances - Credits
        # (Assuming Advances/Credits are POSITIVE numbers representing debt)
        driver_stats['Final Net Payout'] = driver_stats['Base Earnings'] - driver_stats['Advance Amount'] - driver_stats['Credit Amount']
        
        # Formatting
        st.subheader("Final Driver Payouts")
        st.dataframe(driver_stats)
        
        # Download
        csv = driver_stats.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Payout Report as CSV",
            data=csv,
            file_name='driver_payouts_final.csv',
            mime='text/csv',
        )
        
        # Optional: Show details for a specific driver
        st.divider()
        st.subheader("Driver Detail View")
        selected_driver = st.selectbox("Select Driver", driver_stats['driver name'].unique())
        if selected_driver:
            driver_orders = df[df['driver name'] == selected_driver]
            st.write(f"Orders for {selected_driver}:")
            # Show relevant columns including debug info
            cols_to_show = ['order id', 'order time', 'restaurant name', 'Payment Method', 'item total', 'driver payout', 'Bonus Amount', 'Calculated Payout']
            # Only show columns that exist
            cols_to_show = [c for c in cols_to_show if c in driver_orders.columns]
            st.dataframe(driver_orders[cols_to_show])

    else:
        st.warning("No valid data found in uploaded files.")

else:
    st.info("Please upload the Order Export files to begin.")
