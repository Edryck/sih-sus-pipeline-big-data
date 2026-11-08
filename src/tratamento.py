import argparse
import unicodedata
from functools import reduce
from pathlib import Path

import pandas as pd
from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession, functions as F

ROOT = Path(__file__).resolve().parents[1]

COLS = ["ANO_CMPT", "MES_CMPT", "MUNIC_RES", "MUNIC_MOV", "NASC", "SEXO", "IDADE", "COD_IDADE", "DT_INTER",
        "DT_SAIDA", "DIAG_PRINC", "PROC_REA", "CAR_INT", "DIAS_PERM", "UTI_MES_TO", "VAL_TOT", "MORTE", "CNES",
        "N_AIH", "IDENT", "RACA_COR"]
SENTINELS = {"SEXO": ["0", "9"], "COD_IDADE": ["0"], "RACA_COR": ["99"], "CAR_INT": ["99"], "CNES": ["0000000"]}
UF_SIGLA = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA",
            "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
            "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
            "51": "MT", "52": "GO", "53": "DF"}
REGIAO = {"1": "Norte", "2": "Nordeste", "3": "Sudeste", "4": "Sul", "5": "Centro-Oeste"}
FAIXAS = [(0, 4), (5, 9), (10, 14), (15, 19), (20, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 79)]
# U09/U10 (pós-COVID, síndrome inflamatória) não estão na tabela TabWin, mas são do cap. XXII
CID_EXTRA = {"U09": "XXII.Códigos para propósitos especiais", "U10": "XXII.Códigos para propósitos especiais"}


