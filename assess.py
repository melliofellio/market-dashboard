#!/usr/bin/env python3
"""Write the daily written assessment from data/latest.json using the Anthropic API.

The assessment may use ONLY the numbers in latest.json: no news, no outside data, no price targets.
If ANTHROPIC_API_KEY is missing or the call fails, the dashboard still updates and the previous
assessment stays on the page, labelled with its own date.
"""
import json
import os
import sys
import urllib.error
import urllib.request
import datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
MODELS = [m for m in [os.environ.get("ANTHROPIC_MODEL", "").strip(), "claude-sonnet-5-5", "claude-sonnet-4-5"] if m]

SYSTEM = """You are the analyst behind a personal market dashboard for one individual investor.
Write a daily assessment that draws conclusions from the aggregated indicator data below, so the reader is not
swayed by headlines or commentary.

Hard rules:
- Use ONLY the numbers provided. No news, no outside facts, no events you were not given. Never invent or estimate a value.
  If a figure is missing, stale, or carried forward, say so instead of working around it.
- Quote specific values and as-of dates. Each indicator has its own as-of date; do not treat them as simultaneous.
- No price targets, no probabilities presented as model output, no buy/sell instructions. Describe what the data says and what would change the read.
- Be a critical-thinking counterpart, not a cheerleader. Say where indicators disagree, and which group (leading, coincident, lagging) deserves more weight for the question at hand.
- Treat daily moves as information about money flow and participant behavior. Treat the weekly close as the number that carries weight.
- Credit spreads lag equity stress by days but lead the real economy; GDPNow and the derived series (net liquidity, policy expectations, rate volatility, equal-weight vs cap-weight) are constructed measures, not official statistics. Say so when you lean on them.
- Name a behavioral trap (recency, anchoring, narrative-chasing, over-reading one print) only when the data actually invites it today.
- Plain, concise, specific. Total length at most 380 words.

Return ONLY a JSON object with these string fields (use "" if not applicable):
headline: one sentence, the single most important thing in the data today
read: 3 to 5 sentences, the overall regime read across all groups
leading: 1 to 2 sentences on the leading group
coincident: 1 to 2 sentences on the coincident group
lagging: 1 to 2 sentences on the lagging group
disagreements: where the groups or the markets contradict each other, and what that implies
flows: what today's cross-asset moves say about money flow and participant psychology (rates, dollar, gold, crypto, equities, liquidity)
week: only if the latest observations complete a trading week (Friday or later): the weekly-close read; otherwise ""
watch: what to watch next, drawn from the calendar and the indicators closest to a regime change
trap: one sentence naming a behavioral trap if warranted, otherwise ""
caveats: data problems the reader should know (stale, failed, carried-forward, unusually lagged series), otherwise ""
"""


def fmt(v, rec):
    if v is None:
        return "n/a"
    s = f"{v:,.{rec['dec']}f}"
    return f"{rec['pre']}{s}{rec['suf']}"


def chg_text(rec):
    c = rec.get("chg")
    if c is None:
        return "n/a"
    k = rec["chg_kind"]
    if k == "bp":
        return f"{c * 100:+.0f} bp"
    if k == "pct" and rec.get("chg_pct") is not None:
        return f"{rec['chg_pct']:+.2f}%"
    return f"{c:+.{max(rec['dec'], 1)}f}"


def summarize(data):
    lines = []
    for sec in data["sections"]:
        lines.append(f"\n## {sec['title']}")
        for r in [i for i in data["indicators"] if i["section"] == sec["id"]]:
            parts = [f"{r['name']}: {fmt(r['value'], r)} (as of {r['asof']})",
                     f"vs prior obs {r['prev_asof']}: {chg_text(r)}"]
            w = r.get("wtd")
            if w:
                parts.append(f"week-to-date {w['pct']:+.2f}%" if w.get("pct") is not None and r["chg_kind"] == "pct"
                             else f"week-to-date {w['chg'] * (100 if r['chg_kind'] == 'bp' else 1):+.2f}" + (" bp" if r['chg_kind'] == 'bp' else ""))
                parts.append("week complete" if w.get("complete") else "week in progress")
            if r.get("trend"):
                parts.append(f"{r['trend_label']} trend: {r['trend']}")
            if r.get("pctile") is not None:
                parts.append(f"percentile in own history: {int(r['pctile'])}")
            for e in r.get("extra", []):
                parts.append(f"{e['k']}: {e['v']}")
            if r.get("stale"):
                parts.append("STALE" + (" (carried forward from earlier run)" if r.get("carried_forward") else ""))
            lines.append("- " + " | ".join(parts))
    if data.get("calendar"):
        lines.append("\n## Upcoming releases (next 30 days)")
        for e in data["calendar"]:
            lines.append(f"- {e['date']}: {e['event']}")
    h = data["health"]
    if h["failed"]:
        lines.append("\n## Fetch failures this run")
        for f in h["failed"]:
            lines.append(f"- {f['name']}: {f['error']}")
    return "\n".join(lines)


def call_api(key, model, user_text):
    body = json.dumps({"model": model, "max_tokens": 1800, "system": SYSTEM,
                       "messages": [{"role": "user", "content": user_text}]}).encode()
    base = os.environ.get("ANTHROPIC_BASE", "https://api.anthropic.com")
    req = urllib.request.Request(f"{base}/v1/messages", data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode())
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def main():
    latest_path = os.path.join(DATA, "latest.json")
    data = json.load(open(latest_path))
    hist_path = os.path.join(DATA, "assessments.json")
    hist = json.load(open(hist_path)) if os.path.exists(hist_path) else []

    def record_status(msg):
        data["health"]["assessment_error"] = msg
        json.dump(data, open(latest_path, "w"), separators=(",", ":"))

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("No ANTHROPIC_API_KEY; automatic assessment is off (weekly assessment is written by a Claude scheduled task instead)")
        return

    prior_heads = [f"{h['generated_ct']}: {h['content'].get('headline', '')}" for h in hist[:2]]
    user_text = (f"Data generated {data['generated_ct']} ({data['session']} run).\n"
                 f"Previous headlines for continuity (do not repeat them; say what changed):\n"
                 + "\n".join(prior_heads) + "\n\nINDICATORS" + summarize(data))

    last_err = None
    for model in MODELS:
        try:
            raw = call_api(key, model, user_text)
            content = parse_json(raw)
            entry = {"date": data["generated_ct"][:16], "generated_ct": data["generated_ct"],
                     "generated_utc": data["generated_utc"], "session": data["session"],
                     "model": model, "content": content}
            hist.insert(0, entry)
            hist = hist[:120]
            json.dump(hist, open(hist_path, "w"), indent=1)
            data["health"].pop("assessment_error", None)
            json.dump(data, open(latest_path, "w"), separators=(",", ":"))
            print(f"Assessment written with {model}")
            return
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            last_err = f"{model}: HTTP {e.code} {detail}"
            if e.code not in (400, 404):   # bad model id -> try the next; other errors will not improve
                break
        except Exception as e:
            last_err = f"{model}: {type(e).__name__}: {e}"
            break
    record_status(f"assessment failed: {last_err}"[:300])
    print("Assessment failed:", last_err)


if __name__ == "__main__":
    main()
