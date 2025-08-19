import os
import csv, io
from fastapi.responses import StreamingResponse
import re
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from contextlib import asynccontextmanager
import uvicorn
import stripe
from fastapi import FastAPI, Request, HTTPException
from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import models
from sqlalchemy import inspect
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

# Load environment variables from .env (for local runs)
from dotenv import load_dotenv
load_dotenv()


# FastAPI and Stripe

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database
try:
    from db import SessionLocal, db_path_info, init_db
    from crud import (
        get_active_by_email,
        get_active_and_not_expired_by_email,
        mark_telegram_id,
        get_recent_invite_for_email,
        get_recent_invite_for_user,
        log_invite,
    )
    from stripe_handlers import process_stripe_webhook_event
    DATABASE_AVAILABLE = True
except ImportError as e:
    logger.warning("Database modules not available: %s", e)
    DATABASE_AVAILABLE = False

# ======================
# 🔧 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change-this-admin-secret")

# Brand and URL Configuration
BRAND_NAME = os.getenv("BRAND_NAME", "PyAutoBot")
SALES_WEBSITE_URL = os.getenv("SALES_WEBSITE_URL", "")
FREE_GROUP_URL = os.getenv("FREE_GROUP_URL", "")

# Stripe Configuration
STRIPE_SECRET_KEY = os.getenv(
    "STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Stripe links (static Payment Links used in the Plans button)
STRIPE_MONTHLY_URL = os.getenv("STRIPE_MONTHLY_URL", "")
STRIPE_QUARTERLY_URL = os.getenv("STRIPE_QUARTERLY_URL", "")
STRIPE_ANNUAL_URL = os.getenv("STRIPE_ANNUAL_URL", "")

# VIP invite fallback (primary static link)
VIP_INVITE_LINK = os.getenv(
    "VIP_INVITE_LINK", "https://t.me/+PSEZYQQnodszYjYx")

# Placeholder for future one-time invites for multiple groups


def _parse_group_ids(raw: str) -> List[int]:
    ids: List[int] = []
    for p in (raw or "").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            ids.append(int(p))
        except ValueError:
            pass
    return ids


VIP_GROUP_IDS: List[int] = _parse_group_ids(os.getenv("VIP_GROUP_IDS", ""))

# Global application instance
application: Optional[Application] = None

# ======================
# Bot texts
# ======================
HOW_IT_WORKS_TEXT = (
    f"ℹ️ **Como funciona o {BRAND_NAME}**\n\n"
    "1️⃣ Escolha seu plano (se disponível).\n"
    "2️⃣ Realize o pagamento (se aplicável).\n"
    "3️⃣ Desbloqueie o acesso VIP informando seu e-mail.\n\n"
    "Se precisar de ajuda, toque em **🆘 Suporte**."
)

# ======================
# Bot UI
# ======================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("💡 Sobre", callback_data="about"),
            InlineKeyboardButton("🆘 Suporte", callback_data="support"),
        ],
        [
            InlineKeyboardButton("🔓 Desbloquear acesso", callback_data="unlock_access"),
            InlineKeyboardButton("🌟 Planos", callback_data="plans.open"),
        ],
        [
            InlineKeyboardButton("🎁 Comunidade", url=FREE_GROUP_URL),
        ],
        [
            InlineKeyboardButton("☕ Buy Me a Coffee", url="https://buymeacoffee.com/johnlen7"),
            InlineKeyboardButton("💸 PayPal", url="https://www.paypal.com/donate/?hosted_button_id=3VYZMCWGZRFML"),
        ],
        [
            InlineKeyboardButton("ℹ️ Como funciona", callback_data="how_it_works"),
        ]
    ]
    apoio_text = ("\n\nSe o projeto te ajudou, considere apoiar: "
                  "[BuyMeACoffee](https://buymeacoffee.com/johnlen7) ou "
                  "[PayPal](https://www.paypal.com/donate/?hosted_button_id=3VYZMCWGZRFML). "
                  "Sua contribuição ajuda a manter e evoluir o bot! 🙌")
    await update.effective_message.reply_text(
        f"✅ Bem-vindo ao {BRAND_NAME}! Escolha uma opção:{apoio_text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(
        f"🆔 Seu ID do Telegram é: {user_id}"
    )

