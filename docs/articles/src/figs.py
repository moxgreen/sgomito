"""SVG helpers for the articles: hex boards and simple bar charts, styled by page CSS tokens."""
import math

S3 = math.sqrt(3)


def on_board(R, q, r):
    return abs(q) <= R and abs(r) <= R and abs(q + r) <= R


def center(q, r, size):
    return size * S3 * (q + r / 2), size * 1.5 * r


def hex_points(x, y, size):
    pts = []
    for k in range(6):
        a = math.radians(60 * k - 30)
        pts.append(f"{x + size * math.cos(a):.1f},{y + size * math.sin(a):.1f}")
    return " ".join(pts)


def board_svg(pieces, R=4, size=20, arrows=(), marks=(), label="", caption_cells=None,
              flip=False):
    """pieces: {(q, r): 'A'|'B'}; arrows: [((q,r),(q,r),'a'|'b'|'n')]; marks: cells ringed.
    flip=True puts A at the bottom (rotates the board 180 degrees)."""
    def pos(q, r):
        x, y = center(q, r, size)
        return (-x, -y) if flip else (x, y)
    cells = [(q, r) for q in range(-R, R + 1) for r in range(-R, R + 1) if on_board(R, q, r)]
    w = size * S3 * (2 * R + 1) + 8
    h = size * (3 * R + 2) + 8
    out = [f'<svg class="board" viewBox="{-w / 2:.0f} {-h / 2:.0f} {w:.0f} {h:.0f}" '
           f'role="img" aria-label="{label}">']
    for q, r in cells:
        x, y = pos(q, r)
        cls = "cell"
        if r == -R:
            cls += " home-a"
        elif r == R:
            cls += " home-b"
        out.append(f'<polygon class="{cls}" points="{hex_points(x, y, size * 0.97)}"/>')
    for c in marks:
        x, y = pos(*c)
        out.append(f'<circle class="mark" cx="{x:.1f}" cy="{y:.1f}" r="{size * 0.78:.1f}"/>')
    for (q, r), p in pieces.items():
        x, y = pos(q, r)
        out.append(f'<circle class="piece p{p.lower()}" cx="{x:.1f}" cy="{y:.1f}" '
                   f'r="{size * 0.58:.1f}"/>')
    for a, b, kind in arrows:
        x1, y1 = pos(*a)
        x2, y2 = pos(*b)
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        sx, sy = x1 + dx / L * size * 0.45, y1 + dy / L * size * 0.45
        ex, ey = x2 - dx / L * size * 0.55, y2 - dy / L * size * 0.55
        out.append(f'<line class="arrow arr-{kind}" x1="{sx:.1f}" y1="{sy:.1f}" '
                   f'x2="{ex:.1f}" y2="{ey:.1f}" marker-end="url(#head-{kind})"/>')
    out.append("</svg>")
    return "\n".join(out)


