""" Raw RentCast listing dicts: properties/listings/realtors,
answers 'what's for sale, storm_listing_matches matches it to
an actual storm

_norm columns (email_norm, list_agent_email_norm, etc.) are DB-generated
(lower(trim(...)))
"""

import logging
import math
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

_UPSERT_PROPERTY_SQL = """
INSERT INTO properties
    (rentcast_id, property_address, address_1, address_2, city, state,
    zip_code, county, state_fips, county_fips, list_latitude,
     list_longitude, property_type, bedrooms, bathrooms, square_footage,
      lot_size, year_built, hoa_dues, created_date, first_seen_at)
VALUES
    (%(rentcast_id)s, %(property_address)s, %(address_1)s, %(address_2)s,
     %(city)s, %(state)s, %(zip_code)s, %(county)s, %(state_fips)s,
     %(county_fips)s, %(list_latitude)s, %(list_longitude)s,
     %(property_type)s, %(bedrooms)s, %(bathrooms)s, %(square_footage)s,
     %(lot_size)s, %(year_built)s, %(hoa_dues)s, %(created_date)s, now())
ON CONFLICT (rentcast_id) DO UPDATE SET
    property_address= EXCLUDED.property_address,
    address_1 = EXCLUDED.address_1, address_2 = EXCLUDED.address_2,
    city = EXCLUDED.city, state = EXCLUDED.state,
    zip_code = EXCLUDED.zip_code, county = EXCLUDED.county,
    state_fips = EXCLUDED.state_fips, county_fips = EXCLUDED.county_fips,
    list_latitude = EXCLUDED.list_latitude,
    list_longitude = EXCLUDED.list_longitude,
    property_type = EXCLUDED.property_type, bedrooms = EXCLUDED.bedrooms,
    bathrooms = EXCLUDED.bathrooms, square_footage = EXCLUDED.square_footage,
    lot_size = EXCLUDED.lot_size, year_built = EXCLUDED.year_built,
    hoa_dues = EXCLUDED.hoa_dues
    -- first_seen_at/created_date untouched because both record something
    -- first happend, should not be overwritten     
"""

_UPSERT_REALTOR_SQL = """
INSERT INTO realtors
    (agent_name, agent_email, agent_phone, agent_office_name,
    agent_office_phone, agent_office_email, first_seen_at, last_seen_at)
VALUES
    (%(agent_name)s, %(agent_email)s, %(agent_phone)s,
     %(agent_office_name)s, %(agent_office_phone)s, %(agent_office_email)s,
     now(), now())
ON CONFLICT (email_norm) WHERE email_norm IS NOT NULL DO UPDATE SET
    agent_name = EXCLUDED.agent_name, agent_phone = EXCLUDED.agent_phone,
    agent_office_name = EXCLUDED.agent_office_name,
    agent_office_phone = EXCLUDED.agent_office_phone,
    agent_office_email = EXCLUDED.agent_office_email,
    last_seen_at = now()
RETURNING realtor_id
"""

_UPSERT_LISTING_SQL = """
INSERT INTO listings
    (rentcast_id, list_date, list_status, list_price, list_type,
     list_removed_date, list_last_seen, list_days_on_market,
     list_mls_name, list_mls_number, list_agent_name, list_agent_phone,
     list_agent_email, list_office_name, list_office_phone,
     list_office_email, list_office_website, realtor_id, raw_payload,
     created_date, first_seen_at)
VALUES
    (%(rentcast_id)s, %(list_date)s, %(list_status)s, %(list_price)s,
     %(list_type)s, %(list_removed_date)s, %(list_last_seen)s,
     %(list_days_on_market)s, %(list_mls_name)s, %(list_mls_number)s,
     %(list_agent_name)s, %(list_agent_phone)s, %(list_agent_email)s,
     %(list_office_name)s, %(list_office_phone)s, %(list_office_email)s,
     %(list_office_website)s, %(realtor_id)s, %(raw_payload)s,
     %(created_date)s, now())
ON CONFLICT (rentcast_id, list_date) DO UPDATE SET
    list_status = EXCLUDED.list_status, list_price = EXCLUDED.list_price,
    list_type = EXCLUDED.list_type,
    list_removed_date = EXCLUDED.list_removed_date,
    list_last_seen = EXCLUDED.list_last_seen,
    list_days_on_market = EXCLUDED.list_days_on_market,
    list_mls_name = EXCLUDED.list_mls_name,
    list_mls_number = EXCLUDED.list_mls_number,
    list_agent_name = EXCLUDED.list_agent_name,
    list_agent_phone = EXCLUDED.list_agent_phone,
    list_agent_email = EXCLUDED.list_agent_email,
    list_office_name = EXCLUDED.list_office_name,
    list_office_phone = EXCLUDED.list_office_phone,
    list_office_email = EXCLUDED.list_office_email,
    list_office_website = EXCLUDED.list_office_website,
    realtor_id = EXCLUDED.realtor_id, raw_payload = EXCLUDED.raw_payload
"""

