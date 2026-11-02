"""Duty-roster engine - Python port of src/engine.ts (branch python-engine).

Heuristic (greedy + simulated annealing), NOT a proven-optimal solver.
Times are hours from the start of day 0 (fractions allowed, e.g. 6.5 = 06:30).

A task runs inside a clock window each active day, cut into shifts of shiftLen hours.
from=0,to=24 on every day = continuous 24/7. to <= from means the window crosses midnight (e.g. 22 -> 6).
restBefore/restAfter: hard hours of rest a person must have immediately BEFORE starting / AFTER finishing
a shift of this task. Between two consecutive shifts the required gap is the LARGER of the previous
task's restAfter, the next task's restBefore and the global cfg['minRest'] - never their sum.
Rest before the first shift of the horizon and after the last one is NOT checked (no data outside it).
"""
import bisect
import json
import math
from functools import cmp_to_key

U = 8.0  # normalisation unit (hours) for cost terms
EPS = 1e-9


def default_config():
    return {'days': 14, 'minRest': 8, 'comfortRest': 24, 'balance': 0.4,
            'firstWeekday': 0, 'weekendDays': [5, 6], 'seed': 1, 'iterations': 40000}


def js_round(x):
    """Math.round: half towards +infinity (Python's round() is banker's rounding)."""
    return math.floor(x + 0.5)


def dur(s):
    return s['end'] - s['start']


def night_hours(start, end):
    """hours of [start,end) that fall in 22:00-06:00"""
    h = 0.0
    for d in range(math.floor(start / 24) - 1, math.floor(end / 24) + 1):
        for a, b in ((d * 24, d * 24 + 6), (d * 24 + 22, d * 24 + 24)):
            h += max(0.0, min(end, b) - max(start, a))
    return h


def build_slots(cfg, tasks):
    slots = []

    def wk(d):
        return (cfg['firstWeekday'] + d) % 7 in cfg['weekendDays']

    for ti, t in enumerate(tasks):
        length = max(0.5, t['shiftLen'])
        windows = []
        continuous = t['from'] == 0 and t['to'] == 24 and not t.get('days')
        if continuous:
            windows.append((0.0, cfg['days'] * 24.0))
        else:
            for d in range(cfg['days']):
                if t.get('days') and d not in t['days']:
                    continue
                to = t['to'] + 24 if t['to'] <= t['from'] else t['to']
                windows.append((d * 24 + t['from'], d * 24 + to))
        for a, b in windows:
            s = float(a)
            while s < b - EPS:
                e = min(b, s + length)
                slots.append({'id': len(slots), 'day': math.floor(s / 24), 'task': ti,
                              'start': s, 'end': e, 'weekend': wk(math.floor(s / 24))})
                s += length
    slots.sort(key=lambda x: (x['start'], x['task']))
    for i, s in enumerate(slots):
        s['id'] = i
    return slots


MASK = 0xFFFFFFFF


def _i32(x):
    x &= MASK
    return x - 0x100000000 if x >= 0x80000000 else x


def make_rng(seed):
    """Exact port of the mulberry32 variant in engine.ts."""
    s = (seed & MASK) or 1

    def f():
        nonlocal s
        s = (s + 0x6D2B79F5) & MASK
        t = s
        t = _i32((t ^ (t >> 15)) * (t | 1))
        t2 = _i32((t ^ ((t & MASK) >> 7)) * (t | 61))
        t = _i32(t ^ _i32(t + t2))
        return ((t ^ ((t & MASK) >> 14)) & MASK) / 4294967296

    return f


def gap_between(a, b):
    return b['start'] - a['end'] if a['start'] <= b['start'] else a['start'] - b['end']


def required_gap(cfg, tasks, earlier, later):
    """Hard gap between two CONSECUTIVE shifts (earlier ends before later starts) of one person: the
    larger of the earlier task's restAfter, the later task's restBefore and the global minimum. Directional."""
    return max(cfg['minRest'], tasks[earlier['task']].get('restAfter') or 0,
               tasks[later['task']].get('restBefore') or 0)


def on_leave(p, s):
    for d in range(math.floor(s['start'] / 24), math.floor((s['end'] - EPS) / 24) + 1):
        if d in p['leave']:
            return True
    return False


