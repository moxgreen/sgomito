"""Policy appresa per il computer del sito: corpus, allenamento, misura.

Il computer del browser (web/play/static/play/mcts.js) può usare una policy come prior
della ricerca (PUCT, opzione `prior`). La policy è una regressione logistica
multinomiale su feature locali di ogni mossa (mcts.moveFeatures) e, con --pairs, sulle
loro coppie, allenata a imitare la distribuzione delle visite di una ricerca forte.

    # 1. corpus: partite mcts.js a 20000 simulazioni, visite della radice a ogni mossa
    pypy3 -m hexpush.policy corpus --ko --moves-per-turn 2 --sims 20000 --games 300 \\
        --out results/policy_corpus.jsonl
    # 2. allenamento: pesi in results/policy_weights.json (da copiare in mcts.js)
    pypy3 -m hexpush.policy train results/policy_corpus.jsonl --out results/policy_weights.json
    # 3. misura: guidato a N contro puro a N, 2N, 4N
    pypy3 -m hexpush.policy match --ko --moves-per-turn 2 --sims 500 --games 200 \\
        --out results/policy_match
    # oppure contro il prior di un'altra copia di mcts.js alle stesse simulazioni
    pypy3 -m hexpush.policy match --ko --moves-per-turn 2 --sims 500,2000 --games 200 \\
        --base-js base.mjs --out results/policy_vs_base

corpus --prior gioca il corpus con la ricerca guidata (expert iteration): vedi
docs/policy_prior.md per la seconda versione dei pesi.

Il bot puro (mctsd, opzione prior spenta) resta quello della curva a raddoppi.
"""
import argparse
import csv
import json
import math
import os
import random
import re
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import ladder
from .board import other
from .game import PASS, apply, new_game
from .jsbot import JsBot, engine, to_move
from .simulate import add_rules_args, rules_from_args

OPENING_PLIES = 6  # nel corpus le prime semimosse si scelgono a caso, pesate con le visite


# --- corpus --------------------------------------------------------------------------

def corpus_game(task):
    rules, n_sim, seed, prior, js = task
    rng = random.Random(seed)
    bot = JsBot(rng, n_sim, js=js, prior=True) if prior else JsBot(rng, n_sim, js=js)
    state = new_game(rules)
    moves, visits = [], []
    t = time.perf_counter()
    while state.result is None:
        move = bot.choose(state)
        ch = bot.last_children
        if ch is not None and state.ply < OPENING_PLIES:
            pick = rng.choices(ch, weights=[c[1] for c in ch])[0]
            move = to_move(state, pick[0])
        visits.append(None if ch is None else
                      [[*(m or [None] * 3), n, round(v / n, 4)] for m, n, v in ch])
        moves.append(None if move is PASS else [move.q, move.r, move.d])
        state = apply(state, move)
    return {"seed": seed, "sims": n_sim, "prior": prior, "result": state.result,
            "reason": state.reason, "plies": state.ply, "moves": moves, "visits": visits,
            "seconds": round(time.perf_counter() - t, 1)}


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def cmd_corpus(args):
    rules = rules_from_args(args)
    name = "jsp" if args.prior else "js"
    seeds = [f"{args.seed}-{name}{args.sims}-{i}" for i in range(args.games)]
    done = {g["seed"] for g in read_jsonl(args.out)} if os.path.exists(args.out) else set()
    tasks = [(rules, args.sims, s, args.prior, args.js) for s in seeds if s not in done]
    print(f"{rules}\nmcts.js {args.sims}{' col prior' if args.prior else ''}"
          f"{' (' + args.js + ')' if args.js else ''}: {len(tasks)} partite da giocare "
          f"({len(done)} già nel file), {args.jobs} processi", flush=True)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t0 = time.perf_counter()
    with open(args.out, "a") as f, ProcessPoolExecutor(args.jobs) as ex:
        futures = [ex.submit(corpus_game, t) for t in tasks]
        for k, fut in enumerate(as_completed(futures), 1):
            f.write(json.dumps(fut.result(), separators=(",", ":")) + "\n")
            f.flush()
            if k % 5 == 0 or k == len(tasks):
                print(f"[{time.perf_counter() - t0:6.0f} s] {k}/{len(tasks)}", flush=True)


# --- feature -------------------------------------------------------------------------

def position_rows(game, rules):
    """(stato, visite per mossa) delle posizioni di una partita del corpus con una
    ricerca vera (non le mosse obbligate o vincenti)."""
    state = new_game(rules)
    for move, visits in zip(game["moves"], game["visits"]):
        if visits is not None:
            yield state, {tuple(v[:3]): v[3] for v in visits}
        state = apply(state, to_move(state, move))


