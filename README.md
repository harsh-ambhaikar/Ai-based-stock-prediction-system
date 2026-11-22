# Stockwise

A responsive stock research dashboard built with Flask, HTML, CSS, JavaScript, SQLAlchemy, and scikit-learn forecasting models.

## Run locally

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 and create an account. SQLite is created automatically at `instance/stockwise.db`. Set `SECRET_KEY` before deploying. To enable MySQL, create a database and set `DATABASE_URL` (example in `.env.example`):

```text
mysql+pymysql://username:password@localhost/stockwise?charset=utf8mb4
```

## Deploy on Streamlit Community Cloud

Community Cloud deploys the Streamlit interface from a GitHub repository. The entry point is `streamlit_app.py`; it reuses the Flask project's user, market-data, database, and forecasting code. The Streamlit theme starts in dark mode.

1. Push this project to a GitHub repository. Keep `.env`, `.streamlit/secrets.toml`, `instance/`, and the Kaggle archive/database out of Git; they are ignored locally.
2. Sign in at [Streamlit Community Cloud](https://share.streamlit.io/) with GitHub, choose **Create app**, select the repository and branch, and set the app file path to `streamlit_app.py`.
3. In the app's **Advanced settings → Secrets**, provide the credentials the deployed app needs, for example:

   ```toml
   DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
   FINNHUB_API_KEY = "your-finnhub-key"
   SECRET_KEY = "a-long-random-value"
   ```

Use a hosted PostgreSQL or MySQL database for accounts and prediction history. Without `DATABASE_URL`, the app falls back to local SQLite, whose data should not be treated as durable on a hosted app. The large local Kaggle index is not uploaded; market history is fetched from Yahoo Finance when available. Community Cloud requires a linked GitHub repository and supports secrets configured in the app settings; do not commit secrets to Git.

The app attempts to load ten years of daily prices from Yahoo Finance via `yfinance`, refreshing in the background so market lookups do not block the dashboard. When the local Kaggle index is imported, it serves matching symbols immediately from SQLite while live data refreshes in the background. Built-in demo symbols are visibly marked when only illustrative data is available; unknown symbols without verified live or Kaggle history are rejected instead of fabricated. Forecasts accept trading days, months, or years (up to five years) and predict the selected horizon directly from returns, volatility, momentum, and moving-average features. Ridge Regression, Random Forest, and Gradient Boosting are compared on a chronological holdout with a full-horizon gap between training labels and validation features to reduce leakage. The lowest holdout MAE model is refit on the available history and used for the outlook; the dashboard reports its holdout MAE. Longer horizons require enough historical data to evaluate honestly. Forecasts and their data source are estimates for education, not investment advice.

## Live quote updates

The dashboard polls Finnhub for a quote on the selected US ticker every 15 seconds. Keep the API key on the Flask server; it is never sent to the browser. Add it to `.env`:

```text
FINNHUB_API_KEY=your_api_key
```

Copy `.env.example` to `.env`, add your Finnhub key, and restart Flask. The dashboard displays provider timestamps and warns when the key is missing or the provider is unavailable. The chart overlays the latest quote on daily history rather than showing a trade-by-trade feed.

## Import the large Kaggle history

Stockwise can use the public-domain [Huge Stock Market Dataset on Kaggle](https://www.kaggle.com/datasets/borismarjanovic/price-volume-data-for-all-us-stocks-etfs), which contains daily records for thousands of US stocks and ETFs. Its snapshot is historical (through November 2017), so it provides broad model history but is not a current quote feed.

1. Download the Kaggle dataset archive and save it as `data/kaggle/stocks.zip` (or extract it into `data/kaggle/`).
2. Build the local indexed market database:

   ```bash
   python import_kaggle_stocks.py data/kaggle/stocks.zip
   ```

The importer batches rows into a compact SQLite database with a `(symbol, date)` primary key and swaps it into place only after a successful import. Requests then fetch a ticker's recent history through that index instead of scanning the full archive. The archive and generated database are local data files and are excluded from Git.

## Refresh recent S&P 500 prices

The Kaggle full-universe snapshot ends in 2017. The local database can be refreshed for current S&P 500 constituents using Aethereal's [daily S&P 500 dataset](https://dataset.aethereal.top/datasets/sp500-stock-prices), which provides adjusted OHLCV rows through the latest completed session. Save its CSV to `data/market/sp500-stock-prices.csv`, then merge it into the existing index:

```bash
python import_kaggle_stocks.py data/market/sp500-stock-prices.csv --merge
```

The refresh source currently states that it is free for research and education. It covers the current S&P 500 constituents, while the older Kaggle snapshot continues to provide wider historical symbol coverage.

## Included

- Registration, sign-in, password hashing, profile name editing, and logout
- Modern responsive dashboard with ticker search, popular stocks, market indicators, and price charts
- 7, 14, or 30 trading-day Random Forest estimates and saved prediction history
- SQLite by default and MySQL through `DATABASE_URL`

Stockwise is an educational project. Market data may be delayed, and model outputs are experimental estimates, not financial advice.
