# Tutorial Power BI — Internações SUS 2022–2024

Passo a passo para montar o dashboard a partir do schema `dw` no PostgreSQL do Ubuntu Server. Pré-requisito: `python src/gold.py` já rodou e a carga terminou com "carga no PostgreSQL (schema dw) ok".

Os nomes de menu seguem o Power BI Desktop em português. Em versões novas a posição de alguns botões muda, mas os nomes continuam os mesmos.

## 1. Windows 10 para rodar o Power BI

O Power BI Desktop só roda no Windows. Há duas opções.

**Opção A: VM no VirtualBox (dentro do Ubuntu desktop)**

1. VirtualBox → **Novo** → Windows 10 (64-bit), 4 GB+ de RAM, 2 CPUs, disco de 64 GB.
2. **Configurações → Rede → Adaptador 1 → Conectado a: Placa em modo Bridge**, escolhendo a placa física. Assim a VM ganha IP na mesma rede `192.168.3.0/24` do servidor. Em NAT, a VM não alcança o Postgres sem redirecionar porta.
3. Instale o Windows. Depois, no Prompt de Comando, teste a rede: `ping 192.168.3.154` e `Test-NetConnection 192.168.3.154 -Port 5432` (no PowerShell). O teste deve mostrar `TcpTestSucceeded : True`.

**Opção B: dual boot.** Inicie no Windows 10, que já está na mesma rede, e faça o mesmo teste de porta.

Se a porta não responder, confira no servidor o `listen_addresses`, o `pg_hba.conf` e o `ufw` (seção "Banco no Ubuntu Server" do README).

## 2. Instalar o Power BI Desktop

1. Microsoft Store → procure **Power BI Desktop** → **Obter**. A versão da Store se atualiza sozinha.
2. O conector PostgreSQL usa o driver Npgsql, que já vem embutido nas versões recentes. Se, ao conectar, aparecer o erro "Este conector requer que um ou mais componentes adicionais sejam instalados", instale o Npgsql (github.com/npgsql/npgsql/releases, versão 4.x com a opção **Npgsql GAC Installation** marcada) e reinicie o Power BI.

## 3. Importar as tabelas do `dw`

1. **Página Inicial → Obter dados → Mais... → Banco de dados → Banco de dados PostgreSQL → Conectar**.
2. Servidor: `192.168.3.154:5432`. Banco de dados: `sih`. Modo: **Importar**. Com 12,7 milhões de linhas, a importação fica rápida e permite todas as funções DAX. DirectQuery seria lento pela rede.
3. Credenciais: aba **Banco de dados**, usuário `sih_user` e a senha do `.env`. Se perguntar sobre criptografia, aceite conexão sem criptografia (rede local).
4. No Navegador, marque as 6 tabelas `dw.*`: `dim_tempo`, `dim_municipio`, `dim_cid_capitulo`, `dim_faixa_etaria`, `fato_internacao_mensal` e `populacao_municipio_ano`.
5. Clique em **Carregar**. A carga da fato leva alguns minutos.

## 4. Modelo (relacionamentos)

1. Vá na **Exibição de modelo** (ícone do diagrama, na barra lateral esquerda).
2. O Power BI pode ter criado relacionamentos sozinho. Apague os que não estiverem na lista abaixo (clique na linha → Delete).
3. Crie cada relação arrastando a coluna da fato até a coluna da dimensão. Todas são **Muitos para um (\*:1)**, com **Direção do filtro cruzado: Único**.

| De (lado muitos) | Para (lado um) |
|---|---|
| `fato_internacao_mensal[id_tempo]` | `dim_tempo[id_tempo]` |
| `fato_internacao_mensal[id_municipio_res]` | `dim_municipio[id_municipio]` |
| `fato_internacao_mensal[id_cid_capitulo]` | `dim_cid_capitulo[id_cid_capitulo]` |
| `fato_internacao_mensal[id_faixa_etaria]` | `dim_faixa_etaria[id_faixa_etaria]` |
| `populacao_municipio_ano[id_municipio]` | `dim_municipio[id_municipio]` |

4. `populacao_municipio_ano` **não** se liga a `dim_tempo`. O grão dela é o ano, e o de `dim_tempo` é o mês. O filtro de ano chega até ela pela medida `População`, usando `TREATAS` (seção 5).
5. Em `dim_faixa_etaria`, selecione a coluna `faixa` → **Ferramentas de coluna → Classificar por coluna → ordem**. Faça o mesmo em `dim_tempo[nome_mes]`, ordenando por `mes`.
6. Em `dim_municipio`, selecione `uf` → **Ferramentas de coluna → Categoria de dados → Estado ou Província**. Em `lat` use **Latitude** e em `lon` use **Longitude**.

## 5. Medidas DAX

Crie uma tabela só para as medidas: **Página Inicial → Inserir dados**, chame a tabela de `Medidas` e clique em **Carregar**. Depois, com ela selecionada: **Ferramentas de tabela → Nova medida**. Crie uma medida por vez:

