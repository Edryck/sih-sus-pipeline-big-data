import argparse
import ftplib
import hashlib
import json
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyreaddbc
import requests
from dbfread import DBF, FieldParser

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"
DBC_DIR = BRONZE / "dbc"
RD_DIR = BRONZE / "sih_rd"
STATUS_FILE = RD_DIR / "_status.json"
METADATA_CSV = ROOT / "reports" / "metadados_coleta.csv"

FTP_HOST = "ftp.datasus.gov.br"
RD_REMOTE = "/dissemin/publicos/SIHSUS/200801_/Dados"
TAB_SIH_REMOTE = "/dissemin/publicos/SIHSUS/200801_/Auxiliar/TAB_SIH.zip"
SIDRA = "https://apisidra.ibge.gov.br/values"

UFS = "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split()
YEARS = [2022, 2023, 2024]


def rd_filename(uf: str, year: int, month: int) -> str:
    return f"RD{uf}{year % 100:02d}{month:02d}.dbc"


def is_complete(local_path: Path, remote_size: int) -> bool:
    return local_path.exists() and local_path.stat().st_size == remote_size


def with_retry(fn, attempts: int = 5, base_delay: float = 2.0):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            print(f"  tentativa {i + 1} falhou ({e}); nova tentativa em {base_delay * 2**i:.0f}s")
            time.sleep(base_delay * 2**i)


def parse_cnv(path: Path, encoding: str = "latin-1") -> pd.DataFrame:
    # layout TabWin: seq até col 7, descrição cols 9-59, códigos a partir da col 60
    rows = []
    for line in path.read_text(encoding=encoding).splitlines()[1:]:
        if len(line) < 61:
            continue
        rows.append({"codigo": line[60:].strip().rstrip(","), "descricao": line[9:60].strip()})
    return pd.DataFrame(rows, columns=["codigo", "descricao"])


def expand_codes(spec: str) -> list[str]:
    # "A00-B99,C05" -> todas as categorias de 3 caracteres no intervalo
    out = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        if "-" not in part:
            out.append(part)
            continue
        start, end = part.split("-")
        letter, num = start[0], int(start[1:3])
        while True:
            code = f"{letter}{num:02d}"
            out.append(code)
            if code == end[:3]:
                break
            num += 1
            if num > 99:
                letter, num = chr(ord(letter) + 1), 0
    return out


class RawParser(FieldParser):
    # tudo como texto cru: datas/números impossíveis precisam chegar na EDA
    def parse(self, field, data):
        return data.decode("latin-1").strip()


def _connect() -> ftplib.FTP:
    ftp = ftplib.FTP(FTP_HOST, timeout=120)
    ftp.login()
    ftp.voidcmd("TYPE I")
    return ftp


def _fetch(remote: str, local: Path) -> int:
    ftp = _connect()
    try:
        size = ftp.size(remote)
        if is_complete(local, size):
            return size
        part = local.with_suffix(local.suffix + ".part")
        with open(part, "wb") as f:
            ftp.retrbinary(f"RETR {remote}", f.write)
        if part.stat().st_size != size:
            raise OSError(f"tamanho incompleto em {local.name}")
        part.rename(local)
        return size
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def download_rd(uf: str, year: int, month: int, dest_dir: Path) -> dict:
    name = rd_filename(uf, year, month)
    url = f"ftp://{FTP_HOST}{RD_REMOTE}/{name}"
    local = dest_dir / name
    row = {"arquivo": name, "url": url, "status": "ok"}
    try:
        ftp = _connect()
        try:
            remote_size = ftp.size(f"{RD_REMOTE}/{name}")
        finally:
            ftp.quit()
    except ftplib.error_perm as e:
        if str(e).startswith("550"):
            return {**row, "status": "missing"}
        raise
    if is_complete(local, remote_size):
        return {**row, "status": "skipped"}
    try:
        with_retry(lambda: _fetch(f"{RD_REMOTE}/{name}", local))
    except Exception as e:
        print(f"  falhou {name}: {e}")
        return {**row, "status": "failed"}
    return row


def dbc_to_parquet(dbc_path: Path, out_path: Path, batch_rows: int = 50_000) -> dict:
    dbf_path = out_path.with_suffix(".dbf")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pyreaddbc.dbc2dbf(str(dbc_path), str(dbf_path))
    try:
        table = DBF(str(dbf_path), encoding="latin-1", parserclass=RawParser, raw=False)
        cols = table.field_names
        schema = pa.schema([(c, pa.string()) for c in cols])
        tmp = out_path.with_suffix(".parquet.part")
        rows = 0
        # em lotes pra não estourar memória com SP/MG em 4 processos
        with pq.ParquetWriter(tmp, schema, compression="snappy") as writer:
            batch = {c: [] for c in cols}
            for rec in table:
                for c in cols:
                    batch[c].append(rec[c])
                rows += 1
                if rows % batch_rows == 0:
                    writer.write_table(pa.table(batch, schema=schema))
                    batch = {c: [] for c in cols}
            if batch[cols[0]]:
                writer.write_table(pa.table(batch, schema=schema))
        tmp.replace(out_path)
    finally:
        dbf_path.unlink(missing_ok=True)
    return {
        "linhas": rows,
        "colunas_dbf": len(cols),
        "hash_colunas": hashlib.md5(",".join(cols).encode()).hexdigest()[:10],
        "bytes_parquet": out_path.stat().st_size,
    }


