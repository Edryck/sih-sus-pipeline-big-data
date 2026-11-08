import argparse
import shutil
from pathlib import Path

import geopandas as gpd
from dotenv import dotenv_values
from pyspark.sql import DataFrame, SparkSession, functions as F

from tratamento import FAIXAS, REGIAO, ROOT, UF_SIGLA, _map

ROMANOS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI",
           "XVII", "XVIII", "XIX", "XX", "XXI", "XXII"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
FAIXA_ORDEM = [f"{a}-{b}" for a, b in FAIXAS] + ["80+", "ignorado"]
GRAIN = ["id_tempo", "id_municipio_res", "uf_hospital", "id_cid_capitulo", "id_faixa_etaria", "sexo"]
FKS = {"id_tempo": "dim_tempo", "id_municipio_res": "dim_municipio(id_municipio)",
       "id_cid_capitulo": "dim_cid_capitulo", "id_faixa_etaria": "dim_faixa_etaria"}


def build_dims(silver: DataFrame, municipios: DataFrame, geo_path: Path) -> dict:
    spark = silver.sparkSession
    tempo = (silver.select("ano_mes").distinct()
             .select(F.regexp_replace("ano_mes", "-", "").cast("int").alias("id_tempo"),
                     F.substring("ano_mes", 1, 4).cast("int").alias("ano"),
                     F.substring("ano_mes", 6, 2).cast("int").alias("mes"))
             .withColumn("trimestre", F.ceil(F.col("mes") / 3).cast("int"))
             .withColumn("nome_mes", F.element_at(F.array(*map(F.lit, MESES)), F.col("mes")))
             .orderBy("id_tempo"))

    geo = gpd.read_file(geo_path)
    # centróide em coordenada projetada pra não dar warning/erro de geometria geográfica
    cent = geo.geometry.to_crs(5880).centroid.to_crs(4674)
    geo_pd = geo.assign(cod_municipio_6=geo.code_muni.astype("int64").astype(str).str[:6], lat=cent.y, lon=cent.x)
    geo_df = spark.createDataFrame(geo_pd[["cod_municipio_6", "lat", "lon"]])
    mun = (municipios.join(geo_df, "cod_municipio_6", "left")
           .select(F.col("cod_municipio_6").cast("int").alias("id_municipio"), "cod_municipio_6", "nome",
                   _map(UF_SIGLA)[F.substring("cod_municipio_6", 1, 2)].alias("uf"),
                   _map(REGIAO)[F.substring("cod_municipio_6", 1, 1)].alias("regiao"), "lat", "lon"))
    ign = spark.createDataFrame([(-1, "ignorado", "ignorado", "ignorado", "ignorado", None, None)], mun.schema)
    mun = mun.unionByName(ign)

    cap = (silver.select(F.col("cid_capitulo").alias("raw")).distinct()
           .withColumn("capitulo", F.when(F.col("raw") == "ignorado", "ignorado")
                       .otherwise(F.regexp_extract("raw", r"^([IVX]+)\.", 1)))
           .withColumn("descricao", F.when(F.col("raw") == "ignorado", "ignorado")
                       .otherwise(F.trim(F.regexp_extract("raw", r"^[IVX]+\.\s*(.*)$", 1))))
           .withColumn("id_cid_capitulo", F.coalesce(F.array_position(F.array(*map(F.lit, ROMANOS)), F.col("capitulo"))
                                                     .cast("int"), F.lit(-1)))
           .withColumn("id_cid_capitulo", F.when(F.col("id_cid_capitulo") == 0, -1).otherwise(F.col("id_cid_capitulo"))))
    faixa = spark.createDataFrame([(i + 1, f, i + 1) for i, f in enumerate(FAIXA_ORDEM)],
                                  "id_faixa_etaria int, faixa string, ordem int")
    return {"dim_tempo": tempo, "dim_municipio": mun, "dim_cid_capitulo": cap, "dim_faixa_etaria": faixa}


def build_fact(silver: DataFrame, dims: dict) -> DataFrame:
    cap = dims["dim_cid_capitulo"].select(F.col("raw").alias("cid_capitulo"), "id_cid_capitulo")
    faixa = dims["dim_faixa_etaria"].select(F.col("faixa").alias("faixa_etaria"), "id_faixa_etaria")
    # AIH longa permanência (IDENT=5) é continuação, não internação nova
    return (silver.filter(~F.col("longa_permanencia"))
            .withColumn("id_tempo", F.regexp_replace("ano_mes", "-", "").cast("int"))
            .withColumn("id_municipio_res", F.when(F.col("munic_res") == "ignorado", -1)
                        .otherwise(F.col("munic_res").cast("int")))
            .join(F.broadcast(cap), "cid_capitulo", "left")
            .join(F.broadcast(faixa), "faixa_etaria", "left")
            .groupBy(*GRAIN)
            .agg(F.count(F.lit(1)).alias("qtd_internacoes"),
                 F.sum(F.col("obito").cast("int")).alias("qtd_obitos"),
                 F.sum("dias_perm").cast("long").alias("soma_dias_perm"),
                 F.sum("val_tot").alias("soma_val_tot"),
                 F.sum("val_tot_real").alias("soma_val_tot_real"),
                 F.sum(F.col("internado_fora_municipio").cast("int")).alias("qtd_fora_municipio")))


