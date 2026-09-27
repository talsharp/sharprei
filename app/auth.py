from datetime import datetime
from urllib.parse import urlparse

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.audit import current_user_email, log_event
from app.database import SessionLocal
from app.models import User

PUBLIC_PATHS = ("/login", "/auth/", "/static/", "/health", "/favicon.ico")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

oauth = OAuth()
if config.GOOGLE_CONFIGURED:
    oauth.register(
        name="google",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
    )


def _is_public(path: str) -> bool:
    return path == "/login" or any(path.startswith(p) for p in PUBLIC_PATHS if p != "/login")


def _same_origin(request: Request) -> bool:
    """Blocks form posts that come from another website (CSRF). Browsers send
    Origin (or at least Referer) on every POST; if both are missing, it isn't
    a browser-driven cross-site request."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True
    allowed = {request.url.netloc}
    if config.BASE_HOST:
        allowed.add(config.BASE_HOST)
    return urlparse(source).netloc in allowed


def install(app: FastAPI, templates: Jinja2Templates) -> None:
    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        path = request.url.path

        # One canonical address, so the login cookie always belongs to it.
        if config.BASE_HOST and request.url.netloc != config.BASE_HOST and path != "/health":
            return RedirectResponse(config.BASE_URL + str(request.url).split(request.url.netloc, 1)[1], status_code=307)

        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return Response("Request blocked: it came from another website.", status_code=403)

        if _is_public(path):
            return await call_next(request)

        user = None
        user_id = request.session.get("user_id")
        if user_id:
            db = SessionLocal()
            try:
                user = db.get(User, user_id)
            finally:
                db.close()
        if not user or not user.active:
            request.session.clear()
            if request.headers.get("HX-Request") == "true":
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            if request.method == "GET":
                request.session["next"] = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login", status_code=303)

        request.state.user = user
        token = current_user_email.set(user.email)
        try:
            return await call_next(request)
        finally:
            current_user_email.reset(token)

    # Added after auth_gate so it wraps it - the gate needs request.session.
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SECRET_KEY,
        session_cookie="sharprei_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=config.SECURE_COOKIES,
    )

    router = APIRouter()

    def _login_page(request: Request, error: str = None, status_code: int = 200):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error, "google_configured": config.GOOGLE_CONFIGURED},
            status_code=status_code,
        )

    @router.get("/login")
    def login(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/", status_code=303)
        return _login_page(request)

    @router.get("/auth/google")
    async def auth_google(request: Request):
        if not config.GOOGLE_CONFIGURED:
            return RedirectResponse("/login", status_code=303)
        redirect_uri = (config.BASE_URL or str(request.base_url).rstrip("/")) + "/auth/callback"
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @router.get("/auth/callback")
    async def auth_callback(request: Request):
        if not config.GOOGLE_CONFIGURED:
            return RedirectResponse("/login", status_code=303)
        try:
            token = await oauth.google.authorize_access_token(request)
        except OAuthError:
            return _login_page(request, "Google sign-in didn't complete. Please try again.", 400)
        info = token.get("userinfo") or {}
        email = (info.get("email") or "").strip().lower()
        if not email or not info.get("email_verified"):
            return _login_page(request, "Google didn't confirm this email address.", 403)

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            if not user and email in config.OWNER_EMAILS:
                user = User(email=email, name=info.get("name"), role="owner", active=True)
                db.add(user)
                db.flush()
            if not user or not user.active:
                return _login_page(request, f"{email} doesn't have access to SharpREI.", 403)
            user.last_login_at = datetime.utcnow()
            if info.get("name"):
                user.name = info["name"]
            token_ctx = current_user_email.set(email)
            try:
                log_event(db, "sign_in", email)
                db.commit()
            finally:
                current_user_email.reset(token_ctx)
            user_id = user.id
        finally:
            db.close()

        next_url = request.session.pop("next", "/")
        request.session.clear()
        request.session["user_id"] = user_id
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        return RedirectResponse(next_url, status_code=303)

    @router.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    app.include_router(router)
