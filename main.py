"""
Consolidação de transações financeiras.

Lê os CSVs de data/transactions/, cruza com data/prices.xlsx, valida,
normaliza, deduplica e grava os resultados em out/.
"""
import csv
import glob
import math
import os
import re
import unicodedata
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
from dateutil import parser as dateutil_parser
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

try:
    import chardet
except ImportError:  # pragma: no cover
    chardet = None


# ======================================================================
# Números: parsing de valores em formatos variados (BR/US, R$, parênteses)
# ======================================================================

_CURRENCY_CHARS = re.compile(r"[R$US\s€£]", re.IGNORECASE)


def _is_valid_thousands_grouping(parts):
    """
    Valida se uma lista de grupos (resultado de split no separador de milhar)
    tem o formato correto: primeiro grupo com 1-3 dígitos, os demais com
    exatamente 3 dígitos cada, todos numéricos. Ex.: ['1', '234', '567'] válido;
    ['1', '', '234'] ou ['1', '23', '456'] inválidos.
    """
    if len(parts) < 2:
        return False
    first, *rest = parts
    if not first.isdigit() or not (1 <= len(first) <= 3):
        return False
    return all(p.isdigit() and len(p) == 3 for p in rest)


def parse_number(raw, prefer_decimal_on_ambiguous=False):
    """
    Converte uma string/num com formatação variada em float.
    Levanta ValueError se não for possível interpretar o valor como número.

    prefer_decimal_on_ambiguous: quando um único separador aparece com
    exatamente 3 dígitos depois dele (ex: "52.442"), o valor é ambíguo -
    pode ser separador de milhar (52442) ou decimal com 3 casas (52.442).
    Por padrão assume-se milhar (comum em quantidades); usar True para
    colunas onde a precisão decimal importa mais que agrupamento de milhar
    (ex: `price`).
    """
    if raw is None:
        raise ValueError("valor vazio")

    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and math.isnan(raw):
            raise ValueError("valor vazio (NaN)")
        return float(raw)

    s = str(raw).strip()
    if s == "" or s.lower() in ("nan", "none", "null", "n/a", "-"):
        raise ValueError("valor vazio")

    negative = False

    # Negativo indicado por parênteses: (123.45)
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # Remove símbolos de moeda e espaços (mas preserva sinal de menos e separadores)
    s = _CURRENCY_CHARS.sub("", s)
    s = s.strip()

    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if s == "":
        raise ValueError(f"valor não numérico: {raw!r}")

    # Mantém apenas dígitos, ponto, vírgula
    s = re.sub(r"[^0-9.,]", "", s)
    if s == "":
        raise ValueError(f"valor não numérico: {raw!r}")

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        # O último separador encontrado é o decimal; o outro é milhar
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_comma > last_dot:
            # vírgula é decimal (padrão BR): valida e remove pontos (milhar)
            integer_part, decimal_part = s.rsplit(",", 1)
            thousand_groups = integer_part.split(".")
            if len(thousand_groups) > 1 and not _is_valid_thousands_grouping(thousand_groups):
                raise ValueError(f"valor não numérico: {raw!r}")
            s = "".join(thousand_groups) + "." + decimal_part
        else:
            # ponto é decimal (padrão US): valida e remove vírgulas (milhar)
            integer_part, decimal_part = s.rsplit(".", 1)
            thousand_groups = integer_part.split(",")
            if len(thousand_groups) > 1 and not _is_valid_thousands_grouping(thousand_groups):
                raise ValueError(f"valor não numérico: {raw!r}")
            s = "".join(thousand_groups) + "." + decimal_part

    elif has_comma:
        # só vírgula(s)
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # ex: "1234,56" -> decimal
            s = parts[0] + "." + parts[1]
        elif len(parts) == 2 and len(parts[1]) == 3 and prefer_decimal_on_ambiguous:
            # ambíguo, mas preferimos decimal (ex: preço com 3 casas)
            s = parts[0] + "." + parts[1]
        else:
            # ex: "1,234" ou "1,234,567" -> separador de milhar
            if not _is_valid_thousands_grouping(parts):
                raise ValueError(f"valor não numérico: {raw!r}")
            s = "".join(parts)

    elif has_dot:
        # só ponto(s)
        parts = s.split(".")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # ex: "1234.56" -> decimal
            pass  # já está no formato certo
        elif len(parts) == 2 and len(parts[1]) == 3 and prefer_decimal_on_ambiguous:
            # ambíguo, mas preferimos decimal (ex: preço com 3 casas)
            pass
        elif len(parts) == 2 and len(parts[1]) >= 4:
            # 4+ dígitos após o ponto não pode ser separador de milhar
            # (grupo de milhar sempre tem exatamente 3 dígitos) -> decimal
            pass
        else:
            # ex: "1.234" (milhar) ou "1.234.567" -> remove pontos de milhar
            if not _is_valid_thousands_grouping(parts):
                raise ValueError(f"valor não numérico: {raw!r}")
            s = "".join(parts)
    # else: só dígitos, nada a fazer

    try:
        value = float(s)
    except ValueError:
        raise ValueError(f"valor não numérico: {raw!r}")

    return -value if negative else value


