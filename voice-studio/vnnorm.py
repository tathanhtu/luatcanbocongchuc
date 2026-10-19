"""Chuẩn hoá văn bản pháp luật tiếng Việt để đọc thành tiếng: số, ngày tháng, số hiệu văn bản, viết tắt."""
import re

DIG = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _below_1000(n, full):
    h, t, u = n // 100, n // 10 % 10, n % 10
    p = []
    if h or full:
        p.append(DIG[h] + " trăm")
    if t == 0:
        if u:
            if h or full:
                p.append("linh")
            p.append(DIG[u])
    elif t == 1:
        p.append("mười")
        if u:
            p.append("lăm" if u == 5 else DIG[u])
    else:
        p.append(DIG[t] + " mươi")
        if u:
            p.append("mốt" if u == 1 else "lăm" if u == 5 else DIG[u])
    return " ".join(p)


def _below_billion(n, full):
    units = ["", "nghìn", "triệu"]
    groups = [n % 1000, n // 1000 % 1000, n // 10**6]
    out, printed = [], full
    for i in (2, 1, 0):
        g = groups[i]
        if g == 0:
            continue
        s = _below_1000(g, printed)
        out.append((s + " " + units[i]).strip())
        printed = True
    return " ".join(out)


def vn_int(n):
    if n == 0:
        return "không"
    if n >= 10**9:
        hi, lo = divmod(n, 10**9)
        return vn_int(hi) + " tỷ" + (" " + _below_billion(lo, True) if lo else "")
    return _below_billion(n, False)


def _num(s):
    s = s.lstrip("0") or "0"
    return vn_int(int(s)) if len(s) <= 12 else " ".join(DIG[int(c)] for c in s)


ISSUER = {
    "CP": "của Chính phủ", "TTg": "của Thủ tướng Chính phủ", "BNV": "của Bộ Nội vụ", "BTC": "của Bộ Tài chính",
    "BCA": "của Bộ Công an", "BQP": "của Bộ Quốc phòng", "BTP": "của Bộ Tư pháp", "BGDĐT": "của Bộ Giáo dục và Đào tạo",
    "BLĐTBXH": "của Bộ Lao động, Thương binh và Xã hội", "BYT": "của Bộ Y tế", "BTTTT": "của Bộ Thông tin và Truyền thông",
}
ABBR = {"UBND": "Ủy ban nhân dân", "HĐND": "Hội đồng nhân dân", "UBTVQH": "Ủy ban Thường vụ Quốc hội",
        "CBCC": "cán bộ, công chức", "VBQPPL": "văn bản quy phạm pháp luật", "TW": "Trung ương"}


def _issuer(code):
    last = code.split("-")[-1]
    m = re.fullmatch(r"(QH|UBTVQH)(\d+)", last)
    if m:
        return ("của Quốc hội khóa " if m.group(1) == "QH" else "của Ủy ban Thường vụ Quốc hội khóa ") + _num(m.group(2))
    return ISSUER.get(last, code)


def normalize(text):
    t = text
    t = re.sub(r"\b(\d+)/(\d{4})/([A-ZĐ][A-Za-zĐ0-9\-]*)",
               lambda m: f"{_num(m.group(1))} năm {_num(m.group(2))} {_issuer(m.group(3))}", t)
    t = re.sub(r"(?:\bngày\s+)?\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
               lambda m: f"ngày {_num(m.group(1))} tháng {'tư' if int(m.group(2)) == 4 else _num(m.group(2))} năm {_num(m.group(3))}", t)
    t = re.sub(r"\btháng\s+4\b", "tháng tư", t)
    t = re.sub(r"\b\d{1,3}(?:\.\d{3})+\b", lambda m: _num(m.group(0).replace(".", "")), t)
    t = re.sub(r"\b(\d+),(\d{1,2})\b", lambda m: f"{_num(m.group(1))} phẩy {' '.join(DIG[int(c)] for c in m.group(2))}", t)
    t = re.sub(r"%", " phần trăm", t)
    t = re.sub(r"\b\d+\b", lambda m: _num(m.group(0)), t)
    for k, v in ABBR.items():
        t = re.sub(rf"\b{k}\b", v, t)
    return re.sub(r"[ \t]+", " ", t)
