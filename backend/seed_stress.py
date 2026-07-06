"""
seed_stress.py — realistic scale-test org for Nexus.
====================================================
ADDITIVE (never wipes existing rows): 7 teams each with a promoted TEAM LEAD,
50 employees, 8 projects of different shapes, ~150 unevenly-distributed tasks
(overdue / due-soon / overloaded people), dependencies, subtasks, comments,
active contracts WITH baseline snapshots (2 deliberately drifted), goals,
meetings, peer requests, escalations, and per-project chat channels.

Everyone's password is demo123. Run against any DATABASE_URL:
    python seed_stress.py            # local .env database
    DATABASE_URL=... python seed_stress.py   # e.g. the cloud instance
"""
import random
import sys
from datetime import date, datetime, timedelta, timezone

from database.core import SessionLocal
from database.models import (
    Employee, Team, Project, Task, Subtask, TaskComment, TaskDependency,
    Contract, Goal, GoalTask, Meeting, PeerRequest, Escalation,
    Channel, ChannelMember, Message,
)
from api.security import hash_password
from resolution_engine import producer_content

R = random.Random(42)
CO = 1
TODAY = date.today()
NOW = datetime.now(timezone.utc)

TEAMS = {
    "Platform Engineering": ["Backend Engineer", "Infra Engineer", "API Engineer", "SRE"],
    "Mobile Development":   ["iOS Engineer", "Android Engineer", "Mobile QA"],
    "Product Design":       ["Product Designer", "UX Researcher", "Design Systems Lead"],
    "Growth Marketing":     ["Content Marketer", "Performance Marketer", "SEO Specialist"],
    "Enterprise Sales":     ["Account Executive", "Sales Engineer", "SDR"],
    "Data & Analytics":     ["Data Engineer", "Analytics Engineer", "Data Scientist"],
    "Customer Success":     ["CS Manager", "Support Engineer", "Onboarding Specialist"],
}

NAMES = [
    "Amara Okafor", "Boris Ivanov", "Carmen Reyes", "Deepak Sharma", "Elena Petrova",
    "Farid Hassan", "Grace Liu", "Hugo Almeida", "Ingrid Larsen", "Jamal Wright",
    "Keiko Tanaka", "Lars Nielsen", "Maria Santos", "Nikhil Rao", "Olga Sokolova",
    "Pablo Herrera", "Qing Zhao", "Rania Khalil", "Stefan Weber", "Tara Singh",
    "Umar Farouk", "Valentina Rossi", "Wei Chen", "Ximena Torres", "Yusuf Demir",
    "Zoe Papadopoulos", "Andre Baptiste", "Bianca Moretti", "Callum Fraser", "Dalia Mansour",
    "Emil Johansson", "Fatima Zahra", "Gustav Lindqvist", "Hana Kobayashi", "Ivan Horvat",
    "Jasmine Patel", "Kofi Mensah", "Leila Nasser", "Marco Bianchi", "Nadia Volkova",
    "Oscar Duarte", "Priyanka Iyer", "Quentin Moreau", "Rosa Delgado", "Samir Gupta",
    "Thandiwe Ndlovu", "Ulrich Braun", "Vera Kowalska", "Wanjiru Kamau", "Yara Haddad",
]

SKILLS = {
    "Platform Engineering": "python, postgres, kubernetes, apis",
    "Mobile Development":   "swift, kotlin, react-native, testing",
    "Product Design":       "figma, prototyping, user research",
    "Growth Marketing":     "seo, copywriting, paid ads, analytics",
    "Enterprise Sales":     "negotiation, demos, crm",
    "Data & Analytics":     "sql, dbt, python, dashboards",
    "Customer Success":     "onboarding, support, communication",
}