def _parquet_path(name: str) -> Path:
    uf, yy = name[2:4], int(name[4:6])
    return RD_DIR / f"ano={2000 + yy}" / f"uf={uf}" / name.replace(".dbc", ".parquet")


def _convert(name: str) -> str:
    out = _parquet_path(name)
    src = DBC_DIR / name
    # parquet mais novo que o dbc = já convertido
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return name
    dbc_to_parquet(src, out)
    return name


def build_metadata(dbc_dir: Path, rd_dir: Path, status: dict) -> pd.DataFrame:
    rows = []
    names = sorted({p.name for p in dbc_dir.glob("RD*.dbc")} | set(status))
    for name in names:
        dbc = dbc_dir / name
        uf, yy = name[2:4], int(name[4:6])
        pq_path = rd_dir / f"ano={2000 + yy}" / f"uf={uf}" / name.replace(".dbc", ".parquet")
        row = {
            "arquivo": name,
            "url": f"ftp://{FTP_HOST}{RD_REMOTE}/{name}",
            "baixado_em": datetime.fromtimestamp(dbc.stat().st_mtime).isoformat(timespec="seconds") if dbc.exists() else "",
            "status": status.get(name, "ok" if dbc.exists() and pq_path.exists() else "failed"),
            "linhas": 0,
            "colunas_dbf": 0,
            "hash_colunas": "",
            "bytes_dbc": dbc.stat().st_size if dbc.exists() else 0,
            "bytes_parquet": 0,
        }
        if pq_path.exists():
            meta = pq.ParquetFile(pq_path).metadata
            cols = [meta.schema.column(i).name for i in range(meta.num_columns)]
            row.update(
                linhas=meta.num_rows,
                colunas_dbf=len(cols),
                hash_colunas=hashlib.md5(",".join(cols).encode()).hexdigest()[:10],
                bytes_parquet=pq_path.stat().st_size,
            )
        rows.append(row)
    return pd.DataFrame(rows)


def collect_rd(years: list[int], ufs: list[str], workers: int) -> None:
    DBC_DIR.mkdir(parents=True, exist_ok=True)
    RD_DIR.mkdir(parents=True, exist_ok=True)
    status = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else {}
    with ProcessPoolExecutor(workers) as pool:
        futures = []
        for year in years:
            for uf in ufs:
                for month in range(1, 13):
                    r = download_rd(uf, year, month, DBC_DIR)
                    print(f"{r['arquivo']}: {r['status']}")
                    if r["status"] in ("missing", "failed"):
                        status[r["arquivo"]] = r["status"]
                        continue
                    status.pop(r["arquivo"], None)
                    futures.append(pool.submit(_convert, r["arquivo"]))
        for f in futures:
            try:
                f.result()
            except Exception as e:
                print(f"  conversão falhou: {e}")
    STATUS_FILE.write_text(json.dumps(status, indent=1, sort_keys=True))


def write_metadata(full_scope: bool) -> pd.DataFrame:
    status = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else {}
    meta = build_metadata(DBC_DIR, RD_DIR, status)
    METADATA_CSV.parent.mkdir(parents=True, exist_ok=True)
    meta.to_csv(METADATA_CSV, index=False)
    ok = meta[meta["status"] == "ok"]
    print(
        f"\narquivos ok: {len(ok)} | linhas: {ok['linhas'].sum():,} | "
        f"DBC: {ok['bytes_dbc'].sum() / 1e6:.0f} MB | Parquet: {ok['bytes_parquet'].sum() / 1e6:.0f} MB"
    )
    bad = meta[meta["status"] != "ok"]
    if len(bad):
        print("pendentes:", ", ".join(f"{a} ({s})" for a, s in zip(bad["arquivo"], bad["status"])))
    if full_scope:
        assert len(ok) + (meta["status"] == "missing").sum() == len(UFS) * 36, "faltam arquivos do recorte"
        assert ok["linhas"].sum() >= 5_000_000, "menos de 5 milhões de linhas"
    return meta


def _sidra(path: str) -> list[dict]:
    resp = requests.get(f"{SIDRA}/{path}", timeout=300)
    resp.raise_for_status()
    return resp.json()[1:]


