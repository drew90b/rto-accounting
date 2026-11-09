"""
Real concurrency on PostgreSQL (opt-in): VR_PG_URL must point at a DISPOSABLE database already migrated to head.
Two sessions answer the same question from the same page at the same moment; exactly one decision may result.
Skipped when VR_PG_URL is not set.
"""
import os
import threading
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.enums import UserRole
from app.models.vehicle_recon import VrDecision, VrQuestion
from app.services import user_service
from app.services import vehicle_recon_service as svc
from app.services import vr_sources as vr
from tests import vr_fixtures as f
from tests.conftest import TEST_PASSWORD

PG = os.environ.get("VR_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="VR_PG_URL (a disposable, migrated PostgreSQL database) is not set")


def test_simultaneous_answers_one_wins():
    engine = create_engine(PG)
    S = sessionmaker(bind=engine)
    with engine.begin() as c:
        c.execute(text("TRUNCATE vr_events, vr_decisions, vr_questions, vr_source_records, vr_snapshots, users RESTART IDENTITY CASCADE"))
    s = S()
    for n in ("pgadmin1", "pgadmin2"):
        user_service.create_user(s, username=n, display_name=n, role=UserRole.admin, password=TEST_PASSWORD, must_change_password=False)
    s.commit()
    vr.load_frazer_vehicles(s, f.vehicles_csv([f.vehicle("10063", 1), f.vehicle("10118", 1)]), "v.csv", date(2026, 11, 8))
    svc.refresh(s, date(2026, 11, 9), observe_app=False)
    s.commit()
    q = s.query(VrQuestion).one()
    ref, version, fp = q.ref, q.version, q.evidence_fingerprint
    s.close()

    barrier = threading.Barrier(2)
    results = {}

    def worker(name, choice):
        sess = S()
        try:
            u = sess.query(__import__("app.models.user", fromlist=["User"]).User).filter_by(username=name).one()
            barrier.wait()
            try:
                svc.answer_question(sess, u, ref, choice, version, fp, today=date(2026, 11, 10))
                sess.commit()
                results[name] = "won"
            except svc.StaleAnswer:
                sess.rollback()
                results[name] = "refused"
        finally:
            sess.close()

    ts = [threading.Thread(target=worker, args=("pgadmin1", "reentry")), threading.Thread(target=worker, args=("pgadmin2", "duplicate"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results.values()) == ["refused", "won"], results
    s = S()
    assert s.query(VrDecision).count() == 1
    s.close()