```DAX
Internações = SUM(fato_internacao_mensal[qtd_internacoes])
```
```DAX
População = AVERAGEX(VALUES(dim_tempo[ano]),
    CALCULATE(SUM(populacao_municipio_ano[populacao]),
        TREATAS({dim_tempo[ano]}, populacao_municipio_ano[ano])))
```
```DAX
Meses = DISTINCTCOUNT(dim_tempo[id_tempo])
```
```DAX
Taxa por 100 mil (anual) = DIVIDE([Internações] / ([Meses] / 12), [População]) * 100000
```
```DAX
Valor médio aprovado = DIVIDE(SUM(fato_internacao_mensal[soma_val_tot_real]), [Internações])
```
```DAX
Letalidade % = DIVIDE(SUM(fato_internacao_mensal[qtd_obitos]), [Internações]) * 100
```
```DAX
Permanência média = DIVIDE(SUM(fato_internacao_mensal[soma_dias_perm]), [Internações])
```
```DAX
% fora do município = DIVIDE(SUM(fato_internacao_mensal[qtd_fora_municipio]), [Internações]) * 100
```

Formate `Valor médio aprovado` como moeda (**Ferramentas de medida → Formato → Moeda**, R$, 2 casas). O valor é o **valor aprovado pelo SUS, a preços de dez/2024**, e não o custo real da internação. Use esse rótulo nos visuais.

> **Por que a população é média por ano, e não soma?**
> A fato é mensal, mas a população é uma foto anual. Se você somasse a população junto com os meses, 2022 entraria 12 vezes e a taxa ficaria 12× menor. `População` pega, para cada ano no filtro, a soma da população dos municípios filtrados e tira a média entre os anos. `TREATAS` aplica os anos de `dim_tempo` à coluna `ano` da população, sem precisar de relacionamento.
>
> **Por que `Meses / 12`?** `Internações / (Meses/12)` converte qualquer seleção para "internações por ano". Com 1 mês, multiplica por 12; com um trimestre, por 4; com os 3 anos, divide por 3. O resultado é exato para qualquer recorte de tempo.

> **⚠ Atenção: taxa e os filtros de faixa etária, sexo e CID**
> A população do IBGE usada aqui não tem quebra por idade, sexo ou causa. Se um filtro de `dim_faixa_etaria`, `sexo` ou `dim_cid_capitulo` atingir um visual de taxa, só o numerador muda. O resultado vira, por exemplo, "internações de idosos por 100 mil habitantes **de todas as idades**", o que não é uma taxa por faixa.
> Nos visuais de taxa, desligue essas interações: selecione a segmentação de faixa etária, sexo ou CID → **Formatar → Editar interações** → no visual de taxa, clique no ícone **Nenhum** (círculo com traço). Coloque no subtítulo do visual: "Taxa bruta, população total; não filtrada por idade, sexo ou CID".

## 6. Medida rápida: total acumulado

1. Na tabela `Medidas`: **Página Inicial → Medidas rápidas** (ou clique direito em `Medidas` → **Nova medida rápida**).
2. Cálculo: **Totais → Total acumulado**. Valor base: `Internações`. Campo: `dim_tempo[id_tempo]`.
3. Clique em **Adicionar**. O Power BI gera a medida `Internações total acumulado em id_tempo`, e você pode ver o DAX dela na barra de fórmulas.

## 7. Coluna calculada: porte do município

Em **Exibição de dados**, selecione `dim_municipio` → **Ferramentas de tabela → Nova coluna**:

```DAX
Porte =
VAR p = CALCULATE(AVERAGE(populacao_municipio_ano[populacao]))
RETURN SWITCH(TRUE(),
    dim_municipio[id_municipio] = -1, "ignorado",
    p < 20000, "1. até 20 mil",
    p < 100000, "2. 20–100 mil",
    p < 500000, "3. 100–500 mil",
    "4. 500 mil+")
```

O `CALCULATE` converte a linha atual num filtro. Assim, a média pega só a população daquele município nos 3 anos. Diferente de uma medida, a coluna é calculada uma vez, na carga, e pode ser usada como eixo ou segmentação.

## 8. Coluna personalizada no Power Query: semestre

1. **Página Inicial → Transformar dados** (abre o Power Query).
2. Selecione a consulta `dim_tempo` → **Adicionar Coluna → Coluna Personalizada**.
3. Nome: `Semestre`. Fórmula:
   ```
   Text.From([ano]) & "-S" & (if [mes] <= 6 then "1" else "2")
   ```
4. **OK** → **Página Inicial → Fechar e Aplicar**.

A diferença para a coluna calculada é que esta nasce na etapa de carga (linguagem M) e é refeita a cada atualização. A coluna DAX é calculada depois, já no modelo.

## 9. Tabela calculada: calendário

**Modelagem → Nova tabela**:

