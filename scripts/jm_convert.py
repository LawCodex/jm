#!/usr/bin/env python3
"""Convert downloaded Revised Laws of Jamaica PDFs (_raw/jm) to Markdown.

The official PDFs are mostly scanned pages carrying an embedded OCR text layer
(Adobe Acrobat Paper Capture). We use that text layer as-is (no OCR of our
own); documents without a usable text layer are reported as gaps.
"""
import datetime, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jm_pdfmd as pm

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RAW = os.path.abspath(os.path.join(ROOT, "..", "_raw", "jm"))
BASE = "https://laws.moj.gov.jm"
MIN_QUALITY = 0.70
MIN_CHARS_PER_PAGE = 150
JUNK = [r"inclusion\s*o\s*f\s+this\s+page", r"authori[sz]ed\s+by\s+L\.?\s*N", r"^\W*The\s+inclusion"]


def ydate(s):
    try:
        return datetime.datetime.strptime(s.strip(), "%B %d, %Y").date().isoformat()
    except Exception:
        return None


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def front(d):
    out = ["---"]
    for k, v in d.items():
        if v is None or v == "":
            continue
        out.append(f"{k}: {v if isinstance(v, (int, float)) or k in ('date', 'last_amended', 'retrieved', 'type', 'jurisdiction', 'language', 'status') else q(v)}")
    out.append("---")
    return "\n".join(out) + "\n\n"


def main():
    kind = "statutes"
    retrieved = datetime.date.today().isoformat()
    metas = sorted(os.listdir(os.path.join(RAW, kind, "meta")))
    os.makedirs(os.path.join(ROOT, "chapters"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "constitution"), exist_ok=True)
    report = {"converted": [], "gaps": []}
    for mf in metas:
        m = json.load(open(os.path.join(RAW, kind, "meta", mf)))
        slug = mf[:-5]
        pdf = os.path.join(RAW, kind, "pdf", slug + ".pdf")
        title = m.get("Short Title") or m["title"]
        if not m.get("pdf_ok") or not os.path.exists(pdf):
            report["gaps"].append({"title": title, "reason": "PDF not downloadable", "source": BASE + m["href"]})
            continue
        body, st = pm.pdf_to_md(pdf, anywhere_patterns=JUNK, tmpdir=RAW)
        if st["chars_per_page"] < MIN_CHARS_PER_PAGE or st["quality"] < MIN_QUALITY:
            report["gaps"].append({"title": title, "reason": f"scanned PDF without usable text layer "
                                   f"(chars/page {st['chars_per_page']:.0f}, quality {st['quality']})",
                                   "source": BASE + m["href"]})
            continue
        is_const = "constitution-order-in-council" in slug
        fname = re.sub(r"^the-", "", slug)[:100].strip("-") + ".md"
        rel = ("constitution/" if is_const else "chapters/") + fname
        num = m.get("Number", "")
        fm = {
            "title": title,
            "identifier": num if re.search(r"\d", num) else None,
            "type": "constitution" if is_const else "act",
            "jurisdiction": "jm", "language": "en",
            "date": ydate(m.get("Operational Date", "")),
            "last_amended": ydate(m.get("Last Amendment", "")),
            "status": "in_force",
            "long_title": m.get("Preamble") or None,
            "legal_areas": m.get("legal_areas") or None,
            "source": BASE + m["href"],
            "source_pdf": BASE + m["download"],
            "text_source": "embedded text layer of the official PDF (OCR by the publisher; may contain recognition errors)",
            "pages": st["pages"],
            "retrieved": retrieved,
        }
        with open(os.path.join(ROOT, rel), "w", encoding="utf-8") as f:
            f.write(front(fm) + f"# {title}\n\n" + body)
        report["converted"].append({"title": title, "file": rel, "quality": st["quality"]})
        print(rel, st["quality"], flush=True)
    json.dump(report, open(os.path.join(RAW, "convert_report.json"), "w"), indent=1)
    print(len(report["converted"]), "converted;", len(report["gaps"]), "gaps")


if __name__ == "__main__":
    main()
