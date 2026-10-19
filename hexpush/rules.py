"""Parametri del gioco. Le varianti sono solo configurazione, mai fork del codice."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Ruleset:
    R: int = 4                # raggio dell'esagono (R=4 -> 61 celle)
    N: int = 5                # pedine per giocatore
    K: int = 1                # pedine da portare sul traguardo per vincere
    goal_wide: bool = False   # traguardo esteso alle 2 celle laterali adiacenti alla casa nemica
    push_own: bool = True     # si possono spingere le proprie pedine
    ko: bool = False          # vietato ricreare la posizione di 2 semimosse fa
    push_off: bool = False    # si può spingere fuori dal campo una pedina avversaria: è eliminata
    moves_per_turn: int = 1   # 2: due mosse per turno con pedine diverse (A apre con una sola)
    max_plies: int = 200      # oltre questo numero di semimosse la partita è patta

    def __post_init__(self):
        if self.R < 2:
            raise ValueError(f"R deve essere >= 2, non {self.R}")
        # le pedine stanno nella fila di casa (R+1) e nella successiva (R+2)
        cap = 2 * self.R + 3
        if not 1 <= self.N <= cap:
            raise ValueError(f"N deve stare tra 1 e {cap} con R={self.R}, non {self.N}")
        if not 1 <= self.K <= self.N:
            raise ValueError(f"K deve stare tra 1 e N={self.N}, non {self.K}")
        if self.moves_per_turn not in (1, 2):
            raise ValueError(f"moves_per_turn deve essere 1 o 2, non {self.moves_per_turn}")
        if self.max_plies < 1:
            raise ValueError(f"max_plies deve essere >= 1, non {self.max_plies}")
