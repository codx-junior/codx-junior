import hashlib
import logging
import jwt
import bcrypt
import os

from datetime import datetime

from fastapi import HTTPException, Request

from typing import Optional, List

from codx.junior.global_settings import (
    ensure_signing_secret,
    read_global_settings,
    write_global_settings,
    write_settings_section,
)
from codx.junior.security.workspace_api import (
    AUTH_ERROR_INVALID,
    AUTH_ERROR_MISSING,
    WORKSPACE_SERVICE_USERNAME,
    auth_error_detail,
    extract_auth_token,
    is_workspace_token,
)
from codx.junior.model.user import (
  CodxUser,
  CodxUserLogin,
  CompanyMembership,
  ProjectPermission,
  PROJECT_PERMISSION_ADMIN,
  PROJECT_PERMISSION_KNOWLEDGE_ADMIN,
  COMPANY_ROLE_ADMIN,
  COMPANY_ROLE_MANAGER,
  COMPANY_ROLE_MEMBER,
  COMPANY_ROLES,
  USER_ROLE_ADMIN,
  USER_ROLE_COMPANY_ADMIN,
  WORKSPACE_MODE_AUTO,
  WORKSPACE_MODES,
)
from codx.junior.settings import CODXJuniorSettings

logger = logging.getLogger(__name__)


def _redact_token(token: Optional[str]) -> str:
    """
    Return a non-reversible fingerprint of an authentication token for logs.

    SECURITY: bearer tokens were logged verbatim by several branches of the
    login/authentication path (``logger.info(f"... token {token}")``), so anyone
    with read access to the container logs owned every active session. Only a
    truncated SHA-256 digest is emitted; the token itself never reaches a log.
    """
    if not token or not str(token).strip():
        return "<none>"
    digest = hashlib.sha256(str(token).strip().encode("utf-8")).hexdigest()[:12]
    return f"<token sha256:{digest}>"


def redact_user(user) -> dict:
    """
    Return a log-safe representation of a login/user object.

    SECURITY: several branches of the login path logged the whole user model and
    that model carries the plaintext password (``CodxUserLogin``) plus the session
    token, so every successful login wrote a customer credential -- including the
    one provisioned from ``settings.env`` -- into the container logs. The password
    is always replaced by a constant and the token by its digest.
    """
    if user is None:
        return {}
    data = None
    for extract in ("model_dump", "dict"):
        method = getattr(user, extract, None)
        if callable(method):
            try:
                data = dict(method())
                break
            except Exception:
                data = None
    if data is None:
        try:
            data = dict(vars(user))
        except Exception:
            data = None
    if not data:
        return {"user": str(user)}
    if data.get("password"):
        data["password"] = "<redacted>"
    if data.get("token"):
        data["token"] = _redact_token(data["token"])
    return data


class AmbiguousLogin(ValueError):
    """
    A login identifier matches more than one account.

    Raised by :meth:`UserSecurityManager.resolve_login_identifier`; the login
    endpoint turns it into an explicit ``409`` instead of silently choosing an
    account (which would be an account-takeover hazard).
    """

    def __init__(self, identifier: str, matches: List[str]):
        self.identifier = identifier
        self.matches = list(matches)
        super().__init__(
            f"login identifier '{identifier}' matches several accounts: "
            f"{', '.join(self.matches)}"
        )


# Project permissions that allow changing what the profiles learn in the
# project (rules written to the profile enrichment and knowledge files).
PROFILE_KNOWLEDGE_PERMISSIONS = {
    PROJECT_PERMISSION_ADMIN,
    PROJECT_PERMISSION_KNOWLEDGE_ADMIN,
}

# ---------------------------------------------------------------------------
# GitHub Admin Helper
# ---------------------------------------------------------------------------

