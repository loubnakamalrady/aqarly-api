"""Bringing real buildings, units, tenants, staff and admins in from CSV files,
in place of the demo data (run by scripts/import_data.py).

One file per kind, one row per record, first row the column names:

  units     building, address, unit, bedrooms, bathrooms, status
  tenants   name, phone, email, building, unit
  staff     name, phone, trade
  admins    name, phone, portal

It adds what's new and updates what's there; it never deletes. A record is
matched by what identifies it to people rather than by an id: a building by
its name, a unit by its building and label, a person by their phone (digits
only, so "+971 50 123 4567" and "+971501234567" are the same), an admin by
phone and portal. A blank cell leaves that field as it is, so a file can carry
just the columns being changed. Importing the same file twice changes nothing
the second time.

All or nothing: every row is checked, and any problem refuses the whole file
with the row it's on. The caller commits only a clean import.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Admin, Base, Property, Staff, Tenant, Unit
from app.models.enums import RequestType, UnitStatus
from app.services.admin import _staff_id
from app.services.auth import _tenant_id

Kind = Literal["units", "tenants", "staff", "admins"]
KINDS: tuple[Kind, ...] = get_args(Kind)

# (required, optional) columns per kind. A column not listed is refused, so a
# typo ("phnoe") is caught rather than quietly ignored.
COLUMNS: dict[Kind, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "units": (("building",), ("address", "unit", "bedrooms", "bathrooms", "status")),
    "tenants": (("name", "phone"), ("email", "building", "unit")),
    "staff": (("name", "phone", "trade"), ()),
    "admins": (("name", "phone", "portal"), ()),
}

# The portal an admin runs, by the trade it manages (as in services/auth.py).
PORTALS: dict[str, RequestType] = {"ops": "maintenance", "housekeeping": "housekeeping"}
TRADES: tuple[str, ...] = get_args(RequestType)
STATUSES: tuple[str, ...] = get_args(UnitStatus)


# What clearing keeps: the rate card (housekeeping's prices, not people) and
# the marketing site's listings. Everything else is the operation's own data.
KEPT_TABLES = ("housekeeping_rates", "listings")


def clear_operations(session: Session) -> dict[str, int]:
    """Empties buildings, units, tenants, staff, admins, requests, sign-ins and
    registrations, ready for real data to be imported in their place. Returns
    how many rows each table had. The caller commits; rolling back puts every
    row back (TRUNCATE is transactional in Postgres)."""
    tables = [t for t in Base.metadata.sorted_tables if t.name not in KEPT_TABLES]
    counts = {t.name: session.scalar(select(func.count()).select_from(t)) or 0 for t in tables}
    session.execute(text("TRUNCATE " + ", ".join(f'"{t.name}"' for t in tables)))
    return counts


class ImportRefused(Exception):
    """The file can't be imported as it is. Nothing was changed."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


class RowProblem(Exception):
    """One row's problem, collected with its row number."""


@dataclass
class Report:
    """What an import did, a line per record it added or changed."""

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0


def import_rows(session: Session, kind: Kind, header: list[str], rows: list[dict[str, str]]) -> Report:
    """Checks and applies one file's rows. Raises ImportRefused, having
    possibly written some rows, so the caller must roll back on it."""
    _check_header(kind, header)
    importer = _Importer(session)
    apply = getattr(importer, f"import_{kind}")
    problems: list[str] = []
    for line, row in enumerate(rows, start=2):  # row 1 is the header
        cells = {key: (value or "").strip() or None for key, value in row.items() if key}
        if not any(cells.values()):
            continue  # a blank line
        try:
            apply(line, cells)
        except RowProblem as problem:
            problems.append(f"Row {line}: {problem}")
    if problems:
        raise ImportRefused(problems)
    session.flush()
    return importer.report


def _check_header(kind: Kind, header: list[str]) -> None:
    required, optional = COLUMNS[kind]
    problems = [f"Unknown column '{name}'" for name in header if name not in required + optional]
    problems += [f"Missing column '{name}'" for name in required if name not in header]
    if problems:
        allowed = ", ".join(required + optional)
        raise ImportRefused([*problems, f"A {kind} file's columns are: {allowed}"])


def _digits(phone: str) -> str:
    return re.sub(r"[^0-9]", "", phone)


def _phone(value: str) -> str:
    """With its country code, once it's something that can be dialled: sign-in
    puts the country the person picks in front of what they type, so a local
    "0501234567" would never match anyone. Spreadsheets drop the "+" (they
    read +966… as a number), so digits that start with a country code, or
    with 00, count as international too."""
    digits = _digits(value)
    if not value.startswith("+") and digits.startswith("00"):
        digits = digits[2:]
    # 7–15 digits, as sign-in requires (services/auth.normalize_phone).
    if not re.fullmatch(r"\+?[0-9\s()-]+", value) or not 7 <= len(digits) <= 15:
        raise RowProblem(f"'{value}' isn't a phone number")
    if value.startswith("+"):
        return value
    if digits.startswith("0"):
        raise RowProblem(f"'{value}' needs its country code, e.g. +966 50 123 4567")
    return "+" + digits


