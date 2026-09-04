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
