"""The fixed vocabularies from the frontend's `packages/core/src/types.ts`.

Each is a `Literal`, written once and used twice: `Base.type_annotation_map`
turns it into a checked text column, and the Pydantic schemas use it as is,
so the OpenAPI schema (and the TypeScript generated from it) gets the same
string union `types.ts` has today. Keep the values identical to the frontend's.
"""

from typing import Literal, get_args

Stage = Literal["submitted", "assigned", "in-progress", "done"]

# The trade a request belongs to, and the trade a staff member works in.
RequestType = Literal["maintenance", "housekeeping"]

Priority = Literal["urgent", "normal"]

# Who raised a request. Seeded requests carry none.
Origin = Literal["ops", "tenant"]

UnitStatus = Literal["occupied", "under-maintenance", "vacant"]

# The four signed-in apps. A session belongs to one of them, and the same
# phone can be a different account in each (a technician who is also a tenant).
App = Literal["tenant", "field", "ops", "housekeeping"]

# What ops decided about a tenant's registration.
RegistrationDecision = Literal["approved", "declined"]

# Maintenance categories are fixed. A housekeeping category is whichever
# rate-card `service_type` it was booked as, so a request's `category` column
# is plain text rather than this type (see `ServiceRequest`).
MaintenanceCategory = Literal["plumbing", "electrical", "ac", "appliance", "other"]

# Stages in the order a request moves through them. A request's current stage
# is the furthest one its history has reached.
STAGES: tuple[Stage, ...] = get_args(Stage)
MAINTENANCE_CATEGORIES: tuple[MaintenanceCategory, ...] = get_args(MaintenanceCategory)