def hbar_svg(rows, vmax=1.0, width=560, bar_h=22, gap=8, label_w=230, fmt="{:.0%}",
             ticks=(0, 0.25, 0.5, 0.75, 1.0), ref=None, err=None, title=""):
    """rows: [(label, value, cls)]; err: optional [(lo, hi)] per row; ref: vertical reference
    line value."""
    plot_w = width - label_w - 60
    h = len(rows) * (bar_h + gap) + 30
    sx = lambda v: label_w + v / vmax * plot_w
    out = [f'<svg class="chart" viewBox="0 0 {width} {h}" role="img" aria-label="{title}">']
    for t in ticks:
        x = sx(t)
        out.append(f'<line class="grid" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{h - 6}" text-anchor="middle">'
                   f'{fmt.format(t)}</text>')
    if ref is not None:
        x = sx(ref)
        out.append(f'<line class="ref" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}"/>')
    for i, (lab, v, cls) in enumerate(rows):
        y = i * (bar_h + gap)
        out.append(f'<text class="lab" x="{label_w - 10}" y="{y + bar_h * 0.7:.1f}" '
                   f'text-anchor="end">{lab}</text>')
        out.append(f'<rect class="bar {cls}" x="{label_w}" y="{y}" '
                   f'width="{max(sx(v) - label_w, 1):.1f}" height="{bar_h}" rx="2"/>')
        if err:
            lo, hi = err[i]
            out.append(f'<line class="err" x1="{sx(lo):.1f}" y1="{y + bar_h / 2}" '
                       f'x2="{sx(hi):.1f}" y2="{y + bar_h / 2}"/>')
        tx = max(sx(v), sx(err[i][1]) if err else 0) + 6
        out.append(f'<text class="val" x="{tx:.1f}" y="{y + bar_h * 0.7:.1f}">'
                   f'{fmt.format(v)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def line_svg(series, xs, width=560, height=260, ymax=None, ymin=0, xlabel="", ylabel="",
             logx=False, yfmt="{:.0f}", xfmt="{}", yticks=None, title="", errs=None,
             ylabels=None, label_dy=None):
    """series: [(name, [y...], cls)] on common xs; errs: [(lo, hi)] for the first series."""
    ml, mr, mt, mb = 52, 110, 12, 40
    ys = [y for _, s, _ in series for y in s if y is not None]
    ymax = ymax if ymax is not None else max(ys)
    fx = (lambda x: math.log(x)) if logx else (lambda x: x)
    x0, x1 = fx(xs[0]), fx(xs[-1])
    px = lambda x: ml + (fx(x) - x0) / (x1 - x0) * (width - ml - mr)
    py = lambda y: mt + (1 - (y - ymin) / (ymax - ymin)) * (height - mt - mb)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">']
    for t in (yticks or [ymin + (ymax - ymin) * k / 4 for k in range(5)]):
        out.append(f'<line class="grid" x1="{ml}" y1="{py(t):.1f}" x2="{width - mr}" '
                   f'y2="{py(t):.1f}"/>')
        txt = ylabels[t] if ylabels else yfmt.format(t)
        out.append(f'<text class="tick" x="{ml - 6}" y="{py(t) + 4:.1f}" text-anchor="end">'
                   f'{txt}</text>')
    for x in xs:
        out.append(f'<text class="tick" x="{px(x):.1f}" y="{height - mb + 16}" '
                   f'text-anchor="middle">{xfmt.format(x)}</text>')
    out.append(f'<text class="axis" x="{(ml + width - mr) / 2:.0f}" y="{height - 4}" '
               f'text-anchor="middle">{xlabel}</text>')
    out.append(f'<text class="axis" x="{ml - 6}" y="{height - mb + 16}" '
               f'text-anchor="end">{ylabel}</text>')
    if errs:
        for x, (lo, hi) in zip(xs, errs):
            out.append(f'<line class="err" x1="{px(x):.1f}" y1="{py(max(lo, ymin)):.1f}" '
                       f'x2="{px(x):.1f}" y2="{py(min(hi, ymax)):.1f}"/>')
    for name, s, cls in series:
        pts = [(px(x), py(y)) for x, y in zip(xs, s) if y is not None]
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
        out.append(f'<path class="line {cls}" d="{d}"/>')
        for x, y in pts:
            out.append(f'<circle class="dot {cls}" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>')
        lx, ly = pts[-1]
        ly += (label_dy or {}).get(name, 0)
        out.append(f'<text class="serieslab {cls}" x="{lx + 8:.1f}" y="{ly + 4:.1f}">{name}</text>')
    out.append("</svg>")
    return "\n".join(out)


def profile_svg(features, groups, width=600, row_h=24, label_w=190, lim=2.0):
    """Diverging bars: features [(label)], groups [(title, [z...], cls)]."""
    k = len(groups)
    col_w = (width - label_w) / k
    h = 34 + len(features) * row_h
    out = [f'<svg class="chart" viewBox="0 0 {width} {h}" role="img" '
           f'aria-label="Style profiles">']
    for j, (title, zs, cls) in enumerate(groups):
        x0 = label_w + j * col_w
        mid = x0 + col_w / 2
        out.append(f'<text class="lab" x="{mid:.1f}" y="14" text-anchor="middle" '
                   f'font-weight="600">{title}</text>')
        out.append(f'<line class="ref" x1="{mid:.1f}" y1="24" x2="{mid:.1f}" y2="{h - 4}"/>')
        for i, z in enumerate(zs):
            y = 28 + i * row_h
            zc = max(-lim, min(lim, z))
            w = abs(zc) / lim * (col_w / 2 - 8)
            x = mid if zc >= 0 else mid - w
            out.append(f'<rect class="bar {cls}" x="{x:.1f}" y="{y + 4}" width="{max(w, 1):.1f}" '
                       f'height="{row_h - 10}" rx="2"/>')
    for i, lab in enumerate(features):
        y = 28 + i * row_h
        out.append(f'<text class="lab" x="{label_w - 10}" y="{y + row_h / 2 + 3:.1f}" '
                   f'text-anchor="end">{lab}</text>')
        if i:
            out.append(f'<line class="grid" x1="0" y1="{y:.1f}" x2="{width}" y2="{y:.1f}"/>')
    out.append("</svg>")
    return "\n".join(out)
