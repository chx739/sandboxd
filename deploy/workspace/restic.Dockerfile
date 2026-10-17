FROM curlimages/curl:8.12.1@sha256:88a9abad9d958340e48564f9bdcdaa29916a2984be59314da709f8bbc0eef6f7
COPY --chmod=0555 restic /usr/local/bin/restic
COPY --chmod=0555 sandbox-restic /usr/local/bin/sandbox-restic
RUN echo '01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6  /usr/local/bin/restic' | sha256sum -c -
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/sandbox-restic"]
