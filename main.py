import os
import json
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Header, HTTPException
from sendgrid.helpers.eventwebhook import EventWebhook

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


connection_pool = pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DATABASE_URL
)


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
    init_db()
    yield
    connection_pool.closeall()


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
    limit: int = 500
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
@app.get("/latest-version")
def latest_version():

    return {
        "version": os.environ.get("APP_LATEST_VERSION", "1.0.3"),
        "download_url": os.environ.get("APP_DOWNLOAD_URL", ""),
        "sha256": os.environ.get("APP_DOWNLOAD_SHA256", "")
    }
