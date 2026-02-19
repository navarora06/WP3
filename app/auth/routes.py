from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user, LoginManager
from sqlalchemy import select
from datetime import datetime
from app.auth.forms import LoginForm
from app.models import User
from app.util import db_session, verify_password

login_manager = LoginManager()

@login_manager.user_loader
def load_user(user_id: str):
    with db_session() as db:
        return db.get(User, int(user_id))

bp = Blueprint("auth", __name__, url_prefix="")

@bp.get("/login")
def login():
    form = LoginForm()
    return render_template("auth/login.html", form=form)

@bp.post("/login")
def login_post():
    form = LoginForm()
    if not form.validate_on_submit():
        for field, errs in form.errors.items():
            for e in errs:
                flash(f"{field}: {e}", "danger")
        return render_template("auth/login.html", form=form), 400

    with db_session() as db:
        user = db.execute(select(User).where(User.email == form.email.data)).scalar_one_or_none()
        if not user or not verify_password(form.password.data, user.password_hash):
            flash("Wrong email or password", "danger")
            return render_template("auth/login.html", form=form), 401

        user.last_login_at = datetime.utcnow()
        login_user(user)
        return redirect(url_for("admin_upload.index"))

@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