# /groupid — retorna o ID do chat/grupo atual


async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Chat privado"
    await update.effective_message.reply_text(
        f"📌 Nome do grupo: {chat_title}\n🆔 ID do grupo: `{chat_id}`",
        parse_mode="Markdown"
    )

# (removed) test_invite command to avoid any shortcut for generating links


async def open_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        f"🌟 {BRAND_NAME} – Planos Demonstrativos\n\n"
        "💶 Mensal: R$ 10,00/mês\n"
        "📊 Trimestral: R$ 25,00/trimestre\n"
        "🏆 Anual: R$ 80,00/ano\n\n"
        "Esses valores são apenas para demonstração. Nenhum pagamento real será processado."
    )
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="home.back")]]
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def back_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)


async def show_how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=HOW_IT_WORKS_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Voltar", callback_data="home.back")]]
        )
    )
# ======================
# Unlock Access (basic flow)


async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="home.back")]]
    await update.callback_query.edit_message_text(
        text=(
            "🔓 **Desbloquear acesso**\n\n"
            "📧 **Digite o e-mail** que você usou para pagamento.\n\n"
            "✨ Após a verificação, você receberá:\n"
            "• Detalhes da sua assinatura\n"
            "• Um link temporário para o grupo VIP\n\n"
            "💡 Digite apenas seu e-mail abaixo:"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ASK_EMAIL
# ======================

ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^@]+\.[^\s@]+$")


async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_text(
            "⚠️ O e-mail informado não é válido. Tente novamente."
        )
        return ASK_EMAIL

    if not DATABASE_AVAILABLE:
        await update.effective_message.reply_text(
            f"✅ Obrigado! Recebemos **{email}**. Integração com banco de dados em configuração.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    with SessionLocal() as db:
        logger.info("DB URL runtime: %s", db_path_info(db))
        try:
            subscription = get_active_and_not_expired_by_email(db, email)
            if subscription:
                user_id = str(update.effective_user.id)
                logger.info(
                    "Trying to link telegram_user_id %s to email %s", user_id, email)
                success = mark_telegram_id(db, email, user_id)
                logger.info("Result of mark_telegram_id: %s", success)
                if success:
                    # FIRST MESSAGE: Subscription details
                    subscription_info = (
                        "✅ **Assinatura encontrada!**\n\n"
                        f"📧 **E-mail:** {subscription.email}\n"
                        f"📋 **Plano:** {subscription.plan_type.title()}\n"
                        f"📅 **Status:** {subscription.status.title()}\n"
                        f"⏰ **Expira em:** {subscription.expires_at.strftime('%d/%m/%Y') if subscription.expires_at else 'N/A'}\n\n"
                        "🔄 Gerando seu link de acesso..."
                    )
                    await update.effective_message.reply_text(
                        subscription_info,
                        parse_mode="Markdown"
                    )
                    
                    # Pequeno delay para melhor UX
                    await asyncio.sleep(1.5)
                    
                    # SECOND MESSAGE: Temporary link (with cooldown control)
                    try:
                        logger.info(f"🔗 Generating invite link for user {user_id}")
                        cooldown_seconds = int(os.getenv("INVITE_COOLDOWN_SECONDS", "180"))
                        # Checar por email E por telegram_user_id
                        recent_email = get_recent_invite_for_email(db, email, cooldown_seconds)
                        recent_user = get_recent_invite_for_user(db, user_id, cooldown_seconds)
                        recent = recent_email or recent_user
                        if recent:
                            # Em vez de reutilizar, avisar cooldown restante
                            now = datetime.utcnow()
                            elapsed = (now - recent.created_at).total_seconds()
                            remaining = max(0, int(cooldown_seconds - elapsed))
                            await update.effective_message.reply_text(
                                f"⏳ Please wait {remaining} seconds before requesting a new invite link.")
                            return ConversationHandler.END
                        else:
                            invite_link = await create_one_time_invite_link(
                                context.bot, update.effective_user.id)
                            is_temporary = invite_link != VIP_INVITE_LINK
                            expires_at = (datetime.utcnow() + timedelta(hours=1)) if is_temporary else None
                            log_invite(
                                db,
                                email=email,
                                telegram_user_id=user_id,
                                invite_link=invite_link,
                                expires_at=expires_at,
                                member_limit=1,
                                is_temporary=is_temporary,
                            )
                        
                        # Check whether the link is temporary or fallback
                        link_type = "temporário (1 uso)" if is_temporary else "fixo"
                        
                        # Format message for multiple links
                        if "\n" in invite_link:
                            # Múltiplos links (um por linha)
                            links_text = "\n".join([f"🔗 {link}" for link in invite_link.split("\n")])
                            links_count = len(invite_link.split("\n"))
                            await update.effective_message.reply_text(
                                f"🎉 **Acesso liberado!**\n\n"
                                f"🔗 **Seus links VIP ({link_type}):**\n{links_text}\n\n"
                                f"📊 **Total:** {links_count} grupos VIP\n\n"
                                "⏰ **Importante:**\n"
                                f"• {'Esses links expiram em 1 hora' if is_temporary else 'Links permanentes'}\n"
                                f"• {'Válido para uma pessoa apenas' if is_temporary else 'Pode ser usado várias vezes'}\n"
                                "• Use para entrar nos grupos VIP\n\n"
                                "🎯 Bem-vindo ao VIP!",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                        else:
                            # Link único (fallback)
                            await update.effective_message.reply_text(
                                "🎉 **Acesso liberado!**\n\n"
                                f"🔗 **Seu link VIP ({link_type}):**\n{invite_link}\n\n"
                                "⏰ **Importante:**\n"
                                f"• {'Esse link expira em 1 hora' if is_temporary else 'Link permanente'}\n"
                                f"• {'Válido para uma pessoa apenas' if is_temporary else 'Pode ser usado várias vezes'}\n"
                                "• Use para entrar no grupo VIP\n\n"
                                "🎯 Bem-vindo ao VIP!",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                        logger.info(
                            "✅ VIP access granted to user %s for email %s (link type: %s)", 
                            user_id, email, link_type)
                    except Exception as e:
                        logger.error("❌ Error in invite link process: %s", e, exc_info=True)
                        await update.effective_message.reply_text(
                            "⚠️ Erro ao gerar link de convite. Fale com o suporte: @Sthefano_p"
                        )
                else:
                    logger.error(
                        "Failed to link telegram_user_id %s to email %s", user_id, email)
                    await update.effective_message.reply_text(
                        "⚠️ Erro técnico. Tente novamente ou fale com o suporte."
                    )
            else:
                # Check if there is a subscription but expired
                any_sub = None
                try:
                    any_sub = get_active_by_email(db, email)
                except Exception:
                    any_sub = None
                if any_sub and any_sub.expires_at and any_sub.expires_at < datetime.utcnow():
                    # expirada
                    keyboard = [[InlineKeyboardButton("🌟 Planos", callback_data="plans.open")]]
                    await update.effective_message.reply_text(
                        "❌ Sua assinatura expirou. Renove seu plano para continuar. 💳",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await update.effective_message.reply_text(
                        "❌ Assinatura não encontrada para este e-mail.\n\n"
                        "Verifique se o pagamento foi concluído, se o e-mail está correto ou tente novamente mais tarde.\n"
                        "Se precisar de ajuda, fale com o suporte: @Sthefano_p"
                    )
        except (ValueError, TypeError) as e:
            logger.error("Erro de banco de dados em unlock_access_check_email: %s", e)
            await update.effective_message.reply_text(
                "⚠️ Erro técnico no banco de dados. Tente novamente ou fale com o suporte: @Sthefano_p"
            )
        except Exception as e:
            logger.critical(
                "Erro inesperado em unlock_access_check_email: %s", e, exc_info=True)
            await update.effective_message.reply_text(
                "⚠️ Erro inesperado. Fale com o suporte: @Sthefano_p"
            )
    return ConversationHandler.END


async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelado.")
    return ConversationHandler.END

# ======================
# VIP Invite Helpers
# ======================


async def create_one_time_invite_link(bot, user_id: int, ttl_seconds: int = 3600, member_limit: int = 1) -> str:
    """
    Generate one-time invite links for all VIP groups

    Args:
        bot: Bot instance
        user_id: Telegram user ID (for logs)
        ttl_seconds: Time to live in seconds (default: 1 hour)
        member_limit: Members limit (default: 1)

    Returns:
        Invite URLs (one-time or fallback)
    """
    logger.info(f"Starting invite link creation for user {user_id}")
    logger.info(f"Configured VIP_GROUP_IDS: {VIP_GROUP_IDS}")
    logger.info(f"Fallback VIP_INVITE_LINK: {VIP_INVITE_LINK}")
    
    allow_fallback = os.getenv("ALLOW_FALLBACK_INVITE", "0") == "1"
    if not VIP_GROUP_IDS:
        if allow_fallback:
            logger.warning("No VIP_GROUP_IDS configured, using fallback link (dev mode)")
            logger.info(f"Returning fallback link: {VIP_INVITE_LINK}")
            return VIP_INVITE_LINK
        logger.error("VIP group configuration missing and fallback disabled")
        raise RuntimeError("VIP group configuration is missing. Please contact support.")

    # Use epoch timestamp and disable join requests (1 hour, 1 use)
    expire_epoch = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
    logger.info(f"Links will expire at epoch: {expire_epoch}")

    invite_links = []
    
    # Generate links for all groups
    for group_id in VIP_GROUP_IDS:
        try:
            logger.info(f"Trying to create invite for group: {group_id}")
            
            invite_link = await bot.create_chat_invite_link(
                chat_id=group_id,
                expire_date=expire_epoch,
                member_limit=member_limit,
                creates_join_request=False,
                name=f"VIP Access - User {user_id}"
            )

            invite_links.append(invite_link.invite_link)
            logger.info(
                "✅ Created one-time invite for user %s in group %s: %s",
                user_id, group_id, invite_link.invite_link)

        except Exception as e:
            logger.error("❌ Error creating invite link for user %s in group %s: %s",
                         user_id, group_id, e, exc_info=True)
            # Continue with other groups even if one fails

    if invite_links:
        # Return all links separated by newlines
        all_links = "\n".join(invite_links)
        logger.info("✅ Successfully created %d invite links for user %s", len(invite_links), user_id)
        return all_links
    else:
        # If no links were created, use fallback
        if allow_fallback:
            logger.warning("🔄 Using fallback VIP link due to errors (dev mode)")
            logger.info(f"Returning fallback link: {VIP_INVITE_LINK}")
            return VIP_INVITE_LINK
        raise RuntimeError("Failed to create invite links for any VIP group. Please contact support.")

# ======================
# Button router
# ======================


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if data == "about":
        await update.callback_query.answer()
        apoio_text = ("\nSe o projeto te ajudou, apoie: "
                      "[BuyMeACoffee](https://buymeacoffee.com/johnlen7) ou "
                      "[PayPal](https://www.paypal.com/donate/?hosted_button_id=3VYZMCWGZRFML)")
        await update.callback_query.edit_message_text(
            text=(f"🤖 {BRAND_NAME}\n\nBot de automação para Telegram integrado ao Stripe.\n\n"
                  "Desenvolvido por @johnlen7.\n\n"
                  "Código aberto: https://github.com/johnlen7/bot-telegram\n"
                  f"{apoio_text}\n"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Voltar", callback_data="home.back")]]
            )
        )
        return
    data = update.callback_query.data if update.callback_query else None
    if data == "plans.open":
        await open_plans(update, context)
        return
    if data == "home.back":
        await back_to_home(update, context)
        return
    if data == "howitworks":
        await show_how_it_works(update, context)
        return
    if data == "unlock.access":
        await unlock_access_prompt(update, context)
        return
    if data == "myid.show":
        try:
            await update.callback_query.answer()
        except Exception as e:
            logger.warning("CallbackQuery answer failed: %s", e)
        uid = update.effective_user.id
        await update.callback_query.edit_message_text(
            text=f"🆔 Seu ID do Telegram é: <code>{uid}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Voltar", callback_data="home.back")]]
            )
        )
        return
    # fallback
    try:
        await update.callback_query.answer()
    except Exception as e:
        logger.warning("CallbackQuery answer failed: %s", e)
    await update.callback_query.edit_message_text(
        text=f"✅ Você clicou: {data}"
    )

# ======================
# FastAPI Setup
# ======================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the application lifecycle"""
    global application
    polling_task = None

    # Startup
    # Initialize database
    if DATABASE_AVAILABLE:
        try:
            init_db()
            with SessionLocal() as db:
                logger.info("Database available (Postgres/SQL) - URL: %s", db_path_info(db))
                # Lightweight SQLite migration: add missing columns if needed
                try:
                    _apply_sqlite_migrations(db)
                except Exception as mig_err:
                    logger.warning("DB migration step skipped/failed: %s", mig_err)
        except Exception as e:
            logger.exception("Failed to initialize database: %s", e)
    else:
        logger.warning("Database not available - running in limited mode")

    # Configure bot
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN not defined")

    application = ApplicationBuilder().token(TOKEN).build()

    # Add handlers
    setup_handlers(application)

    # Execution mode
    local_mode = os.getenv("LOCAL_POLLING", "0") == "1"
    if local_mode:
        # Local mode: initialize + start + start_polling (compatible with running event loop)
        logger.info("Starting bot in LOCAL POLLING mode")
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
    else:
        # Production mode: webhook
        if not PUBLIC_URL:
            raise RuntimeError("PUBLIC_URL not defined for webhook")
        await application.initialize()
        await application.start()
        webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"Bot webhook set to: {webhook_url}")

    yield

    # Shutdown
    if application:
        if local_mode:
            try:
                await application.updater.stop()
            except Exception:
                pass
        try:
            await application.stop()
            await application.shutdown()
        except Exception:
            pass
        logger.info("Bot application shut down")


