"""Fill report_zip_distances for reports that predate the trigger
Each batch skips reports that already have rows, so an 
interrupted run picks up where it stopped.  Batched, there is 
one transaction per batch rather than on hour-long INSERT.

Recompute Path:  after raising hail_pair_ceiling_m() or 
reloading zcta_boundaries, TRUNCATE report_zip_distances
and run this again.

Usage:
    python3 scripts/backfill_zip_distances.py [--batch-size 1000]
"""

import argparse
import logging
import time

from hailsys.db import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_BATCH_SQL = """
INSERT INTO report_zip_distances (iem_id, zcta5, distance_m)
SELECT i.iem_id, z.zcta5,
    ST_Distance(i.geom::geography, z.geom::geography)
FROM iem_data i
JOIN zcta_boundaries z
    ON ST_DWithin(i.geom::geography, z.geom::geography, hail_pair_ceiling_m())
WHERE i.iem_id >= %(lo)s AND i.iem_id < %(hi)s
    AND NOT EXISTS (
        SELECT 1 FROM report_zip_distances d WHERE d.iem_id = i.iem_id
    )
ON CONFLICT (iem_id, zcta5) DO NOTHING
"""

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="iem_id range per transaction")
    args = parser.parse_args()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT min(iem_id) AS lo, max(iem_id) AS hi FROM iem_data")
            bounds = cur.fetchone()
        lo, hi = bounds["lo"], bounds["hi"]
        if lo is None:
            logger.info("event=backfill_nothing_to_do")
            return
        total_rows = 0
        started = time.monotonic()
        for batch_lo in range(lo, hi + 1, args.batch_size):
            batch_hi = batch_lo + args.batch_size
            with conn.cursor() as cur:
                cur.execute(_BATCH_SQL, {"lo": batch_lo, "hi": batch_hi})
                inserted = cur.rowcount
            conn.commit()
            total_rows += inserted

            done = (batch_hi - lo) / (hi - lo +1)
            elapsed = time.monotonic() - started
            eta = elapsed / done - elapsed if done > 0 else 0
            logger.info("event=backfill_batch iem_id=%d-%d rows=%d total=%d "
                        "progress=%.1f%% eta_min=%.1f",
                        batch_lo, batch_hi -1, inserted, total_rows,
                        min(done, 1.0) * 100, eta / 60)

        with conn.cursor() as cur:
            cur.execute("ANALYZE report_zip_distances")
        conn.commit()
        logger.info("event=backfill_complete total_rows=%d minutes=%.1f",
                    total_rows, (time.monotonic() - started) / 60)


if __name__ == "__main__":
    main()