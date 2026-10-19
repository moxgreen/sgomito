#!/bin/sh
# Batteria livello 3: 100 partite per colore per incontro.
cd "$(dirname "$0")/.."
for opts in "--ko" ""; do
  for pair in "mcts:2000 mcts:100" "mcts:2000 greedy" "mcts:2000 rusher" "mcts:2000 turtle" "mcts:500 mcts:500"; do
    set -- $pair
    echo "##### $1 vs $2 $opts"
    pypy3 -m hexpush.simulate --botA $1 --botB $2 --games 100 --swap $opts
  done
done