# project → (teams that staff it, task templates as (title, desc))
PROJECTS = [
    ("Mobile App v2", ["Mobile Development", "Product Design", "Platform Engineering"], "High", 25, [
        ("Design v2 navigation", "Figma flows for the new tab navigation; hands the component specs to the mobile build."),
        ("Build offline sync engine", "Local queue + conflict resolution; exposes a sync status API the UI reads."),
        ("Push notification service", "Backend service producing the notification payloads the apps consume."),
        ("iOS payments screen", "Implements the checkout screen consuming the payments API JSON."),
        ("Android payments screen", "Checkout on Android consuming the same payments API JSON."),
        ("App-wide dark mode", "Apply the design tokens from the design system across screens."),
        ("Crash reporting integration", "Wire the crash SDK and symbol upload into both apps."),
        ("Beta feedback triage", "Collect and label beta tester feedback for the team."),
    ]),
    ("Website Redesign", ["Product Design", "Growth Marketing", "Platform Engineering"], "High", 30, [
        ("New homepage design", "Hero, social proof, and pricing sections in Figma; developers build from these specs."),
        ("Build homepage", "Implement the homepage from the approved Figma specs."),
        ("CMS migration", "Move blog content into the new CMS; produces the content API the site reads."),
        ("SEO technical audit", "Crawl, fix metadata, and hand the redirect map to the build."),
        ("Pricing page copy", "Final copy for the three plan tiers shown on the site."),
        ("Analytics events schema", "Define the event names + properties the site emits and dashboards consume."),
    ]),
    ("Data Warehouse Migration", ["Data & Analytics", "Platform Engineering"], "Critical", 35, [
        ("Provision Snowflake", "Stand up the warehouse, roles, and databases the pipelines load into."),
        ("Ingestion pipelines", "Move the 12 source connectors; lands raw tables the models transform."),
        ("dbt model rewrite", "Rebuild core models on the new warehouse raw tables."),
        ("Dashboard cutover", "Repoint executive dashboards at the new marts."),
        ("Data quality checks", "Freshness + volume tests on the raw and mart layers."),
        ("Deprecate legacy warehouse", "Turn off the old cluster after cutover completes."),
    ]),
    ("Enterprise Sales Playbook", ["Enterprise Sales", "Growth Marketing"], "Medium", 28, [
        ("Discovery call script", "Question framework the AEs run in first calls."),
        ("ROI calculator", "Spreadsheet the sales engineers use in demos; marketing supplies the benchmark data."),
        ("Competitor battlecards", "One-pagers per competitor for the sales team."),
        ("Case study: Meridian Corp", "Write and design the flagship customer story used in outreach."),
        ("Outbound email sequences", "Five-touch sequences using the case study and battlecards."),
    ]),
    ("Customer Onboarding Revamp", ["Customer Success", "Product Design", "Platform Engineering"], "High", 26, [
        ("Onboarding journey map", "Research current drop-off points; produces the flow the redesign follows."),
        ("In-app checklist widget", "Build the guided checklist consuming the journey map milestones."),
        ("Welcome email series", "Lifecycle emails triggered by checklist progress events."),
        ("Health-score model", "Score accounts from product usage; CS consumes the score in their dashboard."),
        ("CS dashboard", "Internal view of accounts ranked by the health score."),
    ]),
    ("API v3 Platform", ["Platform Engineering", "Data & Analytics"], "Critical", 40, [
        ("v3 API spec", "OpenAPI spec for the public v3 endpoints; clients and docs are built from it."),
        ("Auth service rewrite", "OAuth2 + API keys service issuing the tokens every v3 endpoint validates."),
        ("Rate limiting layer", "Per-key quotas enforced at the gateway."),
        ("v3 SDK (Python)", "Generated client from the v3 spec with typed models."),
        ("Usage metering events", "Emit per-call events the billing pipeline consumes."),
        ("Developer docs portal", "Reference docs generated from the v3 spec."),
    ]),
    ("Q3 Launch Campaign", ["Growth Marketing", "Product Design", "Enterprise Sales"], "High", 20, [
        ("Launch messaging framework", "Positioning + headlines all launch assets are written from."),
        ("Launch visuals", "Key art and social banners following the messaging framework."),
        ("Press release", "Announcement draft using the messaging framework."),
        ("Webinar deck", "Launch webinar slides sales presents from."),
        ("Paid ads flight", "LinkedIn + search ads using the launch visuals."),
    ]),
    ("SOC2 Compliance Audit", ["Platform Engineering", "Data & Analytics", "Customer Success"], "Medium", 45, [
        ("Access review", "Quarterly review of who can touch production and customer data."),
        ("Audit log coverage", "Ensure every privileged action lands in the audit trail the auditors sample."),
        ("Vendor inventory", "Catalog subprocessors with data categories for the report."),
        ("Incident response drill", "Tabletop exercise; produces the findings the policy update consumes."),
        ("Policy updates", "Refresh security policies from the drill findings."),
    ]),
]


