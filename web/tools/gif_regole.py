"""GIF animate degli esempi di "Come si gioca" (web/play/static/play/regole/).

Ogni esempio è una sequenza di mosse giocate sul motore vero (hexpush): una mossa
illegale o una mossa "vietata" che il motore invece permette fa fallire lo script,
così le figure restano allineate alle regole. Il disegno imita il campo SVG del sito
(stessi colori di style.css), visto da Arancio: la sua casa è in basso.

    make gifs        # oppure: .venv/bin/python web/tools/gif_regole.py

Serve Pillow (solo qui, non a runtime: make gifs lo installa nel venv).
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from hexpush.board import DIRS  # noqa: E402
from hexpush.game import Move, apply, from_board, legal_moves  # noqa: E402
from hexpush.rules import Ruleset  # noqa: E402

OUT = ROOT / "web" / "play" / "static" / "play" / "regole"
RULES = Ruleset(R=3, N=4, ko=True, no_pushback=True, moves_per_turn=2)
NAMES = {"A": "Arancio", "B": "Blu"}

SS = 3                 # sovracampionamento: si disegna in grande e si riduce (antialias)
HEX = 23 * SS          # raggio di una cella
SQ3 = math.sqrt(3)
BAR = 34 * SS          # altezza della barra del turno
PAD = 10 * SS

# colori di style.css
COMMON = {"A": "#e2701b", "B": "#2d6cc0", "A-dark": "#8f4108", "B-dark": "#173e72",
          "move": "#2b9a48", "push": "#d23a32", "white": "#ffffff"}
THEMES = {
    "chiaro": {**COMMON, "bg": "#f6f3ee", "cell": "#efe8dc", "cell-line": "#c9bead",
               "A-light": "#fbe1cb", "B-light": "#d5e3f6", "wall": "#3a3328",
               "locked": "#9b978f", "locked-line": "#5d5a54", "text": "#2a2620"},
    "scuro": {**COMMON, "bg": "#1b1d20", "cell": "#34373b", "cell-line": "#4a4e54",
              "A-light": "#5a3a20", "B-light": "#22395a", "wall": "#d9d2c4",
              "locked": "#77746e", "locked-line": "#5d5a54", "text": "#ebe6dd"},
}

STEP_FRAMES = 9        # fotogrammi di un'animazione di mossa
STEP_MS = 35


def font(size):
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


FONT = font(15 * SS)


def rgba(color, alpha=1.0):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (round(255 * alpha),)


# --- geometria del disegno (come px() in game.js, con la casa di Arancio in basso) ----

R = RULES.R
WIDTH = round(HEX * SQ3 * (2 * R + 1) + 2 * PAD)
HEIGHT = round(HEX * (3 * R + 2) + 2 * PAD + BAR)
CX, CY = WIDTH / 2, BAR + (HEIGHT - BAR) / 2


def px(q, r):
    return CX - HEX * SQ3 * (q + r / 2), CY - HEX * 1.5 * r


def lerp(a, b, t):
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def corner(c, size, deg):
    a = math.radians(deg)
    return c[0] + size * math.cos(a), c[1] + size * math.sin(a)


def hex_points(c, size):
    return [corner(c, size, 60 * i - 30) for i in range(6)]


def dashed(draw, pts, color, width, dash, gap, closed=True):
    pts = list(pts) + ([pts[0]] if closed else [])
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        s = 0.0
        while s < length:
            e = min(s + dash, length)
            draw.line([lerp((x1, y1), (x2, y2), s / length),
                       lerp((x1, y1), (x2, y2), e / length)], fill=color, width=width)
            s = e + gap


def thick(draw, a, b, color, width):
    """Segmento con estremi arrotondati."""
    draw.line([a, b], fill=color, width=width)
    for x, y in (a, b):
        draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=color)


def arrow(draw, a, b, color, width, back=0.2, front=0.35):
    """Freccia da a verso b, accorciata ai due capi (in frazioni di HEX)."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    p = (a[0] + ux * HEX * back, a[1] + uy * HEX * back)
    q = (b[0] - ux * HEX * front, b[1] - uy * HEX * front)
    head = width * 2.6
    base = (q[0] - ux * head, q[1] - uy * head)
    thick(draw, p, base, color, width)
    draw.polygon([q, (base[0] - uy * head * 0.75, base[1] + ux * head * 0.75),
                  (base[0] + uy * head * 0.75, base[1] - ux * head * 0.75)], fill=color)


# --- un fotogramma -------------------------------------------------------------------

