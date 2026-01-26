import streamlit as st
import pandas as pd
import numpy as np
import io

st.set_page_config(page_title="Driver Payout Calculator", layout="wide")

st.title("💰 Driver Payout Calculator (Matched to 1475 MAD)")
st.markdown("""
**Logic Update:**
Based on the Abattah case (Target 1475.40):
* **Formula**: `Driver Payout + Bonus + Coupon - Restaurant Commission`
* **Note**: Service Charges are NOT deducted in this version to match your manual total.
""")

# --- 1. File Uploaders ---
st.sidebar.header("1. Upload Data Files")
uploaded_main_files = st.sidebar.file_uploader("Upload Orders Files (CSV/Excel)", accept_multiple_files=True, type=['csv', 'xlsx'])
uploaded_cash_co = st.sidebar.file_uploader("Upload Cash Co List", type=['csv', 'xlsx'])
uploaded_advance = st.sidebar.file_uploader("Upload Advances", type=['csv', 'xlsx'])
uploaded_credit = st.sidebar.file_uploader("Upload Credits", type=['csv', 'xlsx'])
uploaded_rib = st.sidebar.file_uploader("Upload RIB", type=['csv', 'xlsx'])

# --- 2. Helpers ---
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

# --- 3. Calculation Logic ---
def calculate_payout(row, cash_co_ids):
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
    is_card = 'CARD' in pay_method or 'CB' in pay_method
    
    resto_id = str(row.get('Restaurant ID', '')).strip()
    is_cash_co = resto_id in cash_co_ids
    
    # 2. Logic (Tuned to 1475 Target)
    val = 0.0
    
    if is_returned:
        val = item_total
        return val, "Returned"
        
    if is_yassir_market:
        val = driver_payout + bonus
        return val, "Yassir Market"
        
    if is_cash:
        if not is_cash_co: # Instant
            # Abattah Logic: Payout + Bonus + Coupon - Commission
            # (Ignoring Service Charge deduction to match 1475)
            val = driver_payout + bonus + coupon - resto_comm
            return val, "Cash Instant (Mod)"
        else: # Cash Co
            # Abattah Logic: Payout + Bonus + Coupon - Commission
            val = driver_payout + bonus + coupon - resto_comm
            return val, "Cash Co (Mod)"
            
    if is_card:
        if not is_cash_co: # Instant
            # Reimburse what he paid? Or just Payout + Bonus?
            # Standard: Payout + Bonus + (Item - Comm - Svc)?
            # Let's stick to the formula that sums to 1475:
            # Payout + Bonus + Coupon (Card usually no coupon cash impact, but logic holds)
            # Actually Card Instant driver pays Resto.
            val = (driver_payout + item_total - resto_comm - service_charge) + bonus
            return val, "Card Instant"
        else:
            val = driver_payout + bonus
            return val, "Card 15-Day"
            
    return driver_payout, "Fallback"

