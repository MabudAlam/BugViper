from fastapi import HTTPException, Request


def get_current_user(request: Request) -> dict:
    """Return the Firebase user decoded by FirebaseAuthMiddleware."""
    return request.state.user


def get_current_uid(request: Request) -> str:
    """Extract the Firebase UID from the authenticated user."""
    user = request.state.user
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uid