# What each column can hold.  RentCast data is not always sane, and a value
# that doesn't fit raises NumericValueOutOfRange, which rolls back the whole
# zip and ends the pull after its calls are spent
_FIT = {
    "bathrooms": (0, 99.9),     # properties.bathrooms  NUMERIC(3,1)
    "bedrooms": (0, 32767),     # properties.bedrooms   SMALLINT
    "yearBuilt": (0,32767),     # properties.year_built SMALLINT
}

def _fits(item, field):
    value = item.get(field)
    if value is None:
        return None
    low, high = _FIT[field]
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if not (math.isfinite(number) and low <= number <= high):
        logger.warning("event=field_out_of_range rentcast_id=%s field=%s value=%s",
                       item.get("id"), field, value)
        return None
    return value


def upsert_listings(conn, raw_listings: list[dict]) -> None:
    for item in raw_listings:
        _upsert_one(conn, item)
    conn.commit()

def _upsert_one(conn, item:dict) -> None:
    rentcast_id = item.get("id")
    if not rentcast_id:
        logger.error("event=listing_missing_id item=%r", item)
        return

    hoa = item.get("hoa") or {}
    with conn.cursor() as cur:
        cur.execute(_UPSERT_PROPERTY_SQL, {
            "rentcast_id": rentcast_id,
            "property_address": item.get("formattedAddress"),
            "address_1": item.get("addressLine1"),
            "address_2": item.get("addressLine2"),
            "city": item.get("city"),
            "state": item.get("state"),
            "zip_code": item.get("zipCode"),
            "county": item.get("county"),
            "state_fips": item.get("stateFips"),
            "county_fips": item.get("countyFips"),
            "list_latitude": item.get("latitude"),
            "list_longitude": item.get("longitude"),
            "property_type": item.get("propertyType"),
            "bedrooms": _fits(item, "bedrooms"), 
            "bathrooms": _fits(item, "bathrooms"),
            "square_footage": item.get("squareFootage"),
            "lot_size": item.get("lotSize"), "year_built": _fits(item, "yearBuilt"),
            "hoa_dues": hoa.get("fee"), "created_date": item.get("createdDate"),
        })

    agent = item.get("listingAgent") or {}
    office = item.get("listingOffice") or {}
    realtor_id = None
    agent_email = agent.get("email")
    if agent_email:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_REALTOR_SQL, {
                "agent_name": agent.get("name"), "agent_email": agent_email,
                "agent_phone": agent.get("phone"),
                "agent_office_name": office.get("name"),
                "agent_office_phone": office.get("phone"),
                "agent_office_email": office.get("email"),
            })
            realtor_id = cur.fetchone()["realtor_id"]

    with conn.cursor() as cur:
        cur.execute(_UPSERT_LISTING_SQL, {
            "rentcast_id": rentcast_id, "list_date": item.get("listedDate"),
            "list_status": item.get("status"), "list_price": item.get("price"),
            "list_type": item.get("listingType"),
            "list_removed_date": item.get("removedDate"),
            "list_last_seen": item.get("lastSeenDate"),
            "list_days_on_market": item.get("daysOnMarket"),
            "list_mls_name": item.get("mlsName"),
            "list_mls_number": item.get("mlsNumber"),
            "list_agent_name": agent.get("name"),
            "list_agent_phone": agent.get("phone"),
            "list_agent_email": agent_email,
            "list_office_name": office.get("name"),
            "list_office_phone": office.get("phone"),
            "list_office_email": office.get("email"),
            "list_office_website": office.get("website"),
            "realtor_id": realtor_id, "raw_payload": Jsonb(item),
            "created_date": item.get("createdDate"),
        })    