```DAX
Calendario = ADDCOLUMNS(CALENDAR(DATE(2022, 1, 1), DATE(2024, 12, 31)),
    "id_tempo", YEAR([Date]) * 100 + MONTH([Date]),
    "Dia da semana", FORMAT([Date], "dddd"))
```

`CALENDAR` gera uma linha por dia do período (1.096 dias). Neste projeto o grão da fato é mensal, então a tabela serve para mostrar o recurso. Se você quiser usá-la, ligue `Calendario[id_tempo]` → `dim_tempo[id_tempo]` (muitos para um). Em projetos com data diária, ela seria a dimensão de tempo, marcada em **Ferramentas de tabela → Marcar como tabela de data**.

## 10. Cálculo visual: média móvel de 3 meses

1. Crie um **Gráfico de linhas**: eixo X `dim_tempo[id_tempo]` (tipo Categórico; clique direito no campo → **Não resumir**), eixo Y `[Internações]`.
2. Com o visual selecionado: **Página Inicial → Novo cálculo** (ou clique direito no visual → **Novo cálculo**).
3. No editor de cálculo visual, digite:
   ```DAX
   Média móvel 3 meses = MOVINGAVERAGE([Internações], 3)
   ```
4. Clique em **Voltar ao relatório**. A linha suaviza a sazonalidade mensal. O cálculo visual existe só dentro desse visual e não aparece no modelo.

## 11. Dashboard (2 páginas)

**Página 1 — "Panorama"**

1. Segmentações (**Visualizações → Segmentação de dados**): `dim_tempo[ano]` e `dim_municipio[regiao]`, em estilo **Bloco** (**Formatar → Configurações de segmentação → Estilo**).
2. Quatro **Cartões**: `Internações`, `Taxa por 100 mil (anual)`, `Letalidade %` e `Valor médio aprovado`.
3. **Gráfico de linhas** da seção 10 (internações e média móvel).
4. **Mapa coroplético** (**Mapa preenchido**): Local `dim_municipio[uf]` e Saturação de cor `Taxa por 100 mil (anual)`. Título: "Taxa bruta anual por UF de residência". Se o mapa não reconhecer as siglas, crie em `dim_municipio` a coluna `Local = dim_municipio[uf] & ", Brasil"` e use-a.
5. Na página 1 não há segmentação de idade, sexo ou CID, então a taxa fica correta.

**Página 2 — "Causas e valores"**

1. Mesmas segmentações de ano e região. Para copiar com sincronia: **Exibir → Sincronizar segmentações**. Adicione também uma segmentação de `dim_faixa_etaria[faixa]` e uma de `fato_internacao_mensal[sexo]`.
2. **Gráfico de barras clusterizado**: eixo `dim_cid_capitulo[descricao]`, valor `Internações`, classificado em ordem decrescente (**... → Classificar eixo → Internações**).
3. **Gráfico de colunas**: eixo `fato_internacao_mensal[uf_hospital]`, valor `Valor médio aprovado`. Título: "Valor aprovado SUS médio por UF do hospital (R$ dez/2024)".
4. **Matriz**: linhas `dim_cid_capitulo[capitulo]`, colunas `dim_municipio[regiao]`, valores `Letalidade %`. Ative **Formatar → Elementos de célula → Cor da tela de fundo** (escala de cor).
5. Se você colocar nesta página algum visual com `Taxa por 100 mil`, desligue as interações de faixa, sexo e CID nele (aviso da seção 5).
6. Salve como `reports/dashboard_sih.pbix` e tire prints das 2 páginas para o relatório.

## 12. Plano B: CSV via Samba (sem acesso ao Postgres)

1. Copie `data/gold/csv/` para o servidor (ex.: `rsync -av data/gold/csv/ usuario@192.168.3.154:/srv/gold/`).
2. No servidor, em `/etc/samba/smb.conf`, adicione:
   ```
   [gold]
      path = /srv/gold
      read only = yes
      guest ok = no
      valid users = <seu_usuario>
   ```
   Depois: `sudo smbpasswd -a <seu_usuario>` (se ainda não tiver senha Samba), `sudo systemctl restart smbd` e `sudo ufw allow from 192.168.3.0/24 to any app Samba`.
3. No Windows: Explorador → `\\192.168.3.154\gold` (entre com usuário e senha do Samba).
4. Power BI: **Obter dados → Texto/CSV**, um arquivo por vez. Delimitador **Ponto e vírgula**, origem **65001: Unicode (UTF-8)** → **Transformar dados**. Confira os tipos: ids como Número inteiro e somas como Número decimal. Em `lat`/`lon` e nos valores, se a localidade trocar ponto por vírgula, use **Transformar → Tipo de dados → Usando a Localidade... → Inglês (Estados Unidos)**.
5. A fato em CSV tem ~850 MB, e a importação leva alguns minutos. Do passo 4 da seção 4 em diante, tudo é igual.
