#!/usr/bin/env python3
"""
Synthetic IT service-desk ticketing data generator.

Outputs (in --out dir):
  users.csv          - people who raise tickets
  agents.csv         - support staff, by team and tier
  tickets.csv        - one row per ticket (clean)
  ticket_events.csv  - full status/assignment history per ticket
  tickets_raw.csv    - messy copy of tickets.csv for data-cleaning practice (--dirty)

Built-in patterns worth discovering:
  * weekday/hour seasonality (Monday spike, quiet weekends, 9-11am & 2-4pm peaks)
  * gentle month-over-month volume growth
  * two network outages (mass VPN/connectivity tickets linked by problem_id)
  * weekend P3/P4 tickets wait until Monday for a first response
  * agent skill varies -> some agents resolve much faster than others
  * escalations and reopens lengthen resolution and lower CSAT
  * VIP users get priority bumps
  * tickets near the end of the window are still open / pending

Usage:
  python generate_tickets.py --tickets 10000 --start 2025-01-01 --months 12 --seed 42 --dirty
"""
import argparse
import csv
import math
import os
import random
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"

# category -> (weight, team, median P3 resolution hours, {subcategory: [subjects]})
CATEGORIES = {
    "Hardware": (0.18, "Desktop Support", 14, {
        "Laptop": ["Laptop won't boot", "Laptop battery draining fast", "Laptop overheating", "Cracked laptop screen"],
        "Monitor": ["External monitor not detected", "Monitor flickering", "Need second monitor"],
        "Printer": ["Printer offline", "Paper jam on floor printer", "Cannot add network printer"],
        "Peripheral": ["Keyboard not working", "Headset mic not detected", "Docking station not charging"],
    }),
    "Software": (0.24, "Application Support", 12, {
        "Installation": ["Need software installed", "Install fails with error 1603", "Request IDE installation"],
        "License": ["License expired", "Need license for design tool", "Activation failed"],
        "Bug": ["Application crashes on startup", "Report export gives wrong totals", "App freezes when saving"],
        "Update": ["Update stuck at 0%", "App broken after latest update", "Need update rolled back"],
    }),
    "Network": (0.17, "Network Operations", 8, {
        "VPN": ["VPN won't connect", "VPN disconnects frequently", "VPN very slow"],
        "WiFi": ["Cannot connect to office WiFi", "WiFi drops in meeting room", "Weak WiFi signal at desk"],
        "Connectivity": ["No internet access", "Cannot reach internal site", "Network drive not accessible"],
        "DNS": ["Internal hostname not resolving", "Website resolves to wrong IP"],
    }),
    "Access": (0.26, "Identity & Access", 3, {
        "Password Reset": ["Forgot password", "Password expired", "MFA reset needed"],
        "Account Locked": ["Account locked out", "Locked after password change"],
        "Permission Request": ["Need access to shared folder", "Need admin rights", "Request access to reporting tool"],
        "New Account": ["New joiner account setup", "Contractor account request"],
    }),
    "Email": (0.15, "Messaging", 6, {
        "Outlook": ["Outlook not syncing", "Outlook keeps asking for password", "Calendar invites missing"],
        "Spam": ["Receiving phishing emails", "Legit emails going to junk"],
        "Mailbox Full": ["Mailbox full, cannot send", "Need mailbox quota increase"],
        "Distribution List": ["Add me to distribution list", "Create new distribution list"],
    }),
}

PRIORITIES = ["P1", "P2", "P3", "P4"]
PRIORITY_WEIGHTS = [0.03, 0.12, 0.45, 0.40]
SLA_RESPONSE_H = {"P1": 0.25, "P2": 1, "P3": 4, "P4": 8}
SLA_RESOLUTION_H = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}
PRIORITY_TIME_MULT = {"P1": 0.15, "P2": 0.35, "P3": 1.0, "P4": 1.6}

