from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import math
import urllib.request
import os
import sys

API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
MMA_API_KEY = os.environ.get("MMA_API_KEY", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.football-data.org/v4"

# ═══════════════ FOOTBALL ═══════════════

def api_get(path):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"X-Auth-Token": API_KEY})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def poisson_prob(lam, k):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def predict_match(home_team_data, away_team_data):
    home_attack = home_team_data.get("avg_scored_home", 1.4)
    home_defense = home_team_data.get("avg_conceded_home", 1.1)
    away_attack = away_team_data.get("avg_scored_away", 1.1)
    away_defense = away_team_data.get("avg_conceded_away", 1.3)
    league_avg = 1.25
    home_expected = (home_attack / league_avg) * (away_defense / league_avg) * league_avg
    away_expected = (away_attack / league_avg) * (home_defense / league_avg) * league_avg
    home_expected = max(0.3, min(home_expected, 4.0))
    away_expected = max(0.3, min(away_expected, 4.0))

    max_goals = 6
    home_win = draw = away_win = 0
    score_matrix = []
    for h in range(max_goals + 1):
        row = []
        for a in range(max_goals + 1):
            p = poisson_prob(home_expected, h) * poisson_prob(away_expected, a)
            row.append(round(p, 4))
            if h > a: home_win += p
            elif h == a: draw += p
            else: away_win += p
        score_matrix.append(row)

    best_score = (0, 0)
    best_prob = 0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            if score_matrix[h][a] > best_prob:
                best_prob = score_matrix[h][a]
                best_score = (h, a)

    top_scores = []
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            top_scores.append({"score": f"{h}-{a}", "prob": score_matrix[h][a]})
    top_scores.sort(key=lambda x: -x["prob"])

    btts_yes = sum(score_matrix[h][a] for h in range(1, max_goals+1) for a in range(1, max_goals+1))
    over_25 = sum(score_matrix[h][a] for h in range(max_goals+1) for a in range(max_goals+1) if h+a > 2)

    return {
        "home_expected_goals": round(home_expected, 2),
        "away_expected_goals": round(away_expected, 2),
        "home_win": round(home_win * 100, 1),
        "draw": round(draw * 100, 1),
        "away_win": round(away_win * 100, 1),
        "predicted_score": f"{best_score[0]}-{best_score[1]}",
        "predicted_score_prob": round(best_prob * 100, 1),
        "top_scores": top_scores[:5],
        "btts": round(btts_yes * 100, 1),
        "over_25": round(over_25 * 100, 1),
    }

def compute_team_stats(team_id, matches):
    from datetime import datetime, timezone
    home_scored, home_conceded, away_scored, away_conceded = [], [], [], []
    home_weights, away_weights = [], []
    form = []
    now = datetime.now(timezone.utc)
    sorted_matches = sorted(matches, key=lambda m: m.get("utcDate", ""))

    for m in sorted_matches:
        if m.get("status") != "FINISHED": continue
        home_id = m["homeTeam"]["id"]
        away_id = m["awayTeam"]["id"]
        hs = m["score"]["fullTime"]["home"]
        as_ = m["score"]["fullTime"]["away"]
        if hs is None or as_ is None: continue
        try:
            match_date = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
            days_ago = (now - match_date).days
        except:
            days_ago = 90
        weight = max(0.3, 1.0 - (days_ago / 180))
        if home_id == team_id:
            home_scored.append(hs); home_conceded.append(as_); home_weights.append(weight)
            form.append("W" if hs > as_ else ("D" if hs == as_ else "L"))
        elif away_id == team_id:
            away_scored.append(as_); away_conceded.append(hs); away_weights.append(weight)
            form.append("W" if as_ > hs else ("D" if as_ == hs else "L"))

    def weighted_avg(values, weights, default):
        if not values: return default
        total_w = sum(weights)
        if total_w == 0: return default
        return sum(v * w for v, w in zip(values, weights)) / total_w

    return {
        "avg_scored_home": weighted_avg(home_scored, home_weights, 1.4),
        "avg_conceded_home": weighted_avg(home_conceded, home_weights, 1.1),
        "avg_scored_away": weighted_avg(away_scored, away_weights, 1.1),
        "avg_conceded_away": weighted_avg(away_conceded, away_weights, 1.3),
        "form": "".join(form[-5:]),
        "played_home": len(home_scored),
        "played_away": len(away_scored),
    }

COMPETITIONS = ["CL", "FL1", "PL", "PD", "SA", "BL1", "WC", "EC", "PPL", "DED", "BSA", "ELC"]

_cache = {"data": None, "time": 0}
CACHE_TTL = 300

