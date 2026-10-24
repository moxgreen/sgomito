import csv
import json
import secrets
from collections import Counter, defaultdict

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from hexpush.rules import Ruleset

from . import records
from .consumers import PID_COOKIE
from .matches import LEVELS
from .models import Game, Rating

COLORS = (("A", "Arancio (muove per primo)"), ("B", "Blu"), ("casuale", "Casuale"))
DEFAULTS = {"R": 4, "N": 5, "K": 1, "goal_wide": False, "push_off": False}
REASONS = {"goal": "traguardo", "elimination": "eliminazione", "repetition": "ripetizione",
           "max_plies": "limite di mosse", "resign": "abbandono"}


def player_id(request):
    """Identificativo del browser (cookie): serve a riprendere il proprio posto."""
    return request.COOKIES.get(PID_COOKIE) or secrets.token_urlsafe(16)


def with_pid(response, pid):
    response.set_cookie(PID_COOKIE, pid, max_age=365 * 24 * 3600, samesite="Lax")
    return response


def home(request, error=None, values=None, status=200):
    pid = player_id(request)
    player = records.get_player(pid)
    context = {
        "levels": [(k, name, desc) for k, (name, desc, _) in LEVELS.items()],
        "colors": COLORS,
        "v": values or {**DEFAULTS, "color": "casuale", "level": "medio",
                        "name": player.name if player else ""},
        "error": error,
    }
    return with_pid(render(request, "play/home.html", context, status=status), pid)


def _int(post, name, lo, hi):
    try:
        value = int(post.get(name, DEFAULTS[name]))
    except ValueError:
        raise ValueError(f"{name} deve essere un numero intero")
    if not lo <= value <= hi:
        raise ValueError(f"{name} deve stare tra {lo} e {hi}")
    return value


@require_POST
def create(request):
    post = request.POST
    values = {"color": post.get("color", "casuale"), "level": post.get("level", "medio"),
              "goal_wide": "goal_wide" in post, "push_off": "push_off" in post,
              "R": post.get("R"), "N": post.get("N"), "K": post.get("K"),
              "name": post.get("name", "")}
    pid = player_id(request)
    try:
        mode = post.get("mode")
        if mode not in ("amico", "computer"):
            raise ValueError("scegli contro chi giocare")
        level = post.get("level") if mode == "computer" else None
        if level is not None and level not in LEVELS:
            raise ValueError("livello sconosciuto")
        rules = Ruleset(R=_int(post, "R", 3, 5), N=_int(post, "N", 1, 13),
                        K=_int(post, "K", 1, 13), goal_wide=values["goal_wide"],
                        push_off=values["push_off"], ko=True, no_pushback=True,
                        moves_per_turn=2)
        player = records.set_player_name(pid, values["name"])
    except ValueError as e:
        return home(request, error=str(e), values=values, status=400)
    match = records.new_match(rules, player, values["color"], level)
    return with_pid(redirect("match", match_id=match.id), pid)


def match_page(request, match_id):
    match = records.find_match(match_id)
    if match is None:
        raise Http404("Partita inesistente.")
    pid = player_id(request)
    player = records.get_player(pid)
    error = None
    if request.method == "POST":
        try:
            records.set_player_name(pid, request.POST.get("name"))
            return with_pid(redirect("match", match_id=match.id), pid)
        except ValueError as e:
            error = str(e)
    if player is None or error:
        # prima di sedersi (o di guardare) serve un nome
        context = {"match": match, "error": error, "name": request.POST.get("name", ""),
                   "free": [p for p, who in match.seats.items() if who is None]}
        return with_pid(render(request, "play/name.html", context,
                               status=400 if error else 200), pid)
    context = {"match": match, "share_url": request.build_absolute_uri(),
               "vs_computer": match.level is not None}
    return with_pid(render(request, "play/match.html", context), pid)


# --- registro del playtesting ---------------------------------------------------------

def rules_label(rules):
    parts = [f"R={rules['R']}", f"N={rules['N']}"]
    if rules["K"] > 1:
        parts.append(f"K={rules['K']}")
    if rules["goal_wide"]:
        parts.append("traguardo largo")
    if rules["push_off"]:
        parts.append("spinta fuori")
    return " ".join(parts)


def _games():
    return Game.objects.select_related("player_a", "player_b").prefetch_related(
        "ratings", "ratings__player")


