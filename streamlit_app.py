"""Streamlit Community Cloud entry point for Stockwise."""
import os

import streamlit as st
st.set_page_config(page_title="Stockwise · Market Lab", page_icon="📈", layout="wide")

# Community Cloud secrets are injected before importing the shared Flask/data module.
for _key in ("dc1f6f9r01qlc5urbfgg", "DATABASE_URL", "dc1f6f9r01qlc5urbff0dc1f6f9r01qlc5urbffg"):
    try:
        if _key in st.secrets and not os.environ.get(_key):
            os.environ[_key] = str(st.secrets[_key])
    except Exception:
        pass

import numpy as np
import pandas as pd
from werkzeug.security import check_password_hash, generate_password_hash

import app as stockwise

st.markdown("""
<style>
.block-container{max-width:1420px;padding-top:1.6rem;padding-bottom:2.8rem}
[data-testid="stSidebar"]{border-right:1px solid #1c2933}
[data-testid="stMetric"]{background:#111b25;border:1px solid #1e2e39;padding:16px 18px;border-radius:14px}
[data-testid="stMetricLabel"]{color:#98aab7}
div.stButton>button,div.stFormSubmitButton>button{border-radius:9px;font-weight:650;min-height:2.5rem}
.sw-kicker{color:#42dda7;font-size:.75rem;font-weight:750;letter-spacing:.13em;text-transform:uppercase}
.sw-note{color:#91a1ae;font-size:.88rem}
.sw-card{background:linear-gradient(140deg,#121e28,#101820);border:1px solid #20313d;border-radius:16px;padding:18px 20px}
</style>
""", unsafe_allow_html=True)


def db_user(user_id):
    if not user_id:
        return None
    with stockwise.app.app_context():
        return stockwise.db.session.get(stockwise.User, int(user_id))


def load_history(symbol):
    market = stockwise.get_market_data(symbol)
    if market.get("source") not in {"live", "live+kaggle", "snapshot", "snapshot+live", "kaggle"} and symbol not in stockwise.COMPANIES:
        raise ValueError(f"Verified market history for {symbol} isn't available yet. Check the ticker or try again shortly.")
    return market


def show_auth():
    st.markdown('<div class="sw-kicker">Stockwise · Market Lab</div>', unsafe_allow_html=True)
    st.title("Your market workspace")
    st.caption("Sign in to explore market data and create model-based outlooks.")
    login_tab, register_tab = st.tabs(["Sign in", "Create account"])
    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email").strip().lower()
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            with stockwise.app.app_context():
                user = stockwise.User.query.filter_by(email=email).first()
                valid = user and check_password_hash(user.password_hash, password)
                user_id = user.id if valid else None
            if user_id:
                st.session_state.user_id = user_id
                st.rerun()
            st.error("That email and password combination wasn't recognized.")
    with register_tab:
        with st.form("register_form"):
            name = st.text_input("Full name")
            email = st.text_input("Email address").strip().lower()
            password = st.text_input("Password (at least 8 characters)", type="password")
            submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
        if submitted:
            if len(name.strip()) < 2 or "@" not in email or len(password) < 8:
                st.error("Add your name, a valid email, and a password with at least 8 characters.")
            else:
                with stockwise.app.app_context():
                    exists = stockwise.User.query.filter_by(email=email).first()
                    if exists:
                        user_id = None
                    else:
                        user = stockwise.User(name=name.strip(), email=email,
                                              password_hash=generate_password_hash(password))
                        stockwise.db.session.add(user)
                        stockwise.db.session.commit()
                        user_id = user.id
                if user_id:
                    st.session_state.user_id = user_id
                    st.rerun()
                st.error("An account with that email already exists.")


@st.fragment(run_every=15)
def live_quote(symbol):
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        st.caption("Daily history · Add FINNHUB_API_KEY in Streamlit secrets to enable quote updates.")
        return
    try:
        quote = stockwise.get_live_quote(symbol, token)
    except Exception:
        quote = None
    if not quote:
        st.caption("Live quote unavailable · showing daily history")
        return
    timestamp = pd.to_datetime(quote["timestamp"], unit="s", utc=True).tz_convert("Asia/Kolkata")
    st.caption(f"● Quote · {timestamp.strftime('%b %d, %H:%M:%S IST')} · refreshes every 15 seconds")
    c1, c2 = st.columns([1, 4])
    c1.metric("Latest quote", f"${quote['price']:,.2f}", f"{quote['changePct']:+.2f}%")


