import shutil

import pandas as pd
import pytest
from pyspark.sql import functions as F

from coleta import dbc_to_parquet
from conftest import FIXTURES
from tratamento import (
    age_in_years,
    faixa_etaria,
    flag_iqr,
    harmonize,
    normalize_municipio,
    null_sentinels,
    run,
    to_snake,
)


def test_to_snake():
    assert to_snake("Município Res") == "municipio_res"
    assert to_snake("VAL_TOT") == "val_tot"


def test_age_codes(spark):
    cases = [("10", "2", 0), ("5", "3", 0), ("35", "4", 35), ("2", "5", 102), ("x", "9", None), ("130", "4", None)]
    df = spark.createDataFrame([(i, c) for i, c, _ in cases], ["idade", "cod"])
    got = [r[0] for r in df.select(age_in_years(F.col("idade"), F.col("cod"))).collect()]
    assert got == [e for _, _, e in cases]


def test_faixa(spark):
    df = spark.createDataFrame([(0,), (4,), (5,), (19,), (20,), (85,), (None,)], "a int")
    got = [r[0] for r in df.select(faixa_etaria(F.col("a"))).collect()]
    assert got == ["0-4", "0-4", "5-9", "15-19", "20-29", "80+", "ignorado"]


def test_normalize_municipio(spark):
    df = spark.createDataFrame([("3550308",), ("355030",), ("000000",), ("",), (None,), ("35A030",)], "m string")
    got = [r[0] for r in df.select(normalize_municipio(F.col("m"))).collect()]
    assert got == ["355030", "355030", "ignorado", "ignorado", "ignorado", "ignorado"]


def test_null_sentinels(spark):
    df = spark.createDataFrame([("0", "1"), ("9", "99"), ("1", "02")], ["SEXO", "RACA_COR"])
    out = null_sentinels(df, {"SEXO": ["0", "9"], "RACA_COR": ["99"]}).collect()
    assert [r.SEXO for r in out] == [None, None, "1"]
    assert [r.RACA_COR for r in out] == ["1", None, "02"]


def test_harmonize_fills_missing(spark):
    a = spark.createDataFrame([(1, 2)], ["a", "b"])
    b = spark.createDataFrame([(3, 4)], ["a", "c"])
    out = harmonize([a, b])
    assert set(out.columns) == {"a", "b", "c"}
    assert out.count() == 2
    assert out.filter(F.col("c").isNull()).count() == 1


def test_flag_iqr(spark):
    rows = [("p1", float(v)) for v in [1, 2, 3, 4, 100]] + [("p2", 100.0)] * 4
    df = spark.createDataFrame(rows, ["proc", "v"])
    out = flag_iqr(df, "v", "proc", "flag")
    flagged = out.filter("flag").collect()
    assert [(r.proc, r.v) for r in flagged] == [("p1", 100.0)]


@pytest.fixture()
def mini_bronze(tmp_path):
    bronze = tmp_path / "bronze"
    dbc_to_parquet(FIXTURES / "RDAC2201.dbc", bronze / "sih_rd" / "ano=2022" / "uf=AC" / "RDAC2201.parquet")
    (bronze / "aux").mkdir(parents=True)
    (bronze / "ibge").mkdir()
    pd.DataFrame({"cid": ["A00", "O80"], "descricao": ["x", "y"], "capitulo": ["I.", "XV."]}).to_parquet(
        bronze / "aux" / "cid10.parquet")
    pd.DataFrame({"cod_municipio_6": ["120040"], "nome": ["RIO BRANCO"]}).to_parquet(bronze / "aux" / "municipios.parquet")
    pd.DataFrame({"cod_municipio_7": ["1200401"], "ano": [2022], "populacao": [400000], "fonte": ["censo"]}).to_parquet(
        bronze / "ibge" / "populacao_municipio_ano.parquet")
    pd.DataFrame({"ano_mes": ["2022-01", "2024-12"], "indice": [100.0, 120.0]}).to_parquet(
        bronze / "ibge" / "ipca_mensal.parquet")
    return bronze


def test_log_overwritten_on_rerun(spark, mini_bronze, tmp_path):
    log = tmp_path / "log.csv"
    run(spark, mini_bronze, tmp_path / "silver", log, check_scope=False)
    first = pd.read_csv(log)
    run(spark, mini_bronze, tmp_path / "silver", log, check_scope=False)
    second = pd.read_csv(log)
    assert len(first) == len(second) > 5
    assert list(first.columns) == ["problema", "decisao", "justificativa", "linhas_antes", "linhas_depois", "linhas_afetadas"]


def test_silver_keeps_rows_and_derives(spark, mini_bronze, tmp_path):
    run(spark, mini_bronze, tmp_path / "silver", tmp_path / "log.csv", check_scope=False)
    s = spark.read.parquet(str(tmp_path / "silver" / "sih_rd"))
    assert s.count() == 3977
    r = s.filter(F.col("munic_res") == "120040").first()
    assert r.uf_residencia == "AC" and r.regiao_residencia == "Norte" and r.populacao == 400000
    assert r.uf_hospital == "AC"
    # IPCA: 2022-01 → 2024-12 multiplica por 1,2
    assert r.val_tot is None or abs(r.val_tot_real - r.val_tot * 1.2) < 1e-6


def test_ignorado_kept_in_counts(spark, mini_bronze, tmp_path):
    p = next((mini_bronze / "sih_rd").rglob("*.parquet"))
    df = pd.read_parquet(p)
    df.loc[0, "MUNIC_RES"] = "000000"
    df.to_parquet(p, index=False)
    run(spark, mini_bronze, tmp_path / "silver", tmp_path / "log.csv", check_scope=False)
    s = spark.read.parquet(str(tmp_path / "silver" / "sih_rd"))
    assert s.count() == 3977
    assert s.filter(F.col("uf_residencia") == "ignorado").count() >= 1