def features_game(task):
    rules, game, js = task
    eng = engine(js)
    rows = []
    for state, visits in position_rows(game, rules):
        out = eng.features(state)
        target = [visits.get(tuple(m), 0) for m in out["moves"]]
        if sum(target) == 0:
            continue
        # feature sparse: [[indice, valore], ...] per mossa
        feats = [[[k, x] for k, x in enumerate(f) if x] for f in out["features"]]
        rows.append({"seed": game["seed"], "ply": state.ply, "visits": target, "f": feats})
    return rows


def cmd_features(args):
    rules = rules_from_args(args)
    games = read_jsonl(args.corpus)
    t0 = time.perf_counter()
    n = 0
    with open(args.out, "w") as f, ProcessPoolExecutor(args.jobs) as ex:
        for rows in ex.map(features_game, [(rules, g, args.js) for g in games]):
            for row in rows:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
            n += len(rows)
    print(f"{n} posizioni da {len(games)} partite in {time.perf_counter() - t0:.0f} s")


# --- allenamento ---------------------------------------------------------------------

def position_loss(w, row, grad=None):
    """Entropia incrociata tra visite (normalizzate) e softmax(w · f) di una posizione;
    con grad aggiunge il gradiente. Restituisce (perdita, mossa della policy è la più
    visitata, probabilità della policy sulla mossa più visitata)."""
    feats, visits = row["f"], row["visits"]
    z = [sum(w[k] * x for k, x in f) for f in feats]
    top = max(z)
    ex = [math.exp(v - top) for v in z]
    tot = sum(ex)
    prob = [e / tot for e in ex]
    total = sum(visits)
    pi = [v / total for v in visits]
    loss = -sum(t * math.log(max(q, 1e-12)) for t, q in zip(pi, prob) if t)
    if grad is not None:
        for f, q, t in zip(feats, prob, pi):
            g = q - t
            if g:
                for k, x in f:
                    grad[k] += g * x
    best = max(range(len(visits)), key=visits.__getitem__)
    agree = max(range(len(z)), key=z.__getitem__) == best
    return loss, agree, prob[best]


def evaluate(w, rows):
    n = len(rows)
    out = [position_loss(w, r) for r in rows]
    return (sum(o[0] for o in out) / n, sum(o[1] for o in out) / n,
            sum(o[2] for o in out) / n)


def add_pairs(rows, dim):
    """Aggiunge alle feature sparse di ogni mossa i prodotti delle coppie di feature
    diverse da zero (a < b), con indici da dim in su; restituisce le coppie [(a, b), ...]
    nell'ordine dei loro indici."""
    index = {}
    for row in rows:
        for f in row["f"]:
            nz = list(f)
            for i, (a, xa) in enumerate(nz):
                for b, xb in nz[i + 1:]:
                    f.append([index.setdefault((a, b), dim + len(index)), xa * xb])
    return sorted(index, key=index.get)


def cmd_train(args):
    rows = read_jsonl(args.features)
    names = feature_names(args.js)
    base_dim = len(names)
    pairs = add_pairs(rows, base_dim) if args.pairs else []
    dim = base_dim + len(pairs)
    seeds = sorted({r["seed"] for r in rows})
    random.Random(args.seed).shuffle(seeds)
    test_seeds = set(seeds[:max(1, int(len(seeds) * args.test))])
    train = [r for r in rows if r["seed"] not in test_seeds]
    test = [r for r in rows if r["seed"] in test_seeds]
    print(f"{len(train)} posizioni di allenamento, {len(test)} di prova "
          f"({len(test_seeds)} partite), {base_dim} feature e {len(pairs)} coppie")
    w = [0.0] * dim
    print(f"uniforme: prova perdita {evaluate(w, test)[0]:.4f}  accordo "
          f"{evaluate(w, test)[1]:.1%}")
    # Adam a lotto intero, con penalità L2
    m, v = [0.0] * dim, [0.0] * dim
    b1, b2, lr = 0.9, 0.999, args.lr
    for it in range(1, args.iters + 1):
        grad = [0.0] * dim
        loss = 0.0
        for r in train:
            loss += position_loss(w, r, grad)[0]
        n = len(train)
        for k in range(dim):
            g = grad[k] / n + args.l2 * w[k]
            m[k] = b1 * m[k] + (1 - b1) * g
            v[k] = b2 * v[k] + (1 - b2) * g * g
            w[k] -= lr * (m[k] / (1 - b1 ** it)) / (math.sqrt(v[k] / (1 - b2 ** it)) + 1e-8)
        if it % 25 == 0 or it == args.iters:
            tl, ta, tp = evaluate(w, test)
            print(f"iter {it:4d}  allenamento {loss / n:.4f}  prova {tl:.4f}  accordo {ta:.1%}"
                  f"  prob. della più visitata {tp:.3f}", flush=True)
    tl, ta, tp = evaluate(w, test)
    out = {"names": names, "weights": [round(x, 4) for x in w[:base_dim]],
           "pairs": [[names[a], names[b], round(x, 4)]
                     for (a, b), x in zip(pairs, w[base_dim:]) if round(x, 4)],
           "test_loss": round(tl, 4),
           "test_agree": round(ta, 4), "test_prob_best": round(tp, 4),
           "positions": len(rows), "features": args.features}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    for name, x in sorted(zip(names, w), key=lambda t: -abs(t[1])):
        print(f"  {name:16s} {x:+.3f}")
    for a, b, x in sorted(out["pairs"], key=lambda t: -abs(t[2]))[:20]:
        print(f"  {a + ' * ' + b:32s} {x:+.3f}")
    if args.install:
        install_weights(out)
        print(f"pesi scritti in {JS_ENGINE}")