CHANNELS = ["Portal", "Email", "Phone", "Chat"]
CHANNEL_WEIGHTS = [0.45, 0.30, 0.15, 0.10]

DEPARTMENTS = ["Finance", "HR", "Engineering", "Sales", "Marketing", "Operations", "Legal", "Customer Support"]
DEPT_WEIGHTS = [0.10, 0.06, 0.28, 0.16, 0.08, 0.14, 0.04, 0.14]
LOCATIONS = ["Chennai", "Bengaluru", "Noida", "London", "New York", "Frankfurt"]
LOCATION_WEIGHTS = [0.25, 0.25, 0.20, 0.12, 0.10, 0.08]
ROLES = ["Analyst", "Engineer", "Manager", "Associate", "Director", "Intern"]
ROLE_WEIGHTS = [0.25, 0.30, 0.15, 0.20, 0.04, 0.06]

FIRST = ["Aarav", "Priya", "Rahul", "Ananya", "Vikram", "Sneha", "Arjun", "Divya", "Karthik", "Meera",
         "James", "Emma", "Liam", "Olivia", "Noah", "Sofia", "Lukas", "Hannah", "Daniel", "Chloe",
         "Rohan", "Kavya", "Siddharth", "Ishita", "Nikhil", "Lakshmi", "Omar", "Fatima", "Chen", "Yuki"]
LAST = ["Sharma", "Iyer", "Patel", "Reddy", "Nair", "Gupta", "Kumar", "Menon", "Rao", "Das",
        "Smith", "Brown", "Miller", "Wilson", "Taylor", "Schmidt", "Weber", "Clarke", "Lee", "Khan"]

WEEKDAY_WEIGHT = [1.35, 1.10, 1.05, 1.00, 0.85, 0.22, 0.15]  # Mon..Sun
HOUR_WEIGHT = [0.1, 0.05, 0.05, 0.05, 0.1, 0.2, 0.4, 0.9, 2.0, 3.2, 3.4, 2.8,
               1.8, 2.2, 2.9, 3.0, 2.4, 1.5, 0.8, 0.5, 0.35, 0.25, 0.2, 0.15]

DETAILS = ["Started this morning.", "Happening since yesterday.", "Blocking my work.",
           "Tried restarting, no luck.", "Colleagues have the same issue.", "Urgent - client meeting soon.",
           "Not urgent.", "Error screenshot attached.", ""]


def lognormal(median, sigma):
    return median * math.exp(random.gauss(0, sigma))


def make_users(n):
    users = []
    for i in range(1, n + 1):
        role = random.choices(ROLES, ROLE_WEIGHTS)[0]
        users.append({
            "user_id": f"U{i:05d}",
            "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
            "department": random.choices(DEPARTMENTS, DEPT_WEIGHTS)[0],
            "location": random.choices(LOCATIONS, LOCATION_WEIGHTS)[0],
            "role": role,
            "is_vip": int(role == "Director" or random.random() < 0.01),
        })
    return users


