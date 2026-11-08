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

## Limpeza (`src/tratamento.py`)

```bash
.venv/bin/python src/tratamento.py               # base inteira, ~23 min; checa 36 meses, 27 UFs e joins
.venv/bin/python src/tratamento.py --ufs AC --anos 2022   # teste rápido
```

Saídas: `data/silver/sih_rd/ano=/uf_hospital=` (Parquet tipado, 35 colunas, ~1,6 GB) e `reports/log_limpeza.csv` (problema · decisão · justificativa · linhas antes/depois/afetadas), regravado a cada execução.

## Gold (`src/gold.py`)

```bash
.venv/bin/python src/gold.py                 # ~5 min com carga no Postgres
.venv/bin/python src/gold.py --sem-postgres  # só Parquet + CSV
```

Modelo estrela, grão mês × município de residência × UF do hospital × capítulo CID × faixa etária × sexo. AIH de longa permanência (`IDENT=5`) fica fora. Saídas: `data/gold/<tabela>/` (Parquet) e `data/gold/csv/<tabela>.csv` (`;`, UTF-8), e no Postgres o schema `dw`.

| Tabela | Linhas |
|---|---|
| `fato_internacao_mensal` | 12.742.448 (soma = 39.630.392 internações) |
| `dim_tempo` | 36 |
| `dim_municipio` | 5.573 (inclui `-1` = ignorado; lat/lon do centróide) |
| `dim_cid_capitulo` | 21 (cap. XX, causas externas, não aparece como diagnóstico principal) |
| `dim_faixa_etaria` | 12 |
| `populacao_municipio_ano` | 16.710 |

O script confere que a soma da fato bate com a silver e que não há município órfão. A carga só roda com o `.env` completo; ela recria as tabelas e cria PK, FK e índices.

## Banco no Ubuntu Server

```bash
sudo -u postgres psql -c "CREATE USER sih_user WITH PASSWORD '...'" -c "CREATE DATABASE sih OWNER sih_user"
# /etc/postgresql/*/main/postgresql.conf
listen_addresses = '*'
# /etc/postgresql/*/main/pg_hba.conf
host  sih  sih_user  192.168.3.0/24  scram-sha-256
sudo ufw allow from 192.168.3.0/24 to any port 5432
sudo systemctl restart postgresql
psql -h <ip> -U sih_user sih -c 'select count(*) from dw.fato_internacao_mensal'
```

Credenciais vão no `.env` (fora do git), seguindo o `.env.example`.

## Visualizações, Power BI e relatório

- `notebooks/projeto_final.ipynb` seção 4: 9 gráficos com título calculado dos dados, salvos em `reports/figuras/` (o notebook inteiro roda em ~25 min).
- `reports/tutorial_powerbi.md`: passo a passo do dashboard (VM, conexão no Postgres, modelo, DAX, 2 páginas, plano B por Samba).
- `reports/relatorio.md` → `reports/relatorio.pdf` (6 páginas). Para gerar o PDF sem LaTeX:

```bash
cd reports
../.venv/lib/python3.12/site-packages/pypandoc/files/pandoc relatorio.md -s --css relatorio.css -o /tmp/relatorio.html
../.venv/bin/python -c "import weasyprint; weasyprint.HTML('/tmp/relatorio.html', base_url='.').write_pdf('relatorio.pdf', stylesheets=['relatorio.css'])"
```

## Reprodução de ponta a ponta

| Etapa | Comando | Tempo aprox. |
|---|---|---|
| Coleta | `.venv/bin/python src/coleta.py` | ~70 min (FTP) |
| Limpeza | `.venv/bin/python src/tratamento.py` | ~23 min |
| Gold + Postgres | `.venv/bin/python src/gold.py` | ~5 min |
| Notebook | `.venv/bin/jupyter nbconvert --to notebook --execute --inplace notebooks/projeto_final.ipynb` | ~25 min |
| Relatório | comandos acima | < 1 min |
