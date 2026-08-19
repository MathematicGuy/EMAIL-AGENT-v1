"""Diagnostic script to inspect real Gmail threads for reply chains."""

import asyncio
import sys

# Ensure stdout handles UTF-8 on Windows
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cowork_agent.config import GmailSettings, load_runtime_environment
from cowork_agent.integrations.gmail.auth import TokenCipher
from cowork_agent.integrations.gmail.provider import GmailMailboxAdapter
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)


async def inspect_reply_chains() -> None:
    load_runtime_environment()
    settings = GmailSettings.from_env()
    repo = SQLiteMailboxConnectionRepository(settings.connection_db_path)
    cipher = TokenCipher(settings.token_encryption_key)
    adapter = GmailMailboxAdapter(settings, repo, cipher)

    connections = await repo.list_all()
    if not connections:
        print("[!] Khong tim thay mailbox connection nao trong database.")
        return

    conn = connections[0]
    print(f"[*] Dang kiem tra hom thu: {conn.email_address} (ID: {conn.id})\n")

    # 1. Tìm các mail chưa đọc
    search_result = await adapter.search_unread(conn.id, query="is:unread", page_size=20)
    unread_refs = search_result.messages
    print(f"[*] Tim thay {len(unread_refs)} mail chua doc (unread).")

    if not unread_refs:
        print("[-] Khong co mail unread nao de kiem tra chuoi.")
        return

    # Gom theo thread
    thread_map: dict[str, list[str]] = {}
    for ref in unread_refs:
        thread_map.setdefault(ref.thread_id, []).append(ref.message_id)

    print(f"[*] Gom thanh {len(thread_map)} thread(s) co mail unread.\n" + "=" * 60)

    scanned_chains = 0
    skipped_threads = 0

    for idx, (thread_id, unread_msg_ids) in enumerate(thread_map.items(), 1):
        # Lấy toàn bộ email trong thread (cả READ và UNREAD)
        thread_messages = await adapter.get_thread(conn.id, thread_id)
        # Sắp xếp cũ -> mới
        sorted_messages = sorted(thread_messages, key=lambda m: m.received_at)
        total_in_thread = len(sorted_messages)
        latest_message = sorted_messages[-1]
        is_latest_unread = latest_message.gmail_message_id in unread_msg_ids

        print(f"\n[{idx}] Thread ID: {thread_id}")
        print(f"    - Tong so email trong thread: {total_in_thread}")
        print(f"    - Mail moi nhat ID: {latest_message.gmail_message_id}")
        print(f"    - Trang thai mail moi nhat: {'UNREAD' if is_latest_unread else 'READ'}")

        if not is_latest_unread:
            skipped_threads += 1
            print("    ⛔ BO QUA: Mail moi nhat da doc (chi quet khi mail moi nhat la UNREAD).")
            continue

        if total_in_thread > 1:
            scanned_chains += 1
            print("    >>> DU DIEU KIEN: Chuoi reply voi mail moi nhat la UNREAD! <<<")
        else:
            print("    >>> DU DIEU KIEN: Email don le chua doc. <<<")

        # Lấy tối đa 5 email gần nhất theo ADR-011
        bounded_messages = sorted_messages[-5:]
        msg_count_info = f"{len(bounded_messages)}/{total_in_thread}"
        print(f"    - Ap dung ADR-011 lay {msg_count_info} email gan nhat:")

        for pos, msg in enumerate(bounded_messages, 1):
            is_unread = msg.gmail_message_id in unread_msg_ids
            status_tag = "[UNREAD - MOI NHAT]" if is_unread else "[READ - TIEN NHIEM]"
            print(f"      {pos}. {status_tag} ID: {msg.gmail_message_id}")
            print(f"         Tu: {msg.sender_name} <{msg.sender_email}>")
            print(f"         Luc: {msg.received_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"         Chu de: {msg.subject}")
            snippet = msg.normalized_body.strip().replace("\n", " ")[:120]
            print(f"         Trich doan: {snippet}...")

    print("\n" + "=" * 60)
    print(
        f"[*] Tong ket: {scanned_chains} chuoi reply du dieu kien, "
        f"{skipped_threads} thread bi bo qua."
    )


if __name__ == "__main__":
    asyncio.run(inspect_reply_chains())
