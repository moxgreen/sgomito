"""Varietà delle partite e stili di gioco in un corpus (hexpush.corpus).

    pypy3 -m hexpush.strategies results/corpus_mctsd16000.jsonl results/corpus_mctsd2000.jsonl \
        --ref results/corpus_greedy.jsonl results/corpus_random.jsonl --out results/strategie

Tre domande:
1. Aperture: si apre sempre nello stesso modo? Prefissi distinti e "aperture effettive"
   (exp dell'entropia) delle prime semimosse, a meno della simmetria destra-sinistra
   del campo; esito per apertura.
2. Stili: ogni (partita, giocatore) diventa un vettore di caratteristiche leggibili
   (quante pedine usa, quanto resta a casa, quanto spinge...); k-means sui vettori
   standardizzati dei corpus principali, k scelto con la silhouette. Per ogni gruppo:
   profilo, nome descrittivo, percentuale di vittorie. I corpus --ref (bot più deboli)
   si proiettano sugli stessi gruppi senza contribuire a formarli.
3. Finali: come arriva la vittoria (minacce già pronte o combinazione di due mosse,
   staffetta, centro o bordo).

Solo stdlib: gira anche con pypy3 (consigliato, il k-means è in Python puro).
"""
import argparse
import csv
import math
import os
import random
from collections import Counter, defaultdict

from .board import DIRS, other
from .corpus import read
from .game import PASS, Move, apply, from_board, immediate_outcomes, legal_moves, new_game
from .rules import Ruleset

# riflessione destra-sinistra del campo: (q, r) -> (-q - r, r)
MIRROR_DIR = tuple(DIRS.index((-dq - dr, dr)) for dq, dr in DIRS)

# nome, descrizione, etichetta se alto, etichetta se basso
FEATURES = (
    ("pedine", "pedine diverse mosse almeno una volta / N",
     "muove tutte le pedine", "muove poche pedine"),
    ("concentrazione", "quota delle proprie mosse fatte dalla pedina più usata",
     "una pedina fa quasi tutto", "mosse distribuite"),
    ("casa", "quota media delle proprie pedine sulla fila di casa",
     "tiene il muro a casa", "svuota la casa"),
    ("avanzamento", "avanzamento medio delle proprie pedine (0 casa, 1 traguardo)",
     "schieramento avanzato", "schieramento arretrato"),
    ("punta", "avanzamento medio della pedina più avanzata",
     "punta molto avanti", "punta corta"),
    ("allungamento", "distanza media tra la pedina più avanzata e la più arretrata",
     "schieramento allungato", "schieramento compatto"),
    ("spinte_avv", "quota delle proprie mosse che spingono un avversario",
     "spinge molto gli avversari", "spinge poco gli avversari"),
    ("respinte", "quota delle proprie mosse che ricacciano indietro un avversario",
     "ricaccia indietro", "non ricaccia"),
    ("staffette", "quota delle proprie mosse che spingono una propria pedina",
     "staffette (spinge le sue)", "niente staffette"),
    ("ritirate", "quota delle proprie mosse all'indietro",
     "ritirate frequenti", "mai indietro"),
    ("laterali", "quota delle proprie mosse di lato",
     "manovre laterali", "poche mosse laterali"),
    ("larghezza", "distanza media delle proprie pedine dall'asse centrale (/R)",
     "gioca sulle ali", "gioca al centro"),
    ("fianco", "sbilanciamento su un lato delle pedine uscite di casa (/R)",
     "attacca su un fianco", "attacco simmetrico"),
    ("contatto", "quota media delle proprie pedine a contatto con un avversario",
     "corpo a corpo", "evita il contatto"),
    ("tempo", "turno in cui una propria pedina entra per la prima volta nella metà avversaria",
     "attacco tardivo", "attacco precoce"),
)
NAMES = tuple(f[0] for f in FEATURES)
EARLY = 16  # semimosse dell'"inizio" nel confronto vincitore-perdente


# --- rigioco e caratteristiche ---------------------------------------------------------

def to_move_obj(m):
    return PASS if m is None else Move(m[0], m[1], m[2], bool(m[3]))


def frame(cell, p):
    """Cella vista da p: per B si ruota il campo di mezzo giro, così tutti partono da
    r = -R e puntano a r = R."""
    return cell if p == "A" else (-cell[0], -cell[1])


