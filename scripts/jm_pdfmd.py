"""PDF -> Markdown body for Commonwealth-Caribbean revised-law PDFs.

Uses `pdftotext -bbox-layout` (poppler) to get word coordinates, then:
  * removes running headers/footers (lines in the top/bottom page bands whose
    digit-normalised text repeats across pages, or bare page numbers);
  * separates marginal notes (side notes in the left/right margin column) from
    the main text column and emits them as italic lines before the text they
    sit beside;
  * rebuilds paragraphs from line geometry and numbering patterns;
  * marks PART / SCHEDULE / ARRANGEMENT headings and bold section numbers.
No OCR is performed; scanned PDFs are used only through their own embedded
text layer (if any).
"""
import collections, os, re, statistics, subprocess, tempfile
from lxml import etree

NS = "{http://www.w3.org/1999/xhtml}"

_dict_words = None


def dict_words():
    global _dict_words
    if _dict_words is None:
        _dict_words = set()
        for p in ("/usr/share/dict/words", "/usr/share/dict/web2"):
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="ignore") as f:
                    _dict_words.update(w.strip().lower() for w in f)
                break
        _dict_words.update("""act acts shall thereof therein thereto hereby hereunder herein whereof
        minister ministers regulations regulation subsection subsections paragraph paragraphs
        section sections schedule schedules gazette commissioner commissioners registrar
        amendment amendments amended licence licences licensee jamaica trinidad tobago barbados
        bahamas belize guyana caribbean governor-general governor general ordinance cap chap
        prescribed pursuant notwithstanding""".split())
    return _dict_words


def text_quality(text):
    """Fraction of alphabetic tokens (len>=3) that are dictionary words."""
    toks = re.findall(r"[A-Za-z]{3,}", text)
    if not toks:
        return 0.0
    dw = dict_words()
    good = 0
    for t in toks:
        tl = t.lower()
        if tl in dw or tl.rstrip("s") in dw or (tl.endswith("ed") and tl[:-2] in dw) \
                or (tl.endswith("ing") and tl[:-3] in dw) or (tl.endswith("ies") and tl[:-3] + "y" in dw):
            good += 1
    return good / len(toks)


class Seg:
    __slots__ = ("x0", "y0", "x1", "y1", "text", "page")

    def __init__(self, x0, y0, x1, y1, text, page):
        self.x0, self.y0, self.x1, self.y1, self.text, self.page = x0, y0, x1, y1, text, page


def extract(pdf, tmpdir=None):
    """Return list of pages: (width, height, [Seg])."""
    fd, out = tempfile.mkstemp(suffix=".html", dir=tmpdir)
    os.close(fd)
    try:
        subprocess.run(["pdftotext", "-q", "-bbox-layout", pdf, out], check=True, timeout=600)
        with open(out, "rb") as f:
            data = f.read()
    finally:
        os.unlink(out)
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(data, parser)
    pages = []
    for pn, page in enumerate(root.iter(NS + "page")):
        W, H = float(page.get("width")), float(page.get("height"))
        segs = []
        for line in page.iter(NS + "line"):
            words = [(float(w.get("xMin")), float(w.get("yMin")), float(w.get("xMax")), float(w.get("yMax")),
                      (w.text or "")) for w in line.iter(NS + "word")]
            if not words:
                continue
            # split a line where there is a wide horizontal gap (margin note beside text)
            cur = [words[0]]
            groups = [cur]
            for w in words[1:]:
                if w[0] - cur[-1][2] > 25:
                    cur = [w]
                    groups.append(cur)
                else:
                    cur.append(w)
            for g in groups:
                txt = " ".join(w[4] for w in g).strip()
                if txt:
                    segs.append(Seg(min(w[0] for w in g), min(w[1] for w in g), max(w[2] for w in g),
                                    max(w[3] for w in g), txt, pn))
        pages.append((W, H, segs))
    return pages


def _norm(t):
    t = re.sub(r"\d+", "#", t.lower())
    t = re.sub(r"[ivxlc]+\b", "#", t) if len(t) < 12 else t
    return re.sub(r"\s+", " ", t).strip()