def write_tables(tables: dict, gold: Path) -> None:
    for name, df in tables.items():
        df.write.mode("overwrite").parquet(str(gold / name))
        tmp = gold / "csv" / f"_{name}"
        (df.coalesce(1).write.mode("overwrite").option("header", True).option("sep", ";")
         .option("encoding", "UTF-8").csv(str(tmp)))
        next(tmp.glob("part-*.csv")).replace(gold / "csv" / f"{name}.csv")
        shutil.rmtree(tmp)


def load_postgres(spark, tables: dict, env: dict) -> None:
    url = f"jdbc:postgresql://{env['PG_HOST']}:{env.get('PG_PORT', '5432')}/{env['PG_DB']}"
    props = {"user": env["PG_USER"], "password": env["PG_PASSWORD"], "driver": "org.postgresql.Driver"}
    jvm = spark._jvm
    jprops = jvm.java.util.Properties()
    jprops.setProperty("user", props["user"])
    jprops.setProperty("password", props["password"])

    def sql(*stmts):
        # DriverManager não enxerga jar do spark.jars.packages; instancia o driver direto
        conn = jvm.org.postgresql.Driver().connect(url, jprops)
        try:
            st = conn.createStatement()
            for s in stmts:
                st.execute(s)
        finally:
            conn.close()

    sql("CREATE SCHEMA IF NOT EXISTS dw", "DROP TABLE IF EXISTS dw.fato_internacao_mensal, dw.populacao_municipio_ano")
    # dims antes da fato, senão o DROP da dim quebra por causa da FK
    for name, df in tables.items():
        (df.write.mode("overwrite").option("batchsize", 50000).option("numPartitions", 4)
         .jdbc(url + "?reWriteBatchedInserts=true", f"dw.{name}", properties=props))
    pks = {"dim_tempo": "id_tempo", "dim_municipio": "id_municipio", "dim_cid_capitulo": "id_cid_capitulo",
           "dim_faixa_etaria": "id_faixa_etaria", "populacao_municipio_ano": "id_municipio, ano"}
    stmts = [f"ALTER TABLE dw.{t} ADD PRIMARY KEY ({c})" for t, c in pks.items()]
    stmts += [f"ALTER TABLE dw.fato_internacao_mensal ADD FOREIGN KEY ({c}) REFERENCES dw.{ref}"
              for c, ref in FKS.items()]
    stmts += [f"CREATE INDEX ON dw.fato_internacao_mensal ({c})" for c in FKS]
    stmts.append("ALTER TABLE dw.populacao_municipio_ano ADD FOREIGN KEY (id_municipio) "
                 "REFERENCES dw.dim_municipio(id_municipio)")
    sql(*stmts)


def main() -> None:
    ap = argparse.ArgumentParser(description="silver → gold (modelo estrela) + carga no PostgreSQL")
    ap.add_argument("--sem-postgres", action="store_true")
    args = ap.parse_args()
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v}
    use_pg = not args.sem_postgres and {"PG_HOST", "PG_DB", "PG_USER", "PG_PASSWORD"} <= env.keys()
    b = (SparkSession.builder.master("local[*]").appName("gold")
         .config("spark.driver.memory", "6g")
         .config("spark.sql.shuffle.partitions", "64")
         .config("spark.ui.showConsoleProgress", "false"))
    if use_pg:
        b = b.config("spark.jars.packages", "org.postgresql:postgresql:42.7.4")
    spark = b.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")

    bronze, gold = ROOT / "data" / "bronze", ROOT / "data" / "gold"
    silver = spark.read.parquet(str(ROOT / "data" / "silver" / "sih_rd"))
    municipios = spark.read.parquet(str(bronze / "aux" / "municipios.parquet"))
    tables = build_dims(silver, municipios, bronze / "geo" / "municipio.gpkg")
    tables = {k: v.cache() for k, v in tables.items()}
    fact = build_fact(silver, tables)
    pop = (spark.read.parquet(str(bronze / "ibge" / "populacao_municipio_ano.parquet"))
           .select(F.substring("cod_municipio_7", 1, 6).cast("int").alias("id_municipio"),
                   F.col("ano").cast("int"), F.col("populacao").cast("long"))
           .join(tables["dim_municipio"].select("id_municipio"), "id_municipio", "left_semi"))
    out = {**tables, "dim_cid_capitulo": tables["dim_cid_capitulo"].drop("raw"),
           "fato_internacao_mensal": fact, "populacao_municipio_ano": pop}
    write_tables(out, gold)

    out = {k: spark.read.parquet(str(gold / k)) for k in out}
    for k, df in out.items():
        print(f"{k}: {df.count():,}")
    total = out["fato_internacao_mensal"].agg(F.sum("qtd_internacoes")).first()[0]
    esperado = silver.filter(~F.col("longa_permanencia")).count()
    assert total == esperado, f"fato {total} != silver {esperado}"
    orfas = out["fato_internacao_mensal"].join(out["dim_municipio"].withColumnRenamed("id_municipio", "id_municipio_res"),
                                               "id_municipio_res", "left_anti").count()
    assert orfas == 0, f"{orfas} linhas da fato sem município na dimensão"

    if use_pg:
        load_postgres(spark, out, env)
        print("carga no PostgreSQL (schema dw) ok")
    else:
        print("PostgreSQL pulado (.env incompleto ou --sem-postgres)")


if __name__ == "__main__":
    main()
