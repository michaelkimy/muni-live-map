from app.parse import decode_511, parse_vehicles


def test_decode_strips_bom(vehicles_raw):
    assert vehicles_raw.startswith(b"\xef\xbb\xbf")
    assert "Siri" in decode_511(vehicles_raw)


def test_no_route_vehicles_are_dropped(vehicles_raw):
    vehicles = parse_vehicles(decode_511(vehicles_raw))
    # fixture: 18 vehicles, 3 of them without a route
    assert len(vehicles) == 15
    assert all(v["route"] for v in vehicles)


def test_fields_and_types(vehicles_raw):
    v = parse_vehicles(decode_511(vehicles_raw))[0]
    assert set(v) == {"id", "route", "direction", "destination", "lat", "lon",
                      "bearing", "next_stop_id", "next_stop_name"}
    assert isinstance(v["lat"], float) and isinstance(v["lon"], float)
    assert isinstance(v["bearing"], float)
    assert 37.6 < v["lat"] < 37.9 and -122.6 < v["lon"] < -122.3


def test_missing_monitored_call_gives_null_next_stop(vehicles_raw):
    vehicles = parse_vehicles(decode_511(vehicles_raw))
    no_stop = [v for v in vehicles if v["next_stop_id"] is None]
    assert no_stop, "fixture should include in-service vehicles without MonitoredCall"
    assert all(v["next_stop_name"] is None for v in no_stop)


def test_t_third_uses_n_s_directions(vehicles_raw):
    t = [v for v in parse_vehicles(decode_511(vehicles_raw)) if v["route"] == "T"]
    assert t and {v["direction"] for v in t} <= {"N", "S"}


def test_single_object_and_list_delivery_both_work():
    activity = {"MonitoredVehicleJourney": {
        "LineRef": "38", "VehicleRef": "1", "DestinationName": ["Ocean Beach"],
        "VehicleLocation": {"Latitude": "37.78", "Longitude": "-122.45"}}}
    for delivery in ({"VehicleActivity": activity}, [{"VehicleActivity": [activity]}]):
        siri = {"Siri": {"ServiceDelivery": {"VehicleMonitoringDelivery": delivery}}}
        (v,) = parse_vehicles(siri)
        assert v["destination"] == "Ocean Beach" and v["bearing"] is None


def test_empty_or_bad_position_is_dropped():
    activity = {"MonitoredVehicleJourney": {
        "LineRef": "38", "VehicleRef": "1", "VehicleLocation": {"Latitude": "", "Longitude": None}}}
    siri = {"Siri": {"ServiceDelivery": {"VehicleMonitoringDelivery": {"VehicleActivity": [activity]}}}}
    assert parse_vehicles(siri) == []
