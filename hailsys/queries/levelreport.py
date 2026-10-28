"""How sent emails fared, by level.  Read Only.

One row per email that reached Constant Contact (sent, bounced or complained):  its kind,
the best level and the parameter set it carried (zone emails only), its bases, and whether it
bounced, drew a complaint, or was followed by an unsubscribe.  Replies are not tracked (they
arrive in RBI's inbox) and opens and clicks are not stored.  A report email carries no level
yet (parking-lot item 266), so report emails are one comparison row.
"""

LEVEL_ORDER = ("agree", "one_source")

# A report email's best level.  None is "not recorded".  The day was not built under the set
# or the email went prior to 2026-10-28.
REPORT_LEVEL_ORDER = ("agree", "one_source", "context", "none", None)


# An unsubscribe is credited to the latest email sent to that address before our sync
# recorded it.  added_at is when the sync saw it, not when the person clicked.  Older
# suppressions (the legacy import) have no email before them and are credited to nothing.

_EMAILS_SQL = """
WITH emails AS (
    SELECT e.email_id, lower(trim(e.recipient_email)) AS email_norm,
        t.match_kind AS kind,
        max(s.zone_set_id) AS set_id,
        CASE WHEN bool_or(s.level = 'agree') THEN 'agree'
            WHEN bool_or(s.level = 'one_source') THEN 'one_source'
            WHEN bool_or(s.level = 'context') THEN 'context'
            WHEN bool_or(s.level = 'none') THEN 'none' END AS level,
        array_agg(DISTINCT s.basis ORDER BY s.basis) AS bases,
        min(s.sent_at) AS sent_at,
        bool_or(s.send_status = 'bounced') AS bounced,
        bool_or(s.send_status = 'complained') AS complained
    FROM sent_emails e
    JOIN email_templates t ON t.template_id = e.template_id
    JOIN send_log s ON s.email_id = e.email_id
    WHERE s.send_status IN ('sent', 'bounced', 'complained')
    GROUP BY e.email_id, e.recipient_email, t.match_kind
),
unsubs AS (
    SELECT DISTINCT ON (d.dnc_id) d.dnc_id, em.email_id
    FROM dnc_list d
    JOIN emails em ON em.email_norm = d.email_norm AND em.sent_at <= d.added_at
    WHERE d.source = 'unsubscribe' AND d.removed_at IS NULL
    ORDER BY d.dnc_id, em.sent_at DESC, em.email_id DESC
)
SELECT em.email_id, em.kind, em.set_id, em.level, em.bases, em.bounced, em.complained,
    (u.dnc_id IS NOT NULL) AS unsubscribed
FROM emails em
LEFT JOIN unsubs u ON u.email_id = em.email_id
ORDER BY em.email_id
"""


def fetch_emails(conn):
    """One row per email that reached Constant Contact, as described above."""
    return conn.execute(_EMAILS_SQL).fetchall()


def _blank():
    return {"emails": 0, "bounced": 0, "complained": 0, "unsubscribed": 0}


def _add(counts, row):
    counts["emails"] += 1
    for key in ("bounced", "complained", "unsubscribed"):
        counts[key] += bool(row[key])


def summarize(rows):
    """{'by_level', 'by_basis', 'report', 'report_by_level'}.  Zone emails by (set, best
    level) and by (set, bases), newest set first;  report emails in total and by (set, best
    level), newest set first and "not recorded" last.  Each row has the counts 'emails',
    'bounced', 'complained' and 'unsubscribed'."""
    by_level, by_basis, report, report_by = {}, {}, _blank(), {}
    for r in rows:
        if r["kind"] == "report":
            _add(report, r)
            _add(report_by.setdefault((r["set_id"], r["level"]), _blank()), r)
            continue
        _add(by_level.setdefault((r["set_id"], r["level"]), _blank()), r)
        _add(by_basis.setdefault((r["set_id"], " + ".join(r["bases"])), _blank()), r)
    levels = [dict(counts, set_id=set_id, level=level) for (set_id, level), counts
              in sorted(by_level.items(), key=lambda kv: (-kv[0][0], LEVEL_ORDER.index(kv[0][1])))]
    bases = [dict(counts, set_id=set_id, bases=label) for (set_id, label), counts
             in sorted(by_basis.items(), key=lambda kv: (-kv[0][0], kv[0][1]))]
    report_levels = [dict(counts, set_id=set_id, level=level) for (set_id, level), counts
                     in sorted(report_by.items(),
                               key=lambda kv: (kv[0][0] is None, -(kv[0][0] or 0),
                                               REPORT_LEVEL_ORDER.index(kv[0][1])))]
    return {"by_level": levels, "by_basis": bases, "report": report,
            "report_by_level": report_levels}