def validate(cfg, people, tasks, slots, assign):
    """Independent hard-constraint checker, also used by tests."""
    v = []
    per = [[] for _ in people]
    for i, s in enumerate(slots):
        real = [x for x in assign[i] if x >= 0]
        if len(set(real)) != len(real):
            v.append('slot %d: same person twice' % i)
        for p in real:
            per[p].append(s)
            if on_leave(people[p], s):
                v.append('%s משובץ ביום %d שבו הוא לא בבסיס' % (people[p]['name'], s['day'] + 1))
    for p, lst in enumerate(per):
        lst.sort(key=lambda x: x['start'])
        for i in range(1, len(lst)):
            g = lst[i]['start'] - lst[i - 1]['end']
            req = required_gap(cfg, tasks, lst[i - 1], lst[i])
            if g < req - EPS:
                v.append('%s: מנוחה %sh < %sh הנדרשת בין "%s" ל-"%s" לפני יום %d' % (
                    people[p]['name'], _num(g), _num(req),
                    tasks[lst[i - 1]['task']]['name'], tasks[lst[i]['task']]['name'], lst[i]['day'] + 1))
    return v


def _num(x):
    return int(x) if float(x) == int(x) else round(x, 6)


def mk_ctx(cfg, people, tasks):
    slots = build_slots(cfg, tasks)
    avail = [cfg['days'] - len({d for d in p['leave'] if 0 <= d < cfg['days']}) for p in people]
    tot_h = 0.0
    night_h = 0.0
    wk_h = 0.0
    sd = [0.0] * len(slots)
    sn = [0.0] * len(slots)
    sw = [False] * len(slots)
    for i, s in enumerate(slots):
        n = tasks[s['task']]['req']
        sd[i] = dur(s)
        sn[i] = night_hours(s['start'], s['end'])
        sw[i] = s['weekend']
        tot_h += sd[i] * n
        night_h += sn[i] * n
        if s['weekend']:
            wk_h += sd[i] * n
    return {'slots': slots, 'avail': avail, 'availSum': sum(avail) or 1,
            'totH': tot_h, 'nightH': night_h, 'wkH': wk_h, 'sd': sd, 'sn': sn, 'sw': sw}


def person_cost(cfg, ctx, p, lst):
    """Shared cost for one person's sorted shift list: fairness (hours, nights, weekend,
    relative to time at base) + rest below comfort. Mirrors costOf per-person term exactly."""
    bal = cfg['balance']
    w_r = (1 - bal) * 2 + 0.05
    c_r = cfg['comfortRest']
    sd = ctx['sd']
    sn = ctx['sn']
    sw = ctx['sw']
    slots = ctx['slots']
    c = 0.0
    h = 0.0
    n = 0.0
    w = 0.0
    prev_end = None
    for sid in lst:
        h += sd[sid]
        n += sn[sid]
        if sw[sid]:
            w += sd[sid]
        if prev_end is not None:
            g = slots[sid]['start'] - prev_end
            if g < c_r:
                c += w_r * ((c_r - g) / U) ** 2
        prev_end = slots[sid]['end']
    t = lambda x: (x * ctx['avail'][p]) / ctx['availSum']
    c += bal * (((h - t(ctx['totH'])) / U) ** 2 + ((n - t(ctx['nightH'])) / U) ** 2 + 0.5 * ((w - t(ctx['wkH'])) / U) ** 2)
    return c


def cost_of(cfg, ctx, per):
    return sum(person_cost(cfg, ctx, p, lst) for p, lst in enumerate(per))


def _exp(x):
    if x > 709:
        return float('inf')
    return math.exp(x)


