"""Read EasyEDA Pro v3 project connectivity without modifying the input."""
import collections
import json
import math
from pathlib import Path


def documents(source):
    result = []
    for line in source.splitlines():
        shell, body = line.split('||', 1)
        h = json.loads(shell)
        body = body.rstrip('|')
        v = json.loads(body) if body else None
        if h['type'] == 'DOCHEAD':
            current = {'head': v, 'items': []}
            result.append(current)
        current['items'].append({'h': h, 'v': v})
    return result


def entities(doc, kind):
    result = {r['h']['id']: dict(r['v']) for r in doc['items']
              if r['h']['type'] == kind and r['v']}
    for r in doc['items']:
        if r['h']['type'] == 'ATTR' and r['v']['parentId'] in result:
            result[r['v']['parentId']][r['v']['key']] = r['v']['value']
    return result


def connected(point, line):
    x, y = point
    x0, y0, x1, y1 = [line[k] for k in ('startX', 'startY', 'endX', 'endY')]
    return (min(x0, x1)-1e-5 <= x <= max(x0, x1)+1e-5
            and min(y0, y1)-1e-5 <= y <= max(y0, y1)+1e-5
            and abs((x-x0)*(y1-y0)-(y-y0)*(x1-x0)) < 1e-4)


def netlist(docs):
    symbols = {d['head']['uuid']: entities(d, 'PIN') for d in docs
               if d['head']['docType'] == 'SYMBOL'}
    devices = {d['head']['uuid']: next(r['v']['attributes'] for r in d['items']
                                     if r['h']['type'] == 'META')
               for d in docs if d['head']['docType'] == 'DEVICE'}
    all_parts = {}
    issues = []
    for doc in docs:
        if doc['head']['docType'] != 'SCH_PAGE':
            continue
        comps = entities(doc, 'COMPONENT')
        wires = entities(doc, 'WIRE')
        lines = [r['v'] for r in doc['items'] if r['h']['type'] == 'LINE' and r['v']
                 and r['v'].get('lineGroup') in wires]
        parent = {k: k for k in wires}
        def root(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k
        # A wire endpoint on a segment is a junction; two crossing interiors are not.
        for i, a in enumerate(lines):
            for b in lines[:i]:
                if a['lineGroup'] == b['lineGroup']:
                    continue
                if any(connected(pt, other) for pt, other in (
                        ((a['startX'], a['startY']), b),
                        ((a['endX'], a['endY']), b),
                        ((b['startX'], b['startY']), a),
                        ((b['endX'], b['endY']), a))):
                    parent[root(a['lineGroup'])] = root(b['lineGroup'])
        names = collections.defaultdict(set)
        for wid, w in wires.items():
            if w.get('NET'):
                names[root(wid)].add(w['NET'])
        pins_by_comp = {}
        for cid, c in comps.items():
            inherited = devices.get(c.get('Device'), {})
            attrs = {**inherited, **c}
            sym = symbols.get(attrs.get('Symbol'), {})
            pins = {}
            for pid, pin in sym.items():
                # EPRU symbol coordinates use the opposite rotation sign to
                # the component rotation field (confirmed against native nets).
                px, py = pin['x'], pin['y']
                if c.get('isMirror'):
                    px = -px
                angle = -math.radians(c.get('rotation', 0))
                pt = (round(c['x']+px*math.cos(angle)-py*math.sin(angle), 6),
                      round(c['y']+px*math.sin(angle)+py*math.cos(angle), 6))
                groups = {root(l['lineGroup']) for l in lines if connected(pt, l)}
                if len(groups) > 1:
                    groups = sorted(groups)
                    for g in groups[1:]:
                        parent[root(g)] = root(groups[0])
                    groups = {root(groups[0])}
                pins[pin.get('Pin Number', pid)] = {'name': pin.get('Pin Name'),
                    'point': pt, 'group': next(iter(groups), None)}
                if attrs.get('Component Type') == 'netflag' or c.get('partId', '').startswith('pid'):
                    label = attrs.get('Name') or attrs.get('Net')
                    if groups and label and not label.startswith('='):
                        names[next(iter(groups))].add(label)
            pins_by_comp[cid] = (attrs, pins)
        combined = collections.defaultdict(set)
        for g, ns in names.items():
            combined[root(g)].update(ns)
        for g, ns in combined.items():
            # VCC/12v aliases in the source intentionally share one conductor.
            if len(ns) > 1:
                issues.append({'page': doc['head']['uuid'], 'aliases': sorted(ns)})
        for cid, (attrs, pins) in pins_by_comp.items():
            ref = attrs.get('Designator')
            if not ref or ref.endswith('?'):
                continue
            for p in pins.values():
                g = root(p['group']) if p['group'] is not None else None
                ns = combined.get(g, set())
                p['net'] = (sorted(ns)[0] if ns else
                            f"${doc['head']['uuid'][:4]}_{g}" if g else '')
                p.pop('group')
            if ref in all_parts:
                issues.append({'duplicate_designator': ref})
            all_parts[ref] = {'id': cid, 'page': doc['head']['uuid'],
                              'attrs': attrs, 'pins': pins}
    return all_parts, issues


if __name__ == '__main__':
    import sys
    source = Path(sys.argv[1]).read_text(encoding='utf-8')
    parts, issues = netlist(documents(source))
    target = Path(sys.argv[2])
    target.write_text(json.dumps({'parts': parts, 'issues': issues},
                                ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(parts)} parts; {len(issues)} alias/duplicate notices; {target}')
