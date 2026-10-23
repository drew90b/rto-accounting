"""User accounts: password rules, lockout, session_version, last-admin guard, CLI."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.enums import UserRole
from app.models.user import User
from app.scripts import users as cli
from app.services import user_service as svc

PW = "correct horse battery"


def make_user(db, username="belinda", role=UserRole.employee, must_change=False):
    user = svc.create_user(
        db, username=username, display_name=username.title(), role=role,
        password=PW, must_change_password=must_change,
    )
    db.commit()
    return user


def test_username_is_stored_lowercase_and_unique(db):
    make_user(db, "Belinda")
    assert db.query(User).one().username == "belinda"
    with pytest.raises(ValueError):
        svc.create_user(db, username="BELINDA", display_name="x", role=UserRole.employee,
                        password=PW, must_change_password=False)


def test_short_password_is_rejected(db):
    with pytest.raises(ValueError):
        svc.create_user(db, username="a", display_name="A", role=UserRole.employee,
                        password="short", must_change_password=False)


def test_password_is_hashed(db):
    user = make_user(db)
    assert PW not in user.password_hash
    assert svc.verify_password(user.password_hash, PW)
    assert not svc.verify_password(user.password_hash, "wrong password!")


def test_authenticate_success_and_failures(db):
    make_user(db)
    assert svc.authenticate(db, "Belinda", PW).username == "belinda"
    assert svc.authenticate(db, "belinda", "wrong password!") is None
    assert svc.authenticate(db, "nobody", PW) is None


def test_five_failures_lock_the_account_and_unlock_clears_it(db):
    user = make_user(db)
    for _ in range(svc.MAX_FAILED_LOGINS):
        assert svc.authenticate(db, "belinda", "wrong password!") is None
    assert user.locked_until > datetime.utcnow()
    # Even the right password is refused while locked.
    assert svc.authenticate(db, "belinda", PW) is None
    svc.unlock_user(user)
    assert svc.authenticate(db, "belinda", PW) is user


def test_lock_expires(db):
    user = make_user(db)
    user.locked_until = datetime.utcnow() - timedelta(seconds=1)
    assert svc.authenticate(db, "belinda", PW) is user


def test_disabled_user_cannot_authenticate(db):
    user = make_user(db)
    make_user(db, "boss", UserRole.admin)
    svc.disable_user(db, user)
    assert svc.authenticate(db, "belinda", PW) is None


def test_password_reset_role_change_and_disable_bump_session_version(db):
    make_user(db, "boss", UserRole.admin)
    user = make_user(db)
    v = user.session_version
    svc.set_password(user, "another long password", must_change=True)
    assert user.session_version == v + 1 and user.must_change_password
    svc.set_role(db, user, UserRole.admin)
    assert user.session_version == v + 2
    svc.disable_user(db, user)
    assert user.session_version == v + 3


def test_last_active_admin_cannot_be_disabled_or_demoted(db):
    admin = make_user(db, "boss", UserRole.admin)
    with pytest.raises(ValueError):
        svc.disable_user(db, admin)
    with pytest.raises(ValueError):
        svc.set_role(db, admin, UserRole.employee)
    make_user(db, "second", UserRole.admin)
    svc.set_role(db, admin, UserRole.employee)  # now allowed


# --- CLI ------------------------------------------------------------------

@pytest.fixture
def run_cli(db_connection, monkeypatch):
    monkeypatch.setattr(
        cli, "SessionLocal",
        sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint"),
    )
    return cli.main


def test_cli_create_temporary_prints_password_once(run_cli, db, capsys):
    assert run_cli(["create", "--username", "belinda", "--display-name", "Belinda",
                    "--role", "employee", "--temporary"]) == 0
    out = capsys.readouterr().out
    assert "Temporary password" in out
    user = db.query(User).one()
    assert user.must_change_password is True
    password = out.split(":")[-1].strip()
    assert svc.verify_password(user.password_hash, password)


def test_cli_create_prompts_for_password_and_never_takes_it_as_argument(run_cli, db, monkeypatch):
    answers = iter([PW, PW])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert run_cli(["create", "--username", "boss", "--display-name", "Boss", "--role", "admin"]) == 0
    assert db.query(User).one().must_change_password is False
    with pytest.raises(SystemExit):
        run_cli(["create", "--username", "x", "--display-name", "X", "--role", "admin",
                 "--password", PW])


def test_cli_refuses_to_remove_last_admin(run_cli, db, capsys):
    make_user(db, "boss", UserRole.admin)
    assert run_cli(["disable", "boss"]) == 1
    assert run_cli(["set-role", "boss", "employee"]) == 1
    assert "last active admin" in capsys.readouterr().err
    assert db.query(User).one().active is True


def test_cli_list_never_prints_hashes(run_cli, db, capsys):
    user = make_user(db)
    run_cli(["list"])
    out = capsys.readouterr().out
    assert "belinda" in out
    assert user.password_hash not in out and "argon2" not in out


def test_cli_unlock_and_reset(run_cli, db):
    user = make_user(db)
    user.locked_until = datetime.utcnow() + timedelta(minutes=10)
    db.commit()
    assert run_cli(["unlock", "belinda"]) == 0
    db.refresh(user)
    assert user.locked_until is None
    v = user.session_version
    assert run_cli(["reset-password", "belinda", "--temporary"]) == 0
    db.refresh(user)
    assert user.session_version == v + 1 and user.must_change_password