def _count(value: str | None, column: str) -> int | None:
    if value is None:
        return None
    if not value.isdigit():
        raise RowProblem(f"{column} must be a whole number, not '{value}'")
    return int(value)


def _label(value: str | None) -> str | None:
    """A unit's label without a leading "Unit" ("Unit 4", "unit #4" → "4"):
    the apps already say "Unit" in front of every label."""
    if value is None:
        return None
    label = re.sub(r"^unit\b[\s#:.-]*", "", value, flags=re.IGNORECASE)
    if not label:
        raise RowProblem(f"'{value}' needs a unit number or name")
    return label


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class _Importer:
    """Holds what's already there, looked up the ways a file names it, and
    keeps those lookups current as rows add to them."""

    def __init__(self, session: Session):
        self.session = session
        self.report = Report()
        self.buildings = {p.name.casefold(): p for p in session.scalars(select(Property))}
        self.units = {(u.property_id, u.label.casefold()): u for u in session.scalars(select(Unit))}
        self.tenants = {_digits(t.phone): t for t in session.scalars(select(Tenant).order_by(Tenant.id))}
        self.staff = {_digits(s.phone): s for s in session.scalars(select(Staff).order_by(Staff.id))}
        self.admins = {(_digits(a.phone), a.trade): a for a in session.scalars(select(Admin).order_by(Admin.id))}
        self.ids = {
            *(p.id for p in self.buildings.values()),
            *(u.id for u in self.units.values()),
            *session.scalars(select(Admin.id)),
        }
        # Each record once per file: a second row for it would silently win.
        self.seen: set[Any] = set()

    # --- bookkeeping --------------------------------------------------------

    def _once(self, key: Any, what: str) -> None:
        if key in self.seen:
            raise RowProblem(f"{what} is already in an earlier row")
        self.seen.add(key)

    def _new_id(self, base: str) -> str:
        candidate, suffix = base, 2
        while candidate in self.ids:
            candidate, suffix = f"{base}-{suffix}", suffix + 1
        self.ids.add(candidate)
        return candidate

    def _added(self, line: int, what: str) -> None:
        self.report.added.append(f"row {line}: {what}")

    def _updated(self, line: int, what: str, changes: list[str]) -> None:
        if changes:
            self.report.updated.append(f"row {line}: {what}: {'; '.join(changes)}")
        else:
            self.report.unchanged += 1

    @staticmethod
    def _set(record: Any, name: str, value: Any, changes: list[str], label: str | None = None) -> None:
        """Sets a field from a non-blank cell, noting the change."""
        old = getattr(record, name)
        if value is not None and value != old:
            setattr(record, name, value)
            changes.append(f"{label or name} {old} → {value}")

    def _find_unit(self, building: str | None, unit: str | None) -> Unit | None:
        if building is None and unit is None:
            return None
        if building is None or unit is None:
            raise RowProblem("give both building and unit, or neither")
        prop = self.buildings.get(building.casefold())
        if prop is None:
            raise RowProblem(f"no building called '{building}' (import it in a units file first)")
        found = self.units.get((prop.id, unit.casefold()))
        if found is None:
            raise RowProblem(f"{prop.name} has no unit '{unit}' (import it in a units file first)")
        return found

    # --- one method per kind, each taking one row -----------------------------

    def import_units(self, line: int, row: dict[str, str | None]) -> None:
        name = row["building"]
        if name is None:
            raise RowProblem("building is empty")
        address, label = row.get("address"), _label(row.get("unit"))
        bedrooms, bathrooms = _count(row.get("bedrooms"), "bedrooms"), _count(row.get("bathrooms"), "bathrooms")
        status = row.get("status")
        if status is not None and status not in STATUSES:
            raise RowProblem(f"status must be one of {', '.join(STATUSES)}, not '{status}'")

        # A building appears on every one of its units' rows, so only its unit
        # has to be unique here (or the building, on a row without a unit).
        self._once(("unit", name.casefold(), (label or "").casefold()), f"{name} {label or ''}".strip())
        prop = self.buildings.get(name.casefold())
        if prop is None:
            if address is None:
                raise RowProblem(f"'{name}' is a new building: give its address")
            prop = Property(id=self._new_id(f"prop-{_slug(name)}"), name=name, address=address)
            self.session.add(prop)
            self.buildings[name.casefold()] = prop
            self._added(line, f"building {name}")
        elif label is None:
            changes: list[str] = []
            self._set(prop, "address", address, changes)
            self._updated(line, f"building {prop.name}", changes)
        elif address is not None and address != prop.address:
            # On a unit's row, the address is the building's; say so if it
            # changes, rather than folding it into the unit's line.
            changes = []
            self._set(prop, "address", address, changes)
            self._updated(line, f"building {prop.name}", changes)

        if label is None:
            return
        unit = self.units.get((prop.id, label.casefold()))
        if unit is None:
            if bedrooms is None or bathrooms is None:
                raise RowProblem(f"unit {label} is new: give its bedrooms and bathrooms")
            if status == "occupied":
                raise RowProblem("a new unit becomes occupied when a tenants file moves someone in")
            initials = "".join(word[0] for word in _slug(name).split("-") if word)
            unit = Unit(
                id=self._new_id(f"unit-{initials}-{_slug(label)}"),
                property_id=prop.id,
                label=label,
                status=status or "vacant",
                tenant_id=None,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
            )
            self.session.add(unit)
            self.units[(prop.id, label.casefold())] = unit
            self._added(line, f"unit {label} in {prop.name}")
            return

        if status == "vacant" and unit.tenant_id is not None:
            raise RowProblem(f"unit {unit.label} has a tenant, so it can't be vacant")
        if status == "occupied" and unit.tenant_id is None:
            raise RowProblem(f"unit {unit.label} has no tenant; a tenants file moves someone in")
        changes = []
        self._set(unit, "bedrooms", bedrooms, changes)
        self._set(unit, "bathrooms", bathrooms, changes)
        self._set(unit, "status", status, changes)
        self._updated(line, f"unit {unit.label} in {prop.name}", changes)

    def import_tenants(self, line: int, row: dict[str, str | None]) -> None:
        name, raw_phone, email = row["name"], row["phone"], row.get("email")
        if name is None or raw_phone is None:
            raise RowProblem("name and phone are both needed")
        phone = _phone(raw_phone)
        if email is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise RowProblem(f"'{email}' isn't an email address")
        home = self._find_unit(row.get("building"), _label(row.get("unit")))
        self._once(("tenant", _digits(phone)), f"The phone {phone}")

        tenant = self.tenants.get(_digits(phone))
        changes: list[str] = []
        is_new = tenant is None
        if tenant is None:
            tenant = Tenant(id=_tenant_id(self.session, name), name=name, phone=phone, email=email)
            self.session.add(tenant)
            self.session.flush()  # so the next new tenant's id sees this one's
            self.tenants[_digits(phone)] = tenant
        else:
            self._set(tenant, "name", name, changes)
            self._set(tenant, "email", email, changes)

        if home is not None and home.tenant_id != tenant.id:
            before = self.session.get(Tenant, home.tenant_id) if home.tenant_id else None
            home.tenant_id, home.status = tenant.id, "occupied"
            changes.append(f"moved into unit {home.label}" + (f", replacing {before.name}" if before else ""))

        if is_new:
            self._added(line, f"tenant {name}" + (f" ({'; '.join(changes)})" if changes else ""))
        else:
            self._updated(line, f"tenant {tenant.name}", changes)

    def import_staff(self, line: int, row: dict[str, str | None]) -> None:
        name, raw_phone, trade = row["name"], row["phone"], row["trade"]
        if name is None or raw_phone is None or trade is None:
            raise RowProblem("name, phone and trade are all needed")
        phone = _phone(raw_phone)
        if trade not in TRADES:
            raise RowProblem(f"trade must be one of {', '.join(TRADES)}, not '{trade}'")
        self._once(("staff", _digits(phone)), f"The phone {phone}")

        member = self.staff.get(_digits(phone))
        if member is None:
            member = Staff(id=_staff_id(self.session, name), name=name, phone=phone, role=trade, photo=None)
            self.session.add(member)
            self.session.flush()  # so the next new member's id sees this one's
            self.staff[_digits(phone)] = member
            self._added(line, f"{trade} staff {name}")
            return
        changes: list[str] = []
        self._set(member, "name", name, changes)
        self._set(member, "role", trade, changes, label="trade")
        note = " (retired: stays off the roster)" if member.retired_at else ""
        self._updated(line, f"staff {member.name}{note}", changes)

    def import_admins(self, line: int, row: dict[str, str | None]) -> None:
        name, raw_phone, portal = row["name"], row["phone"], row["portal"]
        if name is None or raw_phone is None or portal is None:
            raise RowProblem("name, phone and portal are all needed")
        phone = _phone(raw_phone)
        trade = PORTALS.get(portal)
        if trade is None:
            raise RowProblem(f"portal must be one of {', '.join(PORTALS)}, not '{portal}'")
        self._once(("admin", _digits(phone), trade), f"The phone {phone} for {portal}")

        admin = self.admins.get((_digits(phone), trade))
        if admin is None:
            last = next((part for part in reversed(_slug(name).split("-")) if part), "admin")
            admin = Admin(id=self._new_id(f"adm-{last}"), name=name, phone=phone, trade=trade)
            self.session.add(admin)
            self.admins[(_digits(phone), trade)] = admin
            self._added(line, f"{portal} admin {name}")
            return
        changes: list[str] = []
        self._set(admin, "name", name, changes)
        note = " (retired: can't sign in)" if admin.retired_at else ""
        self._updated(line, f"{portal} admin {admin.name}{note}", changes)
