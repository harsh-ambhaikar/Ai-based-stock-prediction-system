"""Build an indexed, read-only-at-runtime SQLite copy of Kaggle daily stock CSVs.

Accepts the per-symbol .us.txt files from the Huge Stock Market Dataset or a
merged CSV with a Symbol/Ticker/Name column. The finished DB is swapped into
place only after the import succeeds.
"""

import argparse
import csv
import io
import math
import os
import re
import sqlite3
import shutil
import zipfile
from datetime import date, datetime
from pathlib import Path


DEFAULT_OUTPUT = Path(__file__).parent / "instance" / "kaggle_market.sqlite"
SYMBOL_COLUMNS = ("symbol", "ticker", "stock", "name")
DATE_COLUMNS = ("date", "datetime", "timestamp")


def clean_header(value):
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_date(value):
    value = (value or "").strip()
    if len(value) >= 10 and value[4:5] == "-":
        return value[:10]
    for fmt in ("%m/%d/%Y", "%Y%m%d", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def parse_number(value):
    try:
        number = float((value or "").replace(",", "").strip())
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def symbol_from_filename(filename):
    name = Path(filename).name
    match = re.match(r"^(.+?)(?:\.us)?\.(?:txt|csv)$", name, re.IGNORECASE)
    if not match:
        return None
    symbol = match.group(1).upper()
    return symbol if len(symbol) <= 12 and re.fullmatch(r"[A-Z0-9.-]+", symbol) else None


def iter_sources(source):
    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".csv", ".txt")) and not name.startswith("__MACOSX/"):
                    with archive.open(name) as raw:
                        with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as text:
                            yield name, text
        return
    files = [source] if source.is_file() else sorted(source.rglob("*.csv")) + sorted(source.rglob("*.txt"))
    for path in files:
        if path.is_file() and path.suffix.lower() in (".csv", ".txt"):
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as text:
                yield str(path), text


def row_values(raw_row, headers, fallback_symbol):
    row = {headers[key]: value for key, value in raw_row.items() if key in headers}
    symbol = next((row.get(key) for key in SYMBOL_COLUMNS if row.get(key)), fallback_symbol)
    if not symbol:
        return None
    symbol = symbol.strip().upper()
    if len(symbol) > 12 or not re.fullmatch(r"[A-Z0-9.-]+", symbol):
        return None
    raw_date = next((row.get(key) for key in DATE_COLUMNS if row.get(key)), None)
    day = parse_date(raw_date)
    close = parse_number(row.get("adj_close") or row.get("adjusted_close") or row.get("close"))
    raw_close = parse_number(row.get("close")) or close
    if not day or not close or close <= 0:
        return None
    volume = parse_number(row.get("volume")) or 0
    return (symbol, day, parse_number(row.get("open")), parse_number(row.get("high")),
            parse_number(row.get("low")), raw_close, close, int(volume))


def import_dataset(source, output, batch_size=25000, merge=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".building")
    if temporary.exists():
        temporary.unlink()
    if merge and output.exists():
        shutil.copy2(output, temporary)
    connection = sqlite3.connect(temporary)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-131072")
    if not (merge and output.exists()):
        connection.execute("""CREATE TABLE stock_prices (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL NOT NULL,
            adj_close REAL NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, date)
        ) WITHOUT ROWID""")
    batch, source_count, inserted = [], 0, 0
    try:
        for filename, stream in iter_sources(source):
            try:
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    continue
                headers = {header: clean_header(header) for header in reader.fieldnames if header}
                fallback = symbol_from_filename(filename)
                for raw_row in reader:
                    values = row_values(raw_row, headers, fallback)
                    if values:
                        batch.append(values)
                        source_count += 1
                    if len(batch) >= batch_size:
                        connection.executemany("INSERT OR REPLACE INTO stock_prices VALUES (?,?,?,?,?,?,?,?)", batch)
                        inserted += len(batch)
                        connection.commit()
                        batch.clear()
                        if inserted and inserted % 1_000_000 == 0:
                            print(f"Indexed {inserted:,} rows…", flush=True)
            except (UnicodeError, csv.Error) as error:
                print(f"Skipping unreadable file {filename}: {error}")
            finally:
                stream.close()
        if batch:
            connection.executemany("INSERT OR REPLACE INTO stock_prices VALUES (?,?,?,?,?,?,?,?)", batch)
            inserted += len(batch)
            connection.commit()
        if not inserted:
            raise RuntimeError("No usable price rows found. Check the dataset's CSV columns and folder.")
        connection.execute("ANALYZE")
        connection.commit()
        unique_rows, tickers, bounds = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(date) || ' to ' || MAX(date) FROM stock_prices"
        ).fetchone()
        connection.close()
        os.replace(temporary, output)
        print(f"Imported {unique_rows:,} indexed rows from {source_count:,} valid records.")
        print(f"Indexed {tickers:,} tickers; coverage {bounds}.")
        print(f"Database: {output}")
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description="Import Kaggle stock CSVs into a fast local SQLite index.")
    parser.add_argument("source", type=Path, help="Kaggle .zip archive or extracted directory of stock CSVs")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"SQLite output (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--merge", action="store_true", help="Merge this source into an existing indexed database (same symbol/date replaces old values)")
    args = parser.parse_args()
    if not args.source.exists():
        parser.error(f"Dataset path does not exist: {args.source}")
    import_dataset(args.source, args.output, merge=args.merge)


if __name__ == "__main__":
    main()
