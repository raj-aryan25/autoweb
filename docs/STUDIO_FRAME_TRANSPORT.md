# Private live-view frame transport — step 3

Step 3 connects masked capture inside the supervised executor to a temporary
latest-frame store inside the backend. HTTP frame endpoints and the frontend
viewer are subsequent steps. Public manifests still report viewing unavailable;
the internal receiver's availability is independent of run/authentication state.

## Ownership and isolation

For each run requesting live viewing, `WorkerCoordinator` opens a Unix socket in
a new mode-0700 directory under `/tmp`. The socket is mode 0600. Its path and a
random 256-bit token are service-generated `PreviewConnection` values on the
private `Launch` message. They cannot be supplied through `StartRun`, are omitted
from object representations, and never enter public contracts, history or argv.
The guardian forwards this trusted launch configuration through its existing
stdin pipe without receiving or forwarding image frames.

`WorkerHandle.preview` holds the receiver. Guardian `Hello` provides the executor
PID; the receiver pins its process start identity. Each connection must match
the kernel's `SO_PEERCRED` UID/PID and the expected PID/start time, plus the run ID
and constant-time token comparison. Browser children, unrelated executors and
other same-user processes do not qualify. Unauthenticated failures do not erase
an authenticated producer's frame. There is one authenticated producer per run.

The executor creates the sender through the optional session lifecycle hook,
before navigation. `LiveCapture` hands off validated masked frames through a
nonblocking callback and reports capture availability separately. No screenshot
is written to a file by this transport. With live viewing disabled, no preview
socket, sender or capture task is created.

## Private framing and bounds

Each packet begins with a four-byte network-order header length, then a JSON
header of at most 1024 bytes. Frame headers identify the run, token, sequence,
timezone-aware capture time, JPEG format, width, height and byte count. The body
is at most 1 MiB. Integer bounds exclude booleans; dimensions cannot exceed
1920 × 1080, sequence must increase and capture time cannot decrease. Duplicate
JSON keys, extra fields, wrong runs/tokens, invalid types and truncated data are
rejected. The receiver checks JPEG start/end markers and actual SOF dimensions
against the header without decompressing untrusted pixels.

Capture status and heartbeat packets use the same authenticated private channel.
Heartbeats preserve an existing live image without copying pixels again. A
status packet alone cannot establish a live view. None of these packets enter
the guardian's bounded progress JSON pipe, SSE, SQLite, reports or exports.

The sender keeps only one pending frame and the receiver keeps only one latest
frame. Obsolete frames are replaced. Socket drain/header/body/connection waits
are bounded; slow or disconnected consumers cannot accumulate a frame queue.
There are at most four connection tasks plus one startup deadline. Preview setup and wire
failures make viewing unavailable without retrying authentication, navigation or
email requests.

## Cleanup and recovery

Normal lifecycle exit offers a final masked frame and allows a bounded flush.
The receiver can retain that image with an Ended state until worker teardown.
Cancellation skips terminal capture/flush. Unexpected producer loss clears the
image and reports unavailable. Receiver close clears all pixels, cancels and
awaits preview tasks, closes the server and removes the socket directory on
success, Stop, failed preflight, timeout, executor/guardian failure and shutdown.
The guardian's existing process ownership and cleanup attestation remain intact;
unknown browser cleanup still blocks replacement runs.

After an abrupt service death, startup first acquires the existing database lock
and performs process recovery. If no active/uncertain run remains, it removes
stale socket-only directories matching this service data directory's private
namespace. It checks owner, mode and file type, skips symlinks/unexpected files,
and performs no recursive deletion. No preview image or token is persisted for
recovery; history has no live replay.

## Verification

The configured Deepseek coding Paseo profile completed verification on 2026-11-16
using conda `autoweb`, controlled local fixtures and external command timeouts.

| Check | Result |
| --- | --- |
| Transport unit tests | 50 passed |
| Transport integration tests | 15 passed |
| Related worker/capture/verification/API checks | 42 passed, 1 expected desktop skip |
| Full deterministic suite | 645 passed, 6 skipped in 229.5 seconds |
| Both new files after test import cleanup | 65 passed |

The full suite preceded removal of unused test imports; both new test files were
rerun afterward. The six full-suite skips remain five opt-in live checks and the
explicit desktop worker check. No application bugs were found or application code
changed by the verifier.

Coverage includes producer authentication, malformed/oversized headers and frames,
JPEG dimensions, sequence/time ordering, latest-only buffers, terminal delivery,
capture/status callback isolation, preview setup failure, and controlled password,
OTP and magic-link runs. Stop, executor crash and guardian death clear frames and
remove sockets while preserving the existing replacement-run rules. Marker frames
were absent from guardian stdout, durable events, SQLite and artifacts. No preview
socket directories remained after verification.

No live websites, Gmail, OAuth consent or framework AI calls were used; all browser
runs were headless. Contract generation and the frontend build were not repeated
because step 3 changes no generated public contracts or frontend source. Static
manifests retain their unavailable startup hint. Step 4 adds current availability
and JPEG reads through [the runtime view API](STUDIO_VIEW_API.md); the frontend
panel remains pending.
