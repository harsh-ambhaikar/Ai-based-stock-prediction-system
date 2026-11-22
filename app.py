import os
import secrets
import hmac
import threading
import time
import sqlite3
import json
from urllib.parse import urlencode
from urllib.request import urlopen
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(90), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    predictions = db.relationship("Prediction", backref="user", cascade="all, delete-orphan", lazy=True)


class Prediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    symbol = db.Column(db.String(15), nullable=False)
    company = db.Column(db.String(140), nullable=False)
    current_price = db.Column(db.Float, nullable=False)
    predicted_price = db.Column(db.Float, nullable=False)
    horizon = db.Column(db.Integer, nullable=False, default=7)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me-stockwise")
    database_url = os.environ.get("DATABASE_URL", "sqlite:///stockwise.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    with app.app_context():
        db.create_all()

    def signed_in():
        uid = session.get("user_id")
        return db.session.get(User, uid) if uid else None

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            expected = session.get("_csrf_token", "")
            supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRFToken", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                if request.path.startswith("/api/"):
                    return jsonify(error="Your session expired. Refresh the page and try again."), 400
                flash("Your session expired. Please try again.", "error")
                return redirect(request.referrer or url_for("index"))

    @app.context_processor
    def inject_user():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return {"current_user": signed_in(), "csrf_token": session["_csrf_token"]}

    @app.get("/")
    def index():
        return redirect(url_for("dashboard")) if signed_in() else render_template("landing.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if signed_in():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                session.clear()
                session["user_id"] = user.id
                return redirect(url_for("dashboard"))
            flash("That email and password combination wasn't recognized.", "error")
        return render_template("auth.html", mode="login")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if signed_in():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if len(name) < 2 or len(name) > 90 or "@" not in email or len(password) < 8:
                flash("Add your name, a valid email, and a password with at least 8 characters.", "error")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists. Try signing in.", "error")
            else:
                user = User(name=name, email=email, password_hash=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                session["user_id"] = user.id
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="register")

    @app.get("/dashboard")
    def dashboard():
        if not signed_in():
            return redirect(url_for("login"))
        return render_template("dashboard.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/api/history")
    def history():
        user = signed_in()
        if not user:
            return jsonify(error="Please sign in again."), 401
        rows = Prediction.query.filter_by(user_id=user.id).order_by(Prediction.created_at.desc()).limit(50).all()
        return jsonify([{"id": p.id, "symbol": p.symbol, "company": p.company, "current": p.current_price,
                         "predicted": p.predicted_price, "change": (p.predicted_price / p.current_price - 1) * 100,
                         "horizon": p.horizon, "horizonLabel": display_horizon(p.horizon),
                         "created": p.created_at.strftime("%b %d, %Y · %H:%M")} for p in rows])

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        user = signed_in()
        if not user:
            return redirect(url_for("login"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if len(name) >= 2 and len(name) <= 90:
                user.name = name
                db.session.commit()
                flash("Your profile has been updated.", "success")
            else:
                flash("Name must be between 2 and 90 characters.", "error")
            return redirect(url_for("profile"))
        return render_template("profile.html", user=user, prediction_count=len(user.predictions))

    @app.get("/api/stock/<symbol>")
    def stock_data(symbol):
        if not signed_in():
            return jsonify(error="Please sign in again."), 401
        symbol = symbol.upper().strip()
        if len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
            return jsonify(error="Enter a valid ticker symbol."), 400
        data = get_market_data(symbol)
        if data.get("source") not in {"live", "live+kaggle", "snapshot", "snapshot+live", "kaggle"} and symbol not in COMPANIES:
            with _market_cache_lock:
                refreshing = symbol in _market_refreshing
            if refreshing:
                return jsonify(pending=True, message="Checking verified market history…"), 202
            return jsonify(error=f"Live price history for {symbol} isn't available right now. Check the ticker or try again shortly."), 422
        return jsonify(data)

    @app.get("/api/quote/<symbol>")
    def live_quote(symbol):
        """Return a provider quote without exposing the provider token to browsers."""
        if not signed_in():
            return jsonify(error="Please sign in again."), 401
        symbol = symbol.upper().strip()
        if len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
            return jsonify(error="Enter a valid ticker symbol."), 400
        token = os.environ.get("FINNHUB_API_KEY", "").strip()
        provider = "Finnhub"
        if not token:
            return jsonify(configured=False, provider=provider)
        try:
            quote = get_live_quote(symbol, token)
        except Exception:
            return jsonify(configured=True, available=False, provider=provider)
        if not quote:
            return jsonify(configured=True, available=False, provider=provider)
        return jsonify(configured=True, available=True, provider=provider, currency="USD", **quote)

    @app.post("/api/predict")
    def predict():
        user = signed_in()
        if not user:
            return jsonify(error="Please sign in again."), 401
        payload = request.get_json(silent=True) or {}
        symbol = str(payload.get("symbol", "")).upper().strip()
        try:
            if "amount" in payload:
                amount = int(payload.get("amount", 7))
                unit = str(payload.get("unit", "days")).lower().rstrip("s")
                limits = {"day": 1260, "month": 60, "year": 5}
                if unit not in limits or amount < 1 or amount > limits[unit]:
                    return jsonify(error="Choose 1–1,260 trading days, 1–60 months, or 1–5 years."), 400
                horizon = amount if unit == "day" else amount * (21 if unit == "month" else 252)
                horizon_label = f"{amount} {unit}{'' if amount == 1 else 's'}"
            else:
                horizon = max(1, min(1260, int(payload.get("horizon", 7))))
                horizon_label = f"{horizon} trading days"
        except (ValueError, TypeError):
            return jsonify(error="Enter a whole-number forecast period."), 400
        if len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
            return jsonify(error="Enter a valid ticker symbol."), 400
        try:
            market = get_market_data(symbol)
            if market.get("source") not in {"live", "live+kaggle", "snapshot", "snapshot+live", "kaggle"} and symbol not in COMPANIES:
                return jsonify(error=f"Live price history for {symbol} isn't available, so a forecast can't be generated."), 422
            prices = np.array(market["history"]["close"], dtype=float)
            forecast, model_info = forecast_prices(prices, horizon)
        except Exception as exc:
            return jsonify(error=str(exc)), 422
        predicted = float(forecast[-1])
        row = Prediction(user_id=user.id, symbol=symbol, company=market["company"],
                         current_price=float(prices[-1]), predicted_price=predicted, horizon=horizon)
        db.session.add(row)
        db.session.commit()
        return jsonify({"symbol": symbol, "company": market["company"], "source": market.get("source"),
                        "lastDate": market.get("lastDate"), "current": float(prices[-1]),
                        "predicted": predicted, "change": (predicted / prices[-1] - 1) * 100,
                        "horizon": horizon, "horizonLabel": horizon_label,
                        "dates": forecast_dates(horizon), "forecast": forecast.tolist(),
                        "model": model_info["model"], "validationMaePct": model_info["validationMaePct"],
                        "id": row.id})

    return app


COMPANIES = {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "NVDA": "NVIDIA Corporation",
             "GOOGL": "Alphabet Inc.", "AMZN": "Amazon.com, Inc.", "TSLA": "Tesla, Inc.",
             "META": "Meta Platforms, Inc.", "NFLX": "Netflix, Inc.", "AMD": "Advanced Micro Devices, Inc.",
             "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ Trust", "JPM": "JPMorgan Chase & Co."}
_market_cache = {}
_market_refreshing = set()
_market_cache_lock = threading.Lock()
_market_cache_ttl = 300
_quote_cache = {}
_quote_cache_lock = threading.Lock()
_quote_cache_ttl = 12
KAGGLE_MARKET_DB = Path(__file__).parent / "instance" / "kaggle_market.sqlite"


def get_live_quote(symbol, token):
    """Fetch a short-lived, server-cached quote from Finnhub's quote API."""
    now = time.monotonic()
    with _quote_cache_lock:
        cached = _quote_cache.get(symbol)
        if cached and now - cached[0] < _quote_cache_ttl:
            return cached[1]
    query = urlencode({"symbol": symbol, "token": token})
    with urlopen(f"https://finnhub.io/api/v1/quote?{query}", timeout=4) as response:
        payload = json.loads(response.read().decode("utf-8"))
    current = float(payload.get("c") or 0)
    if current <= 0:
        return None
    result = {"symbol": symbol, "price": current, "change": float(payload.get("d") or 0),
              "changePct": float(payload.get("dp") or 0), "timestamp": int(payload.get("t") or 0)}
    with _quote_cache_lock:
        _quote_cache[symbol] = (time.monotonic(), result)
    return result


def display_horizon(trading_days):
    if trading_days % 252 == 0:
        years = trading_days // 252
        return f"{years} year" + ("s" if years != 1 else "")
    if trading_days % 21 == 0:
        months = trading_days // 21
        return f"{months} month" + ("s" if months != 1 else "")
    return f"{trading_days} trading days"


def get_market_data(symbol):
    """Return cached/sample data immediately and refresh live data outside the request."""
    now = time.monotonic()
    should_refresh = False
    with _market_cache_lock:
        cached = _market_cache.get(symbol)
        if cached:
            result = cached[1]
            expired = now - cached[0] >= _market_cache_ttl
            demo_needs_refresh = result.get("source") == "illustrative" and now - cached[0] >= 15
            should_refresh = (expired or demo_needs_refresh) and symbol not in _market_refreshing
        else:
            result = load_kaggle_data(symbol) or make_demo_data(symbol)
            _market_cache[symbol] = (now, result)
            should_refresh = True
        if should_refresh:
            _market_refreshing.add(symbol)
    if should_refresh:
        threading.Thread(target=refresh_market_data, args=(symbol,), daemon=True).start()
    return result


def refresh_market_data(symbol):
    try:
        result = _fetch_market_data(symbol)
        if result.get("source") in {"live", "live+kaggle", "snapshot+live"}:
            with _market_cache_lock:
                _market_cache[symbol] = (time.monotonic(), result)
        else:
            with _market_cache_lock:
                cached = _market_cache.get(symbol)
                if cached:
                    _market_cache[symbol] = (time.monotonic(), cached[1])
    except Exception:
        pass
    finally:
        with _market_cache_lock:
            _market_refreshing.discard(symbol)


def _fetch_market_data(symbol):
    company = COMPANIES.get(symbol, f"{symbol} Holdings")
    try:
        import yfinance as yf
        frame = yf.download(symbol, period="10y", interval="1d", auto_adjust=True, progress=False, threads=False, timeout=5)
        if not frame.empty:
            close_col = frame["Close"]
            if isinstance(close_col, pd.DataFrame):
                close_col = close_col.iloc[:, 0]
            close = close_col.dropna()
            if len(close) >= 40:
                dates = [d.strftime("%Y-%m-%d") for d in close.index]
                vals = close.astype(float).round(2).tolist()
                volume_col = frame["Volume"]
                if isinstance(volume_col, pd.DataFrame):
                    volume_col = volume_col.iloc[:, 0]
                volumes = volume_col.reindex(close.index).fillna(0).astype(float).tolist()
                live = summarize_market(symbol, company, dates, vals, volumes, source="live")
                kaggle = load_kaggle_data(symbol)
                return merge_kaggle_live(kaggle, live) if kaggle else live
    except Exception:
        pass
    return load_kaggle_data(symbol) or make_demo_data(symbol)


def load_kaggle_data(symbol):
    """Read a ticker's pre-indexed Kaggle history without scanning the full dataset."""
    if not KAGGLE_MARKET_DB.exists():
        return None
    try:
        connection = sqlite3.connect(f"{KAGGLE_MARKET_DB.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            rows = connection.execute(
                "SELECT date, close, adj_close, volume FROM stock_prices "
                "WHERE symbol = ? ORDER BY date DESC LIMIT 2520", (symbol,)
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if len(rows) < 60:
        return None
    rows.reverse()
    dates = [row[0] for row in rows]
    close = [float(row[2] if row[2] is not None and row[2] > 0 else row[1]) for row in rows]
    volumes = [int(row[3] or 0) for row in rows]
    source = "snapshot" if dates[-1] > "2017-11-10" else "kaggle"
    return summarize_market(symbol, COMPANIES.get(symbol, f"{symbol} Holdings"), dates, close, volumes, source=source)


def merge_kaggle_live(kaggle, live):
    """Use Kaggle's older history and overlap-calibrate it to the recent live series."""
    old = kaggle["history"]
    recent = live["history"]
    recent_start = recent["dates"][0]
    recent_by_date = dict(zip(recent["dates"], recent["close"]))
    ratios = [recent_by_date[day] / price for day, price in zip(old["dates"], old["close"])
              if day in recent_by_date and price > 0]
    scale = float(np.median(ratios)) if ratios else 1.0
    older_rows = [(day, price * scale, volume) for day, price, volume in
                  zip(old["dates"], old["close"], old["volume"]) if day < recent_start]
    merged = older_rows + list(zip(recent["dates"], recent["close"], recent["volume"]))
    dates = [row[0] for row in merged]
    close = [row[1] for row in merged]
    volumes = [row[2] for row in merged]
    return summarize_market(live["symbol"], live["company"], dates, close, volumes, source="snapshot+live")


def make_demo_data(symbol):
    company = COMPANIES.get(symbol, f"{symbol} Holdings")
    seed = sum(ord(c) * (i + 1) for i, c in enumerate(symbol))
    rng = np.random.default_rng(seed)
    base = 70 + seed % 330
    returns = rng.normal(0.0005, 0.018, 2520)
    close = base * np.cumprod(1 + returns)
    dates = pd.bdate_range(end=datetime.utcnow().date(), periods=len(close)).strftime("%Y-%m-%d").tolist()
    volumes = rng.integers(12_000_000, 65_000_000, len(close)).tolist()
    return summarize_market(symbol, company, dates, close.round(2).tolist(), volumes, source="illustrative")


def summarize_market(symbol, company, dates, close, volumes, source="illustrative"):
    last = close[-1]
    previous = close[-2]
    change = last - previous
    series = pd.Series(close)
    return {"symbol": symbol, "company": company, "source": source, "lastDate": dates[-1], "price": last, "change": round(change, 2),
            "changePct": round(change / previous * 100, 2), "history": {"dates": dates, "close": close, "volume": volumes},
            "stats": {"high52": round(float(series.tail(252).max()), 2), "low52": round(float(series.tail(252).min()), 2),
                      "avgVolume": int(np.mean(volumes[-20:])), "sma20": round(float(series.tail(20).mean()), 2),
                      "sma50": round(float(series.tail(50).mean()), 2)}}


def market_features(prices, end):
    """Stationary, scale-independent features calculated only from prices up to end."""
    window = prices[:end]
    log_returns = np.diff(np.log(window))
    features = []
    for span in (1, 2, 3, 5, 10, 20, 60):
        features.append(float(np.sum(log_returns[-span:])))
    for span in (10, 20, 60):
        features.append(float(np.std(log_returns[-span:])))
    for span in (10, 20, 50, 100, 200):
        features.append(float(window[-1] / np.mean(window[-span:]) - 1))
    changes = np.diff(window[-15:])
    gains = np.maximum(changes, 0).mean()
    losses = np.maximum(-changes, 0).mean()
    features.append(float(1 - 1 / (1 + gains / max(losses, 1e-12))))
    features.append(float(np.mean(log_returns[-20:])))
    return np.asarray(features, dtype=float)


def forecast_prices(prices, horizon):
    """Choose a direct-horizon model using a purged, chronological holdout."""
    from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.base import clone
    values = np.asarray(prices, dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    warmup = min(200, max(60, len(values) // 5))
    independent_samples = len(values) - warmup - (2 * horizon)
    if independent_samples < 60:
        raise ValueError("There isn't enough price history to generate a reliable outlook.")

    origins = np.arange(warmup, len(values) - horizon)
    X = np.asarray([market_features(values, int(i)) for i in origins])
    y = np.log(values[origins + horizon] / values[origins])
    # Keep a full forecast horizon between train labels and validation features.
    # Otherwise overlapping future returns leak the validation period into training.
    cutoff = warmup + horizon + int(independent_samples * 0.70)
    train = origins + horizon < cutoff
    valid = origins >= cutoff
    if train.sum() < 40 or valid.sum() < 20:
        raise ValueError("There isn't enough history to evaluate this forecast window.")

    candidates = [
        ("Ridge Regression", make_pipeline(StandardScaler(), Ridge(alpha=10.0))),
        ("Random Forest", RandomForestRegressor(n_estimators=240, max_depth=4,
          min_samples_leaf=max(10, int(train.sum() * 0.04)), max_features=0.8,
          random_state=23, n_jobs=-1)),
        ("Gradient Boosting", HistGradientBoostingRegressor(max_iter=160, max_leaf_nodes=7,
          learning_rate=0.025, l2_regularization=5.0, min_samples_leaf=20, random_state=23)),
    ]
    best_name, best_model, best_mae = None, None, float("inf")
    for name, candidate in candidates:
        candidate.fit(X[train], y[train])
        score = float(np.mean(np.abs(y[valid] - candidate.predict(X[valid]))))
        if score < best_mae:
            best_name, best_mae = name, score
            best_model = candidate

    final_model = clone(best_model)
    final_model.fit(X, y)
    predicted_return = float(final_model.predict(market_features(values, len(values)).reshape(1, -1))[0])

    daily_volatility = float(np.std(np.diff(np.log(values[-61:]))))
    max_move = max(0.03, 2.5 * daily_volatility * np.sqrt(horizon))
    predicted_return = float(np.clip(predicted_return, -max_move, max_move))
    terminal_price = float(values[-1] * np.exp(predicted_return))
    # A smooth guide from the current price to the model's direct horizon estimate.
    path = values[-1] * np.exp(np.linspace(predicted_return / horizon, predicted_return, horizon))
    return path, {"model": best_name, "terminalPrice": terminal_price,
                  "validationMaePct": best_mae * 100}


def forecast_dates(horizon):
    dates, day = [], datetime.utcnow().date()
    while len(dates) < horizon:
        day += timedelta(days=1)
        if day.weekday() < 5:
            dates.append(day.strftime("%b %d"))
    return dates


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
