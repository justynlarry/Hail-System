"""Permission rows for test recipients (item 189).

sent_emails refuses an address with no active send_permission row (sql/066), so a test that
writes sent_emails, or queues a send, records permission for its recipients first.  Call it
inside the test's own transaction so the rollback removes the rows with everything else.
"""


def permit(conn, *emails, basis="service_client"):
    """Record permission to email each address, unless it is already recorded on that basis."""
    for email in emails:
        conn.execute(
            "INSERT INTO send_permission (email_raw, basis, as_of, detail, added_by) "
            "SELECT %s, %s, current_date, 'test fixture', emp_id "
            "FROM users WHERE role = 'system' ORDER BY emp_id LIMIT 1 "
            "ON CONFLICT (email_norm, basis) WHERE removed_at IS NULL DO NOTHING",
            (email, basis))


def permit_all_realtors(conn):
    """Permission for every realtor with an email, for tests that build the send list from real storm
    data.  Without it the list is empty since item 189 and those tests skip instead of running."""
    conn.execute(
        "INSERT INTO send_permission (email_raw, basis, as_of, detail, added_by) "
        "SELECT DISTINCT ON (lower(trim(r.agent_email))) r.agent_email, 'service_client', current_date, "
        "'test fixture', u.emp_id FROM realtors r "
        "CROSS JOIN (SELECT emp_id FROM users WHERE role = 'system' ORDER BY emp_id LIMIT 1) u "
        "WHERE r.agent_email IS NOT NULL AND btrim(r.agent_email) <> '' "
        "ON CONFLICT (email_norm, basis) WHERE removed_at IS NULL DO NOTHING")
