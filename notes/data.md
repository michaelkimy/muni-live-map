# 511 / Muni data findings (Phase 1, 2026-11-06)

Based on a live VehicleMonitoring snapshot at about 8 pm on a Friday: 609 vehicles, 51 routes. Plus the GTFS feed from `datafeeds?operator_id=SF`.

## VehicleMonitoring response shape

`Siri.ServiceDelivery.VehicleMonitoringDelivery.VehicleActivity[]`. In this feed, `VehicleMonitoringDelivery` is a single **object**, not a list. The raw response is about 2.2 MB.

Per vehicle, under `MonitoredVehicleJourney`:

| Field | Path | Present | Notes |
|---|---|---|---|
| vehicle ID | `VehicleRef` | 609/609 | |
| lat/lon | `VehicleLocation.Latitude/Longitude` | 609/609 | **strings**; convert with `float()` |
| bearing | `Bearing` | 609/609 | string degrees; could rotate map markers |
| route | `LineRef` | 375/609 | matches GTFS `route_id` exactly |
| route name | `PublishedLineName` | 375/609 | e.g. "MARKET & WHARVES" |
| direction | `DirectionRef` | 375/609 | `IB`/`OB` for most routes, but **`N`/`S` on the T Third** |
| destination | `DestinationName` | 375/609 | |
| trip | `FramedVehicleJourneyRef.DatedVehicleJourneyRef` | 375/609 | matches GTFS `trips.txt` `trip_id`, which gives the exact `shape_id` |
| next stop | `MonitoredCall.StopPointName/StopPointRef` | 292/609 | ref matches GTFS `stop_id`; also has `ExpectedArrivalTime` |
| upcoming stops | `OnwardCalls.OnwardCall[]` | 292/609 | free arrival predictions, with no StopMonitoring call needed |
| occupancy | `Occupancy` | 609/609 | 600 of 609 say "seatsAvailable", so not useful |

### Gotchas
- **234 vehicles have no route.** All their route fields are `null`. These are likely out of service or deadheading. Filter them out of `/vehicles`.
- **83 in-service vehicles have no `MonitoredCall`.** Next stop is legitimately optional.
- **No per-vehicle GPS timestamp.** `RecordedAtTime` was identical for all 609 vehicles (the snapshot time). Phase 7's "updated X seconds ago" and stale-vehicle fading can't rely on it. Instead, track when each vehicle's position last *changed* between polls.
- Some fields hold empty strings `""` rather than null (`ValidUntilTime`, `VehicleLocationAtStop`).

## GTFS (static)

17 files, 74 MB unzipped (mostly `stop_times.txt`).

- `routes.txt`: 68 routes, with `route_color` and `route_text_color` hex (no `#`). `route_type` values:
  - `0` (tram/light rail): J K L M N T, **and F**. F is the historic streetcar; it needs a manual override to group it as "streetcar" in Phase 6.
  - `5` (cable car): CA, PH, PM
  - `3` (bus): the other 58, including variants like `14R`, `30X`, and `FBUS`
- `shapes.txt`: 45,014 points. Routes have 1–13 shapes each (one per branch and direction). Link shapes to routes through `trips.txt` (`route_id` → `shape_id`).
- `stops.txt`: 3,240 stops. Every live next-stop ID was found here.

## Implications for later phases
- Phase 3 `/vehicles` should drop vehicles without a route and convert lat/lon to floats. Consider including `bearing`.
- Phase 8: `OnwardCalls` already gives predictions for stops that vehicles are approaching. These could cover many stop clicks without spending StopMonitoring requests.

## StopMonitoring (Phase 8, 2026-11-06)

`transit/StopMonitoring?agency=SF&stopcode=<stop_id>&format=json`. Sample: `tests/fixtures/stop_sample.json`.

- **Different envelope from VehicleMonitoring:** there is **no `Siri` wrapper** (top level is `ServiceDelivery`) and **no BOM**. `app/parse.py` handles both shapes.
- `ServiceDelivery.StopMonitoringDelivery.MonitoredStopVisit[]`. Each visit has a full `MonitoredVehicleJourney` with `MonitoredCall` for this stop.
- Includes **trips that haven't started yet**, which have `VehicleRef: null`. The vehicle feed can't know about these.
- `MonitoredCall.DestinationDisplay` is the short headsign ("Ocean Beach"), nicer than `DestinationName` ("Ortega St & 48th Ave"). The vehicle feed's calls have it too.
- Times: `ExpectedArrivalTime` (live) and `AimedArrivalTime` (schedule). `ExpectedDepartureTime` is usually null.

### Feed coverage
In one snapshot, the vehicle feed's `MonitoredCall` + `OnwardCalls` covered **2,578 of 3,240 stops**, with an average of 23.6 onward calls per vehicle (max 91). Only 69 of 7,196 calls lacked an expected time. So most stop clicks cost no 511 request, and StopMonitoring is only the fallback.

### Feed gap: trips that haven't started (found 2026-11-06)
I compared the vehicle feed with StopMonitoring for the same stops at the same moment (05:04 UTC). For every vehicle both list, the expected times are **identical**. But the feed has **no entry for a trip until its bus starts the trip**. A bus waiting at its first stop (e.g. 7 #6607 at Transit Center Bay E) is either missing or shown without a route. So for stops near a terminal, the **soonest arrival could be missing**: Market & Stockton showed the next 7 as 24 min when one was 4 min away. StopMonitoring lists those trips with `VehicleRef: null`, using timetable times (within 1–2 min of GTFS `stop_times`).

Fix: `app/schedule.py` fills in timetable arrivals from GTFS for trips **not** in the live feed. Live trips are matched by `DatedVehicleJourneyRef` = GTFS `trip_id`, so nothing is listed twice. These arrivals are flagged `scheduled: true`. GTFS times have no leading zero (`7:15:00`) and go past 24:00 for after-midnight trips.