def get_github_admin_accounts() -> List[str]:
    """
    Read the GITHUB_ADMINS environment variable and return a list of
    GitHub usernames that should be granted admin role.

    Format: comma-separated list (case-insensitive matching)
    Example: "user1,user2,user3"

    Returns:
        List of GitHub usernames (normalized to lowercase).
    """
    github_admins_env = os.getenv("GITHUB_ADMINS", "")
    if not github_admins_env.strip():
        return []
    
    # Split by comma, strip whitespace, and normalize to lowercase
    admins = [
        account.strip().lower()
        for account in github_admins_env.split(",")
        if account.strip()
    ]
    return admins


def is_github_admin_account(github_username: str) -> bool:
    """
    Check if a GitHub username is in the GITHUB_ADMINS list.

    Args:
        github_username: The GitHub account username to check.

    Returns:
        True if the account is in the admin list, False otherwise.
    """
    if not github_username:
        return False
    
    admin_accounts = get_github_admin_accounts()
    return github_username.lower() in admin_accounts


class UserSecurityManager():
    # Sections owned by this manager. An account save may only write these.
    # Anything else (AI models and providers, git credentials, working hours,
    # agent settings...) belongs to other components and must never be touched
    # from the account screen.
    USER_SETTINGS_SECTIONS = ("users", "user_logins")

    def __init__(self):
        self.global_settings = read_global_settings()

    def find_user(self, username: str = None) -> Optional[CodxUser]:
        return next((CodxUser(**user.__dict__) for user in self.global_settings.users
                     if user.username == username), None)

    def find_github_user(self, account) -> Optional[CodxUser]:
        return next((CodxUser(**user.__dict__) for user in self.global_settings.users
                     if user.github == account), None)

    def find_user_login(self, username: str = None) -> Optional[CodxUserLogin]:
        return next((login for login in self.global_settings.user_logins
                     if login.username == username), None)

    # ── Login identifier (username or email) ─────────────────────────────────

    def resolve_login(self, identifier: str) -> tuple:
        """
        Resolve a login identifier and report *how* it matched.

        Same rules as :meth:`resolve_login_identifier`, but the caller also
        learns whether the identifier matched a ``username`` or an ``email``.
        The distinction matters: the historical first-login bootstrap (an
        account with no stored credential adopts the password of its first
        login) is only allowed for a username, never for an email.

        Args:
            identifier: The login identifier.

        Returns:
            ``(canonical_username, "username" | "email")``, or ``(None, "")``.

        Raises:
            AmbiguousLogin: When the identifier matches more than one account.
        """
        identifier = str(identifier or "").strip()
        if not identifier:
            return None, ""

        exact = self.find_user(username=identifier)
        if exact is not None:
            return exact.username, "username"

        wanted = identifier.lower()
        username_matches: List[str] = []
        email_matches: List[str] = []

        def add(bucket: List[str], username: Optional[str]) -> None:
            username = str(username or "").strip()
            if username and username not in bucket:
                bucket.append(username)

        for user in self.global_settings.users or []:
            if (user.username or "").strip().lower() == wanted:
                add(username_matches, user.username)
            if (user.email or "").strip().lower() == wanted:
                add(email_matches, user.username)
        for login in self.global_settings.user_logins or []:
            if (login.username or "").strip().lower() == wanted:
                add(username_matches, login.username)
            if (login.email or "").strip().lower() == wanted:
                add(email_matches, login.username)

        matches: List[str] = []
        for username in username_matches + email_matches:
            if username not in matches:
                matches.append(username)

        if not matches:
            return None, ""
        if len(matches) > 1:
            raise AmbiguousLogin(identifier, matches)
        return matches[0], ("username" if username_matches else "email")

    def resolve_login_identifier(self, identifier: str) -> Optional[str]:
        """
        Resolve a login identifier to its canonical username.

        The customer knows the email they signed up with, not the username a
        provisioning script generated, so a login identifier is matched against
        both fields:

        1. an exact ``username`` match always wins;
        2. otherwise ``username`` and ``email`` are compared case-insensitively
           (``CodxUser.email`` and the stored login's email are both considered).

        Args:
            identifier: The ``username`` field of ``POST /api/users/login``
                (which may carry an email address) or an email.

        Returns:
            The canonical username, or ``None`` when nothing matches.

        Raises:
            AmbiguousLogin: When the identifier matches more than one account.
                The caller rejects it explicitly instead of guessing: silently
                picking one account is an account-takeover hazard.
        """
        return self.resolve_login(identifier)[0]

    # ── Scoped workspace credential ──────────────────────────────────────────

    def get_workspace_token_user(self) -> CodxUser:
        """
        Return the principal a valid ``CODX_JUNIOR_WORKSPACE_TOKEN`` acts as.

        The token is scoped to the workspace, not to a browser session: the
        hosted console must be able to create the customer's first task without
        holding their password. It therefore acts **as the owner** when one can
        be resolved:

        1. ``CODX_JUNIOR_ADMIN_USER`` (the account the installer provisions),
        2. the first enabled user with the ``admin`` role,
        3. otherwise a *service principal* named ``workspace-service`` with the
           ``admin`` role, so a workspace whose user store is empty is still
           usable.

        The principal is returned as a copy: nothing is written to the user
        store for an API-key request.

        Returns:
            The principal the workspace token authenticates as (never ``None``).
        """
        users = [
            user
            for user in (self.global_settings.users or [])
            if user is not None and not getattr(user, "disabled", False)
        ]

        def principal(source: CodxUser) -> CodxUser:
            """Copy *source* without its reusable credentials.

            The workspace token is the credential of the request; handing the
            owner's session JWT or API key back in a response would defeat its
            purpose (and leak it into a console log).
            """
            user = CodxUser(**source.__dict__)
            user.token = ""
            user.api_key = ""
            return user

        preferred = (os.environ.get("CODX_JUNIOR_ADMIN_USER") or "").strip()
        if preferred:
            match = next(
                (user for user in users if user.username == preferred), None
            )
            if match is not None:
                return principal(match)

        admins = sorted(
            (user for user in users if getattr(user, "role", "") == USER_ROLE_ADMIN),
            key=lambda user: str(user.username or ""),
        )
        if admins:
            return principal(admins[0])

        return CodxUser(
            username=WORKSPACE_SERVICE_USERNAME,
            role=USER_ROLE_ADMIN,
            email="",
        )

    def list_user(self):
        """
        Return the user catalog used by pickers and the company manager.

        Only ``username`` / ``avatar`` were returned before. The company shell
        also needs the role and the company memberships to resolve a company
        roster and to render the People view, so they are included as optional
        keys (every existing consumer keeps working untouched).
        """
        return [{
            "username": user.username,
            "avatar": user.avatar,
            "email": user.email,
            "role": user.role,
            "language": getattr(user, "language", "auto") or "auto",
            "disabled": bool(user.disabled),
            "companies": [
                membership.model_dump()
                for membership in (user.companies or [])
            ],
        } for user in self.global_settings.users]

    # ── Company membership ───────────────────────────────────────────────────

    def find_company_memberships(self, company_id: str) -> List[dict]:
        """
        Return every user that belongs to *company_id*.

        Args:
            company_id: Company id.

        Returns:
            A list of ``{username, email, avatar, role, company_role, title,
            disabled}`` entries. The company role is the membership role, not
            the global role.
        """
        company_id = str(company_id or "")
        result = []
        for user in self.global_settings.users:
            membership = next(
                (
                    item
                    for item in (user.companies or [])
                    if item.company_id == company_id
                ),
                None,
            )
            if not membership:
                continue
            result.append({
                "username": user.username,
                "email": user.email,
                "avatar": user.avatar,
                "role": user.role,
                "company_role": membership.role,
                "title": membership.title,
                "disabled": bool(user.disabled),
                "joined_at": membership.joined_at,
            })
        return sorted(result, key=lambda item: (item["company_role"], item["username"].lower()))

    def company_role(self, company_id: str, user: Optional[CodxUser]) -> str:
        """Return *user*'s role in *company_id* (``""`` when not a member)."""
        if not user:
            return ""
        if getattr(user, "role", "") == USER_ROLE_ADMIN:
            return COMPANY_ROLE_ADMIN
        membership = next(
            (
                item
                for item in (getattr(user, "companies", None) or [])
                if item.company_id == str(company_id or "")
            ),
            None,
        )
        return membership.role if membership else ""

    def can_manage_company(self, company_id: str, user: Optional[CodxUser]) -> bool:
        """``True`` when *user* can manage the work of *company_id*."""
        return self.company_role(company_id, user) in (
            COMPANY_ROLE_ADMIN,
            COMPANY_ROLE_MANAGER,
        )

    def can_admin_company(self, company_id: str, user: Optional[CodxUser]) -> bool:
        """``True`` when *user* can administer *company_id* (add users, settings)."""
        return self.company_role(company_id, user) == COMPANY_ROLE_ADMIN

    def add_company_member(
        self,
        company_id: str,
        username: str,
        role: str = COMPANY_ROLE_MEMBER,
        title: str = "",
    ) -> dict:
        """
        Add (or update) a user's membership in a company.

        Args:
            company_id: Company id.
            username: Username of an existing codx-junior user.
            role: ``admin`` | ``manager`` | ``member``.
            title: Optional title inside the company.

        Returns:
            The membership as a dictionary.

        Raises:
            ValueError: When the user does not exist or the role is invalid.
        """
        username = str(username or "").strip()
        if not username:
            raise ValueError("username is required")
        role = str(role or COMPANY_ROLE_MEMBER).strip().lower()
        if role not in COMPANY_ROLES:
            raise ValueError(
                f"invalid company role '{role}' (expected one of "
                f"{', '.join(COMPANY_ROLES)})"
            )
        stored_user = next(
            (item for item in self.global_settings.users if item.username == username),
            None,
        )
        if stored_user is None:
            raise ValueError(f"user '{username}' does not exist")
        stored_user.companies = list(stored_user.companies or [])
        membership = next(
            (
                item
                for item in stored_user.companies
                if item.company_id == str(company_id)
            ),
            None,
        )
        if membership is None:
            membership = CompanyMembership(
                company_id=str(company_id),
                role=role,
                title=title,
                joined_at=datetime.now().isoformat(),
            )
            stored_user.companies.append(membership)
        else:
            membership.role = role
            membership.title = title
        self.save_settings()
        return membership.model_dump()

    def remove_company_member(self, company_id: str, username: str) -> bool:
        """
        Remove a user from a company.

        Returns:
            ``True`` when a membership was removed, ``False`` when there was
            nothing to remove.
        """
        stored_user = next(
            (
                item
                for item in self.global_settings.users
                if item.username == str(username or "")
            ),
            None,
        )
        if stored_user is None:
            return False
        before = len(stored_user.companies or [])
        stored_user.companies = [
            item
            for item in (stored_user.companies or [])
            if item.company_id != str(company_id or "")
        ]
        removed = len(stored_user.companies) != before
        if removed:
            self.save_settings()
        return removed

    def create_company_user(
        self,
        company_id: str,
        username: str,
        password: str = "",
        email: str = "",
        role: str = COMPANY_ROLE_MEMBER,
        title: str = "",
        user_role: str = "user",
    ) -> dict:
        """
        Create a codx-junior account **and** enroll it in a company.

        This is the operation behind "company admins can add users": a company
        admin does not need global settings access to onboard a teammate. When
        the account already exists the membership is simply added.

        Args:
            company_id: Company id.
            username: Login name.
            password: Initial password (required for a new account).
            email: Optional email.
            role: Company role.
            title: Optional company title.
            user_role: Global role for a new account (``user`` by default).

        Returns:
            ``{"username", "created", "membership"}``.

        Raises:
            ValueError: On invalid input.
        """
        username = str(username or "").strip()
        if not username:
            raise ValueError("username is required")
        existing = self.find_user(username=username)
        if existing is not None:
            membership = self.add_company_member(
                company_id=company_id,
                username=username,
                role=role,
                title=title,
            )
            return {
                "username": username,
                "created": False,
                "membership": membership,
            }

        password = str(password or "")
        if len(password) < 4:
            raise ValueError("a password of at least 4 characters is required")

        new_user = CodxUser(
            username=username,
            email=str(email or ""),
            avatar=f"https://gravatar.com/avatar/baa8db8ab2afb7ababc235269e762662?s=200&d=robohash&r={username}",
            role=user_role if user_role in ("user", "manager", USER_ROLE_COMPANY_ADMIN) else "user",
        )
        self.global_settings.users.append(new_user)
        hashed_password = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        self.global_settings.user_logins.append(
            CodxUserLogin(
                username=username,
                email=str(email or ""),
                password=hashed_password,
            )
        )
        self.save_settings()
        membership = self.add_company_member(
            company_id=company_id,
            username=username,
            role=role,
            title=title,
        )
        return {
            "username": username,
            "created": True,
            "membership": membership,
        }

    def set_workspace_mode(self, user: CodxUser, mode: str) -> CodxUser:
        """
        Persist the preferred product shell of *user*.

        Args:
            user: Authenticated user.
            mode: ``auto`` | ``company`` | ``work``.

        Returns:
            The updated stored user.

        Raises:
            ValueError: On an invalid mode.
        """
        mode = str(mode or "").strip().lower()
        if mode not in WORKSPACE_MODES:
            raise ValueError(
                f"invalid workspace_mode '{mode}' (expected one of "
                f"{', '.join(WORKSPACE_MODES)})"
            )
        stored = self.find_user(username=getattr(user, "username", ""))
        if stored is None:
            raise ValueError("user does not exist")
        stored.workspace_mode = mode
        self.save_settings()
        return stored

    def _signing_secret(self) -> str:
        """JWT signing key, guaranteed to satisfy ``security.secret_key``.

        ``self.global_settings`` is a snapshot taken when the manager was
        created; :func:`ensure_signing_secret` also repairs that snapshot in
        place, so no request can ever sign with the old short key.
        """
        return ensure_signing_secret(settings=self.global_settings)

    def get_user_token(self, user: CodxUser):
        return jwt.encode({ "username": user.username }, self._signing_secret(), algorithm="HS256")
    
    def get_user_from_token(self, token):
        if not token:
            return None
        try:
            decoded = jwt.decode(token, self._signing_secret(), algorithms=["HS256"])
            return CodxUserLogin(**decoded)
        except:
            return None

    def login_user(
        self,
        user: CodxUserLogin = None,
        token: str = None,
        oauth_password: str = None,
        allow_credential_creation: bool = True,
    ) -> CodxUser:
        """
        Authenticate *user* with its password, or *token*.

        Args:
            user: Credentials to verify.
            token: Session token to verify instead of a password.
            oauth_password: OAuth marker used by the GitHub flow.
            allow_credential_creation: Keep the legacy first-login bootstrap:
                an existing account with **no stored credential** adopts the
                password of its first login. Callers that resolve the account
                from an email pass ``False`` so a wrong password on such an
                account is rejected instead of silently stored.
        """
        def do_login(user: CodxUserLogin, token: str):
            if token:
                user = self.get_user_from_token(token)
                if not user:
                    logging.error(f"Invalid token login {_redact_token(token)}")
                    return None
            try:
                stored_user = self.find_user(username=user.username)
                stored_login = self.find_user_login(username=user.username)

                if stored_user:
                    if stored_user.disabled:
                        logger.error(f"Disabled user login attempt: {stored_user}")
                        return None
                    
                    # Check if user is GitHub-only and attempting password login
                    if stored_user.github_only and not oauth_password:
                        logger.error(
                            f"GitHub-only user '{stored_user.username}' attempted password login. "
                            "Use GitHub OAuth instead."
                        )
                        return None
                    
                    if stored_login:
                        if token == stored_login.token:
                            return stored_user
                        if not user.password and oauth_password:
                            return stored_user
                        # Verify existing password (skip for GitHub-only users with oauth)
                        if oauth_password:
                            # OAuth login, no password verification needed
                            return stored_user
                        if bcrypt.checkpw(user.password.encode('utf-8'), stored_login.password.encode('utf-8')):
                            return stored_user
                        else:
                            logger.error("Invalid password")
                    elif user.username and (user.password or oauth_password):
                        if not allow_credential_creation:
                            # The caller resolved this account from an email:
                            # a password login must never be the event that
                            # CREATES the credential. Otherwise any password
                            # would be accepted for an account that has none.
                            logger.error(
                                "No stored credential for '%s'; email login "
                                "does not create one",
                                user.username,
                            )
                            return None
                        password = user.password or oauth_password
                        # Create a new user login with the hashed password
                        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
                        new_login = CodxUserLogin(username=user.username, email=user.email, password=hashed_password.decode('utf-8'))
                        self.global_settings.user_logins.append(new_login)
                        logger.info("Create a new user login with the hashed password and save settings")
                        self.save_settings()
                        
                        return stored_user
                else:
                    logger.error(f"Invalid login, stored_user not found for {redact_user(user)}, token: {_redact_token(token)}")    
            except Exception as ex:
                logging.error(f"Invalid user login {ex} {redact_user(user)}")
            
            return None
            
        try:
            logged_user = do_login(user=user, token=token)
            logger.info(
                f"do_login user: {redact_user(user)} token: {_redact_token(token)}"
                f" logged_user: {redact_user(logged_user)}"
            )
            if logged_user:
                user_login = self.find_user_login(username=logged_user.username)
                
                if not self.get_user_from_token(user_login.token):
                    user_login.token = self.get_user_token(user=logged_user)
                    logger.info("User logged and save settings")
                    self.save_settings()
                
                logged_user.token = user_login.token
            return logged_user
        except Exception as ex:
            logger.error(f"Invalid login {ex} {redact_user(user)} token: {_redact_token(token)}")
            return None

    def update_user(self, user: CodxUser, password: str = None):
        existing_user = self.find_user(username=user.username)
        if existing_user:
            # ``find_user`` returns a COPY, so mutating it and saving persisted
            # nothing: the account screen reported "User account updated" while
            # the email, avatar, theme and language were silently dropped. Edits
            # go to the document that is actually stored in the users section.
            stored_user = next(
                (u for u in (self.global_settings.users or [])
                 if u.username == user.username),
                None,
            )
            target_user = stored_user or existing_user
            existing_user.email = user.email
            existing_user.avatar = user.avatar
            existing_user.theme = user.theme
            # The user may choose the product shell (company vs working mode).
            # Role and company memberships are NOT editable from the account
            # screen: they are granted by a company/global admin.
            existing_user.workspace_mode = getattr(
                user, "workspace_mode", existing_user.workspace_mode
            ) or WORKSPACE_MODE_AUTO
            existing_user.language = getattr(
                user, "language", existing_user.language
            ) or "auto"

            if target_user is not existing_user:
                target_user.email = existing_user.email
                target_user.avatar = existing_user.avatar
                target_user.theme = existing_user.theme
                target_user.workspace_mode = existing_user.workspace_mode
                target_user.language = existing_user.language

            if password:
                # Prevent password update for GitHub-only users
                if existing_user.github_only:
                    logger.error(
                        f"Cannot set password for GitHub-only user '{existing_user.username}'. "
                        "Use GitHub OAuth for authentication."
                    )
                    raise Exception("Password cannot be set for GitHub-only users")
                
                # Update password in the user logins
                stored_login = self.find_user_login(username=user.username)
                if stored_login:
                    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
                    stored_login.password = hashed_password.decode('utf-8')
            logger.info("Update user and save settings")
            self.save_settings()

        return existing_user

    def get_user_project_access(self, user: CodxUser, settings: CODXJuniorSettings):
        if user:
            if user.role == 'admin':
                return 'admin'
            for p in user.projects:
                if p.project_id == settings.project_id:
                    return p.permissions
        return ''

    def can_manage_profile_knowledge(self, user: CodxUser, settings: CODXJuniorSettings) -> bool:
        """
        Return True when *user* may change what the profiles learn in *settings*'
        project.

        Allowed, in order:
          1. codx-junior admins (``user.role == "admin"``),
          2. project admins (``permissions == "admin"``),
          3. project knowledge admins (``permissions == "knowledge_admin"``).

        The knowledge admin role exists so a project can delegate the custody of
        what the profiles learn (rules and knowledge files) without handing over
        full project administration.

        Args:
            user: User performing the operation (may be None).
            settings: Project the operation applies to.

        Returns:
            True when the user may write profile learning resources.

        Example:
            >>> UserSecurityManager().can_manage_profile_knowledge(user, settings)
            True
        """
        if not user or not getattr(user, "username", ""):
            return False
        if getattr(user, "role", "") == 'admin':
            return True
        return self.get_user_project_access(user=user, settings=settings) \
            in PROFILE_KNOWLEDGE_PERMISSIONS

    def add_user_to_project(self, user: CodxUser, project_id: str, permissions: str):
        global_user = next((u for u in self.global_settings.users if u.username == user.username))
        project = next((p for p in global_user.projects if p.project_id == project_id), None)
        save_settings = False
        if project:
            if project.permissions != permissions:
                project.permissions = permissions
                save_settings = True
        else:
            global_user.projects.append(ProjectPermission(
              project_id=project_id,
              permissions=permissions
            ))
            save_settings = True
        if save_settings:
            logger.info("Add user to project and save settings")
            self.save_settings()

    def get_users_with_project_access(self, project_id: str):
        users_with_access = []
        for user in self.global_settings.users:
            add = user.role == 'admin'
            if not add:
                for project in user.projects:
                    if project.project_id == project_id:
                        add = True
                        break
            if add:
                users_with_access.append({
                    "username": user.username,
                    "avatar": user.avatar
                })
                        
        return users_with_access

    def save_settings(self):
        """
        Persist the sections owned by the user security manager only.

        The previous implementation called ``write_global_settings`` on
        ``self.global_settings``: every section of GlobalSettings was rewritten
        from the snapshot this manager took in ``__init__``. An account save
        (profile, email, avatar, language, theme, workspace mode, password or
        project permission) therefore wrote the stale values of the AI models,
        providers, git credentials, working hours... back over the section
        files and silently reverted whatever the Global Settings screen had
        changed after the snapshot was taken.

        Writing just ``users`` and ``user_logins`` keeps the account screen
        unable to damage anything it does not own, and the sectioned write
        invalidates the shared settings cache so the next read sees the
        persisted state.
        """
        data = self.global_settings.dict()
        for section in self.USER_SETTINGS_SECTIONS:
            write_settings_section(section, data.get(section))
        self.global_settings = read_global_settings(refresh=True)


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------
# The request user is resolved once per request (the session middleware and the
# endpoint dependency both need it) and cached on ``request.state``.
_UNRESOLVED = object()


