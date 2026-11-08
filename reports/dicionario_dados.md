# Dicionário de dados

## Fontes, documentação e licença

| Fonte | Onde | Formato | Uso |
|---|---|---|---|
| SIH/SUS — AIH reduzida (RD) | `ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/RD{UF}{AA}{MM}.dbc` | DBC (DBF comprimido), latin-1 | base principal |
| Layout oficial do RD | `ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Doc/IT_SIHSUS_1603.pdf` | PDF | tipos e domínios abaixo |
| Tabelas TabWin do SIH | `ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Auxiliar/TAB_SIH.zip` | ZIP com `.cnv` de largura fixa, latin-1 | CID-10 por categoria (`CID10_3D.CNV`), capítulos (`CID10CAP.CNV`), municípios (`BR_municip.cnv`) |
| IBGE/SIDRA — Censo 2022, tabela 4709, variável 93 | `apisidra.ibge.gov.br/values/t/4709/n6/all/v/93/p/2022` | JSON | população 2022 |
| IBGE/SIDRA — Estimativas, tabela 6579, variável 9324 | `apisidra.ibge.gov.br/values/t/6579/n6/all/v/9324/p/2024` | JSON | população 2024 |
| IBGE/SIDRA — IPCA, tabela 1737, variável 2266 (número-índice) | `apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266/p/202112-202412` | JSON | deflação de `VAL_TOT` |
| geobr (IPEA) | `geobr.read_state(2020)`, `geobr.read_municipality(2022)` | GeoPackage | mapas |

**Licença:** os dados do DATASUS e do IBGE são públicos e de livre uso, com citação da fonte (Lei 12.527/2011, Lei de Acesso à Informação, e política de dados abertos do governo federal). Os arquivos do RD não identificam pacientes.

**População por ano:** 2022 vem do Censo e 2024 da estimativa oficial. A tabela 6579 pula 2022 e 2023 (os períodos disponíveis são `…2021, 2024, 2025, 2026`), então **2023 é interpolado linearmente** entre 2022 e 2024 por município. A coluna `fonte` marca cada valor (`censo`, `estimativa`, `interpolado`).

**Sobre a largura fixa:** os arquivos `.cnv` do TabWin são texto de largura fixa. O código fica nas colunas 1–7, a descrição nas colunas 10–60 e a lista de códigos ou faixas (ex.: `A00-B99,`) da coluna 61 em diante.

## Variáveis do RD usadas

O RD tem 113 colunas; o bronze guarda todas, como texto. A tabela abaixo lista as que entram na silver. Tipos e domínios seguem o layout `IT_SIHSUS_1603.pdf`.

| Variável | Tipo esperado | Descrição | Domínio |
|---|---|---|---|
| `ANO_CMPT` | inteiro | ano de competência (processamento) da AIH | 2022–2024 |
| `MES_CMPT` | inteiro | mês de competência | 1–12 |
| `UF_ZI` | código (6) | **município gestor** (não é UF) | código IBGE 6 dígitos; não usado como UF |
| `N_AIH` | código (13) | número da AIH | identificador; repetições = continuação/duplicata |
| `IDENT` | categórico | tipo de AIH | 1 = principal, 3 = continuação, 5 = longa permanência |
| `CNES` | código (7) | estabelecimento de saúde | código CNES |
| `MUNIC_RES` | código (6) | município de residência do paciente | código IBGE 6 dígitos |
| `MUNIC_MOV` | código (6) | município do estabelecimento | código IBGE 6 dígitos |
| `NASC` | data `AAAAMMDD` | data de nascimento | data válida ≤ `DT_INTER` |
| `SEXO` | categórico | sexo | 1 = masculino, 2 e 3 = feminino, 0/9 = ignorado |
| `IDADE` | inteiro | idade na unidade de `COD_IDADE` | ≥ 0 |
| `COD_IDADE` | categórico | unidade da idade | 2 = dias, 3 = meses, 4 = anos, 5 = anos acima de 100 (centenas), 0 = ignorado |
| `DT_INTER` | data `AAAAMMDD` | data de internação | data válida |
| `DT_SAIDA` | data `AAAAMMDD` | data de saída | ≥ `DT_INTER` |
| `DIAG_PRINC` | código CID-10 | diagnóstico principal | categoria = 3 primeiros caracteres, presente em `CID10_3D.CNV` |
| `PROC_REA` | código (10) | procedimento realizado (SIGTAP) | código SIGTAP |
| `CAR_INT` | categórico | caráter da internação | 01 = eletivo, 02 = urgência, 03–06 = acidentes/lesões, 99 = ignorado |
| `DIAS_PERM` | inteiro | dias de permanência | ≥ 0 |
| `UTI_MES_TO` | inteiro | dias de UTI no mês | ≥ 0 |
| `VAL_TOT` | decimal (R$) | **valor aprovado/reembolsado pelo SUS** (nominal, não é custo real) | ≥ 0 |
| `MORTE` | categórico | óbito durante a internação | 0 = não, 1 = sim |

## Variáveis derivadas (silver)

| Variável | Tipo | Regra |
|---|---|---|
| `uf_hospital` | texto (2) | UF do arquivo de origem (`RD{UF}…`) |
| `uf_residencia` | texto (2) | 2 primeiros dígitos de `MUNIC_RES` mapeados para a sigla; `ignorado` se inválido |
| `regiao_hospital`, `regiao_residencia` | texto | região da UF correspondente |
| `idade_anos` | inteiro | `IDADE` convertida por `COD_IDADE` |
| `faixa_etaria` | categórico | 0-4, 5-9, 10-14, 15-19, 20-29, …, 80+, ignorado |
| `cid_capitulo` | texto | capítulo CID-10 da categoria de `DIAG_PRINC` |
| `ano_mes` | texto `AAAA-MM` | de `ANO_CMPT` e `MES_CMPT` |
| `internado_fora_municipio` | booleano | `MUNIC_RES` ≠ `MUNIC_MOV` |
| `obito` | booleano | `MORTE` = 1 |
| `val_tot_real` | decimal (R$ dez/2024) | `VAL_TOT × IPCA[2024-12] / IPCA[ano_mes]` |
| `populacao` | inteiro | população do município de residência no ano (IBGE) |
| `longa_permanencia` | booleano | `IDENT` = 5 |
| `outlier_val_tot`, `outlier_dias_perm` | booleano | fora de 1,5 × IQR dentro do procedimento |

*Os domínios de `SEXO`, `CAR_INT` e `COD_IDADE` precisam ser conferidos linha a linha com o PDF do layout. A EDA mede quanto dos dados cai fora deles.*
