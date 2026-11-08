from datetime import date

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from gold import build_dims, build_fact

COLS = ["ano_mes", "munic_res", "uf_hospital", "cid_capitulo", "faixa_etaria", "sexo", "obito", "longa_permanencia",
        "dias_perm", "val_tot", "val_tot_real", "internado_fora_municipio", "dt_inter"]
CAP = "XV.  Gravidez parto e puerpério"


def _silver(spark):
    rows = [
        ("2022-01", "120040", "AC", CAP, "20-29", "feminino", False, False, 2, 100.0, 120.0, False),
        ("2022-01", "120040", "AC", CAP, "20-29", "feminino", True, False, 3, 50.0, 60.0, True),
        ("2022-01", "ignorado", "AC", CAP, "80+", "masculino", True, False, 1, None, None, True),
        ("2022-02", "120040", "AM", "ignorado", "ignorado", "feminino", False, False, 4, 10.0, 12.0, True),
        ("2022-02", "120040", "AM", "ignorado", "ignorado", "feminino", False, False, 1, 10.0, 12.0, False),
        ("2022-02", "120040", "AM", CAP, "20-29", "feminino", True, True, 300, 999.0, 999.0, False),
    ]
    return spark.createDataFrame([r + (date(2022, 1, 1),) for r in rows], COLS)


def _aux(spark, tmp_path):
    mun = spark.createDataFrame(pd.DataFrame({"cod_municipio_6": ["120040"], "nome": ["RIO BRANCO"]}))
    geo = tmp_path / "m.gpkg"
    gpd.GeoDataFrame({"code_muni": [1200401.0]}, geometry=[box(-68, -10, -67, -9)], crs="EPSG:4674").to_file(geo)
    return mun, geo


def test_fact_totals_match_silver(spark, tmp_path):
    silver = _silver(spark)
    mun, geo = _aux(spark, tmp_path)
    dims = build_dims(silver, mun, geo)
    fact = build_fact(silver, dims).toPandas()
    assert fact.qtd_internacoes.sum() == 5
    assert fact.qtd_obitos.sum() == 2
    assert fact.soma_val_tot.sum() == 170.0
    assert fact.qtd_fora_municipio.sum() == 3
    assert set(fact.id_municipio_res) == {120040, -1}
    assert fact.isna().sum()[["id_tempo", "id_cid_capitulo", "id_faixa_etaria"]].sum() == 0


def test_dims(spark, tmp_path):
    mun, geo = _aux(spark, tmp_path)
    dims = {k: v.toPandas() for k, v in build_dims(_silver(spark), mun, geo).items()}
    m = dims["dim_municipio"].set_index("id_municipio")
    assert m.loc[120040, "uf"] == "AC" and m.loc[120040, "regiao"] == "Norte"
    assert abs(m.loc[120040, "lat"] + 9.5) < 1e-3 and m.loc[-1, "nome"] == "ignorado"
    cap = dims["dim_cid_capitulo"].set_index("capitulo")
    assert cap.loc["XV", "descricao"] == "Gravidez parto e puerpério" and cap.loc["ignorado", "id_cid_capitulo"] == -1
    assert len(cap) == 2 and cap.loc["XV", "id_cid_capitulo"] == 15
    t = dims["dim_tempo"]
    assert len(t) == 2 and t.id_tempo.tolist() == [202201, 202202] and t.trimestre.tolist() == [1, 1]
    f = dims["dim_faixa_etaria"].sort_values("ordem")
    assert f.faixa.tolist()[0] == "0-4" and f.faixa.tolist()[-1] == "ignorado" and len(f) == 12