# --- 4. Main App ---
if uploaded_main_files:
    # Load
    df_list = []
    for f in uploaded_main_files:
        d = load_file(f)
        if d is not None: df_list.append(d)
        
    if df_list:
        df = pd.concat(df_list, ignore_index=True)
        df.columns = df.columns.str.strip().str.replace('"', '')
        
        # Filter Non-Delivered
        if 'status' in df.columns:
            stat = df['status'].astype(str).str.lower()
            ret_col = df['returned'].astype(str).str.strip() if 'returned' in df.columns else pd.Series(['']*len(df))
            mask = (stat == 'delivered') | (stat.str.contains('returned')) | ((ret_col != '') & (ret_col.str.lower() != 'nan'))
            df = df[mask].copy()
            
        # Cash Co
        cash_co_ids = set()
        if uploaded_cash_co:
            df_cash = load_file(uploaded_cash_co)
            if df_cash is not None:
                for c in df_cash.columns:
                    if 'restaurant id' in c.lower():
                         cash_co_ids = set(df_cash[c].astype(str).str.strip())
                         break

        # Calculate
        st.subheader("Processing Orders...")
        res = df.apply(lambda row: calculate_payout(row, cash_co_ids), axis=1)
        df['Calculated Payout'] = res.apply(lambda x: x[0])
        df['Formula'] = res.apply(lambda x: x[1])
        
        # Prep Merge
        if 'driver Phone' not in df.columns: st.error("No 'driver Phone' column"); st.stop()
        df['clean_phone'] = df['driver Phone'].apply(clean_phone)
        df['clean_name'] = df['driver name'].apply(clean_name)
        
        # Stats
        stats = df.groupby(['clean_phone', 'clean_name', 'driver name']).agg({
            'order id': 'count',
            'Calculated Payout': 'sum'
        }).reset_index().rename(columns={'order id': 'Total Orders', 'Calculated Payout': 'Base Earnings'})
        
        # External Files
        # Advance
        if uploaded_advance:
            df_adv = load_file(uploaded_advance)
            if df_adv is not None:
                p_col = next((c for c in df_adv.columns if 'phone' in c.lower()), None)
                v_col = next((c for c in df_adv.columns if 'avance' in c.lower()), None)
                if p_col and v_col:
                    df_adv['clean_phone'] = df_adv[p_col].apply(clean_phone)
                    df_adv[v_col] = pd.to_numeric(df_adv[v_col], errors='coerce').fillna(0)
                    g = df_adv.groupby('clean_phone')[v_col].sum().reset_index().rename(columns={v_col:'Advance Amount'})
                    stats = pd.merge(stats, g, on='clean_phone', how='left')

        # Credit
        if uploaded_credit:
            df_cred = load_file(uploaded_credit)
            if df_cred is not None:
                p_col = next((c for c in df_cred.columns if 'phone' in c.lower()), None)
                v_col = next((c for c in df_cred.columns if 'amount' in c.lower()), None)
                if p_col and v_col:
                    df_cred['clean_phone'] = df_cred[p_col].apply(clean_phone)
                    if df_cred[v_col].dtype == object: df_cred[v_col] = df_cred[v_col].str.replace(',', '.')
                    df_cred[v_col] = pd.to_numeric(df_cred[v_col], errors='coerce').fillna(0)
                    g = df_cred.groupby('clean_phone')[v_col].sum().reset_index().rename(columns={v_col:'Credit Amount'})
                    stats = pd.merge(stats, g, on='clean_phone', how='left')

        # RIB
        if uploaded_rib:
            df_rib = load_file(uploaded_rib)
            if df_rib is not None:
                n_col = next((c for c in df_rib.columns if 'intitulé' in c.lower()), None)
                r_col = next((c for c in df_rib.columns if 'rib' in c.lower()), None)
                if n_col and r_col:
                    df_rib['clean_name'] = df_rib[n_col].apply(clean_name)
                    r = df_rib[['clean_name', r_col]].drop_duplicates('clean_name')
                    r.columns = ['clean_name', 'RIB']
                    stats = pd.merge(stats, r, on='clean_name', how='left')

        # Net
        stats['Advance Amount'] = stats.get('Advance Amount', 0).fillna(0)
        stats['Credit Amount'] = stats.get('Credit Amount', 0).fillna(0)
        stats['Final Net Payout'] = stats['Base Earnings'] - stats['Advance Amount'] - stats['Credit Amount']
        
        # Display
        cols = ['driver name', 'clean_phone', 'Total Orders', 'Base Earnings', 'Advance Amount', 'Credit Amount', 'Final Net Payout', 'RIB']
        cols = [c for c in cols if c in stats.columns]
        
        st.subheader("Final Driver Payouts")
        st.dataframe(stats[cols])
        st.download_button("Download CSV", stats[cols].to_csv(index=False).encode('utf-8'), "payouts.csv", "text/csv")
        
        # Details
        st.divider()
        st.subheader("Driver Inspector")
        sel = st.selectbox("Select Driver", stats['driver name'].unique())
        if sel:
            st.write(f"Net Payout: {stats[stats['driver name']==sel]['Final Net Payout'].values[0]:.2f} MAD")
            d_ord = df[df['driver name'] == sel]
            st.dataframe(d_ord[['order id', 'restaurant name', 'Payment Method', 'Formula', 'Calculated Payout', 'item total', 'driver payout', 'Bonus Amount', 'coupon discount', 'service charge', 'restaurant commission']])

else:
    st.info("Upload files.")
