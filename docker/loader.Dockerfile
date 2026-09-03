# Loader image.  The DB service stays on the stock postgis/postgis image --
# the container holding the data has no use for client tools and should not be
# rebuilt to add a one-shot utility.
#
# Why this image exists: postgis/postgis:16-3.4 ships only the server-side
# extension (postgresql-16-postgis-3).  shp2pgsql lives in the separate
# `postgis` client package, which is present in the PGDG repo the base image
# already has configured -- but the base clears /var/lib/apt/lists, so a bare
# apt-get install reports "unable to locate package" and looks exactly like the
# package does not exist.  The apt-get update below is the whole fix.
#
# Client 3.5.x against a 3.4 server is fine: shp2pgsql is a standalone
# converter that emits SQL text and never links against the server.  Pinned
# anyway so a PGDG refresh cannot change the loader underneath us.

FROM postgis/postgis:16-3.4

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        postgis=3.5.2+dfsg-1.pgdg110+1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