def _apply_sqlite_migrations(session):
    """Apply minimal schema migrations for SQLite (non-destructive)."""
    bind = session.get_bind()
    try:
        dialect = bind.dialect.name
    except Exception:
        dialect = "sqlite"
    if dialect != "sqlite":
        return
    conn = bind.connect()
    # Ensure subscriptions.full_name exists
    cols = conn.exec_driver_sql("PRAGMA table_info(subscriptions)").fetchall()
    col_names = {row[1] for row in cols} if cols else set()
    if "full_name" not in col_names:
        conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN full_name VARCHAR(255)")




app = FastAPI(
    title="PyAutoBot",
    description="Telegram Bot + Stripe Webhook Integration",
    lifespan=lifespan
)

# Sessions for admin area
app.add_middleware(SessionMiddleware, secret_key=ADMIN_SECRET)


def _html_page(title: str, body: str) -> str:
    return (
        f"""
<!DOCTYPE html>
<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\"/>\n<title>{title}</title>\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n<style>
/* Base */
body{{background:#0b1220;color:#e2e8f0;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px}}
.muted{{color:#94a3b8}}
.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
/* Cards */
.card{{background:#0f172a;border:1px solid #1f2937;border-radius:12px;padding:20px;box-shadow:0 6px 24px rgba(0,0,0,.25)}}
/* Table */
table{{border-collapse:collapse;width:100%;border-radius:10px;overflow:hidden}}
th,td{{padding:12px;border-bottom:1px solid #1f2937;text-align:left}}
th{{background:#111827;color:#cbd5e1;text-transform:uppercase;font-size:12px;letter-spacing:.6px}}
tr:hover td{{background:#0e1626}}
/* Forms */
label{{display:flex;flex-direction:column;gap:6px;font-size:13px;color:#cbd5e1}}
input,select{{width:100%;background:#0b1220;color:#e2e8f0;border:1px solid #1f2937;border-radius:8px;padding:10px}}
form{{margin:0}}
/* Buttons */
a.button, button, input[type=submit]{{background:#6366f1;color:#fff;border:none;padding:9px 14px;border-radius:8px;text-decoration:none;cursor:pointer}}
a.button:hover, button:hover, input[type=submit]:hover{{filter:brightness(1.1)}}
.danger{{background:#ef4444}}
</style>\n</head>\n<body>\n{body}\n</body></html>"""
    )