def strip_headers(pages, band=0.17, extra_patterns=(), anywhere_patterns=()):
    """Drop running headers/footers: lines in the top/bottom band (relative to the
    page and to the document's typical text extent) whose digit-normalised text
    repeats on several pages, bare page numbers, and lines matching extra_patterns."""
    tops = [min(s.y0 for s in segs) for W, H, segs in pages if segs]
    bots = [max(s.y1 for s in segs) for W, H, segs in pages if segs]
    ytop = statistics.median(tops) if tops else 0
    ybot = statistics.median(bots) if bots else 0
    bh = (ybot - ytop) * 0.12

    def inband(s, H):
        return s.y1 < H * band or s.y0 > H * (1 - band) or s.y1 < ytop + bh or s.y0 > ybot - bh

    counts = collections.Counter()
    for W, H, segs in pages:
        seen = set()
        for s in segs:
            if inband(s, H):
                n = _norm(s.text)
                if n not in seen:
                    seen.add(n)
                    counts[n] += 1
    npages = len(pages)
    thresh = 2 if npages <= 6 else max(2, min(4, int(npages * 0.15)))
    pats = [re.compile(p, re.I) for p in extra_patterns]
    apats = [re.compile(p, re.I) for p in anywhere_patterns]
    out = []
    for W, H, segs in pages:
        keep = []
        for s in segs:
            if inband(s, H):
                n = _norm(s.text)
                if counts[n] >= thresh or re.fullmatch(r"[-–—\s\[\]]*[#ivxlc]{1,4}[-–—\s\[\]]*", n) \
                        or any(p.search(s.text) for p in pats):
                    continue
            elif any(p.fullmatch(s.text.strip()) for p in pats):
                continue
            if any(p.search(s.text) for p in apats):
                continue
            keep.append(s)
        out.append((W, H, keep))
    return out


