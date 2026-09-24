from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.finance import (
    CHANNELS,
    DEAL_STATUSES,
    LEAD_CHANNELS,
    WHOLESALE_CHANNELS,
    MonthSummary,
    build_month_summaries,
    build_totals,
    copy_recurring,
    get_or_create_vendor,
    next_month,
    parse_month_key,
    start_month,
)
from app.models import FinanceExpense, FinanceMonth, Vendor, WholesaleDeal, ZipCode


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _money(value: str) -> float:
    try:
        return float((value or "0").replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def _optional_money(value: str) -> Optional[float]:
    return _money(value) if (value or "").strip() else None


def _int(value: str) -> int:
    try:
        return max(0, int(float(value or 0)))
    except ValueError:
        return 0


def _optional_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/finance")
    templates.env.globals["CHANNELS"] = CHANNELS
    templates.env.globals["DEAL_STATUSES"] = DEAL_STATUSES

    # ---------- Overview ----------

    @router.get("")
    def finance_overview(request: Request, db: Session = Depends(get_db)):
        summaries = build_month_summaries(db)
        totals = build_totals(db, summaries)
        return templates.TemplateResponse(
            "finance_overview.html",
            {
                "request": request,
                "active": "fin_overview",
                "summaries": list(summaries.values()),
                "totals": totals,
                "lead_channels": LEAD_CHANNELS,
                "wholesale_channels": WHOLESALE_CHANNELS,
            },
        )

    # ---------- Monthly expenses ----------

    def _month_context(request: Request, db: Session, month: date) -> dict:
        summaries = build_month_summaries(db)
        summary = summaries.get(month) or MonthSummary(month=month)
        fm = summary.finance_month
        expenses = sorted(fm.expenses, key=lambda e: (e.channel == "general", -float(e.amount or 0))) if fm else []
        all_months = sorted({m for m in summaries} | {month}, reverse=True)
        latest = max((m for m, s in summaries.items() if s.finance_month), default=None)
        return {
            "request": request,
            "active": "fin_month",
            "month": month,
            "key": month.strftime("%Y-%m"),
            "summary": summary,
            "fm": fm,
            "wholesale_expenses": [e for e in expenses if e.channel != "general"],
            "general_expenses": [e for e in expenses if e.channel == "general"],
            "vendors": db.query(Vendor).order_by(Vendor.name).all(),
            "all_months": all_months,
            "next_new_month": next_month(latest) if latest else date.today().replace(day=1),
            "lead_channels": LEAD_CHANNELS,
            "wholesale_channels": WHOLESALE_CHANNELS,
        }

    def _month_body(request: Request, db: Session, month: date):
        if _is_htmx(request):
            return templates.TemplateResponse("_finance_month_body.html", _month_context(request, db, month))
        return RedirectResponse(f"/finance/month/{month:%Y-%m}", status_code=303)

    @router.get("/month")
    def finance_month_latest(db: Session = Depends(get_db)):
        latest = db.query(FinanceMonth).order_by(FinanceMonth.month.desc()).first()
        key = (latest.month if latest else date.today()).strftime("%Y-%m")
        return RedirectResponse(f"/finance/month/{key}", status_code=303)

    @router.get("/month/{key}")
    def finance_month(request: Request, key: str, db: Session = Depends(get_db)):
        month = parse_month_key(key)
        if not month:
            return RedirectResponse("/finance/month", status_code=303)
        return templates.TemplateResponse("finance_month.html", _month_context(request, db, month))

    @router.post("/month/start")
    def finance_month_start(month: str = Form(...), db: Session = Depends(get_db)):
        m = parse_month_key(month)
        if not m:
            return RedirectResponse("/finance/month", status_code=303)
        start_month(db, m)
        db.commit()
        return RedirectResponse(f"/finance/month/{m:%Y-%m}", status_code=303)

    @router.post("/month/{key}/copy-recurring")
    def finance_month_copy_recurring(request: Request, key: str, db: Session = Depends(get_db)):
        month = parse_month_key(key)
        fm = db.query(FinanceMonth).filter(FinanceMonth.month == month).first()
        if fm:
            copy_recurring(db, fm)
            db.commit()
            db.refresh(fm)
        return _month_body(request, db, month)

    @router.post("/month/{key}/leads")
    def finance_month_leads(
        request: Request,
        key: str,
        sms_leads: str = Form("0"),
        sms_follow_up_leads: str = Form("0"),
        cold_call_leads: str = Form("0"),
        facebook_leads: str = Form("0"),
        db: Session = Depends(get_db),
    ):
        month = parse_month_key(key)
        fm = db.query(FinanceMonth).filter(FinanceMonth.month == month).first()
        if fm:
            fm.sms_leads = _int(sms_leads)
            fm.sms_follow_up_leads = _int(sms_follow_up_leads)
            fm.cold_call_leads = _int(cold_call_leads)
            fm.facebook_leads = _int(facebook_leads)
            db.commit()
        return _month_body(request, db, month)

    @router.post("/month/{key}/expenses/new")
    def finance_expense_new(
        request: Request,
        key: str,
        vendor_name: str = Form(""),
        amount: str = Form("0"),
        channel: str = Form(""),
        description: str = Form(""),
        db: Session = Depends(get_db),
    ):
        month = parse_month_key(key)
        fm = db.query(FinanceMonth).filter(FinanceMonth.month == month).first()
        if fm and vendor_name.strip():
            vendor = get_or_create_vendor(db, vendor_name, channel)
            db.add(
                FinanceExpense(
                    finance_month_id=fm.id,
                    vendor_id=vendor.id,
                    amount=_money(amount),
                    channel=channel if channel in CHANNELS else vendor.default_channel,
                    description=description.strip() or None,
                )
            )
            db.commit()
        return _month_body(request, db, month)

    @router.post("/expenses/{expense_id}/update")
    def finance_expense_update(
        request: Request,
        expense_id: int,
        vendor_name: str = Form(""),
        amount: str = Form("0"),
        channel: str = Form(""),
        description: str = Form(""),
        db: Session = Depends(get_db),
    ):
        expense = db.get(FinanceExpense, expense_id)
        if not expense:
            return RedirectResponse("/finance/month", status_code=303)
        month = expense.finance_month.month
        if vendor_name.strip():
            expense.vendor_id = get_or_create_vendor(db, vendor_name, channel).id
        expense.amount = _money(amount)
        if channel in CHANNELS:
            expense.channel = channel
        expense.description = description.strip() or None
        db.commit()
        return _month_body(request, db, month)

    @router.post("/expenses/{expense_id}/delete")
    def finance_expense_delete(request: Request, expense_id: int, db: Session = Depends(get_db)):
        expense = db.get(FinanceExpense, expense_id)
        if not expense:
            return RedirectResponse("/finance/month", status_code=303)
        month = expense.finance_month.month
        db.delete(expense)
        db.commit()
        return _month_body(request, db, month)

    # ---------- Deals ----------

    @router.get("/deals")
    def finance_deals(request: Request, db: Session = Depends(get_db)):
        deals = db.query(WholesaleDeal).all()
        deals.sort(key=lambda d: (d.agreement_date or date.min), reverse=True)
        summaries = build_month_summaries(db)
        totals = build_totals(db, summaries)
        return templates.TemplateResponse(
            "finance_deals.html",
            {"request": request, "active": "fin_deals", "deals": deals, "totals": totals, "today": date.today().isoformat()},
        )

    @router.post("/deals/new")
    def finance_deal_new(
        address: str = Form(...),
        agreement_date: str = Form(""),
        agreement_price: str = Form(""),
        db: Session = Depends(get_db),
    ):
        deal = WholesaleDeal(
            address=address.strip(),
            agreement_date=_optional_date(agreement_date),
            agreement_price=_optional_money(agreement_price),
            status="under_contract",
        )
        db.add(deal)
        db.commit()
        return RedirectResponse(f"/finance/deals/{deal.id}", status_code=303)

    @router.get("/deals/{deal_id}")
    def finance_deal_detail(request: Request, deal_id: int, db: Session = Depends(get_db)):
        deal = db.get(WholesaleDeal, deal_id)
        if not deal:
            return RedirectResponse("/finance/deals", status_code=303)
        zip_codes = db.query(ZipCode).order_by(ZipCode.zip_code).all()
        return templates.TemplateResponse(
            "finance_deal_detail.html",
            {"request": request, "active": "fin_deals", "deal": deal, "zip_codes": zip_codes, "lead_channels": LEAD_CHANNELS},
        )

    @router.post("/deals/{deal_id}/update")
    def finance_deal_update(
        deal_id: int,
        address: str = Form(...),
        agreement_date: str = Form(""),
        agreement_price: str = Form(""),
        sell_price: str = Form(""),
        status: str = Form("under_contract"),
        closed_date: str = Form(""),
        revenue: str = Form(""),
        realtor_commission: str = Form(""),
        acquisition_commission: str = Form(""),
        closing_costs: str = Form(""),
        additional_costs: str = Form(""),
        estimated_value: str = Form(""),
        channel: str = Form(""),
        zip_code_id: str = Form(""),
        notes: str = Form(""),
        db: Session = Depends(get_db),
    ):
        deal = db.get(WholesaleDeal, deal_id)
        if deal:
            deal.address = address.strip()
            deal.agreement_date = _optional_date(agreement_date)
            deal.agreement_price = _optional_money(agreement_price)
            deal.sell_price = _optional_money(sell_price)
            deal.status = status if status in DEAL_STATUSES else "under_contract"
            deal.closed_date = _optional_date(closed_date)
            deal.revenue = _money(revenue)
            deal.realtor_commission = _money(realtor_commission)
            deal.acquisition_commission = _money(acquisition_commission)
            deal.closing_costs = _money(closing_costs)
            deal.additional_costs = _money(additional_costs)
            deal.estimated_value = _optional_money(estimated_value)
            deal.channel = channel if channel in CHANNELS else None
            deal.zip_code_id = int(zip_code_id) if zip_code_id.isdigit() else None
            deal.notes = notes.strip() or None
            db.commit()
        return RedirectResponse("/finance/deals", status_code=303)

    @router.post("/deals/{deal_id}/delete")
    def finance_deal_delete(deal_id: int, db: Session = Depends(get_db)):
        deal = db.get(WholesaleDeal, deal_id)
        if deal:
            db.delete(deal)
            db.commit()
        return RedirectResponse("/finance/deals", status_code=303)

    # ---------- Vendors ----------

    def _vendor_stats(db: Session) -> dict:
        stats = {}
        for e in db.query(FinanceExpense).join(FinanceMonth).all():
            s = stats.setdefault(e.vendor_id, {"count": 0, "total": 0.0, "last_month": None, "last_amount": None})
            s["count"] += 1
            s["total"] += float(e.amount or 0)
            m = e.finance_month.month
            if s["last_month"] is None or m >= s["last_month"]:
                s["last_month"], s["last_amount"] = m, float(e.amount or 0)
        return stats

    @router.get("/vendors")
    def finance_vendors(request: Request, db: Session = Depends(get_db)):
        vendors = db.query(Vendor).order_by(Vendor.name).all()
        return templates.TemplateResponse(
            "finance_vendors.html",
            {"request": request, "active": "fin_vendors", "vendors": vendors, "stats": _vendor_stats(db)},
        )

    @router.post("/vendors/new")
    def finance_vendor_new(
        name: str = Form(...),
        default_channel: str = Form("shared"),
        recurring: Optional[str] = Form(None),
        db: Session = Depends(get_db),
    ):
        if name.strip():
            vendor = get_or_create_vendor(db, name, default_channel)
            vendor.recurring = bool(recurring)
            db.commit()
        return RedirectResponse("/finance/vendors", status_code=303)

    @router.post("/vendors/{vendor_id}/update")
    def finance_vendor_update(
        request: Request,
        vendor_id: int,
        name: str = Form(""),
        default_channel: str = Form("shared"),
        recurring: Optional[str] = Form(None),
        db: Session = Depends(get_db),
    ):
        vendor = db.get(Vendor, vendor_id)
        error = None
        if vendor:
            new_name = name.strip()
            clash = db.query(Vendor).filter(Vendor.name == new_name, Vendor.id != vendor_id).first()
            if new_name and not clash:
                vendor.name = new_name
            elif clash:
                error = f'"{new_name}" already exists'
            if default_channel in CHANNELS:
                vendor.default_channel = default_channel
            vendor.recurring = bool(recurring)
            db.commit()
        if _is_htmx(request) and vendor:
            return templates.TemplateResponse(
                "_finance_vendor_row.html",
                {"request": request, "v": vendor, "stats": _vendor_stats(db), "error": error},
            )
        return RedirectResponse("/finance/vendors", status_code=303)

    @router.post("/vendors/{vendor_id}/delete")
    def finance_vendor_delete(request: Request, vendor_id: int, db: Session = Depends(get_db)):
        vendor = db.get(Vendor, vendor_id)
        if vendor and not vendor.expenses:
            db.delete(vendor)
            db.commit()
            if _is_htmx(request):
                return Response(content="", media_type="text/html")
        return RedirectResponse("/finance/vendors", status_code=303)

    return router
