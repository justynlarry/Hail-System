"""Runtime Settings:  Read from the single-row settings table.
"""

SETTINGS_SQL = """
    SELECT default_zip_radius_miles, default_match_radius_miles,
           rentcast_billing_day, rentcast_monthly_quota
        FROM settings
    WHERE id = 1
"""


def fetch_settings(conn):
    """Returns the tunable radii as floats

    Columns are NUMERIC, so psycopg returns Decimal.  miles_to_metres
    multiplies by a float, and Decimal * float = TypeError.  Cast 
    happens here, instead of at fourteen call sites.
    """
    with conn.cursor() as cur:
        cur.execute(SETTINGS_SQL)
        row = cur.fetchone()
    
    return {
        "zip_radius_miles": float(row["default_zip_radius_miles"]),
        "match_radius_miles": float(row["default_match_radius_miles"]),
        "rentcast_billing_day": row["rentcast_billing_day"],
        "rentcast_monthly_quota": row["rentcast_monthly_quota"],
    }