def make_agents(start):
    agents, idx = [], 1
    for cat, (_, team, _, _) in CATEGORIES.items():
        size = random.randint(7, 11)
        for k in range(size):
            tier = "L3" if k < 2 else ("L2" if k < 2 + size // 3 else "L1")
            hire = start - timedelta(days=random.randint(30, 3000))
            # skill factor: <1 faster, >1 slower; seniority helps a bit
            skill = random.uniform(0.65, 1.45) * {"L1": 1.1, "L2": 1.0, "L3": 0.85}[tier]
            agents.append({
                "agent_id": f"A{idx:03d}",
                "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
                "team": team,
                "tier": tier,
                "hire_date": hire.strftime("%Y-%m-%d"),
                "shift": random.choices(["Day", "Evening", "Night"], [0.7, 0.2, 0.1])[0],
                "_skill": skill,
            })
            idx += 1
    return agents


def sample_created(start, end, growth):
    days = (end - start).days
    day_w = []
    for d in range(days):
        dt = start + timedelta(days=d)
        trend = 1 + growth * d / max(days, 1)
        day_w.append(WEEKDAY_WEIGHT[dt.weekday()] * trend)
    return day_w


def first_business_time(t):
    """Push a weekend timestamp to Monday 09:00."""
    while t.weekday() >= 5:
        t = (t + timedelta(days=1)).replace(hour=9, minute=0, second=0)
    return t


def build_ticket(created, user, agents_by_team, outage=None):
    if outage:
        cat, sub = "Network", outage["subcategory"]
        priority = random.choices(["P2", "P3"], [0.8, 0.2])[0]
    else:
        cat = random.choices(list(CATEGORIES), [c[0] for c in CATEGORIES.values()])[0]
        sub = random.choice(list(CATEGORIES[cat][3]))
        priority = random.choices(PRIORITIES, PRIORITY_WEIGHTS)[0]
        if user["is_vip"] and priority in ("P3", "P4") and random.random() < 0.6:
            priority = PRIORITIES[PRIORITIES.index(priority) - 1]

    _, team, base_h, subs = CATEGORIES[cat]
    subject = random.choice(subs[sub])
    channel = "Phone" if priority == "P1" and random.random() < 0.6 else random.choices(CHANNELS, CHANNEL_WEIGHTS)[0]

    team_agents = agents_by_team[team]
    front = [a for a in team_agents if a["tier"] in ("L1", "L2")]
    l3 = [a for a in team_agents if a["tier"] == "L3"]
    agent = random.choice(front)

    # --- timeline -------------------------------------------------------
    resp_h = lognormal(SLA_RESPONSE_H[priority] * 0.45, 0.8)
    first_resp = created + timedelta(hours=resp_h)
    if priority in ("P3", "P4") and created.weekday() >= 5:
        first_resp = first_business_time(first_resp) + timedelta(minutes=random.randint(5, 180))

    escalated = random.random() < (0.25 if priority == "P1" else 0.20 if sub == "Bug" else 0.07)
    work_h = lognormal(base_h * PRIORITY_TIME_MULT[priority] * agent["_skill"], 0.85)
    if escalated:
        work_h *= random.uniform(1.6, 2.6)
    if outage:
        resolved = outage["end"] + timedelta(minutes=random.randint(5, 120))
        resolved = max(resolved, first_resp + timedelta(minutes=10))
    else:
        resolved = first_resp + timedelta(hours=work_h)

    events = [(created, "created", "", "New", user["user_id"], "")]
    assign_t = created + (first_resp - created) * random.uniform(0.1, 0.5)
    events.append((assign_t, "assigned", "New", "Open", "system", agent["agent_id"]))
    events.append((first_resp, "first_response", "Open", "In Progress", agent["agent_id"], ""))

    span = resolved - first_resp
    final_agent = agent
    if random.random() < 0.22 and not outage:
        p1 = first_resp + span * random.uniform(0.15, 0.4)
        p2 = p1 + span * random.uniform(0.1, 0.3)
        events.append((p1, "status_change", "In Progress", "Pending User", agent["agent_id"], ""))
        events.append((p2, "status_change", "Pending User", "In Progress", user["user_id"], ""))
    if escalated:
        final_agent = random.choice(l3)
        events.append((first_resp + span * random.uniform(0.5, 0.7), "escalated", "In Progress", "In Progress",
                       agent["agent_id"], final_agent["agent_id"]))
    events.append((resolved, "resolved", "In Progress", "Resolved", final_agent["agent_id"], ""))

    reopen_p = 0.05 + (0.06 if work_h < base_h * 0.3 else 0) + (0.04 if cat in ("Software", "Hardware") else 0)
    if random.random() < reopen_p and not outage:
        reopen_t = resolved + timedelta(hours=random.uniform(2, 60))
        resolved = reopen_t + timedelta(hours=lognormal(base_h * 0.5 * final_agent["_skill"], 0.7))
        events.append((reopen_t, "reopened", "Resolved", "In Progress", user["user_id"], ""))
        events.append((resolved, "resolved", "In Progress", "Resolved", final_agent["agent_id"], ""))
    events.append((resolved + timedelta(hours=random.uniform(24, 96)), "closed", "Resolved", "Closed", "system", ""))
    events.sort(key=lambda e: e[0])

    return {
        "created": created, "user": user, "cat": cat, "sub": sub, "priority": priority, "team": team,
        "subject": subject, "channel": channel, "events": events, "final_agent": final_agent,
        "problem_id": outage["problem_id"] if outage else "",
    }


def finalize(t, ticket_id, now):
    evs = [e for e in t["events"] if e[0] <= now]
    status = evs[-1][3]
    created = t["created"]
    first_resp = next((e[0] for e in evs if e[1] == "first_response"), None)
    resolved_all = [e[0] for e in evs if e[1] == "resolved"]
    resolved = resolved_all[-1] if resolved_all and status in ("Resolved", "Closed") else None
    closed = next((e[0] for e in evs if e[1] == "closed"), None)
    escalated = any(e[1] == "escalated" for e in evs)
    reopens = sum(e[1] == "reopened" for e in evs)
    # current assignee = last assignment/escalation target so far
    agent_id = ""
    for e in evs:
        if e[1] in ("assigned", "escalated"):
            agent_id = e[5]

    p = t["priority"]
    resp_breach = ((first_resp or now) - created).total_seconds() / 3600 > SLA_RESPONSE_H[p]
    res_breach = ((resolved or now) - created).total_seconds() / 3600 > SLA_RESOLUTION_H[p]

    csat = ""
    if status in ("Resolved", "Closed") and random.random() < 0.33:
        score = 4.5 - 1.2 * res_breach - 0.8 * reopens - 0.3 * escalated + random.gauss(0, 0.7)
        csat = int(min(5, max(1, round(score))))

    detail = random.choice(DETAILS)
    row = {
        "ticket_id": ticket_id,
        "created_at": created.strftime(FMT),
        "user_id": t["user"]["user_id"],
        "channel": t["channel"],
        "category": t["cat"],
        "subcategory": t["sub"],
        "priority": p,
        "assigned_team": t["team"],
        "assigned_agent_id": agent_id,
        "status": status,
        "subject": t["subject"],
        "description": f"{t['subject']}. {detail}".strip(),
        "first_response_at": first_resp.strftime(FMT) if first_resp else "",
        "resolved_at": resolved.strftime(FMT) if resolved else "",
        "closed_at": closed.strftime(FMT) if closed else "",
        "sla_response_hours": SLA_RESPONSE_H[p],
        "sla_resolution_hours": SLA_RESOLUTION_H[p],
        "response_sla_breached": int(resp_breach),
        "resolution_sla_breached": int(res_breach),
        "escalated": int(escalated),
        "reopen_count": reopens,
        "csat_score": csat,
        "problem_id": t["problem_id"],
    }
    event_rows = [{
        "ticket_id": ticket_id, "timestamp": e[0].strftime(FMT), "event_type": e[1],
        "from_status": e[2], "to_status": e[3], "actor": e[4], "assigned_to": e[5],
    } for e in evs]
    return row, event_rows


def dirty_copy(rows):
    out = []
    for r in rows:
        r = dict(r)
        x = random.random()
        if x < 0.03:
            r["category"] = random.choice([r["category"].lower(), r["category"].upper(), r["category"] + " "])
        if random.random() < 0.02:
            r["priority"] = random.choice(["", "High", "p" + r["priority"][1], "Critical" if r["priority"] == "P1" else "Low"])
        if random.random() < 0.01 and r["created_at"]:
            r["created_at"] = datetime.strptime(r["created_at"], FMT).strftime("%d/%m/%Y %H:%M")
        if random.random() < 0.02:
            r["assigned_agent_id"] = ""
        if random.random() < 0.02:
            r["subcategory"] = ""
        if random.random() < 0.004 and r["resolved_at"]:
            r["resolved_at"], r["created_at"] = r["created_at"], r["resolved_at"]
        if random.random() < 0.01 and r["csat_score"] != "":
            r["csat_score"] = random.choice(["0", "10", "N/A"])
        out.append(r)
        if random.random() < 0.015:
            out.append(dict(r))  # duplicate row
    random.shuffle(out)
    return out


def write_csv(path, rows, fields=None):
    fields = fields or [k for k in rows[0] if not k.startswith("_")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickets", type=int, default=10000, help="approx. number of regular tickets")
    ap.add_argument("--users", type=int, default=1500)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data")
    ap.add_argument("--dirty", action="store_true", help="also write tickets_raw.csv with data-quality issues")
    args = ap.parse_args()

    random.seed(args.seed)
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = start + timedelta(days=round(args.months * 30.44))
    now = end  # snapshot time: anything after this hasn't happened yet

    users = make_users(args.users)
    user_w = [1 / (i + 1) ** 0.6 for i in range(len(users))]  # some users raise far more tickets
    random.shuffle(user_w)
    agents = make_agents(start)
    agents_by_team = {}
    for a in agents:
        agents_by_team.setdefault(a["team"], []).append(a)

    day_w = sample_created(start, end, growth=0.25)
    raw = []
    for _ in range(args.tickets):
        d = random.choices(range(len(day_w)), day_w)[0]
        h = random.choices(range(24), HOUR_WEIGHT)[0]
        created = start + timedelta(days=d, hours=h, minutes=random.randint(0, 59), seconds=random.randint(0, 59))
        raw.append(build_ticket(created, random.choices(users, user_w)[0], agents_by_team))

    # outages
    weekdays = [d for d in range(len(day_w)) if (start + timedelta(days=d)).weekday() < 5]
    for i, d in enumerate(sorted(random.sample(weekdays, 2)), 1):
        o_start = start + timedelta(days=d, hours=random.randint(9, 13))
        outage = {"problem_id": f"PRB{i:04d}", "subcategory": random.choice(["VPN", "Connectivity"]),
                  "end": o_start + timedelta(hours=random.uniform(2.5, 5))}
        for _ in range(random.randint(70, 140)):
            created = o_start + timedelta(minutes=random.expovariate(1 / 40))
            if created < outage["end"]:
                raw.append(build_ticket(created, random.choices(users, user_w)[0], agents_by_team, outage))

    raw.sort(key=lambda t: t["created"])
    tickets, events = [], []
    for i, t in enumerate(raw, 1):
        row, evs = finalize(t, f"INC{i:07d}", now)
        tickets.append(row)
        events.extend(evs)
    for i, e in enumerate(events, 1):
        e["event_id"] = f"EV{i:08d}"

    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "users.csv"), users)
    write_csv(os.path.join(args.out, "agents.csv"), agents)
    write_csv(os.path.join(args.out, "tickets.csv"), tickets)
    write_csv(os.path.join(args.out, "ticket_events.csv"), events,
              ["event_id", "ticket_id", "timestamp", "event_type", "from_status", "to_status", "actor", "assigned_to"])
    if args.dirty:
        write_csv(os.path.join(args.out, "tickets_raw.csv"), dirty_copy(tickets))

    open_n = sum(t["status"] not in ("Resolved", "Closed") for t in tickets)
    print(f"Wrote {len(users)} users, {len(agents)} agents, {len(tickets)} tickets "
          f"({open_n} still open), {len(events)} events -> {os.path.abspath(args.out)}")
    print(f"Window: {start:%Y-%m-%d} to {end:%Y-%m-%d}")


if __name__ == "__main__":
    main()
