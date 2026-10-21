"""Team-Lotterie: Teil 1 (erwartete Punkte) + Teil 2 (Balancing). Reine Rechenlogik, ohne DB."""
import math, random

POINTS = [50,44,40,38,36,34,32,30,28,26,25,24,23,22,21,20,19,18,17,16,15,14,13,12,11,10,9,8,7,6]
GUMBEL = 400 / math.log(10)   # passt zur ELO-Formel: P(i schlaegt j)=1/(1+10^((mj-mi)/400))

def pts(rank):  # rank 0-basiert
    return POINTS[rank] if rank < len(POINTS) else POINTS[-1]

DNF_POINTS = 2   # Punkte fuer einen Ausfall (ohne CR)

def dnf_rates(stats, names, prior_starts=10, weight=0.5, cap=0.25):
    """stats: {name: (dnf_count, total_starts)} -> {name: Ausfallwahrscheinlichkeit pro Event}.
    Glaettung zum Durchschnitt (prior_starts Pseudo-Starts), dann nur `weight` der Abweichung, gedeckelt."""
    D = sum(a for a, b in stats.values()); S = sum(b for a, b in stats.values())
    avg = D / S if S else 0.0; out = {}
    for n in names:
        a, b = stats.get(n, (0, 0))
        smooth = (a + prior_starts * avg) / (b + prior_starts)
        out[n] = min(cap, max(0.0, avg + weight * (smooth - avg)))
    return out

def expected_points(players, sims=4000, seed=1, dnf=None):
    """players: {name: (mu, sigma)} -> {name: erwartete Punkte}. Sigma = Unsicherheit der wahren Staerke.
    dnf: optional {name: p} = Ausfallwahrscheinlichkeit; Ausfaelle werden hinter allen Finishern gewertet (DNF_POINTS)."""
    rnd = random.Random(seed); names = list(players); tot = dict.fromkeys(names, 0.0)
    for _ in range(sims):
        perf = {}; out = []
        for n in names:
            mu, sg = players[n]
            if dnf and rnd.random() < dnf.get(n, 0.0): out.append(n); continue
            perf[n] = rnd.gauss(mu, sg) + GUMBEL * (-math.log(-math.log(rnd.random())))
        for r, n in enumerate(sorted(perf, key=perf.get, reverse=True)):
            tot[n] += pts(r)
        for n in out: tot[n] += DNF_POINTS
    return {n: tot[n] / sims for n in names}

def n_teams(n):
    return max(2, round(n / 3))

