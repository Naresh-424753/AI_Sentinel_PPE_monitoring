"""Authentication: signup, login (now returns a JWT), email-OTP password reset.
Logic is carried over from the original api.py; routes keep the same paths."""
import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from backend import config
from backend.db import get_db, log_audit
from backend.security import pwd_context, create_token, get_current_user

router = APIRouter(tags=["auth"])

OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 5
RESET_TOKEN_EXPIRY_MINUTES = 10

SMTP_HOST = config.SMTP_HOST
SMTP_PORT = config.SMTP_PORT
SMTP_EMAIL = config.SMTP_EMAIL
SMTP_PASSWORD = config.SMTP_PASSWORD


@router.get("/api/me")
def me(user=Depends(get_current_user)):
    return {"success": True, "user": user}


def generate_otp():

    return f"{secrets.randbelow(1000000):06d}"


def hash_value(value: str):

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def datetime_string(dt):

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_datetime(value):

    return datetime.strptime(
        value,
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# REQUEST MODELS
# ============================================================

class SignupRequest(BaseModel):

    name: str

    email: EmailStr

    password: str

    role: str = "USER"


class LoginRequest(BaseModel):

    email: EmailStr

    password: str


class PasswordResetRequest(BaseModel):

    email: EmailStr


class VerifyResetOTPRequest(BaseModel):

    email: EmailStr

    email_otp: str


class ResetPasswordRequest(BaseModel):

    reset_token: str

    new_password: str

    confirm_password: str


# ============================================================
# SIGNUP
# ============================================================

@router.post("/signup")
def signup(request: SignupRequest):

    name = request.name.strip()

    email = str(
        request.email
    ).lower().strip()

    password = request.password

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not name:

        raise HTTPException(
            status_code=400,
            detail="Name is required"
        )

    if len(password) < 8:

        raise HTTPException(
            status_code=400,
            detail=
                "Password must be at least 8 characters"
        )

    if len(
        password.encode("utf-8")
    ) > 72:

        raise HTTPException(
            status_code=400,
            detail=
                "Password must be 72 bytes or less"
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    conn = get_db()

    existing = conn.execute(
        """
        SELECT id

        FROM users

        WHERE LOWER(email) = ?

        LIMIT 1
        """,
        (email,)
    ).fetchone()

    if existing:

        conn.close()

        raise HTTPException(
            status_code=409,
            detail="Email already registered"
        )

    # --------------------------------------------------------
    # HASH PASSWORD
    # --------------------------------------------------------

    password_hash = pwd_context.hash(
        password
    )

    created_at = datetime_string(
        datetime.now()
    )

    # SECURITY:
    # Public signup can ONLY create USER.
    #
    # ADMIN should be created manually
    # in the database.

    role = "USER"

    # --------------------------------------------------------
    # INSERT USER
    # --------------------------------------------------------

    cursor = conn.execute(
        """
        INSERT INTO users
        (
            name,
            email,
            password_hash,
            role,
            created_at,
            status
        )

        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            email,
            password_hash,
            role,
            created_at,
            "ACTIVE"
        )
    )

    user_id = cursor.lastrowid

    conn.commit()

    conn.close()

    print()
    print("==========================================")
    print("✅ USER REGISTERED")
    print("User ID:", user_id)
    print("Name:", name)
    print("Email:", email)
    print("Role:", role)
    print("==========================================")

    return {

        "success": True,

        "message":
            "Registration successful",

        "user": {

            "id":
                user_id,

            "name":
                name,

            "email":
                email,

            "role":
                role,

            "status":
                "ACTIVE"
        }
    }


# ============================================================
# LOGIN
# ============================================================

@router.post("/login")
def login(request: LoginRequest):

    email = str(
        request.email
    ).lower().strip()

    password = request.password

    if not password:

        raise HTTPException(
            status_code=400,
            detail="Password is required"
        )

    conn = get_db()

    user = conn.execute(
        """
        SELECT
            id,
            name,
            email,
            password_hash,
            role,
            status,
            created_at

        FROM users

        WHERE LOWER(email) = ?

        LIMIT 1
        """,
        (email,)
    ).fetchone()

    conn.close()

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    if str(
        user["status"]
    ).upper() != "ACTIVE":

        raise HTTPException(
            status_code=403,
            detail="Account is inactive"
        )

    # --------------------------------------------------------
    # VERIFY PASSWORD
    # --------------------------------------------------------

    try:

        password_valid = pwd_context.verify(
            password,
            user["password_hash"]
        )

    except Exception as error:

        print(
            "❌ PASSWORD VERIFY ERROR:",
            repr(error)
        )

        password_valid = False

    if not password_valid:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    print()
    print("==========================================")
    print("✅ LOGIN SUCCESS"); log_audit(user["email"], "LOGIN", "user", str(user["id"]))
    print("User:", user["email"])
    print("Role:", user["role"])
    print("==========================================")

    return {

        "success": True,

        "message":
            "Login successful",

        "token": create_token(user),

        "user": {

            "id":
                user["id"],

            "name":
                user["name"],

            "email":
                user["email"],

            "role":
                user["role"],

            "status":
                user["status"],

            "created_at":
                user["created_at"]
        }
    }


# ============================================================
# SEND EMAIL OTP
# ============================================================

def send_email_otp(
    email,
    otp
):

    # --------------------------------------------------------
    # DEVELOPMENT MODE
    # --------------------------------------------------------

    if (
        not SMTP_EMAIL
        or not SMTP_PASSWORD
    ):

        print()
        print(
            "⚠️ SMTP credentials are NOT configured"
        )

        print(
            "📧 DEVELOPMENT EMAIL OTP:",
            otp
        )

        return {

            "success":
                False,

            "configured":
                False,

            "message":
                "SMTP credentials not configured"
        }

    # --------------------------------------------------------
    # CREATE EMAIL
    # --------------------------------------------------------

    message = EmailMessage()

    message["Subject"] = (
        "AI Sentinel - Password Reset OTP"
    )

    message["From"] = SMTP_EMAIL

    message["To"] = email

    message.set_content(
        f"""
AI Sentinel Password Reset

Your password reset OTP is:

{otp}

This OTP will expire in
{OTP_EXPIRY_MINUTES} minutes.

If you did not request a password reset,
please ignore this email.

AI Sentinel Security Team
"""
    )

    # --------------------------------------------------------
    # SEND EMAIL
    # --------------------------------------------------------

    try:

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=20
        ) as server:

            server.ehlo()

            server.starttls()

            server.ehlo()

            server.login(
                SMTP_EMAIL,
                SMTP_PASSWORD
            )

            server.send_message(
                message
            )

        print(
            f"📧 EMAIL OTP SENT: {email}"
        )

        return {

            "success":
                True,

            "configured":
                True,

            "message":
                "Email OTP sent"
        }

    except Exception as error:

        print(
            "❌ EMAIL OTP ERROR:",
            repr(error)
        )

        return {

            "success":
                False,

            "configured":
                True,

            "message":
                str(error)
        }


# ============================================================
# CREATE PASSWORD RESET REQUEST
# ============================================================

def create_password_reset_request(
    user_id,
    email
):

    # --------------------------------------------------------
    # GENERATE OTP
    # --------------------------------------------------------

    email_otp = generate_otp()

    email_otp_hash = hash_value(
        email_otp
    )

    now = datetime.now()

    expires_at = datetime_string(
        now +
        timedelta(
            minutes=
                OTP_EXPIRY_MINUTES
        )
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    conn = get_db()

    # Invalidate old reset requests
    conn.execute(
        """
        UPDATE password_reset_otps

        SET used = 1

        WHERE user_id = ?

        AND used = 0
        """,
        (user_id,)
    )

    cursor = conn.execute(
        """
        INSERT INTO password_reset_otps
        (
            user_id,
            email_otp_hash,
            email_verified,
            attempts,
            expires_at,
            reset_token_hash,
            reset_token_expires_at,
            used,
            created_at
        )

        VALUES
        (
            ?,
            ?,
            0,
            0,
            ?,
            NULL,
            NULL,
            0,
            ?
        )
        """,
        (
            user_id,
            email_otp_hash,
            expires_at,
            datetime_string(now)
        )
    )

    request_id = cursor.lastrowid

    conn.commit()

    conn.close()

    # --------------------------------------------------------
    # SEND EMAIL
    # --------------------------------------------------------

    email_result = send_email_otp(
        email,
        email_otp
    )

    print()
    print("==========================================")
    print("🔐 PASSWORD RESET OTP CREATED")
    print("Request ID:", request_id)
    print("User:", email)
    print(
        "Email sent:",
        email_result["success"]
    )
    print(
        "Expires:",
        expires_at
    )
    print("==========================================")

    return {

        "request_id":
            request_id,

        "email_result":
            email_result
    }


# ============================================================
# REQUEST PASSWORD RESET
# ============================================================

@router.post("/request-password-reset")
def request_password_reset(
    request: PasswordResetRequest
):

    email = str(
        request.email
    ).lower().strip()

    conn = get_db()

    user = conn.execute(
        """
        SELECT
            id,
            name,
            email,
            role,
            status

        FROM users

        WHERE LOWER(email) = ?

        LIMIT 1
        """,
        (email,)
    ).fetchone()

    conn.close()

    # --------------------------------------------------------
    # DO NOT REVEAL ACCOUNT EXISTENCE
    # --------------------------------------------------------

    if not user:

        return {

            "success":
                True,

            "message":
                "If the account exists, "
                "an OTP has been sent to the email."
        }

    if str(
        user["status"]
    ).upper() != "ACTIVE":

        return {

            "success":
                True,

            "message":
                "If the account exists, "
                "an OTP has been sent to the email."
        }

    # --------------------------------------------------------
    # CREATE OTP
    # --------------------------------------------------------

    result = create_password_reset_request(
        user_id=user["id"],
        email=email
    )

    return {

        "success":
            True,

        "message":
            "Password reset OTP sent to your email.",

        "request_id":
            result["request_id"],

        "expires_in":
            OTP_EXPIRY_MINUTES * 60,

        "email_sent":
            result["email_result"]["success"],

        "email_configured":
            result["email_result"]["configured"],

        "email_message":
            result["email_result"]["message"]
    }


# ============================================================
# RESEND PASSWORD RESET OTP
# ============================================================

@router.post("/resend-password-reset")
def resend_password_reset(
    request: PasswordResetRequest
):

    email = str(
        request.email
    ).lower().strip()

    conn = get_db()

    user = conn.execute(
        """
        SELECT
            id,
            email,
            status

        FROM users

        WHERE LOWER(email) = ?

        LIMIT 1
        """,
        (email,)
    ).fetchone()

    conn.close()

    if not user:

        raise HTTPException(
            status_code=400,
            detail="Invalid account details"
        )

    if str(
        user["status"]
    ).upper() != "ACTIVE":

        raise HTTPException(
            status_code=400,
            detail="Account is inactive"
        )

    # --------------------------------------------------------
    # CREATE NEW OTP
    # --------------------------------------------------------

    result = create_password_reset_request(
        user_id=user["id"],
        email=email
    )

    return {

        "success":
            True,

        "message":
            "New email OTP sent.",

        "request_id":
            result["request_id"],

        "expires_in":
            OTP_EXPIRY_MINUTES * 60,

        "email_sent":
            result["email_result"]["success"],

        "email_configured":
            result["email_result"]["configured"],

        "email_message":
            result["email_result"]["message"]
    }


# ============================================================
# VERIFY PASSWORD RESET EMAIL OTP
# ============================================================

@router.post("/verify-password-reset")
def verify_password_reset(
    request: VerifyResetOTPRequest
):

    email = str(
        request.email
    ).lower().strip()

    email_otp = str(
        request.email_otp
    ).strip()

    # --------------------------------------------------------
    # OTP FORMAT
    # --------------------------------------------------------

    if (
        not email_otp.isdigit()
        or len(email_otp) != 6
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid email OTP"
        )

    # --------------------------------------------------------
    # GET LATEST ACTIVE OTP
    # --------------------------------------------------------

    conn = get_db()

    row = conn.execute(
        """
        SELECT
            pr.id,
            pr.user_id,
            pr.email_otp_hash,
            pr.email_verified,
            pr.attempts,
            pr.expires_at,
            pr.used

        FROM password_reset_otps pr

        INNER JOIN users u
            ON u.id = pr.user_id

        WHERE LOWER(u.email) = ?

        AND pr.used = 0

        ORDER BY pr.id DESC

        LIMIT 1
        """,
        (email,)
    ).fetchone()

    if not row:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=
                "OTP request not found. "
                "Please request a new OTP."
        )

    # --------------------------------------------------------
    # MAX ATTEMPTS
    # --------------------------------------------------------

    if row["attempts"] >= OTP_MAX_ATTEMPTS:

        conn.execute(
            """
            UPDATE password_reset_otps

            SET used = 1

            WHERE id = ?
            """,
            (row["id"],)
        )

        conn.commit()

        conn.close()

        raise HTTPException(
            status_code=429,
            detail=
                "Maximum OTP attempts exceeded. "
                "Please request a new OTP."
        )

    # --------------------------------------------------------
    # CHECK EXPIRY
    # --------------------------------------------------------

    try:

        expires_at = parse_datetime(
            row["expires_at"]
        )

    except Exception:

        conn.close()

        raise HTTPException(
            status_code=500,
            detail="Invalid OTP expiry data"
        )

    if datetime.now() > expires_at:

        conn.execute(
            """
            UPDATE password_reset_otps

            SET used = 1

            WHERE id = ?
            """,
            (row["id"],)
        )

        conn.commit()

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=
                "OTP expired. "
                "Please request a new OTP."
        )

    # --------------------------------------------------------
    # VERIFY OTP
    # --------------------------------------------------------

    entered_hash = hash_value(
        email_otp
    )

    email_valid = secrets.compare_digest(
        entered_hash,
        row["email_otp_hash"]
    )

    if not email_valid:

        new_attempts = (
            row["attempts"] + 1
        )

        conn.execute(
            """
            UPDATE password_reset_otps

            SET attempts = ?

            WHERE id = ?
            """,
            (
                new_attempts,
                row["id"]
            )
        )

        if new_attempts >= OTP_MAX_ATTEMPTS:

            conn.execute(
                """
                UPDATE password_reset_otps

                SET used = 1

                WHERE id = ?
                """,
                (row["id"],)
            )

        conn.commit()

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=
                "Invalid email OTP. "
                f"Attempts remaining: "
                f"{max(0, OTP_MAX_ATTEMPTS - new_attempts)}"
        )

    # --------------------------------------------------------
    # GENERATE RESET TOKEN
    # --------------------------------------------------------

    reset_token = secrets.token_urlsafe(
        32
    )

    reset_token_hash = hash_value(
        reset_token
    )

    reset_token_expires = datetime_string(
        datetime.now()
        +
        timedelta(
            minutes=
                RESET_TOKEN_EXPIRY_MINUTES
        )
    )

    # --------------------------------------------------------
    # SAVE RESET TOKEN
    # --------------------------------------------------------

    conn.execute(
        """
        UPDATE password_reset_otps

        SET
            email_verified = 1,
            reset_token_hash = ?,
            reset_token_expires_at = ?

        WHERE id = ?
        """,
        (
            reset_token_hash,
            reset_token_expires,
            row["id"]
        )
    )

    conn.commit()

    conn.close()

    print()
    print("==========================================")
    print("✅ EMAIL OTP VERIFIED")
    print("User ID:", row["user_id"])
    print(
        "Reset token expires:",
        reset_token_expires
    )
    print("==========================================")

    return {

        "success":
            True,

        "message":
            "Email OTP verified.",

        "reset_token":
            reset_token,

        "expires_in":
            RESET_TOKEN_EXPIRY_MINUTES * 60
    }


# ============================================================
# RESET PASSWORD
# ============================================================

@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest
):

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not request.reset_token:

        raise HTTPException(
            status_code=400,
            detail="Reset token is required"
        )

    if not request.new_password:

        raise HTTPException(
            status_code=400,
            detail="Password cannot be empty"
        )

    if not request.confirm_password:

        raise HTTPException(
            status_code=400,
            detail=
                "Confirm password cannot be empty"
        )

    if (
        request.new_password
        != request.confirm_password
    ):

        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )

    if len(
        request.new_password
    ) < 8:

        raise HTTPException(
            status_code=400,
            detail=
                "Password must be at least 8 characters"
        )

    if len(
        request.new_password.encode("utf-8")
    ) > 72:

        raise HTTPException(
            status_code=400,
            detail=
                "Password must be 72 bytes or less"
        )

    # --------------------------------------------------------
    # HASH RESET TOKEN
    # --------------------------------------------------------

    token_hash = hash_value(
        request.reset_token
    )

    conn = get_db()

    # --------------------------------------------------------
    # FIND RESET TOKEN
    # --------------------------------------------------------

    row = conn.execute(
        """
        SELECT
            id,
            user_id,
            email_verified,
            reset_token_expires_at,
            used

        FROM password_reset_otps

        WHERE reset_token_hash = ?

        ORDER BY id DESC

        LIMIT 1
        """,
        (token_hash,)
    ).fetchone()

    if not row:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Invalid reset token"
        )

    # --------------------------------------------------------
    # TOKEN ALREADY USED
    # --------------------------------------------------------

    if row["used"]:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Reset token already used"
        )

    # --------------------------------------------------------
    # EMAIL OTP MUST BE VERIFIED
    # --------------------------------------------------------

    if not row["email_verified"]:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=
                "Email OTP verification required"
        )

    # --------------------------------------------------------
    # CHECK TOKEN EXPIRY
    # --------------------------------------------------------

    if not row["reset_token_expires_at"]:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Invalid reset token"
        )

    try:

        token_expires = parse_datetime(
            row["reset_token_expires_at"]
        )

    except Exception:

        conn.close()

        raise HTTPException(
            status_code=500,
            detail=
                "Invalid reset token expiry data"
        )

    if datetime.now() > token_expires:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=
                "Reset token expired. "
                "Please start again."
        )

    # --------------------------------------------------------
    # HASH NEW PASSWORD
    # --------------------------------------------------------

    new_password_hash = pwd_context.hash(
        request.new_password
    )

    # --------------------------------------------------------
    # UPDATE USER PASSWORD
    # --------------------------------------------------------

    cursor = conn.execute(
        """
        UPDATE users

        SET password_hash = ?

        WHERE id = ?
        """,
        (
            new_password_hash,
            row["user_id"]
        )
    )

    if cursor.rowcount == 0:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # --------------------------------------------------------
    # INVALIDATE RESET TOKEN
    # --------------------------------------------------------

    conn.execute(
        """
        UPDATE password_reset_otps

        SET
            used = 1,
            reset_token_hash = NULL

        WHERE id = ?
        """,
        (row["id"],)
    )

    conn.commit()

    conn.close()

    print()
    print("==========================================")
    print("🔐 PASSWORD RESET SUCCESS")
    print("User ID:", row["user_id"])
    print("==========================================")

    return {

        "success":
            True,

        "message":
            "Password changed successfully."
    }
