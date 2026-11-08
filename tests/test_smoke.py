def test_spark_starts(spark):
    assert spark.range(10).count() == 10


def test_dbc_reader_imports():
    import dbfread
    import pyreaddbc

    assert callable(pyreaddbc.dbc2dbf)
    assert dbfread.DBF
