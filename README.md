# Internações hospitalares no SUS (SIH/RD) — Projeto Final, Parte 1

Pipeline reprodutível sobre as internações do SUS (SIH/RD, DATASUS): coleta, análise exploratória, limpeza, visualizações e um dashboard no Power BI. Recorte: 27 UFs, 2022–2024.

## Estrutura

```
src/         coleta.py, tratamento.py, gold.py
notebooks/   projeto_final.ipynb (benchmark, EDA, limpeza, gráficos)
data/        bronze/ silver/ gold/  (não versionados; recriados pelos scripts)
reports/     ficha, dicionário, metadados, log de limpeza, tutorial do Power BI, relatório
tests/       testes pytest
```

## Pré-requisitos

- Linux, Python 3.12 (testado), Java 17 ou 21 (testado com 21)
- ~15 GB livres em disco, 16 GB+ de RAM
- Para compilar o `pyreaddbc`: `sudo apt install build-essential python3-dev`

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
```

## Ordem de execução

1. `python src/coleta.py` — baixa SIH/RD, IBGE, IPCA, tabelas auxiliares → `data/bronze`
2. `notebooks/projeto_final.ipynb` seções 1–2 — benchmark e EDA
3. `python src/tratamento.py` — bronze → silver + `reports/log_limpeza.csv`
4. `python src/gold.py` — silver → gold (modelo estrela) + carga no PostgreSQL
5. `notebooks/projeto_final.ipynb` seções 3–4 — limpeza e gráficos

Para testar rápido num subconjunto: `--ufs AC --anos 2022`.

## Coleta (`src/coleta.py`)

```bash
.venv/bin/python src/coleta.py                 # tudo (~3 GB de DBC, ~70 min a ~700 KB/s)
.venv/bin/python src/coleta.py --only rd       # só SIH/RD (também: ibge, aux, geo)
```

- Pode rodar de novo à vontade: arquivos completos são pulados, e downloads interrompidos (`.part`) são refeitos.
- Resultado de referência (08/11/2026): 972 arquivos, 40.048.092 linhas, 113 colunas em todos os anos. São 3,0 GB em DBC e 2,1 GB em Parquet; os DBC originais ficam em `data/bronze/dbc/`.
- Metadados em `reports/metadados_coleta.csv`; dicionário em `reports/dicionario_dados.md`.

*(Seções de coleta, banco no Ubuntu Server e Power BI são completadas conforme as fases avançam.)*
