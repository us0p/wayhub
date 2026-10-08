"""Terms of use and privacy policy (D40). Bump a version whenever its text changes in
substance: users are asked to accept again on their next request."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Consent

TERMS_VERSION = "2026-10-08"
PRIVACY_VERSION = "2026-10-08"


async def has_current_consent(db: AsyncSession, user_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(Consent.id)
        .where(
            Consent.user_id == user_id,
            Consent.terms_version == TERMS_VERSION,
            Consent.privacy_version == PRIVACY_VERSION,
        )
        .limit(1)
    )
    return found is not None
