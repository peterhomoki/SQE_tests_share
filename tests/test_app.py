"""Smoke + behaviour tests for the Flask UI in app.py."""
import re


def _client(monkeypatch, fixture_db):
    import app
    monkeypatch.setattr(app, "DB_PATH", fixture_db)
    app.app.config["TESTING"] = True
    return app.app.test_client()


def test_home_renders(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Total questions" in body
    assert "10" in body                   # 10 fixture questions
    assert "BLP" in body                   # area code in by-area table
    assert "CrL" in body


def test_browse_lists_all(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/browse")
    assert r.status_code == 200
    body = r.data.decode()
    assert "10 matching question" in body  # total_match shown
    # All 10 QIDs should appear as link text
    for qid in range(1, 11):
        assert f">{qid}</a>" in body


def test_browse_flk_filter(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/browse?flk=FLK1")
    assert r.status_code == 200
    body = r.data.decode()
    assert "5 matching question" in body


def test_browse_area_filter(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/browse?area=CrL")
    assert r.status_code == 200
    body = r.data.decode()
    assert "5 matching question" in body


def test_question_detail_renders(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/question/1")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Question 1" in body
    # The correct option (A) should be tagged CORRECT
    assert "CORRECT" in body
    # All five options listed
    for lbl in "ABCDE":
        assert f"<strong>{lbl}.</strong>" in body
    assert "Explanation for option A" in body


def test_question_detail_404(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/question/9999")
    assert r.status_code == 404


def test_quiz_get_shows_form(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r = c.get("/quiz")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Submit answer" in body
    # Hidden qid input must be present
    assert re.search(r'name="qid"\s+value="\d+"', body)


def test_quiz_post_correct_records_score(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r_get = c.get("/quiz")
    qid = int(re.search(r'name="qid"\s+value="(\d+)"', r_get.data.decode()).group(1))

    # Option A is always correct in the fixture
    r_post = c.post("/quiz", data={"qid": qid, "answer": "A"})
    assert r_post.status_code == 200
    assert b"Correct." in r_post.data
    with c.session_transaction() as sess:
        assert sess["score"]["correct"] == 1
        assert sess["score"]["total"] == 1
        assert sess["score"]["wrong"] == []


def test_quiz_post_wrong_records_wrong_qid(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    r_get = c.get("/quiz")
    qid = int(re.search(r'name="qid"\s+value="(\d+)"', r_get.data.decode()).group(1))

    r_post = c.post("/quiz", data={"qid": qid, "answer": "B"})
    assert r_post.status_code == 200
    assert b"Wrong" in r_post.data
    with c.session_transaction() as sess:
        assert sess["score"]["correct"] == 0
        assert sess["score"]["total"] == 1
        assert sess["score"]["wrong"] == [qid]


def test_score_page_lists_wrong(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    # Force a wrong-answer state
    with c.session_transaction() as sess:
        sess["score"] = {"correct": 0, "total": 1, "wrong": [5]}
    r = c.get("/score")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Correct:" in body
    assert ">5</a>" in body                # the wrong QID is linked


def test_reset_clears_score(monkeypatch, fixture_db):
    c = _client(monkeypatch, fixture_db)
    with c.session_transaction() as sess:
        sess["score"] = {"correct": 3, "total": 5, "wrong": [1, 2]}
    r = c.get("/reset", follow_redirects=False)
    assert r.status_code == 302
    with c.session_transaction() as sess:
        assert "score" not in sess