def page_dashboard(user):
    st.markdown('<div class="sw-kicker">Market overview</div>', unsafe_allow_html=True)
    st.title(f"Good to see you, {user.name.split()[0]}")
    st.caption("Explore verified daily history, check the latest quote, and generate a model outlook.")

    with st.form("stock_search", clear_on_submit=False):
        col1, col2 = st.columns([4, 1])
        symbol_input = col1.text_input("Search a stock ticker", value=st.session_state.get("active_symbol", "NVDA"),
                                       placeholder="Try NVDA, AAPL, MSFT…").strip().upper()
        submitted = col2.form_submit_button("Load stock", type="primary", use_container_width=True)
    if submitted and symbol_input:
        st.session_state.active_symbol = symbol_input
        st.session_state.pop("active_market", None)

    symbols = st.columns(4)
    for col, symbol in zip(symbols, ("NVDA", "AAPL", "MSFT", "GOOGL")):
        if col.button(symbol, use_container_width=True):
            st.session_state.active_symbol = symbol
            st.session_state.pop("active_market", None)

    symbol = st.session_state.get("active_symbol", "NVDA")
    if "active_market" not in st.session_state:
        with st.spinner(f"Loading {symbol} history…"):
            try:
                st.session_state.active_market = load_history(symbol)
            except Exception as exc:
                st.error(str(exc))
                return
    market = st.session_state.active_market
    history = market["history"]
    prices = np.asarray(history["close"], dtype=float)
    current = float(prices[-1])
    previous = float(prices[-2]) if len(prices) > 1 else current

    live_quote(symbol)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Daily close", f"${current:,.2f}", f"{(current / previous - 1) * 100:+.2f}%")
    metric_cols[1].metric("52-week high", f"${market['stats']['high52']:,.2f}")
    metric_cols[2].metric("52-week low", f"${market['stats']['low52']:,.2f}")
    metric_cols[3].metric("20-day average", f"${market['stats']['sma20']:,.2f}")
    st.caption(f"{market['company']} · {symbol} · {market['source']} · history through {market['lastDate']}")

    chart_col, forecast_col = st.columns([1.5, 1], gap="large")
    with chart_col:
        st.subheader("Price history")
        frame = pd.DataFrame({"Date": pd.to_datetime(history["dates"]), "Close": history["close"]}).set_index("Date")
        st.line_chart(frame, height=350, color="#34d399")
    with forecast_col:
        st.subheader("ML price outlook")
        amount = st.number_input("Forecast amount", min_value=1, max_value=1260, value=7, step=1)
        unit = st.selectbox("Forecast unit", ["trading days", "months", "years"])
        limits = {"trading days": 1260, "months": 60, "years": 5}
        if amount > limits[unit]:
            st.error(f"Choose at most {limits[unit]} {unit}.")
        elif st.button("Generate outlook", type="primary", use_container_width=True):
            horizon = int(amount * (1 if unit == "trading days" else 21 if unit == "months" else 252))
            try:
                with st.spinner("Comparing forecasting models…"):
                    forecast, info = stockwise.forecast_prices(prices, horizon)
                predicted = float(forecast[-1])
                with stockwise.app.app_context():
                    row = stockwise.Prediction(user_id=user.id, symbol=symbol, company=market["company"],
                                               current_price=current, predicted_price=predicted, horizon=horizon)
                    stockwise.db.session.add(row)
                    stockwise.db.session.commit()
                st.success(f"{unit.title()} estimate: ${predicted:,.2f}")
                st.metric("Estimated model move", f"{(predicted / current - 1) * 100:+.2f}%")
                st.caption(f"Selected model: {info['model']} · Holdout MAE: {info['validationMaePct']:.2f}%")
                st.caption("Experimental estimate; not investment advice.")
            except Exception as exc:
                st.error(str(exc))


def page_history(user):
    st.markdown('<div class="sw-kicker">Your activity</div>', unsafe_allow_html=True)
    st.title("Prediction history")
    with stockwise.app.app_context():
        rows = (stockwise.Prediction.query.filter_by(user_id=user.id)
                .order_by(stockwise.Prediction.created_at.desc()).limit(100).all())
        records = [{"Ticker": row.symbol, "Company": row.company,
                    "Current": round(row.current_price, 2), "Estimate": round(row.predicted_price, 2),
                    "Move %": round((row.predicted_price / row.current_price - 1) * 100, 2),
                    "Horizon": stockwise.display_horizon(row.horizon),
                    "Created": row.created_at.strftime("%b %d, %Y %H:%M")} for row in rows]
    if records:
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
    else:
        st.info("Your generated outlooks will appear here.")


def page_profile(user):
    st.markdown('<div class="sw-kicker">Account</div>', unsafe_allow_html=True)
    st.title("My profile")
    st.write(user.email)
    with st.form("profile_form"):
        name = st.text_input("Name", value=user.name)
        submitted = st.form_submit_button("Save changes", type="primary")
    if submitted:
        if len(name.strip()) < 2 or len(name.strip()) > 90:
            st.error("Name must be between 2 and 90 characters.")
        else:
            with stockwise.app.app_context():
                record = stockwise.db.session.get(stockwise.User, user.id)
                record.name = name.strip()
                stockwise.db.session.commit()
            st.success("Profile updated.")
            st.rerun()


user = db_user(st.session_state.get("user_id"))
if user is None:
    st.session_state.pop("user_id", None)
    show_auth()
else:
    with st.sidebar:
        st.markdown("## 📈 Stockwise")
        st.caption(f"Signed in as {user.name}")
        page = st.radio("Workspace", ["Dashboard", "Prediction history", "My profile"], label_visibility="collapsed")
        st.divider()
        if st.button("Log out", use_container_width=True):
            for key in ("user_id", "active_market", "active_symbol"):
                st.session_state.pop(key, None)
            st.rerun()
        st.caption("Market data may be delayed. Forecasts are estimates, not investment advice.")

    if page == "Dashboard":
        page_dashboard(user)
    elif page == "Prediction history":
        page_history(user)
    else:
        page_profile(user)