def _is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _require_admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_form(request: Request):
    body = """
    <h1>Admin Login</h1>
    <form method=\"post\" action=\"/admin/login\">\n<div class=\"row\">\n<label>Username <input name=\"username\" required/></label>\n<label>Password <input type=\"password\" name=\"password\" required/></label>\n<input type=\"submit\" value=\"Sign In\"/>\n</div>\n</form>
    """
    return HTMLResponse(_html_page("Admin Login", body))


@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse(url="/admin/subscriptions", status_code=303)
    return HTMLResponse(_html_page("Admin Login", "<p>Invalid credentials.</p><p><a href=\"/admin/login\">Try again</a></p>"))


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


def _subscription_row(s):
    return f"<tr><td>{s.id}</td><td>{s.full_name or ''}</td><td>{s.email}</td><td>{s.telegram_user_id or ''}</td><td>{s.plan_type or ''}</td><td>{s.status or ''}</td><td>{(s.created_at or '')}</td><td>{(s.expires_at or '')}</td><td class=\"row\"><a class=\"button\" href=\"/admin/subscriptions/{s.id}/edit\">Edit</a><form method=\"post\" action=\"/admin/subscriptions/{s.id}/delete\" onsubmit=\"return confirm('Delete?');\"><input class=\"danger\" type=\"submit\" value=\"Delete\"/></form></td></tr>"