def lateral(cell):
    """Ascissa cartesiana (0 sull'asse centrale), in unità di cella."""
    return cell[0] + cell[1] / 2


def replay(game):
    """Stati della partita: states[i] è la posizione prima della semimossa i."""
    state = new_game(Ruleset(**game["rules"]))
    states = [state]
    for m in game["moves"]:
        state = apply(state, to_move_obj(m))
        states.append(state)
    return states


def player_features(game, states, limit=None):
    """Caratteristiche di A e di B in una partita: {p: {nome: valore}}. Con `limit` solo
    sulle prime `limit` semimosse (che cosa faceva ciascuno prima che l'esito fosse chiaro)."""
    R, N = states[0].rules.R, states[0].rules.N
    nbr = states[0].geo.nbr
    ids = {cell: i for i, cell in enumerate(sorted(states[0].board))}
    moves_by = {"A": Counter(), "B": Counter()}
    kinds = {p: Counter() for p in "AB"}
    first_cross = {}
    turns = {"A": 0, "B": 0}
    snap = {p: Counter() for p in "AB"}
    for i, m in enumerate(game["moves"][:limit]):
        before = states[i]
        p = before.to_move
        if before.moves_left == before.rules.moves_per_turn or i == 0:
            turns[p] += 1
        if m is not None:
            src = (m[0], m[1])
            dst = nbr[src][m[2]]
            dr = DIRS[m[2]][1] * (1 if p == "A" else -1)  # verso il traguardo di p
            moves_by[p][ids[src]] += 1
            kinds[p]["mosse"] += 1
            kinds[p]["ritirate"] += dr < 0
            kinds[p]["laterali"] += dr == 0
            if m[3]:
                behind = nbr[dst][m[2]]
                if before.board[dst] == p:
                    kinds[p]["staffette"] += 1
                else:
                    kinds[p]["spinte_avv"] += 1
                    kinds[p]["respinte"] += dr > 0
                if behind is not None:
                    ids[behind] = ids[dst]
            ids[dst] = ids.pop(src)
        after = states[i + 1]
        for q in "AB":
            own = [frame(c, q) for c, o in after.board.items() if o == q]
            adv = [(c[1] + R) / (2 * R) for c in own]
            s = snap[q]
            s["n"] += 1
            s["casa"] += sum(c[1] == -R for c in own) / len(own)
            s["avanzamento"] += sum(adv) / len(adv)
            s["punta"] += max(adv)
            s["allungamento"] += max(adv) - min(adv)
            s["larghezza"] += sum(abs(lateral(c)) for c in own) / len(own) / R
            out = [lateral(c) for c in own if c[1] > -R]
            s["fianco"] += abs(sum(out) / len(out)) / R if out else 0
            touching = sum(any(after.board.get(t) == other(q) for t in nbr[c] if t)
                           for c, o in after.board.items() if o == q)
            s["contatto"] += touching / len(own)
            if q not in first_cross and any(c[1] > 0 for c in own):
                first_cross[q] = turns[q]
    feats = {}
    for p in "AB":
        n = max(kinds[p]["mosse"], 1)
        s = snap[p]
        f = {k: s[k] / s["n"] for k in ("casa", "avanzamento", "punta", "allungamento",
                                        "larghezza", "fianco", "contatto")}
        f["pedine"] = len(moves_by[p]) / N
        f["concentrazione"] = max(moves_by[p].values()) / n if moves_by[p] else 0
        for k in ("spinte_avv", "respinte", "staffette", "ritirate", "laterali"):
            f[k] = kinds[p][k] / n
        f["tempo"] = first_cross.get(p, turns[p] + 1)
        feats[p] = f
    return feats


