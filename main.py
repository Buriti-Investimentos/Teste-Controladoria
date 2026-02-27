# main.py
#
# Execução:
#   python main.py
#
# Leia o README para detalhes sobre o desafio e os dados de entrada/saída esperados.

from pathlib import Path


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"
    out_dir = base_dir / "out"

    out_dir.mkdir(parents=True, exist_ok=True)

    # TODO:
    # 1) Ler e consolidar CSVs em data/transactions/
    # 2) Aplicar validações do README e separar:
    #    - linhas válidas (clean_transactions)
    #    - linhas inválidas (invalid_rows, com invalid_reason)
    # 3) Deduplicar por trade_id mantendo a mais recente
    # 4) Enriquecer com close_price via data/prices.xlsx
    # 5) Gerar:
    #    - out/clean_transactions.csv
    #    - out/invalid_rows.csv
    #    - out/daily_positions.csv
    pass


if __name__ == "__main__":
    main()
