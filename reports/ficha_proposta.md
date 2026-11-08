# Ficha de proposta — Projeto Final, Parte 1

**Tema:** internações hospitalares no SUS, visão geral (sem recorte por doença).

**Fonte:** Sistema de Informações Hospitalares do SUS (SIH/SUS), arquivos de AIH reduzida (RD), DATASUS — https://datasus.saude.gov.br/transferencia-de-arquivos/ (FTP `ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/Dados/`).

**Licença:** dados públicos e abertos do Ministério da Saúde, de livre uso com citação da fonte (Lei de Acesso à Informação, Lei 12.527/2011). Os arquivos não identificam pacientes.

**Recorte:** 27 UFs, competências de jan/2022 a dez/2024 (36 meses).

**Fontes complementares:**
- IBGE/SIDRA: população por município e ano (taxas por 100 mil) e IPCA mensal (deflação do valor aprovado).
- Tabelas auxiliares do DATASUS: conversões do TabWin (`.cnv`, largura fixa) para capítulos e municípios.
- Malhas de UF e município (geobr) para mapas.

## Perguntas de pesquisa

"UF de residência" vem do município de residência (`MUNIC_RES`). "UF do hospital" é a UF do arquivo.

1. **Tendência:** como evoluiu a taxa bruta de internações por 100 mil habitantes em cada UF de residência entre 2022 e 2024? Existe sazonalidade mensal?
2. **Causas:** quais capítulos da CID-10 mais geram internações, e como isso muda entre as regiões?
3. **Valor aprovado:** qual o valor médio aprovado pelo SUS por internação (`VAL_TOT`, deflacionado pelo IPCA) por UF do hospital e por diagnóstico? Onde o gasto se concentra?
4. **Permanência e mortalidade:** como o tempo de permanência e a taxa de óbito hospitalar variam por faixa etária e sexo?
5. **Desigualdade regional:** qual a proporção de pacientes internados fora do município de residência, por UF de residência?

## Estimativa de volume

Listagem do FTP feita em 08/11/2026:

| Item | Valor |
|---|---|
| Arquivos RD no recorte | 972 de 972 esperados (27 UFs × 36 meses), nenhum faltando |
| Tamanho compactado (DBC) | ~3,0 GB |
| Linhas estimadas | ~11–12 milhões de internações por ano → ~35 milhões no total |

O volume passa bem do mínimo exigido (5 milhões de linhas, 36 meses).
