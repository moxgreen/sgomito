#!/bin/sh
# moves_per_turn=2 con ko: mini curva a raddoppi + controlli, poi MCTS(8000) contro il Rusher.
cd "$(dirname "$0")/.."
pypy3 -m hexpush.ladder --ko --moves-per-turn 2 --bot mctsd --levels 250,500,1000,2000 --games 100 --out results/mpt2_ladder
pypy3 -m hexpush.ladder --ko --moves-per-turn 2 --bot mctsd --levels 8000 --against rusher --games 100 --out results/mpt2_antirush8000
