#!/usr/bin/env python3
"""Fetch the Revised Laws of Jamaica (statutes) from laws.moj.gov.jm.

Lists statutes via the site's DataTables JSON endpoint, fetches each statute
detail page (metadata) and downloads the PDF into _raw/jm/.
Usage: jm_fetch.py [statutes|subsidiary]
"""
import json, os, re, subprocess, sys, time
import requests
from bs4 import BeautifulSoup

BASE = "https://laws.moj.gov.jm"
UA = "LawCodex/1.0 (+https://github.com/LawCodex)"
RAW = os.environ.get("JM_RAW", os.path.join(os.path.dirname(__file__), "..", "..", "_raw", "jm"))
DELAY = 1.0

s = requests.Session()
s.headers["User-Agent"] = UA


def ca_bundle():
    """laws.moj.gov.jm does not send its DigiCert intermediate certificate;
    build a bundle = certifi roots + that intermediate (fetched from DigiCert)."""
    import certifi
    path = os.path.join(RAW, "cabundle.pem")
    if not os.path.exists(path):
        der = requests.get("http://cacerts.digicert.com/DigiCertGlobalG2TLSRSASHA2562020CA1-1.crt",
                           headers={"User-Agent": UA}, timeout=60).content
        pem = subprocess.run(["openssl", "x509", "-inform", "der"], input=der,
                             capture_output=True, check=True).stdout
        os.makedirs(RAW, exist_ok=True)
        with open(path, "wb") as f:
            f.write(open(certifi.where(), "rb").read() + b"\n" + pem)
    return path


s.verify = ca_bundle()


def get(url, **kw):
    for i in range(5):
        try:
            r = s.get(url, timeout=180, **kw)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return r
        except requests.RequestException as e:
            print("  retry", url, e, file=sys.stderr)
        time.sleep(5 * (i + 1))
    raise RuntimeError("failed " + url)


def pdf_ok(path, expected_len=None):
    if not os.path.exists(path):
        return False
    if expected_len and os.path.getsize(path) != expected_len:
        return False
    with open(path, "rb") as f:
        head = f.read(5)
        f.seek(max(0, os.path.getsize(path) - 2048))
        tail = f.read()
    if head != b"%PDF-" or b"%%EOF" not in tail:
        return False
    return subprocess.run(["pdfinfo", path], capture_output=True).returncode == 0


def listing(kind):
    path = {"statutes": "/library/statutes/revised",
            "subsidiary": "/library/subsidiary-legislation/revised"}[kind]
    r = get(BASE + path, params={"_dt": "dt", "_init": "true", "draw": 1, "start": 0, "length": 5000},
            headers={"X-Requested-With": "XMLHttpRequest"})
    out = []
    for row in r.json()["data"]:
        m = re.search(r'href="([^"]+)"', row["shortTitle"])
        title = re.sub(r"<[^>]+>", "", row["shortTitle"]).strip()
        out.append({"id": row["DT_RowId"], "title": title, "href": m.group(1),
                    "legal_areas": row.get("legalAreas", "")})
    return out


def detail(href):
    soup = BeautifulSoup(get(BASE + href).text, "lxml")
    meta = {}
    main = soup.find("main") or soup
    for txt in main.get_text("\n").split("\n"):
        if " : " in txt or txt.strip().endswith(":"):
            pass
    # rows are "<label> : <value>" in table cells / paragraphs
    text = re.sub(r"[ \t]+", " ", main.get_text("\n"))
    for key in ["Preamble", "Long Title", "Short Title", "Operational Date", "Number", "Last Amendment"]:
        m = re.search(r"\b" + key + r"[ \t]*:[ \t]*([^\n]*)", text)
        if m:
            meta[key] = m.group(1).strip()
    dl = main.find("a", href=re.compile(r"/download$"))
    meta["download"] = dl["href"] if dl else None
    return meta


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "statutes"
    # optional sharding (the server is slow): jm_fetch.py statutes K N  -> items with idx % N == K
    shard_k, shard_n = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 1)
    os.makedirs(os.path.join(RAW, kind, "pdf"), exist_ok=True)
    os.makedirs(os.path.join(RAW, kind, "meta"), exist_ok=True)
    lp = os.path.join(RAW, kind, "list.json")
    if os.path.exists(lp):
        items = json.load(open(lp))
    else:
        items = listing(kind)
        json.dump(items, open(lp, "w"), indent=1)
    print(len(items), "items")
    for n, it in enumerate(items):
        if n % shard_n != shard_k:
            continue
        slug = it["href"].rstrip("/").split("/")[-1]
        mpath = os.path.join(RAW, kind, "meta", slug + ".json")
        ppath = os.path.join(RAW, kind, "pdf", slug + ".pdf")
        if os.path.exists(mpath):
            meta = json.load(open(mpath))
        else:
            meta = dict(it)
            meta.update(detail(it["href"]))
            time.sleep(DELAY)
        if meta.get("download") and not meta.get("pdf_ok"):
            if not pdf_ok(ppath, meta.get("pdf_len")):
                r = get(BASE + meta["download"])
                ct = r.headers.get("content-type", "")
                if r.status_code == 200 and "pdf" in ct:
                    open(ppath, "wb").write(r.content)
                    cl = r.headers.get("content-length")
                    meta["pdf_len"] = int(cl) if cl else len(r.content)
                else:
                    meta["error"] = f"{r.status_code} {ct}"
                time.sleep(DELAY)
            meta["pdf_ok"] = pdf_ok(ppath, meta.get("pdf_len"))
        json.dump(meta, open(mpath, "w"), indent=1)
        print(n, slug, meta.get("pdf_ok"), meta.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