def frame(theme, state, bar, marks=None, moving=None, last=()):
    """Disegna il campo. marks: dict con select, dests, active, barred.
    moving: {cella_di_partenza: posizione in pixel} per le pedine in animazione.
    last: mosse dell'ultimo turno di Blu come (giocatore, da, a, spinta_a), disegnate
    come nel sito mentre tocca ad Arancio (chi guarda)."""
    T = THEMES[theme]
    geo = state.geo
    img = Image.new("RGBA", (WIDTH, HEIGHT), rgba(T["bg"]))
    d = ImageDraw.Draw(img)

    # barra del turno, come .turn-bar del sito
    player, text = bar
    d.rounded_rectangle([PAD, PAD // 2, WIDTH - PAD, BAR - PAD // 3], radius=8 * SS,
                        fill=rgba(T[player]))
    d.text((WIDTH / 2, (PAD // 2 + BAR - PAD // 3) / 2), text, font=FONT,
           fill=rgba(T["white"]), anchor="mm")

    home = {c: p for p in "AB" for c in geo.home[p]}
    for c in geo.cells:
        fill = T[home[c] + "-light"] if c in home else T["cell"]
        d.polygon(hex_points(px(*c), HEX - SS), fill=rgba(fill), outline=rgba(T["cell-line"]),
                  width=round(1.5 * SS))
    for p in "AB":
        for c in geo.goal[p]:
            dashed(d, hex_points(px(*c), HEX * 0.78), rgba(T[p], 0.85), round(2.5 * SS),
                   6 * SS, 5 * SS)
    for (q, r) in geo.cells:
        center = px(q, r)
        for dq, dr in DIRS:
            if (q + dq, r + dr) in geo.cells:
                continue
            n = px(q + dq, r + dr)
            ang = math.degrees(math.atan2(n[1] - center[1], n[0] - center[0]))
            color = T["A"] if r + dr < -R else T["B"] if r + dr > R else T["wall"]
            thick(d, corner(center, HEX, ang - 30), corner(center, HEX, ang + 30),
                  rgba(color), round(5 * SS))

    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    o = ImageDraw.Draw(over)
    marks = marks or {}
    if marks.get("select"):
        c = px(*marks["select"])
        ring = [corner(c, HEX * 0.8, a) for a in range(0, 360, 10)]
        dashed(o, ring, rgba(T["text"]), round(2.5 * SS), 4 * SS, 3 * SS)
    for m in marks.get("dests", ()):
        to = geo.nbr[(m.q, m.r)][m.d]
        on = m == marks.get("active")
        color = T["push"] if m.push else T["move"]
        pts = hex_points(px(*to), HEX * 0.86)
        o.polygon(pts, fill=rgba(color, (0.42 if on else 0.16) if m.push else
                                 (0.42 if on else 0.2)))
        w = round((5 if on else 3) * SS)
        if m.push:
            dashed(o, pts, rgba(color), w, 7 * SS, 4 * SS)
        else:
            o.polygon(pts, outline=rgba(color), width=w)
            x, y = px(*to)
            rr = HEX * 0.16
            o.ellipse([x - rr, y - rr, x + rr, y + rr], fill=rgba(color))
    for to in marks.get("barred", ()):
        c = px(*to)
        o.polygon(hex_points(c, HEX * 0.86), fill=rgba(T["locked"], 0.3),
                  outline=rgba(T["locked"]), width=round(2 * SS))
    img.alpha_composite(over)

    # pedine: prima le ferme, poi quelle in movimento (sopra)
    moving = moving or {}
    still = [(c, p) for c, p in state.board.items() if c not in moving]
    for c, p in still + [(c, state.board[c]) for c in moving]:
        locked = c == state.locked and state.result is None
        piece(d, moving.get(c, px(*c)), p, T, locked)

    top = Image.new("RGBA", img.size, (0, 0, 0, 0))
    t = ImageDraw.Draw(top)
    for to in marks.get("barred", ()):
        c = px(*to)
        for a in (45, 135):
            thick(t, corner(c, HEX * 0.6, a + 180), corner(c, HEX * 0.6, a),
                  rgba(T["push"]), round(4 * SS))
    for p, src, dst, pushed in last:
        # come .arrow.last del sito: la punta si ferma sul bordo della pedina arrivata
        arrow(t, px(*src), px(*dst), rgba(T[p], 0.85), round(4 * SS), 0.3, 0.68)
        if pushed:
            arrow(t, px(*dst), px(*pushed), rgba(T[p], 0.85), round(3 * SS), 0.6, 0.68)
    m = marks.get("active")
    if m is not None and m.push:
        to = geo.nbr[(m.q, m.r)][m.d]
        arrow(t, px(*to), px(*geo.nbr[to][m.d]), rgba(T["push"]), round(5 * SS), 0.55, 0.12)
    img.alpha_composite(top)
    return img.convert("RGB").resize((WIDTH // SS, HEIGHT // SS), Image.LANCZOS)


def piece(d, c, p, T, locked=False):
    x, y = c
    rr = HEX * 0.64
    body, line = (T["locked"], T["locked-line"]) if locked else (T[p], T[p + "-dark"])
    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=rgba(body), outline=rgba(line),
              width=round(2.5 * SS))
    s = HEX * 0.3
    if p == "A":
        d.ellipse([x - s, y - s, x + s, y + s], outline=rgba(T["white"]), width=round(4 * SS))
    else:
        d.polygon([(x, y - s), (x + s, y), (x, y + s), (x - s, y)], fill=rgba(T["white"]))


# --- sceneggiatura di un esempio -----------------------------------------------------

class Clip:
    """Esempio animato: le mosse passano dal motore, i fotogrammi si accumulano per
    entrambi i temi."""

    def __init__(self, board, to_move, moves_left=2):
        self.state = from_board(RULES, board, to_move, moves_left=moves_left)
        self.frames = {theme: [] for theme in THEMES}
        self.ms = []
        self.turn = []   # mosse del turno in corso
        self.last = []   # mosse del turno precedente, disegnate come frecce

    def _add(self, ms, bar, **kw):
        for theme in THEMES:
            last = self.last if self.state.to_move == "A" else ()
            self.frames[theme].append(frame(theme, self.state, bar, last=last, **kw))
        self.ms.append(ms)

    def _move(self, q, r, d):
        legal = [m for m in legal_moves(self.state) if m and (m.q, m.r, m.d) == (q, r, d)]
        assert legal, f"mossa illegale: {(q, r, d)} con {self.state.to_move} di turno"
        return legal[0]

    def _marks(self, cell, barred=(), active=None):
        dests = [m for m in legal_moves(self.state) if m and (m.q, m.r) == cell]
        for to in barred:
            assert all(self.state.geo.nbr[cell][m.d] != to for m in dests), \
                f"{cell} -> {to} doveva essere vietata"
        return {"select": cell, "dests": dests, "barred": barred, "active": active}

    def bar(self, text=None):
        s = self.state
        if text is None:
            text = f"Tocca a {NAMES[s.to_move]}"
            if s.locked is not None:
                text += " · 2ª mossa"
        return (s.result if s.result in NAMES else s.to_move, text)

    def show(self, ms, text=None, select=None, barred=()):
        """Fermo immagine, eventualmente con una pedina selezionata e le sue mosse."""
        marks = self._marks(select, barred) if select else None
        self._add(ms, self.bar(text), marks=marks)

    def play(self, q, r, d, text=None, think=900, aim=700, after=0):
        """Selezione (think ms), mira sulla destinazione (aim ms), animazione."""
        m = self._move(q, r, d)
        bar = self.bar(text)
        if think:
            self._add(think, bar, marks=self._marks((q, r)))
        if aim:
            self._add(aim, bar, marks=self._marks((q, r), active=m))
        geo = self.state.geo
        src = (q, r)
        dst = geo.nbr[src][d]
        behind = geo.nbr[dst][d] if m.push else None
        for i in range(1, STEP_FRAMES):
            t = 1 - (1 - i / STEP_FRAMES) ** 3
            moving = {src: lerp(px(*src), px(*dst), t)}
            if behind:
                moving[dst] = lerp(px(*dst), px(*behind), t)
            self._add(STEP_MS, bar, moving=moving)
        player = self.state.to_move
        self.state = apply(self.state, m)
        self.turn.append((player, src, dst, behind))
        if self.state.to_move != player:
            self.last, self.turn = self.turn, []
        if after:
            self._add(after, bar)

    def save(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        for theme, frames in self.frames.items():
            path = OUT / (f"{name}.gif" if theme == "chiaro" else f"{name}-{theme}.gif")
            # una palette sola per tutti i fotogrammi e niente dithering: così tra un
            # fotogramma e l'altro cambiano solo i pixel che cambiano davvero e la GIF
            # salva soltanto quelli
            w, h = frames[0].size
            sample = Image.new("RGB", (w, h * len(frames)))
            for i, f in enumerate(frames):
                sample.paste(f, (0, h * i))
            pal = sample.quantize(255, method=Image.Quantize.FASTOCTREE)
            frames = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=self.ms,
                           loop=0, optimize=True)
            print(f"{path.relative_to(ROOT)}  {len(frames)} fotogrammi, "
                  f"{path.stat().st_size // 1024} kB")


# --- gli esempi ----------------------------------------------------------------------
# Celle con R=3, fila per fila: r=-3 q 0..3 (casa di Arancio, in basso), r=-2 q -1..3,
# r=-1 q -2..3, r=0 q -3..3, r=1 q -3..2, r=2 q -3..1, r=3 q -3..0 (casa di Blu).
# Direzioni (board.DIRS): 0 (+1,0)  1 (-1,0)  2 (0,+1)  3 (0,-1)  4 (+1,-1)  5 (-1,+1).
# Per Arancio "avanti" è 2 (in alto a sinistra) o 5 (in alto a destra).

def board(a, b):
    return {**{c: "A" for c in a}, **{c: "B" for c in b}}


def turno():
    c = Clip(board([(0, -3), (1, -3), (2, -3), (3, -3)],
                   [(-3, 3), (-2, 3), (-1, 3), (0, 3)]), "A")
    c.show(1300, "Tocca a te: muovi due pedine")
    c.play(1, -3, 2, "1ª mossa: una casella verde", think=1300, after=500)
    c.show(1500, "2ª mossa: un'altra pedina")
    c.play(3, -3, 5, "2ª mossa: un'altra pedina", think=1100, after=400)
    c.show(700)
    c.play(-1, 3, 3, think=0, aim=0)
    c.play(0, 3, 4, think=0, aim=0, after=1800)
    c.save("turno")


def spinta():
    c = Clip(board([(0, -1), (1, -3), (3, -3), (2, -2)],
                   [(0, 0), (-2, 3), (-3, 2), (-1, 2)]), "A", moves_left=1)
    c.show(1000)
    c.show(1600, "Rosso: spinta", select=(0, -1))
    c.play(0, -1, 2, "Dietro c'è posto: spingi!", think=0, aim=1400, after=1900)
    c.save("spinta")


def spinte_vietate():
    c = Clip(board([(0, -1), (2, -1), (1, -3)],
                   [(0, 0), (0, 1), (3, -1), (-2, 3), (-1, 2)]), "A")
    c.show(900)
    c.show(2400, "Dietro è occupato: no", select=(0, -1), barred=[(0, 0)])
    c.show(2400, "Dietro c'è il muro: no", select=(2, -1), barred=[(3, -1)])
    c.save("spinte-vietate")


def spinta_propria():
    c = Clip(board([(0, -2), (0, -1), (2, -3), (3, -3)],
                   [(-2, 3), (-1, 3), (-3, 2), (1, 1)]), "A", moves_left=1)
    c.show(1000)
    c.show(1500, "Puoi spingere anche le tue", select=(0, -2))
    c.play(0, -2, 2, "Avanzano tutte e due", think=0, aim=1300, after=2000)
    c.save("spinta-propria")


def niente_rispinta():
    c = Clip(board([(0, 0), (2, -2), (1, -3), (3, -3)],
                   [(0, 1), (-2, 2), (-1, 3), (-3, 3)]), "B")
    c.show(900)
    c.play(0, 1, 3, "Blu spinge la tua pedina", think=0, aim=1200, after=900)
    c.play(-2, 2, 3, think=0, aim=0)
    c.show(900)
    c.show(2800, "Non puoi rispingerla indietro", select=(0, -1), barred=[(0, 0)])
    c.play(0, -1, 5, "Le altre mosse vanno bene", think=1300, aim=600, after=1500)
    c.save("niente-rispinta")


def vittoria():
    c = Clip(board([(-1, 2), (1, -3), (2, -2), (2, -3)],
                   [(-1, 3), (-3, 3), (1, 0), (-2, 1)]), "A", moves_left=1)
    c.show(1000)
    c.show(1600, "Il traguardo: la casa di Blu", select=(-1, 2))
    c.play(-1, 2, 5, think=0, aim=900, text="Il traguardo: la casa di Blu")
    assert c.state.result == "A"
    c.show(2600, "Arancio vince!")
    c.save("vittoria")


if __name__ == "__main__":
    for example in (turno, spinta, spinte_vietate, spinta_propria, niente_rispinta, vittoria):
        example()