JS_ENGINE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web", "play",
                                          "static", "play", "mcts.js"))


def feature_names(js=None):
    """FEATURE_NAMES di mcts.js (o della copia in js)."""
    return engine(js).request(op="names")["names"]


def install_weights(out):
    """Scrive in PRIOR_WEIGHTS di mcts.js i pesi di un file di train (out): i lineari e le
    coppie di feature, queste come [a, b, peso, ...] con a < b indici di FEATURE_NAMES."""
    names = feature_names()
    assert out["names"] == names, "pesi allenati su feature diverse da quelle di mcts.js"
    index = {name: k for k, name in enumerate(names)}
    pairs = sorted((*sorted((index[a], index[b])), x) for a, b, x in out.get("pairs", []))

    def block(items):
        """Righe di al più 88 caratteri, senza spezzare un elemento."""
        lines = [""]
        for item in items:
            if lines[-1] and len(lines[-1]) + len(item) + 3 > 88:
                lines.append("")
            lines[-1] += ("" if not lines[-1] else " ") + item + ","
        return "\n".join("  " + line for line in lines if line)
    text = open(JS_ENGINE).read()
    body = (f"[\n{block(f'{x:g}' for x in out['weights'])}\n], [\n"
            f"{block(f'{a}, {b}, {x:g}' for a, b, x in pairs)}\n]")
    new, n = re.subn(r"export const PRIOR_WEIGHTS = .*?\);\n",
                     f"export const PRIOR_WEIGHTS = priorWeights({body});\n", text, flags=re.S)
    assert n == 1
    with open(JS_ENGINE, "w") as f:
        f.write(new)


# --- misura --------------------------------------------------------------------------

def make_js_bot(spec, rng, prior_opts, base):
    """'js:500' -> ricerca pura di mcts.js; 'jsp:500' -> con il prior (prior_opts);
    'base:500' -> col prior della copia di riferimento di mcts.js (base: percorso e opzioni
    della ricerca)."""
    name, n = spec.split(":")
    if name == "js":
        return JsBot(rng, int(n))
    if name == "jsp":
        return JsBot(rng, int(n), prior=True, **prior_opts)
    if name == "base":
        return JsBot(rng, int(n), js=base[0], prior=True, **base[1])
    raise ValueError(f"bot sconosciuto: {spec}")


def match_game(task):
    rules, strong, weak, strong_color, seed, prior_opts, base = task
    t = time.perf_counter()
    specs = {strong_color: strong, other(strong_color): weak}
    bots = {c: make_js_bot(specs[c], random.Random(f"{seed}-{c}"), prior_opts, base)
            for c in "AB"}
    ms = {c: 0.0 for c in "AB"}
    n_sims = {c: 0 for c in "AB"}
    state = new_game(rules)
    pushes = 0
    while state.result is None:
        c = state.to_move
        move = bots[c].choose(state)
        ms[c] += bots[c].last_ms
        n_sims[c] += bots[c].last_sims
        if move is not PASS:
            pushes += move.push
        state = apply(state, move)
    if state.result == "draw":
        outcome = "patta"
    else:
        outcome = "vittoria" if state.result == strong_color else "sconfitta"
    weak_color = other(strong_color)
    return {"livello_forte": strong, "livello_debole": weak, "colore_forte": strong_color,
            "esito": outcome, "motivo": state.reason, "semimosse": state.ply,
            "n_spinte": pushes, "seed": seed, "secondi": round(time.perf_counter() - t, 2),
            "ms_forte": round(ms[strong_color]), "sim_forte": n_sims[strong_color],
            "ms_debole": round(ms[weak_color]), "sim_debole": n_sims[weak_color]}