def _unauthorized(code: str, message: str) -> HTTPException:
    """
    Build the 401 raised by :func:`get_authenticated_user`.

    ``detail`` carries a stable machine-readable ``code`` so a client can tell
    "no credential was sent" from "the credential was rejected" without
    parsing the message.
    """
    return HTTPException(
        status_code=401,
        detail=auth_error_detail(code, message),
        headers={"WWW-Authenticate": 'Bearer realm="codx-junior"'},
    )


def resolve_request_user(request: Request) -> Optional[CodxUser]:
    """
    Resolve the caller from the request credential, or ``None``.

    A credential is either the scoped workspace token
    (``CODX_JUNIOR_WORKSPACE_TOKEN``) or a session JWT. The standard
    ``Authorization: Bearer …`` header and the UI's historical
    ``authentication: Bearer …`` alias are both accepted; ``Authorization``
    wins when both are present (see
    :func:`codx.junior.security.workspace_api.extract_auth_token`).

    Args:
        request: The incoming request.

    Returns:
        The authenticated user, or ``None`` for an anonymous request. This
        function never raises: use :func:`get_authenticated_user` when the
        endpoint requires a credential.
    """
    state = getattr(request, "state", None)
    cached = getattr(state, "_codx_authenticated_user", _UNRESOLVED) if state else _UNRESOLVED
    if cached is not _UNRESOLVED:
        return cached

    token, _header = extract_auth_token(request)
    user = None
    if token:
        manager = UserSecurityManager()
        if is_workspace_token(token):
            # Scoped, opaque API key: acts as the workspace owner (or a service
            # principal), independent of any interactive password.
            user = manager.get_workspace_token_user()
        else:
            user = manager.login_user(token=token)

    if state is not None:
        try:
            state._codx_authenticated_user = user
        except Exception:  # pragma: no cover - state is a plain namespace
            pass
    return user


