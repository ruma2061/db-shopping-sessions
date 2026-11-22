"""Dependency-free native integration and randomized differential tests."""
import csv
import datetime as dt
import io
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

DAY = 86400000
WEEK = 7 * DAY
START = 1790812800000  # 2026-10-01 UTC
EXE = str(Path(os.environ.get("SESSIONS_CORE", "sessions-core.exe")).resolve())


def iso(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerows(rows)
    return stream.getvalue()


def execute(events, clicks=(), links=(), raw_events="", raw_clicks="", raw_links=""):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for folder, header, rows, raw in [
            ("events", "event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents", events, raw_events),
            ("clicks", "click_id,user_id,campaign_id,ts", clicks, raw_clicks),
            ("links", "user_id_a,user_id_b", links, raw_links),
        ]:
            (root / folder).mkdir()
            # Spread input across files, including a header-only file.
            for index, part in enumerate((rows[::2], rows[1::2], [])):
                text = header + "\n" + encode(part) + (raw if index == 0 else "")
                (root / folder / f"{index}.csv").write_bytes(text.encode())
        (root / "products.csv").write_bytes(b"product_id,category\np,a\nq,b\n")
        (root / "campaigns.csv").write_bytes(b"campaign_id,channel\nc1,email\nc2,search\n")
        subprocess.run([EXE, str(root), str(root / "out"), iso(START), iso(START + 14 * DAY)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result = []
        for table in ("user_daily", "campaign_daily"):
            with (root / "out" / (table + ".csv")).open(encoding="utf-8", newline="") as f:
                result.append(list(csv.DictReader(f)))
        return result


def event(id, time, user="u", kind="view", product="p", price="", ingest=None):
    return [id, user, str(time), iso(time if ingest is None else ingest), kind, product, price]


class CoreTests(unittest.TestCase):
    def test_dedup_invalid_earliest_and_malformed(self):
        rows = [event("a", START), event("a", START, ingest=START + 1000), event("b", START)]
        rows[0][2] = "bad"
        users, _ = execute(rows, raw_events='b,u,broken,"stray,view,p,\n')
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["events"], "1")

    def test_dates_prices_and_cr(self):
        valid = ["2026-10-01T00:00:00Z", "2026-09-30T20:00:00-04:00", "000" + str(START)]
        invalid = ["2026-02-30T00:00:00Z", "2026-10-01t00:00:00Z", "2026-10-01T24:00:00Z",
                   "2026-10-01T00:00:00+24:00", "2026-10-01T00:00:00+05:60",
                   "2026-10-01T00:00:00Z\r", "9" * 100, str(START) + ".0", "\u0662\u0665\u0660", ""]
        rows = []
        for i, stamp in enumerate(valid + invalid):
            r = event(str(i), START); r[2] = stamp; rows.append(r)
        for i, price in enumerate(["0250", "10000000", "10000001", "0", "1000\r", "+1", "\uff12\uff15\uff10"]):
            rows.append(event("price" + str(i), START, kind="purchase", price=price))
        for row in rows:
            with self.subTest(event_id=row[0], timestamp=row[2], price=row[6]):
                single, _ = execute([row])
                expected = row[0] in {"0", "1", "2", "price0", "price1"}
                self.assertEqual(sum(int(r["events"]) for r in single), int(expected))
        users, _ = execute(rows)
        self.assertEqual(users[0]["events"], "5")
        self.assertEqual(users[0]["revenue_cents"], "10000250")

    def test_identity_session_boundaries_and_midnight(self):
        t = START + DAY - 60000
        rows = [event("a", t, "z"), event("b", t + 1800000, "y", "purchase", "q", "100"),
                event("c", t + 3600001, "z", "purchase", "missing", "100")]
        users, campaigns = execute(rows, links=[["z", "y"], ["y", "A"], ["A", "A"]])
        self.assertEqual([r["user_id"] for r in users], ["A", "A"])
        self.assertEqual([r["events"] for r in users], ["2", "1"])
        self.assertEqual([r["top_category"] for r in users], ["b", "unknown"])
        self.assertEqual(sum(int(r["sessions"]) for r in campaigns), 2)

    def test_attribution_ties_assist_only_and_boundaries(self):
        clicks = [["old", "u", "c1", str(START - WEEK)],
                  ["a", "u", "c1", str(START)], ["b", "u", "c2", str(START)],
                  ["future", "u", "c2", str(START + 1)]]
        _, result = execute([event("a", START, kind="purchase", price="100")], clicks)
        campaigns = {r["campaign_id"]: r for r in result}
        self.assertEqual(campaigns["c1"]["sessions"], "1")
        self.assertEqual(campaigns["c2"]["sessions"], "0")
        self.assertEqual(campaigns["c2"]["assisted_sessions"], "1")
        for distance, expected in [(WEEK, "c1"), (WEEK + 1, "organic"), (-1, "organic")]:
            _, result = execute([event("a", START)], [["c", "u", "c1", str(START-distance)]])
            self.assertEqual(result[0]["campaign_id"], expected)

    def test_lateness_and_category_tie(self):
        rows = [event("a", START, kind="purchase", product="q", price="5", ingest=START+DAY),
                event("b", START, kind="purchase", product="p", price="5", ingest=START-1000),
                event("c", START, ingest=START+DAY+1000)]
        users, _ = execute(rows)
        self.assertEqual(users[0]["events"], "2")
        self.assertEqual(users[0]["top_category"], "a")

    def test_spaces_unicode_and_long_chain(self):
        links = [[str(i), str(i+1)] for i in range(400)] + [["400", "\u00e9"], ["\u00e9", "Z"]]
        users, _ = execute([event("a", START, "\u00e9"), event("b", START, " u ")], links=links)
        self.assertEqual({r["user_id"] for r in users}, {"0", " u "})

    def test_empty(self):
        self.assertEqual(execute([]), [[], []])

    def test_randomized_against_brute_force(self):
        for seed in range(75):
            rng = random.Random(seed)
            events, clicks = [], []
            expected_users, expected_campaigns = {}, {}
            for person in range(4):
                user = f"u{person}"
                times = sorted(rng.randrange(0, 14 * DAY) + START for _ in range(35))
                raw = []
                for i, t in enumerate(times):
                    revenue = rng.choice([0, 0, 5, 10])
                    category = rng.choice(["a", "b"])
                    events.append(event(f"{user}-{i}", t, user, "purchase" if revenue else "view",
                                        "p" if category == "a" else "q", str(revenue)))
                    raw.append((t, revenue, category))
                local_clicks = []
                for i in range(30):
                    t = rng.choice(times) if i % 3 == 0 else rng.randrange(START-WEEK, START+14*DAY)
                    campaign = rng.choice(["c1", "c2"])
                    id = f"{user}-click-{i}"
                    clicks.append([id, user, campaign, str(t)])
                    local_clicks.append((t, id, campaign))
                groups = []
                for row in raw:
                    if not groups or row[0] - groups[-1][-1][0] > 1800000:
                        groups.append([])
                    groups[-1].append(row)
                for group in groups:
                    start = group[0][0]; day = start // DAY
                    key = (user, str(day))
                    u = expected_users.setdefault(key, [0, 0, 0, {}])
                    u[0] += 1; u[1] += len(group)
                    revenue = sum(r[1] for r in group); u[2] += revenue
                    for _, amount, category in group:
                        if amount: u[3][category] = u[3].get(category, 0) + amount
                    eligible = [c for c in local_clicks if start-WEEK <= c[0] <= start]
                    credited = sorted(eligible, key=lambda c: (-c[0], c[1]))[0][2] if eligible else "organic"
                    c = expected_campaigns.setdefault((credited, str(day)), [0, 0, 0, 0])
                    c[0] += 1; c[1] += bool(revenue); c[2] += revenue
                    for campaign in {c[2] for c in eligible}:
                        expected_campaigns.setdefault((campaign, str(day)), [0, 0, 0, 0])[3] += 1
            rng.shuffle(events); rng.shuffle(clicks)
            actual_users, actual_campaigns = execute(events, clicks)
            observed = {(r["user_id"], r["day"]): [int(r[k]) for k in ("sessions", "events", "revenue_cents")]
                        + [r["top_category"]] for r in actual_users}
            expected = {k: v[:3] + [min(v[3], key=lambda c: (-v[3][c], c)) if v[3] else ""]
                        for k, v in expected_users.items()}
            self.assertEqual(observed, expected, f"users seed={seed}")
            observed = {(r["campaign_id"], r["day"]): [int(r[k]) for k in
                        ("sessions", "purchasing_sessions", "revenue_cents", "assisted_sessions")]
                        for r in actual_campaigns}
            self.assertEqual(observed, expected_campaigns, f"campaigns seed={seed}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