def get_predictions():
    import time as _time
    cache_now = _time.time()
    if _cache["data"] and (cache_now - _cache["time"]) < CACHE_TTL:
        return _cache["data"]

    from datetime import datetime, timedelta, timezone
    utc_now = datetime.now(timezone.utc)
    today = utc_now.strftime("%Y-%m-%d")
    end_date = (utc_now + timedelta(days=30)).strftime("%Y-%m-%d")

    all_upcoming = []
    all_finished = {}
    import time as _t
    for idx, comp in enumerate(COMPETITIONS):
        if idx > 0: _t.sleep(7)
        try:
            fixtures = api_get(f"/competitions/{comp}/matches?dateFrom={today}&dateTo={end_date}")
            upcoming = [m for m in fixtures.get("matches", []) if m["status"] in ("SCHEDULED", "TIMED")]
            if not upcoming and comp in ("CL", "WC", "EC"):
                for season in ("2025", "2024"):
                    try:
                        fixtures = api_get(f"/competitions/{comp}/matches?season={season}")
                        upcoming = [m for m in fixtures.get("matches", []) if m["status"] in ("SCHEDULED", "TIMED")]
                        if upcoming: break
                    except: continue
            if upcoming:
                finished_resp = api_get(f"/competitions/{comp}/matches?status=FINISHED")
                finished_list = finished_resp.get("matches", [])
                if not finished_list and comp in ("CL", "WC", "EC"):
                    for season in ("2025", "2024"):
                        try:
                            finished_resp = api_get(f"/competitions/{comp}/matches?season={season}&status=FINISHED")
                            finished_list = finished_resp.get("matches", [])
                            if finished_list: break
                        except: continue
                for m in upcoming:
                    all_upcoming.append(m)
                all_finished[comp] = finished_list
        except:
            continue

    predictions = []
    for match in all_upcoming:
        home_id = match["homeTeam"]["id"]
        away_id = match["awayTeam"]["id"]
        comp_code = match.get("competition", {}).get("code", "")
        finished_matches = all_finished.get(comp_code, [])
        home_stats = compute_team_stats(home_id, finished_matches)
        away_stats = compute_team_stats(away_id, finished_matches)
        pred = predict_match(home_stats, away_stats)
        pred["home_team"] = match["homeTeam"]["name"]
        pred["away_team"] = match["awayTeam"]["name"]
        pred["home_crest"] = match["homeTeam"].get("crest", "")
        pred["away_crest"] = match["awayTeam"].get("crest", "")
        pred["date"] = match["utcDate"]
        pred["competition"] = match.get("competition", {}).get("name", "Unknown")
        pred["home_form"] = home_stats["form"]
        pred["away_form"] = away_stats["form"]
        pred["home_games"] = home_stats["played_home"] + home_stats["played_away"]
        pred["away_games"] = away_stats["played_home"] + away_stats["played_away"]
        predictions.append(pred)

    predictions.sort(key=lambda p: p["date"])
    _cache["data"] = predictions
    _cache["time"] = cache_now
    return predictions

_backtest_cache = {"data": None, "time": 0}

