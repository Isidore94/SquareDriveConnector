from square_drive_connector.parser import parse_square_csv


CSV = b'''Dates,11:50 18 Sep. 2026,Place,Driving Ranges\n\nClub,Index,Ball Speed(mph),Launch Direction,Launch Angle,Spin Rate,Spin Axis,Back Spin,Side Spin,Apex(yd),Carry(yd),Total(yd),Offline(yd),Landing Angle,Club Path,Face Angle,Attack Angle,Dynamic Loft,ImpactHorizontal,ImpactVertical,ClubSpeed,SmashFactor\nDriver,0,86.5,R5.4,10.6,4077,L4.1,4067,L293,20.0,95.4,119.0,R6.6,19.5,L7.9,R8.0,-6.1,15.9,0.0,20.0,0.0,0.0\nAverage,,86.5,R5.4,10.6,4077,L4.1,4067,L293,20.0,95.4,119.0,R6.6,19.5,L7.9,R8.0,-6.1,15.9,0.0,20.0,0.0,0.0\nClub,Index,Ball Speed(mph),Launch Direction,Launch Angle,Spin Rate,Spin Axis,Back Spin,Side Spin,Apex(yd),Carry(yd),Total(yd),Offline(yd),Landing Angle,Club Path,Face Angle,Attack Angle,Dynamic Loft,ImpactHorizontal,ImpactVertical,ClubSpeed,SmashFactor\n7i,1,,L2.0,,,,,,,,0,,,,,,,,,0,0\nBad,2,1\n'''


def test_metadata_direction_zeros_and_summary_exclusion():
    parsed = parse_square_csv(CSV, "America/Vancouver")
    assert parsed.place == "Driving Ranges"
    assert parsed.original_timestamp_text == "11:50 18 Sep. 2026"
    assert parsed.session_time_iso.endswith("-07:00")
    assert len(parsed.shots) == 2
    shot = parsed.shots[0]
    assert shot.normalized["Launch Direction"] == 5.4
    assert shot.normalized["Spin Axis"] == -4.1
    assert shot.normalized["Offline(yd)"] == 6.6
    assert shot.normalized["ClubSpeed"] == 0.0
    assert "possible_placeholder_zero" in shot.warnings
    assert parsed.shots[1].club_normalized == "7i"
    assert parsed.shots[1].normalized["Ball Speed(mph)"] is None
    assert len(parsed.malformed) == 1


def test_unknown_field_and_invalid_format():
    data = b"Dates,not-a-date,Place,X\nClub,Index,New Metric\nDriver,1,wat\n"
    parsed = parse_square_csv(data, "America/Vancouver")
    assert parsed.shots[0].raw_values["New Metric"] == "wat"
    assert parsed.shots[0].normalized["New Metric"] is None
    assert parsed.session_time_iso is None
