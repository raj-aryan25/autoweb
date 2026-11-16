"""Passive mapping of an owned runtime preview to a public HTTP snapshot."""

from .api_contracts import RunView, ViewFrameMetadata
from .contracts import LiveViewStatus
from .live_capture import MAX_FRAME_BYTES, PreviewFrame
from .live_transport import jpeg_dimensions


def snapshot_view(record, handle, *, closing=False):
    """Return metadata and immutable pixels together, without awaits or mutation.

    All state is read in the service loop. Pairing bytes and metadata in one call
    prevents an intervening frame update from describing a different image.
    """
    run_id = record.snapshot.run_id

    def empty(state, diagnostic=None):
        return RunView(run_id=run_id, status=LiveViewStatus(state=state, diagnostic=diagnostic)), None

    if not record.active or record.cleanup_required or closing:
        return empty("ended")
    if not record.overrides.live_view_enabled:
        return empty("disabled")
    if handle is None:
        return empty("starting")
    if handle.run_id != run_id:
        return empty("unavailable", "transport_unavailable")
    if handle.cleanup_verified or handle.cleanup_uncertain:
        return empty("ended")
    preview = handle.preview
    if preview is None:
        return (empty("starting") if handle.process is None
                else empty("unavailable", "transport_unavailable"))
    if preview.run_id != run_id:
        return empty("unavailable", "transport_unavailable")
    status, frame = preview.status, preview.latest
    if status.state not in {"live", "ended"}:
        return RunView(run_id=run_id, status=status), None
    if frame is None:
        return (empty("ended") if status.state == "ended"
                else empty("unavailable", "capture_failed"))
    try:
        if (not isinstance(frame, PreviewFrame) or type(frame.data) is not bytes
                or not 0 < len(frame.data) <= MAX_FRAME_BYTES
                or jpeg_dimensions(frame.data) != (frame.width, frame.height)):
            raise ValueError("invalid_frame")
        metadata = ViewFrameMetadata(sequence=frame.sequence, captured_at=frame.captured_at,
                                     width=frame.width, height=frame.height, byte_length=len(frame.data))
    except (ValueError, TypeError, AttributeError):
        return empty("unavailable", "capture_failed")
    return RunView(run_id=run_id, status=status, frame=metadata), frame
