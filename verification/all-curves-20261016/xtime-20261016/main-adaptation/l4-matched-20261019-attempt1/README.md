# Matched L4 attempt 1

The CUDA image built and both native binaries passed self-test. The remote
worker then failed while importing the launcher because a client-only source
path variable was absent. The app was aborted. No workload or timing row was
produced. `failure.json` and `modal.log` retain the observed scope and error.
The launcher was changed to guard local paths with `modal.is_local()`.