def _draw_k(ep, firm_groups, soft_wishes, seed, iters, restarts, cap, soft_w, k):
    """ep: {name: erwartete Punkte}; firm_groups: Listen fester Gruppen (Kreise 2-4);
    soft_wishes: [(wunschender, partner)] einseitige Wuensche.
    Prioritaet 1: groesste Abweichung eines Teams vom Schnitt <= cap (Standard 9 %, Toleranz 10 %).
    Prioritaet 2: so viele Wuensche wie moeglich erfuellen (innerhalb der Toleranz entscheiden nur sie).
    Prioritaet 3: moeglichst viele 3er-Teams, kleine Restabweichung. Rueckgabe: (teams, info)."""
    rnd = random.Random(seed)
    names = list(ep)
    inb = {n for g in firm_groups for n in g}
    units = [list(g) for g in firm_groups] + [[n] for n in names if n not in inb]
    soft = [(a, b) for a, b in soft_wishes if a in ep and b in ep]
    def unsat(teams):
        if not soft: return 0
        where = {m: i for i, t in enumerate(teams) for m in t}
        return sum(1 for a, b in soft if where[a] != where[b])
    def devof(teams):
        s = [sum(ep[m] for m in t) for t in teams]; mean = sum(s) / len(s)
        return max(abs(x - mean) for x in s) / mean
    def cost(teams):
        dev = devof(teams)
        c = 5 * max(0.0, dev - cap) + 0.05 * dev
        c += 0.02 * sum(1 for t in teams if len(t) != 3)
        c += soft_w * unsat(teams)
        if any(len(t) < 2 or len(t) > 4 for t in teams): c += 10
        return c
    best = None
    for restart in range(restarts):
        teams = [[] for _ in range(k)]
        order = sorted(units, key=lambda u: -sum(ep[m] for m in u))
        if restart: rnd.shuffle(order)
        for u in order:
            cand = [t for t in teams if len(t) + len(u) <= 4] or teams
            t = min(cand, key=lambda t: (len(t) >= 3, sum(ep[m] for m in t)))
            t.extend(u)
        cur = cost(teams)
        for i in range(iters):
            T = 0.05 * (1 - i / iters) + 1e-4
            a, b = rnd.sample(range(k), 2)
            ua = rnd.choice([u for u in units if all(m in teams[a] for m in u)] or [None])
            if ua is None: continue
            new = [list(t) for t in teams]
            if rnd.random() < 0.4:
                if len(teams[a]) - len(ua) < 2 or len(teams[b]) + len(ua) > 4: continue
                for m in ua: new[a].remove(m)
                new[b].extend(ua)
            else:
                ub = rnd.choice([u for u in units if all(m in teams[b] for m in u)] or [None])
                if ub is None: continue
                for m in ua: new[a].remove(m)
                for m in ub: new[b].remove(m)
                new[a].extend(ub); new[b].extend(ua)
            c = cost(new)
            if c < cur or rnd.random() < math.exp((cur - c) / T):
                teams, cur = new, c
        if best is None or cur < best[0]: best = (cur, [list(t) for t in teams])
    teams = best[1]
    s = [sum(ep[m] for m in t) for t in teams]; mean = sum(s) / len(s)
    dev = max(abs(x - mean) for x in s) / mean
    return best[0], teams, {"sums": s, "spread": (max(s) - min(s)) / mean, "max_dev": dev, "teams": k,
                            "soft_total": len(soft), "soft_ok": len(soft) - unsat(teams)}


def draw(ep, firm_groups=(), soft_wishes=(), seed=1, iters=40000, restarts=6, cap=0.09, soft_w=0.01):
    """Probiert die plausiblen Teamanzahlen (um n/3 herum, Teamgroesse 2-4) und nimmt die beste Aufteilung.
    Bei 20 Fahrern sind z. B. 6 Teams (3,3,3,3,4,4) oft besser ausgeglichen als 7 Teams mit einem 2er-Team."""
    n = len(ep); base = n_teams(n)
    ks = [k for k in range(max(2, base - 1), base + 2) if 2 * k <= n <= 4 * k]
    runs = [_draw_k(ep, firm_groups, soft_wishes, seed, iters, restarts, cap, soft_w, k) for k in ks]
    cost, teams, info = min(runs, key=lambda r: r[0])
    return teams, info


def plan_teams(ep, signups, wishes, seed=1, tol=0.10, **kw):
    """Komplette Auslosung: Wuensche auswerten (team_signup.analyze_wishes), feste Gruppen setzen, Ausgleich
    rechnen. Haelt eine feste Gruppe die Toleranz nicht ein, wird sie aufgeloest (ihre Wuensche zaehlen dann
    nur noch weich); es faellt jeweils die Gruppe weg, deren Entfernung den Ausgleich am meisten verbessert.
    Rueckgabe: dict(teams, info, dissolved, kept_groups, analysis)."""
    from team_signup import analyze_wishes
    an = analyze_wishes(signups, wishes)
    groups = [list(g) for g in an["firm_groups"]]
    soft = [(w["from"], w["to"]) for w in an["soft_wishes"]]
    dissolved = []
    teams, info = draw({n: ep[n] for n in signups}, groups, soft, seed, **kw)
    while info["max_dev"] > tol and groups:
        trial = []
        for g in groups:
            rest = [x for x in groups if x is not g]
            extra = [(g[i], g[(i + 1) % len(g)]) for i in range(len(g))]
            trial.append((draw({n: ep[n] for n in signups}, rest, soft + extra, seed, **kw), g, extra))
        (t2, i2), g, extra = min(trial, key=lambda x: x[0][1]["max_dev"])
        groups.remove(g); soft += extra; dissolved.append(g); teams, info = t2, i2
    return {"teams": teams, "info": info, "dissolved": dissolved, "kept_groups": groups, "analysis": an}
