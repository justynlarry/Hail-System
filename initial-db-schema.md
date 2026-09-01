 WEATHER SIDE                                      PROPERTY SIDE
 ════════════                                      ═════════════

 ┌──────────────────────┐                     ┌──────────────────────┐
 │ Report_Types         │                     │ Properties           │
 │──────────────────────│                     │──────────────────────│
 │ PK (REPORT_TYPE,     │                     │ PK RENTCAST_ID       │
 │     REPORT_TEXT)     │                     │    address, lat/lon  │
 │    ROOF_RELEVANT     │                     │    beds/baths/etc    │
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
                     │    STATUS         ││   │                 │ DNC_LIST             │
                     └───────────────────┼┼───┘                 │──────────────────────│
                                         ││                     │ PK DNC_ID            │
                                         │└────────┐            │ UQ EMAIL_NORM        │
                                         │         │            │ FK REALTOR_ID (null) │
                     ┌───────────────────▼──┐      │            │    SOURCE, REASON    │
                     │ Email_Templates      │      │            └──────────────────────┘
                     │──────────────────────│      │
                     │ PK TEMPLATE_ID       │      │            ┌──────────────────────┐
                     │ FK CREATED_BY ───────┼──┐   │            │ ZCTA_Boundaries      │
                     │ FK REPORT_TYPE(null) │  │   │            │──────────────────────│
                     │ FK SUPERSEDES_ID ─┐  │  │   │            │ PK ZCTA5             │
                     │    IS_ACTIVE      │  │  │   │            │    GEOM (multipoly)  │
                     └───────────────────┘  │  │   │            └──────────────────────┘
                          (self-ref)        │  │   │                   ╎
                                            │  │   │        spatial join only, no FK
                                            │  │   │            ╎  IEM_DATA.GEOM
                     ┌──────────────────────▼──▼───▼┐           ╎  Properties lat/lon
                     │ Users                        │           ╎
                     │──────────────────────────────│
                     │ PK EMP_ID                    │
                     │ FK CREATED_BY (self, null)   │
                     │    ROLE, IS_ACTIVE           │
                     │    PASSWORD_HASH             │
                     └───────────┬──────────────────┘
                                 │ 1
                                 │ N
                     ┌───────────┴────────────┐          ┌──────────────────────┐
                     │ API_Pulls              │    1     │ API_Call_Log         │
                     │────────────────────────│─────────►│──────────────────────│
                     │ PK PULL_ID             │      N   │ PK API_LOG_ID        │
                     │ FK EMP_ID              │          │ FK PULL_ID           │
                     │ FK IEM_ID (nullable)   │          │    ZIP_CODE          │
                     │    ESTIMATED_CALLS     │          │    CALLS_MADE        │
                     │    ACTUAL_CALLS        │          └──────────────────────┘
                     └────────────────────────┘