def to_snake(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "_".join(plain.lower().replace("-", " ").split())


def age_in_years(idade, cod_idade):
    n = idade.cast("int")
    age = (F.when(cod_idade == "4", n)
            .when(cod_idade.isin("2", "3"), F.lit(0))
            .when(cod_idade == "5", n + 100))
    return F.when(age.between(0, 120), age)


def faixa_etaria(age):
    expr = F.when(age >= 80, "80+")
    for lo, hi in reversed(FAIXAS):
        expr = F.when(age.between(lo, hi), f"{lo}-{hi}").otherwise(expr)
    return F.coalesce(expr, F.lit("ignorado"))


def normalize_municipio(code):
    c = F.trim(code)
    six = F.when(F.length(c) == 7, F.substring(c, 1, 6)).otherwise(c)
    return F.when(six.rlike(r"^\d{6}$") & (six != "000000"), six).otherwise(F.lit("ignorado"))


def null_sentinels(df: DataFrame, sentinels: dict) -> DataFrame:
    for col, values in sentinels.items():
        df = df.withColumn(col, F.when(F.trim(col).isin(values) | (F.trim(col) == ""), None).otherwise(F.col(col)))
    return df


def harmonize(dfs: list) -> DataFrame:
    return reduce(lambda a, b: a.unionByName(b, allowMissingColumns=True), dfs)


def flag_iqr(df: DataFrame, value_col: str, group_col: str, flag_col: str) -> DataFrame:
    q = (df.groupBy(group_col)
         .agg(F.percentile_approx(value_col, [0.25, 0.75], 1000).alias("_q"))
         .select(group_col, F.col("_q")[0].alias("_q1"), F.col("_q")[1].alias("_q3")))
    iqr = F.col("_q3") - F.col("_q1")
    out = df.join(F.broadcast(q), group_col, "left")
    flag = (F.col(value_col) < F.col("_q1") - 1.5 * iqr) | (F.col(value_col) > F.col("_q3") + 1.5 * iqr)
    return out.withColumn(flag_col, F.coalesce(flag, F.lit(False))).drop("_q1", "_q3")


def _map(d: dict):
    return F.create_map(*[F.lit(x) for kv in d.items() for x in kv])


def _read_bronze(spark, bronze: Path, anos, ufs) -> DataFrame:
    # um DataFrame por ano: se o layout mudar entre anos, o union preenche com nulo em vez de sumir com colunas
    dfs = []
    for ano_dir in sorted((bronze / "sih_rd").glob("ano=*")):
        ano = int(ano_dir.name.split("=")[1])
        if anos and ano not in anos:
            continue
        df = spark.read.option("mergeSchema", "true").parquet(str(ano_dir)).withColumn("ano", F.lit(ano))
        dfs.append(df.withColumnRenamed("uf", "uf_hospital"))
    df = harmonize(dfs)
    if ufs:
        df = df.filter(F.col("uf_hospital").isin(ufs))
    return df


def run(spark, bronze: Path, silver: Path, log_path: Path, anos=None, ufs=None, check_scope=True) -> None:
    log = []

    def add(problema, decisao, justificativa, antes, depois, afetadas):
        log.append({"problema": problema, "decisao": decisao, "justificativa": justificativa,
                    "linhas_antes": int(antes), "linhas_depois": int(depois), "linhas_afetadas": int(afetadas)})

    raw = _read_bronze(spark, bronze, anos, ufs)
    raw_cols = [c for c in raw.columns if c not in ("ano", "uf_hospital")]
    n_raw = raw.count()

    # hash de 64 bits das colunas brutas: duplicata exata = mesmo hash (colisão desprezível em 40M linhas)
    dedup = raw.withColumn("_h", F.xxhash64(*raw_cols, "uf_hospital")).dropDuplicates(["_h"]).drop("_h")
    df = dedup.select("ano", "uf_hospital", *COLS)
    df = df.select("ano", "uf_hospital", *[F.col(c).alias(to_snake(c)) for c in COLS])
    sent = {to_snake(k): v for k, v in SENTINELS.items()}
    df = null_sentinels(df, sent)

    cid = spark.read.parquet(str(bronze / "aux" / "cid10.parquet")).select(
        F.col("cid").alias("cid3"), F.col("capitulo").alias("_cap"))
    mun = spark.read.parquet(str(bronze / "aux" / "municipios.parquet")).select(
        F.col("cod_municipio_6").alias("munic_res"), F.col("nome").alias("nome_municipio_res"))
    pop = (spark.read.parquet(str(bronze / "ibge" / "populacao_municipio_ano.parquet"))
           .select(F.substring("cod_municipio_7", 1, 6).alias("munic_res"), F.col("ano").cast("int").alias("ano_cmpt"),
                   F.col("populacao").cast("long")))
    ipca = pd.read_parquet(bronze / "ibge" / "ipca_mensal.parquet")
    base = ipca.loc[ipca["ano_mes"] == "2024-12", "indice"].iloc[0]
    fator = spark.createDataFrame(ipca.assign(_fator=base / ipca["indice"])[["ano_mes", "_fator"]])

    idade_bruta = age_in_years(F.col("idade"), F.col("cod_idade"))
    val = F.col("val_tot").cast("double")
    df = (df
          .withColumn("ano_cmpt", F.col("ano_cmpt").cast("short"))
          .withColumn("mes_cmpt", F.col("mes_cmpt").cast("byte"))
          .withColumn("ano_mes", F.format_string("%04d-%02d", "ano_cmpt", "mes_cmpt"))
          .withColumn("munic_res", normalize_municipio(F.col("munic_res")))
          .withColumn("munic_mov", normalize_municipio(F.col("munic_mov")))
          .withColumn("uf_residencia", F.coalesce(_map(UF_SIGLA)[F.substring("munic_res", 1, 2)], F.lit("ignorado")))
          .withColumn("regiao_residencia", F.coalesce(_map(REGIAO)[F.substring("munic_res", 1, 1)], F.lit("ignorado")))
          .withColumn("regiao_hospital",
                      _map(REGIAO)[F.substring(_map({v: k for k, v in UF_SIGLA.items()})[F.col("uf_hospital")], 1, 1)])
          .withColumn("internado_fora_municipio", F.col("munic_res") != F.col("munic_mov"))
          .withColumn("sexo", F.when(F.col("sexo") == "1", "masculino")
                      .when(F.col("sexo").isin("2", "3"), "feminino").otherwise("ignorado"))
          .withColumn("raca_cor", F.coalesce("raca_cor", F.lit("ignorado")))
          .withColumn("car_int", F.coalesce("car_int", F.lit("ignorado")))
          .withColumn("_idade_invalida", idade_bruta.isNull())
          .withColumn("idade_anos", idade_bruta.cast("short"))
          .withColumn("faixa_etaria", faixa_etaria(F.col("idade_anos")))
          .withColumn("nasc", F.to_date("nasc", "yyyyMMdd"))
          .withColumn("dt_inter", F.to_date("dt_inter", "yyyyMMdd"))
          .withColumn("dt_saida", F.to_date("dt_saida", "yyyyMMdd"))
          .withColumn("dias_perm", F.col("dias_perm").cast("int"))
          .withColumn("uti_mes_to", F.col("uti_mes_to").cast("int"))
          .withColumn("_dias_diverge", F.col("dias_perm") != F.datediff("dt_saida", "dt_inter"))
          .withColumn("_val_nao_positivo", val <= 0)
          .withColumn("val_tot", F.when(val > 0, val))
          .withColumn("obito", F.col("morte") == "1")
          .withColumn("longa_permanencia", F.col("ident") == "5")
          .withColumn("cid3", F.substring("diag_princ", 1, 3))
          .drop("idade", "cod_idade", "morte"))

    df = (df.join(F.broadcast(cid), "cid3", "left")
          .withColumn("cid_capitulo", F.coalesce("_cap", _map(CID_EXTRA)[F.col("cid3")], F.lit("ignorado")))
          .withColumn("_cid_sem_capitulo", F.col("_cap").isNull())
          .join(F.broadcast(mun), "munic_res", "left")
          .withColumn("_mun_sem_nome", F.col("nome_municipio_res").isNull())
          .join(F.broadcast(pop), ["munic_res", "ano_cmpt"], "left")
          .withColumn("_sem_populacao", F.col("populacao").isNull())
          .join(F.broadcast(fator), "ano_mes", "left")
          .withColumn("val_tot_real", F.col("val_tot") * F.col("_fator"))
          .drop("_cap", "_fator"))
    df = flag_iqr(df, "val_tot", "proc_rea", "outlier_val_tot")
    df = flag_iqr(df, "dias_perm", "proc_rea", "outlier_dias_perm")
    df = df.persist(StorageLevel.DISK_ONLY)

    n = df.count()
    c = df.agg(
        F.sum(F.col("longa_permanencia").cast("int")).alias("longa"),
        F.sum((F.col("sexo") == "ignorado").cast("int")).alias("sexo"),
        F.sum((F.col("raca_cor") == "ignorado").cast("int")).alias("raca"),
        F.sum((F.col("car_int") == "ignorado").cast("int")).alias("car"),
        F.sum(F.col("_idade_invalida").cast("int")).alias("idade"),
        F.sum(F.coalesce("_dias_diverge", F.lit(False)).cast("int")).alias("dias"),
        F.sum(F.coalesce("_val_nao_positivo", F.lit(True)).cast("int")).alias("val"),
        F.sum(F.col("_cid_sem_capitulo").cast("int")).alias("cid"),
        F.sum((F.col("cid_capitulo") == "ignorado").cast("int")).alias("cid_ign"),
        F.sum((F.col("munic_res") == "ignorado").cast("int")).alias("mun_ign"),
        F.sum(F.col("_mun_sem_nome").cast("int")).alias("mun"),
        F.sum((F.col("_sem_populacao") & (F.col("munic_res") != "ignorado")).cast("int")).alias("pop"),
        F.sum(F.col("outlier_val_tot").cast("int")).alias("out_val"),
        F.sum(F.col("outlier_dias_perm").cast("int")).alias("out_dias"),
    ).first().asDict()

    add("duplicados exatos (todas as 113 colunas)", "removidos",
        "mesma linha repetida infla contagens; EDA não achou nenhum", n_raw, n, n_raw - n)
    add("nomes de colunas e tipos", "snake_case sem acento; datas, inteiros pequenos, double; códigos como texto",
        "texto em object custa ~2 GB por coluna em pandas (EDA 2.1)", n, n, 0)
    add("AIH de longa permanência (IDENT=5) repete N_AIH", "mantida com longa_permanencia=True; fora das contagens",
        "é continuação da mesma internação; contar duas vezes infla internações", n, n, c["longa"])
    add("SEXO sentinela (0/9) e código 3", "1→masculino, 2/3→feminino, resto→ignorado (categoria própria)",
        "manter a linha evita perder internação; viés pequeno se ausência não for aleatória", n, n, c["sexo"])
    add("RACA_COR=99 (sem informação), muito variável entre anos",
        "categoria ignorado; não usar em comparação temporal",
        "19% em 2022 vs 0,02% em 2024: imputar criaria tendência falsa", n, n, c["raca"])
    add("CAR_INT ausente/99", "categoria ignorado", "mesma lógica das categóricas", n, n, c["car"])
    add("idade inválida (COD_IDADE=0 ou > 120 anos)", "idade nula, faixa ignorado; sem imputação",
        "média/mediana distorceriam a distribuição; caso raro", n, n, c["idade"])
    add("DIAS_PERM ≠ DT_SAIDA − DT_INTER", "mantido DIAS_PERM como informado",
        "é o dado de faturamento do SUS; diferença documentada, não corrigida", n, n, c["dias"])
    add("VAL_TOT ≤ 0", "val_tot nulo; internação continua nas contagens",
        "valor zero não é reembolso real; excluído só das médias", n, n, c["val"])
    add("DIAG_PRINC sem capítulo na tabela TabWin", "U09/U10→cap. XXII; restantes→ignorado",
        "códigos novos da COVID não estão na tabela TabWin", n, n, c["cid"])
    add("capítulo CID ignorado após mapeamento", "mantido como ignorado", "sem informação suficiente", n, n, c["cid_ign"])
    add("MUNIC_RES inválido", "ignorado; fora das taxas, dentro das contagens",
        "não dá para atribuir UF de residência", n, n, c["mun_ign"])
    add("join municípios (nome) sem correspondência", "mantido, marcado", "nome é só rótulo", n, n, c["mun"])
    add("join população IBGE sem correspondência", "mantido, marcado; fora das taxas",
        "2023 interpolado; município novo/alterado pode faltar", n, n, c["pop"])
    add("outlier VAL_TOT (1,5×IQR por procedimento)", "marcado outlier_val_tot, não removido",
        "alta complexidade é fenômeno real (EDA 2.3)", n, n, c["out_val"])
    add("outlier DIAS_PERM (1,5×IQR por procedimento)", "marcado outlier_dias_perm, não removido",
        "longa permanência é fenômeno real (EDA 2.3)", n, n, c["out_dias"])

    flags = [x for x in df.columns if x.startswith("_")]
    (df.drop("cid3", *flags)
     .repartition("ano", "uf_hospital")
     .write.mode("overwrite").partitionBy("ano", "uf_hospital").parquet(str(silver / "sih_rd")))
    df.unpersist()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(log).to_csv(log_path, index=False)

    if check_scope:
        s = spark.read.parquet(str(silver / "sih_rd"))
        meses = s.select("ano_mes").distinct().count()
        ufs_n = s.select("uf_hospital").distinct().count()
        validos = n - c["mun_ign"]
        assert meses == 36, f"esperava 36 meses, achei {meses}"
        assert ufs_n == 27, f"esperava 27 UFs, achei {ufs_n}"
        assert c["pop"] / validos < 0.05, f"join população: {c['pop'] / validos:.1%} sem correspondência"
    print(f"silver: {n:,} linhas | log: {len(log)} regras → {log_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="bronze → silver + log de limpeza")
    ap.add_argument("--anos", nargs="+", type=int)
    ap.add_argument("--ufs", nargs="+")
    args = ap.parse_args()
    spark = (SparkSession.builder.master("local[*]").appName("tratamento")
             .config("spark.driver.memory", "6g")
             .config("spark.sql.shuffle.partitions", "64")
             .config("spark.ui.showConsoleProgress", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    full = args.anos is None and args.ufs is None
    run(spark, ROOT / "data" / "bronze", ROOT / "data" / "silver", ROOT / "reports" / "log_limpeza.csv",
        args.anos, args.ufs, check_scope=full)


if __name__ == "__main__":
    main()
