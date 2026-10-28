"""The template list on the send page: Active Templates, one page at a time.

Read Only.  One row per template name, showing its active version.  'versions' counts every
version the name has had, 'sends' and 'last_used' count every version's use.  'body' is returned
because the page renders each row's own sample, the user decides where the sources come from
(built-in template comes from its files, see templatestore.load).
"""

PAGE_SIZE = 10

_SELECT = """
SELECT t.template_id, t.template_name, t.description, t.subject, t.body,
       (t.blocks IS NOT NULL) AS has_blocks,
       t.report_type, t.report_text, t.created_at,
       u.emp_fname, u.emp_lname,
       (SELECT count(*) FROM email_templates v
         WHERE v.template_name = t.template_name) AS versions,
       (SELECT max(e.created_at) FROM sent_emails e
          JOIN email_templates v ON v.template_id = e.template_id
         WHERE v.template_name = t.template_name) AS last_used,
       (SELECT count(DISTINCT e.batch_id) FROM sent_emails e
          JOIN email_templates v ON v.template_id = e.template_id
         WHERE v.template_name = t.template_name) AS sends
FROM email_templates t
LEFT JOIN users u ON u.emp_id = t.created_by
WHERE t.is_active
"""

_COUNT_SQL = "SELECT count(*) AS n FROM email_templates t WHERE t.is_active "

KINDS = ("report", "zone")


def _where(kind):
    """The kind filter and its parameter.  None lists every kind."""
    if kind is None:
        return "", ()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    return "AND t.match_kind = %s ", (kind,)


def fetch_page(conn, page, per_page=PAGE_SIZE, kind=None):
    """{'rows', 'page', 'pages', 'total'}.  A page outside the range is moved into it."""
    where, params = _where(kind)
    total = conn.execute(_COUNT_SQL + where, params).fetchone()["n"]
    pages = max((total + per_page -1) // per_page, 1)
    page = min(max(page, 1), pages)
    rows = conn.execute(_SELECT + where + "ORDER BY t.template_name LIMIT %s OFFSET %s",
                        (*params, per_page, (page - 1) * per_page)).fetchall()
    return {"rows": rows, "page": page, "pages": pages, "total": total}

def fetch_one(conn, name, kind=None):
    """The active version of one template name, or None (also None if it is another kind)."""
    where, params = _where(kind)
    return conn.execute(_SELECT + where + "AND t.template_name = %s", (*params, name,)).fetchone()


def fetch_all(conn, kind=None):
    """Every active template, one row per name (the editor's list, there are few)."""
    where, params = _where(kind)
    return conn.execute(_SELECT + where + "ORDER BY t.template_name", params).fetchall()
