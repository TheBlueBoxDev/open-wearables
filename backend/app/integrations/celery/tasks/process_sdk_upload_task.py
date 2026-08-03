import uuid
from datetime import datetime, timezone
from logging import getLogger
from uuid import UUID

from celery import shared_task

from app.database import SessionLocal
from app.models import User
from app.repositories.user_connection_repository import UserConnectionRepository
from app.repositories.user_repository import UserRepository
from app.schemas.sync_status import SyncSource, SyncStatus
from app.services.apple.healthkit.import_service import (
    ImportService as SDKImportService,
)
from app.services.apple.healthkit.import_service import (
    import_service as sdk_import_service,
)
from app.services.sync_status_service import completed, failed, started
from app.utils.structured_logging import log_structured

logger = getLogger(__name__)


def _get_import_service(provider: str) -> SDKImportService:
    if provider in ("apple", "samsung", "google"):
        return sdk_import_service
    raise ValueError(f"Unsupported provider: {provider}")


@shared_task(queue="sdk_sync")
def process_sdk_upload(
    content: str,
    content_type: str,
    user_id: str,
    provider: str,
    batch_id: str | None = None,
    received_at: str | None = None,
) -> dict[str, int | str]:
    """
    Process SDK data import asynchronously.

    Args:
        content: The request content as string (JSON or multipart data)
        content_type: The content type header value
        user_id: User ID to associate with the data
        provider: Import provider - "apple", "samsung", "google"
        batch_id: Unique batch identifier for tracking (optional for backwards compatibility)
        received_at: ISO 8601 server-side accept time of the batch, used to avoid reactivating a
            connection revoked after the batch was accepted (optional for backwards
            compatibility: batches queued before this parameter existed arrive as None)

    Returns:
        Dictionary with status_code and response message
    """
    # Generate batch_id if not provided (backwards compatibility)
    if not batch_id:
        batch_id = str(uuid.uuid4())

    # Parse defensively: a malformed value degrades to None rather than dropping the batch
    try:
        received_at_dt = datetime.fromisoformat(received_at) if received_at else None
    except (TypeError, ValueError):
        log_structured(
            logger,
            "warning",
            "Ignoring unparsable received_at on SDK batch",
            provider=provider,
            action="parse_received_at",
            batch_id=batch_id,
            user_id=user_id,
            received_at=received_at,
        )
        received_at_dt = None

    if received_at_dt is not None and received_at_dt.tzinfo is None:
        # Comparing naive against the connection's aware updated_at would raise and drop the
        # batch. The API always sends an aware UTC value, so a naive one means an unexpected
        # producer - assume UTC and make it visible rather than skipping the guard.
        log_structured(
            logger,
            "warning",
            "Assuming UTC for timezone-naive received_at on SDK batch",
            provider=provider,
            action="parse_received_at",
            batch_id=batch_id,
            user_id=user_id,
            received_at=received_at,
        )
        received_at_dt = received_at_dt.replace(tzinfo=timezone.utc)

    # Validate user_id format
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        log_structured(
            logger,
            "warning",
            "Invalid user_id format",
            provider=provider,
            action="validate_user_id",
            batch_id=batch_id,
            user_id=user_id,
        )
        return {"status": "error", "reason": "invalid_user_id", "batch_id": batch_id}

    # Validate user exists before processing
    with SessionLocal() as db:
        user_repo = UserRepository(User)
        if not user_repo.get(db, user_uuid):
            log_structured(
                logger,
                "warning",
                "Skipping import for non-existent user",
                provider=provider,
                action="validate_user_exists",
                batch_id=batch_id,
                user_id=user_id,
            )
            return {"status": "skipped", "reason": "user_not_found", "batch_id": batch_id}

    # Log task start
    log_structured(
        logger,
        "info",
        f"{provider.capitalize()} sync batch processing started",
        action=f"{provider}_batch_processing_start",
        batch_id=batch_id,
        user_id=user_id,
        provider=provider,
    )

    started(
        user_uuid,
        provider,
        SyncSource.SDK,
        run_id=batch_id,
        message=f"Processing {provider} SDK batch",
        metadata={"batch_id": batch_id},
    )

    with SessionLocal() as db:
        # Ensure SDK connection exists for this user (SDK-based, no OAuth tokens)
        connection_repo = UserConnectionRepository()
        connection_repo.ensure_sdk_connection(db, user_uuid, provider, received_at=received_at_dt)

        # Select the appropriate import service based on source
        import_service = _get_import_service(provider)

        result = import_service.import_data_from_request(
            db, content, content_type, user_id, batch_id=batch_id
        ).model_dump()

        # Log processing completion with results
        log_structured(
            logger,
            "info",
            f"{provider.capitalize()} sync batch processing completed",
            action=f"{provider}_batch_processing_complete",
            batch_id=batch_id,
            user_id=user_id,
            provider=provider,
            status_code=result.get("status_code"),
            response=result.get("response"),
            # Include counts from result if available
            records_saved=result.get("records_saved", 0),
            workouts_saved=result.get("workouts_saved", 0),
            sleep_saved=result.get("sleep_saved", 0),
        )

        status_code = result.get("status_code", 200)
        records_saved = int(result.get("records_saved", 0) or 0)
        workouts_saved = int(result.get("workouts_saved", 0) or 0)
        sleep_saved = int(result.get("sleep_saved", 0) or 0)
        dropped_count = int(result.get("dropped_count", 0) or 0)
        items_total = records_saved + workouts_saved + sleep_saved

        if isinstance(status_code, int) and 200 <= status_code < 300:
            message = f"{provider.capitalize()} batch saved"
            if dropped_count:
                message += f" ({dropped_count} record(s) dropped by validation)"
            completed(
                user_uuid,
                provider,
                SyncSource.SDK,
                run_id=batch_id,
                status=SyncStatus.SUCCESS,
                message=message,
                items_processed=items_total,
                metadata={
                    "batch_id": batch_id,
                    "records_saved": records_saved,
                    "workouts_saved": workouts_saved,
                    "sleep_saved": sleep_saved,
                    "dropped_count": dropped_count,
                },
            )
        else:
            failed(
                user_uuid,
                provider,
                SyncSource.SDK,
                run_id=batch_id,
                error=str(result.get("response", "Unknown error")),
                message=f"{provider.capitalize()} batch failed",
                metadata={"batch_id": batch_id, "status_code": status_code},
            )

        return {**result, "batch_id": batch_id}