def ending(game, states):
    """Come è stata vinta la partita (None se patta)."""
    win = game["result"]
    if win not in ("A", "B"):
        return None
    rules = states[0].rules
    R = rules.R
    # inizio dell'ultimo turno del vincitore, e dell'ultimo turno del perdente prima
    j = len(game["moves"]) - 1
    while j > 0 and states[j - 1].to_move == win:
        j -= 1
    start = states[j]
    ready = sum(o == 1 for o in immediate_outcomes(start, legal_moves(start)))
    # minacce che il perdente aveva davanti all'inizio del suo ultimo turno: le vittorie
    # in una mossa che il vincitore avrebbe avuto se fosse toccato a lui
    k = j - 1
    while k > 0 and states[k - 1].to_move != win:
        k -= 1
    faced = None
    if j > 0:
        probe = from_board(rules, states[k].board, win)
        faced = sum(o == 1 for o in immediate_outcomes(probe, legal_moves(probe)))
    last = game["moves"][-1]
    src = (last[0], last[1])
    nbr = states[0].geo.nbr
    dst = nbr[src][last[2]]
    if last[3] and states[-2].board[dst] == win:
        how, goal_cell = "staffetta", nbr[dst][last[2]]
    elif last[3]:
        how, goal_cell = "spinta", dst
    else:
        how, goal_cell = "passo", dst
    x = abs(lateral(frame(goal_cell, win)))
    end = states[-1].board
    home_loser = sum(1 for c, o in end.items() if o != win and frame(c, o)[1] == -R)
    return {"pronte": ready, "subite": faced, "come": how,
            "cella": "bordo" if x >= R / 2 else "centro",
            "difensori": home_loser, "mosse_finali": len(game["moves"]) - j}


# --- aperture --------------------------------------------------------------------------

def canonical(moves):
    """Mosse come tuple, orientate in modo che il primo spostamento asimmetrico vada
    verso una parte fissa (minimo lessicografico tra la partita e la sua riflessione)."""
    plain = [(99, 99, 99) if m is None else (m[0], m[1], m[2]) for m in moves]
    mirror = [(99, 99, 99) if m is None else (-m[0] - m[1], m[1], MIRROR_DIR[m[2]])
              for m in moves]
    return min(plain, mirror)


def first_move_name(m, R):
    """Nome della prima mossa di A (una pedina della fila di casa): posizione contata dal
    bordo, e direzione rispetto all'asse centrale."""
    x = lateral((m[0], m[1]))
    dx = DIRS[m[2]][0] + DIRS[m[2]][1] / 2
    if x == 0:
        return "pedina centrale"
    k = round(R / 2 - abs(x))  # 0 = la pedina sul bordo
    place = ("esterna", "seconda", "terza")[k] if k < 3 else f"{k + 1}a dal bordo"
    return f"pedina {place} {'verso il centro' if dx * x < 0 else 'verso il bordo'}"


def entropy_stats(keys):
    c = Counter(keys)
    n = sum(c.values())
    h = -sum(v / n * math.log(v / n) for v in c.values())
    return len(c), math.exp(h), c.most_common(1)[0][1] / n


def wilson(k, n):
    if n == 0:
        return float("nan"), float("nan")
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def opening_report(name, games, states_of):
    lines = [f"== Aperture: {name} ({len(games)} partite)"]
    canon = [canonical(g["moves"]) for g in games]
    lines.append("  semimossa  prefissi distinti  aperture effettive  quota della più comune"
                 "   posizioni distinte")
    for d in (1, 2, 3, 5, 7, 9, 13, 17, 25):
        live = [i for i, c in enumerate(canon) if len(c) >= d]
        if len(live) < len(games) * 0.8:
            break
        n_pref, eff, top = entropy_stats(tuple(canon[i][:d]) for i in live)
        pos = Counter(position_class(states_of(i)[d]) for i in live)
        lines.append(f"  {d:9d}  {n_pref:8d} / {len(live):<6d}  {eff:18.1f}  {top:22.1%}"
                     f"   {len(pos):8d}")
    R = games[0]["rules"]["R"]
    lines.append("  prima mossa di A          partite   quota   A vince (IC 95%)")
    by = defaultdict(list)
    for g, c in zip(games, canon):
        by[first_move_name(c[0], R)].append(g["result"] == "A")
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lo, hi = wilson(sum(v), len(v))
        lines.append(f"  {k:26s} {len(v):6d}  {len(v) / len(games):6.1%}   "
                     f"{sum(v) / len(v):5.1%} ({lo:.0%}-{hi:.0%})")
    # il primo turno di B (due mosse), nell'orientamento canonico
    lines.append("  risposta di B (prime 3 semimosse)               partite   A vince")
    by = defaultdict(list)
    for g, c in zip(games, canon):
        if len(c) >= 3:
            by[tuple(c[:3])].append(g["result"] == "A")
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))[:8]:
        txt = " | ".join(f"{q},{r} {'E W SE NW NE SW'.split()[d]}" for q, r, d in k)
        lines.append(f"  {txt:47s} {len(v):6d}   {sum(v) / len(v):5.1%}")
    conf = [(i, c) for g in games for i, c in enumerate(g.get("conf") or []) if c]
    if conf:
        lines.append("  convinzione del bot (quota di visite della mossa scelta) per fase:")
        for lo, hi in ((0, 1), (1, 5), (5, 15), (15, 30), (30, 60), (60, 999)):
            v = [c[0] for i, c in conf if lo <= i < hi]
            if v:
                lines.append(f"    semimosse {lo:3d}-{min(hi, 200) - 1:<3d}  {sum(v) / len(v):5.1%}"
                             f"  (mosse con quota > 50%: {sum(x > 0.5 for x in v) / len(v):5.1%})")
    return lines