# ======================================================================
# Documentos: normalização e validação de CPF/CNPJ (com dígito verificador)
# ======================================================================

def _only_digits(raw):
    return re.sub(r"\D", "", str(raw or ""))


def _validate_cpf(digits):
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    def calc_digit(base):
        total = sum(int(d) * w for d, w in zip(base, range(len(base) + 1, 1, -1)))
        resto = (total * 10) % 11
        return 0 if resto == 10 else resto

    d1 = calc_digit(digits[:9])
    d2 = calc_digit(digits[:9] + str(d1))
    return digits[-2:] == f"{d1}{d2}"


def _validate_cnpj(digits):
    if len(digits) != 14 or digits == digits[0] * 14:
        return False

    def calc_digit(base):
        pesos_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        pesos_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        pesos = pesos_1 if len(base) == 12 else pesos_2
        total = sum(int(d) * p for d, p in zip(base, pesos))
        resto = total % 11
        return 0 if resto < 2 else 11 - resto

    d1 = calc_digit(digits[:12])
    d2 = calc_digit(digits[:12] + str(d1))
    return digits[-2:] == f"{d1}{d2}"


def _format_cpf(digits):
    return f"{digits[0:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}"


def _format_cnpj(digits):
    return f"{digits[0:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"


def classify_document(raw):
    """
    Recebe o valor bruto de client_document e retorna um dict:
        {"client_document": "...", "document_clean": "...", "document_type": "CPF"/"CNPJ"}
    Levanta ValueError se não for possível identificar/validar como CPF ou CNPJ.
    """
    digits = _only_digits(raw)

    candidates = [digits]
    if len(digits) < 11:
        candidates.append(digits.zfill(11))
    if 11 < len(digits) < 14:
        candidates.append(digits.zfill(14))

    for cand in candidates:
        if len(cand) == 11 and _validate_cpf(cand):
            return {
                "client_document": _format_cpf(cand),
                "document_clean": cand,
                "document_type": "CPF",
            }
        if len(cand) == 14 and _validate_cnpj(cand):
            return {
                "client_document": _format_cnpj(cand),
                "document_clean": cand,
                "document_type": "CNPJ",
            }

    raise ValueError(f"documento inválido: {raw!r}")


# ======================================================================
# Data e side: normalização
# ======================================================================

_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d",
    "%d.%m.%Y", "%Y%m%d", "%d/%m/%y", "%m/%d/%y",
]
_EXCEL_EPOCH = datetime(1899, 12, 30)

_BUY_ALIASES = {"buy", "b", "compra", "cp", "c", "long", "1", "bought", "compr", "compr."}
_SELL_ALIASES = {"sell", "s", "venda", "vd", "v", "short", "-1", "2", "sold", "vend", "vend."}


