import ftplib
import shutil

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import coleta
from coleta import (
    RawParser,
    build_metadata,
    dbc_to_parquet,
    download_rd,
    expand_codes,
    interpolate_population,
    is_complete,
    parse_cnv,
    rd_filename,
    with_retry,
)
from conftest import FIXTURES

FIX_DBC = FIXTURES / "RDAC2201.dbc"


def test_rd_filename():
    assert rd_filename("SP", 2022, 1) == "RDSP2201.dbc"


def test_is_complete_false_when_partial(tmp_path):
    p = tmp_path / "x.dbc"
    p.write_bytes(b"12")
    assert not is_complete(p, 10)
    assert is_complete(p, 2)


def test_is_complete_false_when_missing(tmp_path):
    assert not is_complete(tmp_path / "nope", 10)


def test_with_retry_succeeds_after_failures():
    calls = []

    def f():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("drop")
        return "ok"

    assert with_retry(f, base_delay=0) == "ok"
    assert len(calls) == 3


def test_with_retry_reraises():
    def f():
        raise OSError("down")

    with pytest.raises(OSError):
        with_retry(f, attempts=2, base_delay=0)


def test_parse_cnv_fixture():
    df = parse_cnv(FIXTURES / "sample.cnv")
    assert list(df.columns) == ["codigo", "descricao"]
    assert df.iloc[0]["codigo"] == "A00-B99"
    assert df.iloc[0]["descricao"].startswith("I.   Algumas doenças")
    assert len(df) == 5


def test_expand_codes():
    assert expand_codes("A00-A02,B05") == ["A00", "A01", "A02", "B05"]
    assert expand_codes("A98-B01") == ["A98", "A99", "B00", "B01"]


def test_raw_parser_keeps_impossible_date():
    parser = RawParser.__new__(RawParser)  # construtor real exige uma tabela DBF
    assert parser.parse(None, b"20221399") == "20221399"


def test_dbc_to_parquet_all_strings(tmp_path):
    meta = dbc_to_parquet(FIX_DBC, tmp_path / "o.parquet")
    t = pq.read_table(tmp_path / "o.parquet")
    assert all(pa.types.is_string(f.type) for f in t.schema)
    assert meta["colunas_dbf"] == t.num_columns == 113
    assert meta["linhas"] == t.num_rows == 3977
    assert t.column("DT_INTER")[0].as_py() == "20211227"


class FakeFTP:
    def __init__(self, *a, **k):
        pass

    def login(self):
        pass

    def voidcmd(self, cmd):
        pass

    def size(self, path):
        raise ftplib.error_perm("550 The system cannot find the file specified.")

    def quit(self):
        pass


def test_download_missing_month_marked(monkeypatch, tmp_path):
    monkeypatch.setattr(coleta.ftplib, "FTP", FakeFTP)
    assert download_rd("AC", 2099, 1, tmp_path)["status"] == "missing"


def test_interpolate_missing_year():
    df = pd.DataFrame(
        {"cod_municipio_7": ["1", "1"], "ano": [2022, 2024], "populacao": [100, 120], "fonte": ["censo", "estimativa"]}
    )
    out = interpolate_population(df, [2022, 2023, 2024])
    row = out[out["ano"] == 2023].iloc[0]
    assert row["populacao"] == 110 and row["fonte"] == "interpolado"
    assert len(out) == 3


def test_metadata_identical_on_rerun(tmp_path):
    dbc_dir = tmp_path / "dbc"
    dbc_dir.mkdir()
    shutil.copy(FIX_DBC, dbc_dir / "RDAC2201.dbc")
    out = tmp_path / "bronze" / "sih_rd" / "ano=2022" / "uf=AC" / "RDAC2201.parquet"
    out.parent.mkdir(parents=True)
    dbc_to_parquet(dbc_dir / "RDAC2201.dbc", out)
    first = build_metadata(dbc_dir, tmp_path / "bronze" / "sih_rd", {})
    second = build_metadata(dbc_dir, tmp_path / "bronze" / "sih_rd", {})
    pd.testing.assert_frame_equal(first, second)
    assert first.iloc[0]["status"] == "ok" and first.iloc[0]["linhas"] == 3977