def interpolate_population(df: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    wide = df.pivot(index="cod_municipio_7", columns="ano", values="populacao")
    fonte = df.set_index(["cod_municipio_7", "ano"])["fonte"]
    missing = [y for y in years if y not in wide.columns]
    wide = wide.reindex(columns=sorted(set(wide.columns) | set(years))).interpolate(axis=1, limit_area="inside")
    out = wide[years].round().astype("Int64").stack().rename("populacao").reset_index()
    out["fonte"] = [
        "interpolado" if a in missing else fonte.get((c, a), "") for c, a in zip(out["cod_municipio_7"], out["ano"])
    ]
    return out


def fetch_population(years: list[int]) -> pd.DataFrame:
    # censo 2022 (4709) e estimativa 2024 (6579); 2023 não tem dado oficial municipal
    rows = []
    sources = {2022: ("4709", "93", "censo"), 2024: ("6579", "9324", "estimativa")}
    for year, (table, var, fonte) in sources.items():
        for r in with_retry(lambda: _sidra(f"t/{table}/n6/all/v/{var}/p/{year}")):
            if r["V"].isdigit():
                rows.append({"cod_municipio_7": r["D1C"], "ano": year, "populacao": int(r["V"]), "fonte": fonte})
    return interpolate_population(pd.DataFrame(rows), years)


def fetch_ipca() -> pd.DataFrame:
    rows = with_retry(lambda: _sidra("t/1737/n1/all/v/2266/p/202112-202412"))
    return pd.DataFrame(
        {"ano_mes": [f"{r['D3C'][:4]}-{r['D3C'][4:]}" for r in rows], "indice": [float(r["V"]) for r in rows]}
    )


def _cached(fn, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fn().to_parquet(path, index=False)
    except Exception as e:
        if not path.exists():
            raise
        print(f"aviso: {path.name} não atualizado ({e}); usando cache")


def collect_ibge(years: list[int]) -> None:
    _cached(lambda: fetch_population(years), BRONZE / "ibge" / "populacao_municipio_ano.parquet")
    _cached(fetch_ipca, BRONZE / "ibge" / "ipca_mensal.parquet")


def collect_aux() -> None:
    aux = BRONZE / "aux"
    cnv_dir = aux / "CNV"
    zip_path = aux / "TAB_SIH.zip"
    aux.mkdir(parents=True, exist_ok=True)
    with_retry(lambda: _fetch(TAB_SIH_REMOTE, zip_path))
    with zipfile.ZipFile(zip_path) as z:
        for n in ("CNV/CID10_3D.CNV", "CNV/CID10CAP.CNV", "CNV/BR_municip.cnv"):
            (aux / n).parent.mkdir(exist_ok=True)
            (aux / n).write_bytes(z.read(n))

    cid = parse_cnv(cnv_dir / "CID10_3D.CNV")
    cid = cid[cid["codigo"].str.len() == 3]
    cid = pd.DataFrame({"cid": cid["codigo"], "descricao": cid["descricao"].str[6:].str.strip()})
    chapters = parse_cnv(cnv_dir / "CID10CAP.CNV")
    chapter_of = {}
    # os 22 primeiros são capítulos de verdade; depois vêm "U99" e "não preenchido"
    for _, ch in chapters.head(22).iterrows():
        for code in expand_codes(ch["codigo"]):
            chapter_of.setdefault(code, ch["descricao"])
    cid["capitulo"] = cid["cid"].map(chapter_of).fillna("ignorado")
    cid.to_parquet(aux / "cid10.parquet", index=False)

    mun = parse_cnv(cnv_dir / "BR_municip.cnv")
    mun = mun[mun["codigo"].str.fullmatch(r"\d{6}")]
    names = mun["descricao"].str[7:].str.strip()
    pd.DataFrame({"cod_municipio_6": mun["codigo"], "nome": names}).to_parquet(aux / "municipios.parquet", index=False)


def collect_geo() -> None:
    import geobr

    geo = BRONZE / "geo"
    geo.mkdir(parents=True, exist_ok=True)
    if not (geo / "uf.gpkg").exists():
        with_retry(lambda: geobr.read_state(year=2020)).to_file(geo / "uf.gpkg")
    if not (geo / "municipio.gpkg").exists():
        with_retry(lambda: geobr.read_municipality(year=2022)).to_file(geo / "municipio.gpkg")


def main() -> None:
    ap = argparse.ArgumentParser(description="Coleta SIH/RD + IBGE + auxiliares para data/bronze")
    ap.add_argument("--anos", nargs="+", type=int, default=YEARS)
    ap.add_argument("--ufs", nargs="+", default=UFS)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", choices=["rd", "ibge", "aux", "geo"])
    args = ap.parse_args()
    full = sorted(args.anos) == YEARS and sorted(args.ufs) == sorted(UFS)

    if args.only in (None, "rd"):
        collect_rd(args.anos, args.ufs, args.workers)
        write_metadata(full)
    if args.only in (None, "ibge"):
        collect_ibge(YEARS)
    if args.only in (None, "aux"):
        collect_aux()
    if args.only in (None, "geo"):
        collect_geo()


if __name__ == "__main__":
    main()
