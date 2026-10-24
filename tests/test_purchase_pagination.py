"""Purchases list: 50 per page, filters survive paging, export is unchanged."""

import re

from app.models.purchase import Purchase
from tests.test_purchase_workflow import post_purchase


def make_purchases(client, n, **overrides):
    for i in range(n):
        assert post_purchase(client, **overrides).status_code == 303


def rows(html):
    return len(re.findall(r'href="/purchases/\d+/edit" class="btn btn-sm', html))


def test_list_shows_50_per_page_with_links(admin_client):
    make_purchases(admin_client, 120, vendor="Paged Vendor")
    p1 = admin_client.get("/purchases/").text
    assert rows(p1) == 50
    assert "Page 1 of 3" in p1 and "120 purchases" in p1
    assert "Next" in p1 and "Previous" not in p1
    assert rows(admin_client.get("/purchases/?page=2").text) == 50
    p3 = admin_client.get("/purchases/?page=3").text
    assert rows(p3) == 20 and "Previous" in p3 and "Next" not in p3


def test_pages_do_not_overlap_or_skip(admin_client, db):
    make_purchases(admin_client, 60)
    ids = []
    for page in (1, 2):
        html = admin_client.get(f"/purchases/?page={page}").text
        ids += re.findall(r'href="/purchases/(\d+)/edit" class="btn btn-sm', html)
    assert len(ids) == 60 and len(set(ids)) == 60


def test_filters_are_kept_on_page_links(admin_client):
    make_purchases(admin_client, 55, vendor="Keep Me Vendor")
    make_purchases(admin_client, 3, vendor="Other")
    html = admin_client.get("/purchases/?vendor=keep+me&review_status=pending&page=1").text
    assert "55 purchases" in html and "Page 1 of 2" in html
    link = re.search(r'href="(/purchases/\?[^"]*page=2)"', html).group(1)
    assert "vendor=keep+me" in link and "review_status=pending" in link
    page2 = admin_client.get(link.replace("&amp;", "&")).text
    assert rows(page2) == 5 and "Other" not in page2


def test_bad_page_numbers_are_clamped(admin_client):
    make_purchases(admin_client, 3)
    assert rows(admin_client.get("/purchases/?page=0").text) == 3
    assert rows(admin_client.get("/purchases/?page=-4").text) == 3
    assert rows(admin_client.get("/purchases/?page=99").text) == 3
    assert admin_client.get("/purchases/?page=abc").status_code == 422


def test_empty_list_has_no_pager(admin_client):
    html = admin_client.get("/purchases/").text
    assert "No purchases found" in html and "Page 1" not in html


def test_employee_can_page_and_export_still_includes_everything(employee_client, admin_client, db):
    make_purchases(employee_client, 52)
    assert rows(employee_client.get("/purchases/?page=2").text) == 2
    assert employee_client.get("/purchases/export", follow_redirects=False).status_code == 403
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(admin_client.get("/purchases/export").content))
    assert wb.active.max_row == 1 + 52


def test_menu_script_comes_before_the_page_content(admin_client):
    html = admin_client.get("/purchases/").text
    assert html.index("getElementById('mobile-menu-btn')") < html.index('class="table-container"')
    assert html.count("getElementById('mobile-menu-btn')") == 1
