"""Binged public client's stable per-server routing, without executing upstream JS."""
import re
from refresh import HOSTS
from urllib.parse import urlsplit


def src_hash(value):
    x = 2166136261
    for c in str(value):
        x = ((x ^ ord(c)) * 16777619) & 0xffffffff
    x ^= x >> 16
    x = (x * 0x85ebca6b) & 0xffffffff
    x ^= x >> 13
    x = (x * 0xc2b2ae35) & 0xffffffff
    return (x ^ (x >> 16)) & 0xffffffff


def load_config(text):
    block = re.search(r'streamOrigins\s*:\s*\[([^]]+)\]', text)
    if not block:
        raise ValueError('missing_stream_origins')
    origins = re.findall(r"['\"](https://[^'\"]+)['\"]", block[1])
    for origin in origins:
        u = urlsplit(origin)
        if u.hostname not in HOSTS or u.path or u.port not in (None, 443) or u.username:
            raise ValueError('unrecognized_provider_origin')
    weights_block = re.search(r'streamWeights\s*:\s*\{([^}]+)\}', text)
    pairs = lambda text: {k: int(v) for k, v in re.findall(r"['\"](https://[^'\"]+)['\"]\s*:\s*(\d+)", text)}
    weights = pairs(weights_block[1]) if weights_block else {}
    group_weights = {}
    if 'streamGroupWeights' in text:
        for group, data in re.findall(r"['\"]([^'\"]+)['\"]\s*:\s*\{([^}]+)\}", text.split('streamGroupWeights', 1)[1]):
            group_weights[group] = pairs(data)
    if not origins:
        raise ValueError('empty_provider_origins')
    return {'origins': origins, 'weights': weights, 'group_weights': group_weights}


class Router:
    def __init__(self, catalog, config):
        self.config = config
        self.group_for = {}
        self.group_ids = {}
        for rows in catalog.values():
            for r in rows:
                sid, group = r.get('id'), r.get('group')
                if sid and group and sid not in self.group_for:
                    self.group_for[sid] = group
                    self.group_ids.setdefault(group, []).append(sid)
        for ids in self.group_ids.values():
            ids.sort(key=src_hash)

    def origin(self, sid):
        origins = self.config['origins']
        group = self.group_for.get(sid, '')
        gw = self.config['group_weights'].get(group)
        if gw:
            pool = sorted(o for o in origins if gw.get(o, 0) > 0)
            if len(pool) == 1:
                return pool[0]
            if pool:
                ids = self.group_ids.get(group, [sid])
                pos = (ids.index(sid) if sid in ids else 0) / len(ids)
                total = sum(gw[o] for o in pool)
                cum = 0
                for o in pool:
                    cum += gw[o] / total
                    if pos < cum - 1e-9:
                        return o
                return pool[-1]
        slots = [o for o in origins for _ in range(max(1, self.config['weights'].get(o, 1)))]
        return slots[src_hash(sid) % len(slots)]