def get_backtest():
    import time as _t
    cache_now = _t.time()
    if _backtest_cache["data"] and (cache_now - _backtest_cache["time"]) < 600:
        return _backtest_cache["data"]

    from datetime import datetime, timezone
    results = {"total": 0, "outcome_correct": 0, "exact_score_correct": 0,
               "btts_correct": 0, "over25_correct": 0, "matches": []}
    comps_to_test = ["PL", "PD", "BL1", "SA", "FL1"]
    import time as _t2
    for idx, comp in enumerate(comps_to_test):
        if idx > 0: _t2.sleep(7)
        try:
            resp = api_get(f"/competitions/{comp}/matches?status=FINISHED")
            finished = resp.get("matches", [])
            if not finished: continue
            sorted_finished = sorted(finished, key=lambda m: m.get("utcDate", ""))
            for i, match in enumerate(sorted_finished):
                if i < 5: continue
                home_id = match["homeTeam"]["id"]
                away_id = match["awayTeam"]["id"]
                actual_home = match["score"]["fullTime"]["home"]
                actual_away = match["score"]["fullTime"]["away"]
                if actual_home is None or actual_away is None: continue
                prior_matches = sorted_finished[:i]
                home_stats = compute_team_stats(home_id, prior_matches)
                away_stats = compute_team_stats(away_id, prior_matches)
                if home_stats["played_home"] + home_stats["played_away"] < 3: continue
                pred = predict_match(home_stats, away_stats)
                actual_outcome = "H" if actual_home > actual_away else ("D" if actual_home == actual_away else "A")
                pred_outcome = "H" if pred["home_win"] > pred["away_win"] and pred["home_win"] > pred["draw"] else (
                    "D" if pred["draw"] > pred["home_win"] and pred["draw"] > pred["away_win"] else "A")
                outcome_ok = actual_outcome == pred_outcome
                pred_h, pred_a = map(int, pred["predicted_score"].split("-"))
                exact_ok = pred_h == actual_home and pred_a == actual_away
                actual_btts = actual_home > 0 and actual_away > 0
                btts_ok = actual_btts == (pred["btts"] > 50)
                actual_over25 = (actual_home + actual_away) > 2
                over25_ok = actual_over25 == (pred["over_25"] > 50)
                results["total"] += 1
                if outcome_ok: results["outcome_correct"] += 1
                if exact_ok: results["exact_score_correct"] += 1
                if btts_ok: results["btts_correct"] += 1
                if over25_ok: results["over25_correct"] += 1
                results["matches"].append({
                    "home_team": match["homeTeam"]["name"], "away_team": match["awayTeam"]["name"],
                    "actual_score": f"{actual_home}-{actual_away}", "predicted_score": pred["predicted_score"],
                    "predicted_outcome": pred_outcome, "actual_outcome": actual_outcome,
                    "outcome_correct": outcome_ok, "exact_correct": exact_ok,
                    "date": match["utcDate"],
                    "competition": match.get("competition", {}).get("name", ""),
                })
        except:
            continue

    total = results["total"]
    if total > 0:
        results["outcome_pct"] = round(results["outcome_correct"] / total * 100, 1)
        results["exact_pct"] = round(results["exact_score_correct"] / total * 100, 1)
        results["btts_pct"] = round(results["btts_correct"] / total * 100, 1)
        results["over25_pct"] = round(results["over25_correct"] / total * 100, 1)
    else:
        results["outcome_pct"] = results["exact_pct"] = results["btts_pct"] = results["over25_pct"] = 0
    results["matches"] = results["matches"][-50:]
    _backtest_cache["data"] = results
    _backtest_cache["time"] = cache_now
    return results


# ═══════════════ MMA (Elo + API-Sports) ═══════════════

MMA_BASE = "https://v1.mma.api-sports.io"
_mma_cache = {"data": None, "time": 0}
MMA_CACHE_TTL = 900

def mma_api_get(path):
    url = f"{MMA_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "x-apisports-key": MMA_API_KEY,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def elo_win_prob(elo_a, elo_b):
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400))

def compute_elo_from_fights(fights):
    elo = {}
    sorted_fights = sorted(fights, key=lambda f: f.get("date", "") or "")
    for fight in sorted_fights:
        fighters = fight.get("fighters", [])
        if len(fighters) < 2:
            continue
        f1 = fighters[0]
        f2 = fighters[1]
        id1 = f1.get("id", 0)
        id2 = f2.get("id", 0)
        if id1 not in elo: elo[id1] = 1500
        if id2 not in elo: elo[id2] = 1500

        winner = fight.get("winner", {})
        winner_id = winner.get("id") if winner else None
        method = (fight.get("method") or "").lower()
        k = 48 if any(m in method for m in ("ko", "tko", "sub")) else 32

        e1 = elo_win_prob(elo[id1], elo[id2])
        e2 = 1 - e1

        if winner_id == id1:
            elo[id1] += k * (1 - e1)
            elo[id2] += k * (0 - e2)
        elif winner_id == id2:
            elo[id1] += k * (0 - e1)
            elo[id2] += k * (1 - e2)
        else:
            elo[id1] += k * (0.5 - e1)
            elo[id2] += k * (0.5 - e2)

    return {k: round(v) for k, v in elo.items()}