def _strip_accents(text):
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize_date(raw):
    """Converte um valor de data (string variada ou serial do Excel) para 'YYYY-MM-DD'."""
    if raw is None:
        raise ValueError("data vazia")

    s = str(raw).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        raise ValueError("data vazia")

    # Serial numérico do Excel (ex.: 45312)
    if re.fullmatch(r"\d{4,6}(\.0+)?", s):
        try:
            serial = int(float(s))
            if 1 <= serial <= 100000:
                dt = _EXCEL_EPOCH + timedelta(days=serial)
                return dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            pass

    s_date_only = re.split(r"[T ]", s)[0] if re.search(r"[T ]\d{1,2}:\d{2}", s) else s

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s_date_only, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    try:
        dt = dateutil_parser.parse(s_date_only, dayfirst=True, fuzzy=False)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError, TypeError):
        raise ValueError(f"data inválida: {raw!r}")


def normalize_side(raw):
    """Converte variações de lado de operação para 'BUY' ou 'SELL'."""
    if raw is None:
        raise ValueError("side vazio")
    s = str(raw).strip().lower()
    s = _strip_accents(s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    if s == "":
        raise ValueError("side vazio")
    if s in _BUY_ALIASES:
        return "BUY"
    if s in _SELL_ALIASES:
        return "SELL"
    raise ValueError(f"side inválido: {raw!r}")


class RowInvalid(Exception):
    """Sinaliza que a linha é inválida, carregando a razão padronizada."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def clean_row(row):
    """
    Recebe um dict (linha bruta do CSV) e devolve um dict com os campos
    limpos/normalizados (exceto price/gross_amount/total_costs/net_amount,
    calculados depois do enriquecimento com prices.xlsx).
    Levanta RowInvalid com a razão padronizada em caso de erro.

    Também guarda os valores ORIGINAIS (não normalizados) sob chaves
    "_raw_<coluna>", para que, se a linha vier a ser descartada mais tarde
    por invalid_price, o invalid_rows.csv mostre os dados exatamente como
    vieram no CSV (e não já normalizados), mantendo consistência com as
    demais linhas inválidas.
    """
    result = dict(row)

    for col in ("trade_id", "account_id", "client_document", "date", "ticker",
                "side", "quantity", "broker_fee", "tax", "currency"):
        result[f"_raw_{col}"] = row.get(col)

    try:
        result["date"] = normalize_date(row.get("date"))
    except ValueError:
        raise RowInvalid("invalid_date")

    try:
        result["side"] = normalize_side(row.get("side"))
    except ValueError:
        raise RowInvalid("invalid_side")

    try:
        doc_info = classify_document(row.get("client_document"))
    except ValueError:
        raise RowInvalid("invalid_document")
    result.update(doc_info)

    for col in ("quantity", "broker_fee", "tax"):
        try:
            result[col] = parse_number(row.get(col))
        except ValueError:
            raise RowInvalid("invalid_number")

    if not (result["quantity"] > 0):
        raise RowInvalid("invalid_quantity")

    if result["broker_fee"] < 0 or result["tax"] < 0:
        raise RowInvalid("invalid_costs")

    result["ticker"] = str(row.get("ticker", "")).strip().upper()
    result["account_id"] = str(row.get("account_id", "")).strip()
    result["trade_id"] = str(row.get("trade_id", "")).strip()
    result["currency"] = str(row.get("currency", "")).strip().upper()

    return result


# ======================================================================
# Leitura: CSVs de transações (encoding/delimitador variáveis) e prices.xlsx
# ======================================================================

_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp1252", "latin1"]
_DELIMITERS = [",", ";", "\t", "|"]


def _detect_encoding(path):
    with open(path, "rb") as f:
        raw = f.read(65536)
    if chardet is not None:
        result = chardet.detect(raw)
        enc = result.get("encoding")
        if enc:
            return enc
    return "utf-8"


def _read_text_with_fallback(path):
    detected = _detect_encoding(path)
    encodings = [detected] + [e for e in _ENCODINGS_TO_TRY if e != detected]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read(), enc
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, "r", encoding="latin1", newline="") as f:
        return f.read(), "latin1"


def _detect_delimiter(sample_text):
    try:
        dialect = csv.Sniffer().sniff(sample_text[:8192], delimiters="".join(_DELIMITERS))
        return dialect.delimiter
    except csv.Error:
        first_line = sample_text.splitlines()[0] if sample_text.splitlines() else ""
        counts = {d: first_line.count(d) for d in _DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def read_transactions_csv(path):
    """Lê um CSV de transações detectando encoding/delimitador automaticamente."""
    text, _encoding = _read_text_with_fallback(path)
    delimiter = _detect_delimiter(text)

    df = pd.read_csv(
        StringIO(text), sep=delimiter, dtype=str,
        keep_default_na=False, na_values=[""], engine="python",
    )
    df.columns = [c.strip() for c in df.columns]
    df["source_file"] = os.path.basename(path)
    return df


def read_all_transactions(transactions_dir):
    """Lê e concatena todos os transactions_*.csv de um diretório."""
    paths = sorted(glob.glob(os.path.join(transactions_dir, "*.csv")))
    if not paths:
        raise FileNotFoundError(f"Nenhum arquivo CSV encontrado em: {transactions_dir}")
    frames = [read_transactions_csv(p) for p in paths]
    return pd.concat(frames, ignore_index=True, sort=False)


def read_prices(prices_path):
    """Lê data/prices.xlsx e retorna DataFrame com date (YYYY-MM-DD), ticker, price."""
    df = pd.read_excel(prices_path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"date", "ticker", "price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"prices.xlsx sem as colunas obrigatórias: {missing}")

    out_dates, out_prices = [], []
    for _, row in df.iterrows():
        try:
            out_dates.append(normalize_date(row["date"]))
        except ValueError:
            out_dates.append(None)
        try:
            out_prices.append(parse_number(row["price"], prefer_decimal_on_ambiguous=True))
        except ValueError:
            out_prices.append(None)

    df["date"] = out_dates
    df["price"] = out_prices
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["date", "ticker", "price"])
    df = df.drop_duplicates(subset=["date", "ticker"], keep="last")
    return df[["date", "ticker", "price"]]


# ======================================================================
# Pipeline: limpeza -> dedup -> enriquecimento de preço -> cálculos
# ======================================================================

CLEAN_COLUMNS = [
    "trade_id", "account_id", "client_document", "document_clean",
    "document_type", "date", "ticker", "side", "quantity", "price",
    "broker_fee", "tax", "gross_amount", "total_costs", "net_amount",
    "currency", "source_file",
]

_LOTE_RE = re.compile(r"(\d+)")


def _lote_from_filename(filename):
    """Extrai o número do lote do nome do arquivo (transactions_0002.csv -> 2)."""
    m = _LOTE_RE.search(filename or "")
    return int(m.group(1)) if m else -1


def _split_valid_invalid(raw_df):
    valid_rows, invalid_rows = [], []
    for record in raw_df.to_dict(orient="records"):
        try:
            valid_rows.append(clean_row(record))
        except RowInvalid as e:
            invalid = dict(record)
            invalid["invalid_reason"] = e.reason
            invalid_rows.append(invalid)

    valid_df = pd.DataFrame(valid_rows) if valid_rows else pd.DataFrame(
        columns=["trade_id", "account_id", "client_document", "document_clean",
                 "document_type", "date", "ticker", "side", "quantity",
                 "broker_fee", "tax", "currency", "source_file"]
    )
    invalid_df = pd.DataFrame(invalid_rows) if invalid_rows else pd.DataFrame(
        columns=list(raw_df.columns) + ["invalid_reason"]
    )
    return valid_df, invalid_df


def _dedup_by_trade_id(valid_df):
    """Mantém, para cada trade_id repetido, apenas a linha do lote mais recente."""
    if valid_df.empty:
        return valid_df
    valid_df = valid_df.copy()
    valid_df["_lote"] = valid_df["source_file"].apply(_lote_from_filename)
    valid_df = valid_df.sort_values(["_lote"], kind="stable")
    valid_df = valid_df.drop_duplicates(subset=["trade_id"], keep="last")
    return valid_df.drop(columns=["_lote"])


def _enrich_with_price(valid_df, prices_df):
    """Faz merge por (date, ticker) para trazer o price; separa quem não achou preço."""
    if valid_df.empty:
        valid_df["price"] = []
        return valid_df, pd.DataFrame(columns=list(valid_df.columns) + ["invalid_reason"])

    merged = valid_df.merge(prices_df, how="left", on=["date", "ticker"])

    no_price_mask = merged["price"].isna()
    negative_price_mask = merged["price"].notna() & (merged["price"] < 0)
    bad_mask = no_price_mask | negative_price_mask

    invalid_price_df = merged.loc[bad_mask].copy()
    invalid_price_df["invalid_reason"] = "invalid_price"

    # Restaura os valores originais (não normalizados) nas colunas da
    # transação, para ficar consistente com as demais linhas inválidas
    # (que sempre mostram o dado exatamente como veio no CSV de origem).
    raw_cols = ("trade_id", "account_id", "client_document", "date", "ticker",
                "side", "quantity", "broker_fee", "tax", "currency")
    for col in raw_cols:
        raw_col = f"_raw_{col}"
        if raw_col in invalid_price_df.columns:
            invalid_price_df[col] = invalid_price_df[raw_col]

    drop_cols = [c for c in invalid_price_df.columns if c.startswith("_raw_")]
    drop_cols += ["document_clean", "document_type"]
    invalid_price_df = invalid_price_df.drop(columns=drop_cols, errors="ignore")

    ok_df = merged.loc[~bad_mask].copy()
    raw_helper_cols = [c for c in ok_df.columns if c.startswith("_raw_")]
    ok_df = ok_df.drop(columns=raw_helper_cols, errors="ignore")

    return ok_df, invalid_price_df


def _compute_derived_fields(df):
    df = df.copy()
    sign = df["side"].map({"BUY": 1, "SELL": -1})
    df["gross_amount"] = (df["quantity"] * df["price"]) * sign
    df["total_costs"] = df["broker_fee"] + df["tax"]
    df["net_amount"] = df["gross_amount"] - df["total_costs"]
    return df


def _build_daily_positions(clean_df):
    if clean_df.empty:
        return pd.DataFrame(columns=["date", "ticker", "gross_amount", "avg_trade_price", "total_costs"])

    df = clean_df.copy()
    df["_notional"] = df["price"] * df["quantity"]

    grouped = df.groupby(["date", "ticker"], as_index=False).agg(
        gross_amount=("gross_amount", "sum"),
        total_costs=("total_costs", "sum"),
        _notional_sum=("_notional", "sum"),
        _qty_sum=("quantity", "sum"),
    )
    grouped["avg_trade_price"] = grouped["_notional_sum"] / grouped["_qty_sum"]
    grouped = grouped.drop(columns=["_notional_sum", "_qty_sum"])
    grouped = grouped.sort_values(["date", "ticker"]).reset_index(drop=True)
    return grouped[["date", "ticker", "gross_amount", "avg_trade_price", "total_costs"]]


def run_pipeline(transactions_dir, prices_path):
    """Executa o pipeline completo e retorna: clean_df, invalid_df, daily_positions_df."""
    raw_df = read_all_transactions(transactions_dir)
    prices_df = read_prices(prices_path)

    valid_df, invalid_df = _split_valid_invalid(raw_df)
    valid_df = _dedup_by_trade_id(valid_df)

    enriched_df, invalid_price_df = _enrich_with_price(valid_df, prices_df)
    invalid_df = pd.concat([invalid_df, invalid_price_df], ignore_index=True, sort=False)

    clean_df = _compute_derived_fields(enriched_df)
    if not clean_df.empty:
        clean_df = clean_df[CLEAN_COLUMNS]

    daily_positions_df = _build_daily_positions(clean_df)
    return clean_df, invalid_df, daily_positions_df


# ======================================================================
# Escrita: clean_transactions.csv, invalid_rows.csv, daily_positions.xlsx
# ======================================================================

def _format_brl(value):
    """Formata um float como 'R$ 1.234,56'."""
    if value is None:
        return ""
    negative = value < 0
    value = abs(value)
    s = f"{value:,.2f}"  # ex: '1,234.56' (padrão US)
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")  # -> '1.234,56'
    formatted = f"R$ {s}"
    return f"-{formatted}" if negative else formatted


def _format_date_br(date_str):
    """Converte 'YYYY-MM-DD' para 'dd/mm/aaaa'."""
    if not date_str:
        return ""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d/%m/%Y")


def _sanitize_for_csv(df):
    """
    Remove quebras de linha/tabs internos de valores string, para garantir
    que cada linha do CSV corresponda exatamente a uma linha física do
    arquivo (evita que ferramentas simples, como Excel/Notepad em certas
    configurações, mostrem a linha "quebrada" ou desalinhada).
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"[\r\n\t]+", " ", regex=True)
                .str.strip()
                .replace({"nan": "", "None": "", "<NA>": ""})
            )
    return df


