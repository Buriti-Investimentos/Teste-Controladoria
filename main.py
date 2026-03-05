import pandas as pd
import os
import glob
from datetime import datetime

# caminhos
DATA_DIR = "Dados/Transações"
PRICES_FILE = "Dados/prices.xlsx"
OUT_DIR = "fora"

os.makedirs(OUT_DIR, exist_ok=True)

print("=== Consolidação de transações financeiras ===")

# =========================
# LER TODOS OS CSVs
# =========================

files = glob.glob(os.path.join(DATA_DIR, "transactions_*.csv"))

dfs = []

for file in files:
    print("Lendo:", file)

    try:
        df = pd.read_csv(file, sep=None, engine="python")
    except:
        df = pd.read_csv(file, sep=";")

    df["source_file"] = os.path.basename(file)

    dfs.append(df)

transactions = pd.concat(dfs, ignore_index=True)

print("Linhas lidas:", len(transactions))


# =========================
# NORMALIZAR CAMPOS
# =========================

# datas
transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")

# side
transactions["side"] = transactions["side"].str.upper().str.strip()
transactions.loc[transactions["side"].str.contains("BUY"), "side"] = "BUY"
transactions.loc[transactions["side"].str.contains("SELL"), "side"] = "SELL"

# números
cols = ["quantity", "broker_fee", "tax"]

for c in cols:
    transactions[c] = (
        transactions[c]
        .astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    transactions[c] = pd.to_numeric(transactions[c], errors="coerce")


# =========================
# LER PREÇOS
# =========================

prices = pd.read_excel(PRICES_FILE)

prices["date"] = pd.to_datetime(prices["date"])

transactions["ticker"] = transactions["ticker"].str.upper()
prices["ticker"] = prices["ticker"].str.upper()

transactions = transactions.merge(prices, on=["date", "ticker"], how="left")


# =========================
# CALCULOS
# =========================

transactions["gross_amount"] = transactions["quantity"] * transactions["price"]

transactions.loc[transactions["side"] == "SELL", "gross_amount"] *= -1

transactions["total_costs"] = transactions["broker_fee"] + transactions["tax"]

transactions["net_amount"] = transactions["gross_amount"] - transactions["total_costs"]


# =========================
# REMOVER LINHAS INVALIDAS
# =========================

invalid = transactions[
    (transactions["date"].isna()) |
    (transactions["side"].isna()) |
    (transactions["quantity"] <= 0) |
    (transactions["price"] < 0)
]

valid = transactions.drop(invalid.index)


# =========================
# DEDUPLICAR
# =========================

valid = valid.sort_values("source_file").drop_duplicates("trade_id", keep="last")


# =========================
# SALVAR SAIDAS
# =========================

valid.to_csv(os.path.join(OUT_DIR, "clean_transactions.csv"), index=False)

invalid.to_csv(os.path.join(OUT_DIR, "invalid_rows.csv"), index=False)


# =========================
# DAILY POSITIONS
# =========================

daily = valid.groupby(["date", "ticker"]).agg(
    gross_amount=("gross_amount", "sum"),
    total_costs=("total_costs", "sum"),
    avg_trade_price=("price", "mean")
).reset_index()

daily.to_excel(os.path.join(OUT_DIR, "daily_positions.xlsx"), index=False)


print("Arquivos gerados em:", OUT_DIR)
