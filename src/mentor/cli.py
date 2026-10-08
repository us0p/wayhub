"""Admin CLI (D18).

python -m mentor show EMAIL
python -m mentor set-plan EMAIL {free,tester,admin}
python -m mentor set-quota EMAIL KIND {<int>,unlimited,default}
"""

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Plan, User
from mentor.db import get_sessionmaker, import_models
from mentor.quotas import service as quotas
from mentor.quotas.models import QuotaKind


async def _user(db: AsyncSession, email: str) -> User:
    users = (await db.scalars(select(User).where(User.email == email.lower()))).all()
    if len(users) != 1:
        sys.exit(f"expected exactly one user with e-mail {email!r}, found {len(users)}")
    return users[0]


async def _report(db: AsyncSession, user: User) -> list[str]:
    lines = [f"{user.email}  plan={user.plan.value}  overrides={user.quota_override or '{}'}"]
    for st in await quotas.summary(db, user):
        limit = "unlimited" if st.limit is None else st.limit
        lines.append(f"  {st.kind.value:<16} used={st.used:<6} limit={limit}")
    return lines


async def apply(db: AsyncSession, args: argparse.Namespace) -> list[str]:
    user = await _user(db, args.email)
    if args.command == "set-plan":
        user.plan = Plan(args.plan)
    elif args.command == "set-quota":
        overrides = dict(user.quota_override)
        if args.value == "default":
            overrides.pop(args.kind, None)
        elif args.value == "unlimited":
            overrides[args.kind] = None
        elif args.value.isdigit():
            overrides[args.kind] = int(args.value)
        else:
            sys.exit("value must be a non-negative integer, 'unlimited' or 'default'")
        user.quota_override = overrides
    await db.commit()
    return await _report(db, user)


async def _run(args: argparse.Namespace) -> None:
    import_models()
    async with get_sessionmaker()() as db:
        print("\n".join(await apply(db, args)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m mentor", description="Mentor admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="show a user's plan and quota usage")
    show.add_argument("email")
    plan = sub.add_parser("set-plan", help="change a user's plan")
    plan.add_argument("email")
    plan.add_argument("plan", choices=[p.value for p in Plan])
    quota = sub.add_parser("set-quota", help="override one quota for a user")
    quota.add_argument("email")
    quota.add_argument("kind", choices=[k.value for k in QuotaKind])
    quota.add_argument("value", help="<int> | unlimited | default")
    return parser


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_run(build_parser().parse_args(argv)))
