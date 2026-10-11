from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import UPLOAD_ROOT, get_db
from app.documents import save_uploads
from app.finance import CHANNELS, parse_month_key
from app.models import FinanceMonth, MatchRule, StatementImport, Vendor
from app.statements import (
    KIND_LABELS,
    _num,
    commit_import,
    create_import,
    implied_rate,
    usd_amount,
    suggest_pattern,
    suggest_vendor_name,
)

STATEMENTS_DIR = UPLOAD_ROOT / "statements"


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/finance")

    @router.get("/statements")
    def statements(request: Request, db: Session = Depends(get_db)):
        imports = db.query(StatementImport).order_by(StatementImport.uploaded_at.desc()).all()
        summary = {}
        for imp in imports:
            added = [c for c in imp.charges if c.decision == "vendor"]
            summary[imp.id] = {
                "charges": len(imp.charges),
                "added": len(added),
                "added_usd": sum(float(c.usd_amount or 0) for c in added),
                "new": sum(1 for c in imp.charges if c.decision == "new"),
            }
        return templates.TemplateResponse(
            "finance_statements.html",
            {"request": request, "active": "fin_statements", "imports": imports, "summary": summary,
             "rule_count": db.query(MatchRule).count(), "error": request.query_params.get("error")},
        )

    @router.post("/statements/upload")
    async def statements_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
        saved, too_big = await save_uploads(STATEMENTS_DIR, [file])
        if not saved:
            return RedirectResponse("/finance/statements?error=" + ("too_big" if too_big else "empty"), status_code=303)
        try:
            imp = create_import(db, file.filename, saved[0]["stored_name"], str(STATEMENTS_DIR / saved[0]["stored_name"]))
        except Exception:
            db.rollback()
            (STATEMENTS_DIR / saved[0]["stored_name"]).unlink(missing_ok=True)
            return RedirectResponse("/finance/statements?error=unreadable", status_code=303)
        if not imp.charges:
            db.rollback()
            return RedirectResponse("/finance/statements?error=no_rows", status_code=303)
        db.commit()
        return RedirectResponse(f"/finance/statements/{imp.id}", status_code=303)

    def _review(request: Request, db: Session, imp: StatementImport, errors=None, result=None, problem_ids=()):
        order = {"new": 0, "vendor": 1, "skip": 2}
        charges = sorted(imp.charges, key=lambda c: (c.id not in problem_ids, order.get(c.decision, 3), c.row_index))
        fm = db.query(FinanceMonth).filter(FinanceMonth.month == imp.statement_month).first()
        existing = [e for e in (fm.expenses if fm else []) if e.statement_import_id != imp.id]
        rules = {r.id: r for r in db.query(MatchRule).all()}
        return templates.TemplateResponse(
            "finance_statement_review.html",
            {
                "request": request,
                "active": "fin_statements",
                "imp": imp,
                "charges": charges,
                "counts": {k: sum(1 for c in imp.charges if c.decision == k) for k in ("new", "vendor", "skip")},
                "bank_rate": implied_rate([{"kind": c.kind, "txn_amount": _num(c.txn_amount), "charged_amount": _num(c.charged_amount)} for c in imp.charges]),
                "existing": existing,
                "vendors": db.query(Vendor).order_by(Vendor.name).all(),
                "rules": rules,
                "kind_labels": KIND_LABELS,
                "suggest_pattern": suggest_pattern,
                "suggest_vendor_name": suggest_vendor_name,
                "errors": errors or [],
                "problem_ids": set(problem_ids),
                "result": result,
            },
        )

    @router.get("/statements/{import_id}")
    def statement_review(request: Request, import_id: int, db: Session = Depends(get_db)):
        imp = db.get(StatementImport, import_id)
        if not imp:
            return RedirectResponse("/finance/statements", status_code=303)
        return _review(request, db, imp)

    @router.post("/statements/{import_id}")
    async def statement_save(request: Request, import_id: int, db: Session = Depends(get_db)):
        imp = db.get(StatementImport, import_id)
        if not imp or imp.committed_at:
            return RedirectResponse(f"/finance/statements/{import_id}", status_code=303)
        form = await request.form()

        month = parse_month_key(form.get("statement_month", ""))
        if month:
            imp.statement_month = month
        old_rate = float(imp.ils_per_usd)
        new_rate = _num(form.get("rate")) or old_rate
        rate_changed = abs(new_rate - old_rate) > 1e-6
        imp.ils_per_usd = new_rate

        remember = {}
        for c in imp.charges:
            key = str(c.id)
            decision = form.get(f"decision_{key}", c.decision)
            c.decision = decision if decision in ("vendor", "skip", "new") else c.decision
            c.vendor_name = (form.get(f"vendor_{key}") or "").strip() or None
            if c.decision == "vendor" and not c.vendor_name:
                c.vendor_name = suggest_vendor_name(c.merchant)
            c.channel = form.get(f"channel_{key}") if form.get(f"channel_{key}") in CHANNELS else None
            c.note = (form.get(f"note_{key}") or "").strip()[:255] or None
            try:
                c.split_months = max(1, min(24, int(form.get(f"split_{key}") or 1)))
            except ValueError:
                c.split_months = 1
            submitted = _num(form.get(f"amount_{key}"))
            previous = float(c.usd_amount) if c.usd_amount is not None else None
            if rate_changed and c.kind in ("ils", "ils_installment") and (
                submitted is None or previous is None or abs(submitted - previous) < 0.005
            ):
                c.usd_amount = usd_amount(c.kind, _num(c.txn_amount), _num(c.charged_amount), new_rate)
            else:
                c.usd_amount = submitted
            if form.get(f"remember_{key}"):
                remember[c.id] = form.get(f"pattern_{key}") or suggest_pattern(c.merchant)

        if form.get("action") != "commit":
            db.commit()
            return RedirectResponse(f"/finance/statements/{import_id}#top", status_code=303)

        errors, problem_ids = [], []
        undecided = [c for c in imp.charges if c.decision == "new"]
        if undecided:
            errors.append(f"{len(undecided)} new charge(s) still need a decision - choose Add or Skip for each.")
            problem_ids += [c.id for c in undecided]
        for c in imp.charges:
            if c.decision != "vendor":
                continue
            missing = [label for label, ok in (("a campaign", c.channel), ("an amount", c.usd_amount is not None)) if not ok]
            if missing:
                errors.append(f"{c.merchant}: choose {' and '.join(missing)} (or Skip it).")
                problem_ids.append(c.id)
        if errors:
            db.commit()
            return _review(request, db, imp, errors=errors, problem_ids=problem_ids)

        result = commit_import(db, imp, remember)
        db.commit()
        return RedirectResponse(
            f"/finance/statements/{import_id}?added={result['lines']}&total={result['total']}&skipped={result['skipped']}",
            status_code=303,
        )

    @router.post("/statements/{import_id}/delete")
    def statement_delete(import_id: int, db: Session = Depends(get_db)):
        imp = db.get(StatementImport, import_id)
        if imp and not imp.committed_at:
            (STATEMENTS_DIR / imp.stored_name).unlink(missing_ok=True)
            db.delete(imp)
            db.commit()
        return RedirectResponse("/finance/statements", status_code=303)

    @router.get("/rules")
    def rules_page(request: Request, db: Session = Depends(get_db)):
        rules = db.query(MatchRule).order_by(MatchRule.action, MatchRule.pattern).all()
        return templates.TemplateResponse(
            "finance_rules.html",
            {"request": request, "active": "fin_statements", "rules": rules,
             "vendors": db.query(Vendor).order_by(Vendor.name).all()},
        )

    @router.post("/rules/{rule_id}/delete")
    def rule_delete(rule_id: int, db: Session = Depends(get_db)):
        rule = db.get(MatchRule, rule_id)
        if rule:
            db.delete(rule)
            db.commit()
        return RedirectResponse("/finance/rules", status_code=303)

    return router