def main():
    db = SessionLocal()
    try:
        if db.query(Team).filter(Team.name == "Platform Engineering").first() and "--force" not in sys.argv:
            print("Stress data already present (Platform Engineering team exists). Use --force to add anyway.")
            return

        pw = hash_password("demo123")   # hash ONCE (bcrypt is slow), reuse for all
        summary = {}

        # ── teams + 50 employees + leads ──────────────────────────
        teams, members_of = {}, {}
        name_iter = iter(NAMES)
        for tname, roles in TEAMS.items():
            team = db.query(Team).filter(Team.company_id == CO, Team.name.ilike(tname)).first()
            if not team:
                team = Team(company_id=CO, name=tname, description=f"{tname} team")
                db.add(team)
                db.flush()
            teams[tname] = team
            members_of[tname] = []

        per_team = [8, 7, 7, 7, 7, 7, 7]   # = 50
        for (tname, roles), count in zip(TEAMS.items(), per_team):
            for i in range(count):
                nm = next(name_iter)
                emp = Employee(
                    company_id=CO, name=nm, role=roles[i % len(roles)],
                    team=tname, team_id=teams[tname].id,
                    system_role="employee", is_active=True,
                    age=R.randint(24, 52), experience=R.randint(1, 18),
                    skills=SKILLS[tname], password_hash=pw,
                )
                db.add(emp)
                members_of[tname].append(emp)
        db.flush()

        # first member of each team becomes its LEAD (real promotion: role + lead_id)
        leads = {}
        for tname in TEAMS:
            lead = members_of[tname][0]
            lead.system_role = "team_lead"
            teams[tname].lead_id = lead.id
            leads[tname] = lead
        db.commit()
        summary["teams"] = len(teams)
        summary["employees"] = sum(len(v) for v in members_of.values())
        summary["team_leads"] = len(leads)

        # absorb any pre-existing label-only members into matching teams
        for tname, team in teams.items():
            for e in db.query(Employee).filter(
                    Employee.team.ilike(tname), Employee.team_id.is_(None),
                    Employee.system_role != "manager").all():
                e.team_id = team.id
                members_of[tname].append(e)
        db.commit()

        # ── projects + tasks (uneven, with overdue / due-soon) ────
        all_tasks, proj_rows = [], []
        overloaded = [members_of["Platform Engineering"][1],
                      members_of["Growth Marketing"][1],
                      members_of["Data & Analytics"][1]]
        for pname, staffed, prio, due_in, templates in PROJECTS:
            proj = Project(company_id=CO, name=pname, priority=prio, status="active",
                           description=f"{pname} — cross-team initiative.",
                           due_date=TODAY + timedelta(days=due_in))
            db.add(proj)
            db.flush()
            proj_rows.append(proj)
            pool = [m for t in staffed for m in members_of[t]]
            ptasks = []
            for j, (title, desc) in enumerate(templates * 3):   # ~15-24 tasks/project
                if j >= len(templates) * 2 + R.randint(0, len(templates)):
                    break
                owner = R.choice(overloaded) if R.random() < 0.18 else R.choice(pool)
                done = R.random() < 0.35
                r = R.random()
                if done:
                    due = TODAY - timedelta(days=R.randint(1, 20))
                elif r < 0.15:
                    due = TODAY - timedelta(days=R.randint(2, 14))      # overdue
                elif r < 0.40:
                    due = TODAY + timedelta(days=R.randint(1, 7))       # due soon
                elif r < 0.90:
                    due = TODAY + timedelta(days=R.randint(8, 30))
                else:
                    due = None
                suffix = "" if j < len(templates) else f" (phase {j // len(templates) + 1})"
                t = Task(company_id=CO, title=title + suffix, description=desc,
                         owner_id=owner.id, project_id=proj.id,
                         priority=R.choice(["Low", "Medium", "High", "High", "Critical"]),
                         is_completed=done, due_date=due)
                db.add(t)
                ptasks.append(t)
            db.flush()
            all_tasks.extend(ptasks)

            # dependencies: chain the first few template tasks (natural produce→consume order)
            for a, b in zip(ptasks, ptasks[1:len(templates)]):
                db.add(TaskDependency(task_id=b.id, depends_on_id=a.id))
        db.commit()
        summary["projects"] = len(proj_rows)
        summary["tasks"] = len(all_tasks)

        # ── subtasks + comments ───────────────────────────────────
        n_sub = n_com = 0
        for t in R.sample(all_tasks, 30):
            for k in range(R.randint(2, 4)):
                db.add(Subtask(task_id=t.id, title=f"Step {k + 1} of {t.title[:40]}",
                               is_completed=R.random() < 0.4))
                n_sub += 1
        for t in R.sample(all_tasks, 35):
            author = t.owner_id
            db.add(TaskComment(task_id=t.id, author_id=author, is_ai_generated=False,
                               content=R.choice([
                                   "Blocked until the upstream piece lands — see dependency.",
                                   "Halfway done, on track for the due date.",
                                   "Scope grew a bit; may need another day.",
                                   "Reviewed with the team, proceeding as planned.",
                               ])))
            n_com += 1
        db.commit()
        summary["subtasks"], summary["comments"] = n_sub, n_com

        # ── contracts with baselines (2 drifted on purpose) ───────
        n_con = 0
        drifted = 0
        for proj in proj_rows[:6]:
            pt = [t for t in all_tasks if t.project_id == proj.id]
            if len(pt) < 2:
                continue
            producer, consumer = pt[0], pt[1]
            c = Contract(company_id=CO, project_id=proj.id,
                         producer_task_id=producer.id, consumer_task_id=consumer.id,
                         name=f"{producer.title[:40]} → {consumer.title[:40]}",
                         description=f"{producer.title} hands its output to {consumer.title} as agreed.",
                         status="active", baseline_at=NOW,
                         baseline_snapshot=producer_content(producer))
            db.add(c)
            n_con += 1
            if drifted < 2:
                producer.description = (producer.description or "") + \
                    " CHANGED: the deliverable format is now different from what was agreed (breaking)."
                drifted += 1
        db.commit()
        summary["contracts"] = n_con

        # ── goals ─────────────────────────────────────────────────
        n_goal = 0
        for tname in list(TEAMS)[:5]:
            lead = leads[tname]
            g = Goal(company_id=CO, employee_id=lead.id,
                     title=f"{tname}: ship the quarter's flagship work",
                     description="Deliver the team's committed projects on time.",
                     progress_pct=R.choice([10.0, 25.0, 40.0, 60.0]),
                     status="active", target_date=TODAY + timedelta(days=45))
            db.add(g)
            db.flush()
            for t in R.sample([t for t in all_tasks if t.owner_id in
                               [m.id for m in members_of[tname]]] or all_tasks, 2):
                db.add(GoalTask(goal_id=g.id, task_id=t.id))
            n_goal += 1
        db.commit()
        summary["goals"] = n_goal

        # ── meetings (upcoming, with attendees) ───────────────────
        n_meet = 0
        for i, (tname, team) in enumerate(list(teams.items())):
            d = TODAY + timedelta(days=(i % 7) + 1)
            m = Meeting(company_id=CO, topic=f"{tname} weekly sync",
                        scheduled_time=f"{d.strftime('%b %d')} 10:00 AM",
                        scheduled_date=d, duration_minutes=30,
                        created_by=leads[tname].id, status="scheduled")
            m.attendees = [e for e in members_of[tname][:5]]
            db.add(m)
            n_meet += 1
        db.commit()
        summary["meetings"] = n_meet

        # ── peer requests + escalations ───────────────────────────
        states = ["Pending", "Pending", "Accepted", "Completed", "Declined"]
        n_peer = 0
        for tname in list(TEAMS)[:5]:
            ms = members_of[tname]
            # task_id is NOT NULL — find any open task owned by a teammate
            t = next((t for t in all_tasks
                      if t.owner_id in [m.id for m in ms] and not t.is_completed), None)
            if not t:
                continue
            db.add(PeerRequest(company_id=CO, task_id=t.id,
                               sender_id=t.owner_id,
                               recipient_id=next(m.id for m in ms if m.id != t.owner_id),
                               topic=f"Need a hand with {t.title[:40]}",
                               status=states[n_peer % len(states)], ai_negotiated=False))
            n_peer += 1
        n_esc = 0
        for tname in list(TEAMS)[:4]:
            m = members_of[tname][2 % len(members_of[tname])]
            db.add(Escalation(company_id=CO, from_agent_id=f"Employee_{m.id}",
                              to_agent_id="Manager_1", status="pending",
                              reason=f"{m.name} is blocked on {tname}'s critical-path work and needs a decision."))
            n_esc += 1
        db.commit()
        summary["peer_requests"], summary["escalations"] = n_peer, n_esc

        # ── project channels + a little chat ──────────────────────
        n_chan = n_msg = 0
        for proj in proj_rows:
            ch = db.query(Channel).filter(Channel.project_id == proj.id,
                                          Channel.type == "project").first()
            if not ch:
                ch = Channel(company_id=CO, name=proj.name, type="project",
                             project_id=proj.id, description=f"Team channel for {proj.name}")
                db.add(ch)
                db.flush()
                n_chan += 1
            owner_ids = {t.owner_id for t in all_tasks if t.project_id == proj.id}
            for oid in owner_ids:
                if not db.query(ChannelMember).filter(ChannelMember.channel_id == ch.id,
                                                      ChannelMember.employee_id == oid).first():
                    db.add(ChannelMember(channel_id=ch.id, employee_id=oid))
            some = list(owner_ids)[:3]
            for k, oid in enumerate(some):
                db.add(Message(channel_id=ch.id, sender_id=oid, message_type="text",
                               content=R.choice([
                                   "Kicking off my piece today — will post progress here.",
                                   "Heads up: my part may slip a day, dependency landed late.",
                                   "Done with the first pass, review welcome.",
                               ])))
                n_msg += 1
        db.commit()
        summary["channels"], summary["messages"] = n_chan, n_msg

        print("✅ Stress org seeded (additive):")
        for k, v in summary.items():
            print(f"   {k:14s} {v}")
        print("   password for everyone: demo123")
    finally:
        db.close()


if __name__ == "__main__":
    main()