def position_class(state):
    """Posizione a meno della riflessione destra-sinistra."""
    cells = sorted(state.board.items())
    mirror = sorted(((-q - r, r), o) for (q, r), o in state.board.items())
    return (min(tuple(cells), tuple(mirror)), state.to_move, state.moves_left)


# --- clustering ------------------------------------------------------------------------

def dist2(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def kmeans(X, k, rng, restarts=12, iters=60):
    best = None
    for _ in range(restarts):
        C = [list(rng.choice(X))]
        while len(C) < k:  # k-means++
            d = [min(dist2(x, c) for c in C) for x in X]
            r, acc = rng.random() * sum(d), 0.0
            for x, dx in zip(X, d):
                acc += dx
                if acc >= r:
                    C.append(list(x))
                    break
        lab = None
        for _ in range(iters):
            new = [min(range(k), key=lambda j: dist2(x, C[j])) for x in X]
            if new == lab:
                break
            lab = new
            for j in range(k):
                pts = [x for x, l in zip(X, lab) if l == j]
                if pts:
                    C[j] = [sum(col) / len(pts) for col in zip(*pts)]
        inertia = sum(dist2(x, C[l]) for x, l in zip(X, lab))
        if best is None or inertia < best[0]:
            best = (inertia, C, lab)
    return best[1], best[2]


def silhouette(D, lab, k):
    n = len(lab)
    members = [[i for i in range(n) if lab[i] == j] for j in range(k)]
    tot = 0.0
    for i in range(n):
        own = members[lab[i]]
        if len(own) <= 1:
            continue
        a = sum(D[i][j] for j in own) / (len(own) - 1)
        b = min(sum(D[i][j] for j in m) / len(m) for c, m in enumerate(members)
                if c != lab[i] and m)
        tot += (b - a) / max(a, b)
    return tot / n


def describe(z):
    """Nome descrittivo di un centroide: le caratteristiche più lontane dalla media."""
    ranked = sorted(range(len(z)), key=lambda i: -abs(z[i]))
    words = [FEATURES[i][2] if z[i] > 0 else FEATURES[i][3] for i in ranked[:3]
             if abs(z[i]) >= 0.5]
    return ", ".join(words) or "nella media"


# --- main ------------------------------------------------------------------------------

def load(path):
    games = read(path)
    label = os.path.basename(path).removeprefix("corpus_").removesuffix(".jsonl")
    rows, endings, states = [], [], []
    for gi, g in enumerate(games):
        st = replay(g)
        states.append(st)
        feats = player_features(g, st)
        early = player_features(g, st, EARLY) if g["plies"] >= 2 * EARLY else None
        for p in "AB":
            rows.append({"corpus": label, "partita": gi, "colore": p, "bot": g[f"bot{p}"],
                         "vince": {p: 1, other(p): 0}.get(g["result"], 0.5),
                         "semimosse": g["plies"], **feats[p],
                         "inizio": early and early[p]})
        e = ending(g, st)
        if e:
            endings.append(e)
    return label, games, states, rows, endings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", nargs="+", help="corpus che formano i gruppi")
    ap.add_argument("--ref", nargs="*", default=[], help="corpus solo proiettati sui gruppi")
    ap.add_argument("--k", type=int, help="numero di gruppi (default: migliore silhouette)")
    ap.add_argument("--kmax", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="results/strategie")
    args = ap.parse_args()

    out = []
    say = lambda *ls: (out.extend(ls), print(*ls, sep="\n", flush=True))
    data = [load(p) for p in args.corpus + args.ref]
    main_labels = {d[0] for d in data[:len(args.corpus)]}

    # 0. numeri di base
    say("== Corpus", "  corpus            partite  A vince  patte  semimosse  spinte/mosse")
    for label, games, states, rows, _ in data:
        n = len(games)
        mv = [m for g in games for m in g["moves"] if m]
        say(f"  {label:16s} {n:7d}  {sum(g['result'] == 'A' for g in games) / n:6.1%}"
            f"  {sum(g['result'] == 'draw' for g in games) / n:5.1%}"
            f"  {sum(g['plies'] for g in games) / n:9.1f}"
            f"  {sum(m[3] for m in mv) / len(mv):11.1%}")
    say("")

    # 1. aperture
    for label, games, states, _, _ in data:
        say(*opening_report(label, games, lambda i, st=states: st[i]), "")

    # 2. stili
    rows = [r for d in data for r in d[3]]
    fit = [r for r in rows if r["corpus"] in main_labels]
    mean = [sum(r[f] for r in fit) / len(fit) for f in NAMES]
    sd = [math.sqrt(sum((r[f] - m) ** 2 for r in fit) / len(fit)) or 1.0
          for f, m in zip(NAMES, mean)]
    zs = lambda r: [(r[f] - m) / s for f, m, s in zip(NAMES, mean, sd)]
    X = [zs(r) for r in fit]
    D = [[math.sqrt(dist2(a, b)) for b in X] for a in X]
    rng = random.Random(args.seed)
    say("== Stili: k-means su caratteristiche standardizzate di "
        f"{len(fit)} (partita, giocatore) di {', '.join(sorted(main_labels))}")
    sols = {}
    for k in range(2, args.kmax + 1):
        C, lab = kmeans(X, k, rng)
        sols[k] = (C, lab, silhouette(D, lab, k))
        say(f"  k={k}: silhouette {sols[k][2]:.3f}")
    k = args.k or max(sols, key=lambda k: sols[k][2])
    C, lab, sil = sols[k]
    # gruppi in ordine di grandezza
    order = sorted(range(k), key=lambda j: -lab.count(j))
    remap = {j: i for i, j in enumerate(order)}
    C = [C[j] for j in order]
    for r, l in zip(fit, lab):
        r["gruppo"] = remap[l]
    for r in rows:
        if "gruppo" not in r:
            z = zs(r)
            r["gruppo"] = min(range(k), key=lambda j: dist2(z, C[j]))
    say(f"  scelto k={k}", "")
    say("  caratteristica    media (sd)    " + "".join(f"  gruppo {j:<3d}" for j in range(k)))
    for i, (f, desc, _, _) in enumerate(FEATURES):
        say(f"  {f:16s} {mean[i]:6.3f} ({sd[i]:.3f})"
            + "".join(f"  {C[j][i]:+8.2f}  " for j in range(k)) + f"  {desc}")
    say("")
    for j in range(k):
        g = [r for r in fit if r["gruppo"] == j]
        w = sum(r["vince"] for r in g)
        lo, hi = wilson(w, len(g))
        cols = Counter(r["colore"] for r in g)
        say(f"  gruppo {j}: {describe(C[j])}",
            f"    {len(g)} ({len(g) / len(fit):.0%}), vince {w / len(g):.1%} ({lo:.0%}-{hi:.0%}),"
            f" come A {cols['A']} come B {cols['B']},"
            f" semimosse medie {sum(r['semimosse'] for r in g) / len(g):.0f}")
    say("", "  quota di ciascun gruppo per corpus (e vittorie in quel gruppo):")
    say("  corpus          " + "".join(f"   gruppo {j:<8d}" for j in range(k))
        + "  gruppi effettivi, dispersione (varianza media per caratteristica)")
    for label, *_ in data:
        g = [r for r in rows if r["corpus"] == label]
        cells, cnt = [], Counter(r["gruppo"] for r in g)
        for j in range(k):
            m = [r for r in g if r["gruppo"] == j]
            wr = f"{sum(r['vince'] for r in m) / len(m):.0%}" if m else "-"
            cells.append(f"   {len(m) / len(g):5.1%} ({wr:>4s}) ")
        _, eff, _ = entropy_stats(r["gruppo"] for r in g)
        # varianza totale nello spazio standardizzato (14+ dimensioni): 1 per dimensione
        # nei corpus principali messi insieme
        Z = [zs(r) for r in g]
        mz = [sum(col) / len(Z) for col in zip(*Z)]
        spread = sum(dist2(z, mz) for z in Z) / len(Z) / len(NAMES)
        say(f"  {label:16s}" + "".join(cells) + f"  {eff:5.2f}   dispersione {spread:.2f}")
    say("", "  incontri tra stili (corpus principali): A vince %, partite")
    say("  A \\ B     " + "".join(f"  gruppo {j:<5d}" for j in range(k)))
    pairs = defaultdict(list)
    by_game = defaultdict(dict)
    for r in fit:
        by_game[(r["corpus"], r["partita"])][r["colore"]] = r
    for pr in by_game.values():
        pairs[(pr["A"]["gruppo"], pr["B"]["gruppo"])].append(pr["A"]["vince"])
    for a in range(k):
        cells = []
        for b in range(k):
            v = pairs.get((a, b), [])
            cells.append(f"  {sum(v) / len(v):4.0%} ({len(v):3d}) " if v else "      -       ")
        say(f"  gruppo {a:<3d}" + "".join(cells))
    say("")

    # 2b. cosa distingue chi vince da chi perde nella stessa partita
    for label, *_ in data:
        g = defaultdict(dict)
        for r in rows:
            if r["corpus"] == label and r["vince"] != 0.5:
                g[r["partita"]][r["vince"]] = r
        whole = [(v[1], v[0]) for v in g.values() if len(v) == 2]
        early = [(v[1]["inizio"], v[0]["inizio"]) for v in g.values()
                 if len(v) == 2 and v[1]["inizio"]]
        for pairs, what in ((whole, "tutta la partita"),
                            (early, f"solo le prime {EARLY} semimosse, partite di almeno"
                                    f" {2 * EARLY}")):
            if not pairs:
                continue
            say(f"== Vincitore contro perdente nella stessa partita: {label}, {what}"
                f" ({len(pairs)} partite)",
                "  caratteristica    vincitore  perdente  differenza (in sd)"
                "  il vincitore ha di più")
            for i, f in enumerate(NAMES):
                w = sum(p[0][f] for p in pairs) / len(pairs)
                l = sum(p[1][f] for p in pairs) / len(pairs)
                more = sum(p[0][f] > p[1][f] for p in pairs)
                ties = sum(p[0][f] == p[1][f] for p in pairs)
                share = (more + ties / 2) / len(pairs)
                say(f"  {f:16s} {w:9.3f} {l:9.3f} {(w - l) / sd[i]:+12.2f}        {share:6.1%}")
            say("")

    # 3. finali
    for label, games, _, _, endings in data:
        if not endings:
            continue
        n = len(endings)
        say(f"== Finali: {label} ({n} vittorie)")
        for key, title in (("come", "ultima mossa"), ("cella", "cella d'arrivo"),
                           ("pronte", "vittorie in una mossa all'inizio dell'ultimo turno"),
                           ("subite", "minacce in una mossa davanti al perdente nel suo"
                                      " ultimo turno"),
                           ("difensori", "pedine del perdente rimaste sulla sua fila di casa"),
                           ("mosse_finali", "mosse usate nell'ultimo turno")):
            c = Counter(min(e[key], 3) if isinstance(e[key], int) else e[key]
                        for e in endings if e[key] is not None)
            txt = "  ".join(f"{'3+' if v == 3 and key not in ('mosse_finali',) else v}:"
                            f" {c[v] / n:.0%}" for v in sorted(c, key=str))
            say(f"  {title}: {txt}")
        say("")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(f"{args.out}_stili.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["corpus", "partita", "colore", "bot", "vince",
                                          "semimosse", "gruppo", *NAMES])
        w.writeheader()
        for r in rows:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()
                        if k != "inizio"})
    with open(f"{args.out}_riassunto.txt", "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
