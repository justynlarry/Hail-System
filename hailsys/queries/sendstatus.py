"""Read-Only status of sends for Send History Pages

One state per email, worked out from sent_emails and send_log, nothing stored.
Every email has at least one send_log row and the engine moves all of an email's
queued rows together, so the rows of one email are read as a group:

  sent          no queued rows left and none failed (sent, or later bounced or complained)
  failed        no queued rows left and at least one failed: nothing was sent
  needs_review  still queued, and sent_emails.error_detail is set: it MAY have been sent
  in_progress   still queued, and a list, campaign or schedule is already recorded: running
                now, or stopped part-way and waiting to be resumed
  queued        still queued and nothing started
"""

STATES = ("sent", "failed", "needs_review", "in_progress", "queued")

def _with_states(where):
    """Shared CTE, 'where' is one of the constants below."""
    return f"""
WITH per_email AS (
    SELECT e.email_id, e.batch_id, e.recipient_email, e.created_at, e.created_by,
        e.cc_list_id, e.cc_campaign_id, e.cc_activity_id, e.scheduled_at,
        e.error_detail AS email_error,
        count(*) FILTER(WHERE s.send_status = 'queued') AS n_queued,
        count(*) FILTER(WHERE s.send_status = 'failed') AS n_failed,
        max(s.sent_at) AS sent_at
    FROM sent_emails e
    JOIN send_log s ON s.email_id = e.email_id
    {where}
    GROUP BY e.email_id
), state AS (
    SELECT p.*,
            CASE
              WHEN n_queued = 0 AND n_failed = 0 THEN 'sent'
              WHEN n_queued = 0 THEN 'failed'
              WHEN email_error IS NOT NULL THEN 'needs_review'
              WHEN cc_list_id IS NOT NULL OR cc_campaign_id IS NOT NULL
                    OR scheduled_at IS NOT NULL THEN 'in_progress'
              ELSE 'queued'
            END AS state
    FROM per_email p
)
"""

_BATCHES_SQL = _with_states("") + """
SELECT b.*, u.emp_fname
FROM (
    SELECT batch_id, min(created_at) AS started_at, min(created_by) AS created_by,
           count(*) AS emails,
           count(*) FILTER (WHERE state = 'sent') AS sent,
           count(*) FILTER (WHERE state = 'failed') AS failed,
           count(*) FILTER (WHERE state = 'needs_review') AS needs_review,
           count(*) FILTER (WHERE state = 'in_progress') AS in_progress,
           count(*) FILTER (WHERE state = 'queued') AS queued
    FROM state
    GROUP BY batch_id
    ORDER BY min(created_at) DESC
    LIMIT %(limit)s
) b
LEFT JOIN users u ON u.emp_id = b.created_by
ORDER BY b.started_at DESC
"""


_BATCH_SQL = _with_states("WHERE e.batch_id = %(batch)s") + """
SELECT * FROM state ORDER BY email_id
"""


def fetch_batches(conn, *, limit=20):
    """Recent sends, newest first, with a count per state."""
    return conn.execute(_BATCHES_SQL, {"limit": limit}).fetchall()


def fetch_batch(conn, batch_id):
    """{'emails': [rows with a 'state'], 'counts': {state: n}}, or None."""
    rows = conn.execute(_BATCH_SQL, {"batch": batch_id}).fetchall()
    if not rows:
        return None
    counts = {s: 0 for s in STATES}
    for r in rows:
        counts[r["state"]] += 1
    return {"emails": rows, "counts": counts}


def fetch_last_sync(conn):
    """The newest unsubscribe sync row (or None), the most common reason
    a send queues nothing"""
    return conn.execute("SELECT status, started_at, finished_at FROM cc_sync_runs "
                        "ORDER BY run_id DESC LIMIT 1").fetchone()