def write_clean_transactions(clean_df, out_path):
    # Usa ';' como separador: é o que o Excel em português (Brasil) espera
    # ao abrir um .csv por duplo clique (já que ',' é o separador decimal
    # nesse locale). Com ',' como separador, o Excel BR jogaria a linha
    # inteira numa coluna só.
    clean_df = _sanitize_for_csv(clean_df)
    clean_df.to_csv(out_path, index=False, encoding="utf-8-sig", sep=";")


def write_invalid_rows(invalid_df, out_path):
    # Remove colunas derivadas que só fazem sentido para linhas válidas
    # (document_clean/document_type). Mantém as colunas originais da
    # transação, mais 'price' (útil para entender o motivo invalid_price).
    invalid_df = invalid_df.drop(columns=["document_clean", "document_type"], errors="ignore")

    original_order = [
        "trade_id", "account_id", "client_document", "date", "ticker",
        "side", "quantity", "broker_fee", "tax", "currency", "price",
    ]
    cols = list(invalid_df.columns)
    ordered = [c for c in original_order if c in cols]
    ordered += [c for c in cols if c not in ordered and c not in ("invalid_reason", "source_file")]
    ordered += [c for c in ("source_file", "invalid_reason") if c in cols]
    invalid_df = invalid_df[ordered]
    invalid_df = _sanitize_for_csv(invalid_df)
    invalid_df.to_csv(out_path, index=False, encoding="utf-8-sig", sep=";")