@app.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_list_subscriptions(request: Request):
    _require_admin(request)
    from sqlalchemy import desc
    with SessionLocal() as db:
        subs = db.query(models.Subscription).order_by(desc(models.Subscription.id)).limit(200).all()
    rows = "".join(_subscription_row(s) for s in subs)
    rows_html = rows if rows else '<tr><td colspan=9 class="muted">No records</td></tr>'
    body = f"""
    <div class=\"row\"><h1 style=\"margin-right:auto\">Subscriptions</h1><a class=\"button\" href=\"/admin/subscriptions/new\">New</a><a class=\"button\" href=\"/admin/subscriptions/export.csv\">Export CSV</a><a class=\"button\" href=\"/admin/logout\">Logout</a></div>
    <table><thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Telegram ID</th><th>Plan</th><th>Status</th><th>Created</th><th>Expires</th><th>Actions</th></tr></thead><tbody>{rows_html}</tbody></table>
    """
    return HTMLResponse(_html_page("Subscriptions", body))


@app.get("/admin/subscriptions/new", response_class=HTMLResponse)
async def admin_new_subscription_form(request: Request):
    _require_admin(request)
    body = """
    <h1>New Subscription</h1>
    <form method=\"post\" action=\"/admin/subscriptions\">\n<div class=\"row\">\n<label>Name <input name=\"full_name\"/></label>\n<label>Email <input name=\"email\" required/></label>\n<label>Telegram ID <input name=\"telegram_user_id\"/></label>\n<label>Plan <select name=\"plan_type\"><option value=\"monthly\">monthly</option><option value=\"quarterly\">quarterly</option><option value=\"annual\">annual</option></select></label>\n<label>Status <select name=\"status\"><option value=\"active\">active</option><option value=\"past_due\">past_due</option><option value=\"canceled\">canceled</option></select></label>\n<label>Expires at (YYYY-MM-DD) <input name=\"expires_at\"/></label>\n<input type=\"submit\" value=\"Create\"/>\n</div>\n</form>
    """
    return HTMLResponse(_html_page("New Subscription", body))


