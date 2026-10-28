"""
The bell: everything that needs Tal today, across the system. Nothing is
stored - items appear and disappear by themselves as things are handled
(task marked Done, date passed, deal closed).

Included (Tal's list, 2026-10-28): deal tasks due today or late, today's
walkthroughs, inspection periods ending today/tomorrow, closings
today/tomorrow, and open tasks in today's group on Tools › Tasks.
"""

from datetime import timedelta
from typing import List

from sqlalchemy.orm import Session

from app.finance import contract_clock, due_tasks, local_today
from app.models import DealBuyer, Task, WholesaleDeal

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # date.weekday() order
STATUS_ICON = {"waiting": "⏳", "confirmed": "✓", "visited": "✓✓", "cant": "✕"}


def _street(address: str) -> str:
    return (address or "").split(",")[0]


def _when(day, today) -> str:
    return "today" if day == today else "tomorrow"


def today_items(db: Session) -> List[dict]:
    """Each item: kind, title, detail, url, urgent (red) - in display order."""
    today = local_today()
    tomorrow = today + timedelta(days=1)
    items: List[dict] = []
    under_contract = db.query(WholesaleDeal).filter(WholesaleDeal.status == "under_contract").all()

    # Deadlines first: inspections and closings.
    for deal in under_contract:
        clock = contract_clock(deal, today)
        if clock.inspection_end in (today, tomorrow):
            items.append({"kind": "Inspection", "title": f"{_street(deal.address)}: inspection ends {_when(clock.inspection_end, today)}",
                          "detail": clock.inspection_end.strftime("%a, %b %d"), "url": f"/finance/deals/{deal.id}",
                          "urgent": clock.inspection_end == today})
        if deal.closed_date in (today, tomorrow):
            items.append({"kind": "Closing", "title": f"{_street(deal.address)}: closing {_when(deal.closed_date, today)}",
                          "detail": f"Buyer: {deal.buyer_name}" if deal.buyer_name else deal.closed_date.strftime("%a, %b %d"),
                          "url": f"/finance/deals/{deal.id}", "urgent": deal.closed_date == today})

    # Today's walkthroughs, one line per deal and time.
    walk = {}
    deal_ids = [d.id for d in under_contract]
    if deal_ids:
        for b in db.query(DealBuyer).filter(DealBuyer.deal_id.in_(deal_ids), DealBuyer.visit_at.isnot(None)).all():
            if b.visit_at.date() == today and b.status != "cant":
                walk.setdefault((b.deal_id, b.visit_at), []).append(b)
    for (deal_id, at), buyers in sorted(walk.items(), key=lambda kv: kv[0][1]):
        deal = buyers[0].deal
        items.append({"kind": "Walkthrough", "title": f"{_street(deal.address)}: {at.strftime('%-I:%M %p')}",
                      "detail": ", ".join(f"{b.name} {STATUS_ICON.get(b.status, '')}" for b in buyers),
                      "url": f"/finance/deals/{deal_id}#buyers-card", "urgent": False})

    # Deal tasks due today or late.
    for t in due_tasks(db):
        late = (today - t.due_date).days
        items.append({"kind": "Deal task", "title": t.title,
                      "detail": f"{_street(t.deal.address)} · " + ("due today" if late == 0 else f"{late} day{'s' if late != 1 else ''} late"),
                      "url": f"/finance/deals/{t.deal_id}#tasks-card", "urgent": late > 0})

    # Open tasks in today's group on the Tasks board.
    day_key = WEEKDAY_KEYS[today.weekday()]
    for t in db.query(Task).filter(Task.day == day_key).order_by(Task.position, Task.id).all():
        if t.status and t.status.is_done:
            continue
        items.append({"kind": "Task", "title": t.title,
                      "detail": (t.label.name + " · " if t.label else "") + (t.status.name if t.status else "Today"),
                      "url": "/tools/tasks", "urgent": False})
    return items
