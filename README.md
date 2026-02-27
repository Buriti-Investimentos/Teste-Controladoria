# Consolidação de transações financeiras

## Execução

A partir da raiz do repositório:

```bash
python main.py
```

O programa deve ler os dados em `data/` e gravar os resultados em `out/` (criando a pasta se necessário).

---

## O que o programa precisa fazer

1. Ler e consolidar os arquivos CSVs de `data/transactions/`
2. Limpar e normalizar datas e números
3. Validar os documentos da coluna `client_document`
4. Deduplicar transações repetidas de `trade_id` mantendo a mais recente
5. Enriquecer dados com `close_price` via `data/prices.xlsx`
6. Gerar os arquivos de retorno em `out/` (formatos e colunas abaixo)

---

## Entradas

### 1) CSVs de transações

Pasta: `data/transactions/`
Arquivos: `transactions_[lote].csv` (ex: `transactions_0001.csv`)

Observações do dataset:

- O separador pode ser `,` ou `;`
- O encoding pode variar (`utf-8`, `utf-8-sig`, `latin1`)
- As datas e números podem vir em formatos diferentes
- A coluna `side` pode vir com ruído (maiúscula/minúscula, espaços, abreviações)

Colunas mínimas esperadas nos CSVs:

- `trade_id`, `account_id`, `client_document`, `date`, `ticker`, `side`,
  `quantity`, `price`, `broker_fee`, `tax`, `currency`

### 2) Excel de preços

Arquivo: `data/prices.xlsx`

Colunas mínimas esperadas nos CSVs:

- `date` (YYYY-MM-DD), `ticker`, `close_price`

---

## Validações

### 1) Datas

Poderão ser recebidas os seguintes formatos de data:

- `DD/MM/YYYY`
- `YYYY-MM-DD`
- `YYYY/MM/DD`
- `DD-MM-YYYY`

Datas válidas devem ser convertidas para o formato `YYYY-MM-DD`. Caso contrário, a linha deverá ser considerada inválida (`invalid_date`).

### 2) Side

Poderão ser recebidas variações de `BUY` e `SELL`:

- BUY: `BUY`, `buy`, `B`, `COMPRA`, `Compra`
- SELL: `SELL`, `sell`, `S`, `VENDA`, `Venda`

Essas variações devem ser convertidas para `BUY` ou `SELL`. Caso contrário, a linha deverá ser considerada inválida (`invalid_side`).

### 3) Documentos

O documento do cliente (`client_document`) pode vir "sujos", e conter ou não pontuação. Deverão normalizados e classificados mantendo as seguintes colunas:
- `client_document`: valor do documento formatado (ex: `123.456.789-00` ou `12.345.678/0001-00`)
- `document_clean`: o número do documento apenas com dígitos
- `document_type` com os valores `CPF` ou `CNPJ`.

Caso não sejá possível identificar, a linha deverá ser considerada inválida (`invalid_document`).

### 4) Números

Poderão ser recebidos números com diferentes formatos, como:

- `10,35`
- `1.234,56`
- `1,234.56`
- `1,000`

Esses valores devem ser convertidos para um formato numérico padrão (ex: `1234.56`). Caso contrário, a linha deverá ser considerada inválida (`invalid_number`).

Algumas colunas, ainda, devem obedecer a regras específicas:

- `quantity` > 0 (senão: `invalid_quantity`)
- `price` >= 0 (senão: `invalid_price`)
- `broker_fee` >= 0 e `tax` >= 0 (senão: `invalid_costs`)

Por fim, deverão ser calculados os seguintes campos:

- `gross_amount` = `quantity * price` (positivo em caso de BUY, negativo para SELL)
- `total_costs` = `broker_fee + tax`
- `net_amount` = `gross_amount - total_costs`

---

## Saídas

### 1) `clean_transactions.csv`

Deverá conter todas as linhas válidas de transações, com as seguintes colunas:

1. `trade_id`
2. `account_id`
3. `client_document`
4. `document_clean` (numérico)
5. `document_type`
6. `date` (YYYY-MM-DD)
7. `ticker`
8. `side` (`BUY`/`SELL`)
9. `quantity` (numérico)
10. `price` (numérico)
11. `broker_fee` (numérico)
12. `tax` (numérico)
13. `gross_amount` (numérico)
14. `total_costs` (numérico)
15. `net_amount` (numérico)
16. `currency` (numérico)
17. `source_file` (nome do CSV de origem)

### 2) `out/invalid_rows.csv`

Deverá conter todas as linhas descartadas como inválidas, contendo:

- as colunas originais da transação
- `invalid_reason` (`invalid_date`/`invalid_side`/`invalid_document`/`invalid_number`/`invalid_quantity`/`invalid_price`/`invalid_costs`)
- `source_file`

### 3) `out/daily_positions.csv`

Deverá conter a agrregação diária das posições por `ticker`, com as seguintes colunas:

1. `date`
2. `ticker`
3. `gross_amount` (somatório)
4. `avg_trade_price` (média ponderada de `price` por `quantity`)
5. `total_costs` (somatório)
