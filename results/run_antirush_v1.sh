#!/bin/sh
# Curva contro il Rusher, regole attuali e con push_off (100 partite per livello).
cd "$(dirname "$0")/.."
echo "##### base (ko)"
pypy3 -m hexpush.ladder --ko --bot mctsd --levels 500,2000,8000,32000 --against rusher --games 100 --out results/antirush_ko
echo "##### push_off"
pypy3 -m hexpush.ladder --ko --push-off --bot mctsd --levels 500,2000,8000,32000 --against rusher --games 100 --out results/antirush_ko_pushoff
echo "##### push_off: rusher vs rusher, mctsd:2000 vs greedy"
pypy3 -m hexpush.simulate --ko --push-off --botA rusher --botB rusher --games 400
pypy3 -m hexpush.simulate --ko --push-off --botA mctsd:2000 --botB greedy --games 100 --swap
