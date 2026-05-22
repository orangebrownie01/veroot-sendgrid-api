import os
import json
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Header, HTTPException
from sendgrid.helpers.eventwebhook import EventWebhook
from mysql_connector import lookup_email, lookup_emails_batch, close_all_mysql_connections

DATABASE_URL = os.environ["DATABASE_URL"]

SENDGRID_PUBLIC_KEY = os.environ.get(
    "SENDGRID_WEBHOOK_PUBLIC_KEY",
    ""
)

APP_API_TOKENS = {
    token.strip()
    for token in os.environ.get(
        "APP_API_TOKENS",
        ""
    ).split(",")
    if token.strip()
}


connection_pool = None


def get_db():
    return connection_pool.getconn()


def release_db(conn):
    connection_pool.putconn(conn)


def init_db():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                timestamp BIGINT,
                recipient TEXT,
                event TEXT,
                reason TEXT,
                sg_event_id TEXT UNIQUE,
                sg_message_id TEXT,
                message_uuid TEXT,
                raw_json JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_recipient ON events (recipient);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_event ON events (event);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_id_desc ON events (id DESC);"
            )
        conn.commit()
    finally:
        release_db(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global connection_pool
    connection_pool = pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL
    )
    init_db()
    yield
    connection_pool.closeall()
    close_all_mysql_connections()


app = FastAPI(lifespan=lifespan)


def verify_sendgrid_signature(
    raw_body: bytes,
    signature: str,
    timestamp: str
) -> bool:

    if not SENDGRID_PUBLIC_KEY:
        print("Missing SENDGRID_WEBHOOK_PUBLIC_KEY")
        return False

    if not signature or not timestamp:
        print("Missing SendGrid signature or timestamp header")
        return False

    try:

        event_webhook = EventWebhook()

        ec_public_key = event_webhook.convert_public_key_to_ecdsa(
            SENDGRID_PUBLIC_KEY
        )

        return event_webhook.verify_signature(
            raw_body.decode("utf-8"),
            signature,
            timestamp,
            ec_public_key
        )

    except Exception as e:

        print(
            "Signature verification error:",
            str(e)
        )

        return False


def require_viewer_token(
    authorization: str | None
):

    if not authorization:

        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header"
        )

    prefix = "Bearer "

    if not authorization.startswith(prefix):

        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header"
        )

    token = authorization[len(prefix):].strip()

    if token not in APP_API_TOKENS:

        raise HTTPException(
            status_code=403,
            detail="Invalid token"
        )


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


@app.post("/sendgrid/events")
async def sendgrid_events(
    request: Request
):

    raw_body = await request.body()

    signature = request.headers.get(
        "x-twilio-email-event-webhook-signature",
        ""
    )

    timestamp_header = request.headers.get(
        "x-twilio-email-event-webhook-timestamp",
        ""
    )

    # Decode once — reused for both JSON parsing and signature verification
    body_str = raw_body.decode("utf-8")

    if not verify_sendgrid_signature(
        raw_body,
        signature,
        timestamp_header
    ):

        print(
            "Rejected SendGrid webhook: invalid signature"
        )

        return Response(
            "Invalid SendGrid signature",
            status_code=401
        )

    events = json.loads(body_str)

    conn = get_db()
    try:
        with conn.cursor() as cur:

            for e in events:

                cur.execute("""
                INSERT INTO events (
                    timestamp,
                    recipient,
                    event,
                    reason,
                    sg_event_id,
                    sg_message_id,
                    message_uuid,
                    raw_json
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (sg_event_id)
                DO NOTHING;
                """, (
                    e.get("timestamp"),
                    e.get("email"),
                    e.get("event"),
                    e.get("reason")
                    or e.get("response"),
                    e.get("sg_event_id"),
                    e.get("sg_message_id"),
                    e.get("message_uuid"),
                    json.dumps(e)
                ))

                print(
                    f"Saved event: "
                    f"{e.get('event')} "
                    f"recipient={e.get('email')}"
                )

        conn.commit()
    finally:
        release_db(conn)

    return Response(status_code=202)


@app.get("/events")
def get_events(
    authorization: str | None = Header(default=None),
    search: str = "",
    event: str = "All",
    limit: int = 500,
    date_from: int | None = None,
    date_to: int | None = None
):

    require_viewer_token(
        authorization
    )

    limit = min(limit, 1000)

    query = """
        SELECT
    id,
    timestamp,
    recipient,
    event,
    reason,
    sg_event_id,
    sg_message_id,
    message_uuid,
    raw_json,
    created_at
        FROM events
        WHERE 1=1
    """

    params = []

    if date_from:
        query += " AND timestamp >= %s"
        params.append(date_from)

    if date_to:
        query += " AND timestamp <= %s"
        params.append(date_to)

    if search:

        query += """
        AND (
            recipient ILIKE %s
            OR event ILIKE %s
            OR reason ILIKE %s
        )
        """

        like = f"%{search}%"

        params.extend([
            like,
            like,
            like,
        ])

    if event != "All":

        query += """
        AND event = %s
        """

        params.append(
            event.lower()
        )

    query += """
    ORDER BY id DESC
    LIMIT %s
    """

    params.append(limit)

    conn = get_db()
    try:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute(
                query,
                params
            )

            rows = cur.fetchall()
    finally:
        release_db(conn)

    return {
        "events": [dict(row) for row in rows]
    }


@app.get("/lookup/email")
def lookup_single_email(
    authorization: str | None = Header(default=None),
    email: str = ""
):
    """
    Single email lookup — checks Veroot users first, then vendor contacts.
    Called per-row from the desktop app when the user clicks Lookup on a row.
    """
    require_viewer_token(authorization)

    if not email:
        raise HTTPException(status_code=400, detail="email parameter required")

    result = lookup_email(email)
    return {"email": email, "result": result}


@app.post("/lookup/batch")
def lookup_batch_emails(
    request_body: dict,
    authorization: str | None = Header(default=None),
):
    """
    Batch email lookup — accepts up to 500 emails, runs two IN-clause queries
    (Veroot users + vendor contacts) and returns results keyed by email.
    Called when the user clicks Enrich All in the desktop app.
    """
    require_viewer_token(authorization)

    emails = request_body.get("emails", [])

    if not isinstance(emails, list):
        raise HTTPException(status_code=400, detail="emails must be a list")

    if len(emails) > 500:
        emails = emails[:500]

    results = lookup_emails_batch(emails)
    return {"results": results}


@app.get("/latest-version")
def latest_version():

    return {
        "version": os.environ.get("APP_LATEST_VERSION", "1.0.1"),
        "download_url": os.environ.get("APP_DOWNLOAD_URL", ""),
        "sha256": os.environ.get("APP_DOWNLOAD_SHA256", "")
    }
