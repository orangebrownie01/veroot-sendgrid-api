"""
mysql_connector.py
------------------
Connection pool and lookup helpers for the Veroot CRM / application database
hosted on AWS RDS (MySQL/MariaDB).

Secrets are read exclusively from environment variables — never hardcoded.
See the README or PROJECT_CONTEXT.md for the full list of required env vars.
"""

import os
import logging
from typing import Optional

import pymysql
import pymysql.cursors
from pymysql import OperationalError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all values come from environment variables set in Render
# ---------------------------------------------------------------------------

MYSQL_HOST     = os.environ.get("MYSQL_HOST", "")           # e.g. mydb.abc123.us-east-1.rds.amazonaws.com
MYSQL_PORT     = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER     = os.environ.get("MYSQL_USER", "")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "")

# How long (seconds) to wait for a connection before giving up
MYSQL_CONNECT_TIMEOUT = int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# Simple connection pool using a list of reusable connections.
# pymysql does not ship a built-in pool; for production scale consider
# switching to SQLAlchemy or aiomysql.  This lightweight pool is sufficient
# for a low-concurrency internal tool.
# ---------------------------------------------------------------------------

_pool: list = []
_POOL_SIZE = int(os.environ.get("MYSQL_POOL_SIZE", "5"))


def _create_connection() -> pymysql.Connection:
    """Open a new verified MySQL connection."""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        connect_timeout=MYSQL_CONNECT_TIMEOUT,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        ssl={"ca": None},   # RDS requires SSL by default; set ca path if you have the cert bundle
    )


def get_mysql_conn() -> Optional[pymysql.Connection]:
    """
    Borrow a connection from the pool, creating one if the pool is empty.
    Returns None if the connection cannot be established (so callers can
    degrade gracefully rather than crashing the whole request).
    """
    while _pool:
        conn = _pool.pop()
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            # Connection is dead — discard it and try the next one
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
    """Return a connection to the pool, or discard it if the pool is full."""
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
    """Drain the pool and close every connection. Called on app shutdown."""
    while _pool:
        conn = _pool.pop()
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Lookup helpers
# Each function accepts a recipient email address (or other key) and returns
# a dict of enriched fields, or an empty dict if nothing is found.
#
# ⚠️  IMPORTANT: replace the table and column names below with your actual
#     schema.  The placeholders follow common naming conventions but will
#     need to match your database.
# ---------------------------------------------------------------------------

def lookup_account(email: str) -> dict:
    """
    Look up account / company information by email domain or a users→accounts
    foreign key relationship.

    Expected return shape:
        {
            "account_id":   "...",
            "account_name": "...",
            "account_plan": "...",
        }
    """
    conn = get_mysql_conn()
    if conn is None:
        return {}

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    a.id            AS account_id,
                    a.name          AS account_name,
                    a.plan          AS account_plan
                FROM accounts a
                JOIN users u ON u.account_id = a.id
                WHERE u.email = %s
                LIMIT 1
            """, (email,))
            row = cur.fetchone()
            return row or {}
    except Exception as exc:
        logger.warning("lookup_account failed for %s: %s", email, exc)
        return {}
    finally:
        release_mysql_conn(conn)


def lookup_user(email: str) -> dict:
    """
    Look up user profile information by email address.

    Expected return shape:
        {
            "user_id":   "...",
            "user_name": "...",
            "user_role": "...",
        }
    """
    conn = get_mysql_conn()
    if conn is None:
        return {}

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id          AS user_id,
                    name        AS user_name,
                    role        AS user_role
                FROM users
                WHERE email = %s
                LIMIT 1
            """, (email,))
            row = cur.fetchone()
            return row or {}
    except Exception as exc:
        logger.warning("lookup_user failed for %s: %s", email, exc)
        return {}
    finally:
        release_mysql_conn(conn)


def lookup_email_metadata(sg_message_id: str) -> dict:
    """
    Look up email metadata (subject, template, campaign) by SendGrid message ID.

    Expected return shape:
        {
            "email_subject":  "...",
            "email_template": "...",
            "email_campaign": "...",
        }
    """
    if not sg_message_id:
        return {}

    conn = get_mysql_conn()
    if conn is None:
        return {}

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    subject     AS email_subject,
                    template    AS email_template,
                    campaign    AS email_campaign
                FROM emails
                WHERE sg_message_id = %s
                LIMIT 1
            """, (sg_message_id,))
            row = cur.fetchone()
            return row or {}
    except Exception as exc:
        logger.warning("lookup_email_metadata failed for %s: %s", sg_message_id, exc)
        return {}
    finally:
        release_mysql_conn(conn)


def enrich_event(event: dict) -> dict:
    """
    Given a single SendGrid event row (as returned by the PostgreSQL query),
    return a new dict with account, user, and email metadata merged in.

    All lookups degrade gracefully — if MySQL is unavailable, the original
    event data is returned unchanged with empty enrichment fields.
    """
    recipient    = event.get("recipient", "")
    sg_message_id = event.get("sg_message_id", "")

    account_data  = lookup_account(recipient)
    user_data     = lookup_user(recipient)
    email_data    = lookup_email_metadata(sg_message_id)

    return {
        **event,
        # Account fields
        "account_id":    account_data.get("account_id"),
        "account_name":  account_data.get("account_name"),
        "account_plan":  account_data.get("account_plan"),
        # User fields
        "user_id":       user_data.get("user_id"),
        "user_name":     user_data.get("user_name"),
        "user_role":     user_data.get("user_role"),
        # Email metadata fields
        "email_subject":  email_data.get("email_subject"),
        "email_template": email_data.get("email_template"),
        "email_campaign": email_data.get("email_campaign"),
    }