def _row(game):
    stars = {r.color: r for r in game.ratings.all()}
    names = {}
    for color, player in (("A", game.player_a), ("B", game.player_b)):
        if game.bot_side == color:
            names[color] = f"computer ({LEVELS.get(game.level, (game.level,))[0]})"
        else:
            names[color] = player.name if player else ""
    duration = game.moves[-1][4] if game.moves else 0
    return {"game": game, "names": names, "rules": rules_label(game.rules),
            "reason": REASONS.get(game.reason, game.reason), "duration": duration,
            "stars": stars}


def registry(request):
    games = list(_games())
    rows = [_row(g) for g in games]
    ratings = [r for g in games for r in g.ratings.all()]
    dist = Counter(r.stars for r in ratings)
    by_rules = defaultdict(lambda: {"games": 0, "ended": 0, "stars": [], "plies": []})
    for row in rows:
        g, v = row["game"], by_rules[row["rules"]]
        v["games"] += 1
        if g.result:
            v["ended"] += 1
            v["plies"].append(g.plies)
        v["stars"] += [r.stars for r in g.ratings.all()]
    variants = [{"rules": k, "games": v["games"], "ended": v["ended"],
                 "ratings": len(v["stars"]),
                 "mean": sum(v["stars"]) / len(v["stars"]) if v["stars"] else None,
                 "plies": sum(v["plies"]) / len(v["plies"]) if v["plies"] else None}
                for k, v in sorted(by_rules.items())]
    context = {
        "rows": rows,
        "n_games": len(games),
        "n_ended": sum(1 for g in games if g.result),
        "n_ratings": len(ratings),
        "mean": sum(r.stars for r in ratings) / len(ratings) if ratings else None,
        "dist": [(s, dist.get(s, 0)) for s in range(5, 0, -1)],
        "dist_max": max(dist.values(), default=1),
        "variants": variants,
    }
    return render(request, "play/registry.html", context)


def write_games_csv(out):
    """Una riga per partita (mosse comprese, in JSON) su un file aperto in testo."""
    w = csv.writer(out)
    w.writerow(["id", "inizio", "fine", "modo", "livello", "regole", "giocatore_A",
                "giocatore_B", "computer", "esito", "motivo", "abbandono", "semimosse",
                "durata_s", "annullamenti", "stelle_A", "stelle_B", "commento_A",
                "commento_B", "mosse"])
    for g in _games():
        row = _row(g)
        st = row["stars"]
        w.writerow([g.id, g.created.isoformat(), g.ended.isoformat() if g.ended else "",
                    g.mode, g.level, json.dumps(g.rules), row["names"]["A"],
                    row["names"]["B"], g.bot_side, g.result, g.reason, g.resigned, g.plies,
                    row["duration"], g.undos,
                    st["A"].stars if "A" in st else "", st["B"].stars if "B" in st else "",
                    st["A"].comment if "A" in st else "", st["B"].comment if "B" in st else "",
                    json.dumps(g.moves)])


def write_ratings_csv(out):
    """Una riga per giudizio."""
    w = csv.writer(out)
    w.writerow(["partita", "giocatore", "colore", "stelle", "commento", "quando", "modo",
                "livello", "regole", "esito", "motivo", "semimosse"])
    for r in Rating.objects.select_related("game", "player").order_by("created"):
        g = r.game
        w.writerow([g.id, r.player.name, r.color, r.stars, r.comment, r.created.isoformat(),
                    g.mode, g.level, rules_label(g.rules), g.result, g.reason, g.plies])


def _csv_response(filename, writer):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer(resp)
    return resp


def registry_games_csv(request):
    return _csv_response("partite.csv", write_games_csv)


def registry_ratings_csv(request):
    return _csv_response("giudizi.csv", write_ratings_csv)


def registry_game_json(request, match_id):
    g = _games().filter(pk=match_id).first()
    if g is None:
        raise Http404("Partita inesistente.")
    row = _row(g)
    return JsonResponse({
        "id": g.id, "created": g.created, "ended": g.ended, "mode": g.mode, "level": g.level,
        "rules": g.rules, "players": row["names"], "bot_side": g.bot_side,
        "result": g.result, "reason": g.reason, "resigned": g.resigned, "plies": g.plies,
        "undos": g.undos, "moves": g.moves,
        "ratings": [{"color": r.color, "player": r.player.name, "stars": r.stars,
                     "comment": r.comment} for r in g.ratings.all()],
    }, json_dumps_params={"ensure_ascii": False, "indent": 1})
