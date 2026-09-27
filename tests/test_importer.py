"""scripts/import_data.py's rules: adds and updates, matched the way people
name things, never deletes, and refuses a bad file row by row."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.importer import ImportRefused, import_rows
from app.models import Admin, Property, Staff, Tenant, Unit


def run(session: Session, kind: str, *lines: str):
    header, *body = [line.split(",") for line in lines]
    return import_rows(session, kind, header, [dict(zip(header, cells, strict=True)) for cells in body])  # type: ignore[arg-type]


def units(session: Session):
    return run(
        session,
        "units",
        "building,address,unit,bedrooms,bathrooms,status",
        "Sunset Towers,12 Harbour Road,,,,",
        "Sunset Towers,,0101,2,2,",
        "Sunset Towers,,0102,1,1,under-maintenance",
    )


def test_units_add_a_building_and_its_units(session: Session):
    report = units(session)

    building = session.scalars(select(Property)).one()
    assert (building.id, building.name, building.address) == ("prop-sunset-towers", "Sunset Towers", "12 Harbour Road")
    by_label = {u.label: u for u in session.scalars(select(Unit))}
    assert by_label["0101"].id == "unit-st-0101"
    assert (by_label["0101"].status, by_label["0102"].status) == ("vacant", "under-maintenance")
    assert len(report.added) == 3 and not report.updated


def test_the_same_file_twice_changes_nothing(session: Session):
    units(session)
    report = units(session)

    assert (report.added, report.updated, report.unchanged) == ([], [], 3)
    assert session.scalar(select(func.count()).select_from(Unit)) == 2


def test_a_blank_cell_keeps_the_field_and_a_filled_one_updates_it(session: Session):
    units(session)
    report = run(session, "units", "building,unit,bedrooms,bathrooms", "sunset towers,0101,3,")

    unit = session.scalars(select(Unit).where(Unit.label == "0101")).one()
    assert (unit.bedrooms, unit.bathrooms) == (3, 2)
    assert report.updated == ["row 2: unit 0101 in Sunset Towers: bedrooms 2 → 3"]


def test_tenants_are_matched_by_phone_and_moved_in(session: Session):
    units(session)
    run(session, "tenants", "name,phone,email,building,unit", "Sara Nasser,+000 000 3001,,Sunset Towers,0101")
    # The same phone written differently is the same person.
    report = run(session, "tenants", "name,phone,email", "Sara Nasser,+0000003001,sara@example.com")

    tenant = session.scalars(select(Tenant)).one()
    assert (tenant.id, tenant.email) == ("ten-nasser", "sara@example.com")
    unit = session.scalars(select(Unit).where(Unit.label == "0101")).one()
    assert (unit.tenant_id, unit.status) == ("ten-nasser", "occupied")
    assert report.updated == ["row 2: tenant Sara Nasser: email None → sara@example.com"]


def test_moving_a_tenant_in_replaces_the_one_there(session: Session):
    units(session)
    run(session, "tenants", "name,phone,building,unit", "Sara Nasser,+000 000 3001,Sunset Towers,0101")
    report = run(session, "tenants", "name,phone,building,unit", "Karim Aziz,+000 000 3002,Sunset Towers,0101")

    assert report.added == ["row 2: tenant Karim Aziz (moved into unit 0101, replacing Sara Nasser)"]
    # Sara isn't deleted: only the unit changed hands.
    assert session.scalar(select(func.count()).select_from(Tenant)) == 2


def test_staff_and_admins_are_added_and_can_sign_in_by_phone(session: Session):
    run(session, "staff", "name,phone,trade", "Hassan Ali,+000 000 3101,maintenance")
    run(session, "admins", "name,phone,portal", "Nadia Rahman,+000 000 3201,ops", "Nadia Rahman,+000 000 3201,housekeeping")

    member = session.scalars(select(Staff)).one()
    assert (member.id, member.role) == ("stf-ali", "maintenance")
    assert sorted((a.id, a.trade) for a in session.scalars(select(Admin))) == [
        ("adm-rahman", "maintenance"),
        ("adm-rahman-2", "housekeeping"),
    ]


def test_a_bad_file_is_refused_row_by_row(session: Session):
    units(session)
    with pytest.raises(ImportRefused) as refused:
        run(
            session,
            "tenants",
            "name,phone,building,unit",
            "Sara Nasser,not a phone,Sunset Towers,0101",
            "Karim Aziz,+000 000 3002,Nowhere,0101",
            "Mona Said,+000 000 3003,Sunset Towers,",
            "Omar Hadi,+000 000 3004,,",
            "Omar Hadi,+000 000 3004,,",
        )

    assert refused.value.problems == [
        "Row 2: 'not a phone' isn't a phone number",
        "Row 3: no building called 'Nowhere' (import it in a units file first)",
        "Row 4: give both building and unit, or neither",
        "Row 6: The phone +000 000 3004 is already in an earlier row",
    ]


def test_unknown_and_missing_columns_are_refused(session: Session):
    with pytest.raises(ImportRefused) as refused:
        run(session, "staff", "name,phnoe", "Hassan Ali,+000 000 3101")

    assert refused.value.problems[:3] == [
        "Unknown column 'phnoe'",
        "Missing column 'phone'",
        "Missing column 'trade'",
    ]


def test_a_new_building_needs_an_address_and_a_new_unit_its_rooms(session: Session):
    with pytest.raises(ImportRefused) as refused:
        run(session, "units", "building,address,unit,bedrooms,bathrooms", "Sunset Towers,,0101,2,2", "Palm Court,1 Palm St,0101,2,")

    assert refused.value.problems == [
        "Row 2: 'Sunset Towers' is a new building: give its address",
        "Row 3: unit 0101 is new: give its bedrooms and bathrooms",
    ]


def test_a_unit_with_a_tenant_cant_be_made_vacant(session: Session):
    units(session)
    run(session, "tenants", "name,phone,building,unit", "Sara Nasser,+000 000 3001,Sunset Towers,0101")
    with pytest.raises(ImportRefused, match="has a tenant, so it can't be vacant"):
        run(session, "units", "building,unit,status", "Sunset Towers,0101,vacant")


def test_clearing_empties_the_operation_and_keeps_the_rate_card(session: Session):
    from app.importer import clear_operations
    from app.models import HousekeepingRate

    session.add(HousekeepingRate(service_type="standard-clean", label="Standard clean", price=120, position=0))
    units(session)
    run(session, "staff", "name,phone,trade", "Hassan Ali,+966 50 000 3101,maintenance")

    counts = clear_operations(session)

    assert counts["units"] == 2 and counts["staff"] == 1
    assert session.scalar(select(func.count()).select_from(Unit)) == 0
    assert session.scalar(select(func.count()).select_from(HousekeepingRate)) == 1


def test_a_phone_needs_its_country_code(session: Session):
    with pytest.raises(ImportRefused) as refused:
        run(session, "staff", "name,phone,trade", "Hassan Ali,050 123 4567,maintenance")

    assert refused.value.problems == ["Row 2: '050 123 4567' needs its country code, e.g. +966 50 123 4567"]


def test_a_phone_without_its_plus_is_taken_as_international(session: Session):
    # What a spreadsheet leaves of +966 55 781 5947, and the 00 way of writing it.
    run(session, "staff", "name,phone,trade", "Hassan Ali,966557815947,maintenance", "Mei Lin,00966557815948,housekeeping")

    assert sorted(session.scalars(select(Staff.phone))) == ["+966557815947", "+966557815948"]


def test_a_leading_unit_is_dropped_from_labels(session: Session):
    run(session, "units", "building,address,unit,bedrooms,bathrooms", "Mushrifah 1,Mushrifah,Unit 10,2,1")
    run(session, "tenants", "name,phone,building,unit", "Sara Nasser,+966 50 000 3001,Mushrifah 1,unit 10")

    unit = session.scalars(select(Unit)).one()
    assert (unit.id, unit.label, unit.tenant_id) == ("unit-m1-10", "10", "ten-nasser")