def solve(cfg, people, tasks):
    ctx = mk_ctx(cfg, people, tasks)
    slots = ctx['slots']
    rand = make_rng(cfg['seed'])
    n_people = len(people)
    pos = []
    for s in slots:
        for k in range(tasks[s['task']]['req']):
            pos.append((s['id'], k))
    assign = [[-1] * tasks[s['task']]['req'] for s in slots]
    per = [[] for _ in people]       # per-person slot ids, ascending (id order == start order)
    per_set = [set() for _ in people]
    pc = [0.0] * n_people
    unc = [len(pos)]                  # every position starts uncovered

    def person_recalc(p):
        pc[p] = person_cost(cfg, ctx, p, per[p])

    for _p in range(n_people):
        person_recalc(_p)

    def unplace(sid, k):
        old = assign[sid][k]
        assign[sid][k] = -1
        if old >= 0:
            unc[0] += 1
            per[old].remove(sid)
            per_set[old].discard(sid)
            person_recalc(old)
        return old

    def take(sid, k, p):
        assign[sid][k] = p
        unc[0] -= 1
        bisect.insort(per[p], sid)
        per_set[p].add(sid)
        person_recalc(p)

    def cost():
        return unc[0] * 1000000 + sum(pc)

    def can_take(p, sid):
        s = slots[sid]
        if on_leave(people[p], s) or p in assign[sid]:
            return False
        for o in per_set[p]:
            if o == sid:
                continue
            os_ = slots[o]
            req = required_gap(cfg, tasks, os_, s) if os_['start'] <= s['start'] else required_gap(cfg, tasks, s, os_)
            if os_['start'] > s['end'] + req + 1 or os_['end'] < s['start'] - req - 1:
                continue
            if gap_between(os_, s) < req - EPS:
                return False
        return True

    load = [0.0] * n_people

    def greedy_cmp(a, b):
        r = load[a] / (ctx['avail'][a] or 1) - load[b] / (ctx['avail'][b] or 1)
        if r:
            return r
        return rand() - 0.5

    for sid, k in pos:
        cands = [p for p in range(n_people) if can_take(p, sid)]
        if not cands:
            continue
        cands.sort(key=cmp_to_key(greedy_cmp))
        take(sid, k, cands[0])
        load[cands[0]] += ctx['sd'][sid]

    cur = cost()
    best = cur
    best_assign = [a[:] for a in assign]
    iters = cfg['iterations']
    for it in range(iters):
        if len(pos) <= 1:
            break
        t = 3 * (1 - it / iters) + 0.01
        a_sid, a_k = pos[int(rand() * len(pos))]
        old = assign[a_sid][a_k]
        if rand() < 0.5:
            p = int(rand() * n_people)
            if p == old:
                continue
            unplace(a_sid, a_k)
            if not can_take(p, a_sid):
                if old >= 0:
                    take(a_sid, a_k, old)
                continue
            take(a_sid, a_k, p)
            nc = cost()
            if nc <= cur or rand() < _exp((cur - nc) / t):
                cur = nc
            else:
                unplace(a_sid, a_k)
                if old >= 0:
                    take(a_sid, a_k, old)
        else:
            b_sid, b_k = pos[int(rand() * len(pos))]
            ob = assign[b_sid][b_k]
            if old == ob or old < 0 or ob < 0 or a_sid == b_sid:
                continue
            unplace(a_sid, a_k)
            unplace(b_sid, b_k)
            if not can_take(ob, a_sid) or not can_take(old, b_sid):
                take(a_sid, a_k, old)
                take(b_sid, b_k, ob)
                continue
            take(a_sid, a_k, ob)
            take(b_sid, b_k, old)
            # the pair check above ignores a<->b interaction; reject if they now clash
            x, y = a_sid, b_sid
            clash = False
            if old in assign[x] and old in assign[y]:
                e, l = (slots[x], slots[y]) if slots[x]['start'] <= slots[y]['start'] else (slots[y], slots[x])
                clash = l['start'] - e['end'] < required_gap(cfg, tasks, e, l) - EPS
            nc = float('inf') if clash else cost()
            if nc <= cur or rand() < _exp((cur - nc) / t):
                cur = nc
            else:
                unplace(a_sid, a_k)
                unplace(b_sid, b_k)
                take(a_sid, a_k, old)
                take(b_sid, b_k, ob)
        if cur < best:
            best = cur
            best_assign = [a[:] for a in assign]
    assign = [a[:] for a in best_assign]

    per_final = [[] for _ in people]
    for i, s in enumerate(slots):
        for p in assign[i]:
            if p >= 0:
                per_final[p].append(s)
    for lst in per_final:
        lst.sort(key=lambda x: x['start'])
    stats = []
    for i, p in enumerate(people):
        lst = per_final[i]
        min_gap = None
        for j in range(1, len(lst)):
            g = lst[j]['start'] - lst[j - 1]['end']
            if min_gap is None or g < min_gap:
                min_gap = g
        stats.append({
            'name': p['name'],
            'shifts': len(lst),
            'hours': sum(dur(s) for s in lst),
            'nights': js_round(sum(night_hours(s['start'], s['end']) for s in lst) * 10) / 10,
            'weekendHours': sum(dur(s) for s in lst if s['weekend']),
            'target': js_round(((ctx['totH'] * ctx['avail'][i]) / ctx['availSum']) * 10) / 10,
            'minGap': min_gap,
            'leaveDays': len({d for d in p['leave'] if 0 <= d < cfg['days']}),
        })
    uncovered = sum(1 for a in assign for p in a if p < 0)
    violations = validate(cfg, people, tasks, slots, assign)
    fz = feasibility(cfg, people, tasks, slots, uncovered)
    return {'feasibility': fz['f'], 'reason': fz['reason'], 'minRestUsed': cfg['minRest'],
            'slots': slots, 'assign': assign, 'uncovered': uncovered, 'violations': violations,
            'stats': stats, 'cost': best}