MATCH_FIELDS = ladder.GAME_FIELDS + ("ms_forte", "sim_forte", "ms_debole", "sim_debole")


def weights_by_name(path, js=None):
    """Pesi di un file di train per la ricerca di mcts.js (o della copia in js): vedi
    dense_weights."""
    with open(path) as f:
        return dense_weights(json.load(f), feature_names(js))


def dense_weights(data, names):
    """Pesi di un file di train (data) come li vuole mcts.movePriors per le feature
    `names`: i lineari nell'ordine di names, poi, se ci sono coppie, la matrice dim × dim
    in cui il peso della coppia a < b è in dim + a * dim + b. Le feature che il file non
    ha valgono 0, così pesi allenati su meno feature danno la stessa policy."""
    by_name = dict(zip(data["names"], data["weights"]))
    missing = set(by_name) - set(names)
    assert not missing, f"feature che mcts.js non ha: {missing}"
    weights = [by_name.get(name, 0.0) for name in names]
    if data.get("pairs"):
        dim, index = len(names), {name: k for k, name in enumerate(names)}
        matrix = [0.0] * (dim * dim)
        for a, b, x in data["pairs"]:
            a, b = sorted((index[a], index[b]))
            matrix[a * dim + b] = x
        weights += matrix
    return weights


def cmd_match(args):
    rules = rules_from_args(args)
    search_opts = {"cPuct": args.cpuct, "fpu": args.fpu, "priorTemp": args.temp}
    prior_opts = dict(search_opts)
    if args.weights:
        prior_opts["weights"] = weights_by_name(args.weights, args.js)
    if args.js:
        prior_opts["js"] = args.js
    base = (args.base_js, search_opts)
    matches = []
    for n in (int(x) for x in args.sims.split(",")):
        if args.base_js:
            matches.append((f"jsp:{n}", f"base:{n}"))
            continue
        for k in (int(x) for x in args.factors.split(",")):
            matches.append((f"jsp:{n}", f"js:{n * k}"))
    tasks = [(rules, strong, weak, "AB"[i % 2], f"{args.seed}-{strong}-{weak}-{i}", prior_opts,
              base) for strong, weak in matches for i in range(args.games)]
    tasks.sort(key=lambda t: -int(t[2].split(":")[1]))
    print(f"{rules}\nprior {search_opts} {args.weights or 'di mcts.js'}"
          f"{', contro il prior di ' + args.base_js if args.base_js else ''}\n"
          f"{len(matches)} incontri, {len(tasks)} partite, {args.jobs} processi", flush=True)
    by_match, games = {}, []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(args.jobs) as ex:
        futures = [ex.submit(match_game, t) for t in tasks]
        for k, fut in enumerate(as_completed(futures), 1):
            g = fut.result()
            key = (g["livello_forte"], g["livello_debole"])
            by_match.setdefault(key, []).append(g)
            games.append(g)
            if len(by_match[key]) == args.games:
                print(f"[{time.perf_counter() - t0:6.0f} s] finito {key[0]} vs {key[1]}"
                      f" ({k}/{len(tasks)} partite)", flush=True)
    table = format_match_table(rules, matches, by_match)
    print("\n" + table)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        games.sort(key=lambda g: g["seed"])
        with open(f"{args.out}_partite.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=MATCH_FIELDS)
            w.writeheader()
            w.writerows(games)
        with open(f"{args.out}_riassunto.txt", "w") as f:
            f.write(f"prior {search_opts} {args.weights or 'di mcts.js'}"
                    f"{', contro il prior di ' + args.base_js if args.base_js else ''}\n"
                    f"{table}\n")


def cost_per_sim(games):
    """Mediane per partita dei microsecondi per simulazione del guidato e del puro, e del
    loro rapporto (stesse partite, stessa macchina). Le mediane non risentono delle
    partite in cui il computer si è sospeso o era sovraccarico."""
    def us(g, who):
        return 1000 * int(g[f"ms_{who}"]) / max(1, int(g[f"sim_{who}"]))
    pairs = [(us(g, "forte"), us(g, "debole")) for g in games
             if int(g["sim_forte"]) and int(g["sim_debole"])]
    if not pairs:
        return float("nan"), float("nan"), float("nan")
    return (statistics.median(a for a, _ in pairs), statistics.median(b for _, b in pairs),
            statistics.median(a / b for a, b in pairs))


def format_match_table(rules, matches, by_match):
    """Tabella come ladder.format_table, con il costo per simulazione dei due bot
    (mediane per partita) e il loro rapporto."""
    lines = [str(rules), "",
             f"{'guidato':>10} {'contro':>10} {'n':>4} {'V':>4} {'S':>4} {'P':>4} {'punti':>7}"
             f" {'Elo':>6} {'IC 95%':>13} {'semim.':>6} {'us/sim g.':>9} {'us/sim c.':>9}"
             f" {'costo':>5}"]
    for strong, weak in matches:
        games = by_match.get((strong, weak))
        if not games:
            continue
        s = ladder.summarize(games, [])
        ug, up, ratio = cost_per_sim(games)
        lines.append(
            f"{strong:>10} {weak:>10} {s['n']:4d} {s['V']:4d} {s['S']:4d} {s['P']:4d}"
            f" {s['punti']:7.1%} {s['elo']:+6.0f} {s['elo_lo']:+6.0f}..{s['elo_hi']:+5.0f}"
            f" {s['semimosse']:6.1f} {ug:9.1f} {up:9.1f} {ratio:5.2f}")
    return "\n".join(lines)


def cmd_table(args):
    """Tabella di un file _partite.csv già scritto da match."""
    with open(args.games) as f:
        games = list(csv.DictReader(f))
    by_match, matches = {}, []
    for g in games:
        for k in ("semimosse", "secondi"):
            g[k] = float(g[k])
        key = (g["livello_forte"], g["livello_debole"])
        if key not in by_match:
            matches.append(key)
        by_match.setdefault(key, []).append(g)
    matches.sort(key=lambda m: (int(m[0].split(":")[1]), int(m[1].split(":")[1])))
    print(format_match_table("", matches, by_match))


# --- main ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("corpus", help="partite mcts.js con le visite della radice")
    add_rules_args(p)
    p.add_argument("--sims", type=int, default=20000)
    p.add_argument("--games", type=int, default=300)
    p.add_argument("--prior", action="store_true",
                   help="maestro guidato dalla policy (expert iteration)")
    p.add_argument("--js", help="un'altra copia di mcts.js (di default quella del sito)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_corpus)
    p = sub.add_parser("features", help="feature (mcts.moveFeatures) delle posizioni del corpus")
    add_rules_args(p)
    p.add_argument("corpus")
    p.add_argument("--js", help="feature di un'altra copia di mcts.js")
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_features)
    p = sub.add_parser("train", help="regressione logistica sulle feature")
    p.add_argument("features")
    p.add_argument("--js", help="feature di un'altra copia di mcts.js (per i nomi)")
    p.add_argument("--pairs", action="store_true",
                   help="anche un peso per ogni coppia di feature (interazioni)")
    p.add_argument("--iters", type=int, default=300)
    p.add_argument("--lr", type=float, default=0.05)
    p.add_argument("--l2", type=float, default=1e-4)
    p.add_argument("--test", type=float, default=0.2, help="quota di partite di prova")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True)
    p.add_argument("--install", action="store_true", help="scrive i pesi in mcts.js")
    p.set_defaults(func=cmd_train)
    p = sub.add_parser("match", help="guidato a N contro puro a N, 2N, 4N (o contro un altro "
                       "prior)")
    add_rules_args(p)
    p.add_argument("--sims", default="500", help="simulazioni del guidato, separate da virgole")
    p.add_argument("--factors", default="1,2,4", help="il puro ha N * fattore simulazioni")
    p.add_argument("--games", type=int, default=200, help="partite per incontro (pari)")
    p.add_argument("--cpuct", type=float, default=2.0)
    p.add_argument("--fpu", type=float, default=0.0)
    p.add_argument("--temp", type=float, default=1.0)
    p.add_argument("--weights", help="pesi da un file di train invece di quelli in mcts.js")
    p.add_argument("--js", help="il guidato usa un'altra copia di mcts.js (per esempio con "
                   "meno feature, per misurarne il costo giusto)")
    p.add_argument("--base-js", help="avversario: il prior di un'altra copia di mcts.js (per "
                   "esempio quella del commit precedente) a parità di simulazioni, invece "
                   "della ricerca pura a N * fattore")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("--out")
    p.set_defaults(func=cmd_match)
    p = sub.add_parser("table", help="tabella di un _partite.csv di match")
    p.add_argument("games")
    p.set_defaults(func=cmd_table)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
