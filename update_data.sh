#!/bin/bash
# Met à jour les données de prédiction et push sur GitHub Pages
cd "$(dirname "$0")"

export FOOTBALL_API_KEY="f8e9bc2714ae490fb616754fcfaeea8d"
export MMA_API_KEY="eb297ce2cd03e8621314c1a7c8eb1730"
export ODDS_API_KEY="f0b3e2ed8beb3f274efeefd254df1417"

python3 -c "
import sys, json
sys.path.insert(0, '.')
import server

football = server.get_predictions()
with open('data/football.json', 'w') as f: json.dump(football, f)
print(f'Football: {len(football)} matchs')

mma = server.get_mma_predictions()
with open('data/mma.json', 'w') as f: json.dump(mma, f)
print(f'MMA: {len(mma)} fights')

boxing = server.get_boxing_predictions()
with open('data/boxing.json', 'w') as f: json.dump(boxing, f)
print(f'Boxing: {len(boxing)} fights')
"

git add data/
git commit -m "Update predictions $(date +%Y-%m-%d_%H:%M)" --allow-empty
git push origin main
echo "Done!"
