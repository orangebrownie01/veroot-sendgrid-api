"""
mysql_connector.py
------------------
Connection pool and lookup helpers for the Veroot application database
hosted on AWS RDS (MySQL/MariaDB).

Two lookup types:
  - Veroot Users  (agents, IACs, brokers) — users + accounts tables
  - Vendors       (contacts)              — contacts + accounts + vendor_fmcsa tables

Secrets are read exclusively from environment variables set in Render.
"""

import os
import logging
from typing import Optional

import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all values from Render environment variables
# ---------------------------------------------------------------------------

MYSQL_HOST     = os.environ.get("MYSQL_HOST", "")
MYSQL_PORT     = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER     = os.environ.get("MYSQL_USER", "")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "")
MYSQL_CONNECT_TIMEOUT = int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "10"))

_pool: list = []
_POOL_SIZE = int(os.environ.get("MYSQL_POOL_SIZE", "5"))


def _create_connection() -> pymysql.Connection:
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        connect_timeout=MYSQL_CONNECT_TIMEOUT,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def get_mysql_conn() -> Optional[pymysql.Connection]:
    while _pool:
        conn = _pool.pop()
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    try:
        return _create_connection()
    except Exception as exc:
        logger.error("MySQL connection failed: %s", exc)
        return None


def release_mysql_conn(conn: Optional[pymysql.Connection]) -> None:
    if conn is None:
        return
    if len(_pool) < _POOL_SIZE:
        _pool.append(conn)
    else:
        try:
            conn.close()
        except Exception:
            pass


def close_all_mysql_connections() -> None:
    while _pool:
        conn = _pool.pop()
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Single-email lookups
# ---------------------------------------------------------------------------

def lookup_veroot_user(email: str) -> Optional[dict]:
    """
    Query 1 — Veroot platform user (agent, IAC, broker).
    Returns account + user info, or None if not found.
    """
    if not email:
        return None

    conn = get_mysql_conn()
    if conn is None:
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    a.accountCompanyName AS account_name,
                    a.accountId          AS account_id,
                    u.userName           AS user_name,
                    u.userId             AS user_id,
                    u.userEmail          AS user_email
                FROM users u
                JOIN accounts a ON a.accountId = u.userAccountId
                WHERE u.userEmail = %s
                  AND u.userStatus = 'active'
                LIMIT 1
            """, (email,))
            row = cur.fetchone()
            if row:
                row["match_type"] = "veroot_user"
            return row or None
    except Exception as exc:
        logger.warning("lookup_veroot_user failed for %s: %s", email, exc)
        return None
    finally:
        release_mysql_conn(conn)


def lookup_vendor_contact(email: str) -> Optional[dict]:
    """
    Query 2 — Vendor contact.
    Returns contact + requestor account + vendor info, or None if not found.
    """
    if not email:
        return None

    conn = get_mysql_conn()
    if conn is None:
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.contactId,
                    c.contactEmail,
                    c.contactStatus,
                    c.contactAccountId,
                    c.contactVendorId,
                    requestor.accountId          AS requestor_account_id,
                    requestor.accountCompanyName AS requestor_account_name,
                    v.id                         AS vendor_id,
                    v.name                       AS vendor_name,
                    v.displayName                AS vendor_display_name,
                    v.deleted                    AS vendor_deleted_at
                FROM contacts c
                LEFT JOIN accounts requestor ON requestor.accountId = c.contactAccountId
                LEFT JOIN vendor_fmcsa v     ON v.id = c.contactVendorId
                WHERE c.contactEmail = %s
                LIMIT 1
            """, (email,))
            row = cur.fetchone()
            if row:
                row["match_type"] = "vendor_contact"
            return row or None
    except Exception as exc:
        logger.warning("lookup_vendor_contact failed for %s: %s", email, exc)
        return None
    finally:
        release_mysql_conn(conn)


def lookup_email(email: str) -> dict:
    """
    Try Veroot user first, then vendor contact.
    Returns enrichment dict with match_type indicating which query matched,
    or match_type='not_found' if neither matched.
    """
    result = lookup_veroot_user(email)
    if result:
        return result

    result = lookup_vendor_contact(email)
    if result:
        return result

    return {"match_type": "not_found", "user_email": email}


# ---------------------------------------------------------------------------
# Batch lookup — accepts up to 500 emails, returns dict keyed by email
# ---------------------------------------------------------------------------

def lookup_emails_batch(emails: list[str]) -> dict[str, dict]:
    """
    Batch lookup for up to 500 emails.
    Runs two queries (one for users, one for vendor contacts) using IN clauses
    rather than looping — far fewer round trips to the DB.
    Returns a dict: { email: enrichment_dict }
    """
    if not emails:
        return {}

    # Deduplicate and cap at 500
    emails = list(dict.fromkeys(emails))[:500]
    placeholders = ",".join(["%s"] * len(emails))
    results: dict[str, dict] = {}

    conn = get_mysql_conn()
    if conn is None:
        return {}

    try:
        with conn.cursor() as cur:

            # --- Query 1: Veroot users ---
            cur.execute(f"""
                SELECT
                    a.accountCompanyName AS account_name,
                    a.accountId          AS account_id,
                    u.userName           AS user_name,
                    u.userId             AS user_id,
                    u.userEmail          AS user_email
                FROM users u
                JOIN accounts a ON a.accountId = u.userAccountId
                WHERE u.userEmail IN ({placeholders})
                  AND u.userStatus = 'active'
            """, emails)

            for row in cur.fetchall():
                email = row["user_email"]
                row["match_type"] = "veroot_user"
                results[email] = row

            # --- Query 2: Vendor contacts (only for emails not already matched) ---
            unmatched = [e for e in emails if e not in results]

            if unmatched:
                vendor_placeholders = ",".join(["%s"] * len(unmatched))
                cur.execute(f"""
                    SELECT
                        c.contactId,
                        c.contactEmail,
                        c.contactStatus,
                        c.contactAccountId,
                        c.contactVendorId,
                        requestor.accountId          AS requestor_account_id,
                        requestor.accountCompanyName AS requestor_account_name,
                        v.id                         AS vendor_id,
                        v.name                       AS vendor_name,
                        v.displayName                AS vendor_display_name,
                        v.deleted                    AS vendor_deleted_at
                    FROM contacts c
                    LEFT JOIN accounts requestor ON requestor.accountId = c.contactAccountId
                    LEFT JOIN vendor_fmcsa v     ON v.id = c.contactVendorId
                    WHERE c.contactEmail IN ({vendor_placeholders})
                """, unmatched)

                for row in cur.fetchall():
                    email = row["contactEmail"]
                    row["match_type"] = "vendor_contact"
                    results[email] = row

    except Exception as exc:
        logger.error("lookup_emails_batch failed: %s", exc)
    finally:
        release_mysql_conn(conn)

    # Fill in not_found for anything still unmatched
    for email in emails:
        if email not in results:
            results[email] = {"match_type": "not_found", "user_email": email}

    return results
