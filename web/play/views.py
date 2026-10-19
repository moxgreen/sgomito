import secrets

from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from hexpush.rules import Ruleset

from .consumers import PID_COOKIE
from .matches import LEVELS, create_match, get_match

COLORS = (("A", "Arancio (muove per primo)"), ("B", "Blu"), ("casuale", "Casuale"))
DEFAULTS = {"R": 4, "N": 5, "K": 1, "goal_wide": False, "push_off": False}


def player_id(request):
    """Identificativo del browser (cookie): serve a riprendere il proprio posto."""
    return request.COOKIES.get(PID_COOKIE) or secrets.token_urlsafe(16)


def with_pid(response, pid):
    response.set_cookie(PID_COOKIE, pid, max_age=365 * 24 * 3600, samesite="Lax")
    return response


def home(request, error=None, values=None, status=200):
    context = {
        "levels": [(k, name, n) for k, (name, n) in LEVELS.items()],
        "colors": COLORS,
        "v": values or {**DEFAULTS, "color": "casuale", "level": "medio"},
        "error": error,
    }
    return with_pid(render(request, "play/home.html", context, status=status),
                    player_id(request))


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
              "R": post.get("R"), "N": post.get("N"), "K": post.get("K")}
    try:
        mode = post.get("mode")
        if mode not in ("amico", "computer"):
            raise ValueError("scegli contro chi giocare")
        level = post.get("level") if mode == "computer" else None
        if level is not None and level not in LEVELS:
            raise ValueError("livello sconosciuto")
        rules = Ruleset(R=_int(post, "R", 3, 5), N=_int(post, "N", 1, 13),
                        K=_int(post, "K", 1, 13), goal_wide=values["goal_wide"],
                        push_off=values["push_off"], ko=True, moves_per_turn=2)
    except ValueError as e:
        return home(request, error=str(e), values=values, status=400)
    pid = player_id(request)
    match = create_match(rules, pid, values["color"], level)
    return with_pid(redirect("match", match_id=match.id), pid)


def match_page(request, match_id):
    match = get_match(match_id)
    if match is None:
        raise Http404("Partita inesistente: forse il server è stato riavviato.")
    context = {"match": match, "share_url": request.build_absolute_uri(),
               "vs_computer": match.level is not None}
    return with_pid(render(request, "play/match.html", context), player_id(request))
