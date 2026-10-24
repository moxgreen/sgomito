from pathlib import Path

STATIC = Path(__file__).parent / "static" / "play"


def asset_version():
    # data dell'ultima modifica di CSS e JavaScript: i browser li riscaricano appena
    # cambiano, anche a server acceso (le anteprime non sempre ricaricano a forza)
    return str(max(int(p.stat().st_mtime) for p in STATIC.iterdir() if p.is_file()))


def assets(request):
    return {"asset_version": asset_version()}
