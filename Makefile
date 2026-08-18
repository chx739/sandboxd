GO ?= go

.PHONY: build test

build:
	$(GO) build ./...

test:
	$(GO) test ./...
