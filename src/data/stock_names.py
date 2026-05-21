"""从通达信本地 tnf 文件加载股票代码->名称映射，用于 ST 判断"""

import os

from config import TDX_DIR

_RECORD_SIZE = 360
_NAME_OFFSET = 31
_NAME_LEN = 20

_cache = None


def _find_header(data):
    for off in range(0, _RECORD_SIZE):
        code = data[off:off + 6]
        if not code.isdigit():
            continue
        name_raw = data[off + _NAME_OFFSET:off + _NAME_OFFSET + _NAME_LEN]
        ne = name_raw.find(b'\x00')
        if ne >= 0:
            name_raw = name_raw[:ne]
        if not name_raw:
            continue
        try:
            name_raw.decode('gbk')
        except Exception:
            continue
        nxt = off + _RECORD_SIZE
        if nxt + 6 < len(data) and data[nxt:nxt + 6].isdigit():
            return off
    return None


def load_stock_names(tdxdir=TDX_DIR):
    """返回 {code: name} 字典，如 {'600745': '*ST闻泰'}"""
    names = {}
    for fname in ('shs.tnf', 'szs.tnf'):
        path = os.path.join(tdxdir, 'T0002', 'hq_cache', fname)
        if not os.path.exists(path):
            continue
        with open(path, 'rb') as f:
            data = f.read()
        header = _find_header(data)
        if header is None:
            continue
        for off in range(header, len(data) - _RECORD_SIZE, _RECORD_SIZE):
            code_raw = data[off:off + 6]
            if not code_raw.isdigit():
                continue
            name_raw = data[off + _NAME_OFFSET:off + _NAME_OFFSET + _NAME_LEN]
            ne = name_raw.find(b'\x00')
            if ne >= 0:
                name_raw = name_raw[:ne]
            if not name_raw:
                continue
            try:
                names[code_raw.decode('ascii')] = name_raw.decode('gbk', errors='replace')
            except Exception:
                pass
    return names


def get_stock_names():
    """带缓存的全局访问"""
    global _cache
    if _cache is None:
        _cache = load_stock_names()
    return _cache


def is_st_stock(code, names=None):
    """判断股票是否处于 ST 状态（含 *ST / ST / S*ST 等）"""
    if names is None:
        names = get_stock_names()
    name = names.get(code, '')
    return 'ST' in name.upper()
