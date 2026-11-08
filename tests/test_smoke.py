from pyspark.sql import SparkSession


def test_spark_starts():
    spark = SparkSession.builder.master("local[1]").appName("smoke").getOrCreate()
    assert spark.range(10).count() == 10
    spark.stop()


def test_dbc_reader_imports():
    import dbfread
    import pyreaddbc

    assert callable(pyreaddbc.dbc2dbf)
    assert dbfread.DBF