def write_daily_positions(daily_df, out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "daily_positions"

    headers = ["date", "ticker", "gross_amount", "avg_trade_price", "total_costs"]
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="305496")
    body_font = Font(name="Arial")

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row_idx, row in enumerate(daily_df.itertuples(index=False), start=2):
        ws.cell(row=row_idx, column=1, value=_format_date_br(row.date)).font = body_font
        ws.cell(row=row_idx, column=2, value=row.ticker).font = body_font
        ws.cell(row=row_idx, column=3, value=_format_brl(row.gross_amount)).font = body_font
        ws.cell(row=row_idx, column=4, value=_format_brl(row.avg_trade_price)).font = body_font
        ws.cell(row=row_idx, column=5, value=_format_brl(row.total_costs)).font = body_font

    widths = [14, 12, 18, 18, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    wb.save(out_path)


# ======================================================================
# Entrada do programa
# ======================================================================

def main() -> None:
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"
    out_dir = base_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Implemente sua lógica aqui
    transactions_dir = data_dir / "transactions"
    prices_path = data_dir / "prices.xlsx"

    print("Lendo e processando transações...")
    clean_df, invalid_df, daily_df = run_pipeline(transactions_dir, prices_path)

    write_clean_transactions(clean_df, out_dir / "clean_transactions.csv")
    write_invalid_rows(invalid_df, out_dir / "invalid_rows.csv")
    write_daily_positions(daily_df, out_dir / "daily_positions.xlsx")

    print(f"OK: {len(clean_df)} transações válidas")
    print(f"OK: {len(invalid_df)} transações inválidas")
    print(f"OK: {len(daily_df)} posições diárias agregadas")
    print(f"Arquivos gravados em: {out_dir}")


if __name__ == "__main__":
    main()