def split_columns(W, H, segs):
    """Return (main_segs, margin_segs) for a page."""
    if not segs:
        return [], []
    wide = [s for s in segs if (s.x1 - s.x0) > W * 0.33]
    if len(wide) < 3:
        return segs, []
    L = statistics.median(sorted(s.x0 for s in wide)[: max(1, len(wide) // 3)])
    R = statistics.median(sorted((s.x1 for s in wide), reverse=True)[: max(1, len(wide) // 3)])
    main, margin = [], []
    for s in segs:
        if re.fullmatch(r"\d+[A-Z]{0,2}\.?|\(\w{1,4}\)", s.text):
            main.append(s)  # section numbers in an arrangement table belong to the main column
        elif (s.x1 <= L + 4 and (s.x1 - s.x0) < W * 0.3 and L > W * 0.18) or \
           (s.x0 >= R - 4 and (s.x1 - s.x0) < W * 0.3 and R < W * 0.82):
            margin.append(s)
        else:
            main.append(s)
    return main, margin


START_RE = re.compile(
    r"^(\d+[A-Z]{0,2}\.(\s|[-–—]|\(|$)|\(\d+[A-Z]?\)\s|\([a-z]{1,2}\)\s|\([ivxl]+\)\s|\[[^\]]*\]$|"
    r"PART\s+[IVXLC\d]+|Part\s+[IVXLC\d]+\b|CHAPTER\s|[A-Z ]*SCHEDULE\b|ARRANGEMENT OF|“|\")"
)
END_PUNCT = re.compile(r"[.;:—–\-]$|[.;:]\s*(and|or)$|\)$")


def build_paragraphs(pages):
    """Yield items: ('p', text) or ('note', text) or ('pb', None)."""
    items = []
    for W, H, segs in pages:
        main, margin = split_columns(W, H, segs)
        main.sort(key=lambda s: (round(s.y0 / 3), s.x0))
        margin.sort(key=lambda s: (s.y0, s.x0))
        # group margin segs into notes
        notes = []
        for s in margin:
            if notes and s.y0 - notes[-1][-1].y1 < (s.y1 - s.y0) * 0.9 and abs(s.x0 - notes[-1][-1].x0) < 40:
                notes[-1].append(s)
            else:
                notes.append([s])
        if not main and not notes:
            continue
        gaps = [b.y0 - a.y1 for a, b in zip(main, main[1:]) if 0 <= b.y0 - a.y1 < 40]
        typ = statistics.median(gaps) if gaps else 3.0
        right = max((s.x1 for s in main), default=W)
        left = min((s.x0 for s in main), default=0)
        ni = 0
        cur = []
        prev = None

        def flush():
            if cur:
                items.append(("p", join_lines([c.text for c in cur])))
                cur.clear()
        for s in main:
            while ni < len(notes) and notes[ni][0].y0 <= s.y1 - 1:
                flush()
                items.append(("note", join_lines([x.text for x in notes[ni]])))
                ni += 1
            brk = False
            if prev is not None:
                gap = s.y0 - prev.y1
                if gap > max(typ * 1.8, typ + 4):
                    brk = True
                elif abs(s.y0 - prev.y0) < 2:  # same line, different segment (table cell)
                    brk = False
                elif START_RE.match(s.text):
                    brk = True
                elif END_PUNCT.search(prev.text) and prev.x1 < right - (right - left) * 0.15:
                    brk = True
                elif prev.x1 < left + (right - left) * 0.55 and len(prev.text) < 60:
                    brk = True
                if abs(s.y0 - prev.y0) < 2 and prev is not None:
                    cur.append(s)
                    prev = s
                    continue
            if brk:
                flush()
            cur.append(s)
            prev = s
        flush()
        while ni < len(notes):
            items.append(("note", join_lines([x.text for x in notes[ni]])))
            ni += 1
        items.append(("pb", None))
    return items


def join_lines(lines):
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not out:
            out = ln
        elif out.endswith("-") and not out.endswith(" -") and ln[:1].isalpha():
            a = re.search(r"([A-Za-z]+)-$", out)
            b = re.match(r"([a-z]+)", ln)
            if a and b and (a.group(1) + b.group(1)).lower() in dict_words() \
                    and (a.group(1) + "-" + b.group(1)).lower() not in dict_words():
                out = out[:-1] + ln  # soft hyphenation at line end
            else:
                out += ln
        else:
            out += " " + ln
    return re.sub(r"[ \t]+", " ", out).strip()


HEAD_RE = re.compile(r"^(PART|Part)\s+([IVXLC]+|\d+)[A-Z]?\b\.?\s*(.*)$")
SCHED_RE = re.compile(r"^((FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH|"
                      r"THIRTEENTH|FOURTEENTH|FIFTEENTH)\s+)?SCHEDULE\b.*$")
SEC_RE = re.compile(r"^(\d+[A-Z]{0,2})\.(\s*[-–—]\s*|\s+|(?=\())(.*)$", re.S)


def is_caps_title(t):
    letters = re.sub(r"[^A-Za-z]", "", t)
    return 3 <= len(letters) and t.upper() == t and len(t) <= 120 and not re.match(r"^\d", t) \
        and not t.endswith((",", ";"))


def md_escape(t):
    t = t.replace("­", "")
    if re.match(r"^([#>+*-]|\d+[.)]\s)", t):
        t = "\\" + t
    return t.replace("<", "&lt;") if re.search(r"<[A-Za-z/!]", t) else t


def to_markdown(items, title=None):
    lines = []
    pending_head = None
    i = 0
    seq = [x for x in items if x[0] != "pb"]
    n = len(seq)
    while i < n:
        kind, t = seq[i]
        if kind == "note":
            lines.append("*" + md_escape(t).replace("*", "\\*") + "*")
            i += 1
            continue
        m = HEAD_RE.match(t)
        if m and len(t) < 140:
            head = t
            # absorb following ALL-CAPS title paragraph
            if not m.group(3) and i + 1 < n and seq[i + 1][0] == "p" and is_caps_title(seq[i + 1][1]):
                head = t + " — " + seq[i + 1][1]
                i += 1
            lines.append("## " + head)
            i += 1
            continue
        if SCHED_RE.match(t) and len(t) < 160:
            lines.append("## " + t)
            i += 1
            continue
        if t.upper().startswith("ARRANGEMENT OF") and len(t) < 60:
            lines.append("## " + t)
            i += 1
            continue
        if is_caps_title(t) and len(t) < 90 and len(t.split()) <= 14 and not re.search(r"[.:]$", t) \
                and not re.fullmatch(r"[\W\d]+", t):
            lines.append("### " + t)
            i += 1
            continue
        ms = SEC_RE.match(t)
        if ms and len(ms.group(1)) <= 5:
            rest = ms.group(3).strip()
            lines.append(f"**{ms.group(1)}.** " + (md_escape(rest) if rest else ""))
            i += 1
            continue
        lines.append(md_escape(t))
        i += 1
    md = "\n\n".join(l.rstrip() for l in lines if l.strip())
    return md + "\n"


def pdf_to_md(pdf, extra_header_patterns=(), anywhere_patterns=(), tmpdir=None):
    pages = extract(pdf, tmpdir)
    npages = len(pages)
    chars = sum(len(s.text) for _, _, segs in pages for s in segs)
    pages = strip_headers(pages, extra_patterns=extra_header_patterns, anywhere_patterns=anywhere_patterns)
    items = build_paragraphs(pages)
    body = to_markdown(items)
    raw = " ".join(t for k, t in items if t)
    return body, {"pages": npages, "chars": chars, "chars_per_page": chars / max(1, npages),
                  "quality": round(text_quality(raw), 3)}
