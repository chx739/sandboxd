FROM curlimages/curl:8.12.1@sha256:88a9abad9d958340e48564f9bdcdaa29916a2984be59314da709f8bbc0eef6f7
COPY --chmod=0555 sandbox-filetool /usr/local/bin/sandbox-filetool
USER 65532:65532
