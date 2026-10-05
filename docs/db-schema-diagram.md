 WEATHER SIDE                                      PROPERTY SIDE
 ════════════                                      ═════════════

 ┌──────────────────────┐                     ┌──────────────────────┐
 │ Report_Types         │                     │ Properties           │
 │──────────────────────│                     │──────────────────────│
 │ PK (REPORT_TYPE,     │                     │ PK RENTCAST_ID       │
 │     REPORT_TEXT)     │                     │    address, lat/lon  │
 │    ROOF_RELEVANT     │                     │    ZIP_CODE          │
 └──────────┬───────────┘                     └──────────┬───────────┘
            │ 1                                          │ 1
            │                                            │
            │ N                                          │ N
 ┌──────────┴───────────┐                     ┌──────────┴───────────┐
 │ IEM_DATA             │                     │ Listings             │
 │──────────────────────│                     │──────────────────────│
 │ PK IEM_ID            │                     │ PK LISTING_ID        │
 │ FK REPORT_TYPE,      │                     │ FK RENTCAST_ID       │
 │    REPORT_TEXT       │                     │ FK REALTOR_ID  ──────┼──┐
 │    GEOM (point)      │                     │ UQ (RENTCAST_ID,     │  │
 │    MAGNITUDE         │                     │     LIST_DATE)       │  │
 │ UQ (datetime,lat,lon,│                     │    RAW_PAYLOAD       │  │
 │     type,mag)        │                     └──────────┬───────────┘  │
 └──────────┬───────────┘                                │ 1            │
            │ 1                                          │              │
            │        ┌────────────────────────┐          │              │
            │   N    │ Storm_Listing_Matches  │    N     │              │
            └───────►│────────────────────────│◄─────────┘              │
                     │ PK MATCH_ID            │                         │
                     │ FK IEM_ID              │                         │
                     │ FK LISTING_ID          │                         │
                     │    DISTANCE_MILES      │                         │
                     │    RADIUS_USED         │                         │
                     │ UQ (IEM,LIST,RADIUS)   │                         │
                     └───────────┬────────────┘                         │
                                 │ 1                                    │
                                 │                                      │
                                 │ N                            ┌───────▼──────────────┐
                     ┌───────────┴────────────┐                 │ Realtors             │
                     │ Send_Log               │      N          │──────────────────────│
                     │────────────────────────│────────────────►│ PK REALTOR_ID        │
                     │ PK SEND_ID             │             1   │ UQ EMAIL_NORM        │
                     │ FK MATCH_ID            │                 │    AGENT_NAME        │
                     │ FK REALTOR_ID          │                 └───────▲──────────────┘
                     │ FK TEMPLATE_ID  ───┐   │                         │ 1
                     │ FK SENT_BY      ──┐│   │                         │
                     │    RECIPIENT_EMAIL││   │                         │ N (nullable)
                     │    PROVIDER_MSG_ID││   │                 ┌───────┴──────────────┐
                     │    SEND_STATUS    ││   │                 │ DNC_LIST             │
                     └───────────────────┼┼───┘                 │──────────────────────│
                                         ││                     │ PK DNC_ID            │
                                         │└────────┐            │ UQ EMAIL_NORM        │
                                         │         │            │ FK REALTOR_ID (null) │
                     ┌───────────────────▼──┐      │            │    SOURCE, REASON    │
                     │ Email_Templates      │      │            │    REMOVED_AT/_BY    │
                     │──────────────────────│      │            └──────────────────────┘
                     │ PK TEMPLATE_ID       │      │
                     │ FK CREATED_BY ───────┼──┐   │       REFERENCE DATA
                     │ FK REPORT_TYPE(null) │  │   │       ══════════════
                     │ FK SUPERSEDES_ID ─┐  │  │   │
                     │    IS_ACTIVE      │  │  │   │    ┌──────────────────────┐
                     └───────────────────┘  │  │   │    │ ZCTA_Boundaries      │
                          (self-ref)        │  │   │    │──────────────────────│
                                            │  │   │    │ PK ZCTA5             │
                                            │  │   │    │    GEOM (multipoly)  │
                     ┌──────────────────────▼──▼───▼┐   └──────────┬───────────┘
                     │ Users                        │              │ 1
                     │──────────────────────────────│              │
                     │ PK EMP_ID                    │              │ N   FK ZCTA5
                     │ FK CREATED_BY (self, null)   │   ┌──────────┴───────────┐
                     │    ROLE, IS_ACTIVE           │   │ Coverage_Zips        │
                     │    PASSWORD_HASH             │   │──────────────────────│
                     └───┬────────────────────┬─────┘   │ PK ZCTA5             │
                         │ 1                  │ 1       │    AREA_NAME, REASON │
                         │                    │      N  │ FK ADDED_BY          │
                         │ N                  └────────►│ FK REMOVED_BY (null) │
             ┌───────────┴────────────┐                 │ CK removal complete  │
             │ API_Pulls              │    1            └──────────────────────┘
             │────────────────────────│───┐
             │ PK PULL_ID             │   │             ╎ ╎
             │ FK EMP_ID              │   │   spatial join only, no FK
             │ FK IEM_ID (nullable)   │   │             ╎ ╎
             │    ESTIMATED_API_CALLS │   │   IEM_DATA.GEOM ──► ZCTA_Boundaries.GEOM
             │    ACTUAL_API_CALLS    │   │   Properties lat/lon ──► ZCTA_Boundaries
             └────────────────────────┘   │
                                          │ N
                              ┌───────────▼──────────┐
                              │ API_Call_Log         │
                              │──────────────────────│
                              │ PK API_LOG_ID        │
                              │ FK PULL_ID           │
                              │    ZIP_CODE          │
                              │    CALLS_MADE        │
                              └──────────────────────┘


 INGEST OPERATIONS
 ═════════════════

 Free-standing. No user, no storm — nothing here points at IEM_DATA or USERS.

                              ┌──────────────────────┐
                              │ Ingest_Runs          │
                              │──────────────────────│
                              │ PK RUN_ID            │
                              │    RUN_MODE          │
                              │    WINDOW_START/_END │
                              │    STARTED/FINISHED  │
                              │    ROWS_SEEN         │
                              │    ROWS_INSERTED     │
                              │    ROWS_SKIPPED      │
                              │    RUN_STATUS        │
                              │ CK window_ordered    │
                              │ CK complete_has_cnts │
                              │ CK counts_consistent │
                              └───────────┬──────────┘
                                          │ 1
                                          │
                                          │ N   FK RUN_ID
                              ┌───────────┴──────────┐
                              │ IEM_Ingest_Rejects   │
                              │──────────────────────│
                              │ PK REJECT_ID         │
                              │ FK RUN_ID            │
                              │    RAW_ROW  (text)   │
                              │    REASON   (CK enum)│
                              │    DETAIL            │
                              └──────────────────────┘


 Coverage_Zips is reference data and a filter on query output, not a link in
 the storm→match chain. It has no relationship to IEM_DATA. A storm reaches it
 only through ZCTA_Boundaries geometry, and only at read time:

     iem_data ──spatial──► zcta_boundaries ──equality──► coverage_zips

 Enforcement point is the RentCast pull. Ingest stays unfiltered.

 Ingest_Runs is written by the nightly job, not by a person, so it carries no
 EMP_ID — unlike API_Pulls, which exists to attribute spend to a human. And it
 has no FK to IEM_DATA: a run is an event in the life of the script, not a
 property of any report it happened to insert. The rows it did insert are
 reachable by timestamp, not by key.

 The alert is the ABSENCE of a row. That is why this is a table.


 ADDED SINCE PHASE 1  (sql/012 – sql/023, drawn 2026-09-24)
 ═══════════════════

 New columns on tables drawn above, not repeated as boxes:
   Users.SESSIONS_INVALIDATED_AT (018)     API_Pulls.STORM_DATE, REPORT_TEXT (013)
   Properties.GEOM, generated (014)        Storm_Listing_Matches.EMP_ID (015)

 ┌──────────────────────┐                 ┌──────────────────────────┐
 │ Report_Zip_Distances │  017            │ Match_Runs               │  022
 │──────────────────────│                 │──────────────────────────│
 │ PK (IEM_ID, ZCTA5)   │                 │ PK MATCH_RUN_ID          │
 │    DISTANCE_M        │                 │ FK EMP_ID ──► Users      │
 └──────────────────────┘                 │    STORM_DATE,           │
   IEM_ID ──► IEM_DATA   (FK)             │    REPORT_TEXT           │
   ZCTA5  ╌╌  no FK: it would block a     │    RADIUS_MILES          │
             TIGER reload                 │    MATCHES_CREATED (new) │
   filled by AFTER INSERT trigger on      │    RUN_STATUS            │
   IEM_DATA; derived, rebuildable         │ CK finished_has_timestamp│
                                          └──────────────────────────┘
                                            No FK to Storm_Listing_Matches:
                                            a run that matched nothing still
                                            leaves this row. Joined to storm
                                            days by (STORM_DATE, REPORT_TEXT),
                                            like API_Pulls.

 ┌──────────────────────┐                 ┌──────────────────────────┐
 │ Settings             │  018/020/023    │ Settings_History         │  020/023
 │──────────────────────│                 │──────────────────────────│
 │ PK ID  (CK id = 1)   │   AFTER UPDATE  │ PK HISTORY_ID            │
 │    GLOBAL_SESSIONS_  │   OF 5 columns  │ FK CHANGED_BY ──► Users  │
 │      INVALIDATED_AT  │ ───trigger────► │    both radii            │
 │    ZIP / MATCH RADIUS│                 │    BILLING_DAY, QUOTA    │
 │    BILLING_DAY, QUOTA│                 │      (NULL before 023)   │
 │ CK match <= zip      │                 └──────────────────────────┘
 └──────────────────────┘
   one row; no FK to anything. The trigger ignores
   GLOBAL_SESSIONS_INVALIDATED_AT on purpose.

 Users ◄── trg_last_admin (019): deferred constraint trigger, refuses any
           COMMIT that leaves no active admin.


 REFERENCE DATA, joined spatially or by value (no FK from the data they describe)
 ═══════════════════════════════════════════════════════════════════════════════

 ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
 │ County_Boundaries    │   │ Municipal_Boundaries │   │ Report_Sources       │
 │──────────────────────│   │──────────────────────│   │──────────────────────│
 │ PK COUNTY_FIPS       │   │ PK PLACE_FIPS        │   │ PK SOURCE            │
 │    STATE_FIPS, NAME  │   │    NAME, GEOM        │   │    CONFIDENCE_TIER   │
 │    GEOM (multipoly)  │   │    SOURCE_ATTRS      │   │    IS_AUTOMATED      │
 │                      │   │                      │   │ FK ADDED_BY,         │
 │                      │   │                      │   │    REMOVED_BY ► Users│
 └──────────────────────┘   └──────────────────────┘   └──────────────────────┘
   012, TIGER              021, DOLA; parked            003; joined on
                           permits work only            IEM_DATA.REPORT_SOURCE_NORM,
                                                        never an FK (free text
                                                        would break ingest)


 ADDED SINCE sql/023  (sql/024 – sql/033, drawn 2026-10-05)
 ═══════════════════

 New columns on tables drawn above, not repeated as boxes:
   Properties.ADDRESS_KEY (024/025/027)  generated from PROPERTY_ADDRESS by the
                                         address_key() function; partial index,
                                         NULL when there is no house number
   Settings.LISTING_FRESHNESS_DAYS (028) and the same column on Settings_History;
                                         the history trigger now watches 5 columns

 ┌──────────────────────────┐          ┌──────────────────────────┐
 │ DNC_Import_Batches       │   1      │ DNC_Import_Rows          │  026
 │──────────────────────────│─────────►│──────────────────────────│
 │ PK BATCH_ID              │       N  │ PK (BATCH_ID, LINE_NO)   │
 │ UQ TOKEN                 │          │ FK BATCH_ID  ON DELETE   │
 │ FK UPLOADED_BY ► Users   │          │    CASCADE               │
 │    FILENAME, ROW_COUNT   │          │    EMAIL_RAW, REJECTED   │
 │    UPLOADED_AT           │          │    NAME_AT_ADD, ADDED_AT │
 │    COMMITTED_AT (null)   │          └──────────────────────────┘
 └──────────────────────────┘
   Staging for the /admin DNC upload. Committing a batch inserts into DNC_LIST;
   these two tables hold the upload until then. No FK to DNC_LIST.

 ┌──────────────────────────┐          ┌──────────────────────────┐
 │ Geocode_Cache            │   1      │ Address_Searches         │  031
 │──────────────────────────│─────────►│──────────────────────────│
 │ PK GEOCODE_ID            │       N  │ PK SEARCH_ID             │
 │ UQ ADDRESS_KEY           │ (null)   │ FK GEOCODE_ID (null)     │
 │    LAT/LON, GEOM (gen.)  │          │ FK SEARCHED_BY ► Users   │
 │    MATCHED_ADDRESS       │          │    QUERY_RAW, ADDRESS_KEY│
 │    TIGER_LINE_ID         │          │    OUTCOME (CK enum)     │
 │    GEOCODED_AT           │          │    REPORTS_FOUND         │
 └──────────────────────────┘          │    RANGE_START/_END      │
   Cache of Census Geocoder results,   └──────────────────────────┘
   keyed on address_key(). Searches    Every search is logged, misses included.
   joined to storms spatially, no FK.

 Triggers that enforce append-only (029, 030), not drawn as boxes:

   Send_Log ◄──────── send_log_guard(): refuses DELETE; an UPDATE may change
                      only SEND_STATUS, STATUS_UPDATED_AT, PROVIDER_MESSAGE_ID,
                      ERROR_DETAIL, SENT_AT (write-once); status moves forward
                      only.
   Email_Templates ◄─ email_templates_guard(): refuses DELETE; the only UPDATE
                      allowed is IS_ACTIVE true -> false.

   DELETE and TRUNCATE are revoked from hail_app on both. The table owner can
   still TRUNCATE (accepted, decision log 2026-09-30).

 Indexes and roles, not tables:
   API_Pulls: partial unique index (STORM_DATE, REPORT_TEXT) WHERE running (033)
   hail_app, hail_ingest: search_path is "$user", public (032)