@app.get("/admin/subscriptions/{sub_id}/edit", response_class=HTMLResponse)
async def admin_edit_subscription_form(request: Request, sub_id: int):
    _require_admin(request)
    with SessionLocal() as db:
        sub = db.query(models.Subscription).filter_by(id=sub_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Not found")
    def val(x):
        return "" if x is None else str(x)
    expires_val = val(sub.expires_at.date() if sub.expires_at else "")
    body = f"""
    <h1>Edit Subscription #{sub.id}</h1>
    <form method=\"post\" action=\"/admin/subscriptions/{sub.id}\">\n<div class=\"row\">\n<label>Name <input name=\"full_name\" value=\"{val(sub.full_name)}\"/></label>\n<label>Email <input name=\"email\" value=\"{val(sub.email)}\" required/></label>\n<label>Telegram ID <input name=\"telegram_user_id\" value=\"{val(sub.telegram_user_id)}\"/></label>\n<label>Plan <select name=\"plan_type\"><option {'selected' if sub.plan_type=='monthly' else ''} value=\"monthly\">monthly</option><option {'selected' if sub.plan_type=='quarterly' else ''} value=\"quarterly\">quarterly</option><option {'selected' if sub.plan_type=='annual' else ''} value=\"annual\">annual</option></select></label>\n<label>Status <select name=\"status\"><option {'selected' if sub.status=='active' else ''} value=\"active\">active</option><option {'selected' if sub.status=='past_due' else ''} value=\"past_due\">past_due</option><option {'selected' if sub.status=='canceled' else ''} value=\"canceled\">canceled</option></select></label>\n<label>Expires at (YYYY-MM-DD) <input name=\"expires_at\" value=\"{expires_val}\"/></label>\n<input type=\"submit\" value=\"Save\"/>\n</div>\n</form>
    """
    return HTMLResponse(_html_page("Edit Subscription", body))

# Nova rota de exportação CSV para admin subscriptions
@app.get("/admin/subscriptions/export.csv")
async def admin_export_subscriptions_csv(request: Request):
    _require_admin(request)
    with SessionLocal() as db:
        subs = db.query(models.Subscription).order_by(models.Subscription.id.asc()).all()

    def fmt_dt(dt):
        try:
            # YYYY-MM-DD HH:MM:SS (sem timezone, compatível com Excel)
            return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
        except Exception:
            return str(dt) if dt else ""

    def fmt_date(dt):
        try:
            return dt.date().isoformat() if dt else ""
        except Exception:
            return dt.isoformat() if dt else ""

    # CSV em memória com BOM para Excel
    sio = io.StringIO()
    sio.write("\ufeff")
    writer = csv.writer(sio)
    writer.writerow(["ID", "Name", "Email", "Telegram ID", "Plan", "Status", "Created", "Expires"])
    for s in subs:
        writer.writerow([
            s.id,
            (s.full_name or ""),
            (s.email or ""),
            (s.telegram_user_id or ""),
            (s.plan_type or ""),
            (s.status or ""),
            fmt_dt(getattr(s, "created_at", None)),
            fmt_date(getattr(s, "expires_at", None)),
        ])

    filename = f"subscriptions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(iter([sio.getvalue()]), media_type="text/csv; charset=utf-8", headers=headers)


def _parse_date_or_none(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


@app.post("/admin/subscriptions")
async def admin_create_subscription(
    request: Request,
    full_name: str = Form(None),
    email: str = Form(...),
    telegram_user_id: str = Form(None),
    plan_type: str = Form("monthly"),
    status: str = Form("active"),
    expires_at: str = Form(None),
):
    _require_admin(request)
    with SessionLocal() as db:
        sub = models.Subscription(
            full_name=full_name,
            email=email.lower().strip(),
            telegram_user_id=telegram_user_id,
            plan_type=plan_type,
            status=status,
            expires_at=_parse_date_or_none(expires_at),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(sub)
        db.commit()
    return RedirectResponse(url="/admin/subscriptions", status_code=303)


@app.post("/admin/subscriptions/{sub_id}")
async def admin_update_subscription(
    request: Request,
    sub_id: int,
    full_name: str = Form(None),
    email: str = Form(...),
    telegram_user_id: str = Form(None),
    plan_type: str = Form("monthly"),
    status: str = Form("active"),
    expires_at: str = Form(None),
):
    _require_admin(request)
    with SessionLocal() as db:
        sub = db.query(models.Subscription).filter_by(id=sub_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Not found")
        sub.full_name = full_name
        sub.email = email.lower().strip()
        sub.telegram_user_id = telegram_user_id
        sub.plan_type = plan_type
        sub.status = status
        sub.expires_at = _parse_date_or_none(expires_at)
        sub.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url="/admin/subscriptions", status_code=303)


@app.post("/admin/subscriptions/{sub_id}/delete")
async def admin_delete_subscription(request: Request, sub_id: int):
    _require_admin(request)
    with SessionLocal() as db:
        sub = db.query(models.Subscription).filter_by(id=sub_id).first()
        if sub:
            db.delete(sub)
            db.commit()
    return RedirectResponse(url="/admin/subscriptions", status_code=303)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/telegram/{token}")
async def telegram_webhook(token: str, request: Request):
    """
    Receive Telegram updates and process them via PTB.

    Security: validate token in URL
    """
    if token != TOKEN:
        logger.warning("Invalid token in webhook: %s", token)
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        update_data = await request.json()
        update = Update.de_json(update_data, application.bot)
        await application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.critical(
            "Unexpected error processing Telegram update: %s", e, exc_info=True)
        raise


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Process Stripe webhooks.

    Security: validate webhook signature
    """
    if not DATABASE_AVAILABLE:
        logger.warning("Stripe webhook received but database not available")
        return {"status": "database_unavailable"}

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing signature")

    try:
        # Validate signature
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as exc:
        logger.error("Invalid payload in stripe webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        logger.error("Invalid signature in stripe webhook: %s", exc)
        raise HTTPException(
            status_code=400, detail="Invalid signature") from exc

    # Processar evento
    with SessionLocal() as db:
        logger.info("DB URL runtime: %s", db_path_info(db))
        try:
            success = await process_stripe_webhook_event(db, event)
            if success:
                logger.info(
                    "Stripe webhook processed: %s (%s)", event['id'], event['type'])
                return {"status": "received"}
            else:
                logger.error(
                    "Failed to process stripe webhook: %s", event['id'])
                raise HTTPException(
                    status_code=500, detail="Processing failed")
        except Exception as e:
            logger.critical(
                "Erro inesperado no handler do stripe webhook: %s", e, exc_info=True)
            raise


@app.post("/stripe/webhook-test")
async def stripe_webhook_test(request: Request):
    """
    Endpoint de teste para webhooks do Stripe (sem validação de assinatura)
    USAR APENAS PARA DESENVOLVIMENTO/TESTE
    """
    if not DATABASE_AVAILABLE:
        logger.warning(
            "Stripe webhook test received but database not available")
        return {"status": "database_unavailable"}

    try:
        event = await request.json()
        logger.info(
            "Test webhook received: %s - %s",
            event.get('type', 'unknown'), event.get('id', 'no-id'))

        # Processar evento
        with SessionLocal() as db:
            logger.info("DB URL runtime: %s", db_path_info(db))
            success = await process_stripe_webhook_event(db, event)
            if success:
                logger.info(
                    f"Test webhook processed successfully: {event.get('id')}")
                return {"status": "received", "processed": True}
            else:
                logger.warning(
                    f"Test webhook processing failed: {event.get('id')}")
                return {"status": "received", "processed": False}

    except Exception as e:
        logger.critical(
            "Erro inesperado no test webhook handler: %s", e, exc_info=True)
        raise

# ======================
# Bot Handlers Setup
# ======================


def setup_handlers(app: Application):
    """Configura todos os handlers do bot"""
    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("groupid", groupid))
    # Removed: testinvite command

    # ConversationHandler para Unlock Access
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(
            unlock_access_prompt, pattern=r"^unlock\.access$")],
        states={
            ASK_EMAIL: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]
        },
        fallbacks=[
            CommandHandler("cancel", unlock_cancel),
            CallbackQueryHandler(back_to_home, pattern=r"^home\.back$")
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)

    # CallbackQueryHandler global para outros botões (exceto unlock.access e home.back no contexto da conversa)
    app.add_handler(CallbackQueryHandler(
        button_router, pattern=r"^(plans\.open|howitworks|myid\.show)$"))
    
    # Handler separado para home.back fora do contexto da conversa
    app.add_handler(CallbackQueryHandler(
        back_to_home, pattern=r"^home\.back$"))

# ======================
# Main
# ======================


def main():
    """
    Executa o bot via FastAPI unificado

    Local: LOCAL_POLLING=1 → FastAPI + bot polling
    Produção: LOCAL_POLLING=0 → FastAPI + bot webhook
    """
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN não definido. Configure no .env ou nas Variables do Railway.")

    # Configurar porta
    port = int(os.environ.get("PORT", "8080"))

    if os.getenv("LOCAL_POLLING", "0") == "1":
        logger.info("Starting in LOCAL mode: FastAPI + Bot Polling")
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
    else:
        logger.info("Starting in PRODUCTION mode: FastAPI + Bot Webhook")
        uvicorn.run(app, host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()