def feasibility(cfg, people, tasks, slots, uncovered):
    """Counting arguments that PROVE infeasibility. If none applies and coverage failed, the answer is only 'not found'."""
    if uncovered == 0:
        return {'f': 'full', 'reason': ''}
    for s in slots:
        free = sum(1 for p in people if not on_leave(p, s))
        if free < tasks[s['task']]['req']:
            return {'f': 'proven-infeasible',
                    'reason': 'ביום %d (%s) נדרשים %d אנשים אבל רק %d נמצאים בבסיס' % (
                        s['day'] + 1, tasks[s['task']]['name'], tasks[s['task']]['req'], free)}
    for s in slots:
        need = 0
        for o in slots:
            if o['start'] <= s['start'] and o['end'] > s['start']:
                need += tasks[o['task']]['req']
        free = sum(1 for p in people if not on_leave(p, {'start': s['start'], 'end': s['start'] + 1e-6}))
        if need > free:
            return {'f': 'proven-infeasible',
                    'reason': 'ביום %d נדרשים %d אנשים במקביל ורק %d בבסיס' % (s['day'] + 1, need, free)}
    min_len = min(dur(s) for s in slots)
    gap_floor = max(cfg['minRest'],
                    min(t.get('restAfter') or 0 for t in tasks),
                    min(t.get('restBefore') or 0 for t in tasks))
    per_person_max = math.floor((cfg['days'] * 24 + gap_floor) / (min_len + gap_floor))
    demand = sum(tasks[s['task']]['req'] for s in slots)
    if demand > len(people) * per_person_max:
        return {'f': 'proven-infeasible',
                'reason': 'סך העמדות הנדרשות (%d) גדול מהמקסימום האפשרי (%d) כשהמנוחה המינימלית בין משמרות היא %s שעות (כולל דרישות מנוחה של משימות)' % (
                    demand, len(people) * per_person_max, _num(gap_floor))}
    return {'f': 'not-found', 'reason': 'החיפוש לא מצא שיבוץ מלא, אבל זה לא הוכחה שאין כזה'}


def solve_max_rest(cfg, people, tasks):
    """User priority: full coverage first, then as much rest as possible. Tries the largest hard minimum
    rest that still yields full coverage. A failed try only means 'not found'."""
    floor = cfg['minRest']
    r = max(floor, 48)
    while r >= floor:
        c2 = dict(cfg)
        c2['minRest'] = r
        c2['iterations'] = min(cfg['iterations'], 8000)
        res = solve(c2, people, tasks)
        if len(res['slots']) == 0:
            return res
        if res['uncovered'] == 0 and len(res['violations']) == 0:
            c3 = dict(cfg)
            c3['minRest'] = r
            fin = solve(c3, people, tasks)
            return fin if (fin['uncovered'] == 0 and len(fin['violations']) == 0) else res
        r -= 1
    return solve(cfg, people, tasks)


def brute_force(cfg, people, tasks):
    """Exhaustive optimum of the same cost for tiny instances (tests only)."""
    ctx = mk_ctx(cfg, people, tasks)
    slots = ctx['slots']
    pos = []
    for s in slots:
        for k in range(tasks[s['task']]['req']):
            pos.append((s['id'], k))
    assign = [[-1] * tasks[s['task']]['req'] for s in slots]
    best = [None]

    def partial_ok(p):
        lst = [slots[i] for i in range(len(slots)) if p in assign[i]]
        lst.sort(key=lambda x: x['start'])
        for s in lst:
            if on_leave(people[p], s):
                return False
        for i in range(1, len(lst)):
            if lst[i]['start'] - lst[i - 1]['end'] < required_gap(cfg, tasks, lst[i - 1], lst[i]) - EPS:
                return False
        return True

    def rec(i):
        if i == len(pos):
            per = [[] for _ in people]
            for j, s in enumerate(slots):
                for p in assign[j]:
                    if p >= 0:
                        per[p].append(s['id'])
            for lst in per:
                lst.sort()
            c = cost_of(cfg, ctx, per)
            if best[0] is None or c < best[0]['cost']:
                best[0] = {'cost': c, 'assign': [a[:] for a in assign]}
            return
        sid, k = pos[i]
        for p in range(len(people)):
            if p in assign[sid]:
                continue
            assign[sid][k] = p
            if partial_ok(p):
                rec(i + 1)
            assign[sid][k] = -1

    rec(0)
    return best[0]


def _norm_task(t):
    return {'name': t['name'], 'req': t['req'], 'from': t['from'], 'to': t['to'],
            'shiftLen': t['shiftLen'], 'days': t.get('days'),
            'restBefore': t.get('restBefore'), 'restAfter': t.get('restAfter')}


def _clean(x):
    if isinstance(x, float) and math.isinf(x):
        return 1e30
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def solve_json(payload):
    """Browser worker entry point: JSON in, JSON out. Called from pyworker.js via Pyodide."""
    d = json.loads(payload)
    cfg = dict(d['cfg'])
    people = [{'name': p['name'], 'leave': p.get('leave', [])} for p in d['people']]
    tasks = [_norm_task(t) for t in d['tasks']]
    res = solve_max_rest(cfg, people, tasks) if d.get('maxRest') else solve(cfg, people, tasks)
    res = _clean(res)
    res['engine'] = 'python-pyodide'
    return json.dumps(res, ensure_ascii=False)
