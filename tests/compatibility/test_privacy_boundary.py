"""Frozen invariant: raw email bodies never appear in responses or stored records."""

import asyncio
import json
from dataclasses import asdict

from conftest import make_email, make_task

SECRET_BODY = (
    "BÍ MẬT KB-9173: ngân sách thưởng quý 4 là 2.4 tỷ đồng, "
    "chỉ trao đổi nội bộ, không chia sẻ ra ngoài."
)


def test_email_body_never_appears_in_responses_or_stored_records(compat_session) -> None:
    async def scenario() -> None:
        tasks = (
            make_task(
                "m1",
                "Xác nhận kế hoạch quý",
                summary="Yêu cầu xác nhận kế hoạch.",
            ),
        )
        messages = [make_email("m1", "t1", "Kế hoạch quý 4", body=SECRET_BODY)]

        async with compat_session(messages, tasks) as s:
            created = await s.post_run("privacy")
            run_id = created.json()["id"]

            surfaces = [
                created.text,
                (await s.get_run(run_id)).text,
                (await s.get_result(run_id)).text,
            ]
            for surface in surfaces:
                assert SECRET_BODY not in surface
                assert "KB-9173" not in surface

            run_record = await s.app.state.run_repository.get(run_id)
            assert run_record is not None
            run_dump = json.dumps(asdict(run_record), ensure_ascii=False, default=str)
            assert SECRET_BODY not in run_dump

            stored_items = await s.app.state.result_repository.list_items(run_id)
            for item in stored_items:
                assert SECRET_BODY not in json.dumps(asdict(item), ensure_ascii=False, default=str)

            processed = await s.app.state.result_repository.list_processed_emails(run_id)
            for email_record in processed:
                assert SECRET_BODY not in json.dumps(
                    asdict(email_record), ensure_ascii=False, default=str
                )

    asyncio.run(scenario())