def get_mma_predictions():
    import time as _t
    cache_now = _t.time()
    if _mma_cache["data"] and (cache_now - _mma_cache["time"]) < MMA_CACHE_TTL:
        return _mma_cache["data"]

    if not ODDS_API_KEY:
        return {"error": "ODDS_API_KEY not set. Get free key at the-odds-api.com"}

    url = f"https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds?apiKey={ODDS_API_KEY}&regions=eu,us,uk&markets=h2h&oddsFormat=decimal"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            events = json.loads(resp.read())
    except Exception as e:
        return {"error": f"Odds API error: {e}"}

    predictions = []
    for event in events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence = event.get("commence_time", "")
        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue

        all_odds = {}
        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "")
                    price = outcome.get("price", 0)
                    if name not in all_odds:
                        all_odds[name] = []
                    all_odds[name].append(price)

        avg_odds = {name: sum(prices)/len(prices) for name, prices in all_odds.items() if prices}
        raw_probs = {name: 1/odds for name, odds in avg_odds.items() if odds > 0}
        total_prob = sum(raw_probs.values())
        if total_prob == 0:
            continue

        norm_probs = {name: round(p / total_prob * 100, 1) for name, p in raw_probs.items()}
        prob_a = norm_probs.get(home, 50)
        prob_b = norm_probs.get(away, 50)

        # Estimate method probabilities from odds spread
        spread = abs(prob_a - prob_b)
        method_ko = round(min(45, 25 + spread * 0.3), 1)
        method_sub = round(max(10, 20 - spread * 0.1), 1)
        method_dec = round(max(10, 100 - method_ko - method_sub), 1)

        confidence = min(95, 40 + len(bookmakers) * 5)

        sport_title = event.get("sport_title", "MMA")
        predictions.append({
            "fighter_a": home,
            "fighter_b": away,
            "prob_a": prob_a,
            "prob_b": prob_b,
            "record_a": "",
            "record_b": "",
            "ko_ratio_a": None,
            "ko_ratio_b": None,
            "streak_a": 0,
            "streak_b": 0,
            "reach_a": None,
            "reach_b": None,
            "weight_class": sport_title,
            "event": sport_title,
            "date": commence,
            "method_ko": method_ko,
            "method_sub": method_sub,
            "method_dec": method_dec,
            "confidence": confidence,
            "bookmakers": len(bookmakers),
        })

    predictions.sort(key=lambda p: p.get("date", ""))
    _mma_cache["data"] = predictions
    _mma_cache["time"] = cache_now
    return predictions


# ═══════════════ BOXING (The Odds API) ═══════════════

ODDS_BASE = "https://api.the-odds-api.com/v4"
_boxing_cache = {"data": None, "time": 0}
BOXING_CACHE_TTL = 3600

def get_boxing_predictions():
    import time as _t
    cache_now = _t.time()
    if _boxing_cache["data"] and (cache_now - _boxing_cache["time"]) < BOXING_CACHE_TTL:
        return _boxing_cache["data"]

    if not ODDS_API_KEY:
        return {"error": "ODDS_API_KEY not set. Get free key at the-odds-api.com"}

    url = f"{ODDS_BASE}/sports/boxing_boxing/odds?apiKey={ODDS_API_KEY}&regions=eu,us,uk&markets=h2h&oddsFormat=decimal"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            events = json.loads(resp.read())
    except Exception as e:
        return {"error": f"Odds API error: {e}"}

    predictions = []
    for event in events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence = event.get("commence_time", "")
        bookmakers = event.get("bookmakers", [])

        if not bookmakers:
            continue

        all_odds = {}
        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "")
                    price = outcome.get("price", 0)
                    if name not in all_odds:
                        all_odds[name] = []
                    all_odds[name].append(price)

        # Average odds → implied probability (remove vig with power method)
        avg_odds = {name: sum(prices)/len(prices) for name, prices in all_odds.items() if prices}
        raw_probs = {name: 1/odds for name, odds in avg_odds.items() if odds > 0}
        total_prob = sum(raw_probs.values())

        if total_prob == 0:
            continue

        norm_probs = {name: round(p / total_prob * 100, 1) for name, p in raw_probs.items()}

        prob_a = norm_probs.get(home, 50)
        prob_b = norm_probs.get(away, 50)
        draw_prob = norm_probs.get("Draw", 0)

        # Renormalize without draw for a cleaner display
        if draw_prob:
            remaining = prob_a + prob_b
            if remaining > 0:
                pass  # keep as is, draw is meaningful in boxing

        predictions.append({
            "fighter_a": home,
            "fighter_b": away,
            "prob_a": prob_a,
            "prob_b": prob_b,
            "draw": draw_prob,
            "date": commence,
            "bookmakers": len(bookmakers),
        })

    predictions.sort(key=lambda p: p.get("date", ""))
    _boxing_cache["data"] = predictions
    _boxing_cache["time"] = cache_now
    return predictions


# ═══════════════ HTTP SERVER ═══════════════

class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/api/predictions": get_predictions,
            "/api/backtest": get_backtest,
            "/api/mma/predictions": get_mma_predictions,
            "/api/boxing/predictions": get_boxing_predictions,
        }
        if self.path in routes:
            try:
                data = routes[self.path]()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    if not API_KEY:
        print("Warning: FOOTBALL_API_KEY not set (football predictions disabled)")
        print("Get your free key at: https://www.football-data.org/client/register")
    if not MMA_API_KEY:
        print("Warning: MMA_API_KEY not set (MMA predictions disabled)")
        print("Get your free key at: https://api-sports.io")
    if not ODDS_API_KEY:
        print("Warning: ODDS_API_KEY not set (boxing predictions disabled)")
        print("Get your free key at: https://the-odds-api.com")

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    port = int(os.environ.get("PORT", 8080))
    server = ReusableHTTPServer(("0.0.0.0", port), Handler)
    print(f"SportPredict AI running on http://0.0.0.0:{port}")
    server.serve_forever()
