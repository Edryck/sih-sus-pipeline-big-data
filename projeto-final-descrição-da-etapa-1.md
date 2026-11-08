## Descrição da Etapa 1 do Projeto

Nesta Etapa 1, é escolhida uma base pública brasileira de grande volume e percorre o ciclo inicial de dados: coleta, análise exploratória, limpeza/tratamento e visualização.

A base escolhida: Internações hospitalares no SUS - https://datasus.saude.gov.br/transferencia-de-arquivos/

O produto final é um notebook reprodutível, uma base tratada e um relatório com perguntas respondidas pelos dados. A Parte 2 (modelagem/insights avançados) partirá dessa base.

## Organização

| Semana | Etapa | Entregável |
|---|---|---|
| 1 | Escolha do tema e proposta | Ficha com tema, fonte, recorte (anos/UFs), 5 perguntas de pesquisa e estimativa de volume |
| 2 | Coleta | Script de coleta + dicionário de dados + base bruta em Parquet |
| 3 | Análise exploratória | Seção de EDA no notebook com diagnóstico de qualidade |
| 4 | Limpeza e tratamento | Base tratada + log de decisões de limpeza |
| 5 | Visualizações e relatório | Notebook final, relatório (máx. 10 páginas) |

Requisitos mínimos da base escolhida:

- Pelo menos 5 milhões de linhas;
- Pelo menos 3 anos (ou 36 meses) de cobertura, para permitir análise temporal;
- Integração com pelo menos uma segunda fonte (ex.: população do IBGE para calcular taxas, tabela de municípios para mapas).

O repositório (GitHub) deve ter a estrutura data/bronze, data/silver, data/gold, notebooks, src, reports e um README explicando como reproduzir tudo. Dados brutos grandes não vão para o repositório; o script de coleta deve recriá-los.

## Etapa 1 - Coleta de dados

A coleta deve ser automatizada e reprodutível: ninguém baixa arquivo "na mão" sem que o script consiga refazer o processo.

## Tarefas:

1. Identificar a fonte oficial, a licença de uso e a documentação (dicionário de variáveis, layout dos arquivos);
2. Escrever um script (src/coleta.py ou .R) que baixe todos os arquivos do recorte, com tratamento de falhas (repetição em caso de erro, verificação de arquivos já baixados);
3. Lidar com os formatos de origem: CSV com ;, encoding latin-1, ZIP, DBC (DATASUS), JSON de API, arquivos de largura fixa;
4. Converter os dados brutos para Parquet (particionado por ano e/ou UF) e comparar tamanho em disco e tempo de leitura CSV × Parquet;
5. Registrar metadados da coleta: data de download, URL, número de arquivos, linhas e colunas por arquivo, tamanho total.

Entregáveis: script de coleta, tabela de metadados, dicionário de dados em português com tipo, descrição e domínio de cada variável usada. Perguntas para o relatório: Qual o volume total coletado? Quanto o Parquet reduziu? Quais dificuldades

a fonte impôs (instabilidade, mudança de layout entre anos)?

## Etapa 2 - Análise exploratória de dados

A EDA vem antes da limpeza: o objetivo é conhecer a base e encontrar os problemas que a Etapa 3 vai resolver.

## Tarefas:

1. Visão geral: dimensões, tipos inferidos × tipos esperados, uso de memória por coluna;
2. Qualidade: percentual de ausentes por coluna e por ano, duplicados, valores fora do domínio do dicionário (códigos inválidos, datas impossíveis, idades negativas);
3. Univariada: medidas de posição e dispersão para numéricas; frequências e cardinalidade para categóricas; identificação de outliers (IQR, z-score) com discussão se são erro ou fenômeno real;
4. Bivariada e temporal: evolução mensal/anual, comparação entre UFs ou regiões, correlações entre variáveis numéricas, tabelas cruzadas;
5. Responder preliminarmente às 5 perguntas de pesquisa da proposta.

Entregável: seção de EDA no notebook, terminando com uma lista priorizada de problemas de qualidade encontrados.

## Etapa 3 - Limpeza e tratamento

Cada problema listado na EDA recebe uma decisão justificada, e o efeito de cada decisão é medido (linhas antes/depois).

## Tarefas:

1. Padronizar nomes de colunas (snake\_case, sem acentos) e tipos (datas, categóricas, inteiros com tamanho adequado);
2. Decodificar códigos usando o dicionário (ex.: CID-10, código IBGE de município, CNAE, código de curso);
3. Tratar ausentes: remover, imputar ou manter como categoria própria, explicando o critério e o viés que cada escolha pode introduzir;
4. Remover ou marcar duplicados e registros inválidos; tratar outliers com regra explícita;
5. Harmonizar diferenças entre anos (colunas renomeadas, categorias que mudaram de código);
6. Criar variáveis derivadas: faixas etárias, região, mês/ano, taxas por 100 mil habitantes (com população do IBGE);
7. Integrar a segunda fonte por chave comum e verificar quantos registros não combinaram;
8. Salvar a base tratada em Parquet particionado em data/silver.

Entregáveis: base tratada e um log de limpeza em tabela: problema · decisão · justificativa · linhas afetadas.

## Etapa 4 - Visualizações

Cada visualização responde a uma pergunta escrita no título do gráfico (ex.: "Internações por dengue triplicaram no Sudeste entre 2022 e 2024"), não apenas nomeia o tema. Mínimo de 8 visualizações, cobrindo:

- Série temporal (linha) com sazonalidade ou tendência;
- Comparação entre categorias (barras ordenadas);
- Distribuição (histograma, boxplot ou violino), incluindo comparação entre grupo;s
- Relação entre duas variáveis (dispersão ou mapa de calor de correlação);
- Mapa coroplético por UF ou município (geobr, geopandas ou plotly), usando taxas e não contagens absolutas;
- Um gráfico "antes × depois" mostrando o efeito da limpeza.

## Relatório e avaliação

O relatório (máx. 10 páginas) segue a ordem: contexto e perguntas → fonte e volume → principais problemas de qualidade e decisões → resultados com visualizações → limitações → próximos passos para a Parte 2.