def get_authenticated_user(request: Request) -> CodxUser:
    """
    FastAPI dependency: require an authenticated caller and return the user.

    This used to return ``None`` when no credential was sent, so every handler
    that then read ``user.username`` answered ``500`` instead of ``401`` — and a
    write endpoint that only used the user for attribution silently accepted
    anonymous writes. A protected route now receives a user or a ``401``; a
    genuinely public route opts out by depending on
    :func:`get_optional_authenticated_user` instead.

    Raises:
        HTTPException: 401 with a stable ``code`` when no/invalid credential is
            present.
    """
    token, header = extract_auth_token(request)
    user = resolve_request_user(request)

    # SECURITY: the raw token and the whole header map (authorization, cookies)
    # were logged at INFO on EVERY request. Log header NAMES only and keep the
    # token as a digest; request authentication is trace-level noise anyway.
    logger.debug(
        "Authenticating request: %s token %s header %s: %s - headers: %s",
        getattr(getattr(request, "url", None), "path", request),
        _redact_token(token),
        header or "<none>",
        user.username if user else "",
        sorted(request.headers.keys()),
    )

    if user is not None:
        return user
    if not token:
        raise _unauthorized(
            AUTH_ERROR_MISSING,
            "Authentication is required: send 'Authorization: Bearer <token>' "
            "(the legacy 'authentication' header is also accepted).",
        )
    raise _unauthorized(AUTH_ERROR_INVALID, "Invalid or expired credentials.")


def get_optional_authenticated_user(request: Request) -> Optional[CodxUser]:
    """
    FastAPI dependency: resolve the caller without requiring a credential.

    For the small set of routes that are genuinely public (login, the request
    session context, meeting join) or that enrich a response when a user is
    present. Every other route must depend on
    :func:`get_authenticated_user`.
    """
    return resolve_request_user(request)


def get_authenticated_user_from_token(token: str) -> CodxUser:
    """
    Resolve a user from a raw token (Socket.IO handshake, background worker).

    Accepts the scoped workspace credential too, so a machine client is not
    forced to hold a password to use the token-based transports.
    """
    user_security_manager = UserSecurityManager()
    user = None
    if token:
        if is_workspace_token(token):
            user = user_security_manager.get_workspace_token_user()
        else:
            user = user_security_manager.login_user(token=token)
    user_name = user.username if user else ""
    logger.info(f"WebSocket token {_redact_token(token)}: {user_name}")
    return